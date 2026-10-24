"""Native-shaped quality managers/provider, real temp files, fake Qt; not STE acceptance."""
import configparser
import copy
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.parse
from unittest import mock

import test_slicer_slicing as base
import test_slicer_profiles as profiles
from AIAssistant.agent.mcp import dispatch
from AIAssistant.slicer.adapter import SlicerAdapter
from AIAssistant.slicer.custom_profiles import CustomProfile


class Container(profiles.Container):
    def __init__(self, app, cid, values=None, **metadata):
        super().__init__(app, cid, copy.deepcopy(values), **metadata)
        self.path, self.dirty, self.serialize_calls = None, True, 0
        self.fail_serialize_at = None

    def getName(self):
        self.app.check_thread()
        return self.metadata.get("name", self.container_id)

    def getReadOnly(self):
        self.app.check_thread()
        return False

    def getPath(self):
        self.app.check_thread()
        return self.path

    def isDirty(self):
        self.app.check_thread()
        return self.dirty

    def clear(self):
        self.app.check_thread()
        self.values.clear()

    def serialize(self):
        self.app.check_thread()
        self.serialize_calls += 1
        if self.serialize_calls == self.fail_serialize_at:
            raise RuntimeError("private serialization detail")
        parser = configparser.ConfigParser(interpolation=None)
        parser["general"] = {"name": self.getName()}
        parser["metadata"] = {key: str(v) for key, v in self.metadata.items()}
        parser["values"] = {key: str(v) for key, v in self.values.items()}
        output = io.StringIO()
        parser.write(output)
        return output.getvalue()


class Stack(base.Stack):
    @property
    def userChanges(self):
        self.app.check_thread()
        return self._user

    @userChanges.setter
    def userChanges(self, value):
        self._user = value

    def __init__(self, app, name="global"):
        super().__init__(app, name)
        self.userChanges = Container(app, "user-" + name)
        self.qualityChanges = Container(app, "empty_quality_changes", type="quality_changes")

    def getTop(self):
        self.app.check_thread()
        return self.userChanges

    def getProperty(self, key, prop):
        if prop == "value" and key != "printing_mode":
            for container in (self.userChanges, self.qualityChanges, self.quality):
                if key in container.values:
                    return container.getProperty(key, prop)
        return super().getProperty(key, prop)

    def getContainers(self):
        self.app.check_thread()
        return [self.userChanges, self.qualityChanges, self.quality, self.material, self.bottom]


class LocalContainerProvider(object):
    def __init__(self, app, directory):
        self.app, self.directory = app, directory
        self._id_to_path, self._id_to_mime = {}, {}
        self.calls, self.silent, self.failure, self.before_save = [], False, False, None

    def saveContainer(self, container):
        self.app.check_thread()
        self.calls.append(container.getId())
        if self.before_save:
            self.before_save()
        if self.failure:
            raise OSError("private filesystem details")
        if self.silent:
            return
        try:
            data = container.serialize()
        except Exception:
            return  # Source native silent serialize failure.
        path = os.path.join(self.directory, urllib.parse.quote_plus(container.getId()) + ".inst.cfg")
        with open(path, "w", encoding="utf-8", newline="\r\n") as stream:
            stream.write(data)
        container.path = path
        self._id_to_path[container.getId()] = path
        self._id_to_mime[container.getId()] = "application/x-uranium-instancecontainer"


class Registry(object):
    def __init__(self, app, provider):
        self.app, self.provider = app, provider
        self.readonly, self.source_provider, self.containers = set(), {}, {}

    def getDefaultSaveProvider(self):
        self.app.check_thread()
        return self.provider

    def uniqueName(self, name):
        self.app.check_thread()
        return name + " #2" if any(c.getName().casefold() == name.casefold() for c in self.containers.values()) else name

    def isReadOnly(self, cid):
        self.app.check_thread()
        return cid in self.readonly

    def saveContainer(self, container, provider=None):
        self.app.check_thread()
        if not container.isDirty():
            return
        provider.saveContainer(container)
        self.source_provider[container.getId()] = provider


