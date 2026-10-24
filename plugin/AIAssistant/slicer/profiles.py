"""Профили/материалы: все функции вызываются только на Qt-потоке."""
import math

from .modes import _text
from .qt_bridge import SlicerError


TOOL_FIELDS = {"list_quality_profiles": (), "list_materials": ("extruder_id",),
               "select_profile": ("quality_type",),
               "select_material": ("extruder_id", "root_material_id")}


def validate_profile_arguments(name, arguments):
    fields = TOOL_FIELDS[name]
    if not isinstance(arguments, dict) or set(arguments) != set(fields):
        raise ValueError("Нужны только поля: %s" % (", ".join(fields) or "{}"))
    for field in fields:
        if not isinstance(arguments[field], str) or not arguments[field]:
            raise ValueError("%s должен быть непустой строкой ID" % field)


def _context(app):
    stack = app.getGlobalContainerStack()
    if stack is None:
        raise SlicerError("no_active_machine", "В STE Slicer не выбрана активная машина")
    manager = app.getMachineManager()
    if manager is None or manager.activeMachine is not stack:
        raise SlicerError("slicer_error", "Менеджер машины STE Slicer недоступен или ещё не синхронизирован")
    return stack, manager


def _manager(value):
    if value is None:
        raise SlicerError("slicer_error", "Менеджер профилей или материалов STE Slicer недоступен")
    return value


def _extruder(stack, position):
    if position not in stack.extruders:
        raise SlicerError("extruder_not_found", "Нет экструдера с указанной позицией; используйте ключ global_stack.extruders")
    return stack.extruders[position]


def _quality_current(manager):
    group = manager.activeQualityGroup
    custom = manager.activeQualityChangesGroup
    custom_id = None
    if custom is not None:
        nodes = [custom.node_for_global] + list(custom.nodes_for_extruders.values())
        custom_id = next((_text(node.getMetaDataEntry("id")) for node in nodes if node is not None), None)
    return {"quality_type": _text(manager.activeQualityType),
            "name": _text(custom.name if custom is not None else group.name if group is not None else None),
            "custom_profile_id": custom_id}


def _layer_height(stack, group):
    container = group.node_for_global.getContainer() if group.node_for_global is not None else None
    definition = stack.getDefinition()
    if definition is None or definition.getProperty("layer_height", "unit") != "mm":
        return None
    value = container.getProperty("layer_height", "value") if container is not None else None
    if value is None:
        value = definition.getProperty("layer_height", "value")
    # SettingFunction на текущем стеке может зависеть от override: не вычисляем
    # такую высоту как якобы базовую. Числовые строки контейнера допустимы.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (ValueError, OverflowError):
        return None


def list_quality_profiles(app):
    stack, manager = _context(app)
    groups = _manager(app.getQualityManager()).getQualityGroups(stack)
    current = _quality_current(manager)
    current["has_user_settings"] = bool(manager.hasUserSettings)
    return {"machine_id": _text(stack.getId()), "current": current,
            "profiles": [{"quality_type": _text(group.quality_type), "name": _text(group.name),
                          "available": bool(group.is_available), "layer_height_mm": _layer_height(stack, group)}
                         for key, group in sorted(groups.items())]}


def _compatible(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "yes", "false", "no"):
        return value.lower() in ("true", "yes")
    # Неизвестное значение не выдаём за подтверждённую совместимость.
    raise SlicerError("slicer_error", "STE Slicer вернул неизвестный формат совместимости материала")


def _material_nodes(app, stack, extruder):
    return _manager(app.getMaterialManager()).getAvailableMaterialsForMachineExtruder(stack, extruder) or {}


def _material_item(root, node):
    metadata = node.getMetadata()
    if bool(metadata.get("removed", False)):
        return None
    return {"root_material_id": _text(root), "material_id": _text(metadata.get("id")),
            "name": _text(metadata.get("name")), "brand": _text(metadata.get("brand")),
            "material": _text(metadata.get("material")), "compatible": _compatible(metadata.get("compatible"))}


