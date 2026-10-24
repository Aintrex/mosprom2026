# Исправление аргументов LLM и диагностики преобразований

24.10.2026. Выполнено только `TASK_FIX_TOOL_ARGUMENTS_AND_MODEL_TRANSFORMS.md` по согласованному ведущим плану. **Локальная реализация проверена изолированно; реальный LLM/STE не запускались.** Установленная копия не обновлена. UI файлов/новые контролы второго участника не реализовывались.

## Причина и границы вывода

Пользовательский отказ доказывает `value:string`, stage=validation/executed=false и отсутствие native-запуска. Источник строки в конкретном реальном ответе неизвестен; утверждение модели «транспорт превратил число» не доказано. Обнаружена реальная неоднозначность прежней схемы: общий union number/boolean/string не связывал value с конкретным key. Инструкция «0.12, не строка» уже существовала и сама проблему не решила.

Синтетический настоящий формат Chat Completions проверен через `ChatCompletionsClient._parse` → `json_arguments` → реестр → адаптер: внешний function.arguments по протоколу строка с JSON, вложенное числовое value0.12 остаётся float, quoted value остаётся string до новой явной границы. JSON-парсер/HTTP-клиент не изменялись; вывод о поведении неизвестного реального провайдера по этому тесту не делается.

Жалоба на поворот пока без реального payload/кода ошибки. Математика RotateOperation по выданному Uranium подтверждена на изолированном сценарии, ошибка поворота не воспроизведена. Поэтому **не заявляется, что конкретный native-отказ пользователя исправлен**: добавлены информативные стадии, проверены корректная цепочка и запреты; защиты не убраны.

## Изменения

| Файл | Изменение |
| --- | --- |
| `plugin/AIAssistant/agent/tools.py` | OneOf key/value из единственного SETTING_TYPES; LLM-only numeric repair; конкретные field_errors и ожидаемая схема известного ключа |
| `plugin/AIAssistant/agent/service.py` | Явный admission до строгой валидации; безопасные метаданные результата и детерминированное видимое уведомление; остановка повторной неверной сигнатуры; точные сообщения transform validation |
| `plugin/AIAssistant/slicer/transforms.py` | Только достоверность стадий preflight/dispatch, математика и native Operations/Undo прежние |
| `plugin/AIAssistant/slicer/adapter.py` | Передача stage/operation_started в ошибки преобразований; неопределённость Qt-timeout не подменяется false |
| `plugin/AIAssistant/web/app.js` | Две небольшие ветки текста существующих ошибок для preflight/execution; без новых контролов/косметики/HTML/CSS |
| `tests/test_numeric_arguments.py` |9 новых сценариев аргументов/границ/повторов/неизвестного исполнения |
| `tests/test_transform_tool_diagnostics.py` |5 новых сценариев upload→liveID→поворот/readback/Undo и стадий отказа |
| `tests/test_setting_tool_rejection.py` | Два согласованных изменения ожиданий, описанные ниже; остальные проверки сохранены |
| `tests/test_agent_web.js` |2 сценария уведомления/стадий поверх30 прежних |
| `docs/API_CONTRACT.md`, оба README | Точный контракт repair/стадий и отдельный ручной повторный сценарий |

### Схема и узкий разбор

Один setter, прежние имена/поля/области; MCP и LLM получают одну schema. Четыре oneOf-ветки группируют120 ключей из общего каталога: float→number, int→integer, bool→boolean, enum→string. Enum options остаются текущими runtime-ключами из describe, второй каталог/отдельный setter не создавался. Скрытого schema-fallback при несовместимости провайдера нет.

Repair существует **только** в LLM-admission `set_setting.value`: известный float/int key, правильная структура/scope, строка1–64 символа. Float — `-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?`, int — `-?(0|[1-9][0-9]*)`. Никакого trim, leading plus/zero, запятых, единиц, выражений или boolean; для int нет decimal/exponent/float-посредника. Float проверяется на finite/overflow и через Decimal на underflow/subnormal/потерю ненулевого числа в0. Корректный нулевой literal не становится ненулевым числом. Int сохраняет точность, включая9007199254740993. Новых зависимостей нет, decimal — stdlib.

