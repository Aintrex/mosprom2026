# Отчёт по ТЗ 03 — встроенное качество и материалы

Дата: 23.10.2026. **Код готов к ревью и отдельной ручной приёмке. Runtime-прогон ТЗ 03 не проводился; установленный плагин не обновлялся.** Приёмка ТЗ 01/02 не распространяется на новые инструменты. Staging, commit, push, работа со сценой, запуск нарезки и экспорт не выполнялись.

## Изменения

- Новый `plugin/AIAssistant/slicer/profiles.py`: списки встроенных QualityGroup и материалов указанной позиции, валидация, выбор публичными методами MachineManager, подтверждение фактических контейнеров и сохранности overrides. Qt/UM-ссылки и снимки userChanges остаются внутри коротких Qt-операций; в JSON и журнал значения overrides не передаются.
- `slicer/adapter.py`: четыре новых метода, ожидание подтверждения только в worker, общий бюджет 5 секунд. Общая неблокирующая `_change_lock` теперь охватывает режим, качество и материал; конкурирующий выбор получает `selection_busy`, режим сохраняет прежний `switch_in_progress`. Прямой выбор из главного потока запрещён до setter. Отмена pending-callback при timeout/shutdown использует существующий мост без изменений.
- `agent/tools.py`: четыре новых схемы/вызова в том же реестре. Вызов через getattr ограничен явным списком TOOL_FIELDS; произвольных методов или Python от клиента нет. Старые четыре инструмента и схемы сохранены. Status перечисляет восемь инструментов слайсера, `runtime_verified=false`, `llm_connected=false`.
- Новый `tests/test_slicer_profiles.py`: 27 значимых сценариев с раздельным HTTP worker и главным тестовым потоком, отдельной очередью применения выбора. В прежних тестах обновлены ожидаемый список tools и обращение к схеме режима по прежней позиции; все 45 прежних проверок сохранены.
- `plugin/README.md`, действующий раздел MCP в `docs/API_CONTRACT.md`: контракт, политика overrides, ограничения и точные PowerShell-команды для приёмки/восстановления. Будущие REST-разделы не реализованы и не объявлены готовыми.

HTTP/SPA, Extension, MCP-транспорт, LLM, `modes.py` и `qt_bridge.py` в этом задании не менялись. Новых зависимостей, singleton и универсального workflow-движка нет. Создание/редактирование/сохранение/выбор custom-профиля, сопла, платформы и машины не добавлялись.

## Проверенные исходники и политика сохранения

Читались только локальные справочные STE `C:/Users/sevay/Desktop/SourceSTE/STE-Slicer/STE-Slicer-master` и соседний `Uranium-steslicer`. Они не изменялись, не копировались в Git; их пути отсутствуют в runtime-коде.

