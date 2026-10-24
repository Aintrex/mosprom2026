"""2B: real store/HTTP/registry/bridge, fake provider and native reader only."""
import base64
import http.client
import io
import json
import os
import queue
import sys
import threading
import types
import unittest
import urllib.error
from unittest import mock

import test_uploads as upload_tests
import test_slicer_models as model_tests
import test_slicer_settings as settings_tests
import test_slicer_modes as mode_tests
from test_agent_integration import CONFIG, ScriptedLLM, calls, tool
from AIAssistant.agent.service import AgentService, APIError
from AIAssistant.agent.llm import ChatCompletionsClient
from AIAssistant.agent.mcp import dispatch
from AIAssistant.agent.tools import ToolRegistry
from AIAssistant.slicer.adapter import SlicerAdapter
from AIAssistant.server import create_server
from AIAssistant.uploads import FileStore


def upload(store, data=upload_tests.STL, name="part.stl"):
    body, kind = upload_tests.multipart(data, name)
    return store.upload(io.BytesIO(body), len(body), kind)


class AttachmentTests(unittest.TestCase):
    def service(self, *answers, **kwargs):
        self.llm = ScriptedLLM(*answers)
        self.agent = AgentService(config_provider=lambda: dict(CONFIG), llm_client=self.llm,
                                  run_async=False, **kwargs)
        self.sid = self.agent.create_session()["session_id"]
        self.addCleanup(self.cleanup)
        return self.agent.file_store

    def cleanup(self):
        self.agent.close()
        self.assertTrue(self.agent.file_store.wait_closed(2))

    def send(self, ids, cmid="attempt"):
        return self.agent.submit_message(self.sid, {"text": "Объясни вложения", "client_message_id": cmid,
                                                   "attachment_ids": ids})

    def test_images_actual_base64_models_only_metadata_and_history_restore(self):
        store = self.service({"content": "Фото получены"}, {"content": "Следующий ответ"})
        items = [upload(store, upload_tests.PNG, "фото.png"), upload(store, upload_tests.JPEG, "photo.jpg"), upload(store)]
        ids = [item["file_id"] for item in items]
        self.send(ids)
        request = self.llm.requests[0][1]
        parts = request[1]["content"]
        urls = [p["image_url"]["url"] for p in parts if p["type"] == "image_url"]
        self.assertEqual(urls, ["data:image/png;base64," + base64.b64encode(upload_tests.PNG).decode(),
                                "data:image/jpeg;base64," + base64.b64encode(upload_tests.JPEG).decode()])
        self.assertNotIn(base64.b64encode(upload_tests.STL).decode(), json.dumps(request))
        self.assertIn(items[2]["file_id"], json.dumps(parts))
        snapshot = self.agent.get_session(self.sid)
        self.assertEqual(snapshot["messages"][0]["attachments"], items)
        self.assertEqual(snapshot["messages"][0]["attachment_ids"], ids)
        for value in (store._directory, "data:image/", CONFIG["api_key"], "_attachment_ids"):
            self.assertNotIn(value, json.dumps(snapshot))
        self.assertNotIn("data:image/", json.dumps(self.agent._sessions[self.sid]["llm_blocks"]))
        self.send([], "followup")
        self.assertEqual(self.llm.requests[1][1][1]["content"], parts)
        self.assertNotIn("_attachment_ids", json.dumps(self.llm.requests))
        schemas = [s["function"]["name"] for s in self.llm.requests[0][2]]
        self.assertIn("load_model_file", schemas)
        self.assertNotIn("load_model", schemas)

    def test_older_history_photos_are_summarised_not_resent(self):
        store = self.service({"content": "1"}, {"content": "2"}, {"content": "3"})
        photo = upload(store, upload_tests.PNG, "фото.png")["file_id"]
        for cmid, ids in (("first", [photo]), ("second", []), ("third", [])):
            self.send(ids, cmid)
        encoded = base64.b64encode(upload_tests.PNG).decode()
        self.assertIn(encoded, json.dumps(self.llm.requests[1]))  # Previous turn keeps the photo.
        self.assertNotIn(encoded, json.dumps(self.llm.requests[2]))
        self.assertIn("повторно не передаётся", json.dumps(self.llm.requests[2], ensure_ascii=False))

    def test_idempotent_full_ordered_body_and_unknown_limits_before_acceptance(self):
        store = self.service({"content": "ok"})
        ids = [upload(store)["file_id"], upload(store)["file_id"]]
        accepted = self.send(ids)
        self.assertEqual(self.send(ids), accepted)
        self.assertEqual(len(self.llm.requests), 1)
        for changed in (list(reversed(ids)), [], ids[:1]):
            with self.assertRaises(APIError) as caught:
                self.send(changed)
            self.assertEqual(caught.exception.code, "message_id_conflict")
        for value, code in ((["unknown"], "file_not_found"), (ids * 3, "attachment_limit"),
                            ([ids[0], ids[0]], "invalid_arguments"), ([True], "invalid_arguments"),
                            (["x" * 129], "invalid_arguments")):
            with self.assertRaises(APIError) as caught:
                self.send(value, "new")
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(len(self.llm.requests), 1)
        self.assertEqual(len(self.agent.get_session(self.sid)["messages"]), 2)
        images = [upload(store, upload_tests.PNG, "photo.png") for i in range(3)]
        # Check 10 MiB aggregate before reading image bytes or accepting the turn.
        with mock.patch.object(store, "metadata", side_effect=[dict(m, size_bytes=4 * 1024 * 1024) for m in images]), \
                mock.patch.object(store, "read_image", side_effect=AssertionError("Not accepted")):
            with self.assertRaises(APIError) as caught:
                self.send([m["file_id"] for m in images], "limit")
            self.assertEqual((caught.exception.status, caught.exception.code), (413, "attachment_limit"))

    def test_real_transport_image_payload_and_honest_vision_http_failure(self):
        captured = []
        def provider(request, **kwargs):
            captured.append(json.loads(request.data.decode()))
            raise urllib.error.HTTPError(request.full_url, 400, "vision unsupported", {}, io.BytesIO(b"private"))
        store = self.service()
        self.agent._llm = ChatCompletionsClient(provider)
        image = upload(store, upload_tests.PNG, "image.png")
        self.send([image["file_id"]])
        self.assertEqual(len(captured), 1)
        content = captured[0]["messages"][1]["content"]
        self.assertEqual(base64.b64decode(content[-1]["image_url"]["url"].split(",")[1]), upload_tests.PNG)
        snapshot = self.agent.get_session(self.sid)
        self.assertTrue(snapshot["messages"][-1]["is_error"])
        self.assertIn("HTTP 400", snapshot["messages"][-1]["text"])
        self.assertEqual([e["data"]["code"] for e in self.agent.get_events(self.sid)["events"] if e["type"] == "error"], ["http_error"])
        self.assertEqual(snapshot["tool_events"], [])

    def test_bad_import_stops_remaining_mutations_and_wrong_kind_never_calls_api(self):
        store = self.service(calls(tool("load_model_file", {"file_id": "missing"}),
                                   tool("set_print_mode", {"mode": "classic"}, "later")))
        image = upload(store, upload_tests.PNG, "image.png")
        with mock.patch.object(self.agent._adapter, "load_model", side_effect=AssertionError("No API")):
            for file_id, error in ((image["file_id"], "file_type_mismatch"), ("unknown", "file_not_found")):
                result = self.agent.tools.call("load_model_file", {"file_id": file_id})
                self.assertEqual(result["error"]["code"], error)
            self.send([image["file_id"]])
        events = self.agent.get_session(self.sid)["tool_events"]
        results = [e["data"]["result"] for e in events if e["type"] == "tool_finished"]
        self.assertEqual([r["error"]["code"] for r in results], ["file_not_found", "tool_not_executed"])
        self.assertNotIn("set_print_mode", [e["data"]["name"] for e in events if e["type"] == "tool_started"])
        self.assertTrue(self.agent.get_session(self.sid)["messages"][-1]["is_error"])
        schema = next(t for t in self.agent.tools.list_tools() if t["name"] == "load_model_file")
        self.assertEqual(set(schema["inputSchema"]["properties"]), {"file_id"})
        self.assertEqual(dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "load_model_file", "arguments": {"file_id": image["file_id"], "reader_done": "bad"}}}, self.agent.tools)["error"]["code"], -32602)


