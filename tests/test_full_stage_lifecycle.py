"""Full 5D process ownership, including unattributed/shared UM signals; no engines."""
import types
import unittest
from unittest import mock

import test_full_slicing as full
import test_slicer_slicing as base
import test_cli_slicing as cli


class FullLifecycleTests(unittest.TestCase):
    setUp, stop_fake = full.FullTests.setUp, full.FullTests.stop_fake
    start, status, drain = full.FullTests.start, full.FullTests.status, full.FullTests.drain
    basement, cura, parser = full.FullTests.basement, full.FullTests.cura, full.FullTests.parser
    assert_error = full.FullTests.assert_error

    def test_unattributed_quit_at_half_then_actual_parser_progress_and_final(self):
        self.cura()
        self.backend.cura_finished()
        self.drain()
        self.backend.backendQuit.emit()
        self.drain()
        status = self.status()
        self.assertEqual((status['state'], status['progress']), ('running', 0.5))
        self.assertTrue(self.adapter._change_lock.locked())
        self.assertEqual(status['diagnostics']['event'], 'quit_source_unknown')
        self.assertEqual(status['diagnostics']['process'], 'unknown')
        self.backend._glicer_process.code = 0
        self.backend._process_cli_job.finish()
        self.drain()
        self.backend.processingProgress.emit(0.835966)
        self.assertEqual((self.status()['state'], self.status()['progress']), ('running', 0.835966))
        self.backend.parser_data()
        self.backend.finish()
        self.drain()
        self.assertEqual((self.status()['state'], self.status()['valid']), ('completed', True))

    def test_late_old_process_quit_during_replacement_and_late_listening(self):
        self.basement()
        old = self.backend.children['CuraEngineBackend']._process
        self.backend._generate_basement_job.finish()
        # Listening/startEngine happens later than the job's bound handler/socket reset.
        child = self.backend.children['CuraEngineBackend']
        child._process = None
        self.backend.backendQuit.emit()
        self.drain()
        self.assertEqual(self.status()['state'], 'running')
        child._process = cli.Process()
        self.adapter._slicing.timer.fire()
        self.assertIs(self.adapter._slicing.record.cli.cura['process'], child._process)
        self.assertEqual(old.poll(), 0)
        self.backend.backendQuit.emit()
        self.drain()
        self.assertEqual(self.status()['state'], 'running')

    def test_current_child_exit_nonzero_and_zero_without_final_fail_after_callback(self):
        for code in (7, 0):
            with self.subTest(code=code):
                self.cura()
                self.backend.children['CuraEngineBackend']._process.code = code
                self.backend.backendQuit.emit()
                self.drain()
                status = self.status()
                self.assertEqual(status['state'], 'failed')
                self.assertEqual(status['error']['code'], 'slicing_parser_failed')
                self.assertEqual(status['diagnostics']['process'], 'cura_engine')
                self.assertEqual(status['diagnostics']['exit_code'], code)
                self.assert_error(self.adapter.get_slicing_result(self.slice_id), 'slice_not_ready')
                self.stop_fake()

    def test_process_exit_before_queued_cura_result_is_rechecked_after_delivery(self):
        self.cura()
        self.backend.children['CuraEngineBackend']._process.code = 0
        # EOF/poll can precede the already queued SlicingFinished callback.
        self.app.callLater(self.backend.cura_finished)
        self.backend.backendQuit.emit()
        self.drain()
        self.assertEqual(self.status()['state'], 'running')
        self.assertTrue(self.adapter._slicing.record.cli.cura_done)

    def test_nonzero_settled_child_is_not_hidden_by_glicer_transition(self):
        self.cura()
        self.backend.children['CuraEngineBackend']._process.code = 9
        self.backend.cura_finished()
        self.backend.backendQuit.emit()
        self.drain()
        self.assertEqual(self.status()['state'], 'failed')
        self.assertEqual(self.status()['diagnostics']['exit_code'], 9)

    def test_current_glicer_crash_guard_and_late_events_never_succeed(self):
        self.cura()
        self.backend.cura_finished()
        self.drain()
        self.backend._glicer_process.code = 4
        self.backend.backendQuit.emit()
        self.drain()
        status = self.status()
        self.assertEqual(status['error']['code'], 'slicing_process_failed')
        self.assertEqual(status['diagnostics']['process'], 'glicer')
        self.assertEqual(status['diagnostics']['exit_code'], 4)
        self.assertTrue(self.adapter._change_lock.locked())  # CLI job still owns work.
        self.backend.processingProgress.emit(0.835966)
        self.backend.backendStateChange.emit(base.State.Done)
        self.drain()
        self.assertEqual(self.status()['state'], 'failed')
        self.assert_error(self.adapter.start_slicing(), 'slicing_busy')
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), 'slice_not_ready')

    def test_shared_done_is_not_outer_final_and_shared_progress_is_only_a_hint(self):
        # QObject-first MRO may leave inherited UM.Signal objects shared. Do not
        # assume that the Signal emitted by a child has an isolated receiver list.
        for child in self.backend.children.values():
            child.backendQuit = self.backend.backendQuit
            child.backendStateChange = self.backend.backendStateChange
            child.processingProgress = self.backend.processingProgress
        self.parser()
        child = self.backend.children['CLIParserBackend']
        self.backend.parser_data()
        child.backendStateChange.emit(base.State.Done)
        child.processingProgress.emit(1)
        self.drain()
        self.assertEqual(self.status()['state'], 'running')
        self.assertFalse(self.adapter._slicing.record.done)
        child.backendQuit.emit()
        self.drain()
        self.assertEqual(self.status()['state'], 'running')
        self.backend.finish()
        self.drain()
        self.assertEqual(self.status()['state'], 'completed')

    def test_final_delivery_wins_over_deferred_parser_exit_but_not_prior_failure(self):
        self.parser()
        self.backend.parser_data()
        self.backend.children['CLIParserBackend']._process.code = 0
        self.app.callLater(self.backend.finish)
        self.backend.backendQuit.emit()
        self.drain()
        self.assertEqual(self.status()['state'], 'completed')

    def test_current_nonzero_parser_is_not_hidden_by_final_done(self):
        self.parser()
        self.backend.parser_data()
        self.backend.children['CLIParserBackend']._process.code = 3
        self.backend.finish()
        self.drain()
        self.assertEqual(self.status()['state'], 'failed')
        self.assertEqual(self.status()['diagnostics']['process'], 'cli_parser')
        self.assertEqual(self.status()['diagnostics']['exit_code'], 3)

    def test_unobserved_parser_process_cannot_complete_from_signals(self):
        self.parser()
        self.adapter._slicing.record.cli.parser['process'] = None
        self.backend.children['CLIParserBackend']._process = None
        self.backend.parser_data()
        self.backend.finish()
        self.drain()
        self.assertEqual(self.status()['state'], 'failed')
        self.assertEqual(self.status()['diagnostics']['event'], 'process_not_observed')
        self.assertTrue(self.adapter._change_lock.locked())  # Unknown is not a confirmed exit.

    def test_live_unknown_eof_needs_final_or_timeout_not_assumed_success(self):
        self.parser()
        self.backend.backendQuit.emit()
        self.backend.processingProgress.emit(1)
        self.drain()
        self.assertEqual(self.status()['state'], 'running')
        self.adapter._slicing.record.deadline = -1
        self.assertEqual(self.status()['error']['code'], 'slicing_timeout')
        self.assertTrue(self.adapter._change_lock.locked())
        self.backend.parser_data()
        self.backend.finish()
        self.drain()
        self.assertEqual(self.status()['state'], 'failed')

    def test_um_function_observers_precede_bound_native_handlers(self):
        class OrderedSignal(base.Signal):
            def emit(self, *args):
                self.app.check_thread()
                slots = list(self.slots)
                for bound in (False, True):
                    for slot in slots:
                        if isinstance(slot, types.MethodType) == bound:
                            slot(*args)
        original = cli.Job.__init__
        def init(job, app, *args):
            original(job, app, *args)
            job.finished = OrderedSignal(app)
        with mock.patch.object(cli.Job, '__init__', init):
            self.parser()
        self.backend.parser_data()
        self.backend.finish()
        self.drain()
        self.assertEqual(self.status()['state'], 'completed')

    def test_safe_stage_diagnostics_survive_agent_observer_and_events(self):
        from AIAssistant.agent.service import AgentService
        from test_agent_integration import CONFIG, ScriptedLLM, calls, tool
        llm = ScriptedLLM()
        llm.answers.extend([calls(tool('start_slicing')), {'content': 'Запуск принят'}])
        agent = AgentService(self.adapter, lambda: dict(CONFIG), llm, run_async=False)
        self.addCleanup(agent.close)
        agent._start_observer = mock.Mock()
        sid = agent.create_session()['session_id']
        pump = base.SlicingTests.pump_until_done.__get__(self)
        worker = base.SlicingTests.worker.__get__(self)
        # Reuse the real worker/Qt queue helpers, not a fabricated service snapshot.
        self.finish = base.SlicingTests.finish.__get__(self)
        pump(worker(lambda: agent.submit_message(sid, {'text': 'Нарежь', 'client_message_id': 'full-stages'})))
        self.slice_id = agent.get_session(sid)['slice']['slice_id']
        self.drain()
        self.backend._start_slice_job.finish(); self.drain()
        self.backend._generate_basement_job.finish(); self.drain()
        self.backend.cura_finished(); self.drain()
        self.backend.backendQuit.emit(); self.drain()
        operation = agent._operations['slice']
        pump(worker(lambda: agent._observe_operation(operation)))
        snapshot = agent.get_session(sid)
        self.assertEqual(snapshot['slice']['stage'], 'glicer')
        self.assertEqual(snapshot['slice']['diagnostics']['event'], 'quit_source_unknown')
        event = [e for e in agent.get_events(sid)['events'] if e['type'] == 'slice_progress'][-1]
        self.assertEqual(event['data']['stage'], 'glicer')
        self.assertEqual(event['data']['diagnostics']['exit_code'], None)
        self.backend._glicer_process.code = 0
        self.backend._process_cli_job.finish(); self.drain()
        self.backend.parser_data(); self.backend.finish(); self.drain()
        operation['next_poll'] = -1
        pump(worker(lambda: agent._observe_operation(operation)))
        snapshot = agent.get_session(sid)
        self.assertEqual(snapshot['slice']['state'], 'completed')
        self.assertEqual(snapshot['slice']['stage'], 'completed')
        self.assertEqual(snapshot['slice']['diagnostics']['event'], 'final_message')
        self.assertEqual(len(llm.requests), 2)


if __name__ == '__main__':
    unittest.main()
