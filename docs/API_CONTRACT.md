# Контракт API между SPA и Python-плагином

Версия документа: 1, 23.10.2026. Для фронтенда, HTTP/backend и интеграции STE Slicer. Это единое место описания HTTP-границы; изменение маршрутов, полей и событий согласовывать до изменения клиентов.

**Статус:** в интеграции b9c0190 реализованы status, sessions, messages и polling событий (разделы 3–4, 6–7). Чат использует существующий реестр и адаптер. Вложения и скачивание остаются целевыми возможностями, маршруты `/api/files` отсутствуют. Все новые сценарии пока проверены только изолированно; native-приёмка открыта. Краткая инструкция клиенту — [FRONTEND_INTEGRATION.md](FRONTEND_INTEGRATION.md).

Интеграция моделей из [ТЗ 04](tasks/TASK_04_MODEL_LOADING.md) реализована для прямого MCP, см. раздел 2; ручная приёмка новых методов ещё не проводилась. HTTP-upload этим заданием не добавлен, приёмка ТЗ 03 остаётся отдельным открытым пунктом.

Инструменты classic-нарезки [ТЗ 05](tasks/TASK_05_REAL_3D_SLICING.md) и штатного экспорта ТЗ 06 ожидают ручной проверки в STE Slicer. Агент публикует только реально прочитанные им снимки прогресса/результата; фонового наблюдения engine нет. 5D-нарезка не реализована.

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

`/api/message` сохранён как legacy-демо и LLM не вызывает. Новый SPA его не использует: новый чат работает через sessions/messages/events ниже. История хранится в памяти процесса и восстанавливается после обновления вкладки. `/api/health` означает только доступность сервера; `mode:scaffold` здесь сохранён ради совместимости и не описывает готовность нового чата. `assistant_status` также оставлен прежним диагностическим инструментом, исключён из LLM; готовность нового API показывает `/api/status`.

Текущий POST `/api/message`: непустой `text` после trim, не более 12000 символов; тело запроса до 65536 байт. Пустой/неверный текст — 400, неверный Content-Type — 415, неверный размер — 413. Неизвестный путь — 404, недопустимый Host/Origin — 403. HTTP-ошибка имеет **старый** формат `{"error":"строка"}`. У GET пока нет полноценной диагностики готовности слайсера: health означает доступность сервера, а не готовность машины, LLM или движка.

### Действующие MCP-инструменты

`initialize`, `tools/list`, `tools/call`. Авторитетный список схем — ответ `tools/list`, формируемый общим реестром `agent/tools.py`.

- `assistant_status`, аргументы `{}`: диагностирует объём реализации; не подтверждает реальный runtime.
- `get_setting`, аргументы `{"key":"layer_height","scope":"global"}` либо `{"key":"material_print_temperature","scope":"extruder","extruder_id":"0"}`.
- `set_setting`, те же key/scope/extruder_id и обязательное value; прежний вызов `{"key":"layer_height","scope":"global","value":0.24}` сохранён.
- `describe_settings`, аргументы `{"scope":"global"}` либо `{"scope":"extruder","extruder_id":"0"}`: allowlist с фактическими значениями/метаданными текущего стека.
- `get_slicer_state`, аргументы `{}`: состояние приложения, активной машины, режима и backend.
- `set_print_mode`, аргументы `{"mode":"classic"}`: разрешены только `classic`, `cylindrical`, `cylindrical_full`; подтверждает настройку и выбранный backend. Вызывать на пустой сцене, без активной нарезки.
- `list_quality_profiles`, аргументы `{}`: все встроенные QualityGroup текущей конфигурации, включая недоступные.
- `list_materials`, аргументы `{"extruder_id":"0"}`: материалы под конкретную позицию экструдера и текущее сопло/диаметр/платформу.
- `select_profile`, аргументы `{"quality_type":"runtime-quality-type"}`: выбор доступной встроенной QualityGroup с сохранением overrides.
- `select_material`, аргументы `{"extruder_id":"0","root_material_id":"runtime-root-id"}`: выбор материала указанного экструдера с подтверждением resolved container.
- `load_model`, аргументы `{"path":"C:/Models/bracket.stl"}`: локальный mesh-импорт STL/OBJ/3MF с подтверждением завершения и новых объектов.
- `list_models`, аргументы `{}`: верхние модели текущей сцены с непрозрачными ID и мировыми границами.
- `get_model_bounds`, аргументы `{"object_id":"model-1"}`: актуальные мировые границы живой модели.
- `start_slicing`, аргументы `{}`: короткий запуск штатной classic-нарезки, возвращает идентификатор для polling.
- `get_slicing_status`, аргументы `{"slice_id":"slice-1"}`: состояние, прогресс и актуальность выбранного запуска.
- `get_slicing_result`, аргументы `{"slice_id":"slice-1"}`: время/материал только завершённого актуального запуска.
- `export_gcode`, аргументы `{"slice_id":"slice-1"}`: запуск штатного GCodeWriter из mesh-handler.
- `save_project_3mf`, аргументы `{"slice_id":"slice-1"}`: запуск 3MFWriter из workspace-handler, сохранение проекта.
- `get_export_status`, аргументы `{"export_id":"export-..."}`: статус записи и готовый локальный артефакт.