| API / источник | Вывод для реализации |
| --- | --- |
| `SteSlicerApplication.py:847,861,865` | `getMachineManager`, `getMaterialManager`, `getQualityManager`; вызовы только внутри Qt-callback. |
| `Machines/QualityManager.py:201`, `QualityGroup.py` | `getQualityGroups(global_stack)` возвращает quality_type → QualityGroup, включая недоступные. Берутся `name`, `quality_type`, `is_available`, global/extruder nodes. Группа может создаваться заново при каждом lookup: сравниваются ID контейнеров, а не identity группы. |
| `Machines/Models/QualityProfilesDropDownMenuModel.py:85` | Базовая высота берётся из global quality container либо definition. В отличие от UI-модели, SettingFunction не вычисляется на активном стеке с overrides: при выражении возвращается null, чтобы не выдать override за базовую высоту. Числа и числовые строки приводятся к конечному положительному числу, unit должен быть mm. |
| `Machines/MaterialManager.py:362` | `getAvailableMaterialsForMachineExtruder` учитывает definition, платформу, сопло и приблизительный диаметр. Список не берётся из всех установленных материалов. |
| `Machines/Models/BaseMaterialsModel.py:113`, `GenericMaterialsModel.py:27`, `FavoriteMaterialsModel.py:27` | Root ID словаря отличается от metadata.id resolved-контейнера. Пакеты с truthy `metadata.removed` исключаются как в штатных моделях. |
| `Machines/ContainerNode.py:31,36,44` | Metadata для описания списка; getContainer для проверки загрузки выбранного target. Отсутствующий target-контейнер отклоняется до setter. |
| `Settings/GlobalStack.py:48`, `MachineManager.py:1064` | Позиции — строковые ключи `extruders`; текущий материал читается непосредственно из этой позиции. Root из metadata.base_file, fallback — public currentRootMaterialId этой позиции. `activeMaterialId` не используется. Пустой материал сравнивается с `app.empty_material_container` или None. |
| `Settings/MachineManager.py:1405–1436` | `setMaterialById(position,root_id)` принимает два аргумента; вызывает штатный setMaterial, который обновляет качество и затем может вызвать discardOrKeepProfileChanges. Активный UI-экструдер выбирать не требуется. |
| `Settings/MachineManager.py:1471–1499`, `1100–1129` | Публичный `setQualityGroup(group,no_dialog=True)` пропускает диалог; его внутренний путь меняет quality/qualityChanges, но не очищает userChanges. Приватные методы плагин не вызывает. Активный custom блокируется до этого вызова. |
| `Settings/MachineManager.py:1222–1259` | После материала доступная группа качества может смениться; при fallback может сброситься custom quality group. Поэтому возвращается фактический `quality_type_after`, требуется перечитать профили. |
| `SteSlicerApplication.py:610–638` | always_discard очищает userChanges global и экструдеров, always_keep вызывает update, прочие политики могут открыть диалог. |
| Uranium `UM/Settings/InstanceContainer.py:353,627` | getAllKeys/getProperty используются для внутренних снимков overrides. update испускает сигналы и помечает контейнер dirty, не заменяя значения overrides. |

Сигналы activeQualityGroupChanged/activeQualityChangesGroupChanged/rootMaterialChanged и отложенные container-сигналы обслуживаются приложением. Подтверждение после setter выполняется отдельным callback, без ручных сигналов, вложенного event loop, processEvents или ожидания на Qt-потоке.

Политика MVP:

1. Встроенное качество выбирается с `no_dialog=True`; global/extruder overrides сравниваются до/после. Прежний layer_height может сохраниться из override, это не ошибка выбора группы.
2. При активном custom реальный выбор встроенного качества возвращает `custom_profile_active` до записи, в том числе при том же underlying quality_type. Материал тоже нельзя реально менять при custom: до изменения нельзя надёжно предсказать его сброс штатным fallback качества. Это консервативное ограничение описано в README. No-op материала допускается.
3. Для материала no-op подтверждается первым, без setter/Preferences. При реальном изменении наличие overrides глобального или любого экструдера либо hasUserSettings требует штатной `always_keep`. Остальные/неизвестные политики возвращают `user_changes_present` до записи. Ограничение консервативно даже в UI basic mode; active_mode не меняется и не используется как обход.
4. Preferences только читаются; Keep/Discard не подтверждается за пользователя. При фактической потере/изменении overrides операция возвращает ошибку, автоматического «восстановления» снимком нет.

## Контракт инструментов

| Инструмент | Обязательные аргументы | data |
| --- | --- | --- |
| list_quality_profiles | `{}` | `{machine_id,current:{quality_type,name,custom_profile_id,has_user_settings},profiles:[{quality_type,name,available,layer_height_mm}]}` |
| list_materials | `{extruder_id}` | `{machine_id,extruder_id,current,materials}` |
| select_profile | `{quality_type}` | `{before:{quality_type,name,custom_profile_id},after:{quality_type,name,custom_profile_id},has_user_settings}` |
| select_material | `{extruder_id,root_material_id}` | `{extruder_id,before,after,quality_type_after,has_user_settings}` |

