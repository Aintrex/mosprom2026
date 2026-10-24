"""TASK 06: real filesystem, fork-shaped fake WriteFileJob/writers, no native export."""
import json
import os
import queue
import sys
import tempfile
import threading
import time
import types
import unittest
import zipfile
from unittest import mock

import test_slicer_slicing as slicing_tests
import test_slicer_modes as mode_tests
from AIAssistant.agent.mcp import dispatch
from AIAssistant.agent.tools import ToolRegistry
from AIAssistant.slicer import exports
from AIAssistant.slicer.adapter import SlicerAdapter


MODEL = b'''<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>
<object id="1"><mesh><vertices><vertex x="0" y="0" z="0"/></vertices>
<triangles><triangle v1="0" v2="0" v3="0"/></triangles></mesh></object></resources>
<build><item objectid="1"/></build></model>'''


class Empty(object):
    pass


class Registry(object):
    def __init__(self, app):
        self.app = app

    def getEmptyInstanceContainer(self):
        self.app.check_thread()
        return Empty()

    @staticmethod
    def getMimeTypeForContainer(cls):
        return types.SimpleNamespace(preferredSuffix="cfg")


class MeshWriter(object):
    def __init__(self, app):
        self.app, self.archive = app, None
        self.store = False
        self.reset_calls = 0

    def getArchive(self):
        self.app.check_thread()
        return self.archive

    def setStoreArchive(self, flag):
        self.app.check_thread()
        self.store = flag
        self.reset_calls += int(not flag)


class Writer(object):
    def __init__(self, app, kind):
        self.app, self.kind = app, kind
        self.calls = []
        self.behavior = "ok"
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()

    def getInformation(self):
        return "writer error"

    def write(self, stream, nodes, mode):
        if threading.current_thread() is self.app.main:
            raise AssertionError("Native decorated entry must be called by WriteFileJob worker")
        self.entered.set()
        if not self.release.wait(3):
            raise AssertionError("Fake writer was not released")
        if self.kind == "project":
            # Как у fork: вход с job worker, тело workspace write на Qt.
            event, result, errors = threading.Event(), [], []

            def qt_write():
                try:
                    result.append(self._write(stream, nodes, mode))
                except Exception as exc:
                    errors.append(exc)
                finally:
                    event.set()
            self.app.callLater(qt_write)
            if not event.wait(3):
                raise AssertionError("Qt callback deadlock")
            if errors:
                raise errors[0]
            return result[0]
        return self._write(stream, nodes, mode)

    def _write(self, stream, nodes, mode):
        if self.kind == "project":
            self.app.check_thread()
        self.calls.append((nodes, mode, stream))
        if self.behavior == "raise":
            if self.kind == "project":
                self.app.mesh_project.setStoreArchive(True)
            raise IOError("sensitive disk detail")
        if self.behavior == "false":
            return False
        if self.behavior == "empty":
            return True
        if self.kind == "gcode":
            stream.write("; native metadata\n")
            if self.behavior != "headers_only":
                # Access to app is intentionally absent in our worker; this fake
                # receives the engine body in advance, like a native data source.
                stream.write(self.app.engine_body)
            stream.write(";SETTING_3 native-settings\n")
        else:
            self.app.mesh_project.setStoreArchive(True)
            if self.behavior == "corrupt":
                stream.write(b"not a zip")
                return True
            archive = zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED)
            self.app.mesh_project.archive = archive
            archive.writestr("3D/3dmodel.model", b"<broken" if self.behavior == "bad_xml" else MODEL)
            if self.behavior == "renamed_workspace":
                # Issued build layout differing from the public sources (observed failure on 24.10).
                archive.writestr("SteSlicer/machine_instance.global.cfg", b"[general]\nname=fixture\n")
                archive.writestr("SteSlicer/version.ini", b"[versions]\n")
            elif self.behavior == "empty_workspace":
                archive.writestr("SteSlicer/preferences.cfg", b"")
            elif self.behavior != "mesh_only":
                for name in exports.workspace_files(self.app, self.app.stack):
                    archive.writestr(name, b"[general]\nname=fixture\n")
            archive.close()
            self.app.mesh_project.setStoreArchive(False)
        return True


