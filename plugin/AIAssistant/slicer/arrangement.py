"""Correlated native ArrangeObjectsJob; own API reads/confirmation only on Qt."""
import copy
import datetime
import logging
import math
import time
import uuid

from .models import walk, mesh_nodes
from .qt_bridge import SlicerError
from .slicing import backend_busy, _stack_signature
from .transforms import AXES, LIMIT, identity, matrix, near


LOGGER = logging.getLogger(__name__)
ARRANGEMENT_FIELDS = {"arrange_models": (), "get_arrangement_status": ("arrangement_id",)}


def validate_arrangement_arguments(name, arguments):
    if not isinstance(arguments, dict) or set(arguments) != set(ARRANGEMENT_FIELDS[name]):
        raise ValueError("Укажите только поля: " + ", ".join(ARRANGEMENT_FIELDS[name]))
    if name == "get_arrangement_status" and (not isinstance(arguments["arrangement_id"], str) or not arguments["arrangement_id"]):
        raise ValueError("arrangement_id должен быть непустой строкой")


def _hull(node):
    # Native ShapeArray skips None or all-zero points. Never substitute an AABB.
    hull = node.callDecoration("getConvexHull")
    if hull is None:
        raise SlicerError("arrangement_hull_unavailable", "Convex hull ещё не готов; дождитесь UI и перечитайте модели")
    for polygon in (hull, node.callDecoration("getConvexHullHead")):
        if polygon is None:
            continue  # Native ShapeArray falls back to hull for the head.
        points = polygon.getPoints()
        if points is None or len(points) < 3:
            raise SlicerError("arrangement_hull_unavailable", "Нужен непустой convex hull минимум с тремя точками")
        try:
            values = [[float(p[i]) for i in range(2)] for p in points]
        except (ValueError, TypeError, IndexError, OverflowError):
            raise SlicerError("arrangement_hull_unavailable", "Не подтверждён формат точек convex hull")
        if (not all(math.isfinite(v) and abs(v) <= LIMIT for p in values for v in p) or
                not any(v != 0 for p in values for v in p)):
            raise SlicerError("arrangement_hull_unavailable", "Convex hull содержит недопустимые или пустые точки")


def _geometry(node, relative):
    origin = matrix(node)
    result = []
    for part in walk(node):
        if not (part.callDecoration("isSliceable") or part.callDecoration("isGroup")):
            continue
        value, mesh = matrix(part), part.getMeshData()
        transform = tuple(v for row in value for v in row)
        if relative:
            transform = tuple(value[i][j] - (origin[i][3] if j == 3 and i < 3 else 0)
                              for i in range(4) for j in range(4))
        object_stack = part.callDecoration("getStack")
        result.append((id(part), id(part.getParent()), id(mesh), mesh.getVertexCount() if mesh is not None else 0,
                       part.callDecoration("getBuildPlateNumber"),
                       _stack_signature(object_stack) if object_stack is not None else None, transform))
    return tuple(result)


def _same_geometry(a, b):
    return len(a) == len(b) and all(x[:-1] == y[:-1] and all(near(v, w) for v, w in zip(x[-1], y[-1]))
                                  for x, y in zip(a, b))


class _Arrangement(object):
    def __init__(self, deadline):
        self.arrangement_id = "arrangement-" + uuid.uuid4().hex
        self.deadline = deadline
        self.state, self.error, self.all_fit = "queued", None, None
        self.models, self.warnings = [], []
        self.valid, self.observed_at = False, None
        self.job, self.callback, self.targets, self.before_nodes = None, None, None, None
        self.started, self.held = False, True


