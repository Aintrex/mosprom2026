"""Four scoped custom-profile operations using native quality/container APIs."""
import copy
import os
import stat
import threading

from .profiles import _context, _quality_targets, _quality_matches
from .slicing import _context as scene_context, _stack_signature, _freeze, backend_busy
from .qt_bridge import SlicerError


CUSTOM_FIELDS = {"list_custom_profiles": (), "create_custom_profile": ("name",),
                 "select_custom_profile": ("profile_id",), "save_custom_profile": ("profile_id",)}
MAX_PROFILE_BYTES = 2 * 1024 * 1024


def validate_custom_arguments(name, arguments):
    fields = CUSTOM_FIELDS[name]
    if not isinstance(arguments, dict) or set(arguments) != set(fields):
        raise ValueError("Нужны только поля: " + ", ".join(fields))
    for field in fields:
        value = arguments[field]
        if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > (80 if field == "name" else 256):
            raise ValueError("Нужна ограниченная непустая строка " + field)
        if any(ord(c) < 32 or ord(c) == 127 or field == "name" and c in "/\\:" for c in value):
            raise ValueError("Недопустимые символы " + field)


def _stacks(stack):
    return {"global": stack, **{str(p): e for p, e in stack.extruders.items()}}


def _values(container):
    return {key: _freeze(container.getProperty(key, "value")) for key in container.getAllKeys()}


def _containers(group):
    result = {}
    if group.node_for_global is not None:
        result["global"] = group.node_for_global.getContainer()
    result.update({str(p): n.getContainer() for p, n in group.nodes_for_extruders.items() if n is not None})
    return result


def _groups(app, stack):
    return list(app.getQualityManager().getQualityChangesGroups(stack).values())


def _id(group):
    node = group.node_for_global
    return node.getMetaDataEntry("id") if node is not None else None


def _item(group, manager, stack):
    containers = _containers(group)
    complete = set(containers) == set(_stacks(stack)) and all(containers.values())
    return {"profile_id": _id(group), "name": group.name, "quality_type": group.quality_type,
            "available": bool(group.is_available and complete), "complete": bool(complete),
            "active": manager.activeQualityChangesGroup is not None and _id(manager.activeQualityChangesGroup) == _id(group),
            "container_ids": {p: c.getId() for p, c in containers.items() if c is not None}}


def list_custom_profiles(app):
    stack, manager = _context(app)
    return {"machine_id": stack.getId(), "profiles": [_item(g, manager, stack) for g in _groups(app, stack)]}


