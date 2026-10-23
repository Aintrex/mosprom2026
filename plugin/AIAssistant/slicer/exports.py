"""Штатный WriteFileJob; filesystem I/O вынесен из Qt в свой worker."""
import copy
import logging
import math
import mmap
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile

from .models import model_roots, walk
from .qt_bridge import SlicerError
from .slicing import backend_busy


LOGGER = logging.getLogger(__name__)
EXPORT_FIELDS = {"export_gcode": "slice_id", "save_project_3mf": "slice_id", "get_export_status": "export_id"}
CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


def validate_export_arguments(name, arguments):
    field = EXPORT_FIELDS[name]
    if (not isinstance(arguments, dict) or set(arguments) != {field} or
            not isinstance(arguments[field], str) or not arguments[field]):
        raise ValueError("Укажите только %s: непустую строку" % field)


def workspace_files(app, stack):
    from UM.Settings.ContainerRegistry import ContainerRegistry
    registry = app.getContainerRegistry()
    empty_type = type(registry.getEmptyInstanceContainer())
    files = set()
    for current in [stack] + list(stack.extruders.values()):
        for container in [current] + list(current.getContainers()):
            if isinstance(container, empty_type):
                continue
            suffix = ContainerRegistry.getMimeTypeForContainer(type(container)).preferredSuffix
            base = container.getMetaDataEntry("base_file")
            name = base if base else container.getId()
            if not name or any(c in name for c in ("/", "\\", "\x00")) or name in (".", ".."):
                raise SlicerError("export_workspace_unavailable", "Контейнер имеет неподдерживаемое имя для workspace")
            files.add("SteSlicer/%s.%s" % (name, suffix))
    return files | {"SteSlicer/preferences.cfg", "SteSlicer/version.ini"}


def verify_project(path, expected):
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or not expected.issubset(set(names)) or archive.testzip() is not None:
            raise SlicerError("export_invalid_file", "3MF не содержит подтверждённой workspace-конфигурации или повреждён")
        model = ET.fromstring(archive.read("3D/3dmodel.model"))
        resources, build = model.find(CORE + "resources"), model.find(CORE + "build")
        if model.tag != CORE + "model" or resources is None or build is None:
            raise SlicerError("export_invalid_file", "Некорректная структура 3MF model")
        objects = {obj.get("id"): obj for obj in resources.findall(CORE + "object")}
        if (not objects or not list(build) or any(item.get("objectid") not in objects for item in build) or
                not any(obj.find("%smesh/%svertices/%svertex" % (CORE, CORE, CORE)) is not None and
                        obj.find("%smesh/%striangles/%striangle" % (CORE, CORE, CORE)) is not None
                        for obj in objects.values())):
            raise SlicerError("export_invalid_file", "3MF не содержит печатной геометрии/build")
        for name in expected:
            if not archive.getinfo(name).file_size:
                raise SlicerError("export_invalid_file", "Пустая workspace-конфигурация 3MF")


def verify_gcode(path, chunks):
    # Только сравнение с данными engine, не генерация траекторий.
    body = "".join(chunks).encode("utf-8")
    if not re.search(br"(?m)^[ \t]*G0?[0123](?=[ \t\r\n])", body):
        raise SlicerError("export_invalid_file", "В подтверждённых данных engine нет команд движения")
    with open(path, "rb") as stream:
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            if data.find(body) < 0:
                raise SlicerError("export_invalid_file", "G-code не содержит исходные данные подтверждённого engine job")


class _Export(object):
    def __init__(self, slice_id, kind, budget):
        self.export_id = "export-" + uuid.uuid4().hex
        self.slice_id, self.kind = slice_id, kind
        self.deadline = time.monotonic() + budget
        self.state, self.progress, self.error = "queued", None, None
        self.artifact, self.directory = None, None
        self.held, self.started, self.native_done = True, False, False
        self.job = self.stream = self.archive = None
        self.subscriptions = []
        self.events = queue.Queue()
        self.finished, self.publish, self.accept = threading.Event(), threading.Event(), threading.Event()
        self.native_ok = self.publish_ok = self.accepted = False


