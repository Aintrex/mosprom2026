# Отчёт по ТЗ 04 — локальный mesh-импорт и габариты

Дата: 23.10.2026. **Реализация готова к ревью и отдельной runtime-приёмке. Реальный импорт в STE Slicer не проводился.** Установленный плагин не обновлялся, приложение не перезапускалось. Ручная приёмка ТЗ 03 остаётся открытым отдельным пунктом. Staging/commit/push, слайсинг и следующие этапы не выполнялись.

## Файлы и объём

- Новый `plugin/AIAssistant/slicer/models.py`: проверка локального файла вне Qt, обход сцены, фильтрация моделей/групп, слабый реестр ID, мировые границы, контекст одного импорта с сигналами и поздним завершением.
- `slicer/adapter.py`: `load_model`, `list_models`, `get_model_bounds`, общий бюджет загрузки 10 секунд, использование существующей блокировки режим/профиль/материал. После потенциального запуска блокировку держит контекст job, а не время жизни HTTP worker. При close сначала выполняется Qt-очистка model-контекста, затем закрывается мост; повторный close безопасен.
- `agent/tools.py`: три новые схемы и вызова через существующий общий whitelist/реестр. Status перечисляет 11 инструментов слайсера, сохраняет `llm_connected=false`, `runtime_verified=false`.
- Новый `tests/test_slicer_models.py`: 22 сценария с fake reader/сценой/сигналами, настоящими временными файлами и локальным HTTP. Прежние `tests/test_slicer_settings.py` и `tests/test_slicer_modes.py` изменены только в ожидаемом списке инструментов; 72 прежние проверки сохранены.
- `docs/API_CONTRACT.md`, `plugin/README.md`: действующий MCP-контракт, коды ошибок, ограничения, последовательные команды PowerShell с таймаутом клиента 35 секунд. Будущие REST/upload не реализованы.

Нет преобразований/удаления объектов, отдельной авторасстановки, LLM, HTTP-upload, экспортёров, нового загрузчика, зависимостей или универсального менеджера задач. `server.py`, SPA, Extension, `profiles.py`, `modes.py` и `qt_bridge.py` не менялись. Модели организаторов/дистрибутивы/исходники извне не добавлялись.

## Подтверждённые исходники

Читались STE `<SOURCE_ROOT>/STE-Slicer-master` и соседний `Uranium-steslicer`. Они не менялись и не копировались в Git; эти пути не используются runtime-кодом.

| API / источник | Подтверждение и использование |
| --- | --- |
| STE `steslicer/SteSlicerApplication.py:1492` | readLocalFile принимает QUrl и skip_project_file_check; с True обходит openProjectFile/checkIsValidProjectFile и запускает ReadMeshJob. Вызывается из короткого Qt-callback. |
| Тот же файл, 1498–1505 | isBlockSlicing может привести к deleteAll до импорта. Наш preflight проверяет весь root и запрещает импорт при таком декораторе до readLocalFile. |
| Тот же файл, 1542–1642 | `_readMeshFinished`: fileLoaded до добавления узлов, fileCompleted после цикла; пустой результат тоже может завершиться. Отдельные ранние выходы не дают completion. Оба сигнала — pyqtSignal(str). |
| Uranium `UM/Mesh/ReadMeshJob.py`, `UM/FileHandler/ReadFileJob.py` | Штатное чтение выполняется job; ошибки reader могут дать пустой результат, prefs управляют autoscale. Собственный reader/job не создаётся; его обращения к приложению не воспроизводятся нашим worker. |
| Uranium `UM/Mesh/MeshFileHandler.py` | Reader-путь может центрировать геометрию/части. Возвращаются реальные итоговые world extents, исходные размеры по файлу не угадываются. |
| STE `plugins/3MFReader/ThreeMFReader.py:90–225` | Вложенные узлы/группы, трансформации и единицы, преобразование осей в Uranium, per-object settings. Mesh-путь не означает удаления overrides объектов или восстановления workspace. |
| Uranium `UM/Scene/Scene.py:136`, `UM/Scene/SceneNode.py` | findObject сравнивает Python id(node); подтверждённого публичного node.getId нет. Используется свой последовательный непрозрачный ID без обращения к имени. SceneNode.getChildren/getMeshData/getWorldTransformation дают фактические узлы/геометрию. |
| Uranium `UM/Scene/Iterator/DepthFirstIterator.py` | Подтверждена структура дерева; наш небольшой рекурсивный обход getChildren позволяет остановиться на верхней модели и не дублировать части группы. |
| Uranium `UM/Scene/SceneNode.py:716` и STE `Scene/SteSlicerSceneNode.py` | getBoundingBox пустого родителя включает его мировую позицию и может расширить группу origin. Поэтому bbox группы не используется как источник геометрии. |
| Uranium `UM/Mesh/MeshData.py:127,187`, `UM/Math/AxisAlignedBox.py` через результаты getExtents | getVertexCount, getExtents(world_transform), minimum/maximum. Объединяются реальные границы печатных mesh-потомков; оси сохраняются как X/Y/Z Uranium, Y вверх, мм. |
| STE `Scene/SliceableObjectDecorator.py`, `BlockSlicingDecorator.py`, `BuildPlateDecorator.py`; Uranium `GroupDecorator` | isSliceable/isGroup — признаки пользовательской печатной модели/группы; isBlockSlicing исключает preview и блокирует импорт; getBuildPlateNumber даёт номер или null. Actual mesh-узлы 3MF reader получают SliceableObjectDecorator. |
| Uranium `UM/Signal.py`, `UM/Job.py` | Job.finished использует стандартный Signal.Auto с перенаправлением в основной поток. Собственные app-сигналы подключаются/отключаются на Qt; неверный поток сигнала не используется для подтверждения сцены. |

