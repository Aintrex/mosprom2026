# Подробная проверка AI Assistant в STE Slicer

Для пользователя24.10.2026. Пока «Разраб» выполняет роль2D, проверяйте **сохранённую копию до этих изменений**. Принятая база:296 Python/10 JS с подменами; реальные новые функции STE ещё не подтверждены. Физическую печать не запускаем. Проверка через PowerShell не требует LLM и ключа.

## 1. Какую папку установить

Источник для этой проверки:

`C:\Users\sevay\Documents\ChatGPT\hackaton_2026\tmp\acceptance\baseline_296\AIAssistant`

Сохранены29 файлов, SHA256 совпали с исходниками; рядом manifest.json. Это локальная копия для приёмки, не новая ветка/коммит. Текущий `plugin/AIAssistant` может меняться разработчиком — не смешивайте его файлы с этой копией.

1. Сохраните открытый проект в STE через меню приложения. Запомните исходные машину, профиль, материалы и слой; сделайте скриншот.
2. Справка→Показать папку конфигурации. Для этой установки обычно `C:\Users\sevay\AppData\Roaming\steslicer\7.10`.
3. Закройте STE штатно. Старую папку plugins/AIAssistant скопируйте в резервное место **вне plugins** (например, отдельную папку на Рабочем столе). Не удаляйте остальную конфигурацию.
4. Замените установленную папку AIAssistant сохранённой копией. Не оставляйте старые файлы внутри неё. Итог: `...\7.10\plugins\AIAssistant\plugin.json`, рядом AIAssistant.py, agent/, slicer/, web/. Вложенности `AIAssistant/AIAssistant` быть не должно.
5. Запустите STE, выберите **Stereotech Hybrid530V5.2.4**, режим3D, пустую тестовую сцену. Выключите автоматическую нарезку и автоматическую физику/опускание/раздвигание моделей в настройках приложения; точные названия зависят от языка UI. Не меняйте неизвестные параметры наугад: при physics_enabled пришлите ошибку/скриншот.
6. Extensions→AI Assistant→Open chat. В адресе `http://127.0.0.1:XXXXX/` число XXXXX — текущий порт. После перезапуска он меняется. Для тестов ниже LLM Settings можно пока не заполнять.

Если пункта AI Assistant нет, дальнейшие команды пока не выполняйте: пришлите версию STE, путь установленного plugin.json и фрагмент ошибки загрузки из stderr.log/лога конфигурации. Ключи/лицензию в сообщение не добавляйте.

## 2. Подготовить одно окно PowerShell

Откройте PowerShell и выполните **обе строки**, включая точку перед путём. Точка загружает функции в это окно; helper сам не меняет сцену и не запускает нарезку.

```powershell
. 'C:\Users\sevay\Documents\ChatGPT\hackaton_2026\scripts\manual_acceptance.ps1'
Connect-QASlicer -Port ([int](Read-Host 'Введите текущий порт чата')) | ConvertTo-Json -Depth 20
```

При Read-Host вводите только число, без URL/скобок/кавычек. Это окно оставьте открытым: следующие блоки используют его переменные. Если PowerShell запрещает запуск скрипта, пришлите точный текст ошибки — не меняйте системную политику вслепую.

Проверить команды и машину:

```powershell
(Invoke-QARpc 'tools/list' @{}).result.tools | Select-Object name
$qaState = Invoke-QATool 'get_slicer_state' @{}
$qaState | ConvertTo-Json -Depth 25
```

Ожидаем нужную машину, printing_mode=classic, backend.matches_mode=true. Health/список tools подтверждают доступность HTTP, не готовность engine/лицензии. Ошибка подключения обычно означает старый порт, неоткрытый чат или незагруженный плагин.

## 3. Высота слоя: изменить и вернуть

Раскройте Quality в STE. На пустой сцене:

```powershell
$qaLayerBefore = Invoke-QATool 'get_setting' @{key='layer_height'; scope='global'}
$qaOriginalLayer = $qaLayerBefore.value
$qaLayerBefore | ConvertTo-Json
$qaNewLayer = if ($qaOriginalLayer -eq 0.12) { 0.1 } else { 0.12 }
Invoke-QATool 'set_setting' @{key='layer_height'; scope='global'; value=$qaNewLayer} | ConvertTo-Json
```

Если0.1/0.12 не подходит фактическому соплу/профилю, остановитесь и используйте допустимое значение после проверки describe_settings/UI. Успех: after совпадает с полем STE. Сделайте скриншот, затем восстановите:

```powershell
Invoke-QATool 'set_setting' @{key='layer_height'; scope='global'; value=$qaOriginalLayer} | ConvertTo-Json
Invoke-QATool 'get_setting' @{key='layer_height'; scope='global'} | ConvertTo-Json
```

