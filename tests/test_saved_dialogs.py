"""2G: real bounded temporary-file archive, scripted LLM and fork-shaped Qt only."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest import mock
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import test_agent_integration as integration
from AIAssistant.agent.chat_store import ChatStore, HistoryError, CHAT_BYTES, TOTAL_BYTES, document, encoded
from AIAssistant.agent.service import AgentService, APIError
from AIAssistant.server import create_server


def identity():
    return "session_" + uuid.uuid4().hex


def snapshot(sid, revision=1, text="Обсудили высоту слоя"):
    return {"session_id": sid, "revision": revision, "title": "Деталь", "created_at": "2026-10-24T10:00:00Z",
            "updated_at": "2026-10-24T10:00:00Z", "messages": [{"role": "user", "text": text,
                "created_at": "2026-10-24T10:00:00Z"}], "history_truncated": False, "interrupted": False}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def store(self):
        store = ChatStore(self.directory.name)
        self.assertTrue(store.ready.wait(2))
        self.addCleanup(lambda: (store.close(), store.wait_closed(2)))
        return store

    def wait(self, store, sid, state="saved", revision=None):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            value = store.state(sid, revision or 1)
            if value["state"] == state and (revision is None or value["saved_revision"] == revision):
                return value
            time.sleep(0.005)
        self.fail("Archive state not reached: %s" % value)

    def test_saved_ack_means_disk_revision_and_older_snapshots_cannot_replace_it(self):
        store, sid = self.store(), identity()
        store.save(snapshot(sid, 5))
        self.wait(store, sid, revision=5)
        self.assertEqual(json.loads(Path(store._path(sid)).read_text(encoding="utf8"))["revision"], 5)
        store.save(snapshot(sid, 4, "старое"))
        store.save(snapshot(sid, 7, "новое"))
        store.save(snapshot(sid, 6, "старое"))
        self.wait(store, sid, revision=7)
        self.assertEqual(store.load(sid)["messages"][0]["text"], "новое")
        index = json.loads((Path(self.directory.name) / "index.json").read_text(encoding="utf8"))
        self.assertEqual(index["sessions"][0]["revision"], 7)

    def test_delete_during_inflight_write_and_late_snapshot_never_resurrect(self):
        store, sid = self.store(), identity()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        atomic = store._atomic
        def paused(path, raw):
            if path == store._path(sid) and not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(2))
            atomic(path, raw)
        store._atomic = paused
        store.save(snapshot(sid))
        self.assertTrue(entered.wait(1))
        store.save(snapshot(sid, 3))
        result = []
        deleted = threading.Thread(target=lambda: result.append(store.delete(sid)))
        deleted.start()
        end = time.monotonic() + 1
        while sid not in store._deleted and time.monotonic() < end:
            time.sleep(0.001)
        self.assertIn(sid, store._deleted)
        store.save(snapshot(sid, 10))
        release.set()
        deleted.join(2)
        self.assertEqual(result, [{"deleted": True}])
        store.save(snapshot(sid, 20))
        store.close()
        self.assertTrue(store.wait_closed(2))
        self.assertFalse(Path(store._path(sid)).exists())
        self.assertNotIn(sid, [s["session_id"] for s in store.listing()["sessions"]])

    def test_atomic_failure_preserves_previous_file_and_cleans_partial_then_new_revision_saves(self):
        store, sid = self.store(), identity()
        store.save(snapshot(sid))
        self.wait(store, sid)
        original = Path(store._path(sid)).read_bytes()
        replace = os.replace
        def fail(source, target):
            if target == store._path(sid):
                raise OSError("dummy disk failure")
            return replace(source, target)
        with mock.patch("AIAssistant.agent.chat_store.os.replace", side_effect=fail):
            store.save(snapshot(sid, 2))
            self.wait(store, sid, "error")
        self.assertEqual(Path(store._path(sid)).read_bytes(), original)
        self.assertFalse(list(Path(self.directory.name).glob("*.tmp-*")))
        store.save(snapshot(sid, 3))
        self.wait(store, sid, revision=3)

    def test_secret_paths_urls_base64_operational_ids_and_raw_protocol_excluded(self):
        store, sid = self.store(), identity()
        raw = snapshot(sid, text=integration.CONFIG["api_key"] + " C:/private/model.stl model-1 slice-5 export_abc artifact-old file_old https://example.invalid/a data:image/png;base64,AAAA " + "A" * 200)
        raw["title"] = integration.CONFIG["api_key"]
        raw["api_key"], raw["tool_calls"] = integration.CONFIG["api_key"], [{"bad": "raw tool"}]
        raw["messages"][0]["attachments"] = [{"name": "photo.png", "file_id": "file_old", "url": "data:image/png;base64,AAAA"}]
        store.save(raw, integration.CONFIG["api_key"])
        self.wait(store, sid)
        saved = Path(store._path(sid)).read_text(encoding="utf8")
        for prohibited in (integration.CONFIG["api_key"], "C:/private", "model-1", "slice-5", "export_abc", "artifact-old", "file_old", "https://", "base64", "A" * 200, "tool_calls", "api_key"):
            self.assertNotIn(prohibited, saved)
        self.assertEqual(store.load(sid)["messages"][0]["attachments"], [{"name": "photo.png", "expired": True}])

    def test_limits_messages_chat_bytes_total_bytes_and_slots_released_by_delete(self):
        store, sid = self.store(), identity()
        raw = snapshot(sid)
        raw["messages"] *= 110
        store.save(raw)
        self.wait(store, sid)
        saved = store.load(sid)
        self.assertEqual(len(saved["messages"]), 100)
        self.assertTrue(saved["history_truncated"])
        original = Path(store._path(sid)).read_bytes()
        large = snapshot(sid, 2)
        large["messages"] = [dict(large["messages"][0], text="я" * 10000) for _ in range(100)]
        store.save(large)
        self.wait(store, sid, "error")
        self.assertEqual(Path(store._path(sid)).read_bytes(), original)
        filler = Path(self.directory.name) / "foreign.bin"
        filler.write_bytes(b"x" * TOTAL_BYTES)
        store.save(snapshot(sid, 3))
        self.wait(store, sid, "error")
        filler.unlink()
        with mock.patch("AIAssistant.agent.chat_store.MAX_CHATS", 1):
            with self.assertRaises(HistoryError):
                store.save(snapshot(identity()))
            store.delete(sid)
            another = identity()
            store.save(snapshot(another))
            self.wait(store, another)

    def test_corruption_bad_version_and_index_recovery_preserve_files_and_reject_paths(self):
        good, bad = identity(), identity()
        root = Path(self.directory.name)
        (root / (good + ".json")).write_bytes(encoded(document(snapshot(good), "")))
        broken = b'{"version":999}'
        (root / (bad + ".json")).write_bytes(broken)
        (root / "index.json").write_bytes(b"broken index")
        store = self.store()
        self.assertTrue(store.listing()["error"])
        self.assertEqual((root / (bad + ".json")).read_bytes(), broken)
        self.assertEqual(len(list(root.glob("index.json.corrupt-*"))), 1)
        self.assertEqual(store.load(good)["session_id"], good)
        with self.assertRaises(HistoryError):
            store.load(bad)
        for sid in ("../secret", "C:/secret", good + "\n", "session_bad"):
            with self.assertRaises(HistoryError):
                store.load(sid)
        store.delete(bad)
        self.assertFalse((root / (bad + ".json")).exists())

    def test_close_nonblocking_with_blocked_worker_and_normal_process_exit_flushes(self):
        store, sid = self.store(), identity()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        atomic = store._atomic
        def paused(path, raw):
            entered.set()
            self.assertTrue(release.wait(2))
            atomic(path, raw)
        store._atomic = paused
        store.save(snapshot(sid))
        self.assertTrue(entered.wait(1))
        began = time.monotonic()
        store.close()
        self.assertLess(time.monotonic() - began, 0.2)
        self.assertFalse(store.done.is_set())
        release.set()
        self.assertTrue(store.wait_closed(2))
        other = identity()
        script = "import sys;sys.path.insert(0,sys.argv[1]);from AIAssistant.agent.chat_store import ChatStore;import json;s=ChatStore(sys.argv[2]);s.ready.wait(2);s.save(json.loads(sys.argv[3]));s.close()"
        completed = subprocess.run([sys.executable, "-c", script, str(Path(__file__).resolve().parents[1] / "plugin"),
                                    self.directory.name, json.dumps(snapshot(other, 4))], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf8", "replace"))
        self.assertEqual(json.loads(Path(store._path(other)).read_text(encoding="utf8"))["revision"], 4)

    def test_deeply_malformed_chat_does_not_kill_worker_or_overwrite_corruption(self):
        sid = identity()
        path = Path(self.directory.name) / (sid + ".json")
        raw = b"[" * 3000 + b"0" + b"]" * 3000
        path.write_bytes(raw)
        store = self.store()
        self.assertTrue(store.listing()["error"])
        with self.assertRaises(HistoryError):
            store.load(sid)
        self.assertEqual(path.read_bytes(), raw)
        other = identity()
        store.save(snapshot(other))
        self.wait(store, other)

    def test_delete_disk_failure_preserves_file_tombstone_and_requires_explicit_retry(self):
        store, sid = self.store(), identity()
        store.save(snapshot(sid))
        self.wait(store, sid)
        original = Path(store._path(sid)).read_bytes()
        remove = os.remove
        attempts = []
        def fail(path):
            if path == store._path(sid):
                attempts.append(path)
                raise OSError("dummy locked file")
            return remove(path)
        with mock.patch("AIAssistant.agent.chat_store.os.remove", side_effect=fail):
            with self.assertRaises(HistoryError):
                store.delete(sid)
            store.save(snapshot(sid, 100, "late snapshot"))
        self.assertEqual(len(attempts), 1)
        self.assertEqual(Path(store._path(sid)).read_bytes(), original)
        self.assertTrue(store.delete(sid)["deleted"])
        self.assertFalse(Path(store._path(sid)).exists())


class SavedServiceTests(unittest.TestCase):
    worker, finish = integration.AgentTests.worker, integration.AgentTests.finish
    pump_until_done = integration.AgentTests.pump_until_done

    def setUp(self):
        integration.AgentTests.setUp(self)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def agent(self, *answers):
        client = integration.ScriptedLLM(*answers)
        agent = AgentService(self.adapter, lambda: dict(integration.CONFIG), client, run_async=False, chat_directory=self.directory.name)
        self.assertTrue(agent.history.ready.wait(2))
        self.addCleanup(lambda: (agent.close(), agent.history.wait_closed(2)))
        return agent, client

    def send(self, agent, sid, text, cmid="new-request"):
        return self.pump_until_done(self.worker(lambda: agent.submit_message(sid, {"text": text, "client_message_id": cmid})))

    def test_two_dialogs_restart_context_isolated_expired_attachments_no_tools_or_replay(self):
        agent, _ = self.agent({"role": "assistant", "content": "Кронштейн PETG"}, {"role": "assistant", "content": "Декор PLA"})
        sid = agent.create_session()["session_id"]
        self.send(agent, sid, "Обсудим кронштейн PETG model-1 slice-old")
        with agent._lock:
            agent._sessions[sid]["messages"][0]["attachments"] = [{"name": "photo.png", "file_id": "file-missing"}]
            agent._sessions[sid]["llm_blocks"][0][0]["_attachment_ids"] = ["file-missing"]
        second = agent.create_session()["session_id"]
        self.send(agent, second, "Декор PLA")
        agent.close()
        self.assertTrue(agent.history.wait_closed(2))
        restarted, client = self.agent({"role": "assistant", "content": "Продолжим обсуждение кронштейна"})
        self.assertEqual(len(restarted.list_sessions()["sessions"]), 2)
        self.assertEqual(restarted._sessions, {}, "Listing does not load all message histories")
        restored = restarted.get_session(sid)
        self.assertTrue(restored["restored"])
        self.assertEqual(restored["artifacts"], [])
        self.assertIsNone(restored["slice"])
        self.assertEqual(restored["messages"][0]["attachments"], [{"name": "photo.png", "expired": True}])
        with mock.patch.object(restarted.file_store, "metadata", side_effect=AssertionError("expired attachment read")), \
                mock.patch.object(restarted._adapter, "get_slicer_state", side_effect=AssertionError("theory must not auto-read scene")):
            self.send(restarted, sid, "Объясни прошлый выбор материала")
        text = json.dumps(client.requests[0][1], ensure_ascii=False)
        self.assertIn("PETG", text)
        for unwanted in ("Декор PLA", "model-1", "slice-old", "file-missing", "_attachment_ids", "tool_calls"):
            self.assertNotIn(unwanted, text)
        self.assertEqual(self.app.stack.writes, [])
        self.assertIsNone(restarted._observer_thread)

    def test_restored_mutation_needs_current_reads_and_old_protocol_never_runs(self):
        agent, _ = self.agent({"role": "assistant", "content": "Высота обсуждалась"})
        sid = agent.create_session()["session_id"]
        self.send(agent, sid, "Высота слоя")
        agent.close()
        self.assertTrue(agent.history.wait_closed(2))
        restarted, _ = self.agent(integration.calls(integration.tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.24}, "blocked")),
            integration.calls(integration.tool("get_slicer_state", call_id="state"), integration.tool("list_models", call_id="models")),
            integration.calls(integration.tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.24}, "write")),
            {"role": "assistant", "content": "Слой подтверждён"})
        self.send(restarted, sid, "Установи высоту слоя0.24")
        results = [e["data"]["result"] for e in restarted.get_session(sid)["tool_events"] if e["type"] == "tool_finished"]
        self.assertEqual(results[0]["error"]["code"], "history_refresh_required")
        self.assertEqual(self.app.stack.writes, [("layer_height", 0.24)])
        self.assertEqual(results[-1]["data"]["after"], 0.24)

    def test_close_interrupted_has_no_pending_clients_ownership_or_restart_job(self):
        agent, _ = self.agent()
        sid = agent.create_session()["session_id"]
        with agent._lock:
            agent._active_turn = "interrupted"
            agent._sessions[sid]["agent_state"] = "processing"
            agent._sessions[sid]["clients"]["old-pending"] = {"content": "old"}
        agent.close()
        self.assertTrue(agent.history.wait_closed(2))
        restarted, _ = self.agent()
        restored = restarted.get_session(sid)
        self.assertEqual(restored["agent_state"], "idle")
        self.assertIn("прерван", restored["messages"][-1]["text"])
        self.assertEqual(restarted._sessions[sid]["clients"], {})
        self.assertEqual(restarted._sessions[sid]["_owned"], {})

    def test_busy_guards_readonly_snapshot_list_remain_available_and_delete_corruption(self):
        agent, _ = self.agent()
        sid = agent.create_session()["session_id"]
        with agent._lock:
            agent._active_turn = "running"
        self.assertEqual(agent.get_session(sid)["session_id"], sid)
        self.assertTrue(agent.list_sessions()["sessions"])
        self.assertNotEqual(agent.create_session()["session_id"], sid, "Creating an empty descriptor preserves the existing readonly-scene API")
        with self.assertRaises(APIError) as caught:
            agent.delete_session(sid)
        self.assertEqual(caught.exception.code, "agent_busy")
        agent._active_turn = None
        agent._operations["slice"] = {"sid": sid}
        self.assertTrue(agent.get_session(sid)["operation_busy"])
        with self.assertRaises(APIError):
            agent.delete_session(sid)
        agent._operations["slice"] = None
        self.assertTrue(agent.delete_session(sid)["deleted"])
        with self.assertRaises(APIError) as caught:
            agent.get_session(sid)
        self.assertEqual(caught.exception.status, 404)
        agent._finish(sid, "old-token", [], "late finish", None, "")
        self.assertNotIn(sid, agent._sessions)

    def test_unavailable_directory_is_visible_temporary_work_not_false_saved(self):
        blocker = Path(self.directory.name) / "not-a-directory"
        blocker.write_text("keep this file", encoding="utf8")
        agent = AgentService(self.adapter, lambda: dict(integration.CONFIG), integration.ScriptedLLM({"role": "assistant", "content": "Ответ"}),
                             run_async=False, chat_directory=str(blocker))
        self.addCleanup(lambda: (agent.close(), agent.history.wait_closed(2)))
        self.assertTrue(agent.history.ready.wait(2))
        self.assertTrue(agent.list_sessions()["error"])
        sid = agent.create_session()["session_id"]
        self.send(agent, sid, "Объясни высоту слоя")
        end = time.monotonic() + 2
        while agent.get_session(sid)["persistence"]["state"] != "error" and time.monotonic() < end:
            time.sleep(0.005)
        value = agent.get_session(sid)
        self.assertEqual(value["persistence"]["state"], "error")
        self.assertEqual(value["messages"][-1]["text"], "Ответ")
        self.assertEqual(blocker.read_text(encoding="utf8"), "keep this file")

    def test_io_in_archive_worker_and_service_close_does_not_wait_under_qt_lock(self):
        agent, _ = self.agent()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        main, atomic = threading.current_thread(), agent.history._atomic
        def paused(path, raw):
            self.assertIsNot(threading.current_thread(), main)
            entered.set()
            self.assertTrue(release.wait(2))
            atomic(path, raw)
        agent.history._atomic = paused
        agent.create_session()
        self.assertTrue(entered.wait(1))
        began = time.monotonic()
        agent.close()
        self.assertLess(time.monotonic() - began, 0.2)
        self.assertTrue(agent._lock.acquire(False))
        agent._lock.release()
        release.set()
        self.assertTrue(agent.history.wait_closed(2))

    def test_real_http_list_open_delete_and_origin_routes(self):
        agent, _ = self.agent()
        sid = agent.create_session()["session_id"]
        server = create_server(agent)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:%d" % server.server_port
        with urlopen(base + "/api/sessions", timeout=2) as response:
            self.assertEqual(json.loads(response.read().decode("utf8"))["sessions"][0]["session_id"], sid)
        request = Request(base + "/api/sessions/" + sid, method="DELETE", headers={"Origin": "http://foreign.invalid"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 403)
        with urlopen(base + "/api/sessions/" + sid, timeout=2) as response:
            self.assertEqual(json.loads(response.read().decode("utf8"))["session_id"], sid)
        with urlopen(Request(base + "/api/sessions/" + sid, method="DELETE"), timeout=2) as response:
            self.assertTrue(json.loads(response.read().decode("utf8"))["deleted"])

    def test_late_lazy_load_after_confirmed_delete_cannot_reinsert_session(self):
        agent, _ = self.agent()
        sid = agent.create_session()["session_id"]
        agent.close()
        self.assertTrue(agent.history.wait_closed(2))
        restarted, _ = self.agent()
        loaded, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        load = restarted.history.load
        def paused(identity):
            result = load(identity)
            loaded.set()
            self.assertTrue(release.wait(2))
            return result
        errors = []
        def get():
            try:
                restarted.get_session(sid)
            except APIError as exc:
                errors.append(exc)
        with mock.patch.object(restarted.history, "load", side_effect=paused):
            getter = threading.Thread(target=get)
            getter.start()
            self.assertTrue(loaded.wait(1))
            self.assertTrue(restarted.delete_session(sid)["deleted"])
            release.set()
            getter.join(2)
        self.assertEqual([error.status for error in errors], [404])
        self.assertNotIn(sid, restarted._sessions)
        self.assertFalse(Path(restarted.history._path(sid)).exists())
        self.assertEqual(restarted.list_sessions()["sessions"], [])


if __name__ == "__main__":
    unittest.main()
