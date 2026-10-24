# Контракт API между SPA и Python-плагином

Версия документа: 1, 23.10.2026. Для фронтенда, HTTP/backend и интеграции STE Slicer. Это единое место описания HTTP-границы; изменение маршрутов, полей и событий согласовывать до изменения клиентов.

**Статус:** в интеграции b9c0190 реализованы status, sessions, messages и polling событий (разделы 3–4, 6–7). Чат использует существующий реестр и адаптер. Роли2A/2B добавили POST `/api/files`, attachment_ids сообщений, image_url и load_model_file через прежний адаптер, минимальный picker. Роль2C добавляет скачивание зарегистрированных native-экспортов: attachments=true, downloads=true в plugin, downloads=false в dev. Флаг возможности не подтверждает готовность engine/лицензии. Все новые сценарии проверены изолированно; native-приёмка открыта. Краткая инструкция клиенту — [FRONTEND_INTEGRATION.md](FRONTEND_INTEGRATION.md).

Интеграция моделей из [ТЗ 04](tasks/TASK_04_MODEL_LOADING.md) реализована для прямого MCP, см. раздел 2; ручная приёмка новых методов ещё не проводилась. HTTP-upload этим заданием не добавлен, приёмка ТЗ 03 остаётся отдельным открытым пунктом.

Инструменты classic-нарезки [ТЗ 05](tasks/TASK_05_REAL_3D_SLICING.md) и штатного экспорта ТЗ 06 ожидают ручной проверки в STE Slicer. Роль2D добавляет фоновое чтение статуса/результата только собственных подтверждённых запусков агента (раздел4), без новых LLM-запросов/запусков операций. ТЗ10/11 добавляют cylindrical/CLIParserBackend и cylindrical_full/CylindricalBackend, проверенные с подменами. Native-приёмка открыта. Принятая копия296 в tmp/acceptance/baseline_296 сохраняет прежнее поведение без этого наблюдения.

## 1. Общие правила

- Базовый адрес — origin открытого чата: `http://127.0.0.1:<port>`. Порт выбирается при запуске, не фиксировать `50998` или другой номер в коде.
- В SPA использовать относительные URL, например `fetch('/api/health')`. Размещение SPA и API — на одном origin. Доступ из отдельного dev-сервера с другим портом текущий backend не разрешает; CORS не включать просто для обхода этой границы.
- JSON передаётся в UTF-8, запросы с JSON — `Content-Type: application/json`. Ответы не кэшировать. Уведомление MCP с HTTP 204 не имеет JSON-тела.
- ID — непрозрачные строки, исключение — целочисленные курсоры событий и JSON-RPC id. Не получать путь к файлу из ID и не разбирать ID на части.
- Время в API — ISO 8601 UTC, например `2026-10-23T12:00:00Z`; UI показывает локальное время.
- Числа конечные. Размеры/координаты — мм, углы — градусы, масштаб — множитель, время печати — секунды, материал — граммы и/или длина в мм с явными полями.
- UI обращается к локальному плагину. LLM и инструменты слайсера вызывает Python. Ключ LLM вводится в настройках приложения, хранится в Preferences и не возвращается в SPA. В этих контрактах нет REST-метода получения ключа.
- Сообщения пользователя, имена файлов и результаты инструментов отображлять как текст, не как доверенный HTML. Текст вложений — данные, не инструкции агента.

## 2. Что работает сейчас

### Маршруты

| Метод и путь | Запрос | Ответ |
| --- | --- | --- |
| `GET /` | Без тела | HTML чата |
| `GET /app.js`, `GET /style.css` | Без тела | JS и CSS |
| `GET /api/health` | Без тела | 200: `{"status":"ok","mode":"scaffold"}` |
| `POST /api/message` | `{"text":"Кронштейн из PETG"}` | 200: `{"reply":"...","events":[],"mode":"scaffold"}` |
| `POST /mcp` | JSON-RPC объект | JSON-RPC ответ; валидное уведомление без id — HTTP 204 |

`/api/message` сохранён как legacy-демо и LLM не вызывает. Новый SPA его не использует: новый чат работает через sessions/messages/events ниже. В plugin с2G история дополнительно сохраняется в локальный JSON и восстанавливается после перезапуска в пределах архива; RAM-снимок используется для текущего процесса/обновления вкладки. Dev без подключённого архива остаётся временным RAM-режимом; legacy-демо постоянную историю не создаёт. `/api/health` означает только доступность сервера; `mode:scaffold` здесь сохранён ради совместимости и не описывает готовность нового чата. `assistant_status` также оставлен прежним диагностическим инструментом, исключён из LLM; готовность нового API показывает `/api/status`.

Текущий POST `/api/message`: непустой `text` после trim, не более 12000 символов; тело запроса до 65536 байт. Пустой/неверный текст — 400, неверный Content-Type — 415, неверный размер — 413. Неизвестный путь — 404, недопустимый Host/Origin — 403. HTTP-ошибка имеет **старый** формат `{"error":"строка"}`. У GET пока нет полноценной диагностики готовности слайсера: health означает доступность сервера, а не готовность машины, LLM или движка.

### Действующие MCP-инструменты

`initialize`, `tools/list`, `tools/call`. Авторитетный список схем — ответ `tools/list`, формируемый общим реестром `agent/tools.py`.

- `assistant_status`, аргументы `{}`: диагностирует объём реализации; не подтверждает реальный runtime.
- `get_setting`, аргументы `{"key":"layer_height","scope":"global"}` либо `{"key":"material_print_temperature","scope":"extruder","extruder_id":"0"}`.
- `set_setting`, те же key/scope/extruder_id и обязательное value; прежний вызов `{"key":"layer_height","scope":"global","value":0.24}` сохранён.
- `describe_settings`, обязательный `scope` (и явный `extruder_id` для extruder), необязательные `keys/category/query/offset/limit`: страница каталога с фактическими значениями/метаданными текущего стека; default20/max30 (ТЗ13).
- `get_slicer_state`, аргументы `{}`: состояние приложения, активной машины, режима и backend.
- `set_print_mode`, аргументы `{"mode":"classic"}`: разрешены только `classic`, `cylindrical`, `cylindrical_full`; подтверждает настройку и выбранный backend. Вызывать на пустой сцене, без активной нарезки.
- `list_quality_profiles`, аргументы `{}`: все встроенные QualityGroup текущей конфигурации, включая недоступные.
- `list_materials`, аргументы `{"extruder_id":"0"}`: материалы под конкретную позицию экструдера и текущее сопло/диаметр/платформу.
- `select_profile`, аргументы `{"quality_type":"runtime-quality-type"}`: выбор доступной встроенной QualityGroup с сохранением overrides.
- `select_material`, аргументы `{"extruder_id":"0","root_material_id":"runtime-root-id"}`: выбор материала указанного экструдера с подтверждением resolved container.
- `load_model`, аргументы `{"path":"C:/Models/bracket.stl"}`: локальный mesh-импорт STL/OBJ/3MF с подтверждением завершения и новых объектов.
- `load_model_file`, аргументы `{"file_id":"file-..."}`: импорт своей зарегистрированной модели через прежний адаптер с подтверждением; путь не принимается.
- `list_models`, аргументы `{}`: верхние модели текущей сцены с непрозрачными ID и мировыми границами.
- `get_model_bounds`, аргументы `{"object_id":"model-1"}`: актуальные мировые границы живой модели.
- `get_model_state`, аргументы `{"object_id":"model-1"}`: origin, мировая матрица 4x4, габариты и outside_build_area.
- `move_model`, аргументы `{"object_id":"model-1","position_mm":{"x":0,"y":10,"z":0}}`: абсолютная мировая позиция origin, мм.
- `rotate_model`, аргументы `{"object_id":"model-1","axis":"y","angle_degrees":90}`: относительный поворот вокруг мировой оси через origin, градусы.
- `scale_model`, аргументы `{"object_id":"model-1","factors":{"x":1.1,"y":1,"z":1.1}}`: множители мировых осей масштаба с сохранением origin.
- `delete_model`, аргументы `{"object_id":"model-1"}`: удаление одной верхней модели/группы штатной операцией с Undo.
- `arrange_models`, аргументы `{}`: запуск штатного ArrangeObjectsJob для всей активной платформы, только classic; возвращает свой arrangement_id.
- `get_arrangement_status`, аргументы `{"arrangement_id":"arrangement-..."}`: состояние native job, фактические before/after и доступные сведения о границах размещения.
- `start_slicing`, аргументы `{}`: короткий запуск штатной classic/cylindrical/cylindrical_full-нарезки, возвращает идентификатор для polling.
- `get_slicing_status`, аргументы `{"slice_id":"slice-1"}`: состояние, прогресс и актуальность выбранного запуска.
- `get_slicing_result`, аргументы `{"slice_id":"slice-1"}`: время/материал только завершённого актуального запуска.
- `export_gcode`, аргументы `{"slice_id":"slice-1"}`: запуск штатного GCodeWriter из mesh-handler.
- `save_project_3mf`, аргументы `{"slice_id":"slice-1"}`: запуск 3MFWriter из workspace-handler, сохранение проекта.
- `get_export_status`, аргументы `{"export_id":"export-..."}`: статус записи и готовый локальный артефакт.

У новых инструментов лишние поля запрещены. ТЗ 07 исходно открыло 19 ключей; ТЗ13 расширяет get/set_setting до каталога120 ниже. Передавать `printing_mode` по-прежнему нельзя. ТЗ 02 принято после 45 автоматических проверок и реального прогона в STE Slicer 7.10.12 на Hybrid 530 V5.2.4: диагностика, три режима, backend, UI и восстановление подтверждены. Это не проверка слайсинга, экспорта или новых настроек.

Инструменты ТЗ 03 реализованы и проверены с подменами; их runtime-приёмка ещё не проводилась. ID — непустые строки; `extruder_id` — ключ позиции из `global_stack.extruders` (например, `"0"`), а не подпись кнопки UI и не числовое значение. UI-экструдер ради операций не переключается. Названия не заменяют ID.

`list_quality_profiles` возвращает `{machine_id,current:{quality_type,name,custom_profile_id,has_user_settings},profiles:[{quality_type,name,available,layer_height_mm}]}` внутри `ok/data`. Высота — число из базового quality-контейнера или definition, не override активного стека. Невычисляемое надёжно значение (в том числе SettingFunction) — null. Custom ID — ID глобального узла QualityChangesGroup, при его отсутствии — первого экструдерного узла; при отсутствии custom — null. Создание, изменение и выбор custom-профиля в этот объём не входят.

`list_materials` возвращает `{machine_id,extruder_id,current,materials}`. `current` — `{root_material_id,material_id,name}` из материала указанного экструдера (root из metadata.base_file, при отсутствии — currentRootMaterialId этой позиции), либо null при отсутствии материала. Элемент `materials`: `{root_material_id,material_id,name,brand,material,compatible}`. Список берётся из `getAvailableMaterialsForMachineExtruder`, removed-пакеты исключаются. Root ID и resolved material ID сохраняются отдельно. Неизвестная совместимость — null; явная несовместимость запрещает выбор. Отсутствующие name/brand/material — null.

`select_profile` возвращает `{before:{quality_type,name,custom_profile_id},after:{quality_type,name,custom_profile_id},has_user_settings}`. Подтверждаются не только quality_type, но и фактически установленные global/extruder quality container ID; custom не должен остаться активным. Штатный вызов — `setQualityGroup(group,no_dialog=True)`, без очистки userChanges. При активном custom-профиле изменение отклоняется до setter, даже если underlying quality_type совпадает.

`select_material` возвращает `{extruder_id,before,after,quality_type_after,has_user_settings}`; before/after имеют форму `current` из списка материалов. Подтверждаются root ID и ожидаемый resolved container для указанной позиции. Материал может изменить качество, поэтому после выбора обязательно перечитать `list_quality_profiles`; сохранение прежних зависимых температур/скоростей не обещается.

