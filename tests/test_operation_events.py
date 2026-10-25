"""2D observer: real registry/QtBridge, controlled fake backend/writer/LLM; no STE/provider."""
import copy
import json
import threading
import time
import unittest
from unittest import mock

import test_slicer_exports as exports
import test_slicer_slicing as slicing
from test_agent_integration import CONFIG, ScriptedLLM, calls, tool
from AIAssistant.agent.service import AgentService, MAX_LLM_STEPS
from AIAssistant.agent.llm import LLMError


class OperationEventsTests(unittest.TestCase):
    start, status, complete, drain = slicing.SlicingTests.start, slicing.SlicingTests.status, slicing.SlicingTests.complete, slicing.SlicingTests.drain
    worker, finish, pump_until_done = slicing.SlicingTests.worker, slicing.SlicingTests.finish, slicing.SlicingTests.pump_until_done
    clean_workers, wait_status = exports.ExportTests.clean_workers, exports.ExportTests.wait_status

    def setUp(self):
        exports.ExportTests.setUp(self)
        self.llm = ScriptedLLM()
        self.agent = AgentService(self.adapter, lambda: dict(CONFIG), self.llm, run_async=False)
        self.addCleanup(self.agent.close)
        # Manual ticks use the exact worker body without sleeps or a racing second poller.
        self.agent._start_observer = mock.Mock()
        self.now = 0.0
        self.agent._observation_clock = lambda: self.now
        self.sid = self.agent.create_session()["session_id"]
        self.sequence = 0

    def send(self, *answers, **kwargs):
        self.llm.answers.extend(answers)
        self.sequence += 1
        sid = kwargs.get("sid", self.sid)
        self.pump_until_done(self.worker(lambda: self.agent.submit_message(sid, {
            "text": "Выполни мой запрос", "client_message_id": "observer-" + str(self.sequence)})))
        return self.agent.get_session(sid)

    def launch_slice(self, *tail, **kwargs):
        return self.send(calls(tool("start_slicing")), *(tail or ({"content": "Запуск принят"},)), **kwargs)

    def poll(self, kind="slice"):
        record = self.agent._operations[kind]
        self.assertIsNotNone(record)
        self.pump_until_done(self.worker(lambda: self.agent._observe_operation(record)))
        return self.agent.get_session(record["sid"])

    def finish_native(self):
        self.app.backend.estimates()
        self.app.backend.finish()
        self.drain()

    def test_after_idle_queued_running_completed_actual_estimates_without_llm_or_fake_tools(self):
        self.app.backend.behavior = "queued"
        snapshot = self.launch_slice()
        self.assertEqual(snapshot["agent_state"], "idle")
        self.assertEqual(snapshot["slice"]["state"], "queued")
        initial_tools = copy.deepcopy(snapshot["tool_events"])
        self.app.backend.begin()
        self.app.backend.processingProgress.emit(0.42)
        snapshot = self.poll()
        self.assertEqual(snapshot["slice"]["progress"], 0.42)
        self.assertEqual(snapshot["slice"]["state"], "running")
        self.finish_native()
        snapshot = self.poll()
        self.assertEqual(snapshot["slice"]["state"], "completed")
        self.assertTrue(snapshot["slice"]["valid"])
        result = snapshot["slice"]["result"]
        self.assertEqual(result["print_time_seconds"], 3723)
        self.assertAlmostEqual(result["material"]["length_mm"], 1270.0)
        self.assertAlmostEqual(result["material"]["weight_g"], 3.6)
        self.assertEqual(result["source"], "ste_slicer")
        self.assertEqual(snapshot["slice"]["observation"]["state"], "finished")
        self.assertEqual(snapshot["tool_events"], initial_tools)
        self.assertEqual(len(self.llm.requests), 2)
        self.assertEqual(self.app.backend.calls, 2)  # One setup slice + one explicit agent start.
        self.assertIsNone(self.agent._operations["slice"])

    def test_llm_network_error_does_not_stop_owned_slice(self):
        snapshot = self.launch_slice(LLMError("network_error", "Fake provider failure"))
        self.assertTrue(snapshot["messages"][-1]["is_error"])
        self.assertEqual(snapshot["agent_state"], "idle")
        self.finish_native()
        self.assertTrue(self.poll()["slice"]["valid"])
        self.assertEqual(len(self.llm.requests), 2)

    def test_llm_step_limit_does_not_stop_observer_or_increase_eight_requests(self):
        answers = [calls(tool("get_slicer_state", call_id="read-" + str(i))) for i in range(MAX_LLM_STEPS - 1)]
        snapshot = self.launch_slice(*answers)
        self.assertTrue(snapshot["messages"][-1]["is_error"])
        self.assertEqual(len(self.llm.requests), MAX_LLM_STEPS)
        self.finish_native()
        self.assertEqual(self.poll()["slice"]["state"], "completed")
        self.assertEqual(len(self.llm.requests), MAX_LLM_STEPS)

    def test_export_both_native_files_become_download_ready_after_idle(self):
        for name in ("export_gcode", "save_project_3mf"):
            snapshot = self.send(calls(tool(name, {"slice_id": self.slice_id})), {"content": "Writer запущен"})
            self.export_id = snapshot["export"]["export_id"]
            self.assertEqual(snapshot["export"]["state"], "queued")
            self.assertEqual(snapshot["export"]["slice_id"], self.slice_id)
            self.assertNotIn("local_directory", json.dumps(snapshot))
            request_count = len(self.llm.requests)
            native = self.wait_status(self.export_id)
            snapshot = self.poll("export")
            self.assertEqual(snapshot["export"]["state"], "completed")
            artifact = snapshot["artifacts"][-1]
            stream, metadata = self.pump_until_done(self.worker(lambda: self.agent.open_download(artifact["file_id"])))
            with stream:
                content = stream.read()
            with open(native["local_directory"] + "/" + metadata["file_name"], "rb") as expected:
                self.assertEqual(content, expected.read())
            self.assertEqual(len(self.llm.requests), request_count)
            self.assertIsNone(self.agent._operations["export"])
            self.assertIsNone(self.agent._operations["slice"])
            self.assertNotIn("path", artifact)
            self.assertNotIn("identity", artifact)
            self.assertNotIn(CONFIG["api_key"], json.dumps(snapshot))

    def test_busy_second_export_is_not_started_and_turn_continues(self):
        # Observed in STE: G-code and 3MF requested back to back; the second write is refused as busy.
        snapshot = self.send(calls(tool("export_gcode", {"slice_id": self.slice_id}),
                                   tool("save_project_3mf", {"slice_id": self.slice_id}, "project")),
                             {"content": "G-code записывается, проект сохраню следом"})
        results = [e["data"]["result"] for e in snapshot["tool_events"] if e["type"] == "tool_finished"]
        self.assertTrue(results[0]["ok"], results[0])
        self.assertEqual((results[1]["error"]["code"], results[1]["error"]["executed"]), ("export_busy", False))
        self.assertIn("wait_for_export", results[1]["error"]["message"])
        self.assertFalse(snapshot["messages"][-1]["is_error"])
        self.assertEqual(len(self.llm.requests), 2, "The model got the refusal and answered; the turn was not cut")
        self.assertTrue(self.adapter.get_slicing_status(self.slice_id)["data"]["valid"], "A refusal does not stale the slice")
        # Let the accepted G-code writer finish: Windows cannot delete the still-open temp file.
        self.assertEqual(self.wait_status(results[0]["data"]["export_id"])["state"], "completed")
        self.clean_workers()

    def test_duplicate_reads_and_terminal_do_not_consume_events_or_poll_forever(self):
        self.launch_slice()
        self.poll()
        previous = self.agent.get_session(self.sid)
        self.now = 50
        self.assertEqual(self.poll(), previous)
        self.finish_native()
        record = self.agent._operations["slice"]
        snapshot = self.poll()
        with mock.patch.object(self.agent.tools, "call") as read:
            self.agent._observe_operation(record)
            read.assert_not_called()
        self.assertEqual(self.agent.get_session(self.sid), snapshot)

    def test_native_cancel_failure_is_terminal_and_never_restarts_engine(self):
        self.launch_slice()
        self.app.backend.slicingCancelled.emit()
        snapshot = self.poll()
        self.assertEqual(snapshot["slice"]["state"], "failed")
        self.assertEqual(snapshot["slice"]["error"]["code"], "slicing_cancelled")
        self.assertIsNone(snapshot["slice"]["result"])
        self.assertIsNone(self.agent._operations["slice"])
        self.assertEqual(self.app.backend.calls, 2)

    def test_native_timeout_is_failed_but_does_not_cancel_or_release_engine(self):
        self.launch_slice()
        deadline = self.adapter._slicing.record.deadline
        self.adapter._slicing.clock = lambda: deadline + 0.01
        snapshot = self.poll()
        self.assertEqual(snapshot["slice"]["error"]["code"], "slicing_timeout")
        self.assertEqual(snapshot["slice"]["state"], "failed")
        self.assertTrue(self.app.backend._slicing)
        self.assertTrue(self.adapter._change_lock.locked())
        self.assertIsNone(self.agent._operations["slice"])

    def test_native_external_change_before_poll_marks_completed_stale_without_result(self):
        self.launch_slice()
        self.finish_native()
        self.assertTrue(self.adapter.set_setting("layer_height", "global", 0.24)["ok"])
        snapshot = self.poll()
        self.assertEqual(snapshot["slice"]["state"], "completed")
        self.assertFalse(snapshot["slice"]["valid"])
        self.assertIsNone(snapshot["slice"]["result"])
        self.assertIsNone(self.agent._operations["slice"])

    def held_read(self, name):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        native = self.agent.tools.call
        def read(tool_name, arguments):
            result = native(tool_name, arguments)
            if tool_name == name:
                entered.set()
                if not release.wait(2):
                    raise AssertionError("Test did not release captured read")
            return result
        patch = mock.patch.object(self.agent.tools, "call", side_effect=read)
        patch.start()
        self.addCleanup(patch.stop)
        record = self.agent._operations["slice"]
        worker = self.worker(lambda: self.agent._observe_operation(record))
        self.app.callbacks.get(timeout=1)()
        if name == "get_slicing_result":
            self.app.callbacks.get(timeout=1)()
        self.assertTrue(entered.wait(1))
        return worker, release

    def test_pending_read_before_busy_mutation_is_fenced_but_observation_resumes(self):
        initial = self.launch_slice()
        worker, release = self.held_read("get_slicing_status")
        self.send(calls(tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.24})), {"content": "Отказ"})
        snapshot = self.agent.get_session(self.sid)
        self.assertEqual(snapshot["slice"], initial["slice"])
        self.assertIsNotNone(self.agent._operations["slice"])
        count = snapshot["last_event_id"]
        release.set()
        self.finish(worker)
        self.assertEqual(self.agent.get_session(self.sid)["last_event_id"], count)
        self.finish_native()
        self.assertTrue(self.poll()["slice"]["valid"])

    def test_late_result_after_mutation_cannot_revive_known_stale_even_with_new_receipt(self):
        self.launch_slice()
        self.finish_native()
        old = self.adapter.get_slicing_result(self.agent.get_session(self.sid)["slice"]["slice_id"])
        worker, release = self.held_read("get_slicing_result")
        self.send(calls(tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.24})), {"content": "Изменено"})
        snapshot = self.agent.get_session(self.sid)
        self.assertFalse(snapshot["slice"]["valid"])
        self.assertEqual(snapshot["slice"]["observation"]["state"], "stopped")
        release.set()
        self.finish(worker)
        self.assertEqual(self.agent.get_session(self.sid), snapshot)
        self.agent._active_turn = "late"
        self.agent._remember_result(self.sid, "late", "get_slicing_result", old, self.agent._prepare_tool("get_slicing_result"))
        self.assertEqual(self.agent.get_session(self.sid), snapshot)

    def test_late_result_after_new_owner_cannot_replace_new_session_operation(self):
        self.launch_slice()
        self.finish_native()
        worker, release = self.held_read("get_slicing_result")
        other = self.agent.create_session()["session_id"]
        snapshot = self.launch_slice(sid=other)
        new_id = snapshot["slice"]["slice_id"]
        release.set()
        self.finish(worker)
        self.assertEqual(self.agent.get_session(other), snapshot)
        self.assertEqual(self.agent._operations["slice"]["id"], new_id)
        self.assertFalse(self.agent.get_session(self.sid)["slice"]["valid"])

    def test_errors_backoff_then_interrupt_without_fake_failed_and_no_secret(self):
        self.launch_slice()
        record = self.agent._operations["slice"]
        with mock.patch.object(self.agent.tools, "call", side_effect=ValueError(CONFIG["api_key"])) as read:
            for delay in (1, 2, 4):
                self.agent._observe_operation(record)
                self.assertEqual(record["next"], self.now + delay)
            self.agent._observe_operation(record)
            self.agent._observe_operation(record)
            self.assertEqual(read.call_count, 4)
        snapshot = self.agent.get_session(self.sid)
        self.assertEqual(snapshot["slice"]["state"], "running")
        self.assertEqual(snapshot["slice"]["observation"]["state"], "stopped")
        self.assertEqual(snapshot["slice"]["observation"]["error"]["code"], "observation_interrupted")
        self.assertNotIn(CONFIG["api_key"], json.dumps(snapshot))
        self.assertTrue(self.app.backend._slicing)
        self.assertIsNone(self.agent._operations["slice"])

    def test_one_lazy_worker_close_while_qt_pending_no_join_no_late_events(self):
        self.assertIsNone(self.agent._observer_thread)
        self.launch_slice()
        self.agent._operations["slice"]["next"] = 0
        AgentService._start_observer(self.agent)
        observer = self.agent._observer_thread
        AgentService._start_observer(self.agent)
        self.assertIs(self.agent._observer_thread, observer)
        callback = self.app.callbacks.get(timeout=1)
        previous = copy.deepcopy(self.agent._sessions[self.sid]["events"])
        before = time.monotonic()
        self.agent.close()  # Main Qt thread must not wait for its own callback.
        self.assertLess(time.monotonic() - before, 0.2)
        callback()
        observer.join(1)  # Test cleanup only; production close never joins.
        self.assertFalse(observer.is_alive())
        self.assertEqual(self.agent._sessions[self.sid]["events"], previous)
        self.assertTrue(self.app.callbacks.empty())
        self.assertTrue(self.app.backend._slicing)

    def test_direct_diagnostic_start_does_not_register_history_or_start_observer(self):
        result = self.agent.tools.call("start_slicing", {})
        self.assertTrue(result["ok"])
        for unused in range(20):
            self.agent.create_session()
        self.assertIsNone(self.agent._observer_thread)
        self.assertEqual(self.agent._operations, {"slice": None, "export": None})
        self.assertIsNone(self.agent.get_session(self.sid)["slice"])
        self.assertTrue(self.agent.get_status()["capabilities"]["background_slice_events"])
        dev = AgentService()
        self.addCleanup(dev.close)
        self.assertFalse(dev.get_status()["capabilities"]["background_slice_events"])

    def test_live_single_worker_confirms_result_after_idle_and_retires_terminal(self):
        self.launch_slice()
        self.finish_native()
        self.agent._operations["slice"]["next"] = 0
        completed = threading.Event()
        apply = self.agent._apply_result
        def publish(*args, **kwargs):
            apply(*args, **kwargs)
            if args[1] == "get_slicing_result":
                completed.set()
        with mock.patch.object(self.agent, "_apply_result", side_effect=publish):
            AgentService._start_observer(self.agent)
            self.app.callbacks.get(timeout=1)()
            self.app.callbacks.get(timeout=1)()
            self.assertTrue(completed.wait(1))
        self.assertTrue(self.agent.get_session(self.sid)["slice"]["valid"])
        self.assertIsNone(self.agent._operations["slice"])
        self.assertEqual(len(self.llm.requests), 2)
        self.agent.close()
        self.agent._observer_thread.join(1)
        self.assertFalse(self.agent._observer_thread.is_alive())

    def test_late_download_registration_after_mutation_publishes_no_artifact(self):
        snapshot = self.send(calls(tool("export_gcode", {"slice_id": self.slice_id})), {"content": "Запуск"})
        self.export_id = snapshot["export"]["export_id"]
        self.wait_status(self.export_id)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        register = self.agent.downloads.register
        def held_register(identity):
            result = register(identity)
            entered.set()
            if not release.wait(2):
                raise AssertionError("Test registration not released")
            return result
        with mock.patch.object(self.agent.downloads, "register", side_effect=held_register):
            record = self.agent._operations["export"]
            worker = self.worker(lambda: self.agent._observe_operation(record))
            self.app.callbacks.get(timeout=1)()  # get_export_status
            self.app.callbacks.get(timeout=1)()  # internal resolver
            self.assertTrue(entered.wait(1))
            self.send(calls(tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.24})), {"content": "Изменено"})
            previous = self.agent.get_session(self.sid)
            release.set()
            self.finish(worker)
        self.assertEqual(self.agent.get_session(self.sid), previous)
        self.assertEqual(previous["artifacts"], [])
        self.assertFalse(previous["export"]["valid"])

    def test_rejected_unexecuted_tool_and_foreign_stale_read_do_not_lose_observation(self):
        snapshot = self.launch_slice()
        record = self.agent._operations["slice"]
        self.send(calls(tool("set_setting", {"invalid": True})), {"content": "Ошибка аргументов"})
        self.assertIs(self.agent._operations["slice"], record)
        self.assertEqual(self.agent.get_session(self.sid)["slice"], snapshot["slice"])
        self.agent._active_turn = "foreign"
        receipt = self.agent._prepare_tool("get_slicing_result", {"slice_id": "foreign-old"})
        self.agent._remember_result(self.sid, "foreign", "get_slicing_result",
            {"ok": False, "error": {"code": "slice_result_stale", "message": "Old result"}}, receipt)
        self.assertEqual(self.agent.get_session(self.sid)["slice"], snapshot["slice"])
        self.assertIs(self.agent._operations["slice"], record)

    def test_premature_result_then_idle_still_observes_actual_completed_result(self):
        snapshot = self.launch_slice()
        identity = snapshot["slice"]["slice_id"]
        record = self.agent._operations["slice"]
        snapshot = self.send(calls(tool("get_slicing_result", {"slice_id": identity})), {"content": "Ещё не готово"})
        errors = [e["data"]["result"]["error"]["code"] for e in snapshot["tool_events"]
                  if e["type"] == "tool_finished" and not e["data"]["result"]["ok"]]
        self.assertIn("slice_not_ready", errors)
        self.assertEqual(snapshot["agent_state"], "idle")
        self.assertIs(self.agent._operations["slice"], record)
        self.assertEqual(snapshot["slice"]["observation"]["state"], "watching")
        # A transient result-read error has the same nonterminal semantics.
        self.agent._active_turn = "transient"
        receipt = self.agent._prepare_tool("get_slicing_result", {"slice_id": identity})
        self.agent._remember_result(self.sid, "transient", "get_slicing_result",
            {"ok": False, "error": {"code": "qt_timeout", "message": "Qt read timed out"}}, receipt)
        self.agent._active_turn = None
        self.assertIs(self.agent._operations["slice"], record)
        requests = len(self.llm.requests)
        self.finish_native()
        snapshot = self.poll()
        self.assertEqual(snapshot["slice"]["state"], "completed")
        self.assertEqual(snapshot["slice"]["result"]["print_time_seconds"], 3723)
        self.assertEqual(len(self.llm.requests), requests)

    def test_real_mode_switch_guard_refusal_preserves_owned_live_observer(self):
        snapshot = self.launch_slice()
        record = self.agent._operations["slice"]
        snapshot = self.send(calls(tool("set_print_mode", {"mode": "cylindrical"})), {"content": "Режим занят"})
        result = [e["data"]["result"] for e in snapshot["tool_events"] if e["type"] == "tool_finished"][-1]
        self.assertEqual(result["error"]["code"], "switch_in_progress")
        self.assertIs(self.agent._operations["slice"], record)
        self.assertEqual(snapshot["slice"]["state"], "running")
        self.assertEqual(snapshot["slice"]["observation"]["state"], "watching")
        self.assertEqual(self.app.stack.mode, "classic")
        self.assertEqual(self.app.backend.calls, 2)
        self.finish_native()
        self.assertTrue(self.poll()["slice"]["valid"])


if __name__ == "__main__":
    unittest.main()
