# ТЗ 13 — расширенные параметры печати

24.10.2026. **Реализован каталог120 ключей/10 групп, адресный describe и прежняя строгая запись с readback. Передача на source-ревью; native-приёмка открыта.** Установленная копия не обновлялась, реальные LLM/STE/принтер не вызывались. Каталог не обещает одновременную доступность всех120: её определяют runtime-машина, профиль, режим, область и зависимости.

## Изменения и граница

| Файл | Изменение ТЗ13 |
| --- | --- |
| `plugin/AIAssistant/slicer/settings_catalog.py` |120 выбранных ключей/типов/кратких названий,10 категорий; без runtime defaults и внешнего JSON |
| `slicer/settings.py` | Общая валидация filters, статическая фильтрация/пагинация до runtime metadata, label/description с происхождением; get/set сохраняют проверки |
| `slicer/adapter.py` | Необязательные аргументы describe с отличием отсутствующего значения от null; прежний QtBridge |
| `agent/tools.py` | Один каталог/схемы MCP и LLM, обновлённый enum ключей/категорий и limits describe |
| `agent/service.py` | Только адресная инструкция describe в существующем SYSTEM_PROMPT; принятые timeout/2F и лимиты сохранены |
| `web/app.js` | Компактный итог страницы: найдено/выдано/остаток, source label для новых ключей; технические поля в закрытом details |
| `tests/test_extended_settings.py` |12 новых поведенческих сценариев |
| `tests/test_print_settings.py` | Два прежних describe-запроса сделаны адресными из-за пагинации; проверки сохранены |
| `tests/test_slicer_settings.py` | Fake hasProperty теперь подтверждает только реализованные свойства, не заявляет несуществующие label/description; прежние проверки не убраны |
| `tests/test_agent_web.js` | Один сценарий страницы поверх21 прежнего |
| `docs/API_CONTRACT.md`, оба README | Действующий контракт120/filters/происхождение и ручные команды с восстановлением |

Пути в таблице со второй строки кода относятся к `plugin/AIAssistant/`, если не указан tests/docs. Чужие frontend CSS/index, timeout runtime,2F и документы презентации сохранялись; их изменения не относятся к13. Изменение TLS fixture в `tests/test_llm_timeout_diagnostics.py` сделал ведущий отдельно, см. проверки ниже.

Прежние19 включены. Категории/числа: resolution12, shell11, infill11, material20, speed14, travel6, cooling9, support20, platform_adhesion13, printing_mode_category4. `cylindrical_round_segments` — float. `support_structure`, machine_*, start/end G-code/CLI и `printing_mode` не добавлены. Режим остаётся отдельным tool. Batch-запись, автозависимости/автопрофили и редактор120 полей отсутствуют.

## Источники и контракт

Только чтение справочной копии выданного STE и соседнего Uranium. В `resources/definitions/fdmprinter.def.json` рекурсивно проверены все120 типов и кратких названий, включая children внутри обычных параметров. Проверены цепочка hte530v5.2.4 → steprinter530_v5.2.4 → steprinter_v5 → fdmprinter и собственные settings/overrides fdmextruder + main/second/fiber extruder V5.2.4: противоречащих type не найдено. Каталог соответствует `TASK_13_SETTINGS_CATALOG.md`. Абсолютные SourceSTE-пути в runtime не добавлены, внешний JSON не копировался целиком.

API описаний подтверждён по `UM/Settings/ContainerStack.py` (getProperty/hasProperty), `DefinitionContainer.py` (получение атрибутов definition), `SettingDefinition.py` (label/description — readonly TranslatedString). API может локализовать текст. Label до200 символов/description до500, флаги label_truncated/description_truncated; text_source указывает definition_api либо catalog, неизвестный description=null/source=null. Fallback каталога не подтверждает значение/доступность. Value/type/unit/options/permissions/bounds читаются из текущего стека, default_value не используется и выражения плагин не вычисляет.

Describe: обязательный scope, явный extruder_id только для extruder; keys1–20 уникальных известных, category из10, query строка<=80, offset int>=0/default0, limit int1–30/default20. Bool не int, null/extra/неизвестные category/key отвергаются до API. Пересечение фильтров → стабильный порядок каталога → страница → только её runtime metadata на Qt. Поиск подстроки без регистра по key/исходному английскому краткому label; runtime label не читается ради поиска. Отсутствие query не фильтрует; явная пустая/пробельная query даёт0. Даже пустая выдача проверяет машину/extruder и остальные аргументы.

Прежние scope/settings/extruder_id сохранены; добавлены total/offset/limit/returned/next_offset/truncated. Total после фильтров до страницы; next_offset=null в конце, truncated обозначает следующую страницу. Offset за концом возвращает пустую страницу при прежнем total. Клиентам, ожидавшим все19 при одном scope, теперь нужны keys/страница; прежние README-команды исправлены. Полные схемы/пример — API_CONTRACT.

Get/set сохраняют finite/bool/int/enum, runtime type/actual options, hard/warning bounds, enabled/readOnly/scope, Qt/userChanges, общий busy guard, фактические before/after, no-op и инвалидацию slice/export. Нет coercion строк, автоповтора после неизвестного исполнения и собственных значений вместо readback. Чтение не делает параметры автоматически доступными для записи. Единый реестр сохраняет безопасную type-only validation-диагностику; сеть/лимиты8LLM/16tools/120s/30s прежние. Инструкция адресного describe не доказывает послушание реальной модели.