Сверьте восстановление в UI. Не продолжайте после ошибки записи, не предполагая, что она ничего не изменила.

## 4. Три режима — пока сцена пустая

Раскройте Printing Mode. Выполняйте по **одной** строке, каждый раз сверяя UI и backend.matches_mode:

```powershell
Invoke-QATool 'set_print_mode' @{mode='cylindrical'} | ConvertTo-Json -Depth 20
Invoke-QATool 'set_print_mode' @{mode='cylindrical_full'} | ConvertTo-Json -Depth 20
Invoke-QATool 'set_print_mode' @{mode='classic'} | ConvertTo-Json -Depth 20
```

UI:5D Spiral→5D→3D. Это проверка переключения, ещё не5D-нарезка.

## 5. Настройки, качество и материалы

```powershell
Invoke-QATool 'describe_settings' @{scope='global'} | ConvertTo-Json -Depth 30
Invoke-QATool 'describe_settings' @{scope='extruder'; extruder_id='0'} | ConvertTo-Json -Depth 30
$qaQualityBefore = Invoke-QATool 'list_quality_profiles' @{}
$qaMaterialBefore = Invoke-QATool 'list_materials' @{extruder_id='0'}
$qaQualityBefore | ConvertTo-Json -Depth 25
$qaMaterialBefore | ConvertTo-Json -Depth 25
```

ID экструдера берите из диагностики: строка'0' означает первую позицию, а не UI-кнопку с цифрой0. Второй экструдер проверяйте по его фактическому ID. Сейчас список разрешённых настроек ограничен, не все100+ полей доступны.

Для выбора возьмите **реальные** quality_type/root_material_id из этих ответов:

```powershell
$qaQualityType = Read-Host 'quality_type доступного встроенного профиля'
Invoke-QATool 'select_profile' @{quality_type=$qaQualityType} | ConvertTo-Json -Depth 25
$qaMaterialId = Read-Host 'root_material_id доступного материала для экструдера0'
Invoke-QATool 'select_material' @{extruder_id='0'; root_material_id=$qaMaterialId} | ConvertTo-Json -Depth 25
```

Сверьте профиль/материал в STE. До нарезки выберите материал, подходящий задаче. Если активен custom_profile и инструмент отказывается переключать builtin, используйте штатный UI: автоматического сброса custom нет. Старые ID/названия не придумывать.

## 6. Импорт модели и габариты

Начните с одной небольшой реальной STL из набора организаторов. В Read-Host вводится абсолютный путь **без окружающих кавычек**. Не импортируйте дважды после timeout: reader может ещё работать.

```powershell
$qaModelPath = Read-Host 'Полный путь к STL/OBJ/3MF'
$qaLoaded = Invoke-QATool 'load_model' @{path=$qaModelPath}
$qaLoaded | ConvertTo-Json -Depth 25
Invoke-QATool 'list_models' @{} | ConvertTo-Json -Depth 25
foreach ($qaObject in $qaLoaded.objects) {
    Invoke-QATool 'get_model_bounds' @{object_id=$qaObject.object_id} | ConvertTo-Json -Depth 25
}
```

Успех: модель видна на столе, IDs существуют, размеры соответствуют UI. Единицы мм; Y — вертикаль, X/Z — стол. OBJ/3MF проверить отдельными импортами после сохранения/очистки тестовой сцены. Здесь3MF импортируется как модель; восстановление workspace проверяется позже через меню открытия проекта.

## 7. Преобразования и расстановка — отдельный тест до нарезки

Физика/авторасстановка UI выключены. Для одной загруженной верхней модели:

```powershell
$qaObjectId = $qaLoaded.objects[0].object_id
$qaModelBefore = Invoke-QATool 'get_model_state' @{object_id=$qaObjectId}
$qaModelBefore | ConvertTo-Json -Depth 25
$qaPosition = $qaModelBefore.position_mm
Invoke-QATool 'move_model' @{object_id=$qaObjectId; position_mm=@{x=($qaPosition.x+5); y=$qaPosition.y; z=$qaPosition.z}} | ConvertTo-Json -Depth 25
```

Сверьте перемещение на5мм поX, затем восстановите позицию:

```powershell
Invoke-QATool 'move_model' @{object_id=$qaObjectId; position_mm=@{x=$qaPosition.x; y=$qaPosition.y; z=$qaPosition.z}} | ConvertTo-Json -Depth 25
Invoke-QATool 'rotate_model' @{object_id=$qaObjectId; axis='y'; angle_degrees=15} | ConvertTo-Json -Depth 25
```

