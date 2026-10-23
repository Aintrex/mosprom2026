"""Локальный mesh-импорт штатным job и адресация текущей сцены."""
import logging
import math
import os
import stat
import threading
import weakref

from .qt_bridge import SlicerError


MAX_MODEL_BYTES = 50 * 1024 * 1024
MODEL_FIELDS = {"load_model": ("path",), "list_models": (), "get_model_bounds": ("object_id",)}
LOGGER = logging.getLogger(__name__)


def validate_model_arguments(name, arguments):
    fields = MODEL_FIELDS[name]
    if not isinstance(arguments, dict) or set(arguments) != set(fields):
        raise ValueError("Укажите только поля: %s" % (", ".join(fields) or "{}"))
    for field in fields:
        if not isinstance(arguments[field], str) or not arguments[field]:
            raise ValueError("%s должен быть непустой строкой" % field)


def path_key(path):
    # Только строковая нормализация, без filesystem I/O в Qt-сигналах.
    return os.path.normcase(os.path.normpath(path))


def _local_path(path):
    if not os.path.isabs(path) or "://" in path:
        raise SlicerError("invalid_model_path", "Нужен абсолютный путь к обычному локальному файлу; URL и сетевые пути запрещены")
    if path.startswith(("//", "\\\\")) or os.name == "nt" and path.startswith(("\\", "/")):
        raise SlicerError("invalid_model_path", "Сетевые пути не поддерживаются")
    if os.name == "nt":
        drive, tail = os.path.splitdrive(path)
        if not drive or ":" in tail:
            raise SlicerError("invalid_model_path", "Device-пути и файловые потоки не поддерживаются")
        import ctypes
        get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
        get_drive_type.argtypes = [ctypes.c_wchar_p]
        get_drive_type.restype = ctypes.c_uint
        if get_drive_type(drive + "\\") == 4:
            raise SlicerError("invalid_model_path", "Файл на сетевом диске не поддерживается")