class Job(object):
    app = None
    created = []
    force_error = False

    def __init__(self, writer, stream, nodes, mode):
        self.app.check_thread()
        self.writer, self.stream, self.nodes, self.mode = writer, stream, nodes, mode
        self.finished, self.progress = slicing_tests.Signal(self.app), slicing_tests.Signal(self.app)
        self.result, self.error = None, None
        self.started = False
        self.created.append(self)

    def setAddToRecentFiles(self, value):
        self.app.check_thread()
        if value:
            raise AssertionError("No preferences/recent files side effect")

    def getResult(self):
        self.app.check_thread()
        return self.result

    def getError(self):
        self.app.check_thread()
        return self.error

    def start(self):
        self.app.check_thread()
        self.started = True

        def run():
            try:
                self.result = self.writer.write(self.stream, self.nodes, self.mode)
                if not self.result or self.force_error:
                    self.error = "error"
            except Exception as exc:
                self.error = exc
            self.app.callLater(lambda: self.finished.emit(self))
        self.thread = threading.Thread(target=run)
        self.thread.start()


class ExportTests(unittest.TestCase):
    worker = slicing_tests.SlicingTests.worker
    finish = slicing_tests.SlicingTests.finish
    pump_until_done = slicing_tests.SlicingTests.pump_until_done
    assert_error = slicing_tests.SlicingTests.assert_error
    start = slicing_tests.SlicingTests.start
    status = slicing_tests.SlicingTests.status
    complete = slicing_tests.SlicingTests.complete
    drain = slicing_tests.SlicingTests.drain

    def setUp(self):
        slicing_tests.SlicingTests.setUp(self)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app.registry = Registry(self.app)
        self.app.getContainerRegistry = lambda: self.app.registry
        self.app.gcode = Writer(self.app, "gcode")
        self.app.project = Writer(self.app, "project")
        self.app.mesh_project = MeshWriter(self.app)
        self.app.mesh_handler_calls, self.app.workspace_handler_calls = [], []

        def get_writer(writer_id, workspace=False):
            self.app.check_thread()
            (self.app.workspace_handler_calls if workspace else self.app.mesh_handler_calls).append(writer_id)
            if workspace:
                return self.app.project
            return self.app.gcode if writer_id == "GCodeWriter" else self.app.mesh_project
        self.app.getMeshFileHandler = lambda: types.SimpleNamespace(getWriter=get_writer)
        self.app.getWorkspaceFileHandler = lambda: types.SimpleNamespace(getWriter=lambda name: get_writer(name, True))
        self.device = types.SimpleNamespace(_writing=False)
        self.outputs = types.SimpleNamespace(getOutputDevice=lambda name: self.device, writeStarted=slicing_tests.Signal(self.app))
        self.app.getOutputDeviceManager = lambda: self.outputs
        # Fork SceneNode.getParent getter.
        self.app.part.getParent = lambda: self.app.scene.root
        qt_resources = types.ModuleType("UM.Resources")
        cache = self.directory.name

        def get_cache():
            self.app.check_thread()
            return cache
        qt_resources.Resources = types.SimpleNamespace(getCacheStoragePath=get_cache)
        file_writer = types.ModuleType("UM.FileHandler.FileWriter")
        file_writer.FileWriter = types.SimpleNamespace(OutputMode=types.SimpleNamespace(TextMode=1, BinaryMode=2))
        jobs = types.ModuleType("UM.FileHandler.WriteFileJob")
        jobs.WriteFileJob = Job
        containers = types.ModuleType("UM.Settings.ContainerRegistry")
        containers.ContainerRegistry = Registry
        self.modules = mock.patch.dict(sys.modules, {"UM.Resources": qt_resources, "UM.FileHandler.FileWriter": file_writer,
            "UM.FileHandler.WriteFileJob": jobs, "UM.Settings.ContainerRegistry": containers})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        Job.app, Job.created, Job.force_error = self.app, [], False
        self.complete()
        self.app.engine_body = "".join(self.app.scene.gcode_dict[0])
        self.addCleanup(self.clean_workers)

    def clean_workers(self):
        for writer in (self.app.gcode, self.app.project):
            if writer is not None:
                writer.release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.drain()
            self.adapter._exports._tick()
            if all(not job.thread.is_alive() for job in Job.created if job.started):
                break
            time.sleep(0.001)
        for record in self.adapter._exports.records.values():
            record.thread.join(0.2)

    def launch(self, kind="gcode"):
        callback = self.adapter.export_gcode if kind == "gcode" else self.adapter.save_project_3mf
        response = callback(self.slice_id)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["data"]["state"], "queued")
        self.export_id = response["data"]["export_id"]
        return self.adapter._exports.records[self.export_id]

    def wait_status(self, export_id=None, terminal=True):
        export_id = export_id or self.export_id
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.drain()
            result = self.adapter.get_export_status(export_id)
            self.assertTrue(result["ok"], result)
            status = result["data"]
            if not terminal and status["state"] == "running" or terminal and status["state"] in ("completed", "failed"):
                return status
            time.sleep(0.001)
        self.fail("Export never reached expected state: %r" % status)

    def wait_cleanup(self, record):
        deadline = time.monotonic() + 3
        while self.adapter._exports.active is record and time.monotonic() < deadline:
            self.drain()
            self.adapter._exports._tick()
            time.sleep(0.001)
        record.thread.join(0.2)
        self.assertFalse(record.thread.is_alive())
        self.assertFalse(self.adapter._change_lock.locked())

    def test_schemas_validation_and_dev_without_app(self):
        tools = ToolRegistry()
        for name, field in exports.EXPORT_FIELDS.items():
            schema = next(tool["inputSchema"] for tool in tools.list_tools() if tool["name"] == name)
            self.assertEqual(schema["required"], [field])
            self.assertFalse(schema["additionalProperties"])
            for invalid in ({}, [], {field: ""}, {field: 1}, {field: True}, {field: "id", "output_path": "C:/x"}):
                response = dispatch(mode_tests.ModeTests.request(name, invalid), self.tools)
                self.assertEqual(response["error"]["code"], -32602)
            self.assert_error(tools.call(name, {field: "id"}), "slicer_not_connected")

    def test_unknown_not_ready_and_stale_slice_do_not_start_writer(self):
        self.assert_error(self.adapter.export_gcode("unknown"), "slice_not_found")
        self.app.stack.value = 0.25
        self.assert_error(self.adapter.export_gcode(self.slice_id), "slice_result_stale")
        self.app.stack.value = 0.2
        self.start()
        self.assert_error(self.adapter.save_project_3mf(self.slice_id), "export_busy")
        self.app.backend.slicingCancelled.emit()
        self.app.backend.idle()
        self.status()
        self.assert_error(self.adapter.export_gcode(self.slice_id), "slice_not_ready")
        self.assertEqual(Job.created, [])

    def test_missing_writer_native_busy_and_ui_busy_preflight(self):
        self.app.gcode = None
        self.assert_error(self.adapter.export_gcode(self.slice_id), "export_writer_unavailable")
        self.app.backend._process_layers_job = object()
        self.assert_error(self.adapter.save_project_3mf(self.slice_id), "export_busy")
        self.app.backend.idle()
        self.device._writing = True
        self.assert_error(self.adapter.save_project_3mf(self.slice_id), "export_busy")
        self.assertFalse(self.adapter._change_lock.locked())
        self.assertEqual(Job.created, [])

    def test_pair_correct_handlers_modes_contents_and_registry(self):
        first = self.launch()
        gcode = self.wait_status()
        self.assertEqual(gcode["state"], "completed")
        self.assertTrue(gcode["valid"])
        first.thread.join(1)
        second = self.launch("project")
        project = self.wait_status()
        second.thread.join(1)
        self.assertEqual(project["state"], "completed")
        self.assertEqual(gcode["slice_id"], project["slice_id"])
        self.assertEqual(self.app.gcode.calls[0][1], 1)
        self.assertEqual(self.app.project.calls[0][1], 2)
        self.assertEqual(self.app.workspace_handler_calls, ["3MFWriter"])
        for status in (gcode, project):
            artifact = status["artifact"]
            path = os.path.join(status["local_directory"], artifact["file_name"])
            self.assertTrue(os.path.commonpath([path, self.directory.name]) == self.directory.name)
            self.assertEqual(os.path.getsize(path), artifact["size_bytes"])
            self.assertNotEqual(artifact["artifact_id"], path)
            self.assertEqual(artifact["source"], "ste_slicer")
            self.assertIn(artifact["artifact_id"], self.adapter._exports.artifacts)
        with open(os.path.join(gcode["local_directory"], "result.gcode"), encoding="utf-8") as stream:
            self.assertIn(self.app.engine_body, stream.read())
        exports.verify_project(os.path.join(project["local_directory"], "project.3mf"), exports.workspace_files(self.app, self.app.stack))
        self.assertFalse(self.app.mesh_project.store)
        self.assertEqual(project["artifact"]["writer_type"], "workspace_writer")

    def test_delayed_job_progress_real_fraction_guard_and_reads_available(self):
        self.app.gcode.release.clear()
        record = self.launch()
        status = self.wait_status(terminal=False)
        self.assertIsNone(status["progress"])
        self.assertIsNone(status["artifact"])
        self.assertFalse(record.stream.closed)
        record.job.progress.emit(record.job, 42)
        record.job.progress.emit(object(), 100)
        for bad in (float("nan"), float("inf"), -1, 101, True):
            record.job.progress.emit(record.job, bad)
        self.assertEqual(self.adapter.get_export_status(record.export_id)["data"]["progress"], 0.42)
        self.assert_error(self.adapter.start_slicing(), "slicing_busy")
        self.assert_error(self.adapter.export_gcode(self.slice_id), "export_busy")
        self.assert_error(self.adapter.set_setting("layer_height", "global", 0.24), "slicing_busy")
        self.assertTrue(self.adapter.get_setting("layer_height", "global")["ok"])
        self.assertTrue(self.adapter.list_models()["ok"])
        self.app.gcode.release.set()
        self.assertEqual(self.wait_status()["state"], "completed")

    def test_writer_false_exception_error_empty_and_headers_only_are_failed(self):
        for behavior in ("false", "raise", "empty", "headers_only"):
            self.app.gcode.behavior = behavior
            record = self.launch()
            with self.assertLogs("AIAssistant.slicer.exports", level="ERROR") if behavior in ("empty", "headers_only") else mock.patch.object(exports.LOGGER, "error"):
                status = self.wait_status()
            self.assertEqual(status["state"], "failed")
            self.assertIsNone(status["artifact"])
            self.wait_cleanup(record)
        self.app.gcode.behavior, Job.force_error = "ok", True
        record = self.launch()
        self.assertEqual(self.wait_status()["state"], "failed")
        self.wait_cleanup(record)
        self.assertEqual(self.adapter._exports.artifacts, {})

    def test_corrupt_mesh_only_or_bad_xml_project_never_published(self):
        for behavior in ("corrupt", "mesh_only", "bad_xml", "raise"):
            self.app.project.behavior = behavior
            record = self.launch("project")
            with mock.patch.object(exports.LOGGER, "error"):
                status = self.wait_status()
            self.assertEqual(status["state"], "failed")
            self.wait_cleanup(record)
            self.assertFalse(self.app.mesh_project.store)
        self.assertEqual(self.adapter._exports.artifacts, {})

    def test_geometry_change_during_write_fails_and_does_not_close_live_stream(self):
        self.app.gcode.release.clear()
        record = self.launch()
        self.wait_status(terminal=False)
        stream = record.stream
        self.app.part.matrix[0][3] = 10
        status = self.wait_status()
        self.assertEqual(status["error"]["code"], "export_result_stale")
        self.assertFalse(stream.closed)
        self.assertTrue(self.adapter._change_lock.locked())
        self.app.gcode.release.set()
        self.wait_cleanup(record)
        self.assertTrue(stream.closed)
        self.assertIsNone(self.adapter.get_export_status(record.export_id)["data"]["artifact"])

    def test_timeout_late_finished_keeps_stream_guard_and_no_artifact(self):
        self.app.gcode.release.clear()
        record = self.launch()
        self.wait_status(terminal=False)
        stream = record.stream
        self.adapter._exports.clock = lambda: record.deadline + 1
        status = self.wait_status()
        self.assertEqual(status["error"]["code"], "export_timeout")
        self.assertTrue(self.adapter._change_lock.locked())
        self.assertFalse(stream.closed)
        self.app.gcode.release.set()
        self.wait_cleanup(record)
        self.assertTrue(stream.closed)
        self.assertEqual(self.adapter.get_export_status(record.export_id)["data"]["state"], "failed")

    def test_shutdown_late_finished_cleanup_without_publication(self):
        self.app.gcode.release.clear()
        record = self.launch()
        self.wait_status(terminal=False)
        stream, job, timer = record.stream, record.job, self.adapter._exports.timer
        self.adapter.close()
        self.assertTrue(timer.deleted)
        self.assertFalse(stream.closed)
        self.assertEqual(job.progress.slots, [])
        self.assertEqual(len(job.finished.slots), 1)
        self.app.gcode.release.set()
        deadline = time.monotonic() + 3
        while record.thread.is_alive() and time.monotonic() < deadline:
            self.drain()
            time.sleep(0.001)
        record.thread.join(0.1)
        self.assertFalse(record.thread.is_alive())
        self.assertTrue(stream.closed)
        self.assertEqual(job.finished.slots, [])
        self.assertEqual(self.adapter._exports.artifacts, {})
        self.assert_error(self.adapter.get_export_status(record.export_id), "slicer_not_connected")

    def test_ui_write_conflict_is_reported(self):
        self.app.gcode.release.clear()
        record = self.launch()
        self.wait_status(terminal=False)
        self.outputs.writeStarted.emit(self.device)
        self.assertEqual(self.wait_status()["error"]["code"], "export_conflict")
        self.app.gcode.release.set()
        self.wait_cleanup(record)

    def test_unknown_export_id_and_historical_file_keeps_own_slice_id(self):
        record = self.launch()
        status = self.wait_status()
        record.thread.join(1)
        self.assert_error(self.adapter.get_export_status("unknown"), "export_not_found")
        self.app.stack.value = 0.24
        historical = self.adapter.get_export_status(record.export_id)["data"]
        self.assertFalse(historical["valid"])
        self.assertEqual(historical["artifact"]["slice_id"], status["slice_id"])
        self.assertTrue(os.path.isfile(os.path.join(historical["local_directory"], "result.gcode")))

    def test_open_failure_and_no_private_path_or_exception_in_response(self):
        with mock.patch.object(exports, "open", side_effect=PermissionError("sensitive disk detail"), create=True):
            with self.assertLogs("AIAssistant.slicer.exports", level="ERROR") as captured:
                record = self.launch()
                status = self.wait_status()
            self.wait_cleanup(record)
        self.assertEqual(status["error"]["code"], "export_io_error")
        self.assertNotIn("sensitive disk detail", "".join(captured.output))
        self.assertIsNone(status["local_directory"])
        self.assertEqual(Job.created, [])

    def test_flush_or_close_failure_not_reported_as_completed(self):
        original_open = open

        class Stream(object):
            failure = None

            def __init__(self, wrapped):
                self.wrapped = wrapped

            def write(self, data):
                return self.wrapped.write(data)

            def flush(self):
                if self.failure == "flush":
                    raise OSError("disk full")
                self.wrapped.flush()

            def close(self):
                self.wrapped.close()
                if self.failure == "close":
                    raise OSError("late disk failure")

        def failing_open(path, mode="r", **kwargs):
            stream = original_open(path, mode, **kwargs)
            return Stream(stream) if path.endswith("writing.tmp") and mode == "x" else stream
        for failure in ("flush", "close"):
            Stream.failure = failure
            with mock.patch.object(exports, "open", side_effect=failing_open, create=True):
                record = self.launch()
                with mock.patch.object(exports.LOGGER, "error"):
                    status = self.wait_status()
                    self.wait_cleanup(record)
            self.assertEqual(status["error"]["code"], "export_io_error")
            self.assertIsNone(status["artifact"])
        self.assertEqual(self.adapter._exports.artifacts, {})

    def test_worker_mcp_start_and_unknown_id_operational_error(self):
        worker = self.worker(lambda: self.tools.call("export_gcode", {"slice_id": self.slice_id}))
        self.app.callbacks.get(timeout=1)()
        response = self.finish(worker)
        self.assertTrue(response["ok"], response)
        self.export_id = response["data"]["export_id"]
        self.assertEqual(self.wait_status()["state"], "completed")
        response = dispatch(mode_tests.ModeTests.request("get_export_status", {"export_id": "unknown"}), self.tools)
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(json.loads(response["result"]["content"][0]["text"])["error"]["code"], "export_not_found")

    def test_existing_final_file_not_overwritten_or_removed(self):
        original_open = open
        foreign = []

        def conflict_open(path, mode="r", **kwargs):
            if path.endswith("result.gcode") and mode == "xb":
                with original_open(path, "wb") as stream:
                    stream.write(b"foreign content")
                foreign.append(path)
            return original_open(path, mode, **kwargs)
        with mock.patch.object(exports, "open", side_effect=conflict_open, create=True):
            record = self.launch()
            with mock.patch.object(exports.LOGGER, "error"), mock.patch.object(exports.LOGGER, "warning"):
                self.assertEqual(self.wait_status()["state"], "failed")
                self.wait_cleanup(record)
        self.assertEqual(len(foreign), 1)
        with original_open(foreign[0], "rb") as stream:
            self.assertEqual(stream.read(), b"foreign content")

    def test_renamed_workspace_is_published_with_warning_but_mesh_only_or_empty_fail(self):
        self.app.project.behavior = "renamed_workspace"
        record = self.launch("project")
        with mock.patch.object(exports.LOGGER, "warning") as warning:
            status = self.wait_status()
        self.assertEqual(status["state"], "completed", status)
        self.assertTrue(status["valid"])
        self.assertTrue(status["artifact"]["file_name"].endswith(".3mf"))
        self.assertIn("отличается от ожидаемого", status["warnings"][0])
        self.assertTrue(warning.called)
        self.wait_cleanup(record)
        for behavior in ("mesh_only", "empty_workspace"):
            self.app.project.behavior = behavior
            record = self.launch("project")
            with mock.patch.object(exports.LOGGER, "error"):
                status = self.wait_status()
            self.assertEqual((status["state"], status["error"]["code"]), ("failed", "export_invalid_file"))
            self.wait_cleanup(record)

    def test_partial_publication_and_preparation_timeout_do_not_publish(self):
        def partial(source, target):
            target.write(b"partial")
        with mock.patch.object(exports.shutil, "copyfileobj", side_effect=partial):
            record = self.launch()
            with mock.patch.object(exports.LOGGER, "error"):
                self.assertEqual(self.wait_status()["error"]["code"], "export_invalid_file")
                self.wait_cleanup(record)
        record = self.launch()
        self.adapter._exports.clock = lambda: record.deadline + 1
        self.assertEqual(self.wait_status()["error"]["code"], "export_timeout")
        self.wait_cleanup(record)
        self.assertEqual(len(Job.created), 1)
        self.assertEqual(self.adapter._exports.artifacts, {})

    def test_shutdown_before_native_start_no_job_and_temporary_cleanup(self):
        record = self.launch()
        self.adapter.close()
        record.thread.join(1)
        self.assertFalse(record.thread.is_alive())
        self.assertEqual(Job.created, [])
        self.assertEqual(self.adapter._exports.artifacts, {})
        self.assertEqual(self.adapter._exports.records, {})
        self.assertIsNone(record.writer)

    def test_group_and_child_transforms_pass_unchanged_service_descendant_rejected(self):
        # No copies/reparenting: native writer receives the complete printable group.
        self.app.scene.root.children.clear()
        group = self.app.scene.root.add(slicing_tests.Node(self.app, "group", mesh=False, group=True))
        group.getParent = lambda: self.app.scene.root
        child = group.add(slicing_tests.Node(self.app))
        group.matrix[0][3], child.matrix[2][3] = 10, 20
        self.complete()
        self.app.engine_body = "".join(self.app.scene.gcode_dict[0])
        record = self.launch("project")
        self.assertEqual(self.wait_status()["state"], "completed")
        record.thread.join(1)
        self.assertIs(self.app.project.calls[0][0][0], group)
        self.assertEqual(child.matrix[2][3], 20)
        group.add(slicing_tests.Node(self.app, "service", sliceable=False))
        self.assert_error(self.adapter.save_project_3mf(self.slice_id), "export_scene_unsupported")
