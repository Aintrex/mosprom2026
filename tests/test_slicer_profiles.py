"""TASK 03: isolated runtime lookups, overrides, deferred application and MCP."""
import contextlib
import json
import queue
import sys
import threading
import time
import types
import unittest
from unittest import mock
from urllib.request import Request, urlopen

import test_slicer_modes as modes_tests
import test_slicer_settings as settings_tests
from AIAssistant.agent.mcp import dispatch
from AIAssistant.agent.service import AgentService
from AIAssistant.agent.tools import ToolRegistry
from AIAssistant.server import create_server
from AIAssistant.slicer.adapter import SlicerAdapter


class Container(object):
    def __init__(self, app, container_id, values=None, **metadata):
        self.app, self.container_id = app, container_id
        self.values = values or {}
        self.metadata = dict(metadata, id=container_id)

    def getId(self):
        self.app.check_thread()
        return self.container_id

    def getProperty(self, key, prop):
        self.app.check_thread()
        if prop == "unit":
            return "mm"
        return self.values.get(key)

    def getAllKeys(self):
        self.app.check_thread()
        return set(self.values)

    def getMetaDataEntry(self, key, default=None):
        self.app.check_thread()
        return self.metadata.get(key, default)

    def setProperty(self, key, prop, value):
        self.app.check_thread()
        self.values[key] = value
        if key == "printing_mode":
            self.app.stack.mode = value
            self.app.backend_events.put(lambda: self.app.apply_backend(value))


class Node(object):
    def __init__(self, app, container):
        self.app, self.container = app, container
        self.metadata = container.metadata.copy()

    def getContainer(self):
        self.app.check_thread()
        return self.container

    def getMetadata(self):
        self.app.check_thread()
        return self.metadata

    def getMetaDataEntry(self, key, default=None):
        self.app.check_thread()
        return self.metadata.get(key, default)


class Group(object):
    def __init__(self, app, quality_type, height, available=True):
        self.app = app
        self.type_value, self.available_value = quality_type, available
        self.global_node = Node(app, Container(app, "global-" + quality_type, {"layer_height": height}))
        self.extruder_nodes = {p: Node(app, Container(app, "ext%s-%s" % (p, quality_type))) for p in ("0", "1")}

    @property
    def quality_type(self):
        self.app.check_thread()
        return self.type_value

    @property
    def name(self):
        self.app.check_thread()
        return "Same visible name"

    @property
    def is_available(self):
        self.app.check_thread()
        return self.available_value

    @property
    def node_for_global(self):
        self.app.check_thread()
        return self.global_node

    @property
    def nodes_for_extruders(self):
        self.app.check_thread()
        return self.extruder_nodes


class Extruder(object):
    def __init__(self, app, position):
        self.app, self.position = app, position
        self.changes = Container(app, "user-" + position)
        self.material_value = None
        self.quality_value = None

    @property
    def userChanges(self):
        self.app.check_thread()
        return self.changes

    @property
    def material(self):
        self.app.check_thread()
        return self.material_value

    @property
    def quality(self):
        self.app.check_thread()
        return self.quality_value


class ProfileStack(modes_tests.ModeStack):
    def __init__(self, app):
        super().__init__(app)
        self.changes = Container(app, "user-global")
        self.extruder_values = {p: Extruder(app, p) for p in ("0", "1")}
        self.quality_value = None

    @property
    def extruders(self):
        self.app.check_thread()
        return self.extruder_values

    @property
    def userChanges(self):
        self.app.check_thread()
        return self.changes

    @property
    def quality(self):
        self.app.check_thread()
        return self.quality_value


class QualityManager(object):
    def __init__(self, app):
        self.app = app
        self.groups = {"q-original": Group(app, "q-original", 0.1),
                       "q-target": Group(app, "q-target", "0.24"),
                       "q-unavailable": Group(app, "q-unavailable", 0.3, False)}
        self.failure = None

    def getQualityGroups(self, stack):
        self.app.check_thread()
        if self.failure:
            raise self.failure
        return self.groups.copy()


