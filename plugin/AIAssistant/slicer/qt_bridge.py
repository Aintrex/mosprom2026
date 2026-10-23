import threading
import time


class SlicerError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class _Call(object):
    def __init__(self):
        self.event = threading.Event()
        self.state = "pending"
        self.value = None
        self.exception = None


class QtBridge(object):
    """Only the HTTP worker waits. State transitions share one short lock."""

    def __init__(self, app, timeout=5.0):
        self._app = app
        self._main_thread = app.getMainThread()
        if threading.current_thread() is not self._main_thread:
            raise RuntimeError("Create QtBridge on the application main thread")
        self._timeout = timeout
        self._lock = threading.Lock()
        self._closed = False
        self._pending = set()

    @staticmethod
    def _disconnected():
        return SlicerError("slicer_not_connected", "Плагин STE Slicer завершает работу; новые операции недоступны")

    def close(self):
        with self._lock:
            self._closed = True
            for call in self._pending:
                if call.state == "pending":
                    call.state = "cancelled"
                    call.exception = self._disconnected()
                    call.event.set()
            self._pending.clear()

    def is_main_thread(self):
        return threading.current_thread() is self._main_thread

    def ensure_open(self):
        with self._lock:
            if self._closed:
                raise self._disconnected()

    def run(self, operation, timeout=None):
        deadline = time.monotonic() + (self._timeout if timeout is None else max(0, timeout))
        call = _Call()
        with self._lock:
            if self._closed:
                raise self._disconnected()
            direct = self.is_main_thread()
            if not direct:
                self._pending.add(call)
        if direct:
            return operation(self._app)

        def invoke():
            with self._lock:
                if call.state != "pending":
                    return
                if time.monotonic() >= deadline:
                    call.state = "cancelled"
                    call.exception = SlicerError("qt_timeout", "Qt не начал операцию вовремя; отложенный вызов отменён.")
                    self._pending.discard(call)
                    call.event.set()
                    return
                call.state = "running"
            try:
                call.value = operation(self._app)
            except Exception as exc:
                call.exception = exc
            finally:
                with self._lock:
                    call.state = "done"
                    self._pending.discard(call)
                    call.event.set()

        try:
            self._app.callLater(invoke)
        except Exception:
            with self._lock:
                call.state = "cancelled"
                self._pending.discard(call)
            raise
        call.event.wait(max(0, deadline - time.monotonic()))
        with self._lock:
            if call.state == "pending":
                call.state = "cancelled"
                self._pending.discard(call)
                raise SlicerError("qt_timeout", "Qt не начал операцию вовремя; отложенный вызов отменён. Повторите запрос после восстановления отклика STE Slicer.")
            if call.state == "running":
                raise SlicerError("qt_timeout", "Qt начал операцию, но результат пока неизвестен. Перечитайте настройку; не повторяйте запись автоматически.")
        if call.exception is not None:
            raise call.exception
        return call.value