def _materials(app, stack, extruder):
    result = {}
    for root, node in _material_nodes(app, stack, extruder).items():
        item = _material_item(root, node)
        if item is not None:
            result[root] = item
    return result


def _material_current(app, stack, manager, position):
    material = _extruder(stack, position).material
    if material is None or material is app.empty_material_container:
        return None
    root = material.getMetaDataEntry("base_file")
    if root is None:
        root = manager.currentRootMaterialId.get(position)
    return {"root_material_id": _text(root), "material_id": _text(material.getId()),
            "name": _text(material.getMetaDataEntry("name"))}


def list_materials(app, extruder_id):
    stack, manager = _context(app)
    extruder = _extruder(stack, extruder_id)
    materials = _materials(app, stack, extruder)
    return {"machine_id": _text(stack.getId()), "extruder_id": extruder_id,
            "current": _material_current(app, stack, manager, extruder_id),
            "materials": [materials[key] for key in sorted(materials)]}


def _overrides(stack):
    # Снимок остаётся на Qt-потоке, значения не попадают в JSON или журнал.
    containers = [(None, stack)] + list(stack.extruders.items())
    return {position: {key: item.userChanges.getProperty(key, "value")
                       for key in item.userChanges.getAllKeys()} for position, item in containers}


def _quality_targets(group):
    nodes = [(None, group.node_for_global)] + [(str(pos), node) for pos, node in group.nodes_for_extruders.items()]
    targets = {}
    for position, node in nodes:
        container = node.getContainer() if node is not None else None
        if container is None:
            raise SlicerError("profile_not_available", "Группа качества не содержит доступных контейнеров")
        targets[position] = _text(container.getId())
    return targets


def _quality_matches(stack, targets):
    for position, expected in targets.items():
        item = stack if position is None else _extruder(stack, position)
        if item.quality is None or item.quality.getId() != expected:
            return False
    return True


class _Selection(object):
    def _start(self, app):
        self.stack, self.manager = _context(app)
        self.extruders = dict(self.stack.extruders)
        self.overrides = _overrides(self.stack)

    def _check(self, app):
        stack, manager = _context(app)
        if stack is not self.stack or manager is not self.manager:
            raise SlicerError("selection_not_applied", "Во время выбора изменилась активная машина или менеджер")
        if set(stack.extruders) != set(self.extruders) or any(stack.extruders[p] is not e for p, e in self.extruders.items()):
            raise SlicerError("selection_not_applied", "Во время выбора изменились экструдеры активной машины")
        if _overrides(stack) != self.overrides:
            raise SlicerError("selection_not_applied", "Пользовательские overrides изменились во время выбора; проверьте состояние вручную")


class ProfileSelection(_Selection):
    def __init__(self, quality_type):
        self.quality_type = quality_type

    def _lookup(self, app):
        group = _manager(app.getQualityManager()).getQualityGroups(self.stack).get(self.quality_type)
        if group is None or not group.is_available or group.quality_type != self.quality_type:
            raise SlicerError("profile_not_available", "Встроенная группа качества с этим quality_type недоступна для текущей конфигурации")
        return group

    def _result(self):
        after = _quality_current(self.manager)
        if (after["quality_type"] != self.quality_type or self.manager.activeQualityChangesGroup is not None or
                not _quality_matches(self.stack, self.targets)):
            return None
        return {"before": self.before, "after": after, "has_user_settings": bool(self.manager.hasUserSettings)}

    def start(self, app):
        self._start(app)
        group = self._lookup(app)
        self.targets = _quality_targets(group)
        self.before = _quality_current(self.manager)
        if self.manager.activeQualityChangesGroup is not None:
            raise SlicerError("custom_profile_active", "Активен custom quality profile. Сначала решите его судьбу в STE Slicer; автоматически он не сбрасывается.")
        result = self._result()
        if result is not None:
            return result
        self.manager.setQualityGroup(group, no_dialog=True)
        return None

    def check(self, app):
        self._check(app)
        if _quality_targets(self._lookup(app)) != self.targets:
            raise SlicerError("selection_not_applied", "После начала выбора изменился lookup контейнеров качества")
        return self._result()


