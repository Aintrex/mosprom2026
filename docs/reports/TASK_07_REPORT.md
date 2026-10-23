# ТЗ 07 — настройки печати и экструдера

23.10.2026. **Фаза A завершена: ТЗ 07 подготовлено к итоговому ревью и сохранению базы ведущим. Native-приёмка ТЗ 03–07 открыта.** Пауза снята новым прямым поручением пользователя, проверенным через read_thread чата «Лид» (userMessage 01a169e1-ef79-7810-b416-80ca9f209f44). Выполнено только завершение ТЗ 07; интеграция b9c0190 и ТЗ 08 не начаты.

## Изменения

- Новый `plugin/AIAssistant/slicer/settings.py`: allowlist 19 ключей, валидация типов/областей, явная позиция экструдера, чтение runtime-метаданных, describe_settings, запись целевого userChanges с before/after, bounds/warnings и проверкой контекста. Общий setter не принимает printing_mode/произвольный Python/списки.
- `slicer/adapter.py`: прежние get_setting/set_setting используют модуль настроек, добавлен describe_settings. Старые вызовы layer_height/global и форма ответов сохранены. Используются прежние QtBridge, общий lock и инвалидация Slicing, без второго источника состояния.
- `agent/tools.py`: расширены существующие схемы key/scope/value/extruder_id, describe_settings добавлен в тот же реестр MCP. Зависимость области/ID и точный тип ключа проверяются общим валидатором до API.
- `tests/test_print_settings.py`: 13 новых сценариев. В прежних test_slicer_settings/test_slicer_modes обновлены fixtures метаданных, tools/list и отрицательный пример запрещённого ключа; старые сценарии не удалены.
- `docs/API_CONTRACT.md`, `plugin/README.md`, `docs/PLAN_SLICER.md`: завершены контракт, ограничения, последовательный ручной сценарий и статус 07. В PLAN уточнено, что отсутствие конфликтов относилось к прежнему 6e855c1; для нового b9c0190 дана ссылка на отдельный план ведущего.

Свойства сверены по справочной сборке STE-Slicer-master и Uranium-steslicer, без изменения/копирования внешних исходников:

| Источник | Подтверждение |
| --- | --- |
| UM/Settings/SettingDefinition.py, STE resources/definitions/fdmprinter.def.json | Все 19 типов совпадают с allowlist; type/unit/options/enabled и minimum_value/maximum_value/minimum_value_warning/maximum_value_warning — реальные свойства |
| UM/Settings/ContainerStack.py | hasProperty проверяет наличие, getProperty вычисляет SettingFunction на выбранном стеке; отсутствие свойства не заменяется догадкой |
| STE steslicer/SteSlicerApplication.py:342–346 | settable_globally/settable_per_extruder регистрируются самим приложением; их defaults возвращает runtime, плагин их не выдумывает |
| UM/Settings/InstanceContainer.py:193,316, SettingInstance.py:126 | getReadOnly делегирует политике registry, setProperty пишет userChanges; чтение native value уже учитывает тип definition. Входные строки от клиента не преобразуются в числа/Python |

Новых зависимостей нет. Саморевью settings.py/adapter/tools не выявило новых блокирующих дефектов; после возобновления завершена документация, код частичного результата сохранён. Ведущий также независимо сверил типы/API и сообщил об отсутствии блокеров: [TASK_07_REVIEW.md](TASK_07_REVIEW.md).

## Контракт и защита

get/set_setting принимают key из allowlist и scope=global/extruder; при extruder обязателен непустой строковый extruder_id, при global поле запрещено. Setter дополнительно требует value. describe_settings принимает scope и ту же условную позицию. Общие schemas используют additionalProperties:false; setter value имеет number/boolean/string, точный тип ключа и зависимость ID проверяет общий валидатор до API (-32602). UI-экструдер не переключается. printing_mode, произвольные ключи/выражения и batch setter запрещены.

Runtime подтверждает тип/наличие/разрешения. Числа конечные, bool не число, int без округления дроби, enum — ключ актуального options. Layer_height сохраняет >0; остальные значения ограничиваются профилем, нулевое заполнение допустимо при соответствующих bounds. Неизвестные enabled/permission/readonly/type запрещают запись; hard bounds запрещают выход, warning bounds дают warnings. Отсутствующие поля describe — null/limitations, неподдерживаемые ключи остаются в списке с available=false.