class Exports(object):
    def __init__(self, slicing, lock, budget=120.0):
        self.slicing, self.lock, self.budget = slicing, lock, budget
        self.records, self.artifacts = {}, {}
        self.active, self.timer, self.app = None, None, None
        self.closed = False
        self.main = threading.current_thread()
        self.clock = time.monotonic

    @staticmethod
    def _fail(record, code, message):
        if record.state not in ("failed", "completed"):
            record.state = "failed"
            record.error = {"code": code, "message": message}
        record.publish.set()
        record.accept.set()

    def _release(self, record):
        if record.held:
            record.held = False
            self.lock.release()

    def _connect(self, record, signal, handler):
        def callback(*args):
            if self.closed and (handler != self._finished or record.native_done):
                return
            if threading.current_thread() is not self.main:
                self.app.callLater(callback, *args)
                return
            try:
                handler(record, *args)
            except Exception as exc:
                LOGGER.error("Export callback: %s", type(exc).__name__)
                self._fail(record, "export_failed", "Ошибка API экспорта; проверьте журнал STE Slicer")
        signal.connect(callback)
        record.subscriptions.append((signal, callback))

    @staticmethod
    def _disconnect(record, keep_finished=False):
        retained = []
        for signal, callback in record.subscriptions:
            if keep_finished and record.job is not None and signal is record.job.finished:
                retained.append((signal, callback))
                continue
            try:
                signal.disconnect(callback)
            except (TypeError, RuntimeError) as exc:
                LOGGER.warning("Export disconnect: %s", type(exc).__name__)
        record.subscriptions = retained

    def _output_busy(self, app):
        local = app.getOutputDeviceManager().getOutputDevice("local_file")
        # Подтверждённое readonly-поле LocalFileOutputDevice; общего getter нет.
        return local is not None and local._writing

    def _validate(self, record):
        self.slicing.result(self.app, record.slice_id)
        if backend_busy(record.backend) or self._output_busy(self.app):
            raise SlicerError("export_busy", "Engine/обработка слоёв или штатная запись заняты")

    def start(self, app, slice_id, kind):
        if self.closed:
            raise SlicerError("slicer_not_connected", "Экспорт закрыт")
        if not self.lock.acquire(False):
            raise SlicerError("export_busy", "Занят экспорт, нарезка, импорт или смена параметров")
        transferred = False
        try:
            self.slicing.result(app, slice_id)
            run = self.slicing._find(slice_id)  # Единственный контекст нарезки из ТЗ 05.
            if run.backend is None or backend_busy(run.backend) or self._output_busy(app):
                raise SlicerError("export_busy", "Backend/обработка слоёв или штатная запись заняты")
            from UM.Resources import Resources
            from UM.FileHandler.FileWriter import FileWriter
            from PyQt5.QtCore import QTimer
            if kind == "gcode":
                writer = app.getMeshFileHandler().getWriter("GCodeWriter")
                mesh_writer = None
                mode = FileWriter.OutputMode.TextMode
                expected = set()
            else:
                writer = app.getWorkspaceFileHandler().getWriter("3MFWriter")
                mesh_writer = app.getMeshFileHandler().getWriter("3MFWriter")
                mode = FileWriter.OutputMode.BinaryMode
                expected = workspace_files(app, run.stack)
            if writer is None or kind == "project" and mesh_writer is None:
                raise SlicerError("export_writer_unavailable", "Штатный writer требуемого типа не зарегистрирован")
            nodes = list(model_roots(run.root))
            for node in nodes:
                if node.getParent() is not run.root or any(
                        not (n.callDecoration("isSliceable") or n.callDecoration("isGroup")) or
                        n.callDecoration("getBuildPlateNumber") != run.plate for n in walk(node)):
                    raise SlicerError("export_scene_unsupported", "Нужны печатные root-модели/группы активной платформы без служебных потомков")
            if not nodes:
                raise SlicerError("empty_scene", "Нет печатных моделей для экспорта")
            record = _Export(slice_id, kind, self.budget)
            record.deadline = self.clock() + self.budget
            record.writer, record.mesh_writer, record.mode = writer, mesh_writer, mode
            record.backend, record.nodes = run.backend, nodes
            record.chunks, record.expected = tuple(run.buffer), expected
            record.cache = Resources.getCacheStoragePath()
            self.app, self.active = app, record
            self.records[record.export_id] = record
            transferred = True
            self._connect(record, app.getOutputDeviceManager().writeStarted, self._foreign_write)
            self.timer = QTimer()
            self.timer.setInterval(100)
            self.timer.timeout.connect(self._tick)
            self.timer.start()
            thread = threading.Thread(target=self._files, args=(record,))
            thread.daemon = True
            record.thread = thread
            thread.start()
            return self._data(record)
        except Exception:
            if transferred:
                self._disconnect(record)
                self._stop_timer()
                self._fail(record, "export_failed", "Не удалось начать подготовку экспорта")
                self._release(record)
                self.active = None
            raise
        finally:
            if not transferred:
                self.lock.release()

    def _foreign_write(self, record, *args):
        self._fail(record, "export_conflict", "Обнаружена параллельная запись через UI; не запускайте её во время экспорта")

    def _progress(self, record, job, amount):
        if job is record.job and record.state == "running" and isinstance(amount, (int, float)) and not isinstance(amount, bool):
            if 0 <= amount <= 100 and math.isfinite(amount):
                record.progress = float(amount) / 100.0

    def _finished(self, record, job):
        if job is not record.job or record.native_done:
            return
        record.native_done = True
        try:
            record.native_ok = bool(job.getResult()) and job.getError() is None
            if record.mesh_writer is not None:
                if self._output_busy(self.app):
                    raise SlicerError("export_conflict", "Чужой writer ещё занят; его archive не изменён, нужен ручной контроль")
                try:
                    record.archive = record.mesh_writer.getArchive()
                finally:
                    record.mesh_writer.setStoreArchive(False)
            if not record.native_ok:
                self._fail(record, "export_failed", "Штатный writer вернул отказ/ошибку; проверьте сообщения STE Slicer")
        except Exception as exc:
            LOGGER.error("Export finish: %s", type(exc).__name__)
            self._fail(record, "export_failed", "Не удалось подтвердить завершение/сброс archive штатного writer")
        finally:
            try:
                self._disconnect(record)
            finally:
                record.finished.set()

    def _wait(self, record, event, before_native=False):
        while not event.wait(0.05):
            if (self.closed or record.state == "failed") and (not before_native or not record.started):
                return False
        return not self.closed and record.state != "failed"

    def _files(self, record):
        staging = final = directory = None
        staging_owned = final_owned = False
        stream = None
        try:
            base = os.path.join(record.cache, "AIAssistant", "exports")
            os.makedirs(base, exist_ok=True)
            directory = tempfile.mkdtemp(prefix="export-", dir=base)
            staging = os.path.join(directory, "writing.tmp")
            name = "result.gcode" if record.kind == "gcode" else "project.3mf"
            final = os.path.join(directory, name)
            stream = open(staging, "x", encoding="utf-8", newline="\n") if record.kind == "gcode" else open(staging, "xb")
            staging_owned = True
            record.events.put(("ready", stream))
            # После native-start ждём именно finished, даже при timeout/shutdown.
            self._wait(record, record.finished, before_native=True)
            if record.started and not record.finished.is_set():
                raise RuntimeError("Unfinished writer cannot release its stream")
            if record.archive is not None:
                record.archive.close()
            try:
                stream.flush()
            finally:
                stream.close()
            stream = None
            if self.closed or record.state == "failed" or not record.native_ok:
                return
            size = os.path.getsize(staging)
            if not size:
                raise SlicerError("export_invalid_file", "Штатный writer создал пустой файл")
            try:
                if record.kind == "project":
                    verify_project(staging, record.expected)
                else:
                    verify_gcode(staging, record.chunks)
            except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError):
                raise SlicerError("export_invalid_file", "Файл не прошёл проверку ZIP/XML/данных engine")
            record.events.put(("verified", None))
            if not self._wait(record, record.publish) or not record.publish_ok:
                return
            # Эксклюзивное создание: существующий пользовательский файл не затирается.
            with open(staging, "rb") as source:
                target = open(final, "xb")
                final_owned = True
                with target:
                    shutil.copyfileobj(source, target)
                    target.flush()
            if os.path.getsize(final) != size:
                raise SlicerError("export_invalid_file", "Размер готового файла не совпал с проверенным временным файлом")
            os.remove(staging)
            staging = None
            record.events.put(("published", (directory, name, os.path.getsize(final))))
            self._wait(record, record.accept)
        except Exception as exc:
            LOGGER.error("Export filesystem: %s", type(exc).__name__)
            code = exc.code if isinstance(exc, SlicerError) else "export_io_error"
            message = exc.message if isinstance(exc, SlicerError) else "Ошибка открытия, записи, закрытия или проверки файла; проверьте журнал"
            record.events.put(("error", (code, message)))
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception as exc:
                    LOGGER.error("Export close cleanup: %s", type(exc).__name__)
            for path in (staging if staging_owned else None, final if final_owned and not record.accepted else None):
                if path is not None:
                    try:
                        os.remove(path)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        LOGGER.warning("Export temporary cleanup: %s", type(exc).__name__)
            if directory is not None and not record.accepted:
                try:
                    os.rmdir(directory)  # Только пустая собственная папка, без recursive delete.
                except OSError as exc:
                    LOGGER.warning("Export directory cleanup: %s", type(exc).__name__)
            record.events.put(("cleaned", None))
            if self.closed:
                # Native job уже вышел (либо не запускался). Только снятие
                # Python-ссылок; методов app/Qt из I/O worker здесь нет.
                record.writer = record.mesh_writer = record.job = record.stream = record.archive = record.backend = None
                record.nodes = record.chunks = record.expected = None

    def _tick(self):
        record = self.active
        if record is None or self.closed:
            return
        if record.state in ("queued", "running"):
            try:
                self._validate(record)
                if self.clock() >= record.deadline:
                    self._fail(record, "export_timeout", "Истекли 120 секунд. Writer не отменён; до его выхода защита остаётся. При зависании перезапустите приложение, без автоповтора.")
            except SlicerError as exc:
                self._fail(record, "export_result_stale", "Изменился контекст нарезки/началась чужая операция: " + exc.code)
            except Exception as exc:
                LOGGER.error("Export observation: %s", type(exc).__name__)
                self._fail(record, "export_failed", "Не удалось проверить контекст экспорта")
        while True:
            try:
                event, data = record.events.get_nowait()
            except queue.Empty:
                break
            if event == "ready" and record.state == "queued":
                try:
                    from UM.FileHandler.WriteFileJob import WriteFileJob
                    record.stream = data
                    record.job = WriteFileJob(record.writer, data, record.nodes, record.mode)
                    self._connect(record, record.job.finished, self._finished)
                    self._connect(record, record.job.progress, self._progress)
                    record.job.setAddToRecentFiles(False)
                    record.started, record.state = True, "running"
                    record.job.start()
                except Exception as exc:
                    LOGGER.error("Export job start: %s", type(exc).__name__)
                    self._fail(record, "export_failed", "Ошибка запуска WriteFileJob; если job принят, дождитесь выхода, при зависании перезапустите приложение")
            elif event == "verified" and record.state == "running":
                record.publish_ok = True
                record.publish.set()
            elif event == "published" and record.state == "running":
                directory, name, size = data
                record.directory = directory
                record.artifact = {"artifact_id": "artifact-" + uuid.uuid4().hex, "file_name": name, "kind": record.kind,
                                   "size_bytes": size, "slice_id": record.slice_id, "source": "ste_slicer",
                                   "writer_id": "GCodeWriter" if record.kind == "gcode" else "3MFWriter",
                                   "writer_type": "mesh_writer" if record.kind == "gcode" else "workspace_writer"}
                self.artifacts[record.artifact["artifact_id"]] = dict(record.artifact, local_directory=directory)
                record.state, record.accepted = "completed", True
                record.accept.set()
                self._retire(record)
            elif event == "error":
                self._fail(record, *data)
            elif event == "cleaned":
                self._retire(record)

    def _retire(self, record):
        self._disconnect(record)
        record.writer = record.mesh_writer = record.job = record.stream = record.archive = record.backend = None
        record.nodes = record.chunks = record.expected = None
        self._release(record)
        if self.active is record:
            self.active = None
            self._stop_timer()

    def _data(self, record):
        valid = False
        if record.state == "completed" and not self.closed:
            try:
                self.slicing.result(self.app, record.slice_id)
                valid = True
            except SlicerError:
                pass  # Исторический файл остаётся привязан к прежнему slice_id.
        return {"export_id": record.export_id, "slice_id": record.slice_id, "kind": record.kind,
                "state": record.state, "progress": record.progress, "error": copy.deepcopy(record.error),
                "artifact": copy.deepcopy(record.artifact), "local_directory": record.directory, "valid": valid}

    def status(self, app, export_id):
        self._tick()
        if export_id not in self.records:
            raise SlicerError("export_not_found", "Идентификатор экспорта не найден в текущей сессии")
        return self._data(self.records[export_id])

    def _stop_timer(self):
        if self.timer is not None:
            self.timer.stop()
            self.timer.timeout.disconnect(self._tick)
            self.timer.deleteLater()
            self.timer = None

    def close(self, app):
        self.closed = True
        self._stop_timer()
        if self.active is not None:
            record = self.active
            self._fail(record, "slicer_not_connected", "Плагин закрывается; экспорт не публикуется, используемый writer stream до finished не закрывается")
            self._disconnect(record, keep_finished=record.started and not record.native_done)
            self._release(record)
            self.active = None
        self.records.clear()