Аргументы-ID — `type:string,minLength:1`, `additionalProperties:false`, все перечисленные поля обязательны. `extruder_id` — позиция, не цифра подписи кнопки UI и не ID контейнера экструдера. Имена не принимаются вместо ID. Некорректная структура — JSON-RPC `-32602`; неизвестный ID при корректной структуре — операционная ошибка до записи.

`current` материала и before/after: `{root_material_id,material_id,name}`; отсутствие материала — null. Список материалов дополнительно содержит brand, material, compatible. Отсутствующие name/brand/material и compatible — null; явный compatible=false запрещает выбор. Custom ID диагностируется по ID global-узла custom, либо первого экструдерного узла при отсутствии global. Чужие/неизвестные lookup ID не заменяются названиями или переданными значениями без чтения.

Все четыре инструмента используют `{ok:true,data}` / `{ok:false,error:{code,message}}`, MCP JSON-текст в content и isError по исходу. Ошибки подключения/машины/Qt/API сохранены; добавлены `extruder_not_found`, `profile_not_available`, `material_not_available`, `custom_profile_active`, `user_changes_present`, `selection_not_applied`, `selection_busy`. Внутренний KeyError API не становится Unknown tool.

Перед setter lookup получается заново. После выбора проверяются идентичность машины/экструдеров/менеджера, прежний lookup, сохранность overrides и реальные target-контейнеры. Для качества должны совпасть выбранный тип и global/extruder IDs, custom должен отсутствовать. Для материала должны совпасть root и resolved IDs указанной позиции. После потенциально начатой записи ошибки требуют перечитать списки/состояние; при timeout/shutdown не обещается отсутствие изменения или откат. Автоповтора нет.

## Проверки

Команды из корня репозитория:

```powershell
python --version
python -m unittest discover -s tests -v
git diff --check
git status --short --untracked-files=all
```

Фактический результат: **72 теста, OK, 5.306 секунды**, Windows, Python **3.13.6**. 45 прежних проверок сохранены, добавлены 27 сценариев ТЗ 03. В этом прогоне локальные HTTP-проверки прошли без сетевых ошибок. `git diff --check` — без замечаний. Новые неотслеживаемые Python-файлы также прочитаны и проверены общим syntax-тестом; staging не выполнялся.

Проверено с подменой приложения:

- Все app/manager/stack/container/node/Preferences-чтения и запись — главный тестовый поток. Worker только ставит callback и ждёт. Отдельная очередь применяет выбор позже, подтверждение до её обработки не возвращает успех.
- Качество: runtime-ID отличаются от одинаковых видимых имён; доступны все группы, включая available=false; базовая высота отличается от override; числовая строка, definition fallback и null для неизвестной высоты. Чтение не вызывает setter.
- Материалы: root/resolved IDs различны; список для позиции 0, removed исключён, несовместимость/неизвестная совместимость различаются, необязательные метаданные и отсутствие материала. Выбор 0 не меняет материал 1 и UI-экструдер 1.
- Отсутствие машины/менеджеров/позиции/target-контейнера, неправильные аргументы, недоступные/чужие ID без записи; обновление lookup до setter и во время подтверждения, замена машины/экструдера.
- Успешный профиль через публичный setter с `no_dialog=True`, сохранение global/extruder overrides; реальное изменение качества после материала; no-op обоих видов без setter; совпавший quality_type с неверными контейнерами не подтверждается.
- Custom не сбрасывается, даже при том же underlying типе; no-op материала при custom разрешён. Unsafe политика блокируется до изменения, always_keep сохраняет global/extruder overrides. Потеря overrides диагностируется без мнимого отката.
- Setter без изменения, KeyError API после начала, приватный текст не попадает в журнал; timeout до callback предотвращает позднюю запись, после начала сообщает неопределённость; shutdown до старта и при подтверждении; взаимное исключение режим/качество/материал.
- Общий реестр и схемы, MCP-ошибки/isError, локальный HTTP → MCP → выбор → повторное чтение. Dev-сервер работает без UM/Qt; новые инструменты возвращают slicer_not_connected. Все файлы плагина/scripts/tests проходят syntax parse с feature_version=(3,5), `plugin.json api:5` сохранён.