class Arrangement(object):
    def __init__(self, registry, transforms, slicing, lock, budget=120.0):
        self.registry, self.transforms, self.slicing, self.lock = registry, transforms, slicing, lock
        self.budget, self.clock = budget, time.monotonic
        self.records, self.order, self.active = {}, [], None
        self.timer, self.app, self.closed = None, None, False

    @staticmethod
    def _fail(record, code, message):
        if record.state not in ("failed", "completed"):
            record.state, record.error, record.valid = "failed", {"code": code, "message": message}, False

    def _context(self, app):
        stack = app.getGlobalContainerStack()
        manager = app.getBackendManager()
        scene = app.getController().getScene()
        plate = app.getMultiBuildPlateModel().activeBuildPlate
        if stack is None:
            raise SlicerError("no_active_machine", "Не выбрана машина STE Slicer")
        backend = manager.getCurrentBackend() if manager is not None else None
        if (stack.getProperty("printing_mode", "value") != "classic" or backend is None or
                backend is not manager.getBackendByType("classic")):
            raise SlicerError("unsupported_arrangement_mode", "ТЗ 09 поддерживает только classic с соответствующим backend")
        prefs = app.getPreferences()
        if prefs.getValue("general/auto_slice") is not False:
            raise SlicerError("auto_slicing_enabled", "Выключите auto-slice в UI перед расстановкой; Preferences не меняются")
        if any(prefs.getValue(key) is not False for key in ("physics/automatic_drop_down", "physics/automatic_push_free")):
            raise SlicerError("arrangement_unsupported", "Выключите Automatic drop down и push free в UI; Preferences не меняются")
        local = app.getOutputDeviceManager().getOutputDevice("local_file")
        if backend_busy(backend) or local is not None and local._writing:
            raise SlicerError("arrangement_busy", "Backend или штатная запись ещё заняты")
        signature = (id(stack), id(manager), id(backend), id(scene), id(scene.getRoot()), plate,
                     _stack_signature(stack), tuple(sorted((key, id(value), _stack_signature(value)) for key, value in stack.extruders.items())))
        return signature

    def _ready(self, start_deadline):
        if self.closed:
            raise SlicerError("slicer_not_connected", "Наблюдение расстановки закрыто")
        if start_deadline is not None and time.monotonic() >= start_deadline:
            raise SlicerError("qt_timeout", "Подготовка расстановки просрочена; native job не запускался. Не повторяйте автоматически")

    def start(self, app, start_deadline=None):
        self._ready(start_deadline)
        if not self.lock.acquire(False):
            raise SlicerError("arrangement_busy", "Заняты импорт, нарезка, экспорт или другая запись")
        record = None
        try:
            context = self._context(app)
            root = app.getController().getScene().getRoot()
            if any(n.callDecoration("isBlockSlicing") for n in walk(root)):
                raise SlicerError("arrangement_unsupported", "Закройте G-code preview перед расстановкой")
            nodes = self.registry.nodes(app)
            plate = app.getMultiBuildPlateModel().activeBuildPlate
            targets = [n for n in nodes if n.callDecoration("getBuildPlateNumber") == plate]
            if not targets:
                raise SlicerError("empty_scene", "На активной платформе нет моделей для расстановки")
            volume = app.getBuildVolume()
            volume_box = volume.getBoundingBox() if volume is not None else None
            if volume_box is None:
                raise SlicerError("arrangement_unsupported", "Границы BuildVolume ещё недоступны")
            width, depth = float(volume_box.width), float(volume_box.depth)
            if not all(math.isfinite(v) and 0 < v <= LIMIT for v in (width, depth)):
                raise SlicerError("arrangement_unsupported", "Некорректные границы BuildVolume")
            before = []
            for node in targets:
                if (not node.isEnabled() or not node.isSelectable() or node.getParent() is None or
                        not identity(matrix(node.getParent())) or
                        any(p.callDecoration("getBuildPlateNumber") != plate for p in mesh_nodes(node))):
                    raise SlicerError("arrangement_unsupported", "Нужны включённые selectable верхние модели/группы одной платформы под единичным родителем")
                object_id = self.registry.item(node)["object_id"]
                state = self.transforms.state(app, object_id)
                if state["bounds"]["size"]["x"] > width or state["bounds"]["size"]["z"] > depth:
                    raise SlicerError("arrangement_unsupported", "Есть модель крупнее стола; запуск всех моделей отклонён, без тихого пропуска")
                _hull(node)
                before.append({"object_id": object_id, "before": state, "after": None})
            offset = float(volume.getEdgeDisallowedSize()) + 2
            if not math.isfinite(offset) or not 0 <= offset <= LIMIT:
                raise SlicerError("arrangement_unsupported", "Некорректный отступ BuildVolume")
            self._ready(start_deadline)
            from steslicer.Arranging.ArrangeObjectsJob import ArrangeObjectsJob
            from PyQt5.QtCore import QTimer
            job = ArrangeObjectsJob(targets, [], min_offset=max(offset, 8))
            record = _Arrangement(self.clock() + self.budget)
            record.context, record.models, record.job = context, before, job
            record.targets, record.before_nodes = tuple(targets), tuple(nodes)
            record.geometry = {id(n): _geometry(n, n in targets) for n in nodes}
            record.callback = lambda completed: self._queue_finished(record, completed)
            job.finished.connect(record.callback)
            self.app, self.active = app, record
            self.records[record.arrangement_id] = record
            self.order.append(record.arrangement_id)
            while len(self.order) > 16:
                del self.records[self.order.pop(0)]
            self.timer = QTimer()
            self.timer.setInterval(250)
            self.timer.timeout.connect(self._tick)
            self.timer.start()
            self._ready(start_deadline)
            # After native acceptance, changes may occur before the next Qt tick.
            self.slicing.invalidate()
            for previous in self.records.values():
                previous.valid = False
            record.started = True
            try:
                job.start()
            except Exception as exc:
                LOGGER.error("Arrangement start: %s", type(exc).__name__)
                self._fail(record, "arrangement_failed", "Запуск не подтверждён; job мог попасть в очередь. Не повторяйте; при зависании перезапустите приложение")
            return self._data(record)
        except Exception:
            if record is not None:
                self._fail(record, "arrangement_failed", "Ошибка подготовки native job")
                self._retire(record)
            else:
                self.lock.release()
            raise

    def _queue_finished(self, record, job):
        if job is not record.job:
            return
        # UM.Auto may already queue this, but the extra Qt callback also lets run() return.
        try:
            self.app.callLater(self._observe, record)
        except Exception as exc:
            LOGGER.error("Arrangement completion scheduling: %s", type(exc).__name__)
            # Context stays alive and guarded; timer/status can still observe the exit.

    def _tick(self):
        if self.active is not None and not self.closed:
            self._observe(self.active)

    def _observe(self, record):
        if record is not self.active or record.job is None:
            return
        exited = False
        try:
            # An early finished signal and getResult()==None prove nothing.
            exited = record.job.isFinished() and not record.job.isRunning()
            if self.closed:
                if exited:
                    self._retire(record)
                return
            if self.clock() >= record.deadline:
                self._fail(record, "arrangement_timeout", "Истекли 120 секунд; native job не отменён. До его выхода изменения заблокированы; при зависании перезапустите приложение, без автоповтора")
            try:
                if self._context(self.app) != record.context:
                    raise SlicerError("arrangement_context_changed", "Контекст изменился")
            except SlicerError as exc:
                self._fail(record, "arrangement_context_changed", "Изменились машина, настройки, сцена или платформа: " + exc.code + "; автоповтора/отката нет")
            if not exited:
                if record.state == "queued" and record.job.isRunning():
                    record.state = "running"
                return
            self.slicing.invalidate()
            if record.job.hasError():
                LOGGER.error("Native arrangement error: %s", type(record.job.getError()).__name__)
                self._fail(record, "arrangement_failed", "Штатная расстановка завершилась с ошибкой; проверьте UI/журнал, частичные изменения сохранены")
            self._confirm(record)
        except SlicerError as exc:
            self._fail(record, "arrangement_context_changed", "Контекст/геометрия расстановки не подтверждены: " + exc.code)
        except Exception as exc:
            LOGGER.error("Arrangement observation: %s", type(exc).__name__)
            self._fail(record, "arrangement_failed", "Ошибка API проверки расстановки; перечитайте модели/UI")
        finally:
            # Only release after public job flags confirm the final JobQueue exit.
            if record.job is not None and exited:
                self._retire(record)

    def _confirm(self, record):
        nodes = self.registry.nodes(self.app)
        if set(nodes) != set(record.before_nodes):
            self._fail(record, "arrangement_context_changed", "Изменился состав сцены; результат не подтверждён")
        for node in record.before_nodes:
            if node not in nodes or not _same_geometry(record.geometry[id(node)], _geometry(node, node in record.targets)):
                self._fail(record, "arrangement_context_changed", "Изменились геометрия/rotation/scale/части группы или модели других платформ")
        # Save actual partial after where IDs remain alive, including failures/timeouts.
        for item in record.models:
            try:
                item["after"] = self.transforms.state(self.app, item["object_id"])
            except SlicerError as exc:
                item["after_error"] = {"code": exc.code, "message": "Перечитайте list_models/get_model_state"}
                self._fail(record, "arrangement_context_changed", "Не все целевые модели сохранились")
        record.observed_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        if record.state != "failed":
            flags = [item["after"]["outside_build_area"] for item in record.models]
            # Native failure fallback X=200 may still be inside a wide build volume.
            # found_solution_for_all is local to run(); boundary=false cannot prove it.
            record.all_fit = False if True in flags else None
            if all(item["before"]["world_transform"] == item["after"]["world_transform"] for item in record.models):
                record.warnings.append("Job завершён без наблюдаемого перемещения; применение операции не подтверждено, фактическая проверка границ сохранена")
            record.state, record.valid = "completed", True
            record.after_geometry = {id(n): _geometry(n, False) for n in nodes}
            record.warnings.append("Native job не возвращает признак полного решения. outside=false не доказывает, что всем найдены места; all_fit неизвестен без явного выхода за границы. Проверьте UI/сообщение расстановки")

    def _retire(self, record):
        if record.job is not None and record.callback is not None:
            try:
                record.job.finished.disconnect(record.callback)
            except (TypeError, RuntimeError, ValueError) as exc:
                LOGGER.warning("Arrangement disconnect: %s", type(exc).__name__)
        record.job = record.callback = record.targets = record.before_nodes = None
        record.geometry = None
        if record.held:
            record.held = False
            self.lock.release()
        if self.active is record:
            self.active = None
            self._stop_timer()

    def _stop_timer(self):
        if self.timer is not None:
            self.timer.stop()
            self.timer.timeout.disconnect(self._tick)
            self.timer.deleteLater()
            self.timer = None

    @staticmethod
    def _data(record):
        return copy.deepcopy({"arrangement_id": record.arrangement_id, "state": record.state, "progress": None,
                              "error": record.error, "models": record.models, "all_fit": record.all_fit,
                              "valid": record.valid, "observed_at": record.observed_at, "warnings": record.warnings})

    def status(self, app, arrangement_id):
        self._tick()
        record = self.records.get(arrangement_id)
        if record is None:
            raise SlicerError("arrangement_not_found", "ID расстановки не найден в текущей сессии")
        if record.state == "completed" and record.valid:
            try:
                nodes = self.registry.nodes(app)
                if (self._context(app) != record.context or set(record.after_geometry) != {id(n) for n in nodes} or
                        any(not _same_geometry(record.after_geometry[id(n)], _geometry(n, False)) for n in nodes)):
                    record.valid = False
            except SlicerError:
                record.valid = False
        return self._data(record)

    def close(self, app):
        self.closed = True
        self._stop_timer()
        if self.active is not None:
            self._fail(self.active, "slicer_not_connected", "Плагин закрывается; native job не отменён, после открытия перечитайте сцену")
            # Retain the job/nodes/finished subscription and lock until actual exit.
            self._observe(self.active)
