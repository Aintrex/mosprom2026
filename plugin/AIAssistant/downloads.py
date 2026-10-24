"""Process-local references to accepted native exports; never owns/deletes their files."""
import copy
import os
import stat
import threading
import uuid


PUBLIC_FIELDS = ("artifact_id", "file_name", "kind", "size_bytes", "slice_id", "source", "writer_id", "writer_type")


class DownloadError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class DownloadRegistry(object):
    def __init__(self, resolve):
        self._resolve_native = resolve
        self._lock = threading.Lock()
        self._closed = False
        self._by_id, self._by_artifact = {}, {}

    def _open_service(self):
        if self._closed:
            raise DownloadError(503, "service_stopped", "Сервис скачивания закрывается")

    def _resolve(self, artifact_id):
        if not isinstance(artifact_id, str) or not artifact_id or len(artifact_id) > 128:
            raise DownloadError(404, "file_not_found", "Неизвестный ID native-артефакта")
        result = self._resolve_native(artifact_id)
        if not result.get("ok"):
            code = result.get("error", {}).get("code")
            if code in ("slice_result_stale", "slice_not_ready", "export_result_stale"):
                raise DownloadError(409, "artifact_stale", "Результат относится к прежнему состоянию проекта")
            if code in ("artifact_not_found", "artifact_not_ready"):
                raise DownloadError(404, "file_not_found", "Готовый native-артефакт не найден")
            raise DownloadError(503, "slicer_unavailable", "STE Slicer не подтвердил актуальность файла; повторите чтение состояния/UI")
        data = result["data"]
        if (any(not isinstance(data.get(key), str) or not data[key] for key in PUBLIC_FIELDS if key != "size_bytes") or
                data.get("artifact_id") != artifact_id or data.get("source") != "ste_slicer" or
                data.get("kind") not in ("gcode", "project") or
                not isinstance(data.get("size_bytes"), int) or isinstance(data["size_bytes"], bool) or data["size_bytes"] <= 0):
            raise DownloadError(404, "artifact_unavailable", "Метаданные native-файла не подтверждены")
        name, path, identity = data.get("file_name"), data.get("path"), data.get("identity")
        if (not isinstance(name, str) or not name or len(name) > 255 or name in (".", "..") or
                any(ord(c) < 32 or ord(c) == 127 or c in "/\\:" for c in name) or
                not isinstance(path, str) or not os.path.isabs(path) or
                not isinstance(identity, (tuple, list)) or len(identity) != 4 or identity[2] != data["size_bytes"] or
                os.path.basename(path) != name):
            raise DownloadError(404, "artifact_unavailable", "Имя/принадлежность native-файла не подтверждены")
        return data

    @staticmethod
    def _fingerprint(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

    def _open_file(self, data):
        stream = None
        try:
            path = data["path"]
            info = os.lstat(path)
            if (not stat.S_ISREG(info.st_mode) or os.path.realpath(path) != path or
                    self._fingerprint(info) != tuple(data["identity"])):
                raise DownloadError(409, "artifact_changed", "Native-файл заменён или изменён после публикации")
            stream = open(path, "rb")
            actual = os.fstat(stream.fileno())
            if not stat.S_ISREG(actual.st_mode) or self._fingerprint(actual) != tuple(data["identity"]):
                raise DownloadError(409, "artifact_changed", "Открытый файл не соответствует опубликованному")
            return stream
        except OSError:
            if stream is not None:
                stream.close()
            raise DownloadError(404, "file_not_found", "Опубликованный файл недоступен")
        except Exception:
            if stream is not None:
                stream.close()
            raise

    def register(self, artifact_id):
        with self._lock:
            self._open_service()
        data = self._resolve(artifact_id)  # No registry/service lock while waiting for Qt or filesystem.
        with self._open_file(data):
            pass
        with self._lock:
            self._open_service()
            previous = self._by_artifact.get(artifact_id)
            if previous is not None:
                if previous["native"] != data:
                    raise DownloadError(409, "artifact_changed", "Зарегистрированный native-артефакт изменился")
                return copy.deepcopy(previous["public"])
            file_id = "download-" + uuid.uuid4().hex
            if file_id in self._by_id:
                raise DownloadError(503, "download_id_conflict", "Не удалось назначить новый ID скачивания")
            public = {key: data[key] for key in PUBLIC_FIELDS}
            public.update(file_id=file_id, download_url="/api/files/" + file_id)
            record = {"native": copy.deepcopy(data), "public": public}
            self._by_artifact[artifact_id] = self._by_id[file_id] = record
            return copy.deepcopy(public)

    def open(self, file_id):
        with self._lock:
            self._open_service()
            record = self._by_id.get(file_id)
            if record is None:
                raise DownloadError(404, "file_not_found", "Неизвестный ID скачивания в текущем процессе")
            expected = copy.deepcopy(record["native"])
        data = self._resolve(expected["artifact_id"])
        if data != expected:
            raise DownloadError(409, "artifact_changed", "Native-артефакт не совпадает с зарегистрированным")
        stream = self._open_file(data)
        try:
            with self._lock:
                self._open_service()
            return stream, {key: data[key] for key in PUBLIC_FIELDS}
        except Exception:
            stream.close()
            raise

    def close(self):
        with self._lock:
            self._closed = True
            self._by_id.clear()
            self._by_artifact.clear()
