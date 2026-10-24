# Роль2C — скачивание native-экспорта

24.10.2026. Реализован назначенный объём [ROLE_02C_NATIVE_DOWNLOADS.md](../tasks/ROLE_02C_NATIVE_DOWNLOADS.md), передаётся на ревью. Git status прочитан до работы; существующие изменения сохранены. Рабочая поставка одна: plugin/AIAssistant. Установленная копия/реальный провайдер/движки/принтер не запускались и не обновлялись. Commit/push/merge не выполнялись. Следующие этапы не начаты.

## Что изменено

- `slicer/exports.py`: после штатного writer и проверенной публикации FS-worker фиксирует канонический путь и immutable dev/ino/size/mtime_ns. Внутренний resolve_artifact проверяет accepted completed record и прежний slicing.result для того же slice_id; только Qt/readback, без файлового I/O. Accepted-файлы сохраняют прежний lifecycle.
- `slicer/adapter.py`: маленький внутренний resolver через существующий QtBridge/app.callLater, без нового MCP/LLM tool и доступа HTTP к adapter._exports. Native-путь/identity не публикуются в MCP/REST/событиях; прежний диагностический local_directory остаётся только прямому MCP.
- Новый `downloads.py`: отдельный DownloadRegistry. Register/open получают канонические метаданные только из resolver; upload store не используется. Вне Qt lstat/realpath/open/fstat сверяют обычный файл, принятую идентичность и размер. Один artifact_id получает стабильный download-ID; повторный status не создаёт новый ID. UUID-коллизия не перезаписывает прежнюю запись. Короткие внутренние lock не охватывают Qt/файловое ожидание. Close запрещает регистрацию/open и очищает только записи, accepted native-файлы не удаляет.
- `agent/service.py`: после реально выполненного агентом get_export_status с completed/valid=true регистрирует файл **до входа в ServiceLock**, дополняет artifact_ready/snapshot file_id/download_url. Повтор одинакового результата дедуплицируется; обновление заменяет прежнюю карточку, включая удаление старого URL при отказе. Известные мутации/прочитанный stale отражают valid=false. Ошибка регистрации оставляет безопасный download_error без ссылки. Прямой MCP tools/call сам карточку/URL не регистрирует; нового register endpoint нет.
- `server.py`: GET `/api/files/{file_id}` только для зарегистрированного экспорта, без query/пути клиента; повторный Qt resolver, открытие вне Qt, Content-Length/Content-Type/attachment filename* UTF-8/no-store/nosniff/CSP. Поток читает максимум64KiB за раз без ServiceLock/engine guard; весь файл в память не загружается. Свой handle закрывается и при обрыве. Host/Origin/loopback/static allowlist/JSON64KiB/POST upload лимиты сохранены.
- `web/index.html`, `web/app.js`: минимальные карточки артефактов, безопасное textContent и href только своего маршрута при valid===true. Исторический результат без URL остаётся локальным; известный stale скрывает ссылку. Обновление по artifact_id, восстановление snapshot/410 без дубликатов. Ссылка открывается в новой вкладке (noopener); успешный Content-Disposition скачивает файл, JSON ошибки сохраняет основной чат. Picker/pending/client_message_id/cursors прежние, редизайна нет.
- Новые `tests/test_downloads.py`, два дополнительных сценария `tests/test_agent_web.js`; обновлены API_CONTRACT, FRONTEND_INTEGRATION, README и plugin/README с самостоятельными командами. PLAN/COORDINATION и чужие файлы не переписывались.

Resolver/регистрация/GET/close согласованы с ведущим до реализации. Tools/slice/export pipelines, native writers, три режима и custom profiles сохранены. capabilities.downloads=true при native bridge, в dev=false; background_slice_events=false. Флаг описывает реализованный путь, не готовность engine/активации/лицензии.

## HTTP-контракт и lifecycle

Artifact сохраняет artifact_id/file_name/kind/size_bytes/slice_id/source/writer_id/writer_type/valid, после регистрации получает file_id и относительный `/api/files/{file_id}`. Upload-ID не является ID скачивания. Каждая попытка GET до заголовков заново проверяет native-актуальность и файл. Неизвестный/upload/исчезнувший ID —404 file_not_found; неподтверждённые метаданные —404 artifact_unavailable; stale —409 artifact_stale; заменённый/изменённый файл —409 artifact_changed; Qt недоступен —503 slicer_unavailable; закрытие —503 service_stopped; общий отказ до заголовков —503 download_unavailable. Регистрация при коллизии ID —503 download_id_conflict. Ошибка — безопасный `{error:{code,message}}`, без путей/ключей. После начала бинарного тела ошибка обрывает соединение без JSON. Никаких автоматических повторов экспорта.

