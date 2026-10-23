"""Один classic-запуск и последний результат; все методы исполняются на Qt."""
import copy
import logging
import math
import threading
import time

from .models import model_roots, walk
from .qt_bridge import SlicerError


LOGGER = logging.getLogger(__name__)
SLICE_FIELDS = {"start_slicing": (), "get_slicing_status": ("slice_id",), "get_slicing_result": ("slice_id",)}


def validate_slice_arguments(name, arguments):
    fields = SLICE_FIELDS[name]
    if not isinstance(arguments, dict) or set(arguments) != set(fields):
        raise ValueError("Укажите только поля: %s" % (", ".join(fields) or "{}"))
    if fields and (not isinstance(arguments["slice_id"], str) or not arguments["slice_id"]):
        raise ValueError("slice_id должен быть непустой строкой")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def backend_busy(backend):
    # В этом форке публичного getter нет. Поля только читаются, не изменяются.
    return bool(backend._slicing or backend._start_slice_job is not None or
                backend._generate_basement_job is not None or backend._process_layers_job is not None)


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((str(k), _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return (type(value).__name__, str(value))


def _stack_signature(stack):
    values = []
    bottom = stack.getBottom()
    for container in stack.getContainers():
        # Definition неизменяем по этому API; не вычисляем тысячи derived settings.
        raw = () if container is bottom else tuple(sorted(
            (key, _freeze(container.getProperty(key, "value"))) for key in container.getAllKeys()))
        metadata = tuple((key, _freeze(container.getMetaDataEntry(key))) for key in
                         ("quality_type", "base_file", "properties", "compatible", "definition", "position"))
        values.append((container.getId(), raw, metadata))
    return tuple(values)


def _context(app):
    stack = app.getGlobalContainerStack()
    manager = app.getBackendManager()
    scene = app.getController().getScene()
    root = scene.getRoot()
    plate = app.getMultiBuildPlateModel().activeBuildPlate
    nodes = list(model_roots(root))
    stacks = [stack] + list(stack.extruders.values()) if stack is not None else []
    geometry = []
    for top in nodes:
        for node in walk(top):
            if not (node.callDecoration("isSliceable") or node.callDecoration("isGroup")):
                continue
            mesh = node.getMeshData()
            matrix = node.getWorldTransformation().getData()
            transform = tuple(float(matrix[i][j]) for i in range(4) for j in range(4))
            geometry.append((id(node), id(mesh), mesh.getVertexCount() if mesh is not None else 0,
                             transform, node.callDecoration("getBuildPlateNumber")))
            object_stack = node.callDecoration("getStack")
            if object_stack is not None and all(object_stack is not s for s in stacks):
                stacks.append(object_stack)
    snapshot = (id(stack), id(manager), id(manager.getCurrentBackend()) if manager is not None else None,
                id(scene), id(root), plate, stack.getProperty("printing_mode", "value") if stack is not None else None,
                tuple(geometry), tuple((id(s), _stack_signature(s)) for s in stacks))
    return snapshot, stacks


def _total(values, factor=1.0):
    if not isinstance(values, (list, tuple)) or not values or not all(_number(v) for v in values):
        return None
    value = sum(float(v) for v in values) * factor
    return value if math.isfinite(value) else None


class _Run(object):
    def __init__(self, slice_id, deadline):
        self.slice_id, self.deadline = slice_id, deadline
        self.state, self.progress, self.valid, self.error = "queued", None, False, None
        self.subscriptions = []
        self.held = True
        self.begun, self.done, self.started_count = False, False, 0
        self.final_check_ready = False
        self.force_returned = False
        self.buffer = None
        self.job = None
        self.job_pending = False
        self.pi_revisions = [0, 0, 0]
        self.estimate_epoch = 0
        self.statistics = None
        self.result = None


class Slicing(object):
    def __init__(self, change_lock, budget=180.0):
        self.lock, self.budget = change_lock, budget
        self.clock = time.monotonic
        self.record, self.last_completed = None, None
        self.sequence = 0
        self.timer = None
        self.closed = False
        self.app = None
        self.main_thread = threading.current_thread()

    def _current(self, record):
        return not self.closed and record is self.record and record.backend is not None

    def _connect(self, record, signal, handler):
        def callback(*args):
            if not self._current(record):
                return
            if threading.current_thread() is not self.main_thread:
                self.app.callLater(self._deliver, record, handler, args)
            else:
                self._deliver(record, handler, args)
        signal.connect(callback)
        record.subscriptions.append((signal, callback))

    def _deliver(self, record, handler, args):
        if not self._current(record):
            return
        try:
            handler(record, *args)
        except Exception as exc:
            LOGGER.error("Slicing callback: %s", type(exc).__name__)
            self._fail(record, "slicing_failed", "Ошибка API при наблюдении нарезки; проверьте журнал STE Slicer")

    def _release(self, record):
        if record.held:
            record.held = False
            self.lock.release()

    def _detach(self, record):
        for signal, callback in record.subscriptions:
            try:
                signal.disconnect(callback)
            except (TypeError, RuntimeError) as exc:
                LOGGER.warning("Slicing disconnect: %s", type(exc).__name__)
        record.subscriptions = []
        record.backend = record.stack = record.scene = record.root = record.info = None
        record.manager = record.job = record.buffer = record.old_buffer = record.snapshot = None

    def _fail(self, record, code, message):
        if record.state == "completed":
            record.valid = False
            return
        if record.state != "failed":
            record.state, record.valid = "failed", False
            record.error = {"code": code, "message": message + " Пока backend занят, изменения заблокированы. Дождитесь выхода/нажмите Stop в UI; при отсутствии завершения перезапустите приложение."}

    def invalidate(self):
        if self.record is not None:
            if self.record.state == "completed":
                self.record.valid = False
            elif self.record.state in ("queued", "running"):
                self._fail(self.record, "slicing_context_changed", "Изменились параметры или печатная геометрия")

    def start(self, app):
        if self.closed:
            raise SlicerError("slicer_not_connected", "Наблюдение нарезки закрыто")
        if not self.lock.acquire(False):
            raise SlicerError("slicing_busy", "Выполняется импорт, смена настроек или другая нарезка")
        transferred = False
        try:
            stack = app.getGlobalContainerStack()
            if stack is None:
                raise SlicerError("no_active_machine", "В STE Slicer не выбрана активная машина")
            manager = app.getBackendManager()
            backend = manager.getCurrentBackend() if manager is not None else None
            if (stack.getProperty("printing_mode", "value") != "classic" or backend is None or
                    backend is not manager.getBackendByType("classic")):
                raise SlicerError("unsupported_slicing_mode", "Для ТЗ 05 требуется classic и соответствующий runtime-backend")
            if app.getPreferences().getValue("general/auto_slice"):
                raise SlicerError("auto_slicing_enabled", "Выключите автоматическую нарезку в UI STE Slicer; плагин не меняет Preferences")
            if backend_busy(backend):
                raise SlicerError("slicing_busy", "Backend или его подготовка/обработка слоёв уже заняты")
            scene = app.getController().getScene()
            root = scene.getRoot()
            all_nodes = list(walk(root))
            if any(n.callDecoration("isBlockSlicing") for n in all_nodes):
                raise SlicerError("slicing_preview_active", "Импортированный G-code preview не является моделью для новой нарезки")
            plates = {n.callDecoration("getBuildPlateNumber") for n in all_nodes
                      if n.callDecoration("isSliceable") and n.getMeshData() is not None and n.getMeshData().getVertexCount() > 0}
            plate = app.getMultiBuildPlateModel().activeBuildPlate
            if not plates:
                raise SlicerError("empty_scene", "На активной платформе нет печатных моделей")
            if plates != {plate}:
                raise SlicerError("multiple_build_plates", "ТЗ 05 поддерживает только одну непустую активную платформу")
            info = app.getPrintInformation()
            if info is None:
                raise SlicerError("slicing_failed", "PrintInformation STE Slicer недоступна")
            snapshot, stacks = _context(app)
            from UM.Backend.Backend import BackendState
            from PyQt5.QtCore import QTimer
            self._stop_timer()
            if self.record is not None:
                self.record.valid = False
                self._detach(self.record)
            self.sequence += 1
            record = _Run("slice-%d" % self.sequence, self.clock() + self.budget)
            record.backend, record.manager, record.stack = backend, manager, stack
            record.scene, record.root, record.info = scene, root, info
            record.plate, record.backend_id = plate, backend.getPluginId()
            record.snapshot = snapshot
            record.states = BackendState
            record.old_buffer = getattr(scene, "gcode_dict", {}).get(plate)
            self.app, self.record = app, record
            transferred = True
            try:
                self._connect(record, backend.slicingStarted, self._started)
                self._connect(record, backend.slicingCancelled, self._cancelled)
                self._connect(record, backend.backendStateChange, self._state)
                self._connect(record, backend.processingProgress, self._progress)
                self._connect(record, backend.backendError, self._backend_error)
                self._connect(record, backend.backendQuit, self._quit)
                self._connect(record, backend.printDurationMessage, self._estimates)
                # Manager пересылает оценки всех backend в общую PrintInformation.
                # Чужая оценка той же платформы делает источник неоднозначным.
                for other in manager.getBackends().values():
                    if other is not backend:
                        self._connect(record, other.printDurationMessage, self._foreign_estimates)
                for index, signal in enumerate((info.currentPrintTimeChanged, info.materialLengthsChanged, info.materialWeightsChanged)):
                    self._connect(record, signal, lambda r, i=index: self._pi_changed(r, i))
                self._connect(record, scene.sceneChanged, self._changed)
                self._connect(record, app.globalContainerStackChanged, self._changed)
                self._connect(record, manager.currendBackendChanged, self._changed)
                for s in stacks:
                    self._connect(record, s.propertyChanged, self._property_changed)
                    self._connect(record, s.containersChanged, self._changed)
                self._connect(record, app.getMultiBuildPlateModel().activeBuildPlateChanged, self._changed)
                self.timer = QTimer()
                self.timer.setInterval(250)
                self.timer.timeout.connect(self._tick)
                self.timer.start()
                backend.forceSlice()
                record.force_returned = True
                record.job = backend._start_slice_job
                self._capture_start(record)
                app.callLater(self._deferred_refresh, record)
            except Exception as exc:
                LOGGER.error("Slicing start: %s", type(exc).__name__)
                self._fail(record, "slicing_failed", "Ошибка API запуска; неизвестно, успел ли backend начать работу")
                if not backend_busy(backend):
                    self._release(record)
                    self._detach(record)
                    self._stop_timer()
                raise SlicerError("slicing_failed", "Ошибка API запуска STE Slicer; проверьте журнал. slice_id: %s. Не повторяйте запуск автоматически." % record.slice_id)
            if record.state == "failed":
                raise SlicerError(record.error["code"], record.error["message"] + " slice_id: " + record.slice_id)
            return self._status_data(record)
        finally:
            if not transferred:
                self.lock.release()

    def _capture_start(self, record):
        if record.begun and record.buffer is None:
            buffer = getattr(record.scene, "gcode_dict", {}).get(record.plate)
            if isinstance(buffer, list) and buffer is not record.old_buffer and not buffer:
                record.buffer = buffer

    def _follow_empty_buffer(self, record):
        # Fork заменяет пустой list при завершении GenerateBasementJob и первом
        # GCodeLayer. Принимаем это только на собственных runtime-сигналах,
        # пока ранее захваченный буфер пуст; непустой результат не подменяем.
        if (record.buffer is None or record.buffer or record.started_count != 1 or
                record.backend._start_slice_job_build_plate != record.plate):
            return
        job = record.backend._start_slice_job
        if job is not None and job is not record.job:
            return
        buffer = getattr(record.scene, "gcode_dict", {}).get(record.plate)
        if isinstance(buffer, list) and buffer is not record.old_buffer:
            record.buffer = buffer

    def _started(self, record):
        if record.state == "completed":
            record.valid = False
            return
        record.started_count += 1
        if record.started_count > 1:
            self._fail(record, "slicing_conflict", "Обнаружен повторный/ручной запуск backend")
            return
        record.begun = True
        if record.state != "failed":
            record.state = "running"
        self._capture_start(record)
        record.job_pending = True
        self.app.callLater(self._deliver, record, self._capture_job, ())

    def _capture_job(self, record):
        if not self._current(record):
            return
        record.job_pending = False
        if record.job is None and record.started_count == 1:
            record.job = record.backend._start_slice_job
        self._tick()

    def _state(self, record, state):
        if state == record.states.Processing:
            record.begun = True
            if record.state == "queued":
                record.state = "running"
            self._follow_empty_buffer(record)
        elif state == record.states.Done and record.begun and record.state != "failed":
            record.done = True
            self.app.callLater(self._deferred_refresh, record)
        elif state in (record.states.Error, record.states.Disabled):
            self._fail(record, "slicing_failed", "Backend сообщил ошибку или недоступность; проверьте сообщения STE Slicer")
        elif state == record.states.NotStarted and record.begun and not record.done:
            self._fail(record, "slicing_failed", "Backend остановился до подтверждённого завершения")

    def _progress(self, record, value):
        if record.begun and _number(value) and value <= 1:
            record.progress = float(value)

    def _cancelled(self, record, *args):
        self._fail(record, "slicing_cancelled", "Нарезка отменена backend или пользователем")

    def _backend_error(self, record, job=None):
        if job is not None and record.job is not None and job is not record.job:
            return
        self._fail(record, "slicing_failed", "Ошибка подготовки/нарезки STE Slicer; подробности в сообщениях приложения и журнале")

    def _quit(self, record, *args):
        self._fail(record, "slicing_failed", "Процесс backend завершился до получения результата")

    def _pi_changed(self, record, index):
        if record.begun:
            record.pi_revisions[index] += 1

    def _foreign_estimates(self, record, plate, *args):
        if plate == record.plate:
            self._fail(record, "slicing_conflict", "Другой backend обновил оценки той же платформы")

    def _estimates(self, record, plate, times, amounts):
        if (not record.begun or plate != record.plate or not isinstance(times, dict) or not times or
                record.backend._start_slice_job_build_plate != record.plate):
            return
        record.estimate_epoch += 1
        epoch = record.estimate_epoch
        self._follow_empty_buffer(record)
        # UM.Signal вызывает функции перед bound methods: наш observer идёт до
        # BackendManager -> PrintInformation. Требуем обновления всех трёх полей.
        revisions = tuple(record.pi_revisions)
        record.statistics = None
        record.estimate_candidate = None
        known_time = all(_number(v) for v in times.values())
        known_material = isinstance(amounts, (list, tuple)) and bool(amounts) and all(_number(v) for v in amounts)
        self.app.callLater(self._capture_estimates, record, epoch, known_time, known_material, revisions)

    def _capture_estimates(self, record, epoch, known_time, known_material, revisions):
        if not self._current(record) or epoch != record.estimate_epoch:
            return
        record.estimate_candidate = (known_time, known_material, revisions)
        self._deliver(record, self._read_estimates, ())

    def _read_estimates(self, record):
        if getattr(record, "estimate_candidate", None) is None:
            return
        known_time, known_material, revisions = record.estimate_candidate
        if not all(current > previous for current, previous in zip(record.pi_revisions, revisions)):
            return
        if self.app.getPrintInformation() is not record.info or self.app.getMultiBuildPlateModel().activeBuildPlate != record.plate:
            return
        duration = record.info.currentPrintTime
        try:
            seconds = int(duration) if duration is not None and not isinstance(duration, bool) and known_time else None
            if seconds is not None and seconds < 0:
                seconds = None
        except (TypeError, ValueError, OverflowError):
            seconds = None
        length = _total(record.info.materialLengths, 1000.0) if known_material else None
        weight = _total(record.info.materialWeights) if known_material else None
        warnings = []
        for name, value in (("print_time_seconds", seconds), ("length_mm", length), ("weight_g", weight)):
            if value is None:
                warnings.append("STE Slicer не предоставил подтверждённое значение %s" % name)
        record.statistics = {"print_time_seconds": seconds, "material": {"length_mm": length, "weight_g": weight}, "warnings": warnings}

    def _changed(self, record, *args):
        if _context(self.app)[0] != record.snapshot:
            self.invalidate()

    def _property_changed(self, record, key, role):
        if role == "value":
            self._changed(record)

    def _deferred_refresh(self, record):
        if self._current(record):
            if record.done:
                record.final_check_ready = True
            self._tick()

    def _tick(self):
        record = self.record
        if record is None or not self._current(record):
            return
        try:
            self._changed(record)
            busy = backend_busy(record.backend)
            if record.state in ("queued", "running") and self.clock() >= record.deadline:
                self._fail(record, "slicing_timeout", "Истекли 180 секунд ожидания; это не отмена engine")
            if record.state == "failed":
                if not busy:
                    self._release(record)
                    self._detach(record)
                    self._stop_timer()
                return
            if record.state == "completed":
                if (getattr(record.scene, "gcode_dict", {}).get(record.plate) is not record.buffer or not record.valid):
                    record.valid = False
                    self._detach(record)
                    self._stop_timer()
                return
            current_job = record.backend._start_slice_job
            if ((current_job is not None and current_job is not record.job and not (record.job_pending and record.job is None)) or
                    record.backend._start_slice_job_build_plate not in (record.plate, None)):
                self._fail(record, "slicing_conflict", "Сменились задание подготовки или платформа backend")
                return
            if record.force_returned and not record.begun and not busy:
                self._fail(record, "slicing_failed", "Команда запуска вернулась без подтверждённого начала нарезки")
                return
            self._read_estimates(record)
            if record.done and record.final_check_ready and not busy and record.buffer is not None and record.statistics is not None:
                buffer = getattr(record.scene, "gcode_dict", {}).get(record.plate)
                if buffer is not record.buffer or not buffer or not any(isinstance(line, str) and line.strip() for line in buffer):
                    self._fail(record, "slicing_failed", "Нет свежих данных G-code захваченного запуска")
                    return
                record.state, record.valid = "completed", True
                record.result = {"slice_id": record.slice_id, "printing_mode": "classic", "backend_id": record.backend_id,
                                 "build_plate": record.plate, "source": "ste_slicer"}
                record.result.update(record.statistics)
                self.last_completed = record
                self._release(record)
        except Exception as exc:
            LOGGER.error("Slicing observation: %s", type(exc).__name__)
            self._fail(record, "slicing_failed", "Не удалось проверить состояние backend; проверьте журнал приложения")

    @staticmethod
    def _status_data(record):
        return {"slice_id": record.slice_id, "state": record.state, "progress": record.progress,
                "printing_mode": "classic", "backend_id": record.backend_id, "build_plate": record.plate,
                "valid": record.valid, "error": copy.deepcopy(record.error)}

    def _find(self, slice_id):
        for record in (self.record, self.last_completed):
            if record is not None and record.slice_id == slice_id:
                return record
        raise SlicerError("slice_not_found", "Идентификатор нарезки не найден в текущей сессии")

    def status(self, app, slice_id):
        self._tick()
        return self._status_data(self._find(slice_id))

    def result(self, app, slice_id):
        self._tick()
        record = self._find(slice_id)
        if record.state != "completed":
            raise SlicerError("slice_not_ready", "Запуск не завершён успешно; прочитайте get_slicing_status")
        if not record.valid:
            raise SlicerError("slice_result_stale", "После нарезки изменились геометрия/параметры/платформа; требуется новый запуск")
        return copy.deepcopy(record.result)

    def _stop_timer(self):
        if self.timer is not None:
            self.timer.stop()
            self.timer.timeout.disconnect(self._tick)
            self.timer.deleteLater()
            self.timer = None

    def close(self, app):
        self.closed = True
        self._stop_timer()
        if self.record is not None:
            self._fail(self.record, "slicer_not_connected", "Плагин завершает работу; запуск не подтверждён")
            self._release(self.record)
            self._detach(self.record)
        self.record = self.last_completed = self.app = None