Ответы сохраняют ok/data/error и MCP isError, get value/unit, set before/after/unit. Для extruder добавляется позиция. Значения читаются из целевого стека, а не копируются из запроса; смена машины/стека/userChanges и неподтверждённый setter дают отказ. No-op не пишет и не инвалидирует slice. Попытка реального изменения инвалидирует нарезку/будущий экспорт даже при неопределённом исключении. Используются прежние QtBridge (5 секунд) и общий guard импорта/нарезки/экспорта/выбора профиля; чтения доступны. При pending timeout отложенная запись отменяется, после начала результат неизвестен, без автоповтора/отката.

Дополнительные коды: setting_metadata_unavailable, setting_option_unavailable, setting_disabled, setting_read_only, setting_scope_unavailable, setting_context_changed. Прежние setting_unavailable/setting_out_of_range/setting_not_applied, no_active_machine/extruder_not_found, slicing_busy/qt_timeout/slicer_error/slicer_not_connected сохранены. Полные поля и allowlist — в API_CONTRACT.

## Проверки

Windows, Python 3.13.6:

```text
python -m unittest discover -s tests
Ran 153 tests in 9.543s
OK
```

Сохранены все 140 предыдущих проверок, добавлены 13 новых: типы bool/enum/int/float, нулевое заполнение, runtime options, hard/warning bounds, missing/disabled/readonly/scope, оба экструдера без смены UI-позиции, no-op/инвалидация, неподтверждённый setter/смена стека, общий busy, worker/Qt, pending/running timeout и shutdown. В полный набор входит грамматика Python 3.5 и api:5. Прежние loopback HTTP/MCP-тесты прошли при разрешённом запуске вне sandbox. Ведущий независимо повторил **153 теста, OK, 9.473 с**.

26 PowerShell-блоков plugin/README разобраны Parser без выполнения, 17 JSON-примеров API/README разобраны json.loads. git diff --check прошёл; прежнее предупреждение CRLF → LF корневого README не является whitespace-ошибкой.

Это проверки с подменами, не native-прогон. Установленный плагин не обновлялся, принтер/нагрев не запускались, реальные LLM-запросы не выполнялись.

## Native-статус и ограничения

В [plugin/README — ТЗ 07](../../plugin/README.md#тз-07-настройки-global-и-явно-выбранного-экструдера) подготовлен самостоятельный PowerShell-сценарий: текущий порт → точная проверка Stereotech Hybrid 530 V5.2.4/classic/backend → describe global/0/1 → сохранение baseline → числовой global speed_print → разрешённая material_print_temperature экструдера 0 → чтение/UI/другой экструдер → явное восстановление значений. При недоступности параметра сценарий прекращается, зависимости не включаются автоматически. JSON до/после/восстановления сохраняется в tmp/. Команды не исполнялись в установленном слайсере.

Native-проверка global/экструдера, UI и восстановления не выполнена. Подмены/синтаксическая совместимость не подтверждают порядок событий и доступность конкретных настроек Hybrid в установленной версии. Производные значения могут пересчитываться штатно; восстановление значения не удаляет созданный override и не возвращает старой нарезке valid. Сброс override возможен штатным UI, актуальная нарезка требует нового запуска. Создание custom-профиля, batch setter, преобразования моделей и новые LLM/HTTP возможности не входят в ТЗ 07. Ручная приёмка ТЗ 03–06 также остаётся открытой.

Весь незакоммиченный результат 05/06 и чужие документы/скрипты сохранены. Корневой README, COORDINATION, отдельные reviews ведущего, scripts/demo_checkpoint.ps1 и Word lock-файлы исполнитель в фазе A не менял. Код slicing/exports, SPA/HTTP/LLM/writers в ТЗ 07 не переписывался. git add/commit/push/merge, переключение ветки, установка и откат исполнителем не выполнялись. После этого отчёта фаза A завершена; разрешённые сохранение/публикацию базы до 07 выполняет ведущий. Фаза B и ТЗ 08 ожидают отдельного поручения.
