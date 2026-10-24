# ТЗ 08: преобразования и удаление моделей

23.10.2026. Реализовано по `docs/tasks/TASK_08_MODEL_TRANSFORMS.md` на основе main `caac6f9d926df33fea6e3dfc4a47634281e90684`. **Передаётся на ревью; native-приёмка не выполнена.** Установленный плагин, STE-исходники, ключи, провайдер и принтер не затрагивались. Git-фиксаций/push/merge/новых веток исполнитель не выполнял. Авторасстановка, custom-профили и 5D не начаты.

## Изменения

- Новый `plugin/AIAssistant/slicer/transforms.py`: чтение состояния и штатные TranslateOperation/RotateOperation/ScaleOperation/RemoveSceneNodeOperation с push в app.getOperationStack, фактическое подтверждение и ограничения.
- `slicer/models.py`: выделен resolve(app,object_id) из прежнего get_bounds; используется тот же ModelRegistry, прежняя схема bounds/правила верхних моделей сохранены.
- `slicer/adapter.py`: get_model_state/move_model/rotate_model/scale_model/delete_model, прежний QtBridge и общий _change_lock, пояснение неопределённого исхода в ошибках. Guard удерживается **внутри Qt callback до подтверждения**, даже если HTTP-worker уже вернул timeout.
- `agent/tools.py`: пять схем/валидация в существующем реестре; четыре мутации внесены в is_mutating. Никакого второго реестра/адаптера. Агентный цикл, HTTP и SPA не менялись; прежняя остановка после неопределённой мутации применяется к новым именам.
- `tests/test_model_transforms.py`: 16 значимых сценариев. В tests/test_slicer_settings.py и test_slicer_modes.py расширены только два точных ожидаемых списка tools на пять новых имён; прежние имена/порядок/сценарии сохранены.
- В test_slicer_models.py один прежний тест общего бюджета переведён с sleep на управляемое время: проверяет остаток после Qt-очереди и тот же model_load_timeout. Ожидание не ослаблено до «любого timeout», runtime/таймауты импорта не менялись.
- Точечно обновлены API_CONTRACT, plugin/README, корневой README, PLAN_SLICER и устаревшая строка FRONTEND_INTEGRATION о недоступности преобразований. PowerShell-сценарии предыдущих этапов сохранены; добавлены команды 08 с ручной сверкой/Undo.

Чужие COORDINATION.md, ROLE_02_AGENT_INTEGRATION.md, задания 08/09 и Word lock-файлы сохранены. scripts/demo_checkpoint.ps1 не менялся. Новый TASK_08_REPORT.md, transforms.py и тесты остаются untracked до решения ведущего; отчёт/новые файлы учитывать вместе с обычным diff.

## Проверенные API выданного форка

Справочная копия: `<SOURCE_ROOT>/Uranium-steslicer` и соседний `STE-Slicer-master`. Пути только для исследования, в runtime не добавлены. Чужие файлы не редактировались и не копировались целиком в репозиторий.

| Источник | Вывод для реализации |
| --- | --- |
| UM/Operations/TranslateOperation.py, redo | set_position=True использует SceneNode.TransformSpace.World; это абсолютная позиция origin |
| RotateOperation.py и Tools/RotateTool | Quaternion + rotate_around_point=node.getPosition; для родителя с единичной world-матрицей local pivot совпадает с мировым origin |
| ScaleOperation.py и Tools/ScaleTool | Ветка set_scale=False/add_scale=False/relative_scale=False умножает мировые оси; pivot=node.getWorldPosition сохраняет origin |
| SceneNode.py, rotate/scale/setPosition, Matrix.py, Quaternion.py | World-преобразование, матрицы row-major и Quaternion.fromAngleAxis в радианах; после операции проверяется world_transform, не Euler |
| OperationStack.py, Operation.py, GroupedOperation.py | push не возвращает надёжного признака выполнения, при занятом lock молча выходит; после push обязательна проверка. Native merge может объединять близкие действия, пользовательский Undo требует сверки |
| RemoveSceneNodeOperation.py | Снимает parent, удаляет Selection, уведомляет backend; Undo возвращает parent. Исходный файл не удаляется |
| steslicer/BuildVolume.py:checkBoundsAndUpdate, Scene/SteSlicerSceneNode.py:isOutsideBuildArea | Обновляются границы top/печатных потомков, наружу bool либо null при ненадёжной проверке |
| steslicer/PlatformPhysics.py | Physics запускается QTimer спустя 100 мс, читает automatic_drop_down/push_free и может переносить другие узлы. Это не завершается синхронным push |