У новых инструментов лишние поля запрещены. ТЗ 07 расширяет get/set_setting на 19 ключей ниже; передавать `printing_mode` по-прежнему нельзя. ТЗ 02 принято после 45 автоматических проверок и реального прогона в STE Slicer 7.10.12 на Hybrid 530 V5.2.4: диагностика, три режима, backend, UI и восстановление подтверждены. Это не проверка слайсинга, экспорта или новых настроек ТЗ 07.

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

### Настройки ТЗ 07 — реализованы, runtime-приёмка открыта

| Тип для ранней валидации | Allowlist |
| --- | --- |
| float | layer_height, layer_height_0, line_width, top_bottom_thickness, infill_sparse_density, support_angle, material_print_temperature, material_bed_temperature, speed_print, retraction_amount, retraction_speed, cool_fan_speed |
| int | wall_line_count |
| bool | support_enable, retraction_enable, cool_fan_enabled |
| enum | infill_pattern, support_type, adhesion_type |

Таблица подтверждена definitions форка и ограничивает синтаксис запроса; она не обещает поддержку текущей машиной. Нужны реальное наличие ключа и совпадающий runtime type. Машина/сопло, start/end G-code, printing_mode, произвольные ключи, строки-выражения и batch setter не допускаются. Режим меняется только set_print_mode.

Schemas get/set: обязательные key/scope и для setter value; key — enum allowlist, scope — global/extruder, extruder_id — необязательная в JSON-schema строка minLength:1, value — number/boolean/string, additionalProperties:false. Общий валидатор требует extruder_id **только** для extruder, запрещает его для global и проверяет тип конкретного ключа. Describe имеет обязательный scope и такое же правило extruder_id. Неверная структура, bool вместо числа, дробный int, NaN/inf, числовая строка и неподдержанный ключ — -32602 до API. Целое 3.0 допускается для int и нормализуется в 3 без округления дроби. Только layer_height сохраняет раннее >0; нулевое заполнение может быть допустимо.

extruder_id — точный строковый ключ global_stack.extruders, без смены UI-позиции и подстановки активного экструдера. Неизвестная позиция — extruder_not_found. Глобальный стек и userChanges целевого стека читаются на Qt. Единицы, options, enabled, settable_globally/settable_per_extruder и четыре bounds берутся из вычисленных runtime-свойств, readonly — из userChanges.getReadOnly. Enum принимает ключ актуального options, не подпись. Неподтверждённые тип/разрешение/enabled/readonly запрещают запись; отключённые и readonly настройки можно читать.

Обычные get/set возвращают прежние key/scope/unit/value либо before/after, для extruder добавляется extruder_id. Пример setter data:

```json
{"key":"material_print_temperature","scope":"extruder","extruder_id":"0","unit":"°C","before":205.0,"after":210.0}
```