class MaterialManager(object):
    def __init__(self, app):
        self.app = app
        self.maps = {}
        for position in ("0", "1"):
            self.maps[position] = {}
            for root in ("root-original", "root-target", "root-removed", "root-incompatible", "root-unknown"):
                metadata = {"base_file": root, "name": "Same material name", "brand": "Runtime brand", "material": "Runtime polymer"}
                if root != "root-unknown":
                    metadata["compatible"] = root != "root-incompatible"
                metadata["removed"] = root == "root-removed"
                self.maps[position][root] = Node(app, Container(app, "resolved-%s-%s" % (position, root), **metadata))
        self.calls = []

    def getAvailableMaterialsForMachineExtruder(self, stack, extruder):
        self.app.check_thread()
        self.calls.append((stack, extruder.position))
        return self.maps[extruder.position].copy()


class MachineManager(object):
    def __init__(self, app):
        self.app = app
        self.group = app.quality_manager.groups["q-original"]
        self.custom = None
        self.calls = []
        self.material_quality = None
        self.ignore = False
        self.delay = True
        self.failure = None
        self.write_wait = None

    @property
    def activeMachine(self):
        self.app.check_thread()
        return self.app.stack

    @property
    def activeQualityType(self):
        self.app.check_thread()
        return self.group.quality_type if self.group is not None else None

    @property
    def activeQualityGroup(self):
        self.app.check_thread()
        return self.group

    @property
    def activeQualityChangesGroup(self):
        self.app.check_thread()
        return self.custom

    @property
    def hasUserSettings(self):
        self.app.check_thread()
        return any(s.userChanges.values for s in [self.app.stack] + list(self.app.stack.extruders.values()))

    @property
    def currentRootMaterialId(self):
        self.app.check_thread()
        return {p: e.material.getMetaDataEntry("base_file") if e.material else None for p, e in self.app.stack.extruders.items()}

    def apply_group(self, group):
        self.app.check_thread()
        self.group = group
        self.app.stack.quality_value = group.node_for_global.getContainer()
        for position, node in group.nodes_for_extruders.items():
            self.app.stack.extruders[position].quality_value = node.getContainer()

    def _queue(self, callback):
        if self.write_wait:
            if not self.write_wait.wait(2):
                raise AssertionError("Worker did not time out")
        if self.failure:
            raise self.failure
        if not self.ignore:
            if self.delay:
                self.app.selection_events.put(callback)
            else:
                callback()

    def setQualityGroup(self, group, no_dialog=False):
        self.app.check_thread()
        self.calls.append(("quality", group.quality_type, no_dialog))
        if not no_dialog:
            raise AssertionError("Would show a dialog or clear overrides")
        self._queue(lambda: self.apply_group(group))

    def setMaterialById(self, position, root_material_id):
        self.app.check_thread()
        self.calls.append(("material", position, root_material_id))

        def apply():
            self.app.stack.extruders[position].material_value = self.app.material_manager.maps[position][root_material_id].getContainer()
            if self.material_quality:
                self.apply_group(self.app.quality_manager.groups[self.material_quality])
            # STE 7.10 MachineManager.setMaterial: prompt only in Custom mode, never a silent clear otherwise.
            if self.hasUserSettings and self.app.preferences.active_mode == 1:
                if self.app.preferences.policy == "always_discard":
                    for stack in [self.app.stack] + list(self.app.stack.extruders.values()):
                        stack.userChanges.values.clear()
                elif self.app.preferences.policy != "always_keep":
                    raise AssertionError("Would show the Keep/Discard dialog")
        self._queue(apply)

    # Private STE API used for the prompt-free native "keep" path.
    blurSettings = types.SimpleNamespace(emit=lambda: None)

    def _getContainerChangedSignals(self):
        return []

    def _setMaterial(self, position, node):
        self.app.check_thread()
        self.calls.append(("material_keep", position, node.getMetaDataEntry("base_file")))
        self.app.stack.extruders[position].material_value = node.getContainer()

    def _updateQualityWithMaterial(self):
        if self.material_quality:
            self.apply_group(self.app.quality_manager.groups[self.material_quality])