Переиспользованы прежние backend_busy и guard импорта/нарезки/экспорта. У classic/CylindricalBackend в исходниках подтверждены readonly _slicing/_start_slice_job/_generate_basement_job/_process_layers_job; local_file._writing — прежняя граница штатного экспорта. Приватные поля не записываются.

## Контракт и подтверждение

Аргументы строго по ТЗ: object_id, position_mm{x,y,z}, axis/angle_degrees, factors{x,y,z}. Только конечные числа без bool/строк, вложенные/верхние лишние поля запрещены. Factors 0.01–100, координаты и угол ±1000000; матрица и консервативная итоговая геометрия ограничены ±1000000, проверяется аффинность/обратимость. Это технические рамки прототипа, не пределы принтера; консервативная оценка может отказать и при фактически допустимой экстремальной геометрии.

get_model_state возвращает `{object_id,position_mm,world_transform,bounds,build_plate,outside_build_area}`. Оси Uranium Y-up, мм, матрица 4x4, scale — множитель **мировых осей**. move/rotate/scale возвращают `{object_id,before,after,changed,warnings}`, удаление — `{removed_object_id,before,after:null,removed:true}`. before/after и габариты перечитаны из сцены; near использует rel_tol=1e-6/abs_tol=1e-5 для сравнения float32. При ненулевом запросе полностью неизменная матрица не считается успехом даже для малого угла в допуске.

Перемещение/поворот/масштаб выполняются через native operations без изменения mesh. Для поворота градусы переводятся в радианы, pivot сохраняется. Для масштабирования отключена relative_scale, факторы применяются к мировым осям, не просто складываются. No-op — тождественная позиция/0 или целое число оборотов/factors=1: без создания/push операции и без инвалидации native slice. Фактический или неподтверждённый push консервативно инвалидирует slice, включая исключение после начавшейся записи; автоотката нет. Существующий агент может консервативно снять valid кеша и при no-op; свежий status даёт фактическую native-актуальность.

ID повторно разрешается непосредственно перед записью; проверяются машина, scene/root, активная платформа, принадлежность всех печатных частей группы этой платформе. Изменения запрещены при занятости backend/штатной записи/общего guard, в preview, для disabled/неактивной модели и при неединичной world-матрице родителя. Верхняя преобразованная группа под единичным родителем поддерживается целиком. Прочие модели проверяются на сохранение membership/матриц; удаления не подтверждаются только кешем. После удаления resolve/list_models очищает прежний ID; Undo возвращает объект с новым ID при следующем list_models. Старый ID не переиспользуется.

outside_build_area=true даёт предупреждение, без коррекции положения и без обещания готовности к печати. При отсутствии volume bbox/надёжного getter — null, не придуманный false. Чтение state также обновляет производную boundary-проверку на Qt; настройки проекта не меняет.

Новые ошибки: model_operation_busy, model_transform_unsupported, model_transform_not_applied. Сохранены model_not_found/model_bounds_unavailable/no_active_machine, qt_timeout/slicer_not_connected/slicer_error. Неверные схемы — MCP -32602, операционные ошибки — прежние ok:false/isError. После неизвестного исхода нужен get_model_state/list_models/UI, не автоповтор. Pending callback отменяется мостом; running Qt-operation может закончиться позже, до её выхода guard не освобождается. Shutdown/Undo не убивают чужой engine и не удаляют файлы.