class QualityManager(object):
    def __init__(self, app):
        self.app, self.custom, self.creates = app, {}, []
        self.skip_create, self.after_create = None, None
        self.builtin = {"normal": self.group("builtin", "normal", custom=False)}

    def group(self, name, quality_type="normal", custom=True, values=None):
        containers = {}
        for p in ("global", "0", "1"):
            containers[p] = Container(self.app, ("qc-" if custom else "q-") + name + "-" + p,
                                      (values or {}).get(p), name=name, quality_type=quality_type,
                                      type="quality_changes" if custom else "quality")
        return types.SimpleNamespace(name=name, quality_type=quality_type, is_available=True,
            node_for_global=profiles.Node(self.app, containers["global"]),
            nodes_for_extruders={p: profiles.Node(self.app, containers[p]) for p in ("0", "1")})

    def getQualityGroups(self, stack):
        self.app.check_thread()
        return self.builtin.copy()

    def getQualityChangesGroups(self, stack):
        self.app.check_thread()
        return self.custom.copy()

    def createQualityChanges(self, name):
        self.app.check_thread()
        self.creates.append(name)
        values = {}
        for p, stack in self.app.stacks().items():
            if p == self.skip_create:
                continue
            values[p] = dict(stack.qualityChanges.values, **stack.userChanges.values)
            stack.userChanges.clear()
        group = self.group(name, values=values)
        if self.skip_create:
            group.nodes_for_extruders.pop(self.skip_create, None)
        for node in [group.node_for_global] + list(group.nodes_for_extruders.values()):
            self.app.registry.containers[node.getContainer().getId()] = node.getContainer()
        self.app.callLater(lambda: self.custom.update({name: group}))  # Index update is deferred.
        if self.after_create:
            self.after_create()


class MachineManager(object):
    def __init__(self, app):
        self.app, self.activeQualityChangesGroup = app, None
        self.activeQualityType, self.calls, self.ignore = "normal", [], False

    @property
    def activeMachine(self):
        self.app.check_thread()
        return self.app.stack

    def apply(self, group):
        self.app.check_thread()
        self.activeQualityChangesGroup = group
        self.activeQualityType = group.quality_type
        builtin = self.app.quality.builtin[group.quality_type]
        for p, stack in self.app.stacks().items():
            node = group.node_for_global if p == "global" else group.nodes_for_extruders[p]
            stack.qualityChanges = node.getContainer()
            stack.quality = (builtin.node_for_global if p == "global" else builtin.nodes_for_extruders[p]).getContainer()

    def setQualityChangesGroup(self, group, no_dialog=False):
        self.app.check_thread()
        if not no_dialog:
            raise AssertionError("Dialog forbidden")
        self.calls.append(group.name)
        if not self.ignore:
            self.app.callLater(lambda: self.apply(group))


class ContainerManager(object):
    def __init__(self, app):
        self.app, self.skip, self.calls = app, None, 0
        self._machine_manager = app.machine

    def updateQualityChanges(self):
        self.app.check_thread()
        self.calls += 1
        for p, stack in self.app.stacks().items():
            if self.app.registry.isReadOnly(stack.qualityChanges.getId()) or self.skip == p:
                continue  # Source returns True despite silent skips.
            stack.qualityChanges.values.update(stack.userChanges.values)
            stack.qualityChanges.dirty = True
            stack.userChanges.clear()
        return True