Describe data имеет `{scope,extruder_id?,settings:[...]}` ровно для allowlist, включая недоступные ключи. Иллюстративный элемент:

```json
{"key":"speed_print","available":true,"type":"float","value":50.0,"unit":"mm/s","enabled":true,"settable":true,"options":{},"settable_globally":true,"settable_per_extruder":true,"read_only":false,"minimum_value":0.0,"maximum_value":null,"minimum_value_warning":10.0,"maximum_value_warning":150.0,"limitations":[]}
```

available означает наличие value в стеке, settable — разрешение выбранной области с учётом userChanges/readonly; enabled/type проверяются отдельно. Неизвестные поля и непригодное значение — null с limitations, не собственная догадка. Пустая строка unit допустима как runtime-единица без размерности. Отсутствующая граница — null; нечисловая/неконечная граница помечается limitation. Неизвестное enum options или значение вне них не выдаётся за подтверждённое value.

Запись идёт только в userChanges целевого стека. Hard bounds запрещают выход, несогласованные/невычисленные hard bounds дают slicer_error. Warning bounds не запрещают запись: при выходе setter добавляет warnings, как и при недоступных необязательных метаданных. Ни одна температура не является командой нагрева. Зависимые значения могут пересчитаться штатно; сохранение всех derived values не обещается.

Before/after читаются из стека и нормализуются по runtime-типу; запрос не подставляется вместо after. Смена машины/целевого стека/userChanges или отсутствие подтверждения — ошибка. Подтверждённый no-op не вызывает setter и не инвалидирует slice. Реальная попытка изменения, включая неопределённый setter, инвалидирует прежнюю нарезку/будущий экспорт; автоматического отката/повтора нет. Прежний общий lock охватывает импорт/нарезку/экспорт/выбор профиля; записи получают slicing_busy, чтения доступны. QtBridge сохраняет обычный бюджет 5 секунд и отмену просроченного pending callback; после running timeout нужны чтение/UI до дальнейших изменений.

Дополнительные операционные коды: setting_metadata_unavailable, setting_option_unavailable, setting_disabled, setting_read_only, setting_scope_unavailable, setting_context_changed. Сохранены setting_unavailable/setting_out_of_range/setting_not_applied, extruder_not_found/no_active_machine, slicing_busy/qt_timeout/slicer_error/slicer_not_connected. Полные проверки и ограничения: [отчёт ТЗ 07](reports/TASK_07_REPORT.md).

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

### Classic-нарезка ТЗ 05 — реализована, runtime-приёмка открыта

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

До forceSlice проверяются classic и идентичность runtime-backend, активная машина, одна непустая активная платформа, отсутствие G-code preview, импорта и подготовки/обработки слоёв. При включённом `general/auto_slice` требуется выключить его вручную в UI. Плагин не меняет Preferences. Ручной параллельный запуск/редактирование не поддерживаются; обнаруженный конфликт не считается нашим успехом.

Подписки устанавливаются до forceSlice на Qt. Завершение требует начала (`slicingStarted/Processing`), собственного `Done`, отдельного Qt-callback после финализации, свободного backend, неизменного контекста, свежего G-code и собственных оценок платформы с последующим обновлением всех трёх полей PrintInformation. Начальный `NotStarted`, один progress=1, старый буфер/время и чужой Done недостаточны. Учтена штатная замена пустых списков в GenerateBasementJob/первом GCodeLayer; непустой захваченный буфер другим списком не подменяется. Оценка другого зарегистрированного backend той же платформы — конфликт.

Таймер наблюдения на Qt — 250 мс, бюджет принятого запуска — **180 секунд**, без блокирующего ожидания на Qt/HTTP. После `slicing_timeout`, отмены или ошибки поздний Done не меняет failed на completed. Общая защита остаётся до наблюдаемого освобождения `_slicing/_start_slice_job/_generate_basement_job/_process_layers_job`; собственных вызовов Stop/kill/retry нет. Если поля не освобождаются, нужен ручной Stop/ожидание либо перезапуск приложения. Эти приватные поля и `_start_slice_job_build_plate` читаются только в slicing.py, не изменяются плагином.

