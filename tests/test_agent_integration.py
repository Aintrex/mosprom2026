"""Agent merge: real registry/bridge, scripted LLM/STE; no provider or native run."""
import copy
import importlib
import io
import json
import queue
import ssl
import sys
import threading
import time
import types
import unittest
import urllib.error
from unittest import mock
from urllib.request import Request, urlopen

import test_slicer_settings as settings_tests
import test_slicer_slicing as slicing_tests
import test_print_settings as print_tests
from AIAssistant.agent.config import PreferencesConfigStore, validate_config
from AIAssistant.agent.llm import ChatCompletionsClient, LLMError, sanitize_secret
from AIAssistant.agent.service import AgentService, APIError, MAX_LLM_STEPS, MAX_TOOL_CALLS, TOTAL_BUDGET_SECONDS
from AIAssistant.server import create_server
from AIAssistant.slicer.qt_bridge import QtBridge, SlicerError


CONFIG = {"base_url": "https://provider.invalid/v1", "model": "user-model", "api_key": "unit-secret-+/"}


def tool(name, arguments=None, call_id="call-1"):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments or {}, allow_nan=False)}}


def calls(*items):
    return {"role": "assistant", "content": None, "tool_calls": list(items)}


class ScriptedLLM(object):
    def __init__(self, *answers):
        self.answers, self.requests, self.closed = list(answers), [], False

    def complete(self, config, messages, tools, timeout):
        self.requests.append(copy.deepcopy((config, messages, tools, timeout)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer(self, messages) if callable(answer) else answer

    def close(self):
        self.closed = True


class Preferences(object):
    def __init__(self, app):
        self.app, self.values, self.writes = app, {}, []
        self.ignore, self.failure = False, None

    def check(self):
        if threading.current_thread() is not self.app.main:
            raise AssertionError("Preferences accessed outside Qt")

    def addPreference(self, key, value):
        self.check()
        self.values.setdefault(key, value)

    def getValue(self, key):
        self.check()
        return self.values[key]

    def setValue(self, key, value):
        self.check()
        self.writes.append((key, value))
        if self.failure:
            raise self.failure
        if not self.ignore:
            self.values[key] = value


class ConfigTests(unittest.TestCase):
    worker = settings_tests.SettingsTests.worker
    finish = settings_tests.SettingsTests.finish

    def setUp(self):
        self.app = settings_tests.App()
        self.prefs = Preferences(self.app)
        self.app.getPreferences = lambda: self.prefs
        self.bridge = QtBridge(self.app, timeout=0.1)
        self.addCleanup(self.bridge.close)
        self.store = PreferencesConfigStore(self.bridge)

    def test_worker_read_write_confirm_keep_clear_and_restart(self):
        worker = self.worker(self.store.load)
        callback = self.app.callbacks.get(timeout=1)
        self.assertFalse(worker[3].is_set())
        callback()
        self.assertEqual(self.finish(worker), {"base_url": "", "model": "", "api_key": ""})
        worker = self.worker(lambda: self.store.save(**CONFIG))
        self.app.callbacks.get(timeout=1)()
        self.assertEqual(self.finish(worker), {"configured": True, "has_key": True})
        self.store.save(CONFIG["base_url"], "second-model")
        self.assertEqual(self.store.snapshot()["api_key"], CONFIG["api_key"])
        restarted = PreferencesConfigStore(self.bridge)
        self.assertEqual(restarted.load(), self.store.snapshot())
        self.store.save(CONFIG["base_url"], "second-model", clear_api_key=True)
        self.assertEqual(self.store.snapshot()["api_key"], "")

    def test_ignored_or_failed_write_never_confirms_cache(self):
        self.store.load()
        self.store.save(**CONFIG)
        self.prefs.ignore = True
        with self.assertRaises(SlicerError) as caught:
            self.store.save(CONFIG["base_url"], "ignored")
        self.assertEqual(caught.exception.code, "llm_config_not_applied")
        with self.assertRaises(SlicerError):
            self.store.snapshot()
        self.assertEqual(self.store.load()["model"], CONFIG["model"])
        self.prefs.failure = ValueError("private")
        with self.assertRaises(SlicerError):
            self.store.save(CONFIG["base_url"], "failed")
        with self.assertRaises(SlicerError):
            self.store.snapshot()

    def test_pending_timeout_no_late_write_and_requires_reload(self):
        self.store.load()
        worker = self.worker(lambda: self.store.save(**CONFIG))
        late = self.app.callbacks.get(timeout=1)
        self.assertTrue(worker[3].wait(1))
        self.assertEqual(worker[2][0].code, "qt_timeout")
        late()
        self.assertEqual(self.prefs.writes, [])
        with self.assertRaises(SlicerError):
            self.store.snapshot()
        self.assertFalse(self.store.load()["api_key"])

    def test_busy_load_does_not_block_qt_on_worker_save(self):
        worker = self.worker(lambda: self.store.save(**CONFIG))
        callback = self.app.callbacks.get(timeout=1)
        with self.assertRaises(SlicerError) as caught:
            self.store.load()
        self.assertEqual(caught.exception.code, "llm_config_busy")
        callback()
        self.assertTrue(self.finish(worker)["configured"])

    def test_urls_and_headers_rejected_before_preferences(self):
        for url in ("file:///tmp", "https://user:pass@provider/", "https://provider/?key=x",
                    "https://provider/#", "https://provider/?", "https://provider:0", "https://provider:bad"):
            with self.assertRaises(ValueError):
                self.store.save(url, "model", "key")
        for field in CONFIG:
            with self.assertRaises(ValueError):
                validate_config(dict(CONFIG, **{field: "x\r\nprivate"}))
        self.assertEqual(self.prefs.writes, [])


class ClientTests(unittest.TestCase):
    def response(self, message=None, raw=None, finish_reason="stop"):
        if raw is None:
            raw = json.dumps({"choices": [{"finish_reason": finish_reason,
                                          "message": message or {"role": "assistant", "content": "answer"}}]}).encode("utf-8")
        return io.BytesIO(raw)

    def test_chat_payload_tls_tool_shape_response_closed(self):
        response, captured = self.response(), []
        def open_fake(req, **kwargs):
            captured.append((req, kwargs))
            self.assertIsNot(threading.current_thread(), threading.main_thread())
            return response
        client = ChatCompletionsClient(open_fake)
        self.assertEqual(client.complete(CONFIG, [{"role": "user", "content": "hello"}],
                                         [{"type": "function", "function": {"name": "get_slicer_state", "parameters": {}}}], 99)["content"], "answer")
        req, kwargs = captured[0]
        self.assertEqual(req.full_url, CONFIG["base_url"] + "/chat/completions")
        self.assertEqual(req.get_header("Authorization"), "Bearer " + CONFIG["api_key"])
        self.assertEqual(kwargs["timeout"], 60)
        self.assertEqual(kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(kwargs["context"].check_hostname)
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["model"], CONFIG["model"])
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertFalse(payload["stream"])
        self.assertTrue(response.closed)

    def test_error_body_and_key_never_leave_client(self):
        def denied(req, **kwargs):
            raise urllib.error.HTTPError(req.full_url, 401, CONFIG["api_key"], {}, io.BytesIO(CONFIG["api_key"].encode()))
        with self.assertRaises(LLMError) as caught:
            ChatCompletionsClient(denied).complete(CONFIG, [])
        self.assertEqual(caught.exception.code, "auth_error")
        self.assertNotIn(CONFIG["api_key"], str(caught.exception))
        for secret in (CONFIG["api_key"], "x"):
            self.assertNotIn(secret, sanitize_secret(secret, secret))

    def test_bad_incomplete_oversize_json_is_not_tool_answer(self):
        cases = [(b"not JSON", "invalid_response"), (b'{"choices":NaN}', "invalid_response"),
                 (b"x" * (1024 * 1024 + 1), "response_too_large")]
        for raw, code in cases:
            response = self.response(raw=raw)
            with self.assertRaises(LLMError) as caught:
                ChatCompletionsClient(lambda *a, **k: response).complete(CONFIG, [])
            self.assertEqual(caught.exception.code, code)
            self.assertTrue(response.closed)
        response = self.response(calls(tool("start_slicing")), finish_reason="length")
        with self.assertRaises(LLMError) as caught:
            ChatCompletionsClient(lambda *a, **k: response).complete(CONFIG, [])
        self.assertEqual(caught.exception.code, "incomplete_response")

    def test_finite_wait_and_single_hanging_transport_no_retry(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def hung(req, **kwargs):
            entered.set()
            release.wait(2)
            finished.set()
            return self.response()
        client = ChatCompletionsClient(hung)
        self.addCleanup(release.set)
        config = dict(CONFIG, base_url="http://provider.invalid/v1")
        began = time.monotonic()
        with self.assertRaises(LLMError) as caught:
            client.complete(config, [], timeout=0.03)
        self.assertLess(time.monotonic() - began, 0.5)
        self.assertEqual(caught.exception.code, "network_timeout")
        self.assertTrue(entered.is_set())
        with self.assertRaises(LLMError) as caught:
            client.complete(CONFIG, [])
        self.assertEqual(caught.exception.code, "request_busy")
        client.close()
        release.set()
        self.assertTrue(finished.wait(1))
        with self.assertRaises(LLMError) as caught:
            client.complete(CONFIG, [])
        self.assertEqual(caught.exception.code, "service_stopped")


class AgentTests(unittest.TestCase):
    worker = settings_tests.SettingsTests.worker
    finish = settings_tests.SettingsTests.finish
    pump_until_done = slicing_tests.SlicingTests.pump_until_done
    drain = slicing_tests.SlicingTests.drain

    def setUp(self):
        print_tests.PrintSettingsTests.setUp(self)

    def service(self, *answers, **kwargs):
        self.llm = ScriptedLLM(*answers)
        self.agent = AgentService(self.adapter, lambda: dict(CONFIG), self.llm, run_async=kwargs.get("run_async", False))
        self.addCleanup(self.agent.close)
        self.sid = self.agent.create_session()["session_id"]
        return self.agent

    def send(self, text="Установи высоту слоя", cmid="client-1"):
        return self.agent.submit_message(self.sid, {"text": text, "client_message_id": cmid})

    def snapshot(self):
        return self.agent.get_session(self.sid)

    def results(self):
        return [e["data"]["result"] for e in self.snapshot()["tool_events"] if e["type"] == "tool_finished"]

    def test_actual_registry_worker_write_read_schemas_and_history(self):
        self.service(calls(tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.24}),
                           tool("get_setting", {"key": "layer_height", "scope": "global"}, "call-2")),
                     {"role": "assistant", "content": "Слой подтверждён"})
        response = self.pump_until_done(self.worker(self.send))
        self.assertEqual(response["agent_state"], "processing")
        self.assertEqual(self.app.stack.writes, [("layer_height", 0.24)])
        self.assertEqual(self.results()[0]["data"]["before"], 0.2)
        self.assertEqual(self.results()[1]["data"]["value"], 0.24)
        schemas = self.llm.requests[0][2]
        expected = [t for t in self.tools.list_tools() if t["name"] not in ("assistant_status", "load_model")]
        self.assertEqual({t["function"]["name"]: t["function"]["parameters"] for t in schemas},
                         {t["name"]: {k: v for k, v in t["inputSchema"].items() if k != "oneOf"} for t in expected})
        self.assertEqual([m["role"] for m in self.llm.requests[1][1]], ["system", "user", "assistant", "tool", "tool"])
        self.assertEqual(self.snapshot()["agent_state"], "idle")
        self.assertEqual(len(self.snapshot()["messages"]), 2)

    def test_capabilities_question_hallucinated_mutation_never_reaches_app(self):
        self.service(calls(tool("start_slicing"), tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.24}, "b")),
                     {"content": "Доступны инструменты"})
        self.send("Что умеешь?")
        self.assertEqual(self.app.backend.calls, 0)
        self.assertEqual(self.app.stack.writes, [])
        self.assertTrue(all(not self.tools.is_mutating(t["function"]["name"]) for t in self.llm.requests[0][2]))
        self.assertTrue(all(r["error"]["code"] == "invalid_tool_arguments" for r in self.results()))

    def test_missing_config_and_network_failure_no_hidden_changes(self):
        self.service(LLMError("network_error", "Сеть недоступна"))
        self.agent._config_provider = lambda: {"base_url": "", "model": "", "api_key": ""}
        with self.assertRaises(APIError) as caught:
            self.send()
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(self.llm.requests, [])
        self.assertEqual(self.snapshot()["messages"], [])
        self.agent._config_provider = lambda: dict(CONFIG)
        self.send()
        self.assertTrue(self.snapshot()["messages"][-1]["is_error"])
        self.assertEqual(self.app.stack.writes, [])
        self.assertEqual(self.app.backend.calls, 0)

    def test_unknown_invalid_args_and_diagnostic_path_never_touch_api(self):
        invalid = tool("set_setting", {"key": "layer_height", "scope": "global", "value": "0.24"})
        self.service(calls(invalid, tool("invented", call_id="b"), tool("load_model", {"path": "C:/private.stl"}, "c")),
                     {"content": "Ошибка аргументов"})
        with mock.patch.object(self.adapter, "set_setting", side_effect=AssertionError("No API")), \
                mock.patch.object(self.adapter, "load_model", side_effect=AssertionError("No API")):
            self.send()
        self.assertEqual(len(self.results()), 3)
        self.assertTrue(all(not r["ok"] for r in self.results()))

    def test_uncertain_write_stops_same_batch_and_no_retry_history_complete(self):
        self.service(calls(tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.24}),
                           tool("start_slicing", call_id="b")))
        # No Qt pump: real bridge pending callback times out and is cancelled.
        self.finish(self.worker(self.send))
        self.drain()
        self.assertEqual(self.app.stack.writes, [])
        self.assertEqual(self.app.backend.calls, 0)
        self.assertEqual([r["error"]["code"] for r in self.results()], ["qt_timeout", "tool_not_executed"])
        self.assertEqual(len(self.llm.requests), 1)
        self.assertEqual(self.agent._sessions[self.sid]["llm_blocks"][0][-1]["role"], "assistant")
        self.assertEqual(self.snapshot()["agent_state"], "idle")
        event = [e for e in self.agent.get_events(self.sid)["events"] if e["type"] == "assistant_message"][-1]
        self.assertEqual(event["data"]["event_id"], event["id"])

    def test_partial_success_survives_llm_error_and_whole_block_history_bound(self):
        self.service(calls(tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.24})),
                     LLMError("network_error", "Невозможно продолжить"))
        self.send()
        self.assertEqual(self.app.stack.writes, [("layer_height", 0.24)])
        self.assertIn("set_setting", self.snapshot()["messages"][-1]["text"])
        self.llm.answers += [{"content": "ok"}] * 13
        for i in range(13):
            self.send("Продолжение", "next-" + str(i))
        blocks = self.agent._sessions[self.sid]["llm_blocks"]
        self.assertEqual(len(blocks), 12)
        self.assertTrue(all(b[0]["role"] == "user" and b[-1]["role"] == "assistant" for b in blocks))
        initial = self.llm.requests[2][1]
        self.assertEqual([m["role"] for m in initial[:5]], ["system", "user", "assistant", "tool", "assistant"])

    def test_idempotency_whole_body_even_after_finished_and_conflict(self):
        self.service({"content": "ok"})
        response = self.send()
        self.assertEqual(self.send(), response)
        self.assertEqual(len(self.llm.requests), 1)
        for extra in ({"text": "Другой текст"}, {"attachment_ids": ["file-1"]}):
            body = dict({"text": "Установи высоту слоя", "client_message_id": "client-1"}, **extra)
            with self.assertRaises(APIError) as caught:
                self.agent.submit_message(self.sid, body)
            self.assertEqual(caught.exception.code, "message_id_conflict")
        with self.assertRaises(APIError) as caught:
            self.agent.submit_message(self.sid, {"text": "file", "client_message_id": "new", "attachment_ids": ["file-1"]})
        self.assertEqual(caught.exception.code, "file_not_found")

    def test_one_active_turn_global_close_rejects_late_answer_and_tools(self):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        def late(client, messages):
            entered.set()
            release.wait(2)
            done.set()
            return calls(tool("start_slicing"))
        self.service(late, run_async=True)
        self.addCleanup(release.set)
        response = self.send()
        self.assertTrue(entered.wait(1))
        other = self.agent.create_session()["session_id"]
        with self.assertRaises(APIError) as caught:
            self.agent.submit_message(other, {"text": "hi", "client_message_id": "different"})
        self.assertEqual(caught.exception.code, "agent_busy")
        self.assertEqual(self.send(), response)
        self.agent.close()
        prior = copy.deepcopy(self.agent._sessions[self.sid])
        release.set()
        self.assertTrue(done.wait(1))
        time.sleep(0.02)
        self.drain()
        self.assertEqual(self.app.backend.calls, 0)
        self.assertEqual(self.agent._sessions[self.sid], prior)
        self.assertTrue(self.llm.closed)

    def test_direct_mcp_write_invalidates_slice_and_respects_busy_agent(self):
        self.service()
        session = self.agent._sessions[self.sid]
        session["slice"] = {"slice_id": "s1", "state": "completed", "progress": 1.0, "result": {"x": 1}, "valid": True, "error": None}
        direct = self.agent.mcp_tools
        with self.assertRaises(ValueError):
            direct.call("set_setting", {"key": "layer_height", "scope": "global", "value": "0.12"})  # MCP stays strict
        self.agent._active_turn = "other-turn"
        self.assertEqual(direct.call("set_setting", {"key": "layer_height", "scope": "global", "value": 0.12})["error"]["code"], "agent_busy")
        self.assertEqual(self.app.stack.writes, [])
        self.agent._active_turn = None
        self.assertTrue(direct.call("set_setting", {"key": "layer_height", "scope": "global", "value": 0.12})["ok"])
        self.assertEqual(self.app.stack.writes, [("layer_height", 0.12)])
        self.assertFalse(session["slice"]["valid"])
        self.assertIn("s1", session["_invalidated"])
        self.assertFalse(self.agent._write_pending)

    def test_step_tool_and_remaining_time_budgets(self):
        self.service(*[calls(tool("list_models", call_id="c" + str(i))) for i in range(MAX_LLM_STEPS)])
        self.send("Диагностика")
        self.assertEqual(len(self.llm.requests), MAX_LLM_STEPS)
        self.assertEqual(self.agent.get_events(self.sid)["events"][-3]["data"]["code"], "step_limit")
        self.service(calls(*[tool("list_models", call_id="c" + str(i)) for i in range(MAX_TOOL_CALLS)]),
                     calls(tool("start_slicing", call_id="last")))
        self.send("Диагностика")
        self.assertEqual(len(self.results()), MAX_TOOL_CALLS + 1)
        self.assertEqual(self.results()[-1]["error"]["code"], "tool_limit")
        self.assertEqual(self.app.backend.calls, 0)
        now = [0.0]
        def spend(client, messages):
            now[0] = TOTAL_BUDGET_SECONDS - 0.2
            return calls(tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.24}))
        self.service(spend)
        self.agent.clock = lambda: now[0]
        self.send()
        self.assertEqual(self.results()[0]["error"]["code"], "budget_exceeded")
        self.assertEqual(self.app.stack.writes, [])
        now[0] = 0
        def spend_request(client, messages):
            now[0] = TOTAL_BUDGET_SECONDS - 0.1
            return calls(tool("invented"))
        self.service(spend_request, {"content": "ok"})
        self.agent.clock = lambda: now[0]
        self.send()
        self.assertAlmostEqual(self.llm.requests[1][3], 0.1)

    def test_bad_message_empty_duplicate_ids_and_oversize_batch_release_busy(self):
        for answer in ({}, {"role": "user", "content": "oops"}, calls(tool("list_models"), tool("list_models")),
                       calls(*[tool("start_slicing", call_id=str(i)) for i in range(MAX_TOOL_CALLS + 1)])):
            self.service(answer)
            self.send()
            self.assertEqual(self.snapshot()["agent_state"], "idle")
            self.assertTrue(self.snapshot()["messages"][-1]["is_error"])
        self.assertEqual(self.app.backend.calls, 0)

    def test_secrets_not_in_public_surfaces_or_history(self):
        self.service({"content": "Provider says " + CONFIG["api_key"]})
        self.send("Мой ключ " + CONFIG["api_key"])
        for surface in (self.snapshot(), self.agent.get_status(), self.agent.get_events(self.sid),
                        self.agent._sessions[self.sid]["llm_blocks"]):
            self.assertNotIn(CONFIG["api_key"], json.dumps(surface))
            self.assertNotIn('"api_key"', json.dumps(surface))
        self.assertNotIn(CONFIG["api_key"], self.llm.requests[0][1][1]["content"])

    def test_events_pagination_expiry_future_cursor_and_snapshot_copy(self):
        self.service({"content": "ok"})
        self.send()
        session = self.agent._sessions[self.sid]
        with self.agent._lock:
            for i in range(1100):
                self.agent._emit(session, "agent_state", {"state": "idle"})
        with self.assertRaises(APIError) as caught:
            self.agent.get_events(self.sid, 0)
        self.assertEqual(caught.exception.status, 410)
        start = session["events"][0]["id"] - 1
        page = self.agent.get_events(self.sid, start)
        self.assertEqual(len(page["events"]), 100)
        self.assertTrue(page["has_more"])
        page["events"][0]["data"]["state"] = "corrupted"
        self.assertEqual(self.agent.get_events(self.sid, start)["events"][0]["data"]["state"], "idle")
        last = self.snapshot()["last_event_id"]
        self.assertEqual(self.agent.get_events(self.sid, last)["next_after"], last)
        with self.assertRaises(APIError):
            self.agent.get_events(self.sid, last + 1)

    def test_completed_result_survives_status_and_stale_removes_it(self):
        sid = self.adapter.start_slicing()["data"]["slice_id"]
        self.app.backend.estimates()
        self.app.backend.finish()
        self.drain()
        self.service(calls(tool("get_slicing_result", {"slice_id": sid})), {"content": "Оценки"},
                     calls(tool("get_slicing_status", {"slice_id": sid})), {"content": "Статус"},
                     calls(tool("get_slicing_status", {"slice_id": sid})), {"content": "Устарел"})
        self.send("Результат")
        result = self.snapshot()["slice"]["result"]
        self.assertEqual(result["source"], "ste_slicer")
        self.send("Статус", "two")
        self.assertEqual(self.snapshot()["slice"]["result"], result)
        self.assertTrue(self.snapshot()["slice"]["valid"])
        self.adapter.set_setting("layer_height", "global", 0.24)
        self.send("Статус", "three")
        self.assertFalse(self.snapshot()["slice"]["valid"])
        self.assertIsNone(self.snapshot()["slice"]["result"])
        event = [e for e in self.agent.get_events(self.sid)["events"] if e["type"] == "slice_progress"][-1]
        self.assertFalse(event["data"]["valid"])
        self.assertIn("error", event["data"])

    def test_llm_step_limit_does_not_complete_or_fail_native_job(self):
        def start_then_poll(client, messages):
            responses = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
            sid = responses[0]["data"]["slice_id"]
            return calls(tool("get_slicing_status", {"slice_id": sid}, "poll-" + str(len(client.requests))))
        self.service(calls(tool("start_slicing")), *([start_then_poll] * 7))
        self.send("Нарежь classic")
        snapshot = self.snapshot()
        self.assertEqual(snapshot["agent_state"], "idle")
        self.assertEqual(snapshot["slice"]["state"], "running")
        sid = snapshot["slice"]["slice_id"]
        self.assertEqual(self.adapter.get_slicing_status(sid)["data"]["state"], "running")
        self.assertEqual(self.app.backend.calls, 1)
        self.app.backend.estimates()
        self.app.backend.finish()
        self.drain()
        self.assertTrue(self.adapter.get_slicing_result(sid)["ok"])
        self.assertEqual(self.snapshot()["slice"]["state"], "running")  # Last observed, no background monitor.

    def test_agent_mutation_publishes_cached_slice_invalidation(self):
        sid = self.adapter.start_slicing()["data"]["slice_id"]
        self.app.backend.estimates()
        self.app.backend.finish()
        self.drain()
        self.service(calls(tool("get_slicing_result", {"slice_id": sid})), {"content": "Результат"},
                     calls(tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.24})), {"content": "Изменено"})
        self.send("Результат")
        self.send("Измени слой", "next")
        event = [e for e in self.agent.get_events(self.sid)["events"] if e["type"] == "slice_progress"][-1]
        self.assertFalse(event["data"]["valid"])
        self.assertIsNone(event["data"]["result"])
        self.assertFalse(self.adapter.get_slicing_status(sid)["data"]["valid"])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.llm = ScriptedLLM({"content": "ok"})
        self.agent = AgentService(config_provider=lambda: dict(CONFIG), llm_client=self.llm, run_async=False)
        self.server = create_server(self.agent)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.addCleanup(self.cleanup_server)

    def cleanup_server(self):
        self.agent.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, body=None, headers=None):
        headers = dict(headers or {})
        if body is not None:
            headers.setdefault("Content-Type", "application/json")
        req = Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            response = urlopen(req, timeout=2)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            return response.status, json.loads(raw.decode()) if raw else None, response.headers

    def test_contract_status_202_sessions_events_and_retry(self):
        code, status, headers = self.request("/api/status")
        self.assertEqual(code, 200)
        self.assertFalse(status["slicer"]["connected"])
        self.assertTrue(status["llm"]["configured"])
        self.assertTrue(status["capabilities"]["attachments"])
        self.assertFalse(status["capabilities"]["background_slice_events"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        code, session, _ = self.request("/api/sessions", {})
        self.assertEqual(code, 201)
        sid = session["session_id"]
        path = "/api/sessions/" + sid + "/messages"
        body = {"text": "hello", "client_message_id": "retry-id", "attachment_ids": []}
        code, accepted, _ = self.request(path, body)
        self.assertEqual(code, 202)
        self.assertEqual(self.request(path, body)[1], accepted)
        self.assertEqual(len(self.llm.requests), 1)
        restored = self.request("/api/sessions/" + sid)[1]
        self.assertEqual([m["role"] for m in restored["messages"]], ["user", "assistant"])
        page = self.request("/api/events?session_id=" + sid + "&after=0")[1]
        self.assertEqual(page["next_after"], restored["last_event_id"])
        self.assertFalse(page["has_more"])
        self.assertEqual(self.request(path, dict(body, text="different"))[0], 409)
        for data in (status, accepted, restored, page):
            self.assertNotIn(CONFIG["api_key"], json.dumps(data))

    def test_typed_errors_host_origin_limits_and_no_extra_routes(self):
        for path in ("/api/sessions/missing", "/api/events?session_id=missing"):
            code, data, _ = self.request(path)
            self.assertEqual(code, 404)
            self.assertEqual(data["error"]["code"], "session_not_found")
        code, error, _ = self.request("/api/status", headers={"Origin": "http://localhost:1"})
        self.assertEqual(code, 403)
        self.assertEqual(error["error"]["code"], "forbidden_origin")
        self.assertEqual(self.request("/api/status", headers={"Host": "localhost:1"})[0], 403)
        for path in ("/api/settings", "/api/files", "/../AIAssistant.py", "/api/sessions/x/events"):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/api/sessions", {}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("/api/sessions", {"text": "x" * 66000})[0], 413)
        self.assertEqual(self.request("/api/events?session_id=x&after=bad")[0], 400)
        self.assertEqual(self.request("/api/events?session_id=x&session_id=y")[0], 400)

    def test_legacy_and_mcp_notification_do_not_call_agent_or_mutate(self):
        self.assertEqual(self.request("/api/health")[1], {"status": "ok", "mode": "plugin"})
        self.assertEqual(self.request("/api/message", {"text": "hello"})[1]["mode"], "scaffold")
        request = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "start_slicing", "arguments": {}}}
        with mock.patch.object(self.agent.tools, "call", side_effect=AssertionError("No notification execution")):
            self.assertEqual(self.request("/mcp", request)[:2], (204, None))
        self.assertEqual(self.llm.requests, [])
        self.agent.close()
        self.assertEqual(self.request("/api/sessions", {})[0], 503)