class CustomProfile(object):
    def __init__(self, action, value, invalidate):
        self.action, self.value, self.invalidate = action, value, invalidate
        self.stop = threading.Event()
        self.actual = {"phase": "not_started", "memory_verified": False, "disk_verified": False}

    def _note(self, **values):
        self.actual = dict(self.actual, **values)

    def _live(self):
        if self.stop.is_set():
            raise SlicerError("custom_profile_unconfirmed", "Подтверждение остановлено; native-изменения могли сохраниться. Перечитайте list_custom_profiles/UI без автоповтора")

    def _stable(self, app):
        stack, manager = _context(app)
        stacks = _stacks(stack)
        retained = []
        for p, s in sorted(stacks.items()):
            ignored = {s.userChanges.getId(), s.qualityChanges.getId(), s.quality.getId()}
            retained.append((p, id(s), tuple(c for c in _stack_signature(s) if c[0] not in ignored)))
        scene, all_stacks = scene_context(app)
        others = tuple((id(s), _stack_signature(s)) for s in all_stacks if all(s is not item for item in stacks.values()))
        return (scene[:8], id(manager), tuple(retained), others)

    def _check_context(self, app):
        self._live()
        if (self._stable(app) != self.stable or
                any(s.userChanges is not self.users[p] for p, s in self.stacks.items())):
            raise SlicerError("custom_profile_context_changed", "Изменилась машина, сцена, экструдеры, режим или постоянные контейнеры; проверьте UI")

    def _lookup(self, app, profile_id):
        groups = [g for g in _groups(app, self.stack) if _id(g) == profile_id]
        if len(groups) != 1 or not _item(groups[0], self.manager, self.stack)["available"]:
            raise SlicerError("custom_profile_not_available", "Нет одной полной доступной пользовательской группы с этим ID для текущей машины")
        group = groups[0]
        containers = _containers(group)
        if any(c.getMetaDataEntry("type") != "quality_changes" or c.getId() == "empty_quality_changes" for c in containers.values()):
            raise SlicerError("custom_profile_not_available", "ID не относится к полной пользовательской группе")
        return group, containers

    def _provider(self):
        try:
            provider = self.registry.getDefaultSaveProvider()
        except (NotImplementedError, AttributeError):
            raise SlicerError("custom_profile_storage_unavailable", "Штатный provider хранения не определён однозначно")
        if (type(provider).__name__ != "LocalContainerProvider" or
                not isinstance(provider._id_to_path, dict) or not isinstance(provider._id_to_mime, dict)):
            raise SlicerError("custom_profile_storage_unavailable", "Для этого provider не подтверждён протокол сохранения")
        return provider

    def _targets(self, app, group, containers):
        builtin = app.getQualityManager().getQualityGroups(self.stack).get(group.quality_type)
        if builtin is None or not builtin.is_available:
            raise SlicerError("custom_profile_not_available", "Базовое качество пользовательской группы недоступно")
        self.quality_targets = _quality_targets(builtin)
        self.targets = containers
        self.target_ids = {p: c.getId() for p, c in containers.items()}
        self.expected = {p: _values(c) for p, c in containers.items()}
        self.profile_id = _id(group)

    def start(self, app):
        self._live()
        self.stack, self.manager = _context(app)
        self.registry = app.getContainerRegistry()
        self.stacks = _stacks(self.stack)
        if backend_busy(app.getBackendManager().getCurrentBackend()):
            raise SlicerError("selection_busy", "Native backend занят; профиль не изменён")
        if any(s.userChanges is None or s.qualityChanges is None or s.quality is None or s.getTop() is not s.userChanges for s in self.stacks.values()):
            raise SlicerError("custom_profile_not_available", "Неполный стек качества/userChanges")
        self.stable = self._stable(app)
        self.users = {p: s.userChanges for p, s in self.stacks.items()}
        self.user = {p: _values(s.userChanges) for p, s in self.stacks.items()}
        self.expected_user = copy.deepcopy(self.user)
        self.source = {p: (s.qualityChanges, _values(s.qualityChanges)) for p, s in self.stacks.items()}
        self.before_profile = _id(self.manager.activeQualityChangesGroup) if self.manager.activeQualityChangesGroup is not None else None
        self._note(phase="preflight", profile_id=self.before_profile)
        if self.action in ("create", "save") and any(self.registry.isReadOnly(s.userChanges.getId()) for s in self.stacks.values()):
            raise SlicerError("custom_profile_read_only", "User overrides недоступны для штатного переноса")
        if self.action == "create":
            self.provider = self._provider()
            base = app.getQualityManager().getQualityGroups(self.stack).get(self.manager.activeQualityType)
            if base is None or not base.is_available or not _quality_matches(self.stack, _quality_targets(base)):
                raise SlicerError("custom_profile_not_available", "Активное базовое качество не подтверждено")
            if self.registry.uniqueName(self.value) != self.value or any(g.name.casefold() == self.value.casefold() for g in _groups(app, self.stack)):
                raise SlicerError("custom_profile_name_conflict", "Имя уже используется; выберите другое имя")
            self.old_ids = {_id(g) for g in _groups(app, self.stack)}
            self.expected = {p: dict(values, **self.user[p]) for p, (c, values) in self.source.items()}
            self.expected_user = {p: {} for p in self.stacks}
            self.base_type = self.manager.activeQualityType
            self.invalidate()
            self._note(phase="create_called")
            app.getQualityManager().createQualityChanges(self.value)
            self._note(user_override_counts={p: len(s.userChanges.getAllKeys()) for p, s in self.stacks.items()})
            self.phase = "created"
        else:
            group, containers = self._lookup(app, self.value)
            self._targets(app, group, containers)
            if self.action == "save":
                self.provider = self._provider()
                from steslicer.Settings.ContainerManager import ContainerManager
                self.container_manager = ContainerManager.getInstance()
                if self.container_manager._machine_manager is not self.manager:
                    raise SlicerError("custom_profile_context_changed", "ContainerManager не соответствует активной машине")
                if self.before_profile != self.value or any(s.qualityChanges is not containers[p] for p, s in self.stacks.items()):
                    raise SlicerError("custom_profile_not_active", "Сохранять можно только указанную активную группу")
                if any(self.registry.isReadOnly(c.getId()) for c in containers.values()):
                    raise SlicerError("custom_profile_read_only", "Один из контейнеров профиля read-only; запись не начата")
                self.expected = {p: dict(values, **self.user[p]) for p, values in self.expected.items()}
                self.expected_user = {p: {} for p in self.stacks}
                self.invalidate()
                self._note(phase="update_called", profile_id=self.profile_id)
                if self.container_manager.updateQualityChanges() is not True:
                    raise SlicerError("custom_profile_unconfirmed", "Штатный update не подтвердил вызов; проверьте фактическое состояние")
                self._note(user_override_counts={p: len(s.userChanges.getAllKeys()) for p, s in self.stacks.items()})
                self.phase = "selected"
            else:
                self.invalidate()
                self._note(phase="select_called", profile_id=self.profile_id)
                self.manager.setQualityChangesGroup(group, no_dialog=True)
                self.phase = "selected"
        return None

    def _memory(self, app):
        self._check_context(app)
        active = _id(self.manager.activeQualityChangesGroup) if self.manager.activeQualityChangesGroup is not None else None
        if active not in (self.before_profile, self.profile_id):
            raise SlicerError("custom_profile_context_changed", "Во время подтверждения выбран другой пользовательский профиль")
        if {p: _values(s.userChanges) for p, s in self.stacks.items()} != self.expected_user:
            raise SlicerError("custom_profile_unconfirmed", "User overrides не совпадают с ожидаемым переносом/сохранением")
        group, containers = self._lookup(app, self.profile_id)
        if ({p: c.getId() for p, c in containers.items()} != self.target_ids or
                {p: _values(c) for p, c in containers.items()} != self.expected):
            raise SlicerError("custom_profile_unconfirmed", "Содержимое/ID целевых контейнеров изменилось или перенос неполон")
        if (self.manager.activeQualityChangesGroup is None or _id(self.manager.activeQualityChangesGroup) != self.profile_id or
                not _quality_matches(self.stack, self.quality_targets) or
                any(s.qualityChanges is not containers[p] for p, s in self.stacks.items())):
            return None
        return group

    def check(self, app):
        self._check_context(app)
        if self.phase == "created":
            active = _id(self.manager.activeQualityChangesGroup) if self.manager.activeQualityChangesGroup is not None else None
            if self.manager.activeQualityType != self.base_type or active != self.before_profile:
                raise SlicerError("custom_profile_context_changed", "До выбора новой группы изменился активный профиль")
            if any(_values(c) != values for c, values in self.source.values()):
                raise SlicerError("custom_profile_unconfirmed", "Создание изменило исходный custom-профиль")
            groups = [g for g in _groups(app, self.stack) if _id(g) not in self.old_ids and g.name == self.value]
            if not groups or not _item(groups[0], self.manager, self.stack)["complete"]:
                return None
            if len(groups) != 1 or groups[0].quality_type != self.base_type:
                raise SlicerError("custom_profile_unconfirmed", "Новая группа не определена однозначно")
            expected = self.expected
            self._targets(app, groups[0], _containers(groups[0]))
            if self.expected != expected:
                raise SlicerError("custom_profile_unconfirmed", "Новая группа не сохранила все прежние значения")
            self._note(phase="select_created", profile_id=self.profile_id)
            self.manager.setQualityChangesGroup(groups[0], no_dialog=True)
            self.phase = "selected"
            return None
        group = self._memory(app)
        if group is None:
            return None
        self._note(phase="memory_confirmed", memory_verified=True, profile_id=self.profile_id)
        if self.action == "select":
            return {"result": self._result(group, disk=False)}
        plan = []
        for p, container in self.targets.items():
            self._live()
            data = container.serialize()
            if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_PROFILE_BYTES:
                raise SlicerError("custom_profile_storage_unavailable", "Сериализация профиля отсутствует или превышает2MiB")
            self._note(phase="persisting")
            self.registry.saveContainer(container, provider=self.provider)
            cid, path = container.getId(), container.getPath()
            if (self.registry.source_provider.get(cid) is not self.provider or
                    self.provider._id_to_path.get(cid) != path or self.provider._id_to_mime.get(cid) is None or
                    not isinstance(path, str) or not os.path.isabs(path)):
                raise SlicerError("custom_profile_storage_unconfirmed", "Provider не подтвердил регистрацию файла; сохранение не подтверждено")
            plan.append({"id": cid, "path": path, "serialized": data})
        return {"disk_plan": plan}

    @staticmethod
    def verify_files(plan):
        for item in plan:
            try:
                info = os.lstat(item["path"])
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROFILE_BYTES:
                    raise ValueError("Not a bounded regular file")
                with open(item["path"], "r", encoding="utf-8") as stream:
                    data = stream.read(MAX_PROFILE_BYTES + 1)
                if data != item["serialized"]:
                    raise ValueError("Native serialized snapshot differs")
            except (OSError, UnicodeError, ValueError):
                raise SlicerError("custom_profile_storage_unconfirmed", "Файл не совпадает со штатной сериализацией; сохранение на диске не подтверждено")

    def finish(self, app, plan):
        group = self._memory(app)
        if group is None or self.registry.getDefaultSaveProvider() is not self.provider:
            raise SlicerError("custom_profile_context_changed", "Профиль/provider изменился во время проверки хранения")
        for item in plan:
            container = next(c for c in self.targets.values() if c.getId() == item["id"])
            if container.serialize() != item["serialized"] or container.getPath() != item["path"]:
                raise SlicerError("custom_profile_context_changed", "Контейнер изменился во время проверки файла")
        self._note(phase="completed", disk_verified=True)
        return self._result(group, disk=True)

    def _result(self, group, disk):
        return {"profile_id": self.profile_id, "name": group.name, "quality_type": group.quality_type,
                "container_ids": dict(self.target_ids), "active": True,
                "user_overrides_cleared": self.action != "select",
                "transferred_keys": {p: sorted(v) for p, v in self.user.items()} if self.action != "select" else {},
                "persistence": {"memory_verified": True, "disk_verified": disk, "restart_verified": False}}