Защита использует прежний общий lock адаптера. Пока запуск выполняется (включая неопределённый выход после ошибки), `start_slicing`/`set_setting` получают `slicing_busy`; режим, качество/материал и импорт сохраняют прежние `switch_in_progress`, `selection_busy`, `model_load_busy`. Чтения доступны. Изменения печатных mesh/мировых трансформаций, raw-параметров и ID контейнеров глобального/экструдерных/объектных стеков, режима, машины/backend/сцены/платформы делают результат stale. Камера, выделение и служебные узлы слоёв в снимок не входят. Возврат исходной настройки не восстанавливает valid — нужна новая нарезка.

| Код | Значение |
| --- | --- |
| `empty_scene` | Нет печатной модели |
| `unsupported_slicing_mode` | Не classic или backend не совпадает с classic |
| `slicing_busy` | Конкурирующая операция/занятый backend |
| `auto_slicing_enabled` | Нужно выключить автоматическую нарезку в UI |
| `multiple_build_plates` | Непустая платформа не одна активная |
| `slicing_preview_active` | В сцене импортированный G-code preview |
| `slice_not_found` | Нет такого ID в памяти |
| `slice_not_ready` | Запуск ещё не completed либо завершился failed |
| `slice_result_stale` | Completed относится к прежнему контексту |
| `slicing_failed` | Реальный отказ подготовки/engine/API либо запуск не подтверждён; детали в журнале |
| `slicing_timeout` | Истекли 180 секунд, engine автоматически не отменён |
| `slicing_cancelled` | Отмена backend/пользователем |
| `slicing_context_changed` | Печатный контекст изменён во время запуска |
| `slicing_conflict` | Повторный старт, чужое задание/платформа или оценки другого backend |

Также сохраняются `no_active_machine`, `qt_timeout`, `slicer_not_connected`, `slicer_error`. При shutdown собственные сигналы/таймер снимаются через Qt, отложенные callbacks становятся неактивны, ссылки/свой lock освобождаются; чужой engine не завершается. Из-за отсутствия engine request ID неотличимые ручные операции с тем же backend/платформой запрещены для этого прототипа. Подробности и пределы проверки: [отчёт ТЗ 05](reports/TASK_05_REPORT.md).

### Штатный экспорт ТЗ 06 — реализован, runtime-приёмка открыта

Каждый метод имеет ровно одно обязательное поле из списка выше: `type:string,minLength:1`, схема `type:object,additionalProperties:false`. Произвольный output_path не принимается. Неверные аргументы — JSON-RPC -32602 до API; без app — slicer_not_connected. Прежние envelope/isError сохранены; failed-статус читается успешно, причина в data.error. Неизвестный ID не заменяется последним.

Оба запуска требуют актуальный completed slice_id в classic из единственного контекста ТЗ 05. Старый/неуспешный ID и занятые engine/слои/общий lock отклоняются до writer. Проверяется также readonly LocalFileOutputDevice._writing. Ручная параллельная запись/редактирование не поддерживаются; OutputDeviceManager.writeStarted фиксирует конфликт.

```json
{"export_id":"export-example","slice_id":"slice-1","kind":"gcode","state":"queued","progress":null,"error":null,"artifact":null,"local_directory":null,"valid":false}
```

Start возвращает queued, не ждёт writer. Состояния queued/running/completed/failed. progress — Job.progress 0..100, переведённый в 0..1, либо null (штатный writer может не давать прогресс). Один job.start/процент не подтверждает готовность. Иллюстративный completed:

```json
{"export_id":"export-example","slice_id":"slice-1","kind":"project","state":"completed","progress":null,"error":null,"artifact":{"artifact_id":"artifact-example","file_name":"project.3mf","kind":"project","size_bytes":12345,"slice_id":"slice-1","source":"ste_slicer","writer_id":"3MFWriter","writer_type":"workspace_writer"},"local_directory":"<cache>/AIAssistant/exports/export-random","valid":true}
```