class Preferences(object):
    def __init__(self, app):
        self.app = app
        self.policy = "ask"
        self.active_mode = 1  # Custom settings mode, where STE would prompt.
        self.reads = []

    def getValue(self, key):
        self.app.check_thread()
        self.reads.append(key)
        if key == "steslicer/active_mode":
            return self.active_mode
        if key != "steslicer/choice_on_profile_override":
            raise AssertionError("Unexpected Preferences read")
        return self.policy

    def setValue(self, *args):
        raise AssertionError("Preferences must not change")


class ProfileApp(modes_tests.ModeApp):
    def __init__(self):
        super().__init__()
        self.stack = ProfileStack(self)
        self.definition = Container(self, "definition-runtime-id", {"layer_height": 0.15})
        self.empty_material_container = Container(self, "empty-runtime")
        self.quality_manager = QualityManager(self)
        self.material_manager = MaterialManager(self)
        self.machine_manager = MachineManager(self)
        self.preferences = Preferences(self)
        self.selection_events = queue.Queue()
        self.ui_extruder = "1"
        self.machine_manager.apply_group(self.machine_manager.group)
        for p, extruder in self.stack.extruders.items():
            extruder.material_value = self.material_manager.maps[p]["root-original"].getContainer()
        self.reads.clear()

    def getMachineManager(self):
        self.check_thread()
        return self.machine_manager

    def discardOrKeepProfileChangesClosed(self, option):
        self.check_thread()
        self.machine_manager.calls.append(("keep_closed", option))

    def getQualityManager(self):
        self.check_thread()
        return self.quality_manager

    def getMaterialManager(self):
        self.check_thread()
        return self.material_manager

    def getPreferences(self):
        self.check_thread()
        return self.preferences


