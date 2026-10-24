# Роль 2B — вложения сообщения и импорт через агента

Выполнено только `docs/tasks/ROLE_02B_AGENT_ATTACHMENTS.md` после принятой2A. Git status проверен; существующие изменения08/09/черновик10, документы ведущего и Word locks сохранены. Работа передаётся на ревью; возвращение к10 требует отдельного назначения. Установка/реальный провайдер/печать/Git-фиксации не выполнялись.

## Что изменено

- `agent/service.py`: до принятия turn проверяются до5 уникальных file_id, типы/реестр и суммарные изображения до10MiB. Сохранены прежние текст/бюджеты/один turn/идемпотентность. Сравнивается полный упорядоченный массив ID; тот же принятый client_message_id+тело возвращает прежний202, изменённое тело409, без повторного импорта. User snapshot содержит attachment_ids и безопасные attachments-метаданные без путей/байтов.
- JPG/PNG передаются из сохранённых байтов в user content Chat Completions как настоящий base64 image_url с фактическим MIME. Модели — только метаданными. Приватная история хранит ссылки-ID, не копии base64; изображения материализуются вне Qt перед complete и не попадают в snapshot/events. Файлы принятых сессий остаются в ограниченном хранилище до закрытия сервиса, раннего удаления/TTL нет. Ошибка vision/HTTP провайдера сохраняется как ошибка, без выдуманного анализа или fallback.
- `agent/tools.py`: `load_model_file {file_id}` добавлен в единый MCP/LLM ToolRegistry как мутация. Только собственная зарегистрированная модель разрешается в доверенный путь вне Qt; unknown/wrong-kind не вызывает API. Используется прежний adapter.load_model и QtBridge, результат содержит подтверждённые objects, исходное file_name и file_id. Raw-path load_model по-прежнему скрыт от LLM. Ошибка/неподтверждённый импорт прекращает turn; последующие tools не выполняются.
- `web/index.html/app.js/style.css`: минимальный picker, последовательный upload, имена/состояние/ошибки и удаление из списка до отправки. Отправка блокируется до завершения загрузки/устранения ошибки. Pending сохраняет прежние attachment_ids, текст/client_message_id и имена; явный повтор после refresh использует прежнее тело без нового upload. Принятые имена восстанавливаются из snapshot через textContent. Редизайна/внешней сборки нет.
- `/api/status`: attachments=true, downloads/background_slice_events=false; реальные лимиты5/50MiB/5MiB/10MiB и расширения. JSON64KiB, Host/Origin/loopback/CSP/static whitelist сохранены. HTTP-скачивание/раздача локальных путей не добавлялись.

## Граница reader lifetime

Согласована с ведущим до изменения загрузчика. В существующих `slicer/adapter.py` и `models.py` добавлен только внутренний Python callback reader_done, недоступный MCP/LLM. Незапущенный pending/ранний отказ освобождает lease один раз. Перед readLocalFile отдельная подписка наблюдает **свой** fileCompleted; сопоставление по доверенному пути, подписка/снятие на Qt. ModelLoad.finish/_release/ModelRegistry.close не освобождают lease начатого либо неопределённого reader. Timeout/shutdown/закрытый QtBridge не считаются завершением. Поздний собственный сигнал освобождает lease, а файловое удаление делает worker FileStore; lease.close лишь меняет защищённый счётчик.

Основание — выданный `steslicer/SteSlicerApplication.py`: readLocalFile, _readMeshFinished, fileLoaded около1554/fileCompleted около1642. Некоторые ранние выходы _readMeshFinished не испускают fileCompleted. Если сигнал так и не придёт, файл удерживается консервативно и может остаться на диске после выхода процесса; отмена job/доказательство завершения не имитируются. Второго загрузчика или QtBridge нет, обычный load_model(path) без callback сохранён.

## Проверки