## Проверки

Среда — отдельный Python 3.13.6, Node v24.18.0. Основной набор использует подменные UM/STE/Qt и LLM; это не installed runtime.

| Проверка | Результат |
| --- | --- |
| `python -m unittest discover -s tests` | **Итоговые 197 тестов OK, 12.991 сек** после всех изменений: все 181 прежних + 16 новых. Ранее полный набор также прошёл за 12.522 сек; ведущий независимо получил 197 OK за 12.246 сек |
| `python -m unittest discover -s tests -p test_model_transforms.py -v` после адресного уточнения no-op/малого проигнорированного угла | **16 тестов OK, 0.575 сек** |
| `node --check plugin/AIAssistant/web/app.js` и `node tests/test_agent_web.js` | OK, **5 offline DOM-сценариев сохранены**; web/ не менялся |
| Python 3.5 grammar через ast.parse(feature_version=(3,5)) | **29 файлов plugin/scripts/tests OK**, api:5 сохранён |
| `git diff --check` | Без ошибок |

Новые сценарии: вложенная валидация до API/NaN/inf/bool/границы; dev/неизвестный ID; одинаковые имена; worker/реальный реестр/Qt; абсолютный перевод; известные вершины/90°/масштаб мировых осей/origin; группа; delete/Selection/исходный файл/Undo/новый ID; no-op/актуальный slice и stale после изменения; guard/backend/export/чтения; push без применения/зависимая коррекция/исключение без отката; родитель/preview/платформа/disabled/singular/экстремальный результат; включённая/неподтверждённая физика без изменения Preferences; outside/неизвестная проверка; смена контекста/посторонней модели; pending timeout/shutdown и удержание guard при running timeout; агент после неуспешного move не выполняет следующий delete и освобождает processing.

Первый полный набор выявил второй забытый exact-list инструментов и qt_timeout вместо model_load_timeout в старом тесте короткого общего бюджета при параллельной загрузке NumPy для отдельной пробы. Список расширен без удаления старых ожиданий; следующий полный набор прошёл. Позже та же deadline-гонка старого теста повторилась без пробы: очередной Qt-poll попадал на границу общего deadline. В одном тесте sleep заменён управляемыми clock адаптера/моста: сначала виртуальная очередь 0.15 сек, реально выполняемый start/check через исходный QtBridge, затем deadline после завершённого check. Проверяются ровно два bridge.run с бюджетами 0.25 и 0.10 сек, отсутствие свежего бюджета и прежний model_load_timeout; отказ pending Qt отдельно остаётся qt_timeout в соседних сценариях. Это устраняет зависимость от планирования на последних миллисекундах, не меняет runtime-код/бюджет импорта и не допускает произвольный timeout ради успеха. Итоговый полный набор прошёл.

Первая новая проверка журнала содержала приватный маркер прямо в строке исходника raise; маркер вынесен в переменную, чтобы проверять утечку runtime-значения, а не отображение статической строки traceback. Приватные значения в сообщениях/журнале не появились. Последние уточнения 08: no-op определяется самим тождественным запросом, а не широким допуском матрицы; полностью неизменная матрица при ненулевом малом повороте считается отказом. Проверяются платформы всех печатных частей группы, state не вычисляет повторно bounds через registry.item. Все эти изменения включены в итоговый полный прогон.

Дополнительно **успешна read-only проба настоящих классов** из выданной справочной копии: SceneNode, Matrix, Quaternion, Translate/Rotate/Scale/RemoveSceneNodeOperation и OperationStack. Подтверждены origin (10,20,30), поворот Y=90°, габариты (4,3,2), множители (2,3,4) → (8,9,8), три Undo до исходной матрицы и delete/Undo с новым ID. Использованы инертные сигналы, подменные app/mesh/BuildVolume/Selection, NumPy 2.3.3 отдельного Python. Изолированный исследовательский скрипт `tmp/task08_fork_probe.py` игнорируется Git, не является runtime-зависимостью/поставкой. Он не подтверждает PyQt, асинхронные сигналы, UI или физику STE.