Различия установленной сборки для нового сценария не проверены. Наличие Python 3.5-совместимого синтаксиса и предыдущая приёмка других инструментов не подтверждают runtime ТЗ 04.

## Контракт и обработка файлов

Новые schemas в том же ToolRegistry:

```json
{"name":"load_model","inputSchema":{"type":"object","properties":{"path":{"type":"string","minLength":1}},"required":["path"],"additionalProperties":false}}
```

```json
{"name":"list_models","inputSchema":{"type":"object","properties":{},"required":[],"additionalProperties":false}}
```

```json
{"name":"get_model_bounds","inputSchema":{"type":"object","properties":{"object_id":{"type":"string","minLength":1}},"required":["object_id"],"additionalProperties":false}}
```

Структура проверяется до app/file I/O. Синтаксически корректный, но недоступный/неподдерживаемый path или отсутствующий object_id — операционная ошибка, а не JSON-RPC -32602. Локальный файл проверяется в worker: абсолютный путь, regular/readable, расширение STL/OBJ/3MF без учёта регистра, размер **1–52428800 байт**. После realpath снова исключаются сетевые/device-пути и недопустимое расширение; UNC, URL, относительные пути, ADS, каталоги, нулевой/превышенный размер отклоняются. На Windows GetDriveTypeW исключает mapped network drive. Выполняются stat/fstat и чтение одного байта, не собственный разбор содержимого модели.

path — временная диагностическая граница прямого MCP. Нет раздачи произвольного файла и HTTP-upload. Будущий file_id должен поступать от доверенного upload API, это следующий отдельный контракт. Полный путь модели не возвращается в успехе и не попадает в новые сообщения/логи плагина; штатный reader имеет собственные сообщения/логи и может показывать путь.

Успешные data:

- list_models: `{models:[{object_id,name,build_plate,is_group,bounds}]}`; пустая сцена — `models:[]`.
- get_model_bounds: `{object_id,bounds}` с повторным чтением актуальной геометрии.
- load_model: `{file_name,objects:[...]}`, только basename и модели собственного подтверждённого импорта. Повторный файл не no-op, ему выдаются новые ID.

Все результаты — `{ok:true,data}` или `{ok:false,error:{code,message}}`, MCP помещает JSON-текст в content и ставит isError. Внутренний KeyError API не становится Unknown tool. Новые коды: `invalid_model_path`, `unsupported_model_file`, `invalid_model_file`, `model_file_unavailable`, `model_not_found`, `model_bounds_unavailable`, `model_preview_active`, `model_load_busy`, `model_load_failed`, `model_scene_changed`, `model_load_timeout`. Сохранены `slicer_not_connected`, `no_active_machine`, `qt_timeout`, `slicer_error` и старые busy-коды конкурирующих setters.

## ID, группы и оси

ModelRegistry выдаёт model-N монотонно в сессии, без сериализации Python id. Карта хранит weakref, не удерживает удалённый node действующим объектом. Перед каждым list/bounds проверяются актуальные верхние модели, принадлежность scene/root и живость ссылки. Имена могут совпадать; выданные номера не переиспользуются. Смена scene/root, удаление или уход объекта внутрь другой верхней группы инвалидируют прежнюю адресацию.