class ProfileTests(unittest.TestCase):
    worker = settings_tests.SettingsTests.worker
    finish = settings_tests.SettingsTests.finish
    pump_until_done = modes_tests.ModeTests.pump_until_done
    assert_error = modes_tests.ModeTests.assert_error

    def setUp(self):
        self.app = ProfileApp()
        self.adapter = SlicerAdapter(self.app, timeout=0.2)
        self.tools = ToolRegistry(self.adapter)
        signal = types.ModuleType("UM.Signal")
        signal.postponeSignals = lambda *signals, **kwargs: contextlib.suppress()
        signal.CompressTechnique = types.SimpleNamespace(CompressPerParameterValue=1)
        patch = mock.patch.dict(sys.modules, {"UM.Signal": signal})
        patch.start()
        self.addCleanup(patch.stop)

    def rpc(self, name, arguments):
        return dispatch(modes_tests.ModeTests.request(name, arguments), self.tools)

    def start(self, operation):
        worker = self.worker(operation)
        self.app.callbacks.get(timeout=1)()
        return worker

    def applied(self, operation):
        worker = self.start(operation)
        self.app.selection_events.get(timeout=1)()
        return self.pump_until_done(worker)

    def quality(self):
        return self.adapter.select_profile("q-target")

    def material(self):
        return self.adapter.select_material("0", "root-target")

    def test_list_reads_only_main_thread_and_base_profile_height(self):
        self.app.stack.changes.values["layer_height"] = 0.42
        worker = self.worker(self.adapter.list_quality_profiles)
        callback = self.app.callbacks.get(timeout=1)
        self.assertEqual(self.app.reads, [])
        callback()
        result = self.finish(worker)
        self.assertEqual(result["data"]["current"], {"quality_type": "q-original", "name": "Same visible name", "custom_profile_id": None, "has_user_settings": True})
        profiles = {p["quality_type"]: p for p in result["data"]["profiles"]}
        self.assertEqual(profiles["q-original"]["layer_height_mm"], 0.1)
        self.assertEqual(profiles["q-target"]["layer_height_mm"], 0.24)
        self.assertFalse(profiles["q-unavailable"]["available"])
        self.assertTrue(all(t is self.app.main for t in self.app.reads))
        self.assertEqual(self.app.machine_manager.calls, [])
        json.dumps(result, allow_nan=False)

    def test_unknown_or_computed_height_is_null_without_evaluating_override(self):
        group = self.app.quality_manager.groups["q-target"]
        for value in (object(), "=layer_height", float("nan"), True):
            group.global_node.container.values["layer_height"] = value
            profile = next(p for p in self.adapter.list_quality_profiles()["data"]["profiles"] if p["quality_type"] == "q-target")
            self.assertIsNone(profile["layer_height_mm"])
        group.global_node.container.values.clear()
        profile = next(p for p in self.adapter.list_quality_profiles()["data"]["profiles"] if p["quality_type"] == "q-target")
        self.assertEqual(profile["layer_height_mm"], 0.15)

    def test_material_list_uses_position_filters_removed_and_preserves_ids(self):
        worker = self.start(lambda: self.adapter.list_materials("0"))
        result = self.finish(worker)
        data = result["data"]
        self.assertEqual(data["extruder_id"], "0")
        self.assertEqual(data["current"]["root_material_id"], "root-original")
        self.assertEqual(data["current"]["material_id"], "resolved-0-root-original")
        materials = {m["root_material_id"]: m for m in data["materials"]}
        self.assertNotIn("root-removed", materials)
        self.assertFalse(materials["root-incompatible"]["compatible"])
        self.assertIsNone(materials["root-unknown"]["compatible"])
        self.assertEqual(self.app.ui_extruder, "1")
        self.assertEqual(self.app.machine_manager.calls, [])
        self.assertEqual(self.app.material_manager.calls[-1][1], "0")
        json.dumps(result, allow_nan=False)

    def test_absent_material_and_optional_metadata(self):
        self.app.stack.extruders["0"].material_value = self.app.empty_material_container
        self.assertIsNone(self.adapter.list_materials("0")["data"]["current"])
        node = self.app.material_manager.maps["0"]["root-target"]
        for key in ("name", "brand", "material"):
            node.metadata.pop(key)
        item = next(m for m in self.adapter.list_materials("0")["data"]["materials"] if m["root_material_id"] == "root-target")
        self.assertIsNone(item["name"])
        self.assertIsNone(item["brand"])
        self.assertIsNone(item["material"])

    def test_missing_machine_managers_and_extruder_are_diagnosed(self):
        for manager in ("machine_manager", "quality_manager", "material_manager"):
            saved = getattr(self.app, manager)
            setattr(self.app, manager, None)
            operation = (lambda: self.adapter.list_materials("0")) if manager == "material_manager" else self.adapter.list_quality_profiles
            self.assert_error(operation(), "slicer_error")
            setattr(self.app, manager, saved)
        self.assert_error(self.adapter.list_materials("999"), "extruder_not_found")
        self.app.stack = None
        self.assert_error(self.adapter.list_quality_profiles(), "no_active_machine")
        self.assert_error(self.adapter.list_materials("0"), "no_active_machine")

    def test_validation_before_api_for_all_tools(self):
        cases = {"list_quality_profiles": [{"extra": 1}], "list_materials": [{}, {"extruder_id": 0}, {"extruder_id": ""}],
                 "select_profile": [{}, {"quality_type": None}, {"quality_type": ""}, {"quality_type": "q-target", "extra": 1}],
                 "select_material": [{"extruder_id": "0"}, {"extruder_id": True, "root_material_id": "root-target"},
                                     {"extruder_id": "0", "root_material_id": []}, {"extruder_id": "0", "root_material_id": ""}]}
        for name, cases_for_tool in cases.items():
            for arguments in cases_for_tool:
                with self.subTest(name=name, arguments=arguments):
                    self.assertEqual(self.rpc(name, arguments)["error"]["code"], -32602)
        self.assertEqual(self.app.reads, [])
        self.assertEqual(self.app.machine_manager.calls, [])

    def test_unavailable_ids_are_operational_errors_before_setter(self):
        for operation, code in ((lambda: self.adapter.select_profile("Same visible name"), "profile_not_available"),
                                (lambda: self.adapter.select_profile("q-unavailable"), "profile_not_available"),
                                (lambda: self.adapter.select_material("0", "resolved-0-root-target"), "material_not_available"),
                                (lambda: self.adapter.select_material("0", "root-removed"), "material_not_available"),
                                (lambda: self.adapter.select_material("0", "root-incompatible"), "material_not_available"),
                                (lambda: self.adapter.select_material("missing", "root-target"), "extruder_not_found")):
            self.assert_error(self.finish(self.start(operation)), code)
        self.assertEqual(self.app.machine_manager.calls, [])

    def test_missing_target_container_rejected_before_setter(self):
        self.app.material_manager.maps["0"]["root-target"].container = None
        self.assert_error(self.finish(self.start(self.material)), "material_not_available")
        self.app.quality_manager.groups["q-target"].global_node.container = None
        self.assert_error(self.finish(self.start(self.quality)), "profile_not_available")
        self.assertEqual(self.app.machine_manager.calls, [])

    def test_profile_select_waits_for_containers_and_preserves_overrides(self):
        self.app.stack.changes.values["layer_height"] = 0.42
        self.app.stack.extruders["1"].changes.values["speed_print"] = 17
        worker = self.start(self.quality)
        self.app.callbacks.get(timeout=1)()
        self.assertFalse(worker[3].is_set())
        self.app.selection_events.get(timeout=1)()
        result = self.pump_until_done(worker)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["before"]["quality_type"], "q-original")
        self.assertEqual(result["data"]["after"]["quality_type"], "q-target")
        self.assertEqual(self.app.stack.changes.values["layer_height"], 0.42)
        self.assertEqual(self.app.stack.extruders["1"].changes.values["speed_print"], 17)
        self.assertEqual(self.app.machine_manager.calls, [("quality", "q-target", True)])

    def test_quality_type_alone_does_not_confirm_wrong_container(self):
        worker = self.start(self.quality)
        self.app.machine_manager.group = self.app.quality_manager.groups["q-target"]
        self.assert_error(self.pump_until_done(worker), "selection_not_applied")

    def test_material_select_explicit_position_and_reports_changed_quality(self):
        self.app.machine_manager.material_quality = "q-target"
        other = self.app.stack.extruders["1"].material
        result = self.applied(self.material)
        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(data["extruder_id"], "0")
        self.assertEqual(data["before"]["root_material_id"], "root-original")
        self.assertEqual(data["after"]["root_material_id"], "root-target")
        self.assertEqual(data["after"]["material_id"], "resolved-0-root-target")
        self.assertEqual(data["quality_type_after"], "q-target")
        self.assertIs(self.app.stack.extruders["1"].material, other)
        self.assertEqual(self.app.ui_extruder, "1")

    def test_noop_avoids_setter_even_with_unsafe_material_policy(self):
        self.app.stack.changes.values["layer_height"] = 0.42
        for operation in (lambda: self.adapter.select_profile("q-original"),
                          lambda: self.adapter.select_material("0", "root-original")):
            result = self.finish(self.start(operation))
            self.assertTrue(result["ok"])
            self.assertEqual(result["data"]["before"], result["data"]["after"])
        self.assertEqual(self.app.machine_manager.calls, [])
        self.assertEqual(self.app.preferences.reads, [])

    def test_custom_profile_never_silently_reset_even_same_underlying_type(self):
        self.app.machine_manager.custom = Group(self.app, "custom-type", 0.1)
        custom = self.adapter.list_quality_profiles()["data"]["current"]["custom_profile_id"]
        self.assertEqual(custom, "global-custom-type")
        for operation in (self.quality, lambda: self.adapter.select_profile("q-original"), self.material):
            self.assert_error(self.finish(self.start(operation)), "custom_profile_active")
        self.assertEqual(self.app.machine_manager.calls, [])
        self.assertIsNotNone(self.app.machine_manager.custom)
        self.assertTrue(self.finish(self.start(lambda: self.adapter.select_material("0", "root-original")))["ok"])

    def test_material_with_overrides_keeps_them_without_prompt(self):
        # Observed in STE: the agent changes settings before materials; select_material must still work.
        for policy in ("ask", None, "always_keep"):
            with self.subTest(policy=policy):
                self.setUp()
                self.app.stack.changes.values["layer_height"] = 0.42
                self.app.stack.extruders["0"].changes.values["material_print_temperature"] = 230
                self.app.preferences.policy = policy
                result = self.pump_until_done(self.start(self.material))
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["data"]["after"]["root_material_id"], "root-target")
                self.assertEqual(result["data"]["kept_user_settings"], ["extruder 0: material_print_temperature", "global: layer_height"])
                self.assertTrue(result["data"]["warnings"])
                self.assertEqual(self.app.stack.changes.values, {"layer_height": 0.42})
                self.assertEqual(self.app.machine_manager.calls[-2:], [("material_keep", "0", "root-target"), ("keep_closed", "keep")])

    def test_material_custom_always_discard_refused_before_write(self):
        self.app.stack.changes.values["layer_height"] = 0.42
        self.app.preferences.policy = "always_discard"
        self.assert_error(self.finish(self.start(self.material)), "user_changes_present")
        self.assertEqual(self.app.machine_manager.calls, [])
        self.assertEqual(self.app.stack.changes.values, {"layer_height": 0.42})

    def test_always_keep_preserves_global_and_extruder_overrides(self):
        self.app.stack.changes.values["layer_height"] = 0.42
        self.app.stack.extruders["1"].changes.values["speed_print"] = 17
        self.app.preferences.policy = "always_keep"
        result = self.pump_until_done(self.start(self.material))  # Prompt-free keep path is synchronous.
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["has_user_settings"])
        self.assertEqual(self.app.stack.changes.values, {"layer_height": 0.42})
        self.assertEqual(self.app.stack.extruders["1"].changes.values, {"speed_print": 17})

    def test_ignored_setters_are_not_success(self):
        self.app.machine_manager.ignore = True
        for operation in (self.quality, self.material):
            result = self.pump_until_done(self.start(operation))
            self.assert_error(result, "selection_not_applied")
            self.assertIn("list_quality_profiles", result["error"]["message"])

    def test_lookup_is_refreshed_before_write_and_during_confirmation(self):
        self.adapter.list_quality_profiles()
        self.app.quality_manager.groups["q-target"].available_value = False
        self.assert_error(self.finish(self.start(self.quality)), "profile_not_available")
        self.assertEqual(self.app.machine_manager.calls, [])
        self.app.quality_manager.groups["q-target"].available_value = True
        worker = self.start(self.quality)
        self.app.selection_events.get(timeout=1)()
        self.app.quality_manager.groups["q-target"] = Group(self.app, "q-target", 0.24)
        self.app.quality_manager.groups["q-target"].global_node.container.container_id = "new-resolved-quality"
        self.assert_error(self.pump_until_done(worker), "selection_not_applied")

    def test_material_lookup_change_cannot_confirm_stale_container(self):
        worker = self.start(self.material)
        self.app.selection_events.get(timeout=1)()
        self.app.material_manager.maps["0"]["root-target"].metadata["id"] = "other-resolved-id"
        result = self.pump_until_done(worker)
        self.assertFalse(result["ok"])
        self.assertIn("list_materials", result["error"]["message"])

    def test_machine_and_extruder_replacement_prevent_confirmation(self):
        worker = self.start(self.quality)
        original = self.app.stack.extruders["0"]
        self.app.stack.extruder_values["0"] = Extruder(self.app, "0")
        self.assert_error(self.pump_until_done(worker), "selection_not_applied")
        self.app.stack.extruder_values["0"] = original
        worker = self.start(self.material)
        self.app.stack = ProfileStack(self.app)
        self.assert_error(self.pump_until_done(worker), "selection_not_applied")

    def test_override_loss_during_setter_is_reported_without_fake_rollback(self):
        self.app.stack.changes.values["layer_height"] = 0.42
        worker = self.start(self.quality)
        self.app.selection_events.get(timeout=1)()
        self.app.stack.changes.values.clear()
        self.assert_error(self.pump_until_done(worker), "selection_not_applied")
        self.assertEqual(self.app.stack.changes.values, {})

    def test_api_keyerror_is_operational_error_and_private_text_not_logged(self):
        self.app.machine_manager.failure = KeyError("private material path")
        worker = self.worker(lambda: self.rpc("select_material", {"extruder_id": "0", "root_material_id": "root-target"}))
        with self.assertLogs("AIAssistant.slicer.adapter", level="ERROR") as captured:
            self.app.callbacks.get(timeout=1)()
            response = self.finish(worker)
        self.assertTrue(response["result"]["isError"])
        self.assert_error(json.loads(response["result"]["content"][0]["text"]), "slicer_error")
        self.assertNotIn("private material path", "".join(captured.output))

    def test_timeout_cancels_pending_selection_and_reports_started_uncertainty(self):
        worker = self.worker(self.material)
        callback = self.app.callbacks.get(timeout=1)
        self.assert_error(self.finish(worker), "qt_timeout")
        callback()
        self.assertEqual(self.app.machine_manager.calls, [])
        worker = self.worker(self.quality)
        self.app.machine_manager.write_wait = worker[3]
        self.app.callbacks.get(timeout=1)()
        result = self.finish(worker)
        self.assert_error(result, "qt_timeout")
        self.assertIn("list_quality_profiles", result["error"]["message"])
        self.assertEqual(len(self.app.machine_manager.calls), 1)

    def test_shutdown_before_start_and_during_confirmation(self):
        worker = self.worker(self.quality)
        callback = self.app.callbacks.get(timeout=1)
        self.adapter.close()
        self.assert_error(self.finish(worker), "slicer_not_connected")
        callback()
        self.assertEqual(self.app.machine_manager.calls, [])
        self.assert_error(self.adapter.list_quality_profiles(), "slicer_not_connected")
        self.adapter = SlicerAdapter(self.app, timeout=0.2)
        worker = self.start(self.material)
        self.app.callbacks.get(timeout=1)()
        self.adapter.close()
        self.assert_error(self.finish(worker), "slicer_not_connected")

    def test_shared_busy_lock_between_quality_material_and_mode(self):
        worker = self.start(self.quality)
        self.assert_error(self.finish(self.worker(self.material)), "selection_busy")
        self.assert_error(self.finish(self.worker(lambda: self.adapter.set_print_mode("cylindrical"))), "switch_in_progress")
        self.app.selection_events.get(timeout=1)()
        self.assertTrue(self.pump_until_done(worker)["ok"])
        mode_worker = self.worker(lambda: self.adapter.set_print_mode("cylindrical"))
        self.app.callbacks.get(timeout=1)()
        self.assert_error(self.finish(self.worker(self.material)), "selection_busy")
        self.app.backend_events.get(timeout=1)()
        self.assertTrue(self.pump_until_done(mode_worker)["ok"])

    def test_direct_qt_selection_fails_before_setter(self):
        self.assert_error(self.quality(), "slicer_error")
        self.assert_error(self.material(), "slicer_error")
        self.assertEqual(self.app.machine_manager.calls, [])

    def test_dev_and_common_registry_schemas(self):
        tools = {t["name"]: t for t in self.tools.list_tools()}
        for name, fields in (("list_quality_profiles", ()), ("list_materials", ("extruder_id",)),
                             ("select_profile", ("quality_type",)), ("select_material", ("extruder_id", "root_material_id"))):
            schema = tools[name]["inputSchema"]
            self.assertEqual(set(schema["required"]), set(fields))
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(schema["properties"], {field: {"type": "string", "minLength": 1} for field in fields})
            response = dispatch(modes_tests.ModeTests.request(name, {field: "runtime-id" for field in fields}), AgentService().tools)
            self.assertTrue(response["result"]["isError"])
            self.assert_error(json.loads(response["result"]["content"][0]["text"]), "slicer_not_connected")

    def test_real_http_mcp_select_and_reread(self):
        server = create_server(AgentService(self.adapter))
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        try:
            def post(name, arguments):
                body = json.dumps(modes_tests.ModeTests.request(name, arguments)).encode("utf-8")
                request = Request("http://127.0.0.1:%d/mcp" % server.server_port, data=body, headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=3) as response:
                    return json.loads(response.read().decode("utf-8"))
            worker = self.start(lambda: post("select_material", {"extruder_id": "0", "root_material_id": "root-target"}))
            self.app.selection_events.get(timeout=1)()
            response = self.pump_until_done(worker)
            self.assertFalse(response["result"]["isError"])
            worker = self.start(lambda: post("list_materials", {"extruder_id": "0"}))
            response = self.finish(worker)
            current = json.loads(response["result"]["content"][0]["text"])["data"]["current"]
            self.assertEqual(current["material_id"], "resolved-0-root-target")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
