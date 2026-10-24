# ТЗ12 — пользовательские профили качества

24.10.2026. Выполнено только `docs/tasks/TASK_12_CUSTOM_PROFILES.md` после принятого11. AGENTS/задание/исходники прочитаны, source-схема согласована с ведущим до реализации, git status проверен. Изменения прежних этапов/чужие документы/Word locks сохранены. Установка, commit/push/merge, реальныйLLM и принтер не выполнялись. Следующий этап/downloads не начат; результат остановлен для ревью.

## Изменения и контракт

В едином ToolRegistry четыре новых инструмента: `list_custom_profiles {}`, `create_custom_profile {name}`, `select_custom_profile {profile_id}`, `save_custom_profile {profile_id}`. Три мутации отмечены is_mutating; неподтверждённый исход останавливает агент. Builtin select_profile{quality_type}, старые инструменты/allowlist/три режима/2A/2B сохранены.

- List возвращает глобальный ID группы текущей машины, имя, quality_type, available/complete/active и container_ids. Нет произвольного поиска Registry по имени.
- Create отклоняет коллизию имени, вызывает штатный create, проверяет полную новую группу и сохранение **всех** прежних qualityChanges+userChanges global/extruder, неизменность старого custom; затем отдельно выбирает новую группу no_dialog и перечитывает контейнеры. Очистка userChanges отражена как подтверждённый перенос.
- Select принимает доступный ID и сохраняет user overrides. Save принимает только активную полную writable custom-группу; перед update проверяются все целевые контейнеры/userChanges, после — каждый перенесённый ключ/очистка. Повторный save не создаёт новых групп/ID. Встроенное качество не перезаписывается.
- Ответ: profile_id/name/quality_type/container_ids/active, user_overrides_cleared, transferred_keys (имена ключей), persistence:{memory_verified,disk_verified,restart_verified:false}. У select disk_verified=false означает отсутствие проверки файлов, не доказательство их отсутствия. В ошибке actual содержит последнюю подтверждённую фазу/ID/флаги/число оставшихся overrides. Сырые значения и пути не выдаются.

`custom_profiles.py` использует прежние QtBridge/change_lock, контекст сцены/машины/экструдеров/режима/контейнеров и инвалидирование slice. Новый профиль, чужие изменения или busy дают отказ. Частичный результат не удаляется/не откатывается автоматически; нужно list/UI без повторного Create. Qt-объекты не переносятся в filesystem worker. Для новых операций узкий latch удерживает guard при running-timeout до выхода callback; pending отменяется прежним Bridge, дальнейшие контейнеры после abandonment не сохраняются. Общий Bridge/старый выбор профилей не перерабатывались.

## Source-схема и подтверждение хранения

Читалась выданная копия `<SOURCE_ROOT>/STE-Slicer-master` и соседний Uranium-steslicer, без изменения/копирования дистрибутива и без runtime-зависимости от пути:

- QualityManager.getQualityChangesGroups/createQualityChanges/_createQualityChanges: Create копирует старые QC и user, очищает user и добавляет контейнеры, возвращаетNone, **не выбирает** новую группу. ProfilesPage.qml ждёт itemsChanged и активирует отдельно. Наблюдатель ждёт индекс/полноту и делает свой штатный setQualityChangesGroup(no_dialog=True) с readback.
- MachineManager.setQualityChangesGroup/_setQualityChangesGroup: подтверждаются global/extruder quality и qualityChanges IDs, availability и user overrides. ContainerManager используется через фактический **ContainerManager.getInstance()**, getter app.getContainerManager у выданной сборки отсутствует; singleton проверяется по своему MachineManager.
- ContainerManager.updateQualityChanges/_performMerge: True возможен после silent skip read-only/отсутствующего контейнера. Поэтому preflight и полный post-readback обязательны.
- ContainerRegistry.saveContainer, LocalContainerProvider.saveContainer и UM.SaveFile: void/dirty недостаточны; provider может молча пропустить serialize/MIME. Для Create/Save вызывается штатное сохранение **только целевых QC**, через известный LocalContainerProvider; глобальный saveDirtyContainers/Preferences не вызывается.

