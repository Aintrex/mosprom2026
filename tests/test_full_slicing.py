"""Full 5D source-shaped fake native chain, including distinct basement job API."""
import os
import types
import unittest
import zipfile
from enum import IntEnum
from unittest import mock

import test_slicer_slicing as base
import test_cli_slicing as cli
import test_slicer_exports as export_tests


FullResult = IntEnum("StartJobResult", {"Finished": 1, "Error": 2})


class Prep(cli.Job):
    def __init__(self, app):
        super().__init__(app)
        del self._build_plate_number
        self._start_slice_job_build_plate = 0

    def finish(self, emit=True, result=FullResult.Finished):
        super().finish(emit, result)


class Basement(cli.Job):
    def __init__(self, app):
        super().__init__(app)
        self._build_plate_number = None  # Native outer never calls setBuildPlate.

    def finish(self, emit=True, result=None):
        super().finish(emit, result)

    def getGCodeList(self):
        return ["; native basement\nG0 A0\n"]

    def getLayersData(self):
        return []


class CylindricalBackend(base.Backend):
    def __init__(self, app):
        super().__init__(app)
        self._process_cli_job = self._glicer_process = None
        self.children = {"CuraEngineBackend": app.manager.backends["classic"],
                         "CLIParserBackend": app.manager.backends["cylindrical"]}
        for child in self.children.values():
            child._process = cli.Process()
            child._socket = types.SimpleNamespace(error=base.Signal(app))
        self.fast = False

    def getPluginId(self):
        return "CylindricalBackend"

    def getBackends(self):
        return self.children

    def begin(self):
        self.backendStateChange.emit(base.State.NotStarted)
        self.app.info.update(0, (0,), (0,))
        self.backendStateChange.emit(base.State.Processing)
        self.app.scene.gcode_dict[0] = []
        self.slicingStarted.emit()
        self._start_slice_job = Prep(self.app)
        self._start_slice_job_build_plate = 0
        self._start_slice_job.finished.connect(self.prepared)

    def prepared(self, job):
        self._start_slice_job = None
        if job.error or job.cancelled or job.result != FullResult.Finished:
            self.backendStateChange.emit(base.State.Error)
            self.backendError.emit(job)
            return
        self.backendStateChange.emit(base.State.Processing)
        self.processingProgress.emit(0.1)
        self._generate_basement_job = Basement(self.app)
        self._generate_basement_job.finished.connect(self.basement_finished)
        if self.fast:
            job = self._generate_basement_job
            job.finish(emit=False)
            self.app.callLater(job.finished.emit, job)

    def reset_child(self, name):
        child = self.children[name]
        if child._process is not None:
            child._process.code = 0
        child._socket = types.SimpleNamespace(error=base.Signal(self.app))
        child._process = cli.Process()

    def basement_finished(self, job):
        if not self.app.scene.gcode_dict[0]:
            self.app.scene.gcode_dict[0] = []
        self.app.scene.gcode_dict[0].extend(job.getGCodeList())
        self._generate_basement_job = None
        if job.cancelled:
            self.backendStateChange.emit(base.State.NotStarted)
            self.backendError.emit(job)
            return
        self.reset_child("CuraEngineBackend")
        if self.fast:
            self.app.callLater(self.cura_finished)

    def cura_finished(self):
        # Native emits Processing before creating Glicer, never outer Done here.
        self.backendStateChange.emit(base.State.Processing)
        self.processingProgress.emit(0.5)
        self.app.scene.gcode_dict[0].append("; native Cura part\nG1 X10\n")
        self._glicer_process = cli.Process()
        self._process_cli_job = cli.Job(self.app, self._glicer_process)
        self._process_cli_job.finished.connect(self.cli_finished)
        if self.fast:
            self._glicer_process.code = 0
            job = self._process_cli_job
            job.finish(emit=False)
            self.app.callLater(job.finished.emit, job)

    def cli_finished(self, job):
        self.reset_child("CLIParserBackend")  # Source does this even before error/cancel checks.
        self._process_cli_job = None
        if job.error or job.cancelled:
            self.backendStateChange.emit(base.State.Error)
            self.backendError.emit(job)
            return
        self.backendStateChange.emit(base.State.Processing)
        self.processingProgress.emit(0.6)

    def estimates(self, seconds=800, lengths=(2.1,), weights=(6.2,)):
        self.printDurationMessage.emit(0, {"outer_total": seconds}, [1234.0])
        self.app.info.update(seconds, lengths, weights)

    def parser_data(self):
        self.app.scene.gcode_dict[0].append("; pending full {print_time}\nG1 A10 C20\n")
        self.estimates()
        self.backendStateChange.emit(base.State.Processing)

    def finish(self):
        self.backendStateChange.emit(base.State.Done)
        self.processingProgress.emit(1)
        self.app.scene.gcode_dict[0][-1] = "; final native full\nG1 A10 C20\n"
        self._slicing = False
        self._start_slice_job_build_plate = None


