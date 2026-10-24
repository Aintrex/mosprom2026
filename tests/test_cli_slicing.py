"""TASK10: fork-shaped CLI jobs/processes/Qt queue; no native engine/activation."""
import json
import os
import sys
import types
import unittest
import zipfile
from enum import IntEnum
from unittest import mock

import test_slicer_slicing as base
import test_slicer_exports as export_tests
from AIAssistant.slicer import exports


class StartJobResult(IntEnum):
    Finished, Error, SettingError = range(3)


class Process(object):
    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code

    def wait(self, *args):
        raise AssertionError("Plugin must not wait/kill a process")

    terminate = kill = wait


class JobSignal(base.Signal):
    def emit(self, *args):
        self.app.check_thread()
        # UM.Signal runs observer functions before native bound handlers.
        slots = list(self.slots)
        for callback in sorted(slots, key=lambda f: getattr(f, "__self__", None) is not None):
            callback(*args)


class Job(object):
    def __init__(self, app, process=None):
        self.finished = JobSignal(app)
        self._process, self._build_plate_number = process, 0
        self.running, self.done, self.cancelled = True, False, False
        self.result, self.error = None, None

    def isRunning(self):
        return self.running

    def isFinished(self):
        return self.done

    def isCancelled(self):
        return self.cancelled

    def getError(self):
        return self.error

    def getResult(self):
        return self.result

    def finish(self, emit=True, result=StartJobResult.Finished):
        self.running, self.done, self.result = False, True, result
        if emit:
            self.finished.emit(self)


class CliParserBackend(base.Backend):
    def __init__(self, app):
        super().__init__(app)
        del self._generate_basement_job
        self._process_cli_job, self._glicer_process = None, None
        self._process = Process()  # Persistent CLIParser, intentionally alive after Done.
        self._socket = types.SimpleNamespace(error=base.Signal(app))
        self.fast = False

    def getPluginId(self):
        return "CLIParserBackend"

    def forceSlice(self):
        if self._process is None:
            self._socket = types.SimpleNamespace(error=base.Signal(self.app))
            self._process = Process()
        super().forceSlice()

    def begin(self):
        self.app.info.update(0, (0.0,), (0.0,))
        self.processingProgress.emit(0)
        self.backendStateChange.emit(base.State.Processing)
        self.app.scene.gcode_dict[0] = []
        self.slicingStarted.emit()
        self._start_slice_job = Job(self.app)
        self._start_slice_job_build_plate = 0
        self._start_slice_job.finished.connect(self.prepared)

    def prepared(self, job):
        self._start_slice_job = None
        if job.error or job.cancelled or job.result != StartJobResult.Finished:
            self.backendStateChange.emit(base.State.Error)
            self.backendError.emit(job)
            return
        self.processingProgress.emit(0.1)
        self.backendStateChange.emit(base.State.Processing)  # Next refs do not exist yet.
        self._glicer_process = Process()
        self._process_cli_job = Job(self.app, self._glicer_process)
        self._process_cli_job.finished.connect(self.processed)
        if self.fast:
            self._glicer_process.code = 0
            job = self._process_cli_job
            job.finish(emit=False)  # Worker flags change before the queued finished dispatch.
            self.app.callLater(job.finished.emit, job)

    def processed(self, job):
        self._process_cli_job = None
        if job.error or job.cancelled or job.result != StartJobResult.Finished:
            self.backendStateChange.emit(base.State.Error)
            self.backendError.emit(job)
            return
        self.backendStateChange.emit(base.State.Processing)  # After native sendMessage.
        self.processingProgress.emit(0.3)

    def estimates(self, plate=0, times=None, amounts=None, update=True):
        self.printDurationMessage.emit(plate, {"infill": 3723.0} if times is None else times,
                                      [1234.0] if amounts is None else amounts)
        if update:
            self.app.info.update()

    def parser_data(self):
        self.app.scene.gcode_dict[0] = ["; pending {print_time}\nG1 X10\n"]
        self.backendStateChange.emit(base.State.Processing)
        self.processingProgress.emit(0.8)
        self.estimates()

    def finish(self):
        self.backendStateChange.emit(base.State.Done)
        self.processingProgress.emit(1.0)
        # Same native callback still replaces placeholders and resets state.
        self.app.scene.gcode_dict[0][0] = "; final native header\nG1 X10\n"
        self._start_slice_job_build_plate = None
        self._slicing = False


def install_cli(test):
    arcus = types.ModuleType("Arcus")
    arcus.ErrorCode = types.SimpleNamespace(Debug=1, ConnectionResetError=2)
    patch = mock.patch.dict(sys.modules, {"Arcus": arcus})
    patch.start()
    test.addCleanup(patch.stop)
    test.app.stack.mode = "cylindrical"
    test.app.backend = CliParserBackend(test.app)
    test.app.manager.current = test.app.manager.backends["cylindrical"] = test.app.backend


