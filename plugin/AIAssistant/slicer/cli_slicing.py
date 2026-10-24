"""Read-only stage correlation for this fork's CLIParserBackend, on Qt only."""
from .qt_bridge import SlicerError


class CliStages(object):
    def __init__(self, owner, record):
        self.owner, self.record = owner, record
        self.old_process = record.backend._glicer_process
        self.prep, self.cli, self.process = None, None, None
        self.prep_done, self.cli_done, self.parser_started = False, False, False
        # Set by the native final Done of this parser. After it CliParserBackend lets the
        # parser exit and recreates its socket/process (_onSocketError -> _terminate ->
        # _createSocket); gcode_dict is kept, so those events are not a failure.
        self.finished = False
        self.exitcode = None
        try:
            self.parser, self.socket = record.backend._process, record.backend._socket
            self.startup = self.parser is None
            self.startup_socket_adopted = False
            from Arcus import ErrorCode
            self.debug_error = ErrorCode.Debug
            if self.socket is not None and not self.startup:
                self.owner._connect(record, self.socket.error, self._socket_error)
        except (AttributeError, ImportError):
            raise SlicerError("backend_state_unavailable", "Не подтверждён layout процесса/сокета CLIParser")

    def _socket_error(self, record, error):
        if error.getErrorCode() != self.debug_error and not self.finished:
            self._fail("slicing_parser_failed", "Ошибка соединения CLIParser; результат не подтверждён")

    def _fail(self, code, message):
        self.owner._fail(self.record, code, message)

    def _connect_job(self, job):
        self.owner._connect(self.record, job.finished, self._finished)

    def _finished(self, record, job):
        if job is self.prep or job is self.cli:
            # Functions run before native bound handlers; observe new refs after them.
            self.owner.app.callLater(self.owner._deferred_refresh, record)

    def _check_job(self, job):
        if not job.isFinished() or job.isRunning():
            return False
        if job._build_plate_number != self.record.plate:
            self._fail("slicing_conflict", "Стадия CLI относится к другой платформе")
            return False
        if job.getError() is not None or job.isCancelled():
            self._fail("slicing_failed", "Собственное задание CLI завершилось с ошибкой/отменой; проверьте сообщения STE Slicer")
            return False
        result = job.getResult()
        if type(result).__name__ != "StartJobResult" or result.name != "Finished":
            self._fail("slicing_failed", "Native CLI-задание не подтвердило Finished")
            return False
        return True

    def observe(self):
        r, backend = self.record, self.record.backend
        parser, socket = backend._process, backend._socket
        if self.finished:
            return  # Post-finish parser restart is native; the result buffer is checked by the owner.
        if self.parser is None:
            self.parser = parser
        elif parser is not self.parser:
            self._fail("slicing_conflict", "Сменился процесс CLIParser текущего запуска")
        if (socket is not self.socket and socket is not None and self.startup and
                not r.force_returned and not self.startup_socket_adopted):
            # slice() creates/replaces its socket synchronously when parser _process is None.
            self.startup_socket_adopted = True
            self.socket = socket
            self.owner._connect(r, socket.error, self._socket_error)
        elif socket is not self.socket:
            self._fail("slicing_parser_failed", "Сменилось соединение CLIParser текущего запуска")
        if self.parser is not None and self.parser.poll() is not None:
            self._fail("slicing_parser_failed", "CLIParser завершился; финальный результат не подтверждён")
        if self.prep is None and r.job is not None:
            self.prep = r.job
            self._connect_job(self.prep)
        current = backend._start_slice_job
        if current is not None and self.prep is not None and current is not self.prep:
            self._fail("slicing_conflict", "Сменилось задание подготовки CLI")
        if self.prep is not None:
            self.prep_done = self._check_job(self.prep)
        process = backend._glicer_process
        if process is not None and process is not self.old_process:
            if self.process is not None and self.process is not process:
                self._fail("slicing_conflict", "Сменился собственный процесс Glicer")
            elif self.process is None and self.prep_done:
                self.process = process
        current = backend._process_cli_job
        if current is not None:
            if self.cli is current and (current._process is not self.process or current._build_plate_number != r.plate):
                self._fail("slicing_conflict", "Изменилась принадлежность собственного ProcessCliJob")
            if self.cli is not None and current is not self.cli:
                self._fail("slicing_conflict", "Сменилось задание обработки CLI")
            elif self.cli is None and self.prep_done and self.process is not None:
                if current._process is not self.process or current._build_plate_number != r.plate:
                    self._fail("slicing_conflict", "Не подтверждена принадлежность ProcessCliJob текущему Glicer/платформе")
                else:
                    self.cli = current
                    self._connect_job(current)
        if self.process is not None:
            self.exitcode = self.process.poll()  # Nonblocking; never wait/terminate from plugin.
            if self.exitcode is not None and self.exitcode != 0:
                self._fail("slicing_process_failed", "Glicer завершился с ненулевым кодом; результат не принимается, проверьте журнал STE")
        if self.cli is not None:
            self.cli_done = self._check_job(self.cli)
        if backend._process_cli_job is None and self.cli_done and self.process is not backend._glicer_process:
            # A missing/replaced process after observing it is not a successful exit.
            self._fail("slicing_conflict", "Потерян захваченный процесс Glicer до подтверждения результата")

    def processing(self):
        self.observe()
        # Native _onProcessCliFinished clears its ref, sends parser request, then Processing.
        if (self.prep_done and self.cli_done and self.exitcode == 0 and
                self.record.backend._process_cli_job is None):
            self.parser_started = True

    def ready(self):
        return self.prep_done and self.cli_done and self.exitcode == 0 and self.parser_started and self.finished

    def final(self):
        if self.parser_started:
            self.finished = True

    def closed_event(self, handler, args):
        pass

    def busy(self):
        # Native fields may be cleared before a worker has really returned.
        return (any(job is not None and (job.isRunning() or not job.isFinished()) for job in (self.prep, self.cli)) or
                self.process is not None and self.process.poll() is None)

    def owns(self, job):
        return job is self.prep or job is self.cli