class ExtensionTests(unittest.TestCase):
    def test_register_app_existing_bridge_preferences_and_shutdown(self):
        app = settings_tests.App()
        app.applicationShuttingDown = slicing_tests.Signal(app)
        app.check_thread = lambda: self.assertIs(threading.current_thread(), app.main)
        prefs = Preferences(app)
        app.getPreferences = lambda: prefs
        class Extension(object):
            def __init__(self):
                self.items = []
            def setMenuName(self, name):
                self.name = name
            def addMenuItem(self, name, callback):
                self.items.append((name, callback))
        extension_module = types.ModuleType("UM.Extension")
        extension_module.Extension = Extension
        with mock.patch.dict(sys.modules, {"UM": types.ModuleType("UM"), "UM.Extension": extension_module}):
            import AIAssistant
            module = importlib.import_module("AIAssistant.AIAssistant")
            try:
                plugin = AIAssistant.register(app)["extension"]
                self.assertIs(plugin._app, app)
                self.assertIs(plugin._config.bridge, plugin._adapter._bridge)
                self.assertIs(plugin._service.tools._adapter, plugin._adapter)
                self.assertEqual([i[0] for i in plugin.items], ["Open chat", "LLM Settings"])
                plugin._config.save(**CONFIG)
                self.assertEqual(plugin._service._config(), CONFIG)
                app.applicationShuttingDown.emit()
                self.assertTrue(plugin._service.closed)
                self.assertFalse(plugin._adapter.get_slicer_state()["ok"])
                plugin.close()
            finally:
                sys.modules.pop(module.__name__, None)