Для G-code — result.gcode, kind=gcode, writer_id=GCodeWriter, writer_type=mesh_writer. ID непрозрачны, не пути. Реестр в памяти содержит только созданные нами проверенные файлы; после перезапуска ID не восстанавливаются. local_directory — диагностический путь прямого MCP; будущий download API должен работать по зарегистрированному artifact_id. Новых файловых HTTP-маршрутов нет. Кэш может очищаться приложением, нужные результаты копируются пользователем.

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
| `GET /api/sessions/{session_id}` | История и актуальное состояние диалога | 200 |
| `POST /api/sessions/{session_id}/messages` | Принять сообщение и запустить цикл агента | 202 |
| `GET /api/events?session_id=...&after=0` | Получить события после курсора | 200 |
| `POST /api/files` | Будущий upload, сейчас отсутствует | 404 |
| `GET /api/files/{file_id}` | Будущее скачивание, сейчас отсутствует | 404 |

Поле capabilities позволяет UI включать только реализованные возможности. REST-настроек LLM нет: ключ вводится только в Qt-меню **AI Assistant → LLM Settings**, URL/модель/ключ сохраняются через Preferences и существующий QtBridge. Пустой ключ при сохранении сохраняет прежний; отдельный флажок удаляет его. После неподтверждённой записи кеш не используется до перечитывания Preferences. Наличие настроек не доказывает связь с провайдером.

### GET /api/status

Форма ответа; значения машины здесь иллюстративны:

```json
{
  "api_version": 1,
  "mode": "plugin",
  "capabilities": {"chat": true, "attachments": false, "events": true, "downloads": false, "background_slice_events": false},
  "llm": {"configured": true, "has_key": true},
  "agent_state": "idle",
  "slicer": {"connected": true, "machine_name": "Stereotech Hybrid 530 V5.2.4", "printing_mode": "classic"},
  "limits": {"message_characters": 12000, "files_per_message": 0, "file_bytes": 0},
  "accepted_extensions": []
}
```

`mode`: `plugin` или `dev`. `machine_name`/`printing_mode` могут быть null при отсутствии машины; connected не равно «машина готова к нарезке». `llm.configured` означает наличие подтверждённых настроек, не успешность запроса к провайдеру. Здесь нет ключа, URL, модели или значений Preferences. `agent_state` описывает общий turn для всей сцены, не состояние engine. Dev не имеет Preferences/STE и сам не вызывает LLM. Лимиты выше — фактические ограничения этой версии.

### Диалог и сообщение

POST `/api/sessions` возвращает:

```json
{"session_id":"session_1","messages":[],"agent_state":"idle","last_event_id":0,"slice":null,"artifacts":[],"tool_events":[]}
```

GET `/api/sessions/{session_id}` возвращает ту же форму с фактической историей. `messages` — упорядоченный массив `{message_id,role,text,attachment_ids,created_at,event_id}`; role: user/assistant. У user есть `client_message_id`, у assistant — `is_error`. `tool_events` содержит сохранённые tool_started/tool_finished; для восстановления ленты объединить их с messages по `event_id`/`id`. Системный промпт/служебная LLM-история не выдаются. Snapshot и курсор атомарны.

POST `/api/sessions/{session_id}/messages`:

```json
{"client_message_id":"client_1","text":"Прочитай высоту слоя","attachment_ids":[]}
```

`client_message_id` — ID, создаваемый UI для одной попытки отправки; повтор того же сообщения после сетевой ошибки использует тот же ID. Backend возвращает прежний ответ для того же ID и тела, а для того же ID с другим телом — 409. Не повторять автоматически сообщение с новым ID: это может повторить изменение сцены.

`text` — непустая строка после trim, входная длина до 12000 символов; `client_message_id` — непустая строка до 128. Разрешены только эти три поля. attachment_ids можно опустить или передать []; непустой список отклоняется `400 attachments_unavailable`. Идемпотентность сравнивает принятый trim-текст и полный массив вложений; конфликт с уже принятым ID даёт 409 даже для неподдерживаемого нового вложения. Для всей сцены допускается один активный turn: новый запрос в любом диалоге получает 409 agent_busy. Принятое сообщение сохраняется в истории до ответа HTTP 202:

```json
{"session_id":"session_1","message_id":"message_1","client_message_id":"client_1","agent_state":"processing"}
```

Это подтверждение принятия, не готовый ответ LLM и не результат слайсинга. Содержательный ответ поступает событием assistant_message и сохраняется в истории перед idle. Повтор принятого запроса возвращает тот же 202-ответ даже после завершения. Предельные бюджеты turn: 8 LLM-запросов, 16 tools, 120 секунд; сеть — до 30 секунд с учётом остатка. После начатого изменяющего вызова с неподтверждённым исходом остальные действия останавливаются, без повтора или fallback. Непустой ответ LLM сам по себе не подтверждает изменение/нарезку.

История хранится в памяти: до 32 диалогов, 512 принятых ID на диалог, 200 видимых сообщений, 12 целых блоков истории LLM (включая tools и частично выполненные действия при ошибке), 1000 событий. При достижении лимита диалогов/ID — 409 session_limit. UI сохраняет session_id и неподтверждённую попытку (полное тело/ID) в localStorage, восстанавливает snapshot; после перезапуска плагина неизвестный session_id даёт 404, UI объясняет сброс и ничего автоматически не повторяет. Постоянной истории нет.

### Вложения и скачивание — только целевой контракт, не реализованы

POST `/api/files`: `multipart/form-data`, одно поле `file`. Content-Type с boundary формирует браузер:

```javascript
const form = new FormData();
form.append("file", selectedFile);
const response = await fetch("/api/files", {method: "POST", body: form});
```

Не выставлять Content-Type вручную для FormData. После успешной проверки типа, размера, безопасного сохранения и назначения ID ответ 201:

```json
{"file_id":"file_1","name":"bracket.stl","kind":"model","size_bytes":125000,"mime_type":"model/stl"}
```

`kind`: `image` или `model`; расширение/содержимое проверяет сервер, присланному MIME слепо не доверять. Неизвестные и недоступные file_id отклонять. Загрузка файла ещё не означает загрузку модели в сцену: эту операцию отдельно выполняет инструмент агента.

Через API отдавать ID и безопасное исходное имя, не произвольные пути пользователя. GET `/api/files/{file_id}` раздаёт только зарегистрированные backend файлы и проверенные артефакты. Для скачивания — Content-Disposition attachment с безопасным именем и фактический MIME. Не применять лимит JSON-запроса 64 KiB к multipart; загрузчику нужен отдельный конечный лимит из `/api/status`.

UI отправляет attachment_ids в сообщение после завершения upload. При ошибке загрузки не изображать файл прикреплённым успешно. Очистка временных файлов — обязанность backend; отдельный DELETE для MVP не требуется.

## 4. События и прогресс — реализованы снимки, без фонового мониторинга

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
| `slice_progress` | `slice_id`, `state`, `progress`, `valid`, `error`, `observed_at`, `result` | Последний снимок, полученный tools, не текущее состояние engine |
| `slice_finished` | Та же форма slice с `result` | Подтверждённый get_slicing_result, не завершение turn |
| `artifact_ready` | `artifact` | Метаданные локального штатного экспорта, без ссылки скачивания |
| `error` | `code`, `message`, `related_id` (или null) | Понятная ошибка с привязкой к действию |

Пара `(turn_id,tool_call_id)` связывает начало/конец; разные turn могут использовать одинаковый call ID. `slice_id` различает запуски; progress — 0..1 или null, состояния queued/running/completed/failed. События slice возникают только после реальных start/status/result tools. `background_slice_events:false`: после idle или лимита LLM engine может продолжаться; snapshot остаётся последним наблюдением, а не обещанием актуальности. Запросите новый статус (чатом или MCP). Автоматического мониторинга между turn нет. `completed` с `valid:false` — устаревший результат; не показывать как актуальный. Повторный актуальный status того же slice сохраняет уже прочитанный result, новый slice/неактуальность его очищают.

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