class NativeAttachmentTests(unittest.TestCase):
    worker = settings_tests.SettingsTests.worker
    finish = settings_tests.SettingsTests.finish
    pump_until_done = mode_tests.ModeTests.pump_until_done

    def setUp(self):
        self.app = model_tests.ModelApp()
        self.adapter = SlicerAdapter(self.app, timeout=0.3, load_timeout=0.2)
        self.store = FileStore()
        self.tools = ToolRegistry(self.adapter, self.store)
        self.metadata = upload(self.store)
        self.path = next(iter(self.store._paths))
        qt = types.ModuleType("PyQt5.QtCore")
        qt.QUrl = model_tests.Url
        patch = mock.patch.dict(sys.modules, {"PyQt5": types.ModuleType("PyQt5"), "PyQt5.QtCore": qt})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.adapter.close()
        # A test with deliberately missing completion must explicitly deliver it before cleanup.
        if self.app.fileCompleted.slots:
            self.app.fileCompleted.emit(self.path)
        self.store.close()
        self.assertTrue(self.store.wait_closed(2))

    def start(self):
        worker = self.worker(lambda: self.tools.call("load_model_file", {"file_id": self.metadata["file_id"]}))
        self.app.callbacks.get(timeout=1)()
        return worker

    def test_existing_import_qt_completion_and_safe_file_metadata(self):
        worker = self.start()
        self.assertFalse(worker[3].is_set())
        self.app.fileCompleted.emit(self.path + "foreign")
        self.assertEqual(self.store._users, 1)
        self.app.complete([model_tests.Node(self.app)])
        result = self.pump_until_done(worker)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["file_id"], self.metadata["file_id"])
        self.assertEqual(result["data"]["file_name"], "part.stl")
        self.assertNotIn(self.store._directory, json.dumps(result))
        self.assertEqual(self.store._users, 0)
        self.assertEqual(self.app.fileCompleted.slots, [])
        self.assertEqual(len(self.app.loads), 1)

    def test_timeout_store_service_and_registry_close_keep_until_late_own_completion(self):
        worker = self.start()
        result = self.pump_until_done(worker)
        self.assertEqual(result["error"]["code"], "model_load_timeout")
        service = AgentService(self.adapter, file_store=self.store)
        service.close()
        self.adapter.close()  # Registry.finish must not release the live reader's lease.
        self.assertEqual(self.store._users, 1)
        self.assertTrue(os.path.isfile(self.path))
        self.assertFalse(self.store.wait_closed(0.02))
        self.app.fileCompleted.emit(self.path + "foreign")
        self.assertTrue(os.path.isfile(self.path))
        self.app.fileCompleted.emit(self.path)
        self.assertTrue(self.store.wait_closed(2))
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.app.fileCompleted.slots, [])

    def test_pending_timeout_preflight_failure_and_no_bridge_release_without_reader(self):
        worker = self.worker(lambda: self.tools.call("load_model_file", {"file_id": self.metadata["file_id"]}))
        late = self.app.callbacks.get(timeout=1)
        self.assertEqual(self.finish(worker)["error"]["code"], "qt_timeout")
        self.assertEqual(self.store._users, 0)
        late()
        self.assertEqual(self.app.loads, [])
        self.app.stack = None
        worker = self.start()
        self.assertEqual(self.finish(worker)["error"]["code"], "no_active_machine")
        self.assertEqual(self.store._users, 0)
        dev = ToolRegistry(SlicerAdapter(), self.store)
        self.assertEqual(dev.call("load_model_file", {"file_id": self.metadata["file_id"]})["error"]["code"], "slicer_not_connected")
        self.assertEqual(self.store._users, 0)

    def test_launched_exception_holds_lease_until_actual_completion(self):
        self.app.load_error = RuntimeError("reader outcome unknown")
        with self.assertLogs("AIAssistant.slicer.adapter", level="ERROR"):
            worker = self.start()
            self.assertEqual(self.finish(worker)["error"]["code"], "slicer_error")
        self.store.close()
        self.adapter.close()
        self.assertTrue(os.path.isfile(self.path))
        self.assertFalse(self.store.wait_closed(0.02))
        self.app.fileCompleted.emit(self.path)
        self.assertTrue(self.store.wait_closed(2))

    def test_native_import_failure_stops_agent_before_second_mutation(self):
        llm = ScriptedLLM(calls(tool("load_model_file", {"file_id": self.metadata["file_id"]}),
                                tool("set_print_mode", {"mode": "classic"}, "later")))
        service = AgentService(self.adapter, lambda: dict(CONFIG), llm, run_async=False, file_store=self.store)
        self.addCleanup(service.close)
        sid = service.create_session()["session_id"]
        worker = self.worker(lambda: service.submit_message(sid, {
            "text": "Загрузи модель", "client_message_id": "failure", "attachment_ids": [self.metadata["file_id"]]}))
        self.app.callbacks.get(timeout=1)()
        self.app.complete([])
        self.pump_until_done(worker)
        results = [e["data"]["result"] for e in service.get_session(sid)["tool_events"] if e["type"] == "tool_finished"]
        self.assertEqual([r["error"]["code"] for r in results], ["model_load_failed", "tool_not_executed"])
        self.assertEqual(len(self.app.loads), 1)
        self.assertEqual(self.store._users, 0)
        self.assertEqual(len(llm.requests), 1)

    def test_upload_message_native_tool_duplicate_http_does_not_repeat_import(self):
        llm = ScriptedLLM(calls(tool("load_model_file", {"file_id": self.metadata["file_id"]})), {"content": "Импорт подтверждён"})
        service = AgentService(self.adapter, lambda: dict(CONFIG), llm, file_store=self.store)
        self.addCleanup(service.close)
        server = create_server(service)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        def cleanup_server():
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.addCleanup(cleanup_server)
        def request(path, body, content_type="application/json"):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            try:
                connection.request("POST", path, body if isinstance(body, bytes) else json.dumps(body).encode(), {"Content-Type": content_type})
                response = connection.getresponse()
                return response.status, json.loads(response.read().decode())
            finally:
                connection.close()
        payload, content_type = upload_tests.multipart(name="from-browser.stl")
        code, metadata = request("/api/files", payload, content_type)
        self.assertEqual(code, 201)
        llm.answers[0] = calls(tool("load_model_file", {"file_id": metadata["file_id"]}))
        sid = request("/api/sessions", {})[1]["session_id"]
        body = {"text": "Загрузи приложенную модель", "attachment_ids": [metadata["file_id"]], "client_message_id": "exact"}
        path = "/api/sessions/" + sid + "/messages"
        accepted = request(path, body)
        self.assertEqual(accepted[0], 202)
        self.app.callbacks.get(timeout=1)()
        self.app.complete([model_tests.Node(self.app)])
        # Pump the worker's confirmation through the same bridge.
        done = threading.Event()
        def wait_turn():
            for i in range(200):
                if service.get_session(sid)["agent_state"] == "idle":
                    done.set()
                    return
                done.wait(0.01)
        waiter = threading.Thread(target=wait_turn)
        waiter.start()
        while not done.is_set():
            try:
                self.app.callbacks.get(timeout=0.05)()
            except queue.Empty:
                pass
            if not waiter.is_alive():
                break
        waiter.join(2)
        self.assertTrue(done.is_set())
        self.assertEqual(request(path, body), accepted)
        self.assertEqual(len(self.app.loads), 1)
        snapshot = service.get_session(sid)
        self.assertFalse(snapshot["messages"][-1].get("is_error"), snapshot)
        self.assertEqual(snapshot["messages"][0]["attachments"], [metadata])
        self.assertEqual(len(llm.requests), 2)