## Ограничения и native-приёмка

1. **Новая установленная копия не обновлялась.** Не подтверждены реальный импорт/материалы/качество и прочие открытые ТЗ 03–07, агент, native-преобразования/Selection/Undo/физика/BuildVolume ТЗ 08. Подмены/справочные классы не заменяют эту приёмку.
2. **Автоматическая физика должна быть выключена пользователем.** physics/automatic_drop_down и physics/automatic_push_free принимаются только подтверждённо false. При true/неизвестном значении — model_transform_unsupported до push. Плагин не меняет Preferences и не ждёт стабилизацию physics. Это явное ограничение, необходимое для origin/сохранности соседей; проверка этой границы в установленном UI остаётся открытой.
3. Поддерживаются активная платформа, включённые верхние модели/группы под единичным мировым родителем. Другой parent transform отклоняется; служебные/preview/дети групп не адресуются. Нет автопереноса на стол, коллизий/auto-arrange, зеркалирования, произвольного задания matrix/Euler или создания нового mesh.
4. Native OperationStack может объединять соседние операции, в том числе по короткому временному окну. Один tool не гарантирует один Undo. Source mergeWith Rotate/Scale/Translate хранит старую трансформацию для Undo, но детали повторного Redo объединённых действий требуют отдельной runtime-сверки; plugin не переписывает операции форка. Обратные LLM-команды вместо Undo не выполняются. После Undo старая нарезка не становится снова актуальной автоматически.
5. Чтение/запись возвращает снимок на момент Qt-проверки. Параллельные ручные правки сцены/платформы/запуск jobs не поддерживаются. При ошибке уже начатой мутации исход может остаться неопределённым, поэтому сохранённый агент останавливает дальнейшие действия. Автоматического исправления/отката нет.

Ручной сценарий и PowerShell-команды — раздел ТЗ 08 в plugin/README.md: текущий порт → диагностический импорт маленького тестового STL → baseline state → move/проверка UI/Undo до baseline → rotate/проверка/Undo → scale/проверка/Undo → delete/отсутствие/сохранность соседей и исходного файла → Undo/list_models/новый ID. Отдельно — включённая физика должна дать отказ без записи, outside-build-area даёт предупреждение, изменение делает slice stale. Настройки физики после теста восстанавливаются пользователем вручную; ничего не печатать. Установку и native-прогон организует ведущий после ревью.

После отчёта исполнитель останавливается. Следующие этапы и Git-действия — только по отдельному назначению/разрешению.

## Дополнение после ревью: cylindrical busy-check

Ведущий обнаружил несовпадение CLIParserBackend с layout classic: CliParserBackend.py:90+ объявляет _process_cli_job/_glicer_process вместо _generate_basement_job. CylindricalBackend.py:70–89 имеет и basement, и CLI-стадию/Glicer. Общий backend_busy явно различает три подтверждённых plugin ID/layout: CuraEngineBackend (прежние четыре поля), CLIParserBackend с классом CliParserBackend (start/process_cli/process_layers/_slicing/Glicer), CylindricalBackend (все эти стадии плюс basement). Живой Glicer проверяется неблокирующим poll(); отсутствующие обязательные поля/неизвестный backend дают backend_state_unavailable, не fallback в idle. Поля только читаются, engine не вызывается/не останавливается. Fixture classic использует реальный ID CuraEngineBackend вместо условной строки; проверка идентификатора сохранена.

Добавлен сценарий idle CLI без basement → move_model в cylindrical, занятые CLI jobs/живой process → запрет мутации, завершённый process → idle; также idle cylindrical_full → move и каждый job/live Glicer при _slicing=False → busy. Неизвестный layout/ID → отказ. Адресный набор преобразований: 17 тестов OK; финальный полный результат включается в отчёт ТЗ 09. Это исправление границы 08, не 5D-нарезка и не native-приёмка.
