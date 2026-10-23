# -*- coding: utf-8 -*-
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request


class LLMError(Exception):
    def __init__(self, code, message, status_code=None):
        super(LLMError, self).__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def sanitize_secret(text, api_key=None):
    if not text:
        return ""
    cleaned = str(text)
    if api_key and len(api_key) >= 4:
        cleaned = cleaned.replace(api_key, "[REDACTED]")
    cleaned = re.sub(r"Bearer\s+[A-Za-z0-9_\-\.]+", "Bearer [REDACTED]", cleaned)
    return cleaned


def mcp_tools_to_openai(mcp_tools, exclude_names=None):
    excluded = set(exclude_names or [])
    openai_tools = []
    for tool in mcp_tools:
        name = tool.get("name")
        if not name or name in excluded:
            continue
        schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
        openai_tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool.get("description", ""),
                "parameters": schema
            }
        })
    return openai_tools


class ChatCompletionsClient(object):
    def __init__(self, urlopen_fn=None):
        self._urlopen = urlopen_fn or urllib.request.urlopen

    def complete(self, config_snapshot, messages, tools=None, timeout=30.0):
        base_url = (config_snapshot.get("base_url") or "").strip().rstrip("/")
        api_key = (config_snapshot.get("api_key") or "").strip()
        model = (config_snapshot.get("model") or "").strip()

        if not base_url:
            raise LLMError("config_missing", "Не указан Base URL для подключения к LLM.")
        if not model:
            raise LLMError("config_missing", "Не указано имя модели LLM.")
        if not api_key:
            raise LLMError("api_key_missing", "Не задан API-ключ LLM. Откройте настройки плагина и введите ключ.")

        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise LLMError("invalid_url", "Некорректный формат Base URL: %s" % base_url)

        if base_url.endswith("/chat/completions"):
            endpoint = base_url
        else:
            endpoint = base_url + "/chat/completions"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "Authorization": "Bearer " + api_key,
            "User-Agent": "STESlicer-AIAssistant/1.0"
        }

        req = urllib.request.Request(endpoint, data=body_bytes, headers=headers, method="POST")
        ssl_ctx = ssl.create_default_context() if parsed.scheme == "https" else None

        try:
            if ssl_ctx is not None:
                resp = self._urlopen(req, timeout=float(timeout), context=ssl_ctx)
            else:
                resp = self._urlopen(req, timeout=float(timeout))
            raw_resp = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            safe_detail = sanitize_secret(err_body[:300], api_key)
            if e.code in (401, 403):
                raise LLMError(
                    "auth_error",
                    "Ошибка авторизации LLM (HTTP %d). Проверьте API-ключ и права доступа." % e.code,
                    status_code=e.code
                )
            raise LLMError(
                "http_error",
                "Сервер LLM вернул ошибку HTTP %d: %s" % (e.code, safe_detail or e.reason),
                status_code=e.code
            )
        except ssl.SSLError as e:
            raise LLMError(
                "tls_error",
                "Ошибка проверки TLS-сертификата при подключении к LLM: %s" % sanitize_secret(e, api_key)
            )
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            raise LLMError(
                "network_error",
                "Ошибка сети или таймаут при обращении к LLM: %s" % sanitize_secret(reason, api_key)
            )
        except Exception as e:
            raise LLMError(
                "request_failed",
                "Сбой при обращении к LLM: %s" % sanitize_secret(e, api_key)
            )

        try:
            parsed_json = json.loads(raw_resp)
        except Exception:
            raise LLMError("invalid_json", "Сервер LLM вернул некорректный JSON.")

        choices = parsed_json.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError("empty_choices", "Ответ LLM не содержит ни одного варианта (choices).")

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise LLMError("invalid_message", "В ответе LLM отсутствует объект message.")

        return message