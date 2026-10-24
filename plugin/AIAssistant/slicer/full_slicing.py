"""Known CylindricalBackend stages only; native handlers remain untouched."""
from .qt_bridge import SlicerError


class FullStages(object):
    def __init__(self, owner, record, is_busy):
        self.owner, self.record = owner, record
        self.prep = self.basement = self.cli = self.process = None
        self.prep_done = self.basement_done = self.cli_done = False
        self.cura_done = self.parser_started = self.parser_done = False
        self.exitcode = None
        self.old_process = record.backend._glicer_process
        try:
            if type(record.backend).__name__ != "CylindricalBackend":
                raise ValueError("Unknown full backend type")
            from Arcus import ErrorCode
            self.debug_error = ErrorCode.Debug
            children = record.backend.getBackends()
            self.children = dict(children)
            if set(children) != {"CuraEngineBackend", "CLIParserBackend"}:
                raise ValueError("Unexpected child backends")
            for mode, name in (("classic", "CuraEngineBackend"), ("cylindrical", "CLIParserBackend")):
                child = children[name]
                if child is not record.manager.getBackendByType(mode) or child.getPluginId() != name:
                    raise ValueError("Child registration mismatch")
                if is_busy(child):
                    raise SlicerError("slicing_busy", "Подчинённый backend занят; дождитесь выхода его операции")
                # Prep uses both sockets to create messages before the native resets.
                if child._socket is None:
                    raise ValueError("Uninitialized child socket")
            self.cura = self._endpoint(children["CuraEngineBackend"])
            self.parser = self._endpoint(children["CLIParserBackend"])
        except (AttributeError, ImportError, TypeError, ValueError):
            raise SlicerError("backend_state_unavailable", "Не подтверждён состав/инициализация подчинённых backend полного режима")

    @staticmethod
    def _endpoint(backend):
        return {"backend": backend, "old_socket": backend._socket, "old_process": backend._process,
                "socket": None, "process": None, "resetting": False, "active": False}

    def _fail(self, code, message):
        self.owner._fail(self.record, code, message)

    def _refresh(self):
        if self.record is not self.owner.record:
            return
        if self.owner.closed:
            self.owner._closed_cli_refresh(self.record)
        else:
            self.owner._deferred_refresh(self.record)

    def _connect_job(self, job):
        self.owner._connect(self.record, job.finished, self._finished)

    def _finished(self, record, job):
        endpoint = self.cura if job is self.basement else self.parser if job is self.cli else None
        if endpoint is not None:
            if endpoint["active"] or endpoint["resetting"]:
                self._fail("slicing_conflict", "Повторный finished собственной стадии полного режима")
                return
            endpoint["resetting"] = True
            # Observer runs before the native bound handler that replaces the child socket.
            self.owner.app.callLater(self._after_reset, endpoint)
        if self.owns(job):
            self.owner.app.callLater(self._refresh)

    def _after_reset(self, endpoint):
        if self.record is not self.owner.record:
            return
        backend = endpoint["backend"]
        socket, process = backend._socket, backend._process
        endpoint["resetting"] = False
        if socket is None or socket is endpoint["old_socket"]:
            self._fail("slicing_parser_failed", "Штатная стадия не подтвердила новый socket подчинённого backend")
            return
        if process is not None and process is endpoint["old_process"]:
            self._fail("slicing_conflict", "Не подтверждён штатный restart процесса подчинённого backend")
            return
        endpoint["socket"], endpoint["process"], endpoint["active"] = socket, process, True
        def error(record, event):
            if self._settled(endpoint):
                return  # A stage that already delivered its result may drop its connection.
            if socket is backend._socket and event.getErrorCode() != self.debug_error:
                self._fail("slicing_parser_failed", "Ошибка socket подчинённого backend полного режима")
        self.owner._connect(self.record, socket.error, error)
        self._refresh()

    def _settled(self, endpoint):
        # Cura is settled once Glicer started from its output; the parser after the final Done.
        return self.parser_done or endpoint is self.cura and self.cura_done

    def _observe_endpoint(self, endpoint):
        if not endpoint["active"] or endpoint["resetting"] or self._settled(endpoint):
            return
        backend, process = endpoint["backend"], endpoint["process"]
        if backend._socket is not endpoint["socket"]:
            self._fail("slicing_parser_failed", "Поздняя смена socket подчинённого backend")
        if process is None:
            endpoint["process"] = backend._process  # First process may arrive after Listening.
            process = endpoint["process"]
        elif backend._process is not process:
            self._fail("slicing_conflict", "Поздняя смена процесса подчинённого backend")
        if process is not None and process.poll() is not None:
            self._fail("slicing_parser_failed", "Подчинённый процесс завершился до принятия результата")

    def _job_done(self, job, kind):
        if not job.isFinished() or job.isRunning():
            return False
        plate = job._start_slice_job_build_plate if kind == "prep" else job._build_plate_number
        if plate != self.record.plate and not (kind == "basement" and plate is None):
            self._fail("slicing_conflict", "Собственное задание относится к другой платформе")
            return False
        if job.getError() is not None or job.isCancelled():
            self._fail("slicing_failed", "Собственное задание полного режима завершилось с ошибкой/отменой")
            return False
        result = job.getResult()
        if kind == "basement":
            # GenerateBasementJob never sets Job.result; its output is getGCodeList/getLayersData.
            valid = result is None and isinstance(job.getGCodeList(), list) and job.getLayersData() is not None
        else:
            valid = type(result).__name__ == "StartJobResult" and result.name == "Finished"
        if not valid:
            self._fail("slicing_failed", "Нет подтверждённого результата штатной стадии полного режима")
        return valid

    def observe(self):
        r, backend = self.record, self.record.backend
        if backend.getBackends() != self.children:
            self._fail("slicing_conflict", "Изменился состав подчинённых backend")
        if self.prep is None and r.job is not None:
            self.prep = r.job
            self._connect_job(self.prep)
        if self.prep is not None:
            self.prep_done = self._job_done(self.prep, "prep")
        basement = backend._generate_basement_job
        if basement is not None and self.prep_done:
            if self.basement is None:
                self.basement = basement
                self._connect_job(basement)
            elif basement is not self.basement:
                self._fail("slicing_conflict", "Сменилось собственное задание основания")
        if self.basement is not None:
            self.basement_done = self._job_done(self.basement, "basement")
        self._observe_endpoint(self.cura)
        self._observe_endpoint(self.parser)
        process = backend._glicer_process
        if process is not None and process is not self.old_process:
            if self.process is None and self.basement_done and self.cura["active"]:
                self.process, self.cura_done = process, True
            elif self.process is not None and process is not self.process:
                self._fail("slicing_conflict", "Сменился собственный Glicer полного режима")
        job = backend._process_cli_job
        if job is not None and self.process is not None:
            if job._process is not self.process or job._build_plate_number != r.plate:
                self._fail("slicing_conflict", "Не подтверждена принадлежность ProcessCliJob полного режима")
            if self.cli is None:
                self.cli = job
                self._connect_job(job)
            elif job is not self.cli:
                self._fail("slicing_conflict", "Сменилось собственное задание обработки CLI")
        if self.process is not None:
            self.exitcode = self.process.poll()
            if backend._glicer_process is not self.process:
                self._fail("slicing_conflict", "Потерян собственный Glicer полного режима")
            if self.exitcode is not None and self.exitcode != 0:
                self._fail("slicing_process_failed", "Glicer полного режима завершился с ненулевым кодом")
        if self.cli is not None:
            self.cli_done = self._job_done(self.cli, "cli")
        if (self.cli_done and self.exitcode == 0 and self.parser["active"] and
                backend._process_cli_job is None):
            self.parser_started = True
        if not self.owner.closed and self.basement_done:
            self.owner._follow_empty_buffer(r)

    def processing(self):
        self.observe()
        # Cura Processing0.5 precedes construction of Glicer/ProcessCliJob in the same callback.
        self.owner.app.callLater(self._refresh)

    def final(self):
        if self.parser_started:
            self.parser_done = True

    def closed_event(self, handler, args):
        if handler == self._finished and args:
            self._finished(self.record, args[0])
        elif handler == self.owner._state and args:
            if args[0] == self.record.states.Done:
                self.final()
            elif args[0] == self.record.states.Processing:
                self.processing()

    @staticmethod
    def _live(endpoint):
        process = endpoint["process"]
        return endpoint["active"] and process is not None and process.poll() is None

    def busy(self):
        return (any(job is not None and (job.isRunning() or not job.isFinished()) for job in (self.prep, self.basement, self.cli)) or
                self.process is not None and self.process.poll() is None or
                self.cura["resetting"] or self.parser["resetting"] or
                self.cura["active"] and not self.cura_done and (self.cura["process"] is None or self._live(self.cura)) or
                self.parser_started and not self.parser_done and (self.parser["process"] is None or self._live(self.parser)))

    def ready(self):
        # Child engines may exit/restart after delivering their stage; liveness is not required.
        return (self.prep_done and self.basement_done and self.cura_done and self.cli_done and self.exitcode == 0 and
                self.parser_started and self.parser_done)

    def owns(self, job):
        return job is self.prep or job is self.basement or job is self.cli
