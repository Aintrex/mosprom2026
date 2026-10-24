"""Bounded single-part upload and private process-lifetime storage (no Qt APIs)."""
import copy
import io
import logging
import os
import re
import stat
import struct
import tempfile
import threading
import uuid
import xml.etree.ElementTree as ET
import zipfile
import zlib
from email.parser import Parser
from email.utils import collapse_rfc2231_value


MODEL_BYTES, IMAGE_BYTES = 50 * 1024 * 1024, 5 * 1024 * 1024
WRAPPER_BYTES, TOTAL_BYTES = 64 * 1024, 250 * 1024 * 1024
ZIP_BYTES, XML_BYTES, ZIP_ENTRIES = 200 * 1024 * 1024, 16 * 1024 * 1024, 1024
TYPES = {".jpg": ("image", "image/jpeg"), ".jpeg": ("image", "image/jpeg"), ".png": ("image", "image/png"),
         ".stl": ("model", "model/stl"), ".obj": ("model", "model/obj"), ".3mf": ("model", "model/3mf")}
LOGGER = logging.getLogger(__name__)


class UploadError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _invalid(message="Некорректное multipart-тело; нужен ровно один файл в поле file"):
    return UploadError(400, "invalid_upload", message)


def _filename(name):
    if (not isinstance(name, str) or not name or len(name) > 255 or name != name.strip() or
            any(c in name for c in ("/", "\\", ":")) or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise _invalid("Нужно безопасное имя файла без пути и управляющих символов")
    extension = os.path.splitext(name)[1].lower()
    if extension not in TYPES:
        raise UploadError(415, "unsupported_file_type", "Разрешены JPG/JPEG, PNG, STL, OBJ и 3MF")
    return extension


def _boundary(content_type):
    if not isinstance(content_type, str) or any(c in content_type for c in ("\r", "\n", "\x00")):
        raise _invalid("Некорректный Content-Type")
    message = Parser().parsestr("Content-Type: " + content_type + "\r\n\r\n")
    params = message.get_params() or []
    if message.get_content_type() != "multipart/form-data":
        raise UploadError(415, "unsupported_media_type", "Нужен multipart/form-data с boundary")
    boundary = message.get_boundary()
    if (len(params) != 2 or not isinstance(boundary, str) or
            not re.match(r"^[0-9A-Za-z'()+_,./:=?\-]{1,70}$", boundary)):
        raise _invalid("Некорректный multipart boundary")
    return boundary.encode("ascii")


def _read(stream, length, line=False):
    try:
        return stream.readline(length) if line else stream.read(length)
    except OSError:
        raise _invalid("Запрос не дописан или истёк socket timeout")


def _headers(stream, length, boundary):
    initial = _read(stream, min(length, WRAPPER_BYTES + 1), line=True)
    if initial != b"--" + boundary + b"\r\n":
        raise _invalid()
    used, lines = len(initial), []
    while used < length:
        line = _read(stream, min(length - used, WRAPPER_BYTES - used + 1), line=True)
        used += len(line)
        if used > WRAPPER_BYTES:
            raise UploadError(413, "payload_too_large", "Multipart-обёртка превышает 64 KiB")
        if not line or not line.endswith(b"\r\n"):
            raise _invalid()
        if line == b"\r\n":
            break
        if line[:1] in (b" ", b"\t") or b":" not in line:
            raise _invalid()
        lines.append(line)
    else:
        raise _invalid()
    try:
        message = Parser().parsestr(b"".join(lines).decode("utf-8") + "\r\n")
    except UnicodeError:
        raise _invalid("Заголовки multipart должны быть UTF-8")
    keys = [key.lower() for key in message.keys()]
    if (len(keys) != len(set(keys)) or set(keys) - {"content-disposition", "content-type"} or
            keys.count("content-disposition") != 1):
        raise _invalid()
    params = message.get_params(header="content-disposition") or []
    if (not params or params[0][0].lower() != "form-data" or
            sorted(key.lower() for key, value in params[1:]) != ["filename", "name"] or
            message.get_param("name", header="content-disposition") != "file"):
        raise _invalid()
    raw_name = message.get_param("filename", header="content-disposition")
    name = collapse_rfc2231_value(raw_name) if isinstance(raw_name, tuple) else raw_name
    return name, _filename(name), used


def _png(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    position, count, image = 8, 0, False
    while position + 12 <= len(data) and count < 4096:
        size = struct.unpack(">I", data[position:position + 4])[0]
        end, tag = position + 12 + size, data[position + 4:position + 8]
        if end > len(data):
            return False
        value = data[position + 8:end - 4]
        if zlib.crc32(tag + value) & 0xffffffff != struct.unpack(">I", data[end - 4:end])[0]:
            return False
        if count == 0 and (tag != b"IHDR" or size != 13 or not all(struct.unpack(">II", value[:8]))):
            return False
        if count and tag == b"IHDR":
            return False
        image = image or tag == b"IDAT" and size > 0
        if tag == b"IEND":
            return size == 0 and image and end == len(data)
        position, count = end, count + 1
    return False


def _jpeg(data):
    if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        return False
    pos, frame = 2, False
    while pos + 4 <= len(data):
        if data[pos] != 255:
            return False
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos + 3 > len(data):
            return False
        marker = data[pos]
        length = struct.unpack(">H", data[pos + 1:pos + 3])[0]
        if length < 2 or pos + 1 + length > len(data):
            return False
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
            if length < 8 or not all(struct.unpack(">HH", data[pos + 4:pos + 8])):
                return False
            frame = True
        if marker == 0xda:
            return frame and length >= 6  # Header structure only; provider decodes image in 2B.
        pos += 1 + length
    return False


def _xml(data):
    try:
        value = data.decode("utf-8-sig")
        if "\x00" in value or "<!DOCTYPE" in value.upper() or "<!ENTITY" in value.upper():
            raise ValueError()
        return ET.fromstring(value)
    except (UnicodeError, ValueError, ET.ParseError):
        raise _invalid("Небезопасная или повреждённая XML-структура 3MF; поддержан UTF-8 без DTD/entities")


def _three_mf(path):
    try:
        with zipfile.ZipFile(path, "r") as archive:
            entries, names, expanded, required = archive.infolist(), set(), 0, {}
            if len(entries) > ZIP_ENTRIES:
                raise UploadError(413, "archive_too_large", "В 3MF слишком много частей")
            for entry in entries:
                name = entry.filename
                if (name.startswith("/") or "\\" in name or ":" in name or
                        len(name) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in name) or
                        any(part in ("", ".", "..") for part in name.rstrip("/").split("/")) or
                        name.lower() in names or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1 or
                        entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise _invalid("Небезопасные или неподдержанные части ZIP/3MF")
                names.add(name.lower())
                expanded += entry.file_size
                if expanded > ZIP_BYTES:
                    raise UploadError(413, "archive_too_large", "Раскрываемый объём 3MF превышает 200 MiB")
                keep = name in ("[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model")
                if keep and entry.file_size > XML_BYTES:
                    raise UploadError(413, "archive_too_large", "Основная XML-часть 3MF превышает 16 MiB")
                chunks, actual = [], 0
                with archive.open(entry) as member:
                    while True:
                        chunk = member.read(65536)
                        if not chunk:
                            break
                        actual += len(chunk)
                        if actual > entry.file_size or actual > ZIP_BYTES:
                            raise UploadError(413, "archive_too_large", "Неподтверждённый раскрываемый объём 3MF")
                        if keep:
                            chunks.append(chunk)
                if actual != entry.file_size:
                    raise _invalid("Повреждённая часть 3MF")
                if keep:
                    required[name] = _xml(b"".join(chunks))
            if set(required) != {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}:
                raise _invalid("В 3MF отсутствуют основные OPC/model части")
            model = required["3D/3dmodel.model"]
            core = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
            if (model.tag != core + "model" or model.find(core + "resources") is None or model.find(core + "build") is None or
                    required["[Content_Types].xml"].tag.split("}")[-1] != "Types" or
                    required["_rels/.rels"].tag.split("}")[-1] != "Relationships"):
                raise _invalid("Не подтверждена базовая структура 3MF")
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, zlib.error):
        raise _invalid("Повреждённый или неподдерживаемый ZIP/3MF")


def _validate(path, extension, size):
    if extension == ".3mf":
        _three_mf(path)
        return
    valid = False
    with open(path, "rb") as stream:
        if extension in (".jpg", ".jpeg", ".png"):
            data = stream.read(IMAGE_BYTES + 1)
            valid = _png(data) if extension == ".png" else _jpeg(data)
        elif extension == ".stl":
            head = stream.read(65536)
            if len(head) >= 84:
                count = struct.unpack("<I", head[80:84])[0]
                valid = count > 0 and 84 + 50 * count == size
            if not valid:
                stream.seek(max(0, size - 4096))
                tail = stream.read(4096).lower()
                header = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
                valid = header.startswith(b"solid") and b"facet" in header and b"vertex" in header and b"endsolid" in tail
        elif extension == ".obj":
            vertex, primitive = False, False
            text = io.TextIOWrapper(stream, encoding="utf-8-sig", errors="replace", newline=None)
            while True:
                line = text.readline(1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 1024 * 1024 or "\x00" in line:
                    raise _invalid("Некорректная/слишком длинная текстовая запись OBJ")
                words = line.split(None, 1)
                if len(words) == 2:
                    vertex = vertex or words[0] == "v"
                    primitive = primitive or words[0] in ("f", "l", "p")
            valid = vertex and primitive
    if not valid:
        raise UploadError(415, "file_type_mismatch", "Содержимое не соответствует разрешённому расширению; окончательную геометрию проверяет STE")


class _Lease(object):
    def __init__(self, store, record):
        self.store, self.path, self.metadata, self.identity = store, record["path"], copy.deepcopy(record["metadata"]), record["identity"]
        self.released = False
    def __enter__(self):
        if self.released:
            raise UploadError(503, "file_lease_closed", "Вложение больше не удерживается")
        return self.path
    def __exit__(self, *args):
        self.close()
    def close(self):
        with self.store._condition:
            if not self.released:
                self.released = True
                self.store._end()


class FileStore(object):
    def __init__(self, total_limit=TOTAL_BYTES):
        self._condition = threading.Condition()
        self._upload_lock = threading.Lock()
        self._closed, self._users, self._used = False, 0, 0
        self._directory, self._directory_identity = None, None
        self._records, self._paths = {}, set()
        self._total_limit = total_limit
        self._cleanup_done = threading.Event()

    def _open(self):
        if self._closed:
            raise UploadError(503, "service_stopped", "Хранилище закрывается; новые операции запрещены")

    def _end(self):
        with self._condition:
            self._users -= 1
            self._condition.notify_all()

    def metadata(self, file_id):
        with self._condition:
            self._open()
            return copy.deepcopy(self._record(file_id)["metadata"])

    def _record(self, file_id):
        if not isinstance(file_id, str) or file_id not in self._records:
            raise UploadError(404, "file_not_found", "ID отсутствует в хранилище текущего процесса")
        return self._records[file_id]

    def _lease(self, file_id, kind):
        with self._condition:
            self._open()
            record = self._record(file_id)
            if record["metadata"]["kind"] != kind:
                raise UploadError(415, "file_type_mismatch", "Тип вложения не подходит для этой операции")
            self._users += 1
            lease = _Lease(self, record)
        try:
            info = os.lstat(lease.path)
            if (not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino, info.st_size) != lease.identity or
                    os.path.realpath(lease.path) != lease.path):
                raise UploadError(404, "file_unavailable", "Сохранённое вложение изменилось или недоступно")
            return lease
        except OSError:
            lease.close()
            raise UploadError(404, "file_unavailable", "Сохранённое вложение недоступно")
        except Exception:
            lease.close()
            raise

    def model_path(self, file_id):
        # Internal lease: 2B must hold it until native import actually ends, even after HTTP timeout.
        return self._lease(file_id, "model")

    def read_image(self, file_id):
        lease = self._lease(file_id, "image")
        try:
            with open(lease.path, "rb") as stream:
                data = stream.read(IMAGE_BYTES + 1)
            if len(data) != lease.metadata["size_bytes"] or len(data) > IMAGE_BYTES:
                raise UploadError(404, "file_unavailable", "Сохранённое изображение изменилось")
            return lease.metadata["mime_type"], data
        except OSError:
            raise UploadError(404, "file_unavailable", "Сохранённое изображение недоступно")
        finally:
            lease.close()

    def upload(self, stream, length, content_type):
        if not self._upload_lock.acquire(False):
            raise UploadError(409, "upload_busy", "Уже загружается файл; дождитесь результата")
        active, path, created, published = False, None, False, False
        try:
            with self._condition:
                self._open()
                self._users += 1
                active = True
            if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
                raise _invalid("Нужен положительный Content-Length")
            if length > MODEL_BYTES + WRAPPER_BYTES:
                raise UploadError(413, "payload_too_large", "Multipart-запрос превышает 50 MiB + 64 KiB")
            boundary = _boundary(content_type)
            name, extension, used = _headers(stream, length, boundary)
            kind, mime = TYPES[extension]
            limit = IMAGE_BYTES if kind == "image" else MODEL_BYTES
            if length - used > limit + WRAPPER_BYTES:
                raise UploadError(413, "payload_too_large", "Превышен размер вложения")
            if self._directory is None:
                self._directory = os.path.realpath(tempfile.mkdtemp(prefix="AIAssistant-uploads-"))
                info = os.lstat(self._directory)
                self._directory_identity = (info.st_dev, info.st_ino)
            path = os.path.join(self._directory, uuid.uuid4().hex + extension)
            size, tail, previous = 0, b"", b""
            marker, closing = b"\r\n--" + boundary, b"\r\n--" + boundary + b"--"
            def write(piece, target):
                nonlocal size, previous
                if marker in previous + piece:
                    raise _invalid("Дополнительные multipart-поля запрещены")
                previous = (previous + piece)[-(len(marker) - 1):]
                if size + len(piece) > limit or self._used + size + len(piece) > self._total_limit:
                    raise UploadError(413, "file_limit_exceeded", "Превышен лимит файла или суммарная квота 250 MiB")
                target.write(piece)
                size += len(piece)
            with open(path, "xb") as target:
                created = True
                self._paths.add(path)
                remaining = length - used
                while remaining:
                    chunk = _read(stream, min(65536, remaining))
                    if not chunk:
                        raise _invalid("Запрос оборван до Content-Length")
                    remaining -= len(chunk)
                    value = tail + chunk
                    count = max(0, len(value) - len(closing) - 2)
                    write(value[:count], target)
                    tail = value[count:]
                ending = closing + b"\r\n" if tail.endswith(closing + b"\r\n") else closing
                if not tail.endswith(ending) or used + len(ending) > WRAPPER_BYTES:
                    raise _invalid()
                write(tail[:-len(ending)], target)
            if size == 0:
                raise _invalid("Пустое вложение не поддерживается")
            _validate(path, extension, size)
            info = os.lstat(path)
            metadata = {"file_id": "file-" + uuid.uuid4().hex, "name": name, "kind": kind, "size_bytes": size, "mime_type": mime}
            with self._condition:
                self._open()
                if metadata["file_id"] in self._records:
                    raise UploadError(500, "upload_id_collision", "Не удалось назначить новый ID; прежнее вложение сохранено")
                self._records[metadata["file_id"]] = {"metadata": metadata, "path": path,
                    "identity": (info.st_dev, info.st_ino, info.st_size)}
                self._used += size
                published = True
            return dict(metadata)
        except OSError as exc:
            LOGGER.error("Upload storage: %s", type(exc).__name__)
            raise UploadError(500, "upload_storage_error", "Не удалось безопасно сохранить вложение")
        finally:
            if created and not published:
                self._unlink(path)
            if active:
                self._end()
            self._upload_lock.release()

    def _unlink(self, path):
        try:
            os.unlink(path)
            self._paths.discard(path)
        except FileNotFoundError:
            self._paths.discard(path)
        except OSError as exc:
            LOGGER.warning("Upload cleanup: %s", type(exc).__name__)

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            if self._users == 0 and self._directory is None:
                self._cleanup_done.set()
                return
        worker = threading.Thread(target=self._cleanup)
        worker.daemon = True
        try:
            worker.start()  # No filesystem work or waits on Extension's Qt thread.
        except RuntimeError as exc:
            LOGGER.error("Upload cleanup scheduling: %s", type(exc).__name__)
            self._cleanup_done.set()

    def _cleanup(self):
        try:
            with self._condition:
                while self._users:
                    self._condition.wait()
                self._records.clear()
            if self._directory is not None:
                info = os.lstat(self._directory)
                if (not stat.S_ISDIR(info.st_mode) or os.path.realpath(self._directory) != self._directory or
                        (info.st_dev, info.st_ino) != self._directory_identity):
                    LOGGER.error("Upload directory identity changed; cleanup refused")
                    return
                for path in list(self._paths):
                    if os.path.dirname(path) == self._directory:
                        self._unlink(path)
                os.rmdir(self._directory)  # Only own empty directory; never recursive deletion.
        except OSError as exc:
            LOGGER.warning("Upload directory cleanup: %s", type(exc).__name__)
        finally:
            self._cleanup_done.set()

    def wait_closed(self, timeout=None):
        # Test/worker helper, never called by Qt lifecycle.
        return self._cleanup_done.wait(timeout)
