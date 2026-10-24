"""Ограниченные настройки текущих стеков; все функции API исполняются на Qt."""
import math

from .qt_bridge import SlicerError
from .settings_catalog import SETTING_KEYS, SETTING_TYPES, CATALOG_BY_KEY, CATEGORIES


BOUNDS = ("minimum_value", "maximum_value", "minimum_value_warning", "maximum_value_warning")
DESCRIBE_FILTERS = ("keys", "category", "query", "offset", "limit")


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def normalise(value, kind):
    if kind == "float" and finite(value):
        return float(value)
    if kind == "int" and finite(value) and int(value) == value:
        return int(value)
    if kind == "bool" and isinstance(value, bool):
        return value
    if kind == "enum" and isinstance(value, str) and value and not value.lstrip().startswith("="):
        return value
    raise ValueError("Значение не соответствует типу %s" % kind)


def validate_setting_arguments(arguments, write=False, describe=False):
    if not isinstance(arguments, dict):
        raise ValueError("Нужен объект аргументов")
    scope = arguments.get("scope")
    if scope not in ("global", "extruder"):
        raise ValueError("scope должен быть global либо extruder")
    fields = {"scope"} if describe else {"key", "scope"}
    if write:
        fields.add("value")
    if scope == "extruder":
        fields.add("extruder_id")
        position = arguments.get("extruder_id")
        if not isinstance(position, str) or not position:
            raise ValueError("extruder_id — непустая строковая позиция runtime-экструдера")
    allowed = fields | set(DESCRIBE_FILTERS) if describe else fields
    if not fields.issubset(arguments) or set(arguments) - allowed:
        raise ValueError("Обязательные поля: %s; допустимые поля: %s" % (", ".join(sorted(fields)), ", ".join(sorted(allowed))))
    if describe:
        if "keys" in arguments:
            keys = arguments["keys"]
            if (not isinstance(keys, list) or not 1 <= len(keys) <= 20 or
                    any(not isinstance(key, str) or key not in SETTING_TYPES for key in keys) or len(set(keys)) != len(keys)):
                raise ValueError("keys — от 1 до 20 уникальных разрешённых ключей")
        if "category" in arguments and (not isinstance(arguments["category"], str) or arguments["category"] not in CATEGORIES):
            raise ValueError("category должна быть одной из категорий каталога")
        if "query" in arguments and (not isinstance(arguments["query"], str) or len(arguments["query"]) > 80):
            raise ValueError("query — строка до 80 символов")
        for name, low, high in (("offset", 0, None), ("limit", 1, 30)):
            if name in arguments:
                value = arguments[name]
                if isinstance(value, bool) or not isinstance(value, int) or value < low or high is not None and value > high:
                    raise ValueError("offset — целое >=0; limit — целое от 1 до 30")
        return
    key = arguments["key"]
    if not isinstance(key, str) or key not in SETTING_TYPES:
        raise ValueError("Настройка не входит в разрешённый список")
    if write:
        normalise(arguments["value"], SETTING_TYPES[key])
        if key == "layer_height" and arguments["value"] <= 0:
            raise ValueError("layer_height должен быть больше нуля, мм")


def target(app, scope, extruder_id=None):
    global_stack = app.getGlobalContainerStack()
    if global_stack is None:
        raise SlicerError("no_active_machine", "В STE Slicer не выбрана активная машина")
    if scope == "global":
        return global_stack, global_stack
    if extruder_id not in global_stack.extruders:
        raise SlicerError("extruder_not_found", "Нет экструдера с указанной строковой позицией; UI-экструдер не подставляется")
    return global_stack, global_stack.extruders[extruder_id]


def _property(stack, key, name):
    return stack.getProperty(key, name) if stack.hasProperty(key, name) else None


def _kind(stack, key):
    if not stack.hasProperty(key, "value"):
        raise SlicerError("setting_unavailable", "Настройка %s отсутствует в выбранном стеке" % key)
    kind = _property(stack, key, "type")
    if kind != SETTING_TYPES[key]:
        raise SlicerError("setting_metadata_unavailable", "Runtime-тип %s отсутствует или противоречит проверенному allowlist" % key)
    return kind