def install_full(test):
    cli.install_cli(test)
    test.app.stack.mode = "cylindrical_full"
    test.app.backend = CylindricalBackend(test.app)
    test.app.manager.current = test.app.manager.backends["cylindrical_full"] = test.app.backend


class FullTests(unittest.TestCase):
    start, status, drain = cli.CliTests.start, cli.CliTests.status, cli.CliTests.drain
    assert_error = cli.CliTests.assert_error

    def setUp(self):
        base.SlicingTests.setUp(self)
        install_full(self)
        self.backend = self.app.backend
        self.addCleanup(self.stop_fake)

    def stop_fake(self):
        stage = getattr(self.adapter._slicing.record, "cli", None)
        if stage is not None:
            for endpoint in (stage.cura, stage.parser):
                if endpoint["process"] is not None:
                    endpoint["process"].code = 0
            if stage.process is not None:
                stage.process.code = 0
        for job in (self.backend._start_slice_job, self.backend._generate_basement_job, self.backend._process_cli_job):
            if job is not None:
                job.finish(emit=False)
        if self.backend._glicer_process is not None:
            self.backend._glicer_process.code = 0
        for child in self.backend.children.values():
            if child._process is not None:
                child._process.code = 0
        self.backend._start_slice_job = self.backend._generate_basement_job = self.backend._process_cli_job = None
        self.backend._slicing = False
        self.adapter._slicing._tick()

    def basement(self):
        self.start()
        self.drain()
        self.backend._start_slice_job.finish()
        self.drain()

    def cura(self):
        self.basement()
        self.backend._generate_basement_job.finish()
        self.drain()

    def parser(self):
        self.cura()
        self.backend.estimates(120)
        self.backend.cura_finished()
        self.drain()
        self.backend._glicer_process.code = 0
        self.backend._process_cli_job.finish()
        self.drain()
        self.assertTrue(self.adapter._slicing.record.cli.parser_started)

    def complete(self):
        self.parser()
        self.backend.parser_data()
        self.backend.finish()
        self.drain()
        self.assertEqual(self.status()["state"], "completed")

    def test_complete_chain_final_readback_and_last_outer_pi(self):
        self.parser()
        self.assertEqual(self.status()["state"], "running")
        self.assertIsNone(self.adapter._slicing.record.statistics)
        self.backend.parser_data()
        self.backend.finish()
        self.assertFalse(self.adapter._slicing.record.valid)
        self.drain()
        result = self.adapter.get_slicing_result(self.slice_id)["data"]
        self.assertEqual((result["printing_mode"], result["backend_id"]), ("cylindrical_full", "CylindricalBackend"))
        self.assertEqual(result["print_time_seconds"], 800)
        self.assertEqual(result["material"], {"length_mm": 2100.0, "weight_g": 6.2})
        self.assertIn("final native full", self.app.scene.gcode_dict[0][-1])
        self.assertFalse(self.adapter._change_lock.locked())
        self.assertTrue(all(c._process.poll() is None for c in self.backend.children.values()))

    def test_fast_basement_and_process_jobs_keep_fifo_membership(self):
        self.backend.fast = True
        self.basement()
        self.assertTrue(self.adapter._slicing.record.cli.parser_started)
        self.backend.parser_data()
        self.backend.finish()
        self.drain()
        self.assertEqual(self.status()["state"], "completed")

    def test_child_done_and_cura_intermediate_never_complete(self):
        self.cura()
        self.backend.estimates(120)
        self.backend.children["CuraEngineBackend"].backendStateChange.emit(base.State.Done)
        self.backend.cura_finished()
        self.drain()
        self.assertEqual(self.status()["state"], "running")
        self.assertFalse(self.adapter._slicing.record.done)
        self.assertIsNone(self.adapter._slicing.record.statistics)

    def test_distinct_job_result_apis_and_errors_every_stage(self):
        for stage in ("prep", "basement", "cli"):
            for failure in ("error", "cancel", "result"):
                with self.subTest(stage=stage, failure=failure):
                    if stage == "prep":
                        self.start(); self.drain()
                        job = self.backend._start_slice_job
                    elif stage == "basement":
                        self.basement()
                        job = self.backend._generate_basement_job
                    else:
                        self.cura(); self.backend.cura_finished(); self.drain()
                        self.backend._glicer_process.code = 0
                        job = self.backend._process_cli_job
                    if failure == "error": job.error = RuntimeError("private")
                    if failure == "cancel": job.cancelled = True
                    if failure == "result": job.finish(result=object())
                    else: job.finish()
                    self.drain()
                    self.assertEqual(self.status()["state"], "failed")
                    self.stop_fake()

    def test_glicer_nonzero_and_wrong_job_process_plate(self):
        for failure in ("exit", "process", "plate"):
            with self.subTest(failure=failure):
                self.cura(); self.backend.cura_finished(); self.drain()
                job = self.backend._process_cli_job
                if failure == "exit": self.backend._glicer_process.code = 7
                if failure == "process": job._process = cli.Process(0)
                if failure == "plate": job._build_plate_number = 1
                self.assertEqual(self.status()["state"], "failed")
                self.stop_fake()

    def test_child_reset_is_once_late_socket_or_process_replacement_fails(self):
        for failure in ("socket", "process", "error"):
            with self.subTest(failure=failure):
                self.cura()
                child = self.backend.children["CuraEngineBackend"]
                if failure == "socket": child._socket = types.SimpleNamespace(error=base.Signal(self.app))
                if failure == "process": child._process = cli.Process()
                if failure == "error": child._socket.error.emit(types.SimpleNamespace(getErrorCode=lambda: 2))
                self.assertEqual(self.status()["state"], "failed")
                self.stop_fake()

    def test_unknown_or_busy_sub_backend_rejected_before_force(self):
        child = self.backend.children["CLIParserBackend"]
        child._slicing = True
        self.assert_error(self.adapter.start_slicing(), "slicing_busy")
        child._slicing = False
        saved = child._socket
        child._socket = None
        self.assert_error(self.adapter.start_slicing(), "backend_state_unavailable")
        child._socket = saved
        self.backend.children["CLIParserBackend"] = self.backend.children["CuraEngineBackend"]
        self.assert_error(self.adapter.start_slicing(), "backend_state_unavailable")
        self.assertEqual(self.backend.calls, 0)

    def test_foreign_pi_and_job_signal_not_success(self):
        self.parser()
        self.backend.backendError.emit(cli.Job(self.app))
        self.assertEqual(self.status()["state"], "running")
        self.backend.parser_data()
        self.backend.children["CLIParserBackend"].printDurationMessage.emit(0, {"foreign": 1}, [1])
        self.assertEqual(self.status()["error"]["code"], "slicing_conflict")

    def test_timeout_live_cura_then_process_exit_releases_guard(self):
        self.cura()
        self.adapter._slicing.record.deadline = -1
        self.assertEqual(self.status()["error"]["code"], "slicing_timeout")
        self.backend._slicing = False
        self.assertTrue(self.adapter._change_lock.locked())
        self.backend.children["CuraEngineBackend"]._process.code = 0
        self.adapter._slicing.timer.fire()
        self.assertFalse(self.adapter._change_lock.locked())

    def test_timeout_late_parser_final_does_not_become_success(self):
        self.parser()
        self.adapter._slicing.record.deadline = -1
        self.assertEqual(self.status()["state"], "failed")
        self.backend._slicing = False
        self.assertTrue(self.adapter._change_lock.locked())
        self.backend.parser_data(); self.backend.finish(); self.drain()
        self.assertEqual(self.status()["state"], "failed")
        self.assertFalse(self.adapter._change_lock.locked())

    def test_shutdown_late_final_readonly_and_bounded_unknown(self):
        self.parser()
        clock = [0]
        self.adapter._slicing.clock = lambda: clock[0]
        self.adapter.close()
        self.backend.finish()
        with mock.patch.object(self.app, "getController", side_effect=AssertionError("No scene after close")), \
                mock.patch.object(self.app, "getPrintInformation", side_effect=AssertionError("No PI after close")):
            self.drain()
        self.assertFalse(self.adapter._change_lock.locked())
        self.assertIsNone(self.adapter._slicing.timer)

    def test_full_result_stale_on_context_and_final_chunks(self):
        self.complete()
        self.app.scene.gcode_dict[0].append("G1 A999\n")
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_result_stale")

    def test_foreign_basement_wrong_prep_plate_and_duplicate_finish_fail(self):
        self.start(); self.drain()
        self.backend._start_slice_job._start_slice_job_build_plate = 1
        self.backend._start_slice_job.finish()
        self.drain()
        self.assertEqual(self.status()["error"]["code"], "slicing_conflict")
        self.stop_fake()
        self.basement()
        self.backend._generate_basement_job = Basement(self.app)
        self.assertEqual(self.status()["error"]["code"], "slicing_conflict")
        # Simulate both the overwritten and the captured fake job actually exiting.
        self.adapter._slicing.record.cli.basement.finish(emit=False)
        self.stop_fake()
        self.cura()
        stage = self.adapter._slicing.record.cli
        stage.basement.finished.emit(stage.basement)
        self.drain()
        self.assertEqual(self.status()["error"]["code"], "slicing_conflict")

    def test_child_engines_exit_after_final_done_keep_valid_result(self):
        # Same native pattern as 5D Spiral: engines may exit/restart after delivering output.
        self.parser()
        self.backend.parser_data()
        self.backend.finish()
        for name in ("CuraEngineBackend", "CLIParserBackend"):
            child = self.backend.children[name]
            child._socket.error.emit(types.SimpleNamespace(getErrorCode=lambda: 2))
            self.backend.reset_child(name)
        self.backend.backendQuit.emit()
        self.drain()
        status = self.status()
        self.assertEqual((status["state"], status["valid"]), ("completed", True), status)
        self.assertTrue(self.adapter.get_slicing_result(self.slice_id)["ok"])
        self.assertFalse(self.adapter._change_lock.locked())

    def test_parser_child_exit_and_old_child_socket_not_final(self):
        old = self.backend.children["CLIParserBackend"]._socket
        self.parser()
        old.error.emit(types.SimpleNamespace(getErrorCode=lambda: 2))
        self.assertEqual(self.status()["state"], "running")
        self.backend.children["CLIParserBackend"].backendStateChange.emit(base.State.Done)
        self.assertFalse(self.adapter._slicing.record.done)
        self.backend.children["CLIParserBackend"]._process.code = 0
        self.assertEqual(self.status()["error"]["code"], "slicing_parser_failed")

    def test_shutdown_unknown_budget_never_releases_live_guard(self):
        self.cura()
        clock = [0]
        self.adapter._slicing.clock = lambda: clock[0]
        record = self.adapter._slicing.record
        self.adapter.close()
        timer = self.adapter._slicing.timer
        clock[0] = 31
        with mock.patch.object(self.app, "getController", side_effect=AssertionError("No scene")), \
                mock.patch.object(self.app, "getPrintInformation", side_effect=AssertionError("No PI")):
            timer.fire()
        self.assertTrue(record.held)
        self.assertTrue(timer.deleted)
        self.assertIn("30", record.error["message"])

    def test_shutdown_observes_new_native_stages_after_pending_prep(self):
        self.start(); self.drain()
        self.adapter.close()
        with mock.patch.object(self.app, "getController", side_effect=AssertionError("No scene after close")), \
                mock.patch.object(self.app, "getPrintInformation", side_effect=AssertionError("No PI after close")):
            self.backend._start_slice_job.finish(); self.drain()
            self.backend._generate_basement_job.finish(); self.drain()
            self.backend.cura_finished(); self.drain()
            self.backend._glicer_process.code = 0
            self.backend._process_cli_job.finish(); self.drain()
            self.backend.finish(); self.drain()
        self.assertFalse(self.adapter._change_lock.locked())
        self.assertIsNone(self.adapter._slicing.record)
        self.assertEqual(self.backend.calls, 1)

    def test_cancelled_cli_reset_idle_child_does_not_hold_after_native_exit(self):
        self.cura(); self.backend.cura_finished(); self.drain()
        self.backend._glicer_process.code = 0
        self.backend._process_cli_job.cancelled = True
        self.backend._process_cli_job.finish()
        self.backend._slicing = False
        self.drain()
        self.assertEqual(self.status()["state"], "failed")
        self.assertFalse(self.adapter._change_lock.locked())