class MaterialSelection(_Selection):
    def __init__(self, extruder_id, root_material_id):
        self.position = extruder_id
        self.root = root_material_id

    def _lookup(self, app):
        node = _material_nodes(app, self.stack, _extruder(self.stack, self.position)).get(self.root)
        target = _material_item(self.root, node) if node is not None else None
        if target is None or not target["material_id"] or target["compatible"] is False:
            raise SlicerError("material_not_available", "Материал с этим root ID отсутствует или несовместим с текущим экструдером/соплом")
        container = node.getContainer()
        if container is None or container.getId() != target["material_id"]:
            raise SlicerError("material_not_available", "Не удалось загрузить ожидаемый контейнер материала")
        return target

    def _result(self, app):
        after = _material_current(app, self.stack, self.manager, self.position)
        if after is None or after["root_material_id"] != self.root or after["material_id"] != self.target["material_id"]:
            return None
        result = {"extruder_id": self.position, "before": self.before, "after": after,
                  "quality_type_after": _text(self.manager.activeQualityType), "has_user_settings": bool(self.manager.hasUserSettings)}
        kept = sorted(("global" if position is None else "extruder " + position) + ": " + key
                      for position, values in self.overrides.items() for key in values)
        if kept:
            result["kept_user_settings"] = kept
            result["warnings"] = ["Изменённые вами настройки сохранены (как «Keep» в STE). Проверьте, подходят ли они новому материалу, например температуры."]
        return result

    def start(self, app):
        self._start(app)
        _extruder(self.stack, self.position)
        self.target = self._lookup(app)
        self.before = _material_current(app, self.stack, self.manager, self.position)
        result = self._result(app)
        if result is not None:
            return result
        # Материал способен сбросить custom group при смене доступного quality_type.
        if self.manager.activeQualityChangesGroup is not None:
            raise SlicerError("custom_profile_active", "Материал может сбросить активный custom quality profile. Сначала решите его судьбу в STE Slicer.")
        if self.manager.hasUserSettings or any(self.overrides.values()):
            # Native setMaterial never clears userChanges itself; only in Custom mode it asks
            # Keep/Discard (SteSlicerApplication.discardOrKeepProfileChanges). We apply the native
            # "keep" without the modal prompt, and refuse only where STE would discard silently.
            prefs = app.getPreferences()
            custom = str(prefs.getValue("steslicer/active_mode")) == "1"
            if custom and prefs.getValue("steslicer/choice_on_profile_override") == "always_discard":
                raise SlicerError("user_changes_present", "В STE включено «всегда сбрасывать изменения»: смена материала удалила бы ваши настройки. Материал не менялся; выберите его в STE или смените эту политику.")
            self._keep_without_prompt(app)
            return None
        self.manager.setMaterialById(self.position, self.root)
        return None

    def _keep_without_prompt(self, app):
        manager = self.manager
        node = _material_nodes(app, self.stack, _extruder(self.stack, self.position)).get(self.root)
        if (node is None or not all(hasattr(manager, name) for name in
                                    ("_setMaterial", "_updateQualityWithMaterial", "_getContainerChangedSignals", "blurSettings")) or
                not hasattr(app, "discardOrKeepProfileChangesClosed")):
            raise SlicerError("backend_state_unavailable", "Не подтверждён штатный путь смены материала с сохранением настроек; материал не менялся")
        from UM.Signal import postponeSignals, CompressTechnique
        # Body of MachineManager.setMaterial (STE 7.10) minus the prompt, then the native "keep".
        manager.blurSettings.emit()
        with postponeSignals(*manager._getContainerChangedSignals(), compress=CompressTechnique.CompressPerParameterValue):
            manager._setMaterial(self.position, node)
            manager._updateQualityWithMaterial()
        app.discardOrKeepProfileChangesClosed("keep")

    def check(self, app):
        self._check(app)
        if self._lookup(app) != self.target:
            raise SlicerError("selection_not_applied", "После начала выбора изменился lookup материала для этого экструдера")
        return self._result(app)