Сверьте поворот, затем **Undo в UI**, и перечитайте get_model_state. Отдельно scale_model с factors={x=1.1,y=1.1,z=1.1}, затем Undo. Delete_model проверяйте только на ненужной тестовой копии с последующим Undo; не удаляйте рабочую деталь ради проверки.

Авторасстановка в classic:

```powershell
$qaArrange = Invoke-QATool 'arrange_models' @{}
Wait-QAOperation -Kind arrangement -Id $qaArrange.arrangement_id | ConvertTo-Json -Depth 25
```

Сверьте размещение глазами. Completed означает завершение job, all_fit=null не является доказательством отсутствия всех коллизий. После любых преобразований проверьте, что модель внутри стола; при необходимости восстановите тестовый проект.

## 8. Настоящая нарезка в3D

Одна подходящая деталь внутри стола, auto-slice выключен, режимclassic, preview закрыт. После начала до сохранения **обоих** файлов ничего в сцене/профиле/материале не меняйте.

```powershell
$qaBaseline = @{
    state = Invoke-QATool 'get_slicer_state' @{}
    quality = Invoke-QATool 'list_quality_profiles' @{}
    material0 = Invoke-QATool 'list_materials' @{extruder_id='0'}
    material1 = Invoke-QATool 'list_materials' @{extruder_id='1'}
    layer = Invoke-QATool 'get_setting' @{key='layer_height'; scope='global'}
    models = Invoke-QATool 'list_models' @{}
}
$qaSliceStart = Invoke-QATool 'start_slicing' @{}
$qaSliceId = $qaSliceStart.slice_id
$qaSliceStart | ConvertTo-Json -Depth 25
$qaSliceStatus = Wait-QAOperation -Kind slice -Id $qaSliceId
$qaSliceResult = Invoke-QATool 'get_slicing_result' @{slice_id=$qaSliceId}
$qaSliceResult | ConvertTo-Json -Depth 25
```

Успех: completed/valid=true, время/материал взяты из STE и совпадают с UI с учётом округления. Null означает отсутствие подтверждённой оценки. Queued/running после запуска — ещё не успех. Helper опрашивает только статус; если ожидание/инструмент завершились ошибкой, новый start автоматически не выполняется.

## 9. Экспорт обоих файлов — строго последовательно

```powershell
$qaGcodeStart = Invoke-QATool 'export_gcode' @{slice_id=$qaSliceId}
$qaGcode = Wait-QAOperation -Kind export -Id $qaGcodeStart.export_id
$qaProjectStart = Invoke-QATool 'save_project_3mf' @{slice_id=$qaSliceId}
$qaProject = Wait-QAOperation -Kind export -Id $qaProjectStart.export_id
$qaGcode | ConvertTo-Json -Depth 25
$qaProject | ConvertTo-Json -Depth 25
```

Оба ответа должны быть completed/valid=true с artifact. local_directory — путь к настоящему файлу кэша, видимый диагностическому MCP, а не путь вывода для LLM. Скопировать в **новую** папку на Рабочем столе:

```powershell
$qaResultDir = Join-Path ([Environment]::GetFolderPath('Desktop')) ('STE_test_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $qaResultDir -ErrorAction Stop | Out-Null
$qaGcodeSource = Join-Path $qaGcode.local_directory $qaGcode.artifact.file_name
$qaProjectSource = Join-Path $qaProject.local_directory $qaProject.artifact.file_name
Copy-Item -LiteralPath $qaGcodeSource -Destination (Join-Path $qaResultDir 'result.gcode') -ErrorAction Stop
Copy-Item -LiteralPath $qaProjectSource -Destination (Join-Path $qaResultDir 'project.3mf') -ErrorAction Stop
@{baseline=$qaBaseline; loaded=$qaLoaded; slice=$qaSliceResult; gcode=$qaGcode; project=$qaProject} | ConvertTo-Json -Depth 40 |
    Set-Content -LiteralPath (Join-Path $qaResultDir 'runtime.json') -Encoding UTF8
Get-ChildItem -LiteralPath $qaResultDir | Select-Object Name,Length
```

Теперь через UI откройте result.gcode в Preview: должны быть реальные траектории этой детали. Затем откройте project.3mf **как проект/workspace** и сравните машину, режим, материалы, настройки, модели, размеры/ориентацию. Изменившиеся object_id после открытия допустимы. Само существование ZIP/непустого файла не подтверждает правильность проекта. Сделайте скриншоты. Старый slice после открытия/изменения проекта может стать stale — не пытайтесь экспортировать его повторно.

## 10. Пользовательский профиль и перезапуск

Отдельный тест после сохранения файлов: на тестовой сцене запомните исходный профиль/слой.