Для выбора материала no-op проверяется до политики диалогов. Для реального изменения при overrides требуется штатная `choice_on_profile_override=always_keep`, иначе `user_changes_present` **до setter**. Preferences не меняются. Проверка консервативна и не зависит от текущего UI active_mode. При активном custom также запрещена реальная смена материала: штатный API может сбросить custom при смене доступной группы качества. Оба ограничения снимаются пользователем в приложении по отдельному решению, не автоматически инструментом.

Выбор выполняется из worker, отдельными короткими Qt-вызовами с общим бюджетом 5 секунд. Режим/качество/материал используют общую неблокирующую блокировку (`switch_in_progress` для режима, `selection_busy` для нового выбора). Повторный подтверждённый выбор не вызывает setter; смена машины/экструдеров/lookup или потеря overrides не считается успехом. После ошибки требуется перечитать списки и состояние; автоматического отката/повтора нет. Отмена pending-callback при timeout/shutdown сохранена.

`get_slicer_state` возвращает `{ok:true,data:{connected,application_version,machine,printing_mode,backend,available_modes}}`. `machine` — `{id,name,definition_id}`, значения берутся из активного стека. `backend` — `{id,expected_id,matches_mode}`: ID из runtime-объектов, `matches_mode` проверяет идентичность текущего backend ожидаемому объекту менеджера. При отсутствии current/expected ID равны null, `matches_mode=false`. Нет активной машины — успешная диагностика с `connected=true`, `machine=null`, `printing_mode=null`, `backend=null`, `available_modes=[]`. Это не готовность к печати.

`available_modes` содержит только поддержанные машиной режимы кейса (из enum, для 5D также `machine_five_axis=true`), элементы `{id,label,backend_available}`. Метки берутся из enum текущего стека, `backend_available` — только наличие регистрации, не работоспособность движка или лицензии. Если текущий режим вне трёх разрешённых, диагностика сохраняет его фактическую строку; `set_print_mode` такой режим отклоняет.

Успех `set_print_mode`: `{ok:true,data:{before,after,backend:{id,expected_id,matches_mode:true}}}`. Настройка и backend подтверждаются в отдельных Qt-вызовах после штатных queued-сигналов; общий бюджет — 5 секунд. Уже согласованный режим не записывается повторно. Параллельное переключение отклоняется; синхронный вызов из Qt-потока запрещён до записи. При ошибке после начала записи требуется `get_slicer_state`, откат и автоматический повтор не выполняются. Защита от активного слайсинга в этом объёме не реализована: у исследованного общего API Backend нет getter текущего состояния, применяется ограничение проверки пустой сценой.