class CliTests(unittest.TestCase):
    start = base.SlicingTests.start
    status = base.SlicingTests.status
    drain = base.SlicingTests.drain
    assert_error = base.SlicingTests.assert_error

    def setUp(self):
        base.SlicingTests.setUp(self)
        install_cli(self)
        self.backend = self.app.backend
        self.addCleanup(self.stop_fake)

    def stop_fake(self):
        for job in (self.backend._start_slice_job, self.backend._process_cli_job):
            if job is not None:
                job.finish(emit=False)
        if self.backend._glicer_process is not None:
            self.backend._glicer_process.code = 0
        self.backend._start_slice_job = self.backend._process_cli_job = self.backend._process_layers_job = None
        self.backend._slicing = False

    def parser(self, fast=False):
        self.backend.fast = fast
        data = self.start()
        self.drain()
        self.backend._start_slice_job.finish()
        self.drain()
        if not fast:
            self.backend._glicer_process.code = 0
            self.backend._process_cli_job.finish()
            self.drain()
        self.assertTrue(self.adapter._slicing.record.cli.parser_started)
        return data

    def complete(self):
        data = self.parser()
        self.backend.parser_data()
        self.backend.finish()
        self.drain()
        self.assertEqual(self.status()["state"], "completed")
        return data

    def test_multistage_final_deferred_data_pi_and_persistent_parser(self):
        data = self.parser()
        self.assertEqual((data["printing_mode"], data["backend_id"]), ("cylindrical", "CLIParserBackend"))
        self.assertEqual(self.status()["state"], "running")
        self.backend.parser_data()
        self.backend.backendStateChange.emit(base.State.Done)
        self.assertFalse(self.adapter._slicing.record.valid)
        self.backend.processingProgress.emit(1)
        self.assertEqual(self.status()["state"], "running")
        self.backend.finish()
        self.assertEqual(self.status()["state"], "running")  # Readback has not run yet.
        self.drain()
        result = self.adapter.get_slicing_result(self.slice_id)["data"]
        self.assertEqual(result["printing_mode"], "cylindrical")
        self.assertEqual(result["print_time_seconds"], 3723)
        self.assertAlmostEqual(result["material"]["length_mm"], 1270)
        self.assertEqual(self.app.scene.gcode_dict[0][0], "; final native header\nG1 X10\n")
        self.assertIsNone(self.backend._process.poll())
        self.assertFalse(self.adapter._change_lock.locked())

    def test_fast_job_flags_and_fifo_observers_capture_next_stage(self):
        self.parser(fast=True)
        self.backend.parser_data()
        self.backend.finish()
        self.drain()
        self.assertEqual(self.status()["state"], "completed")

    def test_startup_socket_transition_only_inside_own_force_slice(self):
        for existing in (False, True):
            with self.subTest(existing_socket=existing):
                old = self.backend._socket if existing else None
                self.backend._process, self.backend._socket = None, old
                self.complete()
                self.assertIsNot(self.backend._socket, old)
                if old is not None:
                    old.error.emit(types.SimpleNamespace(getErrorCode=lambda: 2))
                    self.assertTrue(self.status()["valid"])
        self.parser()
        self.backend._socket = types.SimpleNamespace(error=base.Signal(self.app))
        self.assertEqual(self.status()["error"]["code"], "slicing_parser_failed")

    def test_intermediate_done_and_old_estimates_never_complete(self):
        self.start()
        self.backend.estimates()
        self.backend.backendStateChange.emit(base.State.Done)
        self.backend.processingProgress.emit(1)
        self.drain()
        self.assertFalse(self.adapter._slicing.record.done)
        self.assertIsNone(self.adapter._slicing.record.statistics)
        self.assertEqual(self.status()["state"], "running")

    def test_preparation_and_process_job_error_cancel_result(self):
        for stage in ("prep", "cli"):
            for failure in ("error", "cancelled", "result"):
                with self.subTest(stage=stage, failure=failure):
                    self.start()
                    self.drain()
                    if stage == "cli":
                        self.backend._start_slice_job.finish()
                        self.drain()
                        self.backend._glicer_process.code = 0
                    job = self.backend._start_slice_job if stage == "prep" else self.backend._process_cli_job
                    if failure == "error":
                        job.error = RuntimeError("private")
                    if failure == "cancelled":
                        job.cancelled = True
                    job.finish(result=StartJobResult.SettingError if failure == "result" else StartJobResult.Finished)
                    self.drain()
                    self.assertEqual(self.status()["state"], "failed")
                    self.stop_fake()
                    self.adapter._slicing._tick()

    def test_nonzero_glicer_even_finished_cli_is_failure(self):
        self.start()
        self.drain()
        self.backend._start_slice_job.finish()
        self.drain()
        self.backend._glicer_process.code = 9
        self.backend._process_cli_job.finish()
        self.drain()
        self.assertEqual(self.status()["error"]["code"], "slicing_process_failed")

    def test_foreign_job_and_changed_process_do_not_confirm(self):
        self.start()
        self.drain()
        self.backend.backendError.emit(Job(self.app))
        self.assertEqual(self.status()["state"], "running")
        self.backend._start_slice_job.finish()
        self.drain()
        self.backend._process_cli_job._process = Process(0)
        self.assertEqual(self.status()["error"]["code"], "slicing_conflict")

    def test_foreign_pi_and_missing_delivery_block_result(self):
        self.parser()
        self.backend.parser_data()
        self.backend.estimates(update=False)
        self.backend.finish()
        self.drain()
        self.assertEqual(self.status()["state"], "running")
        self.app.manager.backends["classic"].printDurationMessage.emit(0, {"foreign": 1}, [1])
        self.assertEqual(self.status()["error"]["code"], "slicing_conflict")

    def test_repeated_latest_pi_and_unknown_material_are_native_values(self):
        self.parser()
        self.backend.parser_data()
        self.backend.estimates(times={"latest": 99}, amounts=[])
        self.app.info.update(seconds=99, lengths=(9,), weights=(9,))
        self.backend.finish()
        self.drain()
        result = self.adapter.get_slicing_result(self.slice_id)["data"]
        self.assertEqual(result["print_time_seconds"], 99)
        self.assertEqual(result["material"], {"length_mm": None, "weight_g": None})
        self.assertEqual(len(result["warnings"]), 2)

    def test_socket_error_or_parser_exit_is_honest_failure(self):
        for failure in ("socket", "process"):
            with self.subTest(failure=failure):
                self.parser()
                if failure == "socket":
                    self.backend._socket.error.emit(types.SimpleNamespace(getErrorCode=lambda: 2))
                else:
                    self.backend._process.code = 0
                self.assertEqual(self.status()["error"]["code"], "slicing_parser_failed")
                self.stop_fake()
                self.adapter._slicing._tick()
                self.backend._process.code = None

    def test_parser_exit_and_socket_restart_after_final_done_still_complete(self):
        # Observed in STE 7.10.12: progress 1.0, then CLIParser exits and the native
        # _onSocketError -> _terminate -> _createSocket restarts it. The result stands.
        for late in (False, True):
            with self.subTest(after_readback=late):
                self.parser()
                self.backend.parser_data()
                self.backend.finish()
                if late:
                    self.drain()
                    self.assertEqual(self.status()["state"], "completed")
                self.backend._process.code = 0
                self.backend._socket.error.emit(types.SimpleNamespace(getErrorCode=lambda: 2))
                self.backend.backendQuit.emit()
                self.backend._process = Process()
                self.backend._socket = types.SimpleNamespace(error=base.Signal(self.app))
                self.drain()
                status = self.status()
                self.assertEqual((status["state"], status["valid"]), ("completed", True), status)
                result = self.adapter.get_slicing_result(self.slice_id)
                self.assertTrue(result["ok"], result)
                self.assertFalse(self.adapter._change_lock.locked())

    def test_timeout_holds_guard_for_live_job_glicer_then_late_exit(self):
        self.start()
        self.drain()
        self.backend._start_slice_job.finish()
        self.drain()
        clock = [0]
        self.adapter._slicing.clock = lambda: clock[0]
        self.adapter._slicing.record.deadline = 1
        clock[0] = 2
        self.assertEqual(self.status()["error"]["code"], "slicing_timeout")
        self.backend._slicing = False
        job = self.backend._process_cli_job
        job.cancelled = True
        job.finish()
        self.drain()
        self.assertTrue(self.adapter._change_lock.locked())
        self.backend._glicer_process.code = 0  # No new signal for this exit.
        self.adapter._slicing.timer.fire()
        self.assertFalse(self.adapter._change_lock.locked())
        self.assertEqual(self.status()["state"], "failed")

    def test_shutdown_readonly_poll_late_exit_and_expired_budget(self):
        for expired in (False, True):
            with self.subTest(expired=expired):
                if expired:
                    # New adapter after the first closed observer, retaining the same fake scene.
                    from AIAssistant.slicer.adapter import SlicerAdapter
                    self.adapter = SlicerAdapter(self.app)
                self.start()
                self.drain()
                self.backend._start_slice_job.finish()
                self.drain()
                job = self.backend._process_cli_job
                job.cancelled = True
                job.finish()
                self.backend._slicing = False
                self.drain()
                clock = [0]
                self.adapter._slicing.clock = lambda: clock[0]
                record = self.adapter._slicing.record
                self.adapter.close()
                timer = self.adapter._slicing.timer
                with mock.patch.object(self.app, "getPrintInformation", side_effect=AssertionError("No PI after close")), \
                        mock.patch.object(self.app, "getController", side_effect=AssertionError("No scene after close")):
                    clock[0] = 31 if expired else 29
                    if not expired:
                        self.backend._glicer_process.code = 0
                    timer.fire()
                self.assertTrue(timer.deleted)
                self.assertEqual(record.held, expired)
                if expired:
                    self.assertIn("30", record.error["message"])
                    self.backend._glicer_process.code = 0
                    timer.fire()
                    self.assertTrue(record.held)

    def test_mode_change_buffer_mutation_and_old_result_are_stale(self):
        self.complete()
        old = self.slice_id
        self.app.scene.gcode_dict[0].append("G1 X999\n")
        self.assert_error(self.adapter.get_slicing_result(old), "slice_result_stale")
        self.complete()
        self.app.stack.mode = "classic"
        self.app.manager.current = self.app.manager.backends["classic"]
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_result_stale")

    def test_old_buffer_or_missing_final_data_and_second_start_never_succeed(self):
        old = self.app.scene.gcode_dict[0]
        self.parser()
        self.app.scene.gcode_dict[0] = old
        self.backend.estimates()
        self.backend.finish()
        self.drain()
        self.assertEqual(self.status()["state"], "failed")
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_not_ready")
        self.start()
        self.backend.slicingStarted.emit()
        self.assertEqual(self.status()["error"]["code"], "slicing_conflict")

    def test_stale_buffers_wrong_registration_full_and_missing_layout_rejected(self):
        self.app.stack.mode = "cylindrical_full"
        self.assert_error(self.adapter.start_slicing(), "unsupported_slicing_mode")
        self.app.stack.mode = "cylindrical"
        self.app.manager.current = self.app.manager.backends["classic"]
        self.assert_error(self.adapter.start_slicing(), "unsupported_slicing_mode")
        self.app.manager.current = self.backend
        del self.backend._socket
        self.assert_error(self.adapter.start_slicing(), "backend_state_unavailable")
        self.assertEqual(self.backend.calls, 0)


