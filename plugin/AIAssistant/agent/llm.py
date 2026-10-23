# -*- coding: utf-8 -*-
"""Chat Completions transport adapted from artem_backend b9c0190."""
import json
import logging
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import validate_config

MAX_RESPONSE_BYTES = 1024 * 1024
LOGGER = logging.getLogger(__name__)


class LLMError(Exception):
    def __init__(self, code, message, status_code=None):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def sanitize_secret(text, api_key=None):
    if text is None:
        return ""
    cleaned = str(text)
    if api_key:
        for value in (api_key, urllib.parse.quote(api_key, safe=""), urllib.parse.quote_plus(api_key),
                      json.dumps(api_key, ensure_ascii=False)[1:-1]):
            cleaned = cleaned.replace(value, "[REDACTED]")
    return re.sub(r"Bearer\s+[^\s\"']+", "Bearer [REDACTED]", cleaned, flags=re.I)


def mcp_tools_to_openai(mcp_tools, exclude_names=None):
    excluded = set(exclude_names or [])
    return [{"type": "function", "function": {"name": tool["name"],
             "description": tool.get("description", ""), "parameters": tool["inputSchema"]}}
            for tool in mcp_tools if tool["name"] not in excluded]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Не пересылаем Authorization другому origin.


class ChatCompletionsClient(object):
    def __init__(self, urlopen_fn=None):
        self._urlopen = urlopen_fn or self._open
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._abandon = threading.Event()

    @staticmethod
    def _open(request, timeout, context=None):
        opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=context))
        return opener.open(request, timeout=timeout)

    def close(self):
        self._closed.set()
        self._abandon.set()

    def complete(self, config_snapshot, messages, tools=None, timeout=30.0):
        try:
            validate_config(config_snapshot)
        except ValueError:
            raise LLMError("config_missing", "Некорректные настройки подключения LLM")
        if not all(config_snapshot.values()):
            raise LLMError("config_missing", "Задайте URL, модель и ключ LLM в Qt-меню")
        if self._closed.is_set():
            raise LLMError("service_stopped", "Клиент LLM закрыт")
        if timeout <= 0:
            raise LLMError("network_timeout", "Истёк бюджет сетевого вызова")
        if not self._lock.acquire(False):
            raise LLMError("request_busy", "Предыдущий сетевой вызов ещё завершается; не повторяйте автоматически")
        self._abandon.clear()
        timeout = min(30.0, timeout)
        deadline = time.monotonic() + timeout
        payload = {"model": config_snapshot["model"], "messages": messages, "stream": False}
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto"})
        base_url = config_snapshot["base_url"].rstrip("/")
        endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
        try:
            request = urllib.request.Request(endpoint, data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json",
                         "Authorization": "Bearer " + config_snapshot["api_key"]}, method="POST")
        except Exception:
            self._lock.release()
            raise LLMError("invalid_request", "Не удалось сформировать запрос LLM")
        done, values = threading.Event(), []

        def network():
            response = None
            try:
                kwargs = {"timeout": timeout}
                if urllib.parse.urlsplit(endpoint).scheme == "https":
                    kwargs["context"] = ssl.create_default_context()
                if self._closed.is_set() or self._abandon.is_set():
                    raise LLMError("service_stopped", "Запрос остановлен до обращения к провайдеру")
                response = self._urlopen(request, **kwargs)
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise LLMError("response_too_large", "Ответ LLM превышает 1 MiB")
                if not self._abandon.is_set():
                    values.append(self._parse(raw))
            except urllib.error.HTTPError as exc:
                values.append(LLMError("auth_error" if exc.code in (401, 403) else "http_error",
                                      "LLM вернула HTTP %s; проверьте настройки провайдера" % exc.code, exc.code))
                try:
                    exc.close()
                except Exception as close_exc:
                    LOGGER.warning("LLM HTTP cleanup: %s", type(close_exc).__name__)
            except ssl.SSLError:
                values.append(LLMError("tls_error", "Не удалось проверить TLS-сертификат LLM"))
            except LLMError as exc:
                values.append(exc)
            except Exception:
                values.append(LLMError("network_error", "Ошибка сети/ответа LLM; детали провайдера не передаются браузеру"))
            finally:
                try:
                    if response is not None:
                        response.close()
                except Exception as exc:
                    LOGGER.warning("LLM response cleanup: %s", type(exc).__name__)
                self._lock.release()
                done.set()
        worker = threading.Thread(target=network)
        worker.daemon = True
        try:
            worker.start()
        except Exception:
            self._lock.release()
            raise LLMError("request_failed", "Не удалось запустить сетевой вызов")
        if not done.wait(max(0, deadline - time.monotonic())):
            self._abandon.set()
            raise LLMError("network_timeout", "Сетевой бюджет истёк; запрос не повторяется автоматически")
        if self._closed.is_set() or self._abandon.is_set():
            raise LLMError("service_stopped", "Сетевой ответ после остановки не принят")
        if not values:
            raise LLMError("invalid_response", "Не получен ответ LLM")
        if isinstance(values[0], LLMError):
            raise values[0]
        return values[0]

    @staticmethod
    def _parse(raw):
        def reject(value):
            raise ValueError("Non-finite JSON")
        try:
            parsed = json.loads(raw.decode("utf-8"), parse_constant=reject)
            choices = parsed["choices"]
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError()
            if choices[0].get("finish_reason") in ("length", "content_filter"):
                raise LLMError("incomplete_response", "Ответ LLM не завершён; инструменты этого ответа не выполняются")
            message = choices[0]["message"]
            if not isinstance(message, dict):
                raise ValueError()
            return message
        except LLMError:
            raise
        except Exception:
            raise LLMError("invalid_response", "Некорректный JSON/choices/message в ответе LLM")