На Qt фиксируются native serialize, provider/source_provider/_id_to_path/_id_to_mime/container.getPath. Worker читает обычный файл UTF-8 до2MiB, с нормализацией переводов строк, и сравнивает целиком с native serialized snapshot. Затем Qt повторно проверяет контекст/ID/путь/сериализацию. Собственного INI/JSON-писателя нет. Это подтверждает файлы профиля **в этом процессе**, не сохранение активного выбора/всех userChanges-файлов/Preferences и не восстановление после рестарта.

Native LocalContainerProvider/SaveFile пишет синхронно на Qt и может долго ждать ОС (в том числе без своего короткого предела). Worker timeout не отменяет текущую native запись; guard остаётся до её фактического выхода. Собственная файловая проверка вынесена из Qt. Это ограничение source API, не заявление о неблокирующем native-save.

## Проверки

- `python -m unittest discover -s tests -p test_custom_profiles.py -v`: **11 тестов OK,2.192с**. Fake native managers/provider + реальные временные файлы: global/extruder старый custom+overrides, Unicode Create и отдельный deferred select; existing set_setting→select с сохранением overrides→save; повторный save без новыхID; collision/unknown/inactive/read-only/incomplete/busy; silent updateTrue, silent serialization/provider/stale-file/IO failure; partial без rollback; чужая смена контекста после worker-read; stale прежнего slice; pending timeout и удержание guard/запрет следующих saves при running-timeout.
- Два адресных замечания ведущего исправлены: **oldcustom→None builtin** во время индексации Create теперь сравнивается строго с before_profile и не вызывает наш select/save; existing **colon-ID** проходит list→select→save через lookup текущей группы. Native quote_plus используется provider-фикстурой для имени файла; произвольный `C:/...` ID даёт not_available и не превращается в путь. Ограничения имени нового профиля отделены от непрозрачного ID.
- Итоговый `python -m unittest discover -s tests -q`: **286 тестов OK,18.174с** —275 прежних сохранены плюс11. До замечаний полный284 также прошёл17.494с. Старые ожидания списка схем дополнены четырьмя tools, проверки не удалялись. Первые новые отказы были несовпадением формы fixture.userChanges и отсутствием Backend getter; lookup приведён к прежнему BackendManager, fake singleton — к реальному API, валидация не ослаблялась.
- `node --check plugin/AIAssistant/web/app.js`; `node tests/test_agent_web.js`: **8 offline DOM-сценариев OK**, UI не менялся.
- Грамматика Python3.5 для40 Python-файлов и plugin.json api5 — OK на отдельном Python; `git diff --check` чистый. Зависимости не добавлялись. Это не bundled Python3.5/Qt/provider из установленного STE.

Проверки fake provider не доказывают настоящую запись/восстановление настроек установленного STE. После итогового зелёного набора runtime не расширялся.

## Ручной прогон и ограничения

`plugin/README.md`, раздел ТЗ12, содержит самостоятельные PowerShell-команды: зафиксировать исходную группу, создать уникальный временный профиль, set_setting(layer_height), save/readback, переключить и вернуть группу, перезапустить STE и найти тот же ID/значения. Builtin select_profile по-прежнему не сбрасывает активный custom; возврат к builtin при приёмке выполняется штатным UI. После перезапуска нужны новый порт и повторная диагностика. Временный профиль пользователь удаляет через UI; новых rename/delete/import tools нет.

Команды подготовлены, **не выполнялись**; установленная копия не обновлена. Native restart, настоящие файлы provider и полное восстановление selected/profile/user settings остаются открыты. Поддержан известный LocalContainerProvider; неоднозначный default/другой provider, неполные группы или файлы>2MiB дают честный отказ. Частичные группы/записи могут остаться и требуют проверки UI, автоматического rollback/повтора нет. Прежняя native-приёмка ТЗ03/04/слайсинга/экспорта не объявляется выполненной. Downloads и следующий этап не начинались.