Верхняя модель — isSliceable mesh либо isGroup с печатными mesh-потомками. Части группы отдельно не перечисляются. Root и служебные ветви без этих декораторов не становятся моделями; isBlockSlicing исключён. Для размера используются только непустые isSliceable mesh-узлы, без фиктивной позиции пустого group-parent и сервисных mesh. Поле build_plate — int или null из декоратора.

bounds имеет `{coordinate_system:"uranium_world_y_up",unit:"mm",min:{x,y,z},max:{x,y,z},size:{x,y,z}}`. Y — вертикаль, X/Z — стол. Применяется getExtents с мировым преобразованием каждого mesh; scale/rotation и все родители учитываются API. Нефинитные/перевёрнутые границы, недоступная геометрия дают bounds=null в списке и model_bounds_unavailable при отдельном запросе. Нулевой размер оси допустим.

## Корреляция, timeout и жизненный цикл

1. File preflight — worker. На Qt сохраняются машина, scene/root и все исходные узлы; импорт поверх preview отклоняется. Подписки на fileLoaded/fileCompleted устанавливаются до readLocalFile. 3MF использует `skip_project_file_check=True` как остальные mesh-файлы.
2. Пути сигналов сопоставляются normpath/normcase без filesystem I/O на Qt. Свой fileLoaded фиксирует окно перед добавлением; изменения дерева до этого окна/повторный свой loaded трактуются как обнаруженный конфликт. Чужие сигналы не подтверждают успех.
3. Только свой fileCompleted плюс новые верхние модели в этом окне даёт успех. Проверяются прежние scene/root/машина и сохранность исходных узлов. Пустое completion — ошибка, ранний loaded или просто новый посторонний node — не успех.
4. Worker ждёт короткими bridge-проверками в общем бюджете **10 секунд**, включая очередь первого callback. Каждый Qt-вызов ограничен оставшимся бюджетом, отдельный вызов — также обычным timeout моста. HTTP socket timeout остаётся 15 секунд; ручные команды клиента используют 35 секунд. Прямой Qt-load запрещён до файлового чтения/запуска.
5. До запуска job просроченный callback/подготовка отменяется без позднего импорта. После запуска job не отменяется: timeout сообщает неопределённость и list_models, автоматического повтора/удаления нет. Блокировка режим/профиль/материал/load сохраняется контекстом, списки доступны.
6. Свой поздний completion снимает подписки/ссылки и блокировку на Qt даже после завершения HTTP worker. Подтверждённая ошибка подготовки или пустое завершение тоже очищаются. Ошибка readLocalFile после потенциального запуска консервативно удерживает контекст до completion/shutdown, поскольку факт отсутствия job не подтверждён. При отсутствии completion необходим перезапуск, мнимая отмена не заявляется.
7. Shutdown выполняет model cleanup на Qt перед закрытием моста; pending-start не запускается позднее. При нетипичном закрытии из worker и недоступном Qt очистка ограничена таймаутом и безопасно логируется; штатный Extension вызывает close из Qt applicationShuttingDown.

Ручной параллельный импорт/очистка не поддерживаются. API не даёт уникального request ID, поэтому неотличимые ручные операции того же файла не могут быть надёжно различены. Контракт window-корреляции опирается на подтверждённый синхронный порядок fileLoaded → цикл добавления → fileCompleted в _readMeshFinished; на установленной сборке это ещё нужно подтвердить.

## Автоматические проверки

Из корня репозитория:

```powershell
python --version
python -m unittest discover -s tests -v
git diff --check
git status --short --untracked-files=all
```

Финальный результат: **94 теста, OK, 7.491 секунды**, Windows, Python **3.13.6**. Все 72 прежних проверки сохранены; 22 сценария добавлены для ТЗ 04. Первый прогон 92/92 тоже прошёл, после него добавлены проверки отмены во время подготовки и общего бюджета с задержкой очереди. Финальные тесты прошли без сетевых ошибок. `git diff --check` — без замечаний.

Проверено:

- Валидация аргументов до API/файлов, абсолютность/URL/UNC/расширение/наличие/regular/чтение/размер. Временный oversized-файл создаётся truncate и удаляется TemporaryDirectory, в Git не попадает. Неподходящий запрос не запускает readLocalFile; filesystem проверки не выполняются на Qt.
- Пустая сцена, служебные узлы/preview, группа без дубликатов частей, одинаковые имена и стабильные разные ID; удалённый/устаревший ID и непереиспользование. Scene/node/signal API проверяют основной поток.
- Вложенные мировые translation/scale/rotation, оси Y-up/mm, нулевая толщина, недоступные/NaN-границы; группа без расширения origin, повторное актуальное чтение после изменения transform.
- Ранний loaded, чужие signals, новые узлы без completion — не успех; пустой completion или completion без своего loaded — ошибка. Несколько новых моделей/группа, три расширения, skip_project_file_check=True, повторный импорт и сохранность старых моделей.
- Отсутствие машины, preview до запуска, обнаруженная посторонняя геометрия до окна, смена машины/сцены, API KeyError без приватного пути/Unknown tool.
- Timeout до callback и во время подготовки без позднего старта; общий бюджет включает задержку первой очереди. Timeout после старта сохраняет job/lock, блокирует повторный импорт/режим/качество/материал, но разрешает list_models; поздний completion очищает context/subscriptions без повтора.
- Shutdown в полёте и до pending-start, снятие сигналов на Qt. Прямой Qt-load не запускается и не читает файл. Общие schemas/MCP/dev-ошибки; настоящий локальный HTTP → MCP → fake import → bounds/ошибка неизвестного ID.
- Сохранены dev-сервер без UM/PyQt5, api:5 и общий syntax parse всех plugin/scripts/tests как Python 3.5. Fake QUrl подменяется только на время новых тестов и не делает dev-проверку Qt-зависимой.

Это не запуск native reader, Qt или кода на Python 3.5. Файлы новых тестов — фиктивные reader-входы, а геометрия/сигналы моделируются подменами; успешный тест расширения .3mf не означает реальную проверку Savitar/3MF.

## Ручной прогон и ограничения

**Runtime ТЗ 04 не проверен.** Плагин не устанавливался, приложение не перезапускалось, настоящая сцена/профили/материалы не менялись. Приёмка ТЗ 03 не закрывается этим отчётом.

В [plugin/README.md](../../plugin/README.md), раздел «Команды ручной приёмки ТЗ 04», подготовлен самостоятельный PowerShell-блок: текущий порт, initialize/tools/list, baseline list_models/машины/режима/качества/материалов; последовательные load_model для пользовательских STL/OBJ/3MF; повторные list_models/get_model_bounds; повторный STL, неизвестный ID и неверные запросы. Baseline сохраняется пользователем в игнорируемый tmp/task04_before.json. Эти команды исполнителем не выполнялись.

После ревью ведущий координирует обновление и проверку: сохранить проект; Hybrid 530 V5.2.4, classic, без слайсинга/preview. Проверить известные размеры с учётом осей и autoscale, несколько объектов/группу, реальные ID, исходные модели, отсутствие workspace-диалога и замены глобальной конфигурации при 3MF. Per-object overrides отметить отдельно. Нет контрольного файла формата — зафиксировать непроверенный пункт. Удаление тестовых объектов выполняет пользователь в штатном UI, затем проверяются stale ID и сохранность исходной сцены.

Остаются неподтверждёнными native STL/OBJ/3MF reader, фактический порядок/поток сигналов сборки, global state при mesh-3MF, авторазмещение/масштабирование и скорость выданных моделей. Ограничение 50 MiB/10 секунд — прототип: если файлы не укладываются, требуется отдельное согласование async operation_id/polling; текущий HTTP-контракт не расширялся. Защита от активного слайсинга не добавлялась; приёмка без текущей нарезки. Параллельные ручные импорты/очистка не поддерживаются. Известных сбоев в пройденных изолированных сценариях нет.

## Git status

При старте рабочее дерево было чистым. Изменения задания:

```text
 M docs/API_CONTRACT.md
 M plugin/AIAssistant/agent/tools.py
 M plugin/AIAssistant/slicer/adapter.py
 M plugin/README.md
 M tests/test_slicer_modes.py
 M tests/test_slicer_settings.py
?? docs/reports/TASK_04_REPORT.md
?? plugin/AIAssistant/slicer/models.py
?? tests/test_slicer_models.py
```

Никаких файлов в индексе/коммитов/push и установки плагина не выполнялось. Работа остановлена в рамках ТЗ 04; слайсинг и экспорт не начинались.