G-code: application/octet-stream; project: model/3mf. Оба Content-Length и Content-Disposition attachment с percent-encoded filename*, no-store/nosniff и прежним CSP. CRLF/сепараторы пути в имени запрещены. Query/path traversal/чужой origin не дают файл. Range/HEAD/resume не добавлены.

Close запрещает новые регистрации/GET, уже открытая передача может закончиться. Очистка upload и закрытие сервиса не удаляют accepted exports, отдельной копии/recursive cleanup нет. ID живут в памяти до рестарта, нужные файлы надо сохранить из кэша STE.

## Проверки

- `python -m unittest discover -s tests -p test_downloads.py -v`: **10 новых тестов OK, 5.699с**. Реальные временные файлы/локальный HTTP/существующий adapter/QtBridge, подменённые native writer/scene/backend и ScriptedLLM.
- `python -m unittest discover -s tests -q`: **296 тестов OK, 23.765с**, прежние286 сохранены. Ожидаемые диагностические строки отказных тестов: LLM Preferences: ValueError; Slicer shutdown cleanup: SlicerError (тест специально закрывает QtBridge).
- `node tests/test_agent_web.js`: **10 offline DOM-сценариев OK**, прежние8 сохранены. Snapshot/повторные события/refresh/410, stale-update, исторический artifact без URL и unsafe href/textContent проверены.
- ast.parse с grammar Python3.5 для plugin/scripts/tests: **42 файла OK**, plugin.json api=5. Это синтаксическая проверка на установленном Python, не запуск в Python3.5/STE.
- `git diff --check`: без ошибок whitespace. Независимый прогон ведущего: **296 Python OK, 23.373с; JS10 OK**; по просмотренному коду блокирующих замечаний не найдено. Финальная приёмка остаётся за ведущим.

Значимое покрытие: точные bytes/headers для G-code и3MF; privacy публичного snapshot/tool/event и canonical fields вместо подменённого tool JSON; стабильность ID/дедупликация/UUID-коллизия; unfinished/unknown/stale/removed/изменение того же размера/замена файла; закрытый Qt/сервис; Host/Origin/upload-ID/path/query отказ; большие данные передаются read<=65536. Отдельно передача при service.close/очистке upload завершается тем же accepted-файлом, guard свободен, ServiceLock не удерживается при ожидании resolver. BrokenPipe после заголовков закрывает FD без JSON. Internal resolver отсутствует в схемах tools/list. Новые тесты не запускают реальный engine/LLM/installed plugin.

## Самостоятельная ручная приёмка и ограничения

Команды и порядок — [plugin/README.md](../../plugin/README.md#роль2c-скачивание-native-экспорта): после отдельно согласованных установки/провайдера получить настоящий completed/valid native export, явно прочитать его get_export_status агентом, восстановить snapshot текущей сессии/порта, скачать собственный download_url через curl.exe в новый путь, сверить размер и SHA256 с native-файлом. Повторить G-code/3MF, проверить Preview и reopen3MF как workspace; затем старую ссылку при stale, refresh без дубликатов. Команды подготовлены, **не выполнялись**. Сравнение SHA256 — команда приёмки, не новый runtime hash-layer.

Открыто: реальный STE/провайдер/браузерная визуальная приёмка/Preview/reopen3MF, три режима, custom-профили с рестартом и ручные ТЗ03/04. Fake writer HTTP не подтверждает native-файл/качество/материалы/лицензию. Сигналы нового engine-monitoring не добавлены: snapshot — последнее наблюдение; внешняя смена проекта может стать известна после следующего чтения/GET. Карточка не гарантирует доступность файла в момент клика; сервер решает заново.

Fingerprint dev/ino/size/mtime_ns обнаруживает обычные изменения/замену и сверяется с публикацией и открытым FD; **криптографической гарантии против внешнего редактора, сохраняющего mtime, нет**. Изменения проекта/файла после проверки и начала потока его не отзывают; обрыв/усечение не превращаются в успешный JSON. Native-пути остаются внутренними, реестр процесса не восстанавливается после рестарта. Новые квоты/cleanup/hashes/прокси не добавлялись.

Работа остановлена для ревью; новых фич/следующего этапа нет.