```powershell
$qaOriginalProfiles = Invoke-QATool 'list_custom_profiles' @{}
$qaProfileLayerBefore = (Invoke-QATool 'get_setting' @{key='layer_height'; scope='global'}).value
$qaCustom = Invoke-QATool 'create_custom_profile' @{name=('AI_TEST_' + [guid]::NewGuid().ToString('N'))}
$qaCustom | ConvertTo-Json -Depth 25
$qaProfileLayer = if ($qaProfileLayerBefore -eq 0.12) { 0.1 } else { 0.12 }
Invoke-QATool 'set_setting' @{key='layer_height'; scope='global'; value=$qaProfileLayer} | ConvertTo-Json
Invoke-QATool 'save_custom_profile' @{profile_id=$qaCustom.profile_id} | ConvertTo-Json -Depth 25
```

Запишите profile_id, имя и слой. Disk_verified=true — подтверждение целевых файлов в этом процессе. Чтобы подтвердить restart: штатно закройте/откройте STE, снова Open chat, **Connect-QASlicer с новым портом**, затем:

```powershell
Invoke-QATool 'list_custom_profiles' @{} | ConvertTo-Json -Depth 25
Invoke-QATool 'select_custom_profile' @{profile_id=$qaCustom.profile_id} | ConvertTo-Json -Depth 25
Invoke-QATool 'get_setting' @{key='layer_height'; scope='global'} | ConvertTo-Json
```

Если окно PowerShell закрывали, заново загрузите helper и присвойте qaCustom/profile_id по записанному ID. Успех: тот же профиль найден, выбран, слой сохранился. В конце верните исходный профиль/настройки через UI и удалите временный профиль штатно; автоматического удаления инструментом нет.

## 11. Проверить оба5D

Для каждого режима отдельный прогон: сохранить текущую работу→пустая тестовая сцена→выбрать cylindrical либо cylindrical_full→загрузить подходящую выданную деталь→проверить её положение/параметры→повторить шаги8–9 с **новым slice_id**. Модель и режим должны соответствовать задаче организаторов. Произвольная3D-деталь не является доказательством корректного5D-проекта.

Требуются штатные движки/активация. При backend_state_unavailable, ошибке лицензии, parser/process failed сохраните ответ и сообщения STE, а не обходите ограничения. Full5D включает несколько стадий: промежуточный конец Cura/50% не означает готовый full G-code. Проверяйте конечный result, Preview и workspace после открытия.

## 12. Чат/вложения/скачивание — после прямых тестов

Только с выданным endpoint/моделью/ключом организаторов. Пользователь вводит их в AI Assistant→LLM Settings, ключ в чат/логи/скриншоты не добавляет. Agent пока не обращался к реальному провайдеру в наших автоматических проверках.

Проверять маленькими запросами:

1. «Только прочитай высоту слоя, ничего не меняй». Сверить get_setting и UI.
2. «Установи высоту слоя0.12мм и перечитай результат». Только если значение допустимо; затем вернуть исходное отдельным запросом.
3. Приложить PNG/JPG и маленький STL: «Загрузи приложенную модель и опиши фото. Нарезку пока не запускай». Проверить tool load_model_file, объект/размеры в STE, осмысленный ответ по фото. Upload/текст ответа не заменяют подтверждённый импорт.
4. Попросить нарезку уже подготовленного проекта вclassic; дождаться фактического статуса. Для **копии296** progress обновляется только после прочитанного tool-статуса, idle не означает конец engine. Можно явно попросить «Прочитай статус slice_id …».
5. После completed попросить экспорт обоих файлов. В копии296 после завершения каждого экспорта явно попросить «Прочитай get_export_status для export_id …». Только наблюдение агентом создаёт download_url/карточку; прямой MCP-вызов карточку чата не создаёт.
6. Скачать файлы, сравнить с прямыми native-экспортами/размерами, открыть Preview/reopen3MF. Обновить вкладку: история/вложения/карточки без дубликатов. Изменить проект после сохранения: старый URL должен отказать409, не запуская новый экспорт.

Разработчик делает роль2D, чтобы прогресс и готовность экспорта обновлялись без нового LLM-запроса. Она пока не входит в установленную вами копию296; проверим её отдельно после ревью. Не смешивайте ожидания двух версий.

## 13. Что прислать ведущему

Сначала достаточно результата первого3D-прогона: версия/машина, новый порт, имя и задача модели, на каком шаге остановились, JSON ошибки целиком без секретов, скриншот STE. После успеха — runtime.json, реальные result.gcode/project.3mf и скриншоты Preview/восстановленного проекта. Укажите отдельно, что проверено в3D/каждом5D/профилях после restart/чате с настоящим LLM.

При любом timeout не повторяйте import/start/export автоматически: операция может ещё работать. Сначала прочитайте статус того же ID и проверьте UI. Не нажимайте печать на физическом принтере ради приёмки плагина.