- `python -m unittest discover -s tests -p test_attachments.py -v`: **10 тестов OK, 1.296с**. Реальные store/HTTP/реестр/QtBridge с fake reader/provider: точные PNG/JPEG base64, модель только метаданными, приватность snapshot/истории, лимиты/unknown/wrong-kind, ordered duplicate/conflict, честный vision HTTP400, запрет reader_done в MCP, остановка batch после native model_load_failed. Отдельно проверены ранний pending/отказ/dev без bridge, launched exception и timeout → service/store/registry close → файл остаётся → чужой сигнал не освобождает → поздний свой сигнал очищает.
- Интеграционный тест выполняет настоящий localhost POST /api/files → POST сообщения → scripted LLM tool → прежний adapter/Qt → fake fileLoaded/fileCompleted → подтверждённый result. Повтор того же HTTP-сообщения даёт прежний202 и ровно один readLocalFile. Это подмена native API, а не реальный импорт STE.
- Итоговый `python -m unittest discover -s tests -q`: **241 тест OK, 15.501с** — прежние231 сохранены плюс10 новых. Ожидания списка tools и attachments capability/ошибки неизвестного ID актуализированы. Первый полный241 выявил ещё одно старое ожидание списка без load_model_file; список дополнен, проверки не удалены и не ослаблены. Старого mode timing-отказа в2B не наблюдалось.
- `node --check plugin/AIAssistant/web/app.js`; `node tests/test_agent_web.js`: **8 offline DOM-сценариев OK** — прежние5 плюс незавершённый/последовательный upload, удаление/ошибка файла, pending IDs/refresh/явный exact retry/восстановление имён. Это не визуальная/native-приёмка.
- Грамматика Python3.5 для35 Python-файлов и plugin.json api5 — OK на отдельном Python; `git diff --check` чистый. Новых зависимостей нет.

Первая попытка новых файловых тестов в sandbox получила PermissionError его временного каталога; разрешённый прогон вне sandbox выполнил их успешно. Реальные внешние адреса и ключи не использовались; HTTP400 получен от fake transport, payload проверен до его отказа. Проверка агрегатных10MiB использует уменьшенные фикстуры/подмену размеров метаданных до чтения, не нагрузочный vision-запрос.

## Ручная демонстрация — не выполнялась

После отдельно согласованного обновления установленной копии и настройки vision-провайдера: открыть чат, выбрать небольшой STL и PNG/JPG, дождаться обоих upload, отправить «Загрузи эту модель и объясни фото». Сверить карточку load_model_file/подтверждённые objects с объектом/габаритами в STE и реальным содержимым фото. Обновить страницу: имена и история восстанавливаются, импорт сам не повторяется. Убрать выбранный файл до отправки; неверный файл должен показать ошибку и блокировать отправку до удаления. При timeout/ошибке сверить list_models/UI, не повторять импорт автоматически.

Из PowerShell можно проверить ту же форму сообщения с заранее загруженным по инструкции plugin/README.md file_id (команды здесь не запускались):

```powershell
$chatPort = [int](Read-Host 'Текущий порт чата')
$chatBase = "http://127.0.0.1:$chatPort"
$modelFileId = Read-Host 'file_id успешного upload модели'
$session = Invoke-RestMethod "$chatBase/api/sessions" -Method Post -ContentType 'application/json' -Body '{}'
$messageBody = @{text='Загрузи только приложенную модель'; attachment_ids=@($modelFileId); client_message_id=[guid]::NewGuid().ToString()} | ConvertTo-Json -Compress
$messageBytes = [Text.Encoding]::UTF8.GetBytes($messageBody)
Invoke-RestMethod "$chatBase/api/sessions/$($session.session_id)/messages" -Method Post -ContentType 'application/json; charset=utf-8' -Body $messageBytes
# Это202, не завершение. Перечитать snapshot; сообщение автоматически не повторять.
Invoke-RestMethod "$chatBase/api/sessions/$($session.session_id)" | ConvertTo-Json -Depth 30
```

## Оставшиеся ограничения

Фотоанализ и объект в установленном STE не подтверждены; требуется vision-совместимая модель провайдера. Структурные проверки2A не являются полной image/mesh-декодировкой. Файлы/история существуют в памяти процесса до закрытия; неизвестные ID после перезапуска404, removed-from-picker файлы остаются в процессной квоте до close. При отсутствии native fileCompleted возможен временный остаток. Сцена может показывать серверное имя файла, переименование не добавлено. Текст ответа/201/202 не является доказательством импорта.

Ручная приёмка ТЗ03/04 и других native-сценариев остаётся открыта. ТЗ10/cli_slicing.py и его черновые hooks не продолжались; пройденная регрессия не подтверждает5D. Downloads, экспортные resolver, профили и фоновой мониторинг не добавлены. API_CONTRACT/FRONTEND_INTEGRATION/README обновлены по факту. После отчёта исполнитель остановлен для ревью.