Пример запроса:

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_setting","arguments":{"key":"layer_height","scope":"global"}}}
```

Пример успешного ответа:

```json
{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"{\"ok\":true,\"data\":{\"key\":\"layer_height\",\"scope\":\"global\",\"value\":0.2,\"unit\":\"mm\"}}"}],"isError":false}}
```

Операционная ошибка инструментов слайсера находится в JSON-тексте `content`: `{"ok":false,"error":{"code":"slicer_not_connected","message":"..."}}`, MCP выставляет `isError:true`. Неверные аргументы/неизвестный инструмент — JSON-RPC `error.code=-32602`, не операционный успех. Коды операций ТЗ 01: `slicer_not_connected`, `no_active_machine`, `setting_unavailable`, `setting_out_of_range`, `qt_timeout`, `setting_not_applied`, `slicer_error`. ТЗ 02 дополнительно использует `mode_unavailable`, `backend_unavailable`, `mode_not_applied`, `backend_not_switched`, `switch_in_progress`. ТЗ 03: `extruder_not_found`, `profile_not_available`, `material_not_available`, `custom_profile_active`, `user_changes_present`, `selection_not_applied`, `selection_busy`. Неизвестный синтаксически корректный ID — операционная ошибка до записи. Внутренняя ошибка API не выдаётся за Unknown tool.

У `assistant_status` сейчас отдельный диагностический формат без обёртки `ok/data`. Не считать все ответы MCP одинаковыми. Он перечисляет реализованные инструменты слайсера и allowlist/области настроек, но сохраняет `llm_connected=false` и `runtime_verified=false`; прошлый прогон не подтверждает новый код. В dev-сервере операции с приложением отвечают `slicer_not_connected`. ТЗ 01 принято в STE Slicer 7.10.12 на Hybrid 530 V5.2.4, 3D; чтение, изменение и восстановление layer_height подтверждены API/UI. Это не делает доступными целевые REST-маршруты ниже.

### Настройки ТЗ 07/13 — реализованы, runtime-приёмка открыта

Исходные19 сохранены; полный перечень120 ключей/типов и стабильный порядок — [каталог ТЗ13](tasks/TASK_13_SETTINGS_CATALOG.md), единственный runtime-каталог — `slicer/settings_catalog.py`. Категории перечислены в той же схеме MCP/LLM:

| category | Группа | Ключей |
| --- | --- | ---: |
| resolution | Высоты/ширины линий | 12 |
| shell | Стенки/поверхности | 11 |
| infill | Заполнение | 11 |
| material | Температуры/подача/ретракт | 20 |
| speed | Скорости | 14 |
| travel | Перемещения/подъём | 6 |
| cooling | Охлаждение | 9 |
| support | Поддержки | 20 |
| platform_adhesion | Адгезия | 13 |
| printing_mode_category | Основание5D | 4 |

Каталог подтверждён definitions форка и ограничивает синтаксис запроса; он не обещает поддержку всех120 текущей машиной. Нужны реальное наличие ключа и совпадающий runtime type. `cylindrical_round_segments` имеет тип float; `support_structure` отсутствует. Машина/сопло, start/end G-code, printing_mode, произвольные ключи, строки-выражения и batch setter не допускаются. Режим меняется только set_print_mode.

Schemas get/set: обязательные key/scope и для setter value; key — enum allowlist, scope — global/extruder, extruder_id — необязательная в JSON-schema строка minLength:1, value — number/boolean/string, additionalProperties:false. Общий валидатор требует extruder_id **только** для extruder, запрещает его для global и проверяет тип конкретного ключа. Describe имеет обязательный scope и такое же правило extruder_id. Неверная структура, bool вместо числа, дробный int, NaN/inf, числовая строка и неподдержанный ключ — -32602 до API. Целое 3.0 допускается для int и нормализуется в 3 без округления дроби. Только layer_height сохраняет раннее >0; нулевое заполнение может быть допустимо.

extruder_id — точный строковый ключ global_stack.extruders, без смены UI-позиции и подстановки активного экструдера. Неизвестная позиция — extruder_not_found. Глобальный стек и userChanges целевого стека читаются на Qt. Единицы, options, enabled, settable_globally/settable_per_extruder и четыре bounds берутся из вычисленных runtime-свойств, readonly — из userChanges.getReadOnly. Enum принимает ключ актуального options, не подпись. Неподтверждённые тип/разрешение/enabled/readonly запрещают запись; отключённые и readonly настройки можно читать.

Обычные get/set возвращают прежние key/scope/unit/value либо before/after, для extruder добавляется extruder_id. Пример setter data:

```json
{"key":"material_print_temperature","scope":"extruder","extruder_id":"0","unit":"°C","before":205.0,"after":210.0}
```

Describe дополнительно принимает `keys` (массив1–20 уникальных известных ключей), `category` (из таблицы), `query` (строка до80 символов), `offset` (целое>=0, default0), `limit` (целое1–30, default20). Bool не int; null/лишние поля/неизвестные ключи или категория дают -32602 до API. Фильтры пересекаются, затем берётся страница в порядке каталога, а не входного keys. Поиск — подстрока без регистра по key и исходному краткому английскому label каталога, без fuzzy/перевода. Runtime label может быть локализован; он не участвует в поиске, чтобы не читать все120 описаний. Отсутствие query не фильтрует; явная пустая/пробельная query даёт0 результатов. Даже при пустом результате проверяются остальные аргументы, машина и runtime extruder_id.

Describe data сохраняет `{scope,extruder_id?,settings:[...]}` и добавляет `total` (после фильтров до страницы), `offset`, `limit`, `returned`, `next_offset` (null в конце), `truncated` (есть следующая страница). Недоступные ключи выбранной страницы остаются с available=false. Offset за концом даёт пустой settings, returned0/next_offset=null/truncated=false при прежнем total. Вызов только со scope теперь возвращает первую страницу, полный каталог автоматически не читается. Фильтрация/пагинация выполняются до runtime metadata, на Qt читается только страница.

Пример адресного вызова: `describe_settings {"scope":"global","keys":["support_z_distance","support_xy_distance"]}`; категории и поиск можно пересекать: `{"scope":"global","category":"support","query":"pattern","limit":5}`. Ответ первой страницы без фильтров имеет total120/offset0/limit20/returned20/next_offset20/truncated=true; фактические values этим примером не задаются. Иллюстративный элемент:

```json
{"key":"speed_print","available":true,"type":"float","value":50.0,"unit":"mm/s","enabled":true,"settable":true,"options":{},"settable_globally":true,"settable_per_extruder":true,"read_only":false,"minimum_value":0.0,"maximum_value":null,"minimum_value_warning":10.0,"maximum_value_warning":150.0,"limitations":[]}
```

available означает наличие value в стеке, settable — разрешение выбранной области с учётом userChanges/readonly; enabled/type проверяются отдельно. Неизвестные поля и непригодное значение — null с limitations, не собственная догадка. Пустая строка unit допустима как runtime-единица без размерности. Отсутствующая граница — null; нечисловая/неконечная граница помечается limitation. Неизвестное enum options или значение вне них не выдаётся за подтверждённое value.

Каждый элемент также содержит `label`, `category`, `description`, `text_source:{label,category,description}`, `label_truncated`, `description_truncated`. Label из Definition API обрезается до200 символов, description до500; происхождение `definition_api`. Fallback краткого label и category — `catalog`, неизвестное description=null и его источник=null. Статический текст не подтверждает runtime-доступность/значение. Чтение через подтверждённые ContainerStack.hasProperty/getProperty; default_value не подставляется, выражения вычисляет STE. Enum options остаются целиком из runtime API; отдельной квоты нет, общий размер зависит также от длин options/limitations и локализации. Оценка исходных options/страниц и границы замера — в отчёте13. Усечение страницы (`truncated`) и текста (`description_truncated`) имеют разные значения.

Запись идёт только в userChanges целевого стека. Hard bounds запрещают выход, несогласованные/невычисленные hard bounds дают slicer_error. Warning bounds не запрещают запись: при выходе setter добавляет warnings, как и при недоступных необязательных метаданных. Ни одна температура не является командой нагрева. Зависимые значения могут пересчитаться штатно; сохранение всех derived values не обещается.

Before/after читаются из стека и нормализуются по runtime-типу; запрос не подставляется вместо after. Смена машины/целевого стека/userChanges или отсутствие подтверждения — ошибка. Подтверждённый no-op не вызывает setter и не инвалидирует slice. Реальная попытка изменения, включая неопределённый setter, инвалидирует прежнюю нарезку/будущий экспорт; автоматического отката/повтора нет. Прежний общий lock охватывает импорт/нарезку/экспорт/выбор профиля; записи получают slicing_busy, чтения доступны. QtBridge сохраняет обычный бюджет 5 секунд и отмену просроченного pending callback; после running timeout нужны чтение/UI до дальнейших изменений.

Дополнительные операционные коды: setting_metadata_unavailable, setting_option_unavailable, setting_disabled, setting_read_only, setting_scope_unavailable, setting_context_changed. Сохранены setting_unavailable/setting_out_of_range/setting_not_applied, extruder_not_found/no_active_machine, slicing_busy/qt_timeout/slicer_error/slicer_not_connected. Полные проверки и ограничения: [отчёт ТЗ 07](reports/TASK_07_REPORT.md), [отчёт ТЗ13](reports/TASK_13_REPORT.md). Инструкции LLM требуют адресного describe и не запрашивают120 полных описаний на каждом ходе; прежние лимиты8LLM/16tools/120s/30s и запрет повторов после неизвестного исполнения сохранены. UI показывает количество найденных/выданных параметров и наличие следующей страницы, подробности остаются закрытыми.

### Пользовательские профили — ТЗ12, native-приёмка открыта

Единый реестр добавляет list_custom_profiles{}, create_custom_profile{name}, select_custom_profile{profile_id}, save_custom_profile{profile_id}. Builtin select_profile{quality_type} не менялся: он по-прежнему отклоняет активный custom. Лишние поля/нестроковые аргументы запрещены до Qt; name1–80, profile_id1–256, без краевых пробелов/управляющих символов. Для нового name дополнительно запрещены разделители пути/двоеточие; существующий profile_id — непрозрачный lookup-ID, двоеточие/разделители не превращают его в путь. Мутации отмечены is_mutating и после неподтверждённого результата останавливают turn.

List возвращает {machine_id,profiles:[{profile_id,name,quality_type,available,complete,active,container_ids}]}. ID — глобальный quality_changes контейнер из актуальной группы текущей машины; имя не используется вместо ID для поиска в произвольном Registry. Неполная группа недоступна; если глобального node нет, profile_id=null. Create отклоняет коллизию имени, вызывает native createQualityChanges, ждёт новую полную группу, проверяет перенос raw old qualityChanges+userChanges каждого global/extruder стека и неизменность прежнего custom; затем отдельно выбирает именно новую группу no_dialog с readback. Очистка userChanges допустима только как подтверждённый перенос. Rollback/удаления частичных контейнеров нет.

Select сохраняет user overrides и подтверждает actual global/extruder quality/qualityChanges IDs. Save принимает только активную полную writable custom-группу; все контейнеры/userChanges проверяются до updateQualityChanges, после — каждый перенесённый ключ и очистка. True/None native API не являются доказательством. Ответ мутации: {profile_id,name,quality_type,container_ids,active,user_overrides_cleared,transferred_keys,persistence:{memory_verified,disk_verified,restart_verified:false}}. transferred_keys — только имена ключей по global/позициям, без сырых значений. У select disk_verified=false означает «файлы этой операцией не проверялись», а не отсутствие файла.

Для create/save вызывается Registry.saveContainer только целевых QC через известный LocalContainerProvider. На Qt фиксируются native serialize и регистрация provider/path/mime; worker ограниченно читает реальные файлы (до2MiB каждый, UTF-8/universal newline), сравнивает с native serialized snapshot, затем Qt повторно подтверждает контекст/ID/сериализацию. В ответах/events нет путей/содержимого. Это подтверждение файлов профиля в текущем процессе, не активного выбора/userChanges-файлов/Preferences и не успешного восстановления после рестарта. Постоянство выбора и штатный lifecycle приложения проверяются отдельно.

QtBridge/change_lock/инвалидация slice прежние. Pending-timeout отменяется; при running-timeout синхронного native provider guard держится до выхода callback и следующие контейнеры не сохраняются после abandonment. Native LocalContainerProvider/SaveFile пишет синхронно на Qt и способен ждать ОС: свой писатель/перенос Qt-объектов в worker не добавлены. Worker-файловая проверка вне Qt. Изменение машины/геометрии/режима/экструдеров/профиля/userChanges/постоянных контейнеров/значений во время подтверждения даёт ошибку, без автоповтора.

Коды: custom_profile_not_available, custom_profile_not_active, custom_profile_read_only, custom_profile_name_conflict, custom_profile_context_changed, custom_profile_unconfirmed, custom_profile_storage_unavailable, custom_profile_storage_unconfirmed; selection_busy/qt_timeout/slicer_not_connected сохраняются. Error содержит actual:{phase,profile_id,memory_verified,disk_verified,...} с последним подтверждённым безопасным состоянием/числом оставшихся overrides, без значений/путей. Частичный профиль/запись могут остаться; перечитать list/UI, не создавать повтор автоматически. [TASK_12_REPORT.md](reports/TASK_12_REPORT.md).

### Локальные модели — ТЗ 04, runtime-приёмка открыта

В трёх новых schemas все перечисленные поля обязательны, `additionalProperties=false`; path/object_id — `type:string,minLength:1`. Некорректная структура — JSON-RPC `-32602`. Остальные отказы — `{ok:false,error:{code,message}}` в MCP content, `isError=true`.

`path` — временный диагностический вход только для прямого MCP, не инструмент раздачи/HTTP-upload. Принимаются существующие читаемые обычные локальные файлы `.stl/.obj/.3mf` (регистр не важен), размер **1–52428800 байт (50 MiB)**. Относительные, URL, UNC/device/сетевые диски, каталоги, пустые/слишком большие файлы и прочие расширения отклоняются. Проверки файла выполняются в worker. Полный путь не включается в успешный ответ или новые логи плагина; штатные сообщения/логи reader управляются самим STE. В будущем вместо произвольного path нужен доверенный file_id; этот upload-контракт пока отсутствует.

Результаты внутри `ok/data`:

- `list_models`: `{models:[...]}`, пустая сцена — пустой массив.
- `get_model_bounds`: `{object_id,bounds}`.
- `load_model`: `{file_name,objects:[...]}` — basename и только новые верхние модели собственного завершившегося импорта; повторная загрузка — новый импорт.

Элемент models/objects: `{object_id,name,build_plate,is_group,bounds}`. ID последовательно назначается адресуемому живому объекту в пределах сессии плагина, не является именем файла, Python id или путём. Удалённые/переставшие быть верхними объекты и другая сцена инвалидируют ID; выданные номера не переиспользуются. Реестр использует weakref и заново проверяет принадлежность текущей сцене. build_plate — номер декоратора или null.

`bounds`: `{coordinate_system:"uranium_world_y_up",unit:"mm",min:{x,y,z},max:{x,y,z},size:{x,y,z}}`. Y — вертикаль, X/Z — плоскость стола. Размеры конечны и неотрицательны, плоская модель допустима. Границы вычисляются из `mesh.getExtents(node.getWorldTransformation())`; для группы объединяются границы печатных mesh-потомков, без пустого origin родителя. Недоступные/неконечные границы — null в списке, get_model_bounds возвращает `model_bounds_unavailable`. Группы не дублируются отдельными частями; root, узлы без isSliceable/isGroup и printable mesh, а также isBlockSlicing preview исключаются.

Импорт требует активной машины и отсутствия isBlockSlicing во всей сцене: штатный readLocalFile иначе может вызвать deleteAll. Вызывается `readLocalFile(QUrl,skip_project_file_check=True)`, включая 3MF: mesh-путь без workspace-диалога/открытия проекта. Штатное размещение, автомасштабирование и object overrides не отключаются; возвращается фактическая геометрия, отсутствие глобальных изменений в конкретной сборке требует ручной проверки.

Корреляция — нормализованный полный путь только внутри контекста плюс окно собственного fileLoaded → fileCompleted. Ранний fileLoaded, чужой сигнал или новые узлы без собственного завершения не подтверждают успех; пустое завершение — ошибка. Фиксируются исходные узлы, сцена/root/машина; обнаруженная подмена или исчезновение исходных узлов — `model_scene_changed`. Ручной параллельный импорт и очистка сцены не поддерживаются; неотличимые ручные операции с тем же файлом не имеют надёжного request ID в этом API.

Общий бюджет ожидания импорта — **10 секунд**; короткие вызовы используют оставшийся бюджет, ожидание только в worker. До запуска просроченный callback отменяется. После запуска timeout не отменяет native job: он может добавить модели позднее. Общая блокировка загрузки/режима/качества/материала остаётся до собственного fileCompleted или shutdown; списки и диагностика доступны. Если завершение не приходит, требуется перезапуск плагина/приложения. Ошибка readLocalFile после потенциального запуска также сохраняет этот контекст до подтверждённого завершения. Автоповтора, удаления и фиктивного отката нет.

Новые операционные коды: `invalid_model_path`, `unsupported_model_file`, `invalid_model_file`, `model_file_unavailable`, `model_not_found`, `model_bounds_unavailable`, `model_preview_active`, `model_load_busy`, `model_load_failed`, `model_scene_changed`, `model_load_timeout`. Сохранены `slicer_not_connected`, `no_active_machine`, `qt_timeout`, `slicer_error`; конкурирующие прежние setters возвращают свои busy-коды. При неопределённости требуется list_models. Подписки снимаются на Qt-потоке после подтверждённого завершения, ошибки подготовки или shutdown; незавершённый потенциально запущенный job сохраняет минимальный контекст для позднего завершения.

### Преобразования ТЗ 08 — реализованы, native-приёмка открыта

Схемы добавлены в тот же ToolRegistry; четыре мутации входят в is_mutating и останавливают агент после неподтверждённого изменения. get_model_state — чтение. HTTP/цикл/SPA ради новых tools не менялись. Печатная модель/верхняя группа адресуется только живым object_id из list_models; дочерняя часть, служебный узел и preview не являются отдельной целью. Имена не заменяют ID.

Верхние и вложенные JSON-объекты принимают ровно перечисленные поля. Только конечные int/float без bool/строк. axis — x/y/z, factors — 0.01–100 включительно, position_mm и angle_degrees — ±1000000. Это технические границы прототипа, не размеры принтера. Дополнительно проверяются аффинность/обратимость матрицы и консервативная конечная граница итоговой геометрии ±1000000 мм; небезопасный результат отклоняется до push. Неверная структура — -32602; неподдерживаемая геометрия — операционный отказ.

get_model_state возвращает внутри ok/data:

```json
{"object_id":"model-1","position_mm":{"x":0,"y":10,"z":0},"world_transform":[[1,0,0,0],[0,1,0,10],[0,0,1,0],[0,0,0,1]],"bounds":{"coordinate_system":"uranium_world_y_up","unit":"mm","min":{"x":0,"y":10,"z":0},"max":{"x":2,"y":13,"z":4},"size":{"x":2,"y":3,"z":4}},"build_plate":0,"outside_build_area":false}
```

Y — высота, X/Z — стол. Матрица row-major, столбец [0..2][3] — origin; Euler-углы не используются. Масштабирование умножает **мировые оси**, а не локальные размеры после поворота. Габариты — прежняя схема мировых mesh-границ. outside_build_area: true/false из BuildVolume/SteSlicerSceneNode после проверки, null при отсутствии надёжного bbox/API. true сопровождается предупреждением в ответе мутации и не означает готовность к печати; false также не является проверкой всех условий печати.

move/rotate/scale возвращают `{object_id,before:<state>,after:<state>,changed:true/false,warnings:[]}`. before/after перечитаны из сцены. У no-op (включая 0/полные обороты и factors=1) нет push и инвалидации native slice. Успех требует фактической матрицы, origin и габаритов с числовым допуском, не одного возврата push. delete возвращает `{removed_object_id,before:<state>,after:null,removed:true}` только после отсутствия узла в сцене/реестре и сохранности остальных моделей. Исходные файлы не удаляются. После Undo ID берётся заново из list_models, прежний ID остаётся model_not_found.

Прототип требует активную машину/платформу, включённый узел и единичную мировую матрицу его родителя. Сама верхняя группа может быть преобразована, её печатные потомки меняются вместе. Нетривиальный родитель/preview/вырожденная матрица отклоняются до записи. **Automatic drop down и push free должны быть выключены пользователем в UI**: physics/automatic_drop_down и physics/automatic_push_free должны быть подтверждённо false. Иначе model_transform_unsupported, без изменения Preferences/геометрии. Физика форка с QTimer 100 мс могла бы позже сдвинуть origin или соседние модели; ожидание/отключение её из плагина не добавлено. Даже после отключения пользователь не должен параллельно редактировать сцену/запускать tools.

Все чтения, операции/Selection/BuildVolume и push — Qt через существующий мост. Общий guard удерживается на Qt до подтверждения, в том числе после timeout worker. Он запрещает запись при импорте/нарезке/экспорте/другой мутации; также проверяются текущая занятость backend и штатная запись local_file. Чтения доступны. После начатого push slice консервативно инвалидируется даже при ошибке; автоотката нет, Undo — отдельное пользовательское действие. В агентном кеше no-op также может консервативно снять valid; свежий status восстанавливает фактическое native-значение.

Ошибки: model_not_found, model_bounds_unavailable, no_active_machine, model_operation_busy, model_transform_unsupported, model_transform_not_applied; qt_timeout/slicer_not_connected/slicer_error сохраняют смысл моста/адаптера. После начатой/неподтверждённой операции перечитайте get_model_state/list_models/UI, не повторяйте автоматически. Стандартный Undo/Redo может объединять соседние native operations; один вызов не обещает ровно один шаг Undo. Слайсинг/экспорт после Undo требуют новой актуальной нарезки. Подмены и чтение исходников не закрывают native-приёмку; отчёт — [TASK_08_REPORT.md](reports/TASK_08_REPORT.md).

### Расстановка ТЗ 09 — native job подключён, native-приёмка открыта

arrange_models входит в is_mutating, status — чтение. Используются тот же реестр/QtBridge/ModelRegistry/общий guard; агент/HTTP/SPA не менялись. Структура и типы строгие: только `{}` либо непустая строка arrangement_id без лишних полей, иначе -32602 до API. Память хранит до 16 последних принятых запусков; неизвестный/истёкший ID — arrangement_not_found. Без app — slicer_not_connected.

Принятие возвращает быстро queued/running с arrangement_id; это не завершение и не «всё помещается». status возвращает внутри ok/data `{arrangement_id,state,progress,error,models,all_fit,valid,observed_at,warnings}`. models — массив `{object_id,before:<get_model_state>,after:<get_model_state|null>}`; before/after сохраняют origin/матрицу/габариты/Y-up из ТЗ 08. У недоступного after при конфликте возможен after_error:{code,message}. До окончательного выхода job after=null. Частичные фактические after по живым ID сохраняются и при failed. observed_at — UTC-время чтения результата или null. valid означает соответствие сохранённого снимка текущему контексту, а не успешность поиска места/печати; после следующей расстановки или обнаруженного изменения он false, история не перезаписывается новыми позициями.

state: queued/running/completed/failed. Native job публикует ранний finished и повторный finished после JobQueue; принимается только свой job с isFinished()==true и isRunning()==false, затем Qt-проверка. getResult()==None в этом форке нормален; hasError/getError проверяются отдельно, приватные детали не возвращаются. progress всегда null: ArrangeObjectsJob не испускает Job.progress, процент штатного Message наружу не подменяется расчётом по времени.

**completed подтверждает выход job и сохранение after, не полное решение.** Native found_solution_for_all — локальная переменная, наружу не отдаётся. Непоместившийся объект переносится в `(200,center_y,-n*20)`, что может находиться внутри Hybrid 530. Поэтому outside=false у всех не доказывает, что найдены места всем. В этой версии all_fit **не бывает true**: после успешной проверки false при хотя бы одном штатном outside_build_area=true; иначе null (также при недоступных границах или ненаблюдаемом перемещении). При failed/неподтверждённом контексте all_fit остаётся null. Подтверждённый false сохраняется и без изменения матриц. Per-object outside — факт, не собственная проверка коллизий/полного размещения. UI должен показывать неизвестность и предложить сверить штатную расстановку/сообщение STE; arranged:true не выдаётся.

Перед запуском нужны активная машина/платформа, classic с соответствующим backend, auto_slice=false и обе physics-опции=false, отсутствие preview/конкурирующих job/записи. Preferences пользователь меняет в UI, плагин их не трогает. Выбираются все адресуемые верхние модели активной платформы; каждая включена/selectable под единичным мировым родителем. Все печатные части группы принадлежат активной платформе. Остальные платформы/служебные узлы не передаются job. Пустая платформа → empty_scene, без фиктивного job. Нужны native hull/head hull с конечными непустыми точками и BuildVolume. Отсутствующие/некорректные hull и модели шире/глубже стола отклоняются до запуска всей группы, без тихого пропуска. AABB не заменяет hull. min_offset=max(getEdgeDisallowedSize()+2,8), как в app.arrange; собственный упаковщик не используется.

Job создаётся/подписывается на Qt, start отправляет его в **штатную JobQueue**, как native Arrange All. Внутренние чтения сцены/GroupedOperation native run выполняет в своём worker по API форка; плагин не добавляет туда собственные обращения к app и не запускает run на Qt. Свои callbacks через app.callLater, таймер 250 мс/status только наблюдают; Qt не ждёт job. До завершения берутся только flags/context, итоговая геометрия читается после выхода. Проверяются машина/scene/root/платформа/контейнеры, состав моделей, mesh/части/родители, rotation/scale и относительная геометрия группы; другие платформы не должны изменить матрицы. Разрешены native translate/drop и снятие ZOffsetDecorator.

Общий guard удерживается до фактического выхода и проверки. Бюджет 120 секунд с принятия: timeout → failed/arrangement_timeout, без cancel/kill/повтора/отката; guard и job/nodes остаются до публичного подтверждения выхода. Поздний выход не меняет failed на completed. При ошибке start после возможного принятия сохраняются ID/guard до выхода или перезапуска. Просроченная подготовка в обычном Qt-бюджете отменяется до native start. Shutdown останавливает timer/новые действия, но удерживает нужный live-контекст/finished-подписку до выхода; если event loop уже недоступен, безопасное наблюдение не гарантируется и нужен перезапуск.

Вызов консервативно инвалидирует прежнюю нарезку/экспорт. Ручные параллельные изменения не поддерживаются: обнаруженные конфликты → failed/arrangement_context_changed с фактическим частичным after, без отката. Нельзя надёжно отличить ручной перенос целевой модели от native translate по одному конечному снимку; параллельные user tools запрещены правилами сценария. Undo — штатный пользовательский, после проверки состояния; новую нарезку запускают отдельно.

Ошибки: empty_scene, no_active_machine, unsupported_arrangement_mode, auto_slicing_enabled, arrangement_busy, arrangement_unsupported, arrangement_hull_unavailable, arrangement_not_found; состояния failed используют arrangement_failed/arrangement_context_changed/arrangement_timeout/slicer_not_connected. После ошибочного старта/timeout перечитайте list_models/get_model_state/status/UI, не повторяйте автоматически. Отчёт — [TASK_09_REPORT.md](reports/TASK_09_REPORT.md).

### Нарезка classic/cylindrical/cylindrical_full (ТЗ05/10/11) — runtime-приёмка открыта

Схемы трёх методов имеют `type:object`, `additionalProperties:false`. У `start_slicing` — `properties:{}`, `required:[]`; у двух методов чтения единственное обязательное поле `slice_id` с `type:string,minLength:1`. Неверные типы/лишние поля — JSON-RPC `-32602`, без API-вызова. Без приложения — `slicer_not_connected`. Общая оболочка остаётся `{ok:true,data:...}` / `{ok:false,error:{code,message}}` внутри MCP content; операционные ошибки имеют `isError:true`.

Успешный start возвращает форму статуса с `queued` либо `running`. Это принятие команды, не завершение engine. Если backend отказал синхронно, start возвращает операционную ошибку с slice_id в сообщении. Последующее состояние `failed` само по себе является успешным чтением статуса; причина находится в `data.error`.

```json
{"slice_id":"slice-1","state":"running","progress":0.42,"printing_mode":"classic","backend_id":"CuraEngineBackend","build_plate":0,"valid":false,"error":null}
```

`state`: `queued/running/completed/failed`. `progress` — последнее конечное значение backend в диапазоне 0..1 либо null до его получения, без своей интерполяции. `valid:true` означает актуальный completed-результат. ID непрозрачен и действует в текущей сессии плагина. В памяти хранится текущий запуск и последний completed; старые ID могут исчезать. Неизвестный ID не заменяется последним.

```json
{"slice_id":"slice-1","printing_mode":"classic","backend_id":"CuraEngineBackend","build_plate":0,"print_time_seconds":3723,"material":{"length_mm":1270.0,"weight_g":3.6},"source":"ste_slicer","warnings":[]}
```

Числа выше иллюстративны. Время берётся через `int(PrintInformation.currentPrintTime)` в секундах; длина — сумма `materialLengths` в метрах, переведённая в мм; вес — сумма `materialWeights` в граммах. Позиции экструдера спискам не приписываются. Округление длины в STE до 0.01 м сохраняется; точность выше источника не обещается. Это прогноз слайсера, не измерение физической печати. Недоступное/неконечное значение — null и пояснение в `warnings`; подтверждённый ноль разрешён. Собственных формул времени/расхода и генерации G-code нет.

До forceSlice проверяются три разрешённых режима и идентичность зарегистрированного runtime-backend, активная машина, одна непустая активная платформа, отсутствие G-code preview, импорта и подготовки/обработки слоёв. При включённом `general/auto_slice` требуется выключить его вручную в UI. Плагин не меняет Preferences. Ручной параллельный запуск/редактирование не поддерживаются; обнаруженный конфликт не считается нашим успехом.

Подписки устанавливаются до forceSlice на Qt. Завершение требует начала (`slicingStarted/Processing`), собственного `Done`, отдельного Qt-callback после финализации, свободного backend, неизменного контекста, свежего G-code и собственных оценок платформы с последующим обновлением всех трёх полей PrintInformation. Начальный `NotStarted`, один progress=1, старый буфер/время и чужой Done недостаточны. Учтена штатная замена пустых списков в GenerateBasementJob/первом GCodeLayer; непустой захваченный буфер другим списком не подменяется. Оценка другого зарегистрированного backend той же платформы — конфликт.

Таймер наблюдения на Qt — 250 мс, бюджет принятого запуска — **180 секунд**, без блокирующего ожидания на Qt/HTTP. После `slicing_timeout`, отмены или ошибки поздний Done не меняет failed на completed. Общая защита остаётся до наблюдаемого освобождения `_slicing/_start_slice_job/_generate_basement_job/_process_layers_job`; собственных вызовов Stop/kill/retry нет. Если поля не освобождаются, нужен ручной Stop/ожидание либо перезапуск приложения. Для CLI дополнительно учитываются собственные `_process_cli_job`/Glicer и flags захваченных jobs; basement у него отсутствует. Эти приватные поля и `_start_slice_job_build_plate` только читаются наблюдателем, не изменяются плагином.

Защита использует прежний общий lock адаптера. Пока запуск выполняется (включая неопределённый выход после ошибки), `start_slicing`/`set_setting` получают `slicing_busy`; режим, качество/материал и импорт сохраняют прежние `switch_in_progress`, `selection_busy`, `model_load_busy`. Чтения доступны. Изменения печатных mesh/мировых трансформаций, raw-параметров и ID контейнеров глобального/экструдерных/объектных стеков, режима, машины/backend/сцены/платформы делают результат stale. Камера, выделение и служебные узлы слоёв в снимок не входят. Возврат исходной настройки не восстанавливает valid — нужна новая нарезка.

| Код | Значение |
| --- | --- |
| `empty_scene` | Нет печатной модели |
| `unsupported_slicing_mode` | Режим не разрешён либо backend не совпадает с зарегистрированным для режима |
| `slicing_busy` | Конкурирующая операция/занятый backend |
| `auto_slicing_enabled` | Нужно выключить автоматическую нарезку в UI |
| `multiple_build_plates` | Непустая платформа не одна активная |
| `slicing_preview_active` | В сцене импортированный G-code preview |
| `slice_not_found` | Нет такого ID в памяти |
| `slice_not_ready` | Запуск ещё не completed либо завершился failed |
| `slice_result_stale` | Completed относится к прежнему контексту |
| `slicing_failed` | Реальный отказ подготовки/engine/API либо запуск не подтверждён; детали в журнале |
| `slicing_process_failed` | Glicer завершился с ненулевым кодом |
| `slicing_parser_failed` | Ошибка/смена сокета либо выход CLIParser |
| `slicing_timeout` | Истекли 180 секунд, engine автоматически не отменён |
| `slicing_cancelled` | Отмена backend/пользователем |
| `slicing_context_changed` | Печатный контекст изменён во время запуска |
| `slicing_conflict` | Повторный старт, чужое задание/платформа или оценки другого backend |

Также сохраняются `no_active_machine`, `qt_timeout`, `slicer_not_connected`, `slicer_error`. При shutdown classic сохраняет прежнее поведение. Для CLI до30 секунд действует только readonly Qt-poll собственных jobs/Glicer: до подтверждённого выхода guard сохраняется, без scene/PI/engine-действий. По подтверждённому выходу guard освобождается; по истечении30 секунд наблюдение/подписки снимаются с неизвестным исходом и удержанным guard, нужен перезапуск приложения. Timeout/shutdown не убивают native процесс; без Qt event loop cleanup не гарантируется. Из-за отсутствия engine request ID неотличимые ручные операции с тем же backend/платформой запрещены для этого прототипа. Подробности и пределы проверки: [отчёт ТЗ 05](reports/TASK_05_REPORT.md).

ТЗ10 сохраняет захваченные printing_mode/backend_id и проверяет регистрацию classic→CuraEngineBackend, cylindrical→CLIParserBackend. CLI-layout без basement проверяется по подтверждённым полям; неизвестный отклоняется до forceSlice. forceSlice запускает штатные prep → Glicer → ProcessCliJob → CLIParser целиком. Собственные jobs коррелируются по identity/платформе/flags/result/error/cancel; Glicer — новый процесс с exit0. Живой переиспользуемый parser не считается незавершённым Glicer: успех требует его собственного Done после передачи ProcessCliJob, отложенного Qt-readback после окончательных замен G-code, выхода известных jobs/Glicer/слоёв, свежего буфера и подтверждённого PI. Прогресс1/ProcessCliJob.Finished сами по себе не означают completed. Старые/промежуточные оценки игнорируются; повторная последняя оценка должна пройти PI revisions всех полей, чужой backend той же платформы по-прежнему вызывает конфликт. Если до forceSlice parser отсутствовал, допускается ровно одна штатная замена socket внутри синхронного собственного forceSlice, затем socket фиксирован; позднее появление первого parser-процесса допустимо. Смена уже захваченного процесса/сокета/job/буфера не подменяет результат. Изменение финального CLI-буфера in-place также делает результат stale. При отсутствии финала/достоверного захвата стадий будет ошибка/timeout, без синтетического успеха.

Внешние схемы и единицы прежние; null-показатели сопровождаются warnings. Экспорт использует прежние native writers/WriteFileJob и окончательный buffer этого slice_id; workspace writer сохраняет текущие контейнеры с cylindrical/настройками, проверка повторным открытием остаётся ручной. Нет process wait/kill/subprocess из плагина, переназначения native handlers, расчёта времени/траекторий или обхода активации. Протокол Arcus не содержит plugin slice_id; неотличимые сторонние сообщения того же parser при одновременной ручной работе не поддерживаются. [TASK_10_REPORT.md](reports/TASK_10_REPORT.md).

ТЗ11: cylindrical_full→CylindricalBackend — составной конвейер prep → GenerateBasementJob → Cura → Glicer/ProcessCliJob → parser. Outer не имеет собственного parser _process/_socket: getBackends должен содержать зарегистрированные CuraEngineBackend/CLIParserBackend, свободные до старта, с инициализированными sockets для prep.createMessage. Неизвестный/неинициализированный состав отклоняется до forceSlice. Подчинённые Done/промежуточный outer Processing0.5 не финал. Собственные finished связывают ровно по одному штатному reset socket/process Cura и parser; дальнейшие замены/чужие задания/платформы — отказ. Prep проверяется с _start_slice_job_build_plate и Finished, basement — finished/running/error/cancel и native output с законным result=None/plate=None (outer его не назначает), ProcessCliJob — свой процесс/платформа/Finished плюс exit0 Glicer. Нельзя требовать один enum у всех jobs.

Финал full — только outer parser-final и deferred-readback окончательного G-code с последней подтверждённой outer→PI оценкой. Самостоятельного суммирования промежуточных оценок/основания нет. После ошибки/timeout late final может доказать выход стадии и освободить guard, но не превращает failed в completed. Живой idle child не мешает успеху; активная собственная Cura/parser-фаза удерживает guard даже при outer._slicing=false до фактического финала/выхода. Shutdown наследует30с readonly-бюджет, наблюдает также штатные стадии, возникшие после close, без scene/PI/новых действий engine; неизвестный исход требует перезапуска. Итоговые full chunks и контекст защищают прежний export/3MF, но Preview/reopen остаются ручными. [TASK_11_REPORT.md](reports/TASK_11_REPORT.md).

### Штатный экспорт ТЗ 06 — реализован, runtime-приёмка открыта

Каждый метод имеет ровно одно обязательное поле из списка выше: `type:string,minLength:1`, схема `type:object,additionalProperties:false`. Произвольный output_path не принимается. Неверные аргументы — JSON-RPC -32602 до API; без app — slicer_not_connected. Прежние envelope/isError сохранены; failed-статус читается успешно, причина в data.error. Неизвестный ID не заменяется последним.

Оба запуска требуют актуальный completed slice_id в одном из трёх разрешённых режимов из единственного контекста slicing. Старый/неуспешный ID и занятые engine/слои/общий lock отклоняются до writer. Проверяется также readonly LocalFileOutputDevice._writing. Ручная параллельная запись/редактирование не поддерживаются; OutputDeviceManager.writeStarted фиксирует конфликт.

```json
{"export_id":"export-example","slice_id":"slice-1","kind":"gcode","state":"queued","progress":null,"error":null,"artifact":null,"local_directory":null,"valid":false}
```

Start возвращает queued, не ждёт writer. Состояния queued/running/completed/failed. progress — Job.progress 0..100, переведённый в 0..1, либо null (штатный writer может не давать прогресс). Один job.start/процент не подтверждает готовность. Иллюстративный completed:

```json
{"export_id":"export-example","slice_id":"slice-1","kind":"project","state":"completed","progress":null,"error":null,"artifact":{"artifact_id":"artifact-example","file_name":"project.3mf","kind":"project","size_bytes":12345,"slice_id":"slice-1","source":"ste_slicer","writer_id":"3MFWriter","writer_type":"workspace_writer"},"local_directory":"<cache>/AIAssistant/exports/export-random","valid":true}
```

Для G-code — result.gcode, kind=gcode, writer_id=GCodeWriter, writer_type=mesh_writer. ID непрозрачны, не пути. Реестр в памяти содержит только созданные нами проверенные файлы; после перезапуска ID не восстанавливаются. local_directory — диагностический путь прямого MCP; чат удаляет его из tool-result. Скачивание роли2C работает через отдельный зарегистрированный file_id, см. раздел 5. Кэш может очищаться приложением, нужные результаты сохраняются пользователем.

Поиск writer, узлы, создание/запуск WriteFileJob, getResult/getError и проверки контекста — на Qt. Открытие/flush/close/проверка/публикация файлов — в отдельном stdlib I/O worker без app-вызовов. Штатный job входит в writer на worker приложения: WorkspaceWriter переносит тело write на Qt, GCodeWriter переносит thumbnail, остальные чтения выполняет по своему встроенному механизму. Декораторы/дистрибутив не меняются; синхронного writer.write внутри QtBridge нет.

Файлы создаются эксклюзивно в уникальной папке Resources.getCacheStoragePath()/AIAssistant/exports/: writing.tmp, затем проверенный result.gcode/project.3mf. Существующие файлы не перезаписываются/не удаляются. completed требует своего finished, положительного getResult без getError, успешных flush/close, непустого проверенного файла и повторной проверки актуальности нарезки на Qt перед регистрацией артефакта. Весь native write и финализация используют общий lock ТЗ 05; чтения доступны.

G-code проверяется на точное включение engine-данных своей платформы и команды движения с учётом дополнительных заголовков/настроек writer. Проект — ZIP/CRC, model с resources/build/mesh и непустые ожидаемые SteSlicer/ файлы машины, экструдеров, контейнеров, preferences/version. Mesh-only 3MF недостаточен. Это не заменяет native Preview и **повторное открытие как проекта** с проверкой профилей/материалов/overrides/геометрии. Передаются живые верхние root-модели/группы активной платформы без изменения child transforms. Непечатные wrapper-родители/служебные потомки внутри группы пока отклоняются export_scene_unsupported.

Бюджет **120 секунд**, QTimer 100 мс, polling клиента раз в секунду. Timeout/смена контекста дают failed без публикации. До native finished stream не закрывается, lock не освобождается; поздний finished только очищает контекст. Если call_on_qt_thread завис после исключения (в форке Event.set не в finally), нужен перезапуск без автоповтора. После confirmed finished 3MF сбрасывается setStoreArchive(False), archive закрывается в I/O worker. При чужом активном UI writer его archive не трогается, нужен ручной контроль.

Shutdown прекращает публикацию/таймер и освобождает защиту закрываемого адаптера; для ещё пишущего job сохраняется минимальный finished-callback до выхода, затем закрывается stream и убирается временный файл. Если Qt уже не доставляет finished, очистку до выхода процесса гарантировать нельзя. Готовые файлы остаются историческими. После изменения сцены completed-файл сохраняет прежний slice_id, но статус valid=false; изменение во время записи даёт export_result_stale без артефакта.

| Код | Значение |
| --- | --- |
| export_busy / export_not_found | Занята операция или неизвестный export_id |
| export_writer_unavailable | Нет writer требуемого handler/type |
| export_workspace_unavailable / export_scene_unsupported | Неподдерживаемые имена контейнеров / иерархия для штатной записи |
| export_failed | Отказ native writer/job/API |
| export_io_error / export_invalid_file | Ошибка диска/закрытия либо пустые/неполные/повреждённые данные |
| export_timeout | 120 секунд, writer не отменён |
| export_result_stale / export_conflict | Изменение контекста/чужая операция либо ручная параллельная запись |

Preflight сохраняет slice_not_found/slice_not_ready/slice_result_stale; общие slicer_not_connected/qt_timeout/slicer_error и старые busy-коды остальных setters не меняются. Проверки и границы: [отчёт ТЗ 06](reports/TASK_06_REPORT.md).

Основному чату SPA не нужно самостоятельно отправлять операции MCP. Для UI действия агента придут как события целевого REST API. `/mcp` остаётся общей границей инструментов и средством диагностики.

## 3. REST-маршруты чата — реализованы, native-приёмка открыта

Существующий `/api/message` сохраняется как legacy-демо до явного удаления. Асинхронный чат получает отдельный маршрут; не менять незаметно HTTP 200/reply на HTTP 202 в старом маршруте.

| Метод и путь | Назначение | Успех |
| --- | --- | --- |
| `GET /api/status` | Возможности и состояние подключений, лимиты | 200 |
| `POST /api/sessions` | Создать диалог, тело `{}` | 201 |
| `GET /api/sessions` | Список метаданных диалогов и состояние архива, без тел сообщений | 200 |
| `GET /api/sessions/{session_id}` | История и актуальное состояние диалога | 200 |
| `DELETE /api/sessions/{session_id}` | Удалить историю выбранного диалога; без тела/query | 200 |
| `POST /api/sessions/{session_id}/messages` | Принять сообщение и запустить цикл агента | 202 |
| `GET /api/events?session_id=...&after=0` | Получить события после курсора | 200 |
| `POST /api/files` | Роль2A: одно вложение в приватное хранилище, без импорта/LLM | 201 |
| `GET /api/files/{file_id}` | Роль2C: зарегистрированный актуальный native-экспорт | 200, бинарное тело |

Поле capabilities позволяет UI включать только реализованные возможности. REST-настроек LLM нет: ключ вводится только в Qt-меню **AI Assistant → LLM Settings**, URL/модель/ключ сохраняются через Preferences и существующий QtBridge. Пустой ключ при сохранении сохраняет прежний; отдельный флажок удаляет его. После неподтверждённой записи кеш не используется до перечитывания Preferences. Наличие настроек не доказывает связь с провайдером.

### GET /api/status

Форма ответа; значения машины здесь иллюстративны:

```json
{
  "api_version": 1,
  "mode": "plugin",
  "capabilities": {"chat": true, "attachments": true, "events": true, "downloads": true, "background_slice_events": true, "saved_dialogs": true},
  "process_id": "opaque-process-epoch",
  "operation_busy": false,
  "llm": {"configured": true, "has_key": true},
  "agent_state": "idle",
  "slicer": {"connected": true, "machine_name": "Stereotech Hybrid 530 V5.2.4", "printing_mode": "classic"},
  "limits": {"message_characters": 12000, "files_per_message": 5, "file_bytes": 52428800, "image_bytes": 5242880, "images_per_message_bytes": 10485760},
  "accepted_extensions": [".3mf", ".jpeg", ".jpg", ".obj", ".png", ".stl"]
}
```

`mode`: `plugin` или `dev`. `machine_name`/`printing_mode` могут быть null при отсутствии машины; connected не равно «машина готова к нарезке». `llm.configured` означает наличие подтверждённых настроек, не успешность запроса к провайдеру. Здесь нет ключа, URL, модели или значений Preferences. `agent_state` описывает общий turn для всей сцены, не состояние engine. downloads=true означает наличие native resolver→HTTP, не существование готового файла; background_slice_events=true — наличие observer собственных slice/export, не наблюдение всей сцены/истории. В dev оба false. Dev не имеет Preferences/STE и сам не вызывает LLM. Лимиты выше — фактические ограничения этой версии.

### Диалог и сообщение

POST `/api/sessions` возвращает:

```json
{"session_id":"session_1","messages":[],"agent_state":"idle","last_event_id":0,"slice":null,"export":null,"artifacts":[],"tool_events":[]}
```

GET `/api/sessions/{session_id}` возвращает ту же форму с фактической историей. `messages` — упорядоченный массив `{message_id,role,text,attachment_ids,created_at,event_id}`; user также содержит attachments — массив безопасных метаданных upload в исходном порядке, без путей/байтов; role: user/assistant. У user есть `client_message_id`, у assistant — `is_error`. `tool_events` содержит сохранённые tool_started/tool_finished; для восстановления ленты объединить их с messages по `event_id`/`id`. Системный промпт/служебная LLM-история не выдаются. Snapshot и курсор атомарны.

Роль2G добавляет title/created_at/updated_at/revision, restored/history_truncated, process_id, operation_busy и `persistence:{state,saved_revision,error}`. State: temporary (RAM), saving, saved, error; error — безопасная строка или null. Saved означает подтверждённую запись данной revision на диск; принятие202 и idle отдельно этого не обещают. process_id — непрозрачная эпоха процесса, не секрет/путь; она также есть в status/events. После рестарта исторические messages могут иметь role=note, historical=true и attachments только `{name,expired:true}`, без attachment_ids/client_message_id. Полный старый tool-протокол заменён безопасными текстовыми итогами; tool_events после восстановления содержит только события нового процесса.

### Сохранённые диалоги — роль2G

GET `/api/sessions` → `{ready,persistent,error,sessions:[{session_id,title,created_at,updated_at,revision,history_truncated,...}]}`; загруженные в RAM записи также содержат persistence. Ready=false означает асинхронное чтение каталога; error сообщает недоступность/обнаруженную порчу. Persistent означает подключённое хранилище, не гарантию успешной записи. Список отсортирован по updated_at и не загружает все тела в RAM/LLM. GET отдельного сохранённого диалога лениво читает/валидирует его в файловом worker вне ServiceLock/Qt; GET/snapshot/polling доступны при busy. Глобального выбранного диалога на сервере нет. Создание пустого дескриптора сохраняет прежний API-допуск при busy, сцену не меняет; UI блокирует Новый/выбор/удаление/отправку при active/pending/наблюдаемой своей операции. POST сообщения сохраняет прежний общий guard, DELETE отвергается409 при active-turn или собственном активном slice/export.

DELETE → `{session_id,deleted:true}` только после подтверждения файловой операции; не отменяет engine и не удаляет3MF/G-code пользователя. Неизвестный ID404; body/query запрещены, Host/Origin прежние. При history_deleting409/неподтверждённом delete503 сначала перечитать список, автоматически не повторять. Tombstone не даёт поздним snapshot/finish/load вернуть удалённый чат. Повреждённая запись видна отдельной строкой: чтение даёт ошибку, удаление возможно явно. Ошибки archive I/O/timeout выдаются без путей/содержимого; history_loading503, history_not_found, history_io_error/history_timeout503; session_limit409 при пределе числа. Ошибка асинхронного save/квоты видна в persistence.error и не объявляется сохранением.

Runtime-каталог — Resources.getDataStoragePath()/ai_assistant/chats, путь получается штатно наQt, весь файловый I/O — одним worker. ВерсияJSON1, отдельный session_UUID.json и производный index.json; до20 диалогов,100 архивных сообщений/текстовых итогов,512КиБ на чат,10МиБ суммарно с резервом16КиБ под индекс. Новая запись при переполнении получает видимую ошибку; старые файлы не заменяются пустыми. При обрезке старых сообщений history_truncated=true, UI показывает границу архива. Для записи новой копии временно нужно место ещё под файл до512КиБ; temp очищается после обработанной ошибки. Учитываются и посторонние файлы в этом каталоге; копии повреждённого индекса сохраняются с суффиксом.corrupt. Архив локальный JSON без шифрования; произвольные пути клиента не принимаются, ID формирует сервер.

Сохраняются только выбранные текстовые поля, безопасные краткие результаты и имена истёкших вложений. Ключ (также в user/assistant/title), Preferences/auth/raw provider requests, base64/байты, пути, operational IDs и живые URL не сериализуются. Сериализация/санитизация вне ServiceLock, снимок ограничен; монотонная revision и последовательный worker не дают старому снимку заменить новый. Temp→os.replace; saved только после записи файла/индекса. При закрытии Qt только ставит финальный снимок/stop, отдельный non-daemon cleanup worker даёт до2с ожидания внеQt. Предыдущие saved revisions уже на диске; авария ОС/зависший диск может потерять последнюю неподтверждённую revision. Fsync/защита от одновременных экземпляров STE с одним каталогом не обещаются. Порча/неподдержанная версия сохраняются для явного решения; индекс восстанавливается из валидных файлов.

После рестарта: отдельный ограниченный **текстовый исторический контекст**, максимум12 блоков выбранного чата; без raw tool_calls/results/arguments/_attachment_ids, старых IDs/URL и ownership. Точность старого протокола tools не сохраняется. Ничего не выполняется при открытии; interrupted-turn отмечается прерванным, старые pending/client ledger/observer/nativejobs не восстанавливаются. Сцена общая и историей не восстанавливается. Перед первым новым изменением восстановленного разговора нужны успешные get_slicer_state/list_models этого хода; history_refresh_required со stage=preflight/executed=false означает, что изменяющий tool не запускался. Теория не вызывает чтения сама. После успешного первого изменения обычные правила чтения текущих параметров/ID сохраняются.

Frontend сравнивает process_id на status/snapshot/events: при смене выполняет только GET restore, убирает старые capabilities/pending/вложения, сообщает об истечении попытки. Старый pending не POSTится новым процессом даже при совпадении session/native ID или курсора. В том же процессе явный повтор неизвестной отправки использует прежний полный body/client_message_id; автоPOST нет. В dev по умолчанию временные RAM-чаты (saved_dialogs=false); тесты используют явный временный chat_directory, настоящий AppData не трогают. Runtime-приёмка и browser QA отдельны; отчёт ROLE_02G_REPORT.md.

### Приём сообщения

POST `/api/sessions/{session_id}/messages`:

```json
{"client_message_id":"client_1","text":"Прочитай высоту слоя","attachment_ids":[]}
```

`client_message_id` — ID, создаваемый UI для одной попытки отправки; повтор того же сообщения после сетевой ошибки использует тот же ID. Backend возвращает прежний ответ для того же ID и тела, а для того же ID с другим телом — 409. Не повторять автоматически сообщение с новым ID: это может повторить изменение сцены.

`text` — непустая строка после trim, входная длина до 12000 символов; `client_message_id` — непустая строка до 128. Разрешены только эти три поля. attachment_ids можно опустить или передать до5 уникальных зарегистрированных ID (строки до128 символов); суммарно изображения до10MiB. До принятия проверяются типы/лимиты/реестр: неизвестный ID404 file_not_found, превышение413 attachment_limit, дубликаты/неверный тип400 invalid_arguments. Идемпотентность сравнивает принятый trim-текст и полный массив вложений; конфликт с уже принятым ID даёт 409 даже для неподдерживаемого нового вложения. Для всей сцены допускается один активный turn: новый запрос в любом диалоге получает 409 agent_busy. Принятое сообщение появляется в RAM-истории до ответа HTTP202, дисковая запись асинхронна и подтверждается отдельно persistence.saved:

```json
{"session_id":"session_1","message_id":"message_1","client_message_id":"client_1","agent_state":"processing"}
```

Это подтверждение принятия, не готовый ответ LLM и не результат слайсинга. Содержательный ответ поступает событием assistant_message и сохраняется в истории перед idle. Повтор принятого запроса возвращает тот же 202-ответ даже после завершения. Предельные бюджеты turn: 8 LLM-запросов, 16 tools, 120 секунд; сеть — до 30 секунд с учётом остатка. После начатого изменяющего вызова с неподтверждённым исходом остальные действия останавливаются, без повтора или fallback. Непустой ответ LLM сам по себе не подтверждает изменение/нарезку.

Текущий процесс держит ограниченное RAM-состояние: до32 диалогов без архива (dev), до20 при подключённом2G,512 принятых client IDs на диалог,200 видимых сообщений завершённых ходов,12 блоков истории LLM и1000 событий. При лимите диалогов/ID —409 session_limit. Это не пределы JSON-архива:2G сохраняет до100 сообщений/кратких итогов на чат и восстанавливает текстовую историческую справку, без старых tool-протоколов/client ledger/ownership (см. раздел2G выше). UI хранит session_id и неподтверждённую попытку в localStorage, читает snapshot; при смене process_id старый pending не POSTится. Без архива после рестарта неизвестный session_id даёт404 и явное сообщение о сбросе; с2G сохранённый диалог доступен снова. Постоянное сохранение подтверждается только persistence.saved, не RAM/202/idle.

### Upload и вложения — роли2A/2B реализованы; native-приёмка открыта

POST `/api/files`: `multipart/form-data`, одно поле `file`. Content-Type с boundary формирует браузер:

```javascript
const form = new FormData();
form.append("file", selectedFile);
const response = await fetch("/api/files", {method: "POST", body: form});
```

Не выставлять Content-Type вручную для FormData. После успешной проверки типа, размера, безопасного сохранения и назначения ID ответ 201:

```json
{"file_id":"file-opaque","name":"bracket.stl","kind":"model","size_bytes":125000,"mime_type":"model/stl"}
```

`kind`: image (.jpg/.jpeg/.png) или model (.stl/.obj/.3mf). MIME сервер устанавливает после базовой проверки: image/jpeg, image/png, model/stl, model/obj, model/3mf. Заявленному Content-Type части не доверяет. Исходное UTF-8/RFC2231-имя — отображение, не путь: разделители, двоеточие/управляющие символы, хвостовые пробелы запрещены. Физическое имя — серверный UUID; file_id непрозрачен и не разбирается в путь. HTTP не возвращает каталог/физическое имя.

Фактические технические лимиты upload: модель50MiB, изображение5MiB, multipart-обёртка64KiB, суммарно сохранённые файлы250MiB на процесс. JSON64KiB остальных маршрутов не увеличен. Требуются один положительный Content-Length и один multipart Content-Type/boundary (ASCII1–70), без chunked. Ровно один file-part, без preamble/epilogue/дополнительных полей/CTE/дубликатов заголовков; завершающий CRLF необязателен. Header+footer ограничены отдельно, payload пишется блоками до64KiB с конечным размером. Один upload одновременно; второй409 upload_busy. /api/status сообщает files_per_message=5, file_bytes=50MiB, image_bytes=5MiB, images_per_message_bytes=10MiB и список расширений; attachments=true. Размеры multipart/процессной квоты остаются ограничениями upload.

Проверка содержимого базовая, не собственный mesh/image decoder: PNG signature/chunks/CRC/IHDR/IDAT/IEND (до4096 chunks); JPEG markers/SOF/SOS/EOI без полной entropy-декодировки; STL — binary с числом треугольников и размером (включая header solid) либо ASCII solid/facet/vertex/endsolid; OBJ — текст с v и f/l/p, строка до1MiB. Геометрическую корректность позже подтвердит штатный STE-reader. 3MF — ZIP stored/deflate без encrypted/symlink/unsafe/duplicate имён, до1024 частей/200MiB раскрываемого объёма/16MiB основной XML-части, фактическое чтение с CRC. Нужны [Content_Types].xml, _rels/.rels, 3D/3dmodel.model с базовой core2015 resources/build; основной XML UTF-8 без DTD/entities. Архив не извлекается на диск. Нестандартные варианты вне этих границ могут быть отклонены.

ID публикуется только после завершённой записи/проверки. Неправильный/оборванный запрос не регистрирует полуфайл; неуспешный физический файл удаляется. Реестр/квота thread-safe. Ошибки:400 invalid_upload,413 payload_too_large/file_limit_exceeded/archive_too_large,415 unsupported_media_type/unsupported_file_type/file_type_mismatch,409 upload_busy,503 service_stopped; внутренний отказ записи500 upload_storage_error/upload_failed/upload_id_collision без путей/данных. Host/Origin собственного порта/CSP/static whitelist сохранены. Upload-файлы не добавлены в static: GET по upload-ID возвращает404. Скачивание native-экспортов использует отдельный реестр, см. раздел5; публичного metadata API нет.

Один FileStore на AgentService, ленивый собственный временный каталог вне Qt; нет БД/TTL/восстановления ID после перезапуска. close запрещает новые операции и запускает очистку в worker после текущего upload/leases, без ожидания в Qt. Удаляются только собственные учтённые файлы и свой пустой каталог, без recursive delete; чужой файл внутри каталога не удаляется. При аварийном завершении/отказе ОС возможны временные остатки; чужие папки не сканируются для очистки. Dev-сервер при выходе ограниченно ждёт cleanup вне Qt.

Узкий **внутренний** API для2B: metadata(file_id) → копия метаданных; model_path(file_id) → lease своего модельного пути (context manager/close); read_image(file_id) → MIME и ограниченные байты своего изображения; close(). Неизвестный ID404 file_not_found, изменённый/утраченный файл404 file_unavailable, неверный kind415. Получение пути/чтение/очистка — вне Qt, ключи/пользовательский JSON не заменяют реестр. Lease надо удерживать до **фактического native завершения** импорта/отправки, включая model_load_timeout; повторный параллельный close атомарен. В2B callback reader_done передаётся только внутренним Python-параметром существующего adapter.load_model; в схемах его нет. Ранний отказ/не запущенный pending освобождает lease. Запущенный/неопределённый reader удерживается отдельной подпиской на свой fileCompleted независимо от ModelLoad.finish/закрытого QtBridge. Подписка/снятие — на Qt; lease.close не удаляет файлы в Qt. Если сигнала нет, lease удерживается консервативно и файл может остаться на диске после выхода процесса.

После upload отправить file_id в attachment_ids с текстом и client_message_id. Принятое сообщение хранит метаданные для восстановления имён. JPG/PNG кодируются из собственных байтов как data:image/...;base64 в user content image_url, модели представлены только метаданными. В приватной LLM-истории остаются ID; base64 формируется перед complete и не выдаётся snapshot/events. Файлы принятых сессий сохраняются в хранилище до закрытия сервиса, TTL/раннего удаления нет. Ошибка vision/HTTP провайдера выдаётся честно, без текстового fallback-анализа.

load_model_file({file_id}) присутствует в едином MCP/LLM ToolRegistry: только собственная модель, unknown/wrong-kind до API; путь не принимается. Вызывает прежний adapter.load_model/QtBridge, ждёт его подтверждения, возвращает обычные objects плюс file_id и исходное file_name. Это мутация: неподтверждённый импорт останавливает turn и дальнейшие tools. Диагностический load_model(path) по-прежнему исключён из LLM. Upload/HTTP202 сами по себе импорт не подтверждают.

Минимальная SPA выбирает/последовательно загружает файлы, показывает имя/статус/ошибку и удаляет из списка до отправки (физический файл остаётся в квоте процесса). Отправка заблокирована до окончания upload и устранения ошибки. Pending сохраняет исходные IDs/текст/client_message_id и имена; повтор после refresh не перезагружает файл и не создаёт новый ID. Snapshot восстанавливает имена через textContent. Роль2D наблюдает собственные операции, но не запускает нарезку в фоне самостоятельно. Downloads роли2C описаны ниже. Отчёты: [ROLE_02A_REPORT.md](reports/ROLE_02A_REPORT.md), [ROLE_02B_REPORT.md](reports/ROLE_02B_REPORT.md).

## 4. События и прогресс — снимки и observer собственных операций

Polling раз в ~1 секунду, один незавершённый запрос на диалог; при сетевой ошибке увеличить интервал до нескольких секунд. Это обычный короткий GET, не long polling. Ответ:

```json
{
  "session_id":"session_1",
  "events":[{"id":1,"type":"agent_state","created_at":"2026-10-23T12:00:00Z","data":{"state":"processing"}}],
  "next_after":1,
  "has_more":false
}
```

`after` — целое >=0, по умолчанию 0. ID возрастают **внутри одного диалога**. Клиент обрабатывает события по ID, уже полученные не дублирует; после успешной обработки сохраняет next_after. Если событий нет — `events:[]`, курсор прежний. При has_more следующую страницу запросить сразу. Backend может ограничить размер страницы без потери событий.

| type | Поля data | Что показывает UI |
| --- | --- | --- |
| `agent_state` | `state`: idle/processing | Доступность отправки, индикатор работы |
| `assistant_message` | `message_id`, `text`, `attachment_ids`, `created_at` | Завершённое сообщение и объяснение решений |
| `tool_started` | `turn_id`, `tool_call_id`, `name`, `arguments` | Проверенные аргументы без секретов; отказ валидации не имеет started |
| `tool_finished` | `turn_id`, `tool_call_id`, `name`, `result` | Реальный `{ok,data}` либо `{ok:false,error}`; после остановки — tool_not_executed |
| `slice_progress` | `slice_id`, `state`, `progress`, `valid`, `error`, `observed_at`, `result`, необязательный `observation` | Последний фактический снимок из tool/observer |
| `slice_finished` | Та же форма slice с `result` | Подтверждённый get_slicing_result, не завершение turn |
| `export_progress` | `export_id`, `slice_id`, `kind`, `state`, `progress`, `valid`, `error`, `observed_at`, `observation` | Один последний собственный экспорт сессии; готовые файлы отдельно в artifact_ready |
| `artifact_ready` | `artifact` | Метаданные штатного экспорта; file_id/download_url после регистрации; также обновление известного artifact_id |
| `error` | `code`, `message`, `related_id` (или null) | Понятная ошибка с привязкой к действию |

Отказ LLM-tool **до запуска** публикует tool_finished без tool_started: `{ok:false,error:{code:"invalid_tool_arguments",message,stage:"validation",executed:false,arguments_summary,expected?}}`. message для setting-tools — безопасная причина прежнего строгого validator; expected — schema из общего реестра. arguments_summary содержит format=json_object/invalid_json_object, для разобранного объекта только field_types известных полей, missing_fields и unknown_fields_count (число имён вне schema). Значения/неизвестные имена/raw JSON/пути/ключи не копируются; field_types:null означает тип отдельного поля, а не доказательство raw-null всего вызова. Для global extruder_id запрещён даже null, хотя это известное поле общей schema: конкретную причину даёт validator, descriptions объясняют зависимость scope.

Только этот stage=validation/executed=false разрешает модели исправить аргументы в прежнем ограниченном ходе; UI сам не повторяет вызов. Scope/value не подставляются и строки чисел не преобразуются. Правильный пример: `{key:"layer_height",scope:"global",value:0.12}` с JSON-числом, без extruder_id. После допуска к tools.call любые непредвиденные ошибки (включая ValueError), неизвестное исполнение и timeout сохраняют остановку мутаций; они не объявляются безопасной pre-validation ошибкой. MCP продолжает возвращать -32602 по прежним строгим validators. Подробности — [FIX_SETTING_TOOL_REPORT.md](reports/FIX_SETTING_TOOL_REPORT.md).

Пара `(turn_id,tool_call_id)` связывает начало/конец; разные turn могут использовать одинаковый call ID. `slice_id` различает запуски; progress — 0..1 или null, состояния queued/running/completed/failed. `completed` с `valid:false` — устаревший результат; не показывать как актуальный. Повторный актуальный status того же slice сохраняет уже прочитанный result, новый slice/неактуальность его очищают.

Роль2D: один ленивый daemon-worker AgentService хранит максимум две active-записи процесса (slice/export), только после успешных start_slicing/export_gcode/save_project_3mf агента с подтверждённым ID и владельцем session_id. Примерно раз в1с читает прежние tools через adapter/QtBridge вне ServiceLock. При completed/valid slice обязательно читает get_slicing_result; собственный completed export проходит прежнюю регистрацию download2C. Обновляет прежние slice/artifact события и export_progress, без фальшивых tool_started/tool_finished. После idle/ошибки/лимита LLM продолжает наблюдение без LLM-запросов; лимиты8/16/120 прежние. Новых start/export, автоматической цепочки slice→export или повторов мутации нет. Прямой MCP/ручные UI-запуски и прочитанные исторические IDs не становятся собственными наблюдаемыми операциями.

`observation` — необязательное дополнение slice и обязательное для собственного export: `{state:"watching"|"finished"|"stopped",error:null|{code,message}}`. watching относится к наблюдателю, не гарантирует состояние engine; finished означает terminal-наблюдение/подтверждённый result, включая native failed/stale. stopped с observation_interrupted означает прекращение чтений с неизвестным исходом engine, не cancel/failed. Старый снимок без поля — только прежнее tool-наблюдение. Ошибки status/result повторяются ограниченно с backoff1/2/4с; после4 последовательных отказов observer удаляет active-запись, публикует observation_interrupted и оставляет реальное последнее state/error. Ранний get_slicing_result/slice_not_ready и transient qt_timeout от LLM сами не прекращают observer; настоящий slice_result_stale фиксирует stale. Terminal-записи не опрашиваются бесконечно. После terminal внешние изменения становятся известны очередному явному чтению/GET, постоянного сканирования всей сцены нет.

Перед native-мутацией меняется поколение, чтения на её время приостанавливаются. ID/owner/поколение/closed повторно проверяются после Qt и регистрации download. Поздний ответ не заменяет новый ID/сессию и не оживляет known stale. Подтверждённые предварительные отказы slicing_busy/export_busy/selection_busy/model_load_busy/model_operation_busy/arrangement_busy/switch_in_progress сохраняют полезный observer; неопределённая/частичная мутация явно прерывает его и делает кеш stale. Export tools не инвалидируют живой slice. Одинаковые снимки не создают событий; observed_at — время последнего изменения опубликованного снимка, не heartbeat каждого удачного poll. Close выставляет stop/wake и очищает ownership без join на Qt: принятый in-flight read может вернуть поздний ответ, который игнорируется; новых циклов/публикаций нет. Engine/guard/native jobs не отменяются.

Snapshot.export до собственного запуска null, затем один последний собственный экспорт сессии (не история):

```json
{"export_id":"export-1","slice_id":"slice-1","kind":"gcode","state":"running","progress":null,"valid":false,"error":null,"observed_at":"2026-10-24T06:00:00Z","observation":{"state":"watching","error":null}}
```

kind=project/gcode. Нет local_directory/path/identity/байтов; pending-экспорт не является artifact. После завершения готовый файл по-прежнему приходит artifact_ready и хранится в artifacts. Новый свой экспорт заменяет только snapshot.export; предыдущие готовые artifacts сохраняются. Restore/410 восстанавливает оба статуса из snapshot, не запускает операции. Memory-only ownership/история не переживают рестарт. Копия baseline_296 этого observer и поля export не имеет. Native-приёмка новой версии отдельно открыта: [ROLE_02D_REPORT.md](reports/ROLE_02D_REPORT.md).

Страница — до 100 событий, хранится 1000. Устаревший курсор — 410 events_expired; курсор из будущего/неверный — 400 invalid_arguments. UI перечитывает snapshot и продолжает с last_event_id без дубликатов.

## 5. Итог слайсинга и файлы — фактическая форма MVP

Одинаковый `result` в slice_finished и `slice.result` snapshot:

```json
{
  "slice_id":"slice-1",
  "print_time_seconds": 5400,
  "material": {"length_mm":6100,"weight_g":18.2},
  "printing_mode":"classic",
  "backend_id":"CuraEngineBackend",
  "build_plate":0,
  "source":"ste_slicer"
}
```

Недоступные время/масса/длина — null, не выдуманный 0. Это оценки слайсера и суммарный материал без неподтверждённого распределения по экструдерам. Форма совпадает с MCP get_slicing_result (включая возможные warnings), а не с прежним проектным materials[]. `slice`: `{slice_id,state,progress,valid,error,observed_at,result,observation?}`, до первого наблюдения null. После изменения проекта кеш помечается неактуальным; при ручных изменениях вне агента актуальность выясняется следующим tool/observer-чтением или GET скачивания.

Артефакт в artifact_ready и массиве artifacts snapshot:

```json
{"artifact_id":"artifact-1","file_name":"project.3mf","kind":"project","size_bytes":1234,"slice_id":"slice-1","source":"ste_slicer","writer_id":"3MFWriter","writer_type":"workspace_writer","valid":true,"file_id":"download-example","download_url":"/api/files/download-example"}
```

`kind`: project/gcode (как у штатного экспорта). Метаданные появляются после успешного get_export_status, выполненного агентом либо observer его собственного экспорта. При completed/valid=true AgentService вне своего lock вызывает внутренний resolver адаптера через QtBridge: только принятый завершённый экспорт и актуальный slice. Затем отдельный DownloadRegistry вне Qt проверяет доверенный файл и регистрирует стабильные file_id/download_url. Повторное одинаковое чтение не создаёт ID/событие заново. Прямой MCP tools/call не наполняет snapshot чата и сам URL не регистрирует. Завершение нарезки не подтверждает экспорт; G-code создаёт слайсер.

Путь/идентичность файла остаются внутренними полями resolver, отсутствующего в MCP/LLM. Канонические метаданные берутся из него, не из tool JSON. Ошибка регистрации оставляет карточку без URL с download_error:{code,message}; историческая карточка без URL остаётся локальным результатом. При известном изменении сцены/настроек или stale-статусе artifact_ready обновляет карточку valid=false; клиент объединяет по artifact_id, восстанавливает artifacts из snapshot после refresh/410. Observer наблюдает только активные свои ID: ручные изменения могут стать известны позже, GET всегда перепроверяет актуальность. UI показывает ссылку только при valid===true и собственном относительном `/api/files/{id}`, имя через textContent.

GET принимает только зарегистрированный download-ID без query/path. Upload-ID и неизвестные ID —404. Перед заголовками resolver повторно проверяет native-актуальность через Qt; вне Qt lstat/open/fstat сверяют обычный файл, канонический путь, размер и сохранённые при native-публикации dev/ino/size/mtime_ns. Неизвестный/исчезнувший файл —404 file_not_found; неподтверждённые метаданные —404 artifact_unavailable; stale —409 artifact_stale; замена/изменение —409 artifact_changed; Qt недоступен —503 slicer_unavailable; сервис закрыт —503 service_stopped; отказ передачи до заголовков —503 download_unavailable. Регистрация при коллизии UUID —503 download_id_conflict без замены прежнего ID. Ошибки JSON стандартные {error:{code,message}}, без путей. После начала потока ошибка закрывает handle/соединение без дописывания JSON. Экспорт автоматически не повторяется.

Успех: Content-Type=model/3mf для project, application/octet-stream для gcode; Content-Length, Content-Disposition attachment с UTF-8 filename*, Cache-Control:no-store, nosniff и прежний CSP. Имя запрещает CRLF/сепараторы пути, заголовок percent-encoded. Передача блоками до64KiB без ServiceLock/engine guard/Qt. Range/HEAD/resume не добавлены. Закрытие запрещает новые регистрации/GET, открытая передача может закончиться; принятые native-файлы не копируются в upload и не удаляются его очисткой. ID живут до рестарта. Fingerprint защищает от обычной замены/изменения, без криптографической гарантии против внешнего редактора с сохранением mtime. Изменения проекта/файла после начала передачи её не отзывают. Нужные результаты сохраните из кэша. Проверки и ограничения: [ROLE_02C_REPORT.md](reports/ROLE_02C_REPORT.md).

## 6. Ошибки нового API

Новые REST-маршруты возвращают HTTP-код и тело:

```json
{"error":{"code":"agent_busy","message":"Дождитесь завершения текущего ответа"}}
```

| HTTP | Коды/ситуации |
| --- | --- |
| 400 | `invalid_request`, `invalid_arguments` |
| 403 | `forbidden_origin` |
| 404 | `session_not_found`, `file_not_found`, `file_unavailable`, `artifact_unavailable`, `not_found` (settings отсутствует) |
| 409 | `agent_busy`, `message_id_conflict`, `session_limit`, `artifact_stale`, `artifact_changed` |
| 410 | `events_expired` |
| 413 | `payload_too_large`, `attachment_limit` |
| 415 | `unsupported_media_type` |
| 500 | `internal_error`; техническая причина только в безопасном журнале |
| 503 | `llm_not_configured`, `service_stopped`, `slicer_unavailable`, `download_unavailable`, `download_id_conflict` |

После HTTP 202 ошибки работы агента/инструментов/слайсинга приходят событиями и сохраняются в состоянии диалога. Отказ инструмента не превращается в успех HTTP-операции над принтером. Внешние детали провайдера не копировать целиком в ответ, особенно заголовки авторизации.

UI различает HTTP-ошибку, обрыв связи и операционную ошибку события. После сетевого сбоя не повторять автоматически изменение сцены или настройки. Для повторной отправки сообщения используется прежний client_message_id; для выяснения фактического состояния — snapshot/чтение.

Legacy health/message, MCP и static сохраняют прежние HTTP-ошибки со строкой error; новый API использует объект выше. Операционные ошибки STE (включая slicer_not_connected) приходят в tool_finished.result и не маскируются успешным изменением. После 202 возможны error-коды network_timeout/network_error/auth_error/tls_error, invalid_response/invalid_message/invalid_tool_call/empty_message, step_limit/tool_limit/budget_exceeded, mutation_unconfirmed/agent_failed/worker_failed/service_stopped. Ошибка инструмента сохраняет прежний код адаптера; неподтверждённая мутация завершает turn. Ключ редактируется в данных/тексте, raw provider bodies не выдаются.

При network_timeout существующее error.message/ошибочное assistant_message содержит номер LLM-обращения в ходе, измеренное monotonic время вызова complete, выделенный бюджет min(30с, остаток120с) и остаток бюджета хода перед обращением. Дополнительно — число подтверждённых изменяющих вызовов до отказа (включая0); их фиксированные имена/реальные tool-results сохраняются как прежде. Это количество успешных tool-ответов/принятых запусков, не число изменённых настроек и не completed engine. Новых полей/эндпойнтов/логов нет; UI показывает сообщение прежним способом. Время complete включает локальную подготовку/transport/чтение, не отделяет сеть/очередь/генерацию провайдера. Задержка этим изменением не устранена,30/120/8/16/retry-политика прежние; поздний ответ не запускает tools. См. [FIX_LLM_TIMEOUT_REPORT.md](reports/FIX_LLM_TIMEOUT_REPORT.md).

## 7. Последовательность работы frontend

1. Прочитать /api/status; показать реальные возможности и configured, не «LLM подключена». Настройки вводятся только в Qt.
2. Создать session или восстановить snapshot; отрисовать messages и tool_events, продолжить polling с last_event_id.
3. Перед отправкой сохранить полное тело с client_message_id. Принять 202 как принятие; отрисовать канонический user из snapshot, без оптимистичного дубликата.
4. Обрабатывать tool/progress/error и assistant_message. Не отождествлять idle с завершением engine. Вложения доступны; artifact_ready обновляет карточку по artifact_id, download_url доступен только для подтверждённого native-файла.
5. При обрыве/5xx сохранить тот же ID, перечитать snapshot и повторять только явным действием с тем же телом. 409 не означает, что можно создать новый ID. При 410 восстановить snapshot. После перезапуска процесса объяснить сброс истории, не повторять изменения.

Временный UI из b9c0190 адаптирован к этому контракту; дальнейший дизайн остаётся задачей frontend. Целевой раздел о файлах не является разрешением включать отсутствующие функции.

## 8. Исходники, Git и установленная копия

- Редактируемая и версионируемая папка: `plugin/AIAssistant/` в этом репозитории. Она уже включена в первоначальный коммит, в том числе временный frontend `web/`.
- Установленная копия: `%APPDATA%/steslicer/7.10/plugins/AIAssistant/` (для другой версии путь уточнить через меню приложения). Она нужна для запуска, не является вторым исходным репозиторием и целиком обратно в Git не копируется.
- Фронтендер меняет `plugin/AIAssistant/web/` в своей ветке. После согласования объединяем изменения. Наш код интеграции не должен переписывать его UI.
- Для проверки новых файлов frontend обновить `web/` установленного плагина и перезагрузить страницу. Для изменения Python — обновить файлы плагина и перезапустить слайсер после сохранения проекта.
- Сейчас сервер раздаёт только `/`, `/app.js`, `/style.css` по фиксированному списку `STATIC_FILES`. Новый JS/CSS/изображения требуют согласованного расширения раздачи; произвольный файл в web автоматически доступным не становится.
- Текущая CSP разрешает ресурсы только со своего origin и блокирует inline-скрипты/обработчики. Использовать внешние локальные JS/CSS; необходимость новых ресурсов согласовать, а не отключать CSP целиком.
- Push на GitHub не обновляет установленную копию автоматически. В Git не должны попадать конфигурация приложения, ключи, логи, `__pycache__` и лицензии.
- Рекомендуемый рабочий цикл: изменить исходники -> проверить -> обновить установленную копию -> проверить runtime -> зафиксировать проверенный результат. Временный UI можно заменить без отдельного этапа удаления.