Bool/enum/key/scope/extruder_id/углы/векторы не исправляются. Все обычные validators и runtime metadata/type/enabled/readOnly/bounds/options/Qt guards идут после admission, ничего не обходится. Некорректные структура/extra/scope не угадываются. **Прямые MCP/адаптер по-прежнему отвергают numeric string** до API. Это подготовка одного текущего ещё не запущенного вызова, не retry/новый сетевой запрос.

Результат содержит `argument_admission:{field,from_type,to_type,reason,stage:"llm_boundary"}` без raw значений/провайдера/ключа. Обычное assistant_message всегда получает видимое уведомление о разборе и неизвестной причине строкового типа, независимо от объяснения LLM; оно прямо не подтверждает запись. Настоящий успех требует ok/before/after/отдельного readback. Для baseline0.1 и target0.12 тест подтверждает ровно одну запись и before0.1/after0.12/readback0.12.

`error.field_errors` даёт field/expected_type/actual_type без значения; известному setting возвращается компактная expected schema этого ключа/типа. Unknown имена/пути/значения не копируются в диагностику. Повтор одной неверной семантической сигнатуры с другим call_id/порядком полей останавливается на втором отказе, error.repeated=true и final code repeated_invalid_tool_arguments. Сигнатура — внутренний digest, наружу не возвращается. Разные исправления ограничены прежними8LLM/16tools/120s/30s. После допуска к tools.call и ошибки/Qt/network timeout/unknown прежний запрет дальнейших мутаций сохранён; stage=preflight модели не разрешает повтор native-вызова автоматически.

### Поворот и соседние операции

Проверены выданные `UM/Operations/RotateOperation.py`, `UM/Math/Quaternion.py`, `UM/Scene/SceneNode.py` и OperationStack: radians/fromAngleAxis, World rotation, origin при единичном мировом родителе, Undo. Их код не копировался в Git/не менялся. Изолированный probe реальных классов с inert signals/fake app/mesh/build-volume прошёл move/rotate/scale/Undo/delete/Undo; это не native-Qt/engine-прогон.

Новый сценарий использует настоящий FileStore upload/проверку STL, прежний load_model_file/lease и QtBridge; подменный reader завершает импорт только своим fileCompleted. Затем свежий list_models подтверждает ID импортированной модели, get_model_state → rotate JSON-number90 → отдельный get_model_state подтверждает матрицу/габариты, другой одноимённый объект не изменён, Undo восстанавливает матрицу. Сам STL reader/геометрия подменены, чтение реальной детали STE этим не доказано.

Стадии разделены:

- validation/executed=false: аргументы отвергнуты до адаптера; например string angle не исправляется молча, требуется корректный JSON-number.
- preflight/operation_started=false: Qt-проверки могли выполниться, но OperationStack.push не разрешён. Проверены physics/drop_down/push_free, busy backend/export, Preview, платформа/родитель и stale ID. Preferences не меняются.
- execution/operation_started=true: начат dispatch push, результат/матрица не подтверждены. Это не доказательство изменения и не безопасное разрешение повтора/отката.
- unknown/operation_started=null: достоверной стадии нет, включая Qt-timeout снаружи callback. Поздний pending callback не выполняется после отмены мостом; running-неопределённость не маркируется false.

Move/scale/delete сохраняют те же preflight/typed-vector проверки. Arrange не принимает numeric arguments: лишние поля отвергаются; прежние18 arrangement-тестов прошли без изменений solver/job. Общие guards/Qt/Undo и инвалидация slice/export сохранены: отказ до push не инвалидирует completed, успешное преобразование инвалидирует; Undo не оживляет старую нарезку. Восстановленный чат по-прежнему не использует старые IDs, требует актуальные чтения. Новый упаковщик/автоотключение физики не добавлены.

## Проверки

Windows, отдельный Python3.13.6/Node; LLM/STE/Qt подменены. Последний полный прогон:

```text
python -m unittest discover -s tests -q
Ran 377 tests in 26.663s
OK

node --check plugin/AIAssistant/web/app.js
node tests/test_agent_web.js
Agent web contract: 32 scenarios OK (30 preserved + numeric admission and transform stages; offline DOM).
```