def checked_model_path(path):
    """Вызывается в worker, не читает содержимое модели целиком."""
    if "\x00" in path:
        raise SlicerError("invalid_model_path", "Недопустимый путь модели")
    _local_path(path)
    if os.path.splitext(path)[1].lower() not in (".stl", ".obj", ".3mf"):
        raise SlicerError("unsupported_model_file", "Поддерживаются только STL, OBJ и 3MF")
    try:
        path = os.path.realpath(path)
        _local_path(path)
        if os.path.splitext(path)[1].lower() not in (".stl", ".obj", ".3mf"):
            raise SlicerError("unsupported_model_file", "Фактический файл должен иметь расширение STL, OBJ или 3MF")
        info = os.stat(path)
        if not stat.S_ISREG(info.st_mode):
            raise SlicerError("invalid_model_file", "Выбранный путь не является обычным файлом")
        if not 0 < info.st_size <= MAX_MODEL_BYTES:
            raise SlicerError("invalid_model_file", "Модель должна быть непустой и не больше 50 MiB")
        with open(path, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_MODEL_BYTES or not stream.read(1):
                raise SlicerError("invalid_model_file", "Файл модели изменился или имеет недопустимый размер")
        return path
    except OSError:
        raise SlicerError("model_file_unavailable", "Локальный файл модели не найден или недоступен для чтения")


def walk(node):
    yield node
    for child in node.getChildren():
        for descendant in walk(child):
            yield descendant


def mesh_nodes(node):
    if node.callDecoration("isBlockSlicing"):
        return
    mesh = node.getMeshData()
    if mesh is not None and node.callDecoration("isSliceable") and mesh.getVertexCount() > 0:
        yield node
    for child in node.getChildren():
        for descendant in mesh_nodes(child):
            yield descendant


def model_roots(root):
    for node in root.getChildren():
        if node.callDecoration("isBlockSlicing"):
            continue
        if (node.callDecoration("isGroup") or node.callDecoration("isSliceable")) and any(mesh_nodes(node)):
            yield node
        else:
            for child in model_roots(node):
                yield child


def bounds(node):
    minimum, maximum = None, None
    for part in mesh_nodes(node):
        box = part.getMeshData().getExtents(part.getWorldTransformation())
        if box is None:
            return None
        try:
            lo = [float(getattr(box.minimum, axis)) for axis in ("x", "y", "z")]
            hi = [float(getattr(box.maximum, axis)) for axis in ("x", "y", "z")]
        except (ValueError, TypeError, OverflowError):
            return None
        if not all(math.isfinite(v) for v in lo + hi) or any(a > b for a, b in zip(lo, hi)):
            return None
        minimum = lo if minimum is None else [min(a, b) for a, b in zip(minimum, lo)]
        maximum = hi if maximum is None else [max(a, b) for a, b in zip(maximum, hi)]
    if minimum is None:
        return None
    size = [b - a for a, b in zip(minimum, maximum)]
    if not all(math.isfinite(v) for v in size):
        return None
    return {"coordinate_system": "uranium_world_y_up", "unit": "mm",
            "min": dict(zip(("x", "y", "z"), minimum)),
            "max": dict(zip(("x", "y", "z"), maximum)),
            "size": dict(zip(("x", "y", "z"), size))}


class ModelRegistry(object):
    def __init__(self):
        self._ids = {}
        self._sequence = 0
        self._scene = None
        self._root = None
        self.active_load = None

    def nodes(self, app):
        scene = app.getController().getScene()
        root = scene.getRoot()
        if scene is not self._scene or root is not self._root:
            self._ids.clear()
            self._scene, self._root = scene, root
        nodes = list(model_roots(root))
        living = {id(node): node for node in nodes}
        for key, reference in list(self._ids.items()):
            node = reference()
            if node is None or living.get(id(node)) is not node:
                del self._ids[key]
        for node in nodes:
            if not any(reference() is node for reference in self._ids.values()):
                self._sequence += 1
                self._ids["model-%d" % self._sequence] = weakref.ref(node)
        return nodes

    def item(self, node):
        object_id = next(key for key, reference in self._ids.items() if reference() is node)
        name = node.getName()
        name = os.path.basename(name.replace("\\", "/")) if isinstance(name, str) else None
        plate = node.callDecoration("getBuildPlateNumber")
        return {"object_id": object_id, "name": name,
                "build_plate": int(plate) if isinstance(plate, int) and not isinstance(plate, bool) else None,
                "is_group": bool(node.callDecoration("isGroup")), "bounds": bounds(node)}

    def list_models(self, app):
        return {"models": [self.item(node) for node in self.nodes(app)]}

    def get_bounds(self, app, object_id):
        self.nodes(app)
        reference = self._ids.get(object_id)
        node = reference() if reference is not None else None
        if node is None:
            raise SlicerError("model_not_found", "Объект отсутствует в текущей сцене; вызовите list_models")
        value = bounds(node)
        if value is None:
            raise SlicerError("model_bounds_unavailable", "Мировые границы геометрии объекта недоступны")
        return {"object_id": object_id, "bounds": value}

    def close(self, app):
        if self.active_load is not None:
            self.active_load.finish(error=SlicerError("slicer_not_connected", "Плагин завершает работу; результат импорта неизвестен"))
        self._ids.clear()
        self._scene, self._root = None, None


class ModelLoad(object):
    def __init__(self, registry, path, change_lock):
        self.registry, self.path, self.change_lock = registry, path, change_lock
        self._guard = threading.Lock()
        self._phase = "pending"
        self._abandoned = False
        self._released = False
        self.app = None
        self.subscriptions = []
        self.result = None
        self.error = None
        self.loaded_nodes = None
        self.initial_nodes = None

    def _release(self):
        with self._guard:
            if self._released:
                return
            self._released = True
        self.change_lock.release()

    def abandon(self):
        # Worker может отменить подготовку, но не запущенный штатный job.
        with self._guard:
            self._abandoned = True
            pending = self._phase == "pending"
            if pending:
                self._phase = "cancelled"
        if pending:
            self._release()

    def start(self, app):
        with self._guard:
            if self._phase == "cancelled":
                return
            self._phase = "preparing"
        self.app = app
        self.main_thread = threading.current_thread()
        try:
            self.machine = app.getGlobalContainerStack()
            if self.machine is None:
                raise SlicerError("no_active_machine", "В STE Slicer не выбрана активная машина")
            self.scene = app.getController().getScene()
            self.root = self.scene.getRoot()
            self.initial_nodes = set(walk(self.root))
            if any(node.callDecoration("isBlockSlicing") for node in self.initial_nodes):
                raise SlicerError("model_preview_active", "Закройте G-code preview перед импортом; штатный загрузчик может очистить сцену")
            self.registry.nodes(app)
            self.registry.active_load = self
            for signal, slot in ((app.fileLoaded, self.on_loaded), (app.fileCompleted, self.on_completed)):
                signal.connect(slot)
                self.subscriptions.append((signal, slot))
            # Ленивый импорт сохраняет dev-режим без Qt/UM.
            from PyQt5.QtCore import QUrl
            url = QUrl.fromLocalFile(self.path)
            with self._guard:
                if self._abandoned:
                    raise SlicerError("qt_timeout", "Просроченный запуск импорта отменён")
                self._phase = "launched"
            app.readLocalFile(url, skip_project_file_check=True)
        except Exception as exc:
            with self._guard:
                launched = self._phase == "launched"
            if launched:
                self.error = exc  # Не известно, успел ли readLocalFile запустить job.
            else:
                self.finish(error=exc)
            raise

    def _same_scene(self):
        if (self.app.getGlobalContainerStack() is not self.machine or
                self.app.getController().getScene() is not self.scene or self.scene.getRoot() is not self.root):
            raise SlicerError("model_scene_changed", "Во время импорта изменилась машина или сцена")
        current = set(walk(self.root))
        if not self.initial_nodes.issubset(current):
            raise SlicerError("model_scene_changed", "Во время импорта исчезли исходные узлы сцены")
        return current

    def on_loaded(self, path):
        if self.app is None or path_key(path) != path_key(self.path):
            return
        if threading.current_thread() is not self.main_thread:
            self.app.callLater(self._signal_thread_error)
            return
        try:
            if self.error is not None:
                return
            current = self._same_scene()
            if self.loaded_nodes is not None or current != self.initial_nodes:
                raise SlicerError("model_scene_changed", "Обнаружен параллельный импорт или изменение сцены")
            self.loaded_nodes = current
        except Exception as exc:
            self.error = exc

    def _signal_thread_error(self):
        self.error = SlicerError("slicer_error", "Сигнал загрузки пришёл вне Qt-потока; результат нельзя надёжно сопоставить")

    def on_completed(self, path):
        if self.app is None or path_key(path) != path_key(self.path):
            return
        if threading.current_thread() is not self.main_thread:
            self.app.callLater(self._complete_off_thread)
            return
        try:
            if self.error is not None:
                raise self.error
            self._same_scene()
            if self.loaded_nodes is None:
                raise SlicerError("model_load_failed", "Нет подтверждённого начала добавления узлов своего файла")
            nodes = [node for node in self.registry.nodes(self.app) if node not in self.loaded_nodes]
            if not nodes:
                raise SlicerError("model_load_failed", "Загрузчик завершился без новых моделей; проверьте файл и сообщения reader")
            data = {"file_name": os.path.basename(self.path), "objects": [self.registry.item(node) for node in nodes]}
            self.finish(result=data)
        except Exception as exc:
            self.finish(error=exc)

    def _complete_off_thread(self):
        self._signal_thread_error()
        self.finish(error=self.error)

    def check(self, app):
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        try:
            self._same_scene()
        except Exception as exc:
            self.error = exc
            raise
        return None

    def finish(self, result=None, error=None):
        # Вызывается только на Qt-потоке, в том числе при позднем завершении.
        self.result, self.error = result, error
        for signal, slot in self.subscriptions:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError) as exc:
                LOGGER.warning("Model signal disconnect: %s", type(exc).__name__)
        self.subscriptions = []
        if self.registry.active_load is self:
            self.registry.active_load = None
        self.app, self.scene, self.root, self.machine = None, None, None, None
        self.initial_nodes, self.loaded_nodes = None, None
        with self._guard:
            self._phase = "done"
        self._release()
