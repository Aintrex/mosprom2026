"""2C: real export files/HTTP/QtBridge with fake native writer, no installed STE."""
import copy
import http.client
import io
import json
import os
import threading
import types
import unittest
from unittest import mock

import test_slicer_exports as export_tests
import test_slicer_slicing as base
import test_uploads as upload_tests
from test_agent_integration import CONFIG, ScriptedLLM, calls, tool
from AIAssistant.agent.service import AgentService
from AIAssistant.downloads import DownloadError
from AIAssistant.server import create_server


class DownloadsTests(unittest.TestCase):
    start, status, complete, drain = base.SlicingTests.start, base.SlicingTests.status, base.SlicingTests.complete, base.SlicingTests.drain
    worker, finish, pump_until_done = base.SlicingTests.worker, base.SlicingTests.finish, base.SlicingTests.pump_until_done
    launch, wait_status, clean_workers = export_tests.ExportTests.launch, export_tests.ExportTests.wait_status, export_tests.ExportTests.clean_workers

    def setUp(self):
        export_tests.ExportTests.setUp(self)
        self.llm = ScriptedLLM()
        self.service = AgentService(self.adapter, lambda: dict(CONFIG), self.llm, run_async=False)
        self.sid = self.service.create_session()["session_id"]
        self.server = create_server(self.service)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.sequence = 0

    def close_server(self):
        self.service.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.assertTrue(self.service.file_store.wait_closed(2))

    def turn_status(self, export_id):
        self.sequence += 1
        self.llm.answers.extend([calls(tool("get_export_status", {"export_id": export_id})), {"content": "Native export observed"}])
        worker = self.worker(lambda: self.service.submit_message(self.sid, {
            "text": "Прочитай статус экспорта", "client_message_id": "download-" + str(self.sequence), "attachment_ids": []}))
        self.pump_until_done(worker)
        return self.service.get_session(self.sid)

    def accepted(self, kind="gcode"):
        self.launch(kind)
        status = self.wait_status()
        self.assertEqual(status["state"], "completed", status)
        snapshot = self.turn_status(self.export_id)
        return status, snapshot["artifacts"][-1]

    def request_worker(self, path, headers=None):
        def request():
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
            try:
                connection.request("GET", path, headers=headers or {})
                response = connection.getresponse()
                return response.status, response.read(), dict(response.getheaders())
            finally:
                connection.close()
        return self.worker(request)

    def get(self, path, headers=None):
        return self.pump_until_done(self.request_worker(path, headers))

    def test_native_artifact_agent_event_snapshot_get_bytes_headers_both_kinds(self):
        for kind, mime in (("gcode", "application/octet-stream"), ("project", "model/3mf")):
            status, artifact = self.accepted(kind)
            self.assertTrue(artifact["valid"])
            self.assertTrue(artifact["file_id"].startswith("download-"))
            self.assertEqual(artifact["artifact_id"], status["artifact"]["artifact_id"])
            code, body, headers = self.get(artifact["download_url"])
            self.assertEqual(code, 200)
            self.assertEqual(headers["Content-Type"], mime)
            self.assertEqual(int(headers["Content-Length"]), len(body))
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(headers["Content-Disposition"], "attachment; filename*=UTF-8''" + artifact["file_name"])
            with open(os.path.join(status["local_directory"], artifact["file_name"]), "rb") as stream:
                self.assertEqual(body, stream.read())
            snapshot = self.service.get_session(self.sid)
            events = self.service.get_events(self.sid)["events"]
            self.assertIn(artifact, [e["data"]["artifact"] for e in events if e["type"] == "artifact_ready"])
            self.assertNotIn(status["local_directory"], json.dumps(snapshot))
            self.assertNotIn(CONFIG["api_key"], json.dumps(snapshot))
        status = self.pump_until_done(self.worker(self.service.get_status))
        self.assertTrue(status["capabilities"]["downloads"])
        dev = AgentService()
        try:
            self.assertFalse(dev.get_status()["capabilities"]["downloads"])
        finally:
            dev.close()

    def test_stable_registration_dedup_and_private_resolver_not_tool(self):
        status, artifact = self.accepted()
        snapshot = self.turn_status(self.export_id)
        self.assertEqual(snapshot["artifacts"], [artifact])
        events = self.service.get_events(self.sid)["events"]
        self.assertEqual(len([e for e in events if e["type"] == "artifact_ready"]), 1)
        self.assertNotIn("resolve_artifact", [t["name"] for t in self.service.tools.list_tools()])
        resolved = self.pump_until_done(self.worker(lambda: self.adapter.resolve_artifact(artifact["artifact_id"])))
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["data"]["identity"][2], artifact["size_bytes"])
        self.launch("project")
        second = self.wait_status()["artifact"]
        with mock.patch("AIAssistant.downloads.uuid.uuid4", return_value=types.SimpleNamespace(hex=artifact["file_id"][9:])):
            worker = self.worker(lambda: self.service.downloads.register(second["artifact_id"]))
            self.app.callbacks.get(timeout=1)()
            self.assertTrue(worker[3].wait(2))
            self.assertEqual(worker[2][0].code, "download_id_conflict")
        self.assertEqual(self.get(artifact["download_url"])[0], 200)

    def test_unknown_upload_id_path_query_host_and_origin_never_read_arbitrary_file(self):
        status, artifact = self.accepted()
        payload, content_type = upload_tests.multipart()
        uploaded = self.service.file_store.upload(io.BytesIO(payload), len(payload), content_type)
        for path in ("/api/files/unknown", "/api/files/" + uploaded["file_id"], artifact["download_url"] + "?path=C:/private",
                     "/api/files/../private", "/api/files/%2e%2e%2fprivate"):
            self.assertEqual(self.get(path)[0], 404, path)
        self.assertEqual(self.get(artifact["download_url"], {"Host": "localhost:1"})[0], 403)
        self.assertEqual(self.get(artifact["download_url"], {"Origin": "http://localhost:1"})[0], 403)

    def test_stale_get_409_and_known_invalid_artifact_update(self):
        status, artifact = self.accepted()
        self.adapter._slicing.invalidate()
        self.assertEqual(self.get(artifact["download_url"])[0], 409)
        snapshot = self.turn_status(self.export_id)
        self.assertFalse(snapshot["artifacts"][0]["valid"])
        self.assertNotIn("download_url", snapshot["artifacts"][0])
        self.assertEqual(len(self.service.downloads._by_id), 1)
        self.assertEqual(len(export_tests.Job.created), 1, "Stale GET/status never retries a writer")

    def test_removed_tampered_and_replaced_file_rejected_before_binary_headers(self):
        status, artifact = self.accepted()
        path = os.path.join(status["local_directory"], artifact["file_name"])
        with open(path, "r+b") as stream:
            stream.write(b"X")
        info = os.stat(path)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000))
        code, body, headers = self.get(artifact["download_url"])
        self.assertEqual(code, 409)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertNotIn(path, body.decode())
        os.unlink(path)
        self.assertEqual(self.get(artifact["download_url"])[0], 404)
        with open(path, "wb") as stream:
            stream.write(b"X" * artifact["size_bytes"])
        self.assertEqual(self.get(artifact["download_url"])[0], 409)

    def test_unfinished_or_unknown_native_record_never_gets_download_id(self):
        self.app.gcode.release.clear()
        record = self.launch()
        snapshot = self.turn_status(record.export_id)
        self.assertEqual(snapshot["artifacts"], [])
        self.assertEqual(self.service.downloads._by_id, {})
        worker = self.worker(lambda: self.service.downloads.register("artifact-unknown"))
        self.app.callbacks.get(timeout=1)()
        self.assertTrue(worker[3].wait(2))
        self.assertEqual(worker[2][0].status, 404)
        self.app.gcode.release.set()
        self.wait_status()

    def test_qt_unavailable_and_service_close_return_503_preserve_native_file(self):
        status, artifact = self.accepted()
        path = os.path.join(status["local_directory"], artifact["file_name"])
        self.adapter._bridge.close()
        self.assertEqual(self.get(artifact["download_url"])[0], 503)
        self.service.close()
        self.assertEqual(self.get(artifact["download_url"])[0], 503)
        self.assertTrue(os.path.isfile(path))

    def test_get_bounded_reads_and_close_during_stream_does_not_delete_export(self):
        self.app.engine_body += "G1 X2\n" * 400000
        status, artifact = self.accepted()
        payload, content_type = upload_tests.multipart()
        self.service.file_store.upload(io.BytesIO(payload), len(payload), content_type)
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        sizes = []
        original = self.service.downloads._open_file
        class Reader(object):
            def __init__(self, stream): self.stream = stream
            def read(self, size):
                sizes.append(size)
                entered.set()
                if not release.wait(2): raise AssertionError("Test did not release stream")
                return self.stream.read(size)
            def close(self): self.stream.close(); closed.set()
        with mock.patch.object(self.service.downloads, "_open_file", side_effect=lambda data: Reader(original(data))):
            worker = self.request_worker(artifact["download_url"])
            self.app.callbacks.get(timeout=1)()
            self.assertTrue(entered.wait(2))
            self.assertFalse(self.adapter._change_lock.locked())
            self.service.close()
            self.assertTrue(self.service.file_store.wait_closed(2))
            release.set()
            code, body, headers = self.finish(worker)
        self.assertEqual(code, 200)
        self.assertEqual(len(body), artifact["size_bytes"])
        self.assertTrue(closed.is_set())
        self.assertGreater(len(sizes), 2)
        self.assertTrue(all(0 < n <= 65536 for n in sizes))
        self.assertTrue(os.path.isfile(os.path.join(status["local_directory"], artifact["file_name"])))

    def test_midstream_disconnect_closes_handle_without_json_tail(self):
        status, artifact = self.accepted()
        stream = open(os.path.join(status["local_directory"], artifact["file_name"]), "rb")
        handler = self.server.RequestHandlerClass.__new__(self.server.RequestHandlerClass)
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.api_error = mock.Mock()
        handler.wfile = types.SimpleNamespace(write=mock.Mock(side_effect=BrokenPipeError()))
        with mock.patch.object(self.service, "open_download", return_value=(stream, artifact)):
            handler.download_file(artifact["file_id"])
        self.assertTrue(stream.closed)
        handler.api_error.assert_not_called()

    def test_canonical_resolver_ignores_tool_paths_and_no_service_lock_while_qt(self):
        status, artifact = self.accepted()
        original = self.adapter.resolve_artifact
        def resolve(artifact_id):
            self.assertTrue(self.service._lock.acquire(False), "Never hold Service lock while waiting Qt")
            self.service._lock.release()
            return original(artifact_id)
        self.service.downloads._resolve_native = resolve
        self.assertEqual(self.get(artifact["download_url"])[0], 200)
        forged = dict(status["artifact"], file_name="C:/private", path="C:/private")
        with self.service._lock:
            self.service._active_turn = "canonical-test"
        worker = self.worker(lambda: self.service._remember_result(self.sid, "canonical-test", "get_export_status", {
            "ok": True, "data": {"state": "completed", "valid": True, "artifact": forged}}))
        self.pump_until_done(worker)
        snapshot = self.service.get_session(self.sid)
        self.assertNotIn("C:/private", json.dumps(snapshot))
        self.assertEqual(snapshot["artifacts"][0]["file_name"], artifact["file_name"])
        resolved = self.pump_until_done(self.worker(lambda: original(artifact["artifact_id"])))
        for name in ("bad\r\nHeader.gcode", "../secret.gcode", "C:/secret.gcode"):
            with mock.patch.object(self.service.downloads, "_resolve_native", return_value={
                    "ok": True, "data": dict(resolved["data"], file_name=name)}):
                with self.assertRaises(DownloadError) as caught:
                    self.service.downloads.register(artifact["artifact_id"])
                self.assertEqual(caught.exception.status, 404)