def _value(stack, key, kind, options=None):
    try:
        value = normalise(stack.getProperty(key, "value"), kind)
    except ValueError:
        raise SlicerError("setting_unavailable", "STE Slicer не вернул значение подтверждённого типа для %s" % key)
    if kind == "enum" and (not isinstance(options, dict) or
                           not all(isinstance(k, str) and isinstance(v, str) for k, v in options.items()) or value not in options):
        raise SlicerError("setting_unavailable", "Текущее enum-значение не подтверждено runtime options")
    return value


def metadata(stack, key, scope):
    catalog = CATALOG_BY_KEY[key]
    item = {"key": key, "available": bool(stack.hasProperty(key, "value")), "type": None,
            "label": catalog["label"], "category": catalog["category"], "description": None,
            "text_source": {"label": "catalog", "category": "catalog", "description": None},
            "description_truncated": False, "label_truncated": False,
            "value": None, "unit": None, "enabled": None, "settable": None, "options": None,
            "settable_globally": None, "settable_per_extruder": None, "read_only": None,
            "limitations": []}
    item.update({name: None for name in BOUNDS})
    if not item["available"]:
        item["limitations"].append("Нет настройки в текущем стеке")
        return item
    label = _property(stack, key, "label")
    if isinstance(label, str) and label.strip():
        item["label"] = label[:200]
        item["label_truncated"] = len(label) > 200
        item["text_source"]["label"] = "definition_api"
    description = _property(stack, key, "description")
    if isinstance(description, str) and description.strip():
        item["description"] = description[:500]
        item["description_truncated"] = len(description) > 500
        item["text_source"]["description"] = "definition_api"
    kind = _property(stack, key, "type")
    item["type"] = kind if isinstance(kind, str) else None
    unit = _property(stack, key, "unit")
    item["unit"] = unit if isinstance(unit, str) else None
    if item["unit"] is None:
        item["limitations"].append("Единица измерения не предоставлена")
    for name in ("enabled", "settable_globally", "settable_per_extruder"):
        value = _property(stack, key, name)
        item[name] = value if isinstance(value, bool) else None
        if item[name] is None:
            item["limitations"].append("Не подтверждено свойство " + name)
    options = _property(stack, key, "options")
    if isinstance(options, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in options.items()):
        item["options"] = dict(options)
    for name in BOUNDS:
        value = _property(stack, key, name)
        if finite(value):
            item[name] = float(value)
        elif value is not None:
            item["limitations"].append("Не вычислена граница " + name)
    changes = stack.userChanges
    if changes is not None:
        readonly = changes.getReadOnly()
        item["read_only"] = readonly if isinstance(readonly, bool) else None
    permission = item["settable_globally" if scope == "global" else "settable_per_extruder"]
    if changes is None or permission is False or item["read_only"] is True:
        item["settable"] = False
    elif permission is True and item["read_only"] is False:
        item["settable"] = True
    if kind != SETTING_TYPES[key]:
        item["limitations"].append("Runtime-тип не подтверждён allowlist")
    else:
        try:
            item["value"] = _value(stack, key, kind, item["options"])
        except SlicerError as exc:
            item["limitations"].append(exc.message)
    return item


def describe_settings(app, scope, extruder_id=None, keys=None, category=None, query=None, offset=0, limit=20):
    # Catalog-only filtering precedes any setting metadata access on Qt.
    query_folded = query.strip().casefold() if query is not None else None
    selected = [key for key in SETTING_KEYS if (keys is None or key in keys) and
                (category is None or CATALOG_BY_KEY[key]["category"] == category) and
                (query_folded is None or query_folded and
                 (query_folded in key.casefold() or query_folded in CATALOG_BY_KEY[key]["label"].casefold()))]
    total = len(selected)
    page = selected[offset:offset + limit]
    global_stack, stack = target(app, scope, extruder_id)
    next_offset = offset + len(page) if offset + len(page) < total else None
    result = {"scope": scope, "settings": [metadata(stack, key, scope) for key in page],
              "total": total, "offset": offset, "limit": limit, "returned": len(page),
              "next_offset": next_offset, "truncated": next_offset is not None}
    if scope == "extruder":
        result["extruder_id"] = extruder_id
    return result