Недоступные время/масса/длина — null, не выдуманный 0. Это оценки слайсера и суммарный материал без неподтверждённого распределения по экструдерам. Форма совпадает с MCP get_slicing_result (включая возможные warnings), а не с прежним проектным materials[]. `slice`: `{slice_id,state,progress,valid,error,observed_at,result}`, до первого наблюдения null. После изменения проекта кеш помечается неактуальным; при ручных изменениях вне агента актуальность выясняется следующим tool-чтением.

Артефакт в artifact_ready и массиве artifacts snapshot:

```json
{"artifact_id":"artifact-1","file_name":"project.3mf","kind":"project","size_bytes":1234,"slice_id":"slice-1","source":"ste_slicer","writer_id":"3MFWriter","writer_type":"workspace_writer","valid":true}
```

`kind`: project/gcode (как у штатного экспорта). Метаданные появляются только после успешного get_export_status; полного local_directory в чате нет. HTTP download_url/file_id пока не выдаются, downloads:false. Завершение нарезки не подтверждает экспорт. G-code создаёт слайсер. Файлы остаются связаны с прежним slice_id, актуальность — на момент последнего чтения. Копирование локальных результатов пользователь выполняет по MCP/PowerShell из plugin README; браузер не раздаёт произвольные пути.

## 6. Ошибки нового API

Новые REST-маршруты возвращают HTTP-код и тело:

```json
{"error":{"code":"agent_busy","message":"Дождитесь завершения текущего ответа"}}
```

| HTTP | Коды/ситуации |
| --- | --- |
| 400 | `invalid_request`, `invalid_arguments`, `attachments_unavailable` |
| 403 | `forbidden_origin` |
| 404 | `session_not_found`, `not_found` (в том числе ещё отсутствующие files/settings) |
| 409 | `agent_busy`, `message_id_conflict`, `session_limit` |
| 410 | `events_expired` |
| 413 | `payload_too_large` |
| 415 | `unsupported_media_type` |
| 500 | `internal_error`; техническая причина только в безопасном журнале |
| 503 | `llm_not_configured`, `service_stopped` |

После HTTP 202 ошибки работы агента/инструментов/слайсинга приходят событиями и сохраняются в состоянии диалога. Отказ инструмента не превращается в успех HTTP-операции над принтером. Внешние детали провайдера не копировать целиком в ответ, особенно заголовки авторизации.

UI различает HTTP-ошибку, обрыв связи и операционную ошибку события. После сетевого сбоя не повторять автоматически изменение сцены или настройки. Для повторной отправки сообщения используется прежний client_message_id; для выяснения фактического состояния — snapshot/чтение.

Legacy health/message, MCP и static сохраняют прежние HTTP-ошибки со строкой error; новый API использует объект выше. Операционные ошибки STE (включая slicer_not_connected) приходят в tool_finished.result и не маскируются успешным изменением. После 202 возможны error-коды network_timeout/network_error/auth_error/tls_error, invalid_response/invalid_message/invalid_tool_call/empty_message, step_limit/tool_limit/budget_exceeded, mutation_unconfirmed/agent_failed/worker_failed/service_stopped. Ошибка инструмента сохраняет прежний код адаптера; неподтверждённая мутация завершает turn. Ключ редактируется в данных/тексте, raw provider bodies не выдаются.

## 7. Последовательность работы frontend

1. Прочитать /api/status; показать реальные возможности и configured, не «LLM подключена». Настройки вводятся только в Qt.
2. Создать session или восстановить snapshot; отрисовать messages и tool_events, продолжить polling с last_event_id.
3. Перед отправкой сохранить полное тело с client_message_id. Принять 202 как принятие; отрисовать канонический user из snapshot, без оптимистичного дубликата.
4. Обрабатывать tool/progress/error и assistant_message. Не отождествлять idle с завершением engine. attachments/downloads отключены.
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
