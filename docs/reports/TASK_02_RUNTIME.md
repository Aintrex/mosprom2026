# ТЗ 02: реальные проверки режимов

Дата: 23.10.2026. **Решение ведущего: ТЗ 02 принято.** Проверено в STE Slicer **7.10.12**, машина **Stereotech Hybrid 530 V5.2.4**. Адрес прогона: `http://127.0.0.1:56105/`; порт относится к этому запуску, не является константой плагина.

## Исходное состояние

- Установленные `agent/tools.py`, `slicer/adapter.py`, `slicer/qt_bridge.py`, `slicer/modes.py` совпадают с репозиторием по SHA-256.
- Реальные health, initialize и tools/list успешны. Доступны оба новых инструмента и прежние инструменты ТЗ 01.
- get_slicer_state подтвердил версию **7.10.12**, машину **Stereotech Hybrid 530 V5.2.4**, definition_id **hte530v5.2.4**, режим **classic**, backend **CuraEngineBackend**, matches_mode=true.
- Для classic/cylindrical/cylindrical_full диагностика вернула backend_available=true. Это подтверждает регистрацию, не завершённую проверку движка или лицензии.
- Исходный режим classic и полные ответы сохранены локально в игнорируемом `tmp/task02_runtime_state.json`.

## Выполненные шаги

| Шаг | before -> after | Реальный backend | Повторная диагностика | UI |
| --- | --- | --- | --- | --- |
| Повторный выбор 3D | classic -> classic | CuraEngineBackend | classic, matches_mode=true | Исходное состояние, отдельный новый скриншот не запрошен |
| Выбор 5D Spiral | classic -> cylindrical | CLIParserBackend | cylindrical, matches_mode=true | [Подтверждено скриншотом пользователя](evidence/task02/printing-mode-5d-spiral.png) |
| Выбор 5D | cylindrical -> cylindrical_full | CylindricalBackend | cylindrical_full, matches_mode=true | [Подтверждено скриншотом пользователя](evidence/task02/printing-mode-5d.png) |
| Восстановление 3D | cylindrical_full -> classic | CuraEngineBackend | classic, matches_mode=true | [Подтверждено скриншотом пользователя](evidence/task02/printing-mode-3d-restored.png); Layer Height также 0.1 мм |

Ответы всех выполненных set_print_mode: ok=true, before/after соответствуют таблице; последующие get_slicer_state подтвердили режим и ожидаемый backend. Регистрация и текущий backend совпали по проверке инструмента. Слайсинг, экспорт и физическая печать не запускались.

**Исходный 3D (classic) восстановлен и подтверждён API/UI, CuraEngineBackend и matches_mode=true подтверждены отдельной диагностикой.** На финальном скриншоте Layer Height и Classic Layer Height равны прежним 0.1 мм.

## Некорректные запросы

После восстановления отправлены `set_print_mode` с `mode=spherical` и с `mode=classic, extra=1`. Оба отклонены с JSON-RPC `error.code=-32602`. Финальная диагностика после этих запросов подтвердила прежнюю машину, classic и CuraEngineBackend, matches_mode=true; недопустимые запросы не изменили режим.

## Итог и границы приёмки

Диагностика, повторный выбор активного режима, все три режима, соответствующие реальные backend, UI, восстановление и два отрицательных запроса проверены. 45 автоматических проверок также повторены ведущим успешно. Блокирующих замечаний в рамках ТЗ 02 нет.

Слайсинг, экспорт, лицензия движка под нагрузкой и защита от смены режима во время нарезки этой приёмкой не подтверждаются. Приёмка ограничена пустой сценой и операциями текущего ТЗ. Профили качества и материалы не изменялись; их чтение и выбор относятся к следующему заданию.