## Размер ответа

В выбранных исходных определениях8 enum, максимум13 вариантов у infill_pattern. Компактный UTF-8 JSON его options —287 байт (с обычными пробелами312); остальные options меньше. Options не обрезаются, сохраняется строгая проверка фактических ключей.

Иллюстративный расчёт структуры ответа с исходными label/description<=500/options и **null вместо runtime values/permissions/bounds**, ensure_ascii=false/компактные разделители: максимальная последовательная страница20 —14752 байта оболочки/16300 MCP, страница30 —21183/23361; все120 —81304/89687. Это расчёт по исходным текстам, не измерение native/провайдера и не верхняя граница runtime: локализация, options, values, limitations и формат сериализации меняют размер. Новая отдельная квота options/полного ответа не введена. Пагинация и усечение текстов сокращают штатную выдачу, LLM не получает автоматически все120 описаний.

## Проверки

Исполнитель:

-12 новых extended tests — **OK,0.042с** (ранний адресный прогон0.043с). Проверены стабильные страницы/пересечения/пустой поиск/невалидные аргументы, число runtime metadata-чтений, происхождение/усечение, отсутствие defaults, новые float/int/bool/enum, bounds/disabled/readOnly/scope/type mismatch, явный extruder, readback/stale и scripted LLM describe→one write→readback.
- Прежние13 print-settings tests — **OK,0.285с**. Регрессия ТЗ01–06 по маске test_slicer_*.py — **140 OK,8.596с**.
- `node --check plugin/AIAssistant/web/app.js`; **22 JS-сценария OK**,21 прежний сохранён. Новый сценарий проверяет количество страницы/next_offset/первые6 кратких строк, остальные в details, безопасный текст и отсутствие автоматического следующего POST.
- Грамматика Python3.5 — **48 файлов** plugin/scripts/tests, plugin.json api5. Это разбор на современном Python, не запуск в настоящем Python3.5/Qt/UM.
- Read-only SHA256-сверка frozen baseline296 — **29/29 совпадений**. Manifest/helper/TESTING_STEP_BY_STEP не менялись.
- Все46 PowerShell-блоков README разобраны штатным Parser:0 ошибок, без выполнения. Финальный git diff --check прошёл; CRLF-предупреждения относятся к чужим документам. Исторический отчёт05 не имеет diff.

История общего прогона: первый набор после реализации выявил неточный fake hasProperty для новых текстовых свойств; fake уточнён без ослабления продуктовых проверок. При повторном запуске в sandbox были PermissionError временных файлов; разрешённый прогон исполнителя — **346 тестов,24.739с, один failure** в позднем `test_late_transport_mutating_response_ignored_closed_and_no_tool_or_network_retry` (entered=false). Отдельно5 timeout-tests — тот же один failure,0.113с. Это не выдавалось за успешную общую регрессию.

Ведущий независимо исследовал30мс fixture: Windows TLS-подготовка попадала в его бюджет до fake urlopen. Он подготовил реальный SSLContext до измеряемого бюджета и возвращает его через mock, сохранив проверки late-response/close/one-request/no-write и30мс. **Runtime/лимиты/сеть не менялись; причины реальной LLM latency не установлены.** По переданным ведущим итогам: **346 Python OK24.518с;5 timeout OK0.134с;12 extended OK0.049с;22 JS OK**. Это независимый прогон ведущего, не новый прогон исполнителя. После него менялись только документы; полный набор повторно без причины не запускался.

## Ручная проверка и ограничения

Подготовлены отдельные PowerShell-блоки в plugin/README.md, раздел «ТЗ13 / PowerShell для приёмки»: текущий порт, matching Hybrid530/classic, адресное keys/category/query, следующая и пустая страница; чтение до/после/явное восстановление нового scalar speed_wall, enum retraction_combing по актуальным options, material_print_temperature_layer_0 с введённым runtime extruder_id. Отдельно cylindrical_layer_height только после ручного выбора matching cylindrical, подтверждения enabled/settable/type, с восстановлением в том же режиме и ручным возвратом classic. Ошибка/unknown прекращает сценарий до выяснения, без автоматического повтора/отката. Команды не выполнялись.

Реальная доступность/локализация/скорость describe всех выбранных страниц, изменения/UI/восстановление, текущая0.12 и поведение провайдера требуют отдельной приёмки выбранной установленной копии. ТЗ03/04 этим отчётом не закрываются; модель/материал/режим сами не переключались. Нет утверждения о готовом результате нарезки/3MF/G-code/физической печати по этим тестам.

## Сохранность и передача

Git status проверен, чужие незакоммиченные изменения сохранены; git add/commit/push/merge/fetch, новые чаты/агенты/worktree, установкаAppData, чтение ключей и реальные LLM/native/принтер не выполнялись. Ошибочная новая атрибуция текущих проверок к ТЗ05 снята: исторический TASK_05_REPORT.md от23.10 сохранён без diff, повторной реализации05 нет. Результаты текущей работы относятся к13.

Для следующего разрешённого пользователем этапа2G ведущему передан короткий план JSON/revisions/sanitization/истёкшихID/attachments/nonblockingQt-close и компактного списка чатов. При завершении13 runtime2G не реализовывался. Ведущий принял исходники13 (TASK_13_REVIEW.md), согласовал план и отдельно назначил2G; результаты следующего этапа будут в ROLE_02G_REPORT.md, к объёму13 они не относятся.
