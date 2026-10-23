# Клиент агентной интеграции b9c0190

Авторитетный [API_CONTRACT.md](API_CONTRACT.md) описывает границу целиком. Рабочая SPA: `plugin/AIAssistant/web/`, раздаваемые файлы — только index.html, app.js, style.css. Используйте относительные URL того же origin; сторонний localhost-порт запрещён Host/Origin. CSP допускает только локальные внешние ресурсы. Эта интеграция проверена offline; native/реальный провайдер ещё открыты.

1. `GET /api/status` → 200. `llm:{configured,has_key}` означает только заполненные подтверждённые Preferences. Не писать «подключено». URL/модель/ключ вводятся в Qt-меню AI Assistant → LLM Settings; REST-настроек/секретов нет. `agent_state` общий для сцены; capabilities attachments/downloads/background_slice_events — false.
2. `POST /api/sessions` с `{}` → 201 `{session_id,messages,agent_state,last_event_id,slice,artifacts,tool_events}`. Сохранить session_id. После обновления вкладки `GET /api/sessions/{id}` восстанавливает обоих участников. Объединять messages.event_id и tool_events.id для ленты; продолжить с snapshot.last_event_id. 404 после перезапуска процесса означает потерю памяти истории, не разрешение повторить изменения.
3. Перед отправкой сохранить тело и client_message_id. `POST /api/sessions/{id}/messages` с `{"text":"Прочитай высоту слоя","client_message_id":"client-unique","attachment_ids":[]}` → 202 `{"session_id":"...","message_id":"...","client_message_id":"...","agent_state":"processing"}`. Это принятие. Один turn для всей сцены; параллельный запрос → 409 agent_busy. Одинаковые ID и принятое тело → прежний 202, изменённое тело → 409 message_id_conflict. При обрыве/5xx сохранять ID и тело, читать snapshot; повторять только явно с тем же ID. Не создавать автоматически новый ID. User брать из snapshot, не рисовать второй оптимистичный экземпляр.
4. `GET /api/events?session_id=...&after=0` → 200 `{"session_id":"...","events":[{"id":1,"type":"agent_state","created_at":"...Z","data":{"state":"processing"}}],"next_after":1,"has_more":false}`. Polling около 1 секунды, только один незавершённый запрос; при сбое backoff. События дедуплицировать по id, has_more читать сразу. 410 → snapshot и last_event_id; некорректный/будущий курсор → 400.

События для клиента:

| type | data и смысл |
| --- | --- |
| tool_started | `{turn_id,tool_call_id,name,arguments}` — проверенные аргументы |
| tool_finished | `{turn_id,tool_call_id,name,result}` — фактический `{ok,data}`/`{ok:false,error}`; возможен без started при отказе валидации |
| assistant_message | `{message_id,role,text,attachment_ids,created_at,is_error,event_id}` — финальный ответ/ошибка |
| agent_state | `{state:idle/processing}` — состояние turn, отдельно от engine |
| slice_progress / slice_finished | `{slice_id,state,progress,valid,error,observed_at,result}` — последний реально прочитанный tool-снимок |
| artifact_ready | `{artifact:{artifact_id,file_name,kind,size_bytes,slice_id,source,writer_id,writer_type,valid}}` — локальный файл, без download_url |
| error | `{code,message,related_id}` — ошибка обработки, related_id может быть null |

Карточка tool определяется парой turn_id + tool_call_id: в следующем turn тот же call ID может появиться заново. Результаты/текст выводить через textContent, никогда как HTML. Секреты не запрашивать и не хранить в SPA. Неподтверждённый результат мутации прекращает turn; не повторять инструменты на клиенте.

Слайсинг не наблюдается фоновым worker: slice/progress/result — снимок из выполненного tool. При idle/лимите агента engine может продолжать работу. Писать «Последний снимок», оставлять running, предлагать явно прочитать статус по slice_id. completed + valid:false показывать устаревшим; progress:null — неизвестная доля. Пример result: `{"slice_id":"slice-1","printing_mode":"classic","backend_id":"CuraEngineBackend","build_plate":0,"print_time_seconds":3600,"material":{"length_mm":12000,"weight_g":36},"source":"ste_slicer"}`. Это суммарные оценки STE, не собственный расчёт и не массив по экструдерам.

Реализованный текст — 1–12000 символов, ID до 128; attachment_ids только []. Новые REST-ошибки: `{"error":{"code":"agent_busy","message":"..."}}`. Legacy `/api/message` — отдельное демо, не использовать в новом UI; `/api/health` — только liveness. MCP-схемы остаются едиными, диагностический load_model(path) не передаётся LLM. HTTP files/settings/downloads, upload, преобразования, custom-профили и 5D-нарезка отсутствуют. Dev без STE не создаёт фиктивную машину и не вызывает провайдера при старте.