Это проверки с подменами и современным Python. Они не заменяют запуск новых API в Python 3.5, настоящие Qt-сигналы или проверку профилей/материалов выданной сборки.

## Runtime и ручные команды

Ручной прогон ТЗ 03 **не выполнен** по границе задания; установленная копия не обновлялась, API реального приложения для нового сценария не вызывались. Реальные исходные/новые/восстановленные качество и материалы не зафиксированы. Отличия установленной сборки от справочных исходников для ТЗ 03 остаются неподтверждёнными.

Полная последовательность PowerShell подготовлена в [plugin/README.md](../../plugin/README.md), раздел «Команды ручной приёмки ТЗ 03»: текущий порт, initialize/tools/list, baseline качества, root/resolved материалов 0/1, режима и layer_height в `tmp/task03_before.json`; выбор другого доступного quality_type; свежий список материалов; выбор root ID позиции 0; сверка UI и повторных списков. Эти команды не выполнялись исполнителем.

После ревью ведущий координирует отдельное обновление и приёмку. Сохранить проект, использовать пустую сцену и Hybrid 530 V5.2.4. Зафиксировать UI и все overrides отдельно: общий API отдаёт has_user_settings, а get_setting — только layer_height, не полный снимок пользовательских настроек. При user_changes_present пользователь сам решает политику в приложении. Если второго доступного варианта нет — отметить ограничение, не устанавливать новые пакеты и не заменять runtime-моком.

Восстановление: **исходные материалы затронутых позиций → исходный встроенный профиль → подтверждение root/resolved ID, качества, overrides, режима и остальных экструдеров в API/UI**. После ошибки сначала перечитать состояние и не продолжать setters автоматически. Восстановление без фактического подтверждения не заявляется.

Ограничения: custom quality selection/edit/save не реализованы; реальная смена материала при custom заблокирована; material policy намеренно консервативна; вычисляемая базовая высота — null. Не проверены новые runtime-lookup, UI/диалоги и фактическое восстановление. Защита от активной нарезки не добавлялась, применять на пустой сцене. Известных сбоев в пройденных изолированных сценариях нет. Модели, слайсинг и экспорт остаются за рамками ТЗ.

## Файлы и сохранение прежних изменений

При старте уже имелись изменения ТЗ 02 и документации; они не откатывались: `README.md`, `docs/API_CONTRACT.md`, `docs/PLAN_SLICER.md`, `agent/tools.py`, `slicer/adapter.py`, `slicer/qt_bridge.py`, `plugin/README.md`, `tests/test_slicer_settings.py`; неотслеживаемые отчёты/скриншоты ТЗ 02, задание ТЗ 03, `slicer/modes.py`, `tests/test_slicer_modes.py`.

В рамках ТЗ 03 изменены/созданы только:

```text
 M docs/API_CONTRACT.md
 M plugin/AIAssistant/agent/tools.py
 M plugin/AIAssistant/slicer/adapter.py
 M plugin/README.md
 M tests/test_slicer_settings.py
?? docs/reports/TASK_03_REPORT.md
?? plugin/AIAssistant/slicer/profiles.py
?? tests/test_slicer_profiles.py
```

Также точечно обновлён ожидаемый tools/list и индекс старой схемы в ранее неотслеживаемом `tests/test_slicer_modes.py`; его прежние 24 сценария сохранены. Прочие исходные изменения остались без редактирования. Ни один файл не добавлялся в индекс, коммитов и push нет. После отчёта работа останавливается в границах ТЗ 03.