class FullExportTests(unittest.TestCase):
    start, status, drain = FullTests.start, FullTests.status, FullTests.drain
    basement, cura, parser = FullTests.basement, FullTests.cura, FullTests.parser
    launch, wait_status = export_tests.ExportTests.launch, export_tests.ExportTests.wait_status
    clean_workers = export_tests.ExportTests.clean_workers

    def complete(self):
        install_full(self)
        self.backend = self.app.backend
        FullTests.complete(self)

    def setUp(self):
        export_tests.ExportTests.setUp(self)
        self.app.project = cli.WorkspaceWriter(self.app, "project")

    def test_final_native_gcode_and_workspace_full_settings(self):
        self.launch()
        data = self.wait_status()
        self.assertEqual(data["state"], "completed", data)
        with open(os.path.join(data["local_directory"], data["artifact"]["file_name"]), "rb") as stream:
            body = stream.read()
            self.assertIn(b"native basement", body)
            self.assertIn(b"final native full", body)
        self.launch("project")
        data = self.wait_status()
        self.assertEqual(data["state"], "completed", data)
        with zipfile.ZipFile(os.path.join(data["local_directory"], data["artifact"]["file_name"])) as archive:
            settings = archive.read("SteSlicer/global.cfg")
            self.assertIn(b"printing_mode = cylindrical_full", settings)
            self.assertIn(b"layer_height = ", settings)
