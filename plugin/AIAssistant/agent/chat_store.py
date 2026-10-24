"""Bounded local chat archive. All disk access belongs to one worker."""
import copy
import json
import logging
import os
import queue
import re
import threading
import uuid

from .llm import sanitize_secret

MAX_CHATS, MAX_MESSAGES = 20, 100
CHAT_BYTES, TOTAL_BYTES = 512 * 1024, 10 * 1024 * 1024
ID = re.compile(r"\Asession_[0-9a-f]{32}\Z")
LOGGER = logging.getLogger(__name__)


class HistoryError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def clean_text(value, secret=""):
    value = sanitize_secret(value, secret)
    value = re.sub(r"data:[^\s]*", "[вложение не сохранено]", value, flags=re.I)
    value = re.sub(r"[A-Za-z0-9+/]{160,}={0,2}", "[двоичные данные не сохранены]", value)
    value = re.sub(r"(?:https?|file)://[^\s)\]>]+", "[историческая ссылка]", value, flags=re.I)
    value = re.sub(r"(?:[A-Za-z]:[\\/]|\\\\|(?<!\w)/(?:[^/\s]+/)+)[^\s\"']+", "[локальный путь]", value)
    value = re.sub(r"\b(?:model|object|slice|export|artifact|file|download|arrangement|call|client|session)[_-][A-Za-z0-9_-]+", "[исторический ID]", value)
    return value[:12000]


def document(snapshot, secret):
    messages = []
    original = snapshot.get("messages", [])
    for index, item in enumerate(original[-MAX_MESSAGES:]):
        messages.append({"message_id": "history_%d" % index, "role": item["role"],
                         "text": clean_text(item["text"], secret), "created_at": item["created_at"],
                         "is_error": bool(item.get("is_error")), "historical": True,
                         "event_id": index + 1,
                         "attachments": [{"name": clean_text(a.get("name", "Вложение"), secret)[:200],
                                          "expired": True} for a in item.get("attachments", [])[:5]]})
    return {"version": 1, "session_id": snapshot["session_id"], "revision": snapshot["revision"],
            "title": clean_text(snapshot["title"], secret)[:80], "created_at": snapshot["created_at"],
            "updated_at": snapshot["updated_at"], "messages": messages,
            "history_truncated": bool(snapshot.get("history_truncated") or len(original) > MAX_MESSAGES),
            "interrupted": bool(snapshot.get("interrupted"))}