class WorkspaceWriter(export_tests.Writer):
    def _write(self, stream, nodes, mode):
        self.app.check_thread()
        self.calls.append((nodes, mode, stream))
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("3D/3dmodel.model", export_tests.MODEL)
            for name in exports.workspace_files(self.app, self.app.stack):
                data = "[values]\nprinting_mode = %s\nlayer_height = %s\n" % (
                    self.app.stack.getProperty("printing_mode", "value"), self.app.stack.getProperty("layer_height", "value"))
                archive.writestr(name, data)
        return True


class CliExportTests(unittest.TestCase):
    # Reuse existing native-writer test harness, not its classic test cases.
    start, status, drain = CliTests.start, CliTests.status, CliTests.drain
    launch, wait_status = export_tests.ExportTests.launch, export_tests.ExportTests.wait_status
    clean_workers = export_tests.ExportTests.clean_workers

    def complete(self):
        install_cli(self)
        self.app.backend.fast = True
        self.start()
        self.drain()
        self.app.backend._start_slice_job.finish()
        self.drain()
        self.app.backend.parser_data()
        self.app.backend.finish()
        self.drain()
        self.assertEqual(self.status()["state"], "completed")

    def setUp(self):
        export_tests.ExportTests.setUp(self)
        self.app.project = WorkspaceWriter(self.app, "project")

    def test_export_final_cli_gcode_and_workspace_mode_settings(self):
        record = self.launch()
        result = self.wait_status()
        self.assertEqual(result["state"], "completed", result)
        with open(os.path.join(result["local_directory"], result["artifact"]["file_name"]), "rb") as stream:
            self.assertIn(b"; final native header\nG1 X10", stream.read())
        self.assertIsNone(self.app.backend._process.poll())
        self.launch("project")
        project = self.wait_status()
        self.assertEqual(project["state"], "completed", project)
        path = os.path.join(project["local_directory"], project["artifact"]["file_name"])
        with zipfile.ZipFile(path) as archive:
            values = archive.read("SteSlicer/global.cfg")
            self.assertIn(b"printing_mode = cylindrical", values)
            self.assertIn(b"layer_height = ", values)
        self.assertEqual(project["artifact"]["slice_id"], record.slice_id)