Сохранены363 прежних Python/30 JS; добавлены14 Python/2 JS. Адресно:9 numeric OK0.114с,8 прежних rejection OK0.136с,5 новых transform-chain/stage OK0.338с,17 прежних transforms OK0.580с,18 arrange OK0.607с. Проверены quoted/числовой wire type, один repair-call/readback/notice и MCP reject; неверные lexical/64+ chars/bool/enum/int decimal/exponent/nonfinite/overflow/underflow; raw1e999 не ломает диагностику/digest; повторы; runtime-disabled; начатая неизвестная запись/сетевой timeout без второй мутации; стадии/Upload/Undo.

Два старых ожидания изменены по **согласованной политике**, не ради обхода строгой проверки: прежний LLM-reject pure numeric string заменён reject строки с единицей (pure numeric теперь repair, его strict MCP reject и одна запись проверены новыми тестами); прежний8-step тест использует разные неверные аргументы, одинаковые отдельно проверены на остановку во второй попытке. Промежуточные адресные запуски показали эти ожидаемые изменения контракта; новая transform-фикстура также была исправлена, поскольку borrowed complete сам вызывает start/status. Продуктовый slicing_busy не обходился.

Грамматика Python3.5 —52 файла plugin/scripts/tests, plugin.json api5. Frozen296:29/29 SHA256 совпали, manifest не менялся. Все47 PowerShell-блоков plugin/README разобраны Parser без исполнения:0 ошибок. git diff --check прошёл. Никакие реальные provider/STE-MCP/engine/принтер не вызывались; прежние loopback MCP-тесты работают поверх fake API. Отдельного native/browser QA этого исправления нет.

## Повторная ручная приёмка и открытое

После отдельного согласованного обновления **целой** установленной копии пользователь сохраняет проект/before, выполняет0.1→0.12 (либо фактический before→допустимый другой target), подтверждает get_setting/UI и отдельным явным запросом восстанавливает before. Короткий промпт: «Установи global layer_height0.12мм, если метаданные разрешают; подтверди get_setting. При ошибке остановись, не повторяй». Notice repair возможен, но не заменяет реальный результат. При unknown сначала только чтение/UI, без автоматического rollback. Конкретный провайдер/conditional schema/его послушание всё ещё требуют проверки; повышение лимитов и недоказанные объяснения транспорта не использованы.

Для поворота: несимметричный STL → подтверждённый импорт → свежие list_models/get_model_state → «Поверни объект с этим текущим ID вокруг мировой осиZ на90градусов, угол числом; проверь before/after и отдельный get_model_state. При отказе не обходи защиты, остановись» → UI/матрица/другие объекты → штатный Undo/readback. На симметричной детали вид может совпадать. Для оставшейся реальной ошибки нужны имя tool, известные типы полей, свежий ID текущего процесса, axis/тип angle, code/stage/operation_started и безопасный get_model_state, сведения physics/Preview/busy/платформы. Без приватного пути/байтов/ключа. **Причина прежнего native-отказа пока неизвестна**. Подготовленный сценарий не выполнялся, ТЗ03/04/сквозные файлы автоматически не закрыты.

## Сохранность и остановка

Работа началась с чтения AGENTS/ТЗ/координации и git status на0b675648ebbe815c6b575dbeae6e0a2fe872c751. Появившиеся правки COORDINATION ведущего сохранены, его разрешение на публикацию не использовалось исполнителем. PUBLIC-копии/замены/документы privacy, Word/презентация, frozen/helper и UI-этап другого участника не откатывались. Новых чатов/агентов/worktree, add/commit/push/fetch/установки/AppData/реальных ключей/LLM/STE/принтера не было.

Automatic review отклонил одну отправку промежуточного статуса в другой чат из-за отсутствия подтверждённого разрешения на передачу. Она не повторялась и не обходилась; ведущий сообщил, что прочитает финал в этом чате. Безопасная передача — этот отчёт и итог здесь. После отчёта исполнитель полностью останавливается для ревью; публикацию выполняет ведущий отдельно, новых этапов не начинает.