def encoded(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def validated(data, sid):
    fields = {"version", "session_id", "revision", "title", "created_at", "updated_at", "messages", "history_truncated", "interrupted"}
    if not isinstance(data, dict) or set(data) != fields or data["version"] != 1 or isinstance(data["version"], bool) or data["session_id"] != sid:
        raise ValueError("Invalid chat format")
    if isinstance(data["revision"], bool) or not isinstance(data["revision"], int) or data["revision"] < 1:
        raise ValueError("Invalid revision")
    if any(not isinstance(data[k], str) or len(data[k]) > limit for k, limit in (("title", 80), ("created_at", 40), ("updated_at", 40))):
        raise ValueError("Invalid chat metadata")
    if not isinstance(data["messages"], list) or len(data["messages"]) > MAX_MESSAGES or any(not isinstance(data[k], bool) for k in ("history_truncated", "interrupted")):
        raise ValueError("Invalid history limits")
    for item in data["messages"]:
        if (not isinstance(item, dict) or set(item) != {"message_id", "role", "text", "created_at", "is_error", "historical", "event_id", "attachments"} or
                item["role"] not in ("user", "assistant", "note") or not isinstance(item["text"], str) or len(item["text"]) > 12000 or
                not isinstance(item["created_at"], str) or len(item["created_at"]) > 40 or
                not isinstance(item["message_id"], str) or len(item["message_id"]) > 80 or
                not isinstance(item["is_error"], bool) or item["historical"] is not True or
                isinstance(item["event_id"], bool) or not isinstance(item["event_id"], int) or
                not isinstance(item["attachments"], list) or len(item["attachments"]) > 5):
            raise ValueError("Invalid historical message")
        for attachment in item["attachments"]:
            if (not isinstance(attachment, dict) or set(attachment) != {"name", "expired"} or
                    not isinstance(attachment["name"], str) or len(attachment["name"]) > 200 or attachment["expired"] is not True):
                raise ValueError("Invalid historical attachment")
    return data


class ChatStore(object):
    def __init__(self, directory):
        self.directory = directory
        self._lock, self._queue = threading.Lock(), queue.Queue()
        self.ready, self.done = threading.Event(), threading.Event()
        self._entries, self._states, self._wanted, self._sizes = {}, {}, {}, {}
        self._deleted, self._pending = set(), {}
        self._closing, self._error = False, None
        self._thread = threading.Thread(target=self._run, name="AIChatArchive")
        self._thread.daemon = True
        self._thread.start()

    @staticmethod
    def check_id(sid):
        if not isinstance(sid, str) or not ID.match(sid):
            raise HistoryError("history_not_found", "Сохранённый диалог не найден")

    def listing(self):
        with self._lock:
            return {"ready": self.ready.is_set(), "error": self._error,
                    "sessions": copy.deepcopy(list(self._entries.values())) if self.ready.is_set() else []}

    def state(self, sid, revision):
        with self._lock:
            state = dict(self._states.get(sid, {"state": "saving", "saved_revision": 0, "error": None}))
            if state["state"] == "saved" and state["saved_revision"] < revision:
                state["state"] = "saving"
            return state

    def save(self, snapshot, secret=""):
        sid, revision = snapshot["session_id"], snapshot["revision"]
        self.check_id(sid)
        with self._lock:
            if self._closing or sid in self._deleted:
                return
            if revision <= self._wanted.get(sid, 0):
                return
            if sid not in self._wanted and len(self._wanted) >= MAX_CHATS:
                raise HistoryError("history_limit", "Достигнут лимит20 сохранённых диалогов; удалите ненужный")
            self._wanted[sid] = revision
            self._states[sid] = {"state": "saving", "saved_revision": self._states.get(sid, {}).get("saved_revision", 0), "error": None}
            # At most one queued snapshot per chat; a newer revision replaces it.
            first = sid not in self._pending
            self._pending[sid] = (snapshot, secret)
            if first:
                self._queue.put(("save", sid, None))

    def _request(self, action, sid):
        self.check_id(sid)
        request = {"done": threading.Event()}
        with self._lock:
            if self._closing:
                raise HistoryError("history_stopped", "Хранилище закрывается")
            if action == "delete":
                self._deleted.add(sid)
                self._pending.pop(sid, None)
            self._queue.put((action, sid, request))
        if not request["done"].wait(5):
            raise HistoryError("history_timeout", "Исход файловой операции не подтверждён; перечитайте список, не повторяйте автоматически")
        if "error" in request:
            raise request["error"]
        return request.get("data")

    def load(self, sid):
        return self._request("load", sid)

    def delete(self, sid):
        return self._request("delete", sid)

    def _path(self, sid):
        self.check_id(sid)
        return os.path.join(self.directory, sid + ".json")

    def _read(self, sid):
        path = self._path(sid)
        if os.path.islink(path):
            raise ValueError("Linked archive rejected")
        with open(path, "rb") as stream:
            raw = stream.read(CHAT_BYTES + 1)
        if len(raw) > CHAT_BYTES:
            raise ValueError("Chat too large")
        data = validated(json.loads(raw.decode("utf-8")), sid)
        return data, len(raw)

    @staticmethod
    def _entry(data):
        return {k: data[k] for k in ("session_id", "title", "created_at", "updated_at", "revision", "history_truncated", "interrupted")}

    def _atomic(self, path, raw):
        temporary = path + ".tmp-" + uuid.uuid4().hex
        try:
            with open(temporary, "xb") as stream:
                stream.write(raw)
                stream.flush()
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def _index(self):
        with self._lock:
            entries = copy.deepcopy(list(self._entries.values()))
        raw = encoded({"version": 1, "sessions": entries})
        if sum(self._sizes.values()) + len(raw) > TOTAL_BYTES:
            raise HistoryError("history_limit", "Архив превышает10МиБ; удалите ненужный диалог")
        self._atomic(os.path.join(self.directory, "index.json"), raw)

    def _usage(self):
        return sum(os.path.getsize(os.path.join(self.directory, name)) for name in os.listdir(self.directory)
                   if os.path.isfile(os.path.join(self.directory, name)))

    def _scan(self):
        os.makedirs(self.directory, exist_ok=True)
        errors = []
        for name in os.listdir(self.directory):
            if not name.endswith(".json") or not ID.match(name[:-5]):
                continue
            sid = name[:-5]
            if len(self._entries) >= MAX_CHATS:
                errors.append("В папке больше20 диалогов; остальные файлы сохранены, новые записи ограничены квотой")
                break
            try:
                data, size = self._read(sid)
                if len(self._entries) >= MAX_CHATS:
                    raise ValueError("Too many archived chats")
                self._entries[sid], self._sizes[sid] = self._entry(data), size
                self._wanted[sid] = data["revision"]
                self._states[sid] = {"state": "saved", "saved_revision": data["revision"], "error": None}
            except (OSError, ValueError, TypeError, RecursionError):
                # Preserve unreadable files; reserve their space and ID.
                self._sizes[sid] = os.path.getsize(self._path(sid))
                self._wanted[sid] = 0
                self._entries[sid] = {"session_id": sid, "title": "Повреждённый диалог", "created_at": "", "updated_at": "",
                                      "revision": 0, "history_truncated": True, "interrupted": False, "corrupt": True}
                errors.append("Есть повреждённый/неподдерживаемый диалог; файл сохранён без изменений")
        if sum(self._sizes.values()) > TOTAL_BYTES:
            errors.append("Существующий архив превышает10МиБ")
        index = os.path.join(self.directory, "index.json")
        if os.path.exists(index):
            try:
                if os.path.islink(index):
                    raise ValueError("Linked index rejected")
                with open(index, "rb") as stream:
                    raw = stream.read(CHAT_BYTES + 1)
                value = json.loads(raw.decode("utf-8"))
                if len(raw) > CHAT_BYTES or not isinstance(value, dict) or value.get("version") != 1 or isinstance(value.get("version"), bool) or not isinstance(value.get("sessions"), list):
                    raise ValueError("Invalid index")
            except (OSError, ValueError, TypeError, RecursionError):
                # Index is a cache; preserve a corrupt copy before rebuilding it.
                os.replace(index, index + ".corrupt-" + uuid.uuid4().hex)
                errors.append("Индекс повреждён; список восстановлен из файлов, исходный индекс сохранён отдельно")
        if errors:
            self._error = "; ".join(sorted(set(errors)))

    def _write(self, sid):
        with self._lock:
            pending = self._pending.pop(sid, None)
        if pending is None:
            return
        snapshot, secret = pending
        data = document(snapshot, secret)
        raw = encoded(data)
        if len(raw) > CHAT_BYTES or self._usage() - self._sizes.get(sid, 0) + len(raw) + 16384 > TOTAL_BYTES:
            raise HistoryError("history_limit", "Лимит архива:512КиБ на диалог/10МиБ суммарно; последняя версия не сохранена")
        with self._lock:
            if sid in self._deleted or data["revision"] != self._wanted[sid]:
                return
        self._atomic(self._path(sid), raw)
        self._sizes[sid] = len(raw)
        with self._lock:
            self._entries[sid] = self._entry(data)
        self._index()
        with self._lock:
            if sid not in self._deleted and data["revision"] == self._wanted[sid]:
                self._states[sid] = {"state": "saved", "saved_revision": data["revision"], "error": None}

    def _run(self):
        try:
            self._scan()
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            self._error = "Хранилище недоступно; сохранение не подтверждено (%s)" % type(exc).__name__
        finally:
            self.ready.set()
        try:
            while True:
                action, sid, request = self._queue.get()
                if action == "close":
                    break
                try:
                    if action == "save":
                        self._write(sid)
                    elif action == "load":
                        request["data"] = document(self._read(sid)[0], "")
                    elif action == "delete":
                        path = self._path(sid)
                        if os.path.lexists(path):
                            os.remove(path)
                        with self._lock:
                            self._entries.pop(sid, None)
                        self._sizes.pop(sid, None)
                        with self._lock:
                            self._wanted.pop(sid, None)
                        self._index()
                        with self._lock:
                            self._states[sid] = {"state": "deleted", "saved_revision": 0, "error": None}
                        request["data"] = {"deleted": True}
                except (OSError, ValueError, TypeError, RecursionError, HistoryError) as exc:
                    error = exc if isinstance(exc, HistoryError) else HistoryError("history_io_error", "Файловая операция не подтверждена; файл не заменяется пустой историей")
                    with self._lock:
                        self._states[sid] = {"state": "error", "saved_revision": self._states.get(sid, {}).get("saved_revision", 0), "error": error.message}
                    if request is not None:
                        request["error"] = error
                finally:
                    if request is not None:
                        request["done"].set()
        finally:
            self.done.set()

    def close(self):
        with self._lock:
            if self._closing:
                return
            self._closing = True
            self._queue.put(("close", None, None))
        # Qt never joins. A finite non-daemon helper gives ordinary process exit
        # a flush window; forced OS exit and blocked disk I/O remain unconfirmed.
        def finish():
            if not self.done.wait(2):
                LOGGER.error("Chat archive close: final revision unconfirmed")
        closer = threading.Thread(target=finish, name="AIChatClose")
        closer.daemon = False
        closer.start()

    def wait_closed(self, timeout=2):
        return self.done.wait(timeout)