class CustomTests(unittest.TestCase):
    worker, finish = base.SlicingTests.worker, base.SlicingTests.finish
    pump_until_done, assert_error = base.SlicingTests.pump_until_done, base.SlicingTests.assert_error
    drain = base.SlicingTests.drain

    def setUp(self):
        base.SlicingTests.setUp(self)
        self.adapter._timeout = 0.3
        self.app.stack = Stack(self.app)
        self.app.stack.extruders = {p: Stack(self.app, "ext-" + p) for p in ("0", "1")}
        self.app.stacks = lambda: {"global": self.app.stack, **self.app.stack.extruders}
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.provider = LocalContainerProvider(self.app, self.directory.name)
        self.app.registry = Registry(self.app, self.provider)
        self.app.quality = QualityManager(self.app)
        self.app.machine = MachineManager(self.app)
        self.app.container_manager = ContainerManager(self.app)
        self.app.getContainerRegistry = lambda: self.app.registry
        self.app.getQualityManager = lambda: self.app.quality
        self.app.getMachineManager = lambda: self.app.machine
        native_manager = types.ModuleType("steslicer.Settings.ContainerManager")
        native_manager.ContainerManager = types.SimpleNamespace(getInstance=lambda: self.app.container_manager)
        module_patch = mock.patch.dict(sys.modules, {"steslicer": types.ModuleType("steslicer"),
            "steslicer.Settings": types.ModuleType("steslicer.Settings"), "steslicer.Settings.ContainerManager": native_manager})
        module_patch.start()
        self.addCleanup(module_patch.stop)
        builtin = self.app.quality.builtin["normal"]
        for p, stack in self.app.stacks().items():
            stack.quality = (builtin.node_for_global if p == "global" else builtin.nodes_for_extruders[p]).getContainer()

    def invoke(self, action, value=None):
        name = action + "_custom_profile"
        worker = self.worker(lambda: self.tools.call(name, {"name" if action == "create" else "profile_id": value}))
        return self.pump_until_done(worker)

    def create(self, name="Temporary profile"):
        result = self.invoke("create", name)
        self.assertTrue(result["ok"], result)
        self.profile_id = result["data"]["profile_id"]
        return result["data"]

    def test_create_preserves_old_custom_all_overrides_selects_and_persists(self):
        old = self.app.quality.group("Old", values={p: {"old_key": p, "layer_height": 0.1} for p in self.app.stacks()})
        self.app.quality.custom["Old"] = old
        self.app.machine.apply(old)
        source = {p: copy.deepcopy(s.qualityChanges.values) for p, s in self.app.stacks().items()}
        for p, stack in self.app.stacks().items():
            stack.userChanges.values.update({"layer_height": 0.24, "extra_key": p})
        result = self.create("Новый профиль")
        self.assertEqual(self.app.machine.calls, ["Новый профиль"])
        self.assertEqual(result["persistence"], {"memory_verified": True, "disk_verified": True, "restart_verified": False})
        self.assertTrue(result["user_overrides_cleared"])
        for p, stack in self.app.stacks().items():
            self.assertEqual(stack.userChanges.values, {})
            self.assertEqual(stack.qualityChanges.values, {"old_key": p, "layer_height": 0.24, "extra_key": p})
            previous = old.node_for_global if p == "global" else old.nodes_for_extruders[p]
            self.assertEqual(previous.getContainer().values, source[p])
            with open(stack.qualityChanges.path, encoding="utf-8") as stream:
                parser = configparser.ConfigParser(interpolation=None)
                parser.read_file(stream)
                self.assertEqual(parser["values"]["layer_height"], "0.24")
        self.assertNotIn(self.directory.name, json.dumps(result, ensure_ascii=False))
        listed = self.adapter.list_custom_profiles()["data"]["profiles"]
        self.assertTrue(next(p for p in listed if p["profile_id"] == self.profile_id)["active"])

    def test_save_repeated_updates_same_ids_and_select_keeps_overrides(self):
        first = self.create("One")
        second = self.create("Two")
        changed = self.pump_until_done(self.worker(lambda: self.tools.call("set_setting", {
            "key": "layer_height", "scope": "global", "value": 0.28})))
        self.assertTrue(changed["ok"], changed)
        self.assertEqual(changed["data"]["after"], 0.28)
        selected = self.invoke("select", first["profile_id"])
        self.assertTrue(selected["ok"], selected)
        self.assertEqual(self.app.stack.userChanges.values, {"layer_height": 0.28})
        count = len(self.app.registry.containers)
        result = self.invoke("save", first["profile_id"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.app.stack.qualityChanges.values["layer_height"], 0.28)
        self.assertEqual(result["data"]["container_ids"], first["container_ids"])
        self.assertTrue(self.invoke("save", first["profile_id"])["ok"])
        self.assertEqual(len(self.app.registry.containers), count)
        self.assert_error(self.invoke("save", second["profile_id"]), "custom_profile_not_active")

    def test_validation_collision_unknown_readonly_incomplete_and_busy_preflight(self):
        for name, args in (("create_custom_profile", {"name": "../x"}), ("create_custom_profile", {"name": "x\n"}),
                           ("save_custom_profile", {"profile_id": "x", "extra": 1}), ("list_custom_profiles", {"name": "x"})):
            result = dispatch({"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": name, "arguments": args}}, self.tools)
            self.assertEqual(result["error"]["code"], -32602)
        self.create()
        self.assert_error(self.invoke("create", "Temporary profile"), "custom_profile_name_conflict")
        self.assert_error(self.invoke("select", "unknown"), "custom_profile_not_available")
        self.app.registry.readonly.add(self.app.stack.qualityChanges.getId())
        self.assert_error(self.invoke("save", self.profile_id), "custom_profile_read_only")
        self.assertEqual(self.app.container_manager.calls, 0)
        self.app.registry.readonly.clear()
        self.app.machine.activeQualityChangesGroup.nodes_for_extruders.pop("1")
        self.assert_error(self.invoke("save", self.profile_id), "custom_profile_not_available")
        self.app.backend._slicing = True
        self.assert_error(self.invoke("create", "Busy"), "selection_busy")
        self.app.backend._slicing = False
        self.adapter._change_lock.acquire()
        try:
            self.assert_error(self.invoke("create", "Busy"), "selection_busy")
        finally:
            self.adapter._change_lock.release()

    def test_silent_update_skip_never_claims_memory_or_disk_success(self):
        self.create()
        self.app.stack.extruders["1"].userChanges.values["speed_print"] = 60
        self.app.container_manager.skip = "1"
        calls = len(self.provider.calls)
        result = self.invoke("save", self.profile_id)
        self.assert_error(result, "custom_profile_unconfirmed")
        self.assertEqual(self.app.container_manager.calls, 1)
        self.assertFalse(result["error"]["actual"]["memory_verified"])
        self.assertEqual(len(self.provider.calls), calls)

    def test_silent_provider_stale_file_serialize_and_io_failures_not_success(self):
        self.create()
        for failure in ("silent", "serialize", "io"):
            with self.subTest(failure=failure):
                self.app.stack.userChanges.values["layer_height"] = 0.31 + len(self.provider.calls)
                self.provider.silent, self.provider.failure = failure == "silent", failure == "io"
                target = self.app.stack.qualityChanges
                target.fail_serialize_at = target.serialize_calls + 2 if failure == "serialize" else None
                with mock.patch("AIAssistant.slicer.adapter.LOGGER"):
                    result = self.invoke("save", self.profile_id)
                self.assertFalse(result["ok"], result)
                self.assertFalse(result["error"]["actual"]["disk_verified"])
                self.assertNotIn(self.directory.name, json.dumps(result))
        self.provider.silent = self.provider.failure = False

    def test_partial_create_and_context_change_are_reported_without_rollback(self):
        self.app.quality.skip_create = "1"
        self.app.stack.extruders["1"].userChanges.values["speed_print"] = 60
        result = self.invoke("create", "Partial")
        self.assertFalse(result["ok"], result)
        self.assertIn("Partial", self.app.quality.custom)
        self.assertEqual(self.app.stack.extruders["1"].userChanges.values, {"speed_print": 60})
        self.app.quality.skip_create = None
        self.app.quality.after_create = lambda: self.app.stack.material.values.update({"changed": 1})
        self.assert_error(self.invoke("create", "Context"), "custom_profile_context_changed")
        self.assertEqual(len(self.app.quality.creates), 2)

    def test_disk_read_is_worker_and_context_change_after_read_rejects(self):
        self.create()
        original = CustomProfile.verify_files
        def verify(plan):
            self.assertIsNot(threading.current_thread(), self.app.main)
            original(plan)
            self.app.callLater(lambda: self.app.stack.userChanges.values.update({"external": 1}))
        with mock.patch.object(CustomProfile, "verify_files", staticmethod(verify)):
            result = self.invoke("save", self.profile_id)
        self.assert_error(result, "custom_profile_unconfirmed")
        self.assertFalse(result["error"]["actual"]["disk_verified"])

    def test_pending_timeout_does_not_create_and_running_save_retains_guard_until_exit(self):
        worker = self.worker(lambda: self.adapter.create_custom_profile("Pending"))
        late = self.app.callbacks.get(timeout=1)
        result = self.finish(worker)
        self.assert_error(result, "qt_timeout")
        late()
        self.assertEqual(self.app.quality.creates, [])
        self.assertFalse(self.adapter._change_lock.locked())
        self.create()
        self.app.stack.userChanges.values["layer_height"] = 0.25
        worker = self.worker(lambda: self.adapter.save_custom_profile(self.profile_id))
        before = len(self.provider.calls)
        def wait_for_timeout():
            self.assertTrue(worker[3].wait(2))
            self.assertTrue(self.adapter._change_lock.locked())
        self.provider.before_save = wait_for_timeout
        self.pump_until_done(worker)
        self.provider.before_save = None
        self.assert_error(self.finish(worker), "qt_timeout")
        self.assertFalse(self.adapter._change_lock.locked())
        self.assertEqual(len(self.provider.calls), before + 1, "No further saves after worker abandonment")

    def test_creation_invalidates_prior_real_slicing_record(self):
        base.SlicingTests.complete(self)
        old = self.slice_id
        self.create()
        self.assert_error(self.adapter.get_slicing_result(old), "slice_result_stale")

    def test_existing_native_colon_id_is_lookup_only_and_selectable(self):
        group = self.app.quality.group("Existing:UI")
        self.app.quality.custom[group.name] = group
        profile_id = group.node_for_global.getContainer().getId()
        self.assertIn(":", profile_id)
        listed = self.adapter.list_custom_profiles()["data"]["profiles"]
        self.assertEqual(listed[0]["profile_id"], profile_id)
        result = self.invoke("select", profile_id)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["profile_id"], profile_id)
        self.app.stack.userChanges.values["layer_height"] = 0.22
        self.assertTrue(self.invoke("save", profile_id)["ok"])
        self.assertIn("%3A", os.path.basename(self.app.stack.qualityChanges.path))
        self.assert_error(self.invoke("select", "C:/not-a-profile"), "custom_profile_not_available")

    def test_old_custom_to_builtin_during_create_never_triggers_our_select(self):
        group = self.app.quality.group("Old")
        self.app.quality.custom["Old"] = group
        self.app.machine.apply(group)
        self.app.quality.after_create = lambda: setattr(self.app.machine, "activeQualityChangesGroup", None)
        result = self.invoke("create", "Race")
        self.assert_error(result, "custom_profile_context_changed")
        self.assertEqual(self.app.machine.calls, [])
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(len(self.app.quality.creates), 1)

    start, status = base.SlicingTests.start, base.SlicingTests.status