def _identity(key, scope, unit, extruder_id):
    result = {"key": key, "scope": scope, "unit": unit}
    if scope == "extruder":
        result["extruder_id"] = extruder_id
    return result


def get_setting(app, key, scope, extruder_id=None):
    global_stack, stack = target(app, scope, extruder_id)
    kind = _kind(stack, key)
    info = metadata(stack, key, scope)
    result = _identity(key, scope, info["unit"], extruder_id)
    result["value"] = _value(stack, key, kind, info["options"])
    return result


def set_setting(app, invalidate, key, scope, value, extruder_id=None):
    global_stack, stack = target(app, scope, extruder_id)
    kind = _kind(stack, key)
    info = metadata(stack, key, scope)
    if info["type"] != kind:
        raise SlicerError("setting_metadata_unavailable", "Тип настройки изменился при чтении метаданных")
    value = normalise(value, kind)
    if kind == "enum" and (not info["options"] or value not in info["options"]):
        raise SlicerError("setting_option_unavailable", "Укажите ключ из текущих runtime options")
    if info["enabled"] is False:
        raise SlicerError("setting_disabled", "Настройка отключена текущими зависимостями профиля")
    if info["read_only"] is True:
        raise SlicerError("setting_read_only", "Контейнер пользовательских изменений доступен только для чтения")
    if info["settable"] is False:
        raise SlicerError("setting_scope_unavailable", "Настройка не допускает запись в этой области/нет userChanges")
    if info["enabled"] is None or info["settable"] is None:
        raise SlicerError("setting_metadata_unavailable", "Enabled/разрешение области/readOnly не подтверждены API")
    warnings = list(info["limitations"])
    if kind in ("float", "int"):
        low, high = info["minimum_value"], info["maximum_value"]
        if any("Не вычислена граница " + name in warnings for name in BOUNDS[:2]) or (low is not None and high is not None and low > high):
            raise SlicerError("slicer_error", "Не удалось прочитать согласованные жёсткие границы настройки")
        if (low is not None and value < low) or (high is not None and value > high):
            raise SlicerError("setting_out_of_range", "Значение вне жёстких границ текущего профиля")
        for name, lower in (("minimum_value_warning", True), ("maximum_value_warning", False)):
            bound = info[name]
            if bound is not None and (value < bound if lower else value > bound):
                warnings.append("Выход за %s=%s; запись разрешена, проверьте профиль" % (name, bound))
    before = _value(stack, key, kind, info["options"])
    result = _identity(key, scope, info["unit"], extruder_id)
    changes = stack.userChanges
    if before != value:
        try:
            changes.setProperty(key, "value", value)
        finally:
            invalidate()  # Setter мог изменить стек даже при исключении/неподтверждении.
        current_global, current_stack = target(app, scope, extruder_id)
        if current_global is not global_stack or current_stack is not stack or stack.userChanges is not changes:
            raise SlicerError("setting_context_changed", "Во время записи сменились машина/целевой стек/userChanges")
        if _kind(stack, key) != kind:
            raise SlicerError("setting_metadata_unavailable", "Тип настройки изменился во время записи")
    options = _property(stack, key, "options") if kind == "enum" else info["options"]
    after = _value(stack, key, kind, options)
    matches = math.isclose(after, value, rel_tol=1e-9, abs_tol=1e-12) if kind == "float" else after == value
    if not matches:
        raise SlicerError("setting_not_applied", "STE Slicer не подтвердил значение; перечитайте настройку и проверьте UI, без автоповтора")
    result.update({"before": before, "after": after})
    if warnings:
        result["warnings"] = warnings
    return result
