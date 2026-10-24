"""Real upload/registry/Qt queue, fake STL reader/UM ops; no native acceptance."""
import copy
import json
import unittest

import test_model_transforms as transforms
import test_agent_integration as integration
import test_attachments as attachments
from AIAssistant.agent.service import AgentService
from AIAssistant.agent.mcp import dispatch


class TransformToolDiagnosticsTests(unittest.TestCase):
    worker, finish = transforms.TransformTests.worker, transforms.TransformTests.finish
    pump_until_done, drain = transforms.TransformTests.pump_until_done, transforms.TransformTests.drain
    state, start = transforms.TransformTests.state, transforms.TransformTests.start
    complete, status = transforms.TransformTests.complete, transforms.TransformTests.status

    def setUp(self):
        transforms.TransformTests.setUp(self)

    def test_upload_import_live_list_state_rotation_readback_other_objects_and_undo(self):
        llm = integration.ScriptedLLM()
        agent = AgentService(self.adapter, lambda: dict(integration.CONFIG), llm, run_async=False)
        self.addCleanup(lambda: (agent.close(), agent.file_store.wait_closed(2)))
        sid = agent.create_session()["session_id"]
        metadata = attachments.upload(agent.file_store)
        context = {}
        def imported(client, messages):
            result = json.loads(messages[-1]["content"])
            self.assertTrue(result["ok"])
            context["imported_id"] = result["data"]["objects"][0]["object_id"]
            return integration.calls(integration.tool("list_models", call_id="list-live"))
        def listed(client, messages):
            result = json.loads(messages[-1]["content"])
            live = [item["object_id"] for item in result["data"]["models"]]
            self.assertIn(context["imported_id"], live)
            context["live_id"] = context["imported_id"]
            return integration.calls(integration.tool("get_model_state", {"object_id": context["live_id"]}, "state-before"))
        def rotate(client, messages):
            context["before"] = json.loads(messages[-1]["content"])["data"]
            return integration.calls(integration.tool("rotate_model", {"object_id": context["live_id"], "axis": "z", "angle_degrees": 90}, "rotate"))
        def readback(client, messages):
            result = json.loads(messages[-1]["content"])
            self.assertTrue(result["ok"], result)
            return integration.calls(integration.tool("get_model_state", {"object_id": context["live_id"]}, "state-after"))
        def confirmed(client, messages):
            context["after"] = json.loads(messages[-1]["content"])["data"]
            self.assertNotEqual(context["before"]["world_transform"], context["after"]["world_transform"])
            return {"content": "Поворот подтверждён отдельным чтением матрицы"}
        llm.answers = [integration.calls(integration.tool("load_model_file", {"file_id": metadata["file_id"]})),
                       imported, listed, rotate, readback, confirmed]
        other_matrices = [copy.deepcopy(self.node.matrix), copy.deepcopy(self.other.matrix)]
        worker = self.worker(lambda: agent.submit_message(sid, {"text": "Загрузи STL и поверни его на90градусов вокругZ с подтверждением", "client_message_id": "rotate-upload", "attachment_ids": [metadata["file_id"]]}))
        self.app.callbacks.get(timeout=1)()
        self.assertEqual(len(self.app.loads), 1)
        self.assertFalse(worker[3].is_set(), "Reader return is not import completion")
        imported_node = transforms.Node(self.app)
        self.app.complete([imported_node])
        self.pump_until_done(worker)
        self.assertIn("after", context)
        self.assertEqual(len(self.ops.operations), 1)
        self.assertEqual([self.node.matrix, self.other.matrix], other_matrices)
        self.assertEqual(context["after"]["bounds"]["size"], {"x": 3.0, "y": 2.0, "z": 4.0})
        self.assertNotEqual(context["live_id"], self.object_id)
        self.ops.undo()
        self.assertEqual(imported_node.matrix, context["before"]["world_transform"])
        self.assertEqual(agent.file_store._users, 0)

    def test_validation_string_angle_not_repaired_then_corrected_number_runs_once(self):
        llm = integration.ScriptedLLM(integration.calls(integration.tool("rotate_model", {"object_id": self.object_id, "axis": "z", "angle_degrees": "90"})),
            integration.calls(integration.tool("rotate_model", {"object_id": self.object_id, "axis": "z", "angle_degrees": 90}, "corrected")),
            {"content": "Поворот подтверждён"})
        agent = AgentService(self.adapter, lambda: dict(integration.CONFIG), llm, run_async=False)
        self.addCleanup(agent.close)
        sid = agent.create_session()["session_id"]
        self.pump_until_done(self.worker(lambda: agent.submit_message(sid, {"text": "Поверни текущую деталь вокругZ на90градусов", "client_message_id": "angle"})))
        events = agent.get_session(sid)["tool_events"]
        results = [e["data"]["result"] for e in events if e["type"] == "tool_finished"]
        self.assertEqual(results[0]["error"]["stage"], "validation")
        self.assertFalse(results[0]["error"]["executed"])
        self.assertIn({"field": "angle_degrees", "expected_type": "number", "actual_type": "string"}, results[0]["error"]["field_errors"])
        self.assertNotIn("argument_admission", results[0])
        self.assertTrue(results[1]["ok"])
        self.assertEqual(len(self.ops.operations), 1)

    def test_preflight_physics_preview_busy_parent_plate_and_stale_id_never_push(self):
        cases = [(lambda: self.physics.update({"physics/automatic_drop_down": True}), lambda: self.physics.update({"physics/automatic_drop_down": False})),
                 (lambda: self.physics.update({"physics/automatic_push_free": None}), lambda: self.physics.update({"physics/automatic_push_free": False})),
                 (lambda: setattr(self.node, "preview", True), lambda: setattr(self.node, "preview", False)),
                 (lambda: setattr(self.device, "_writing", True), lambda: setattr(self.device, "_writing", False)),
                 (lambda: setattr(self.app.backend, "_slicing", True), lambda: setattr(self.app.backend, "_slicing", False)),
                 (lambda: setattr(self.node, "plate", 1), lambda: setattr(self.node, "plate", 0)),
                 (lambda: setattr(self.app.scene.root, "matrix", [[2 * float(i == j) if i < 3 else float(i == j) for j in range(4)] for i in range(4)]),
                  lambda: setattr(self.app.scene.root, "matrix", transforms.model_tests.identity()))]
        baseline = copy.deepcopy(self.node.matrix)
        for setup, restore in cases:
            setup()
            result = self.adapter.rotate_model(self.object_id, "z", 90)
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error"]["stage"], "preflight")
            self.assertIs(result["error"]["operation_started"], False)
            restore()
        self.assertEqual(self.node.matrix, baseline)
        self.assertEqual(self.ops.operations, [])
        result = self.adapter.rotate_model("stale-id", "z", 90)
        self.assertEqual((result["error"]["code"], result["error"]["stage"], result["error"]["operation_started"]), ("model_not_found", "preflight", False))
        self.assertEqual(self.physics, {"physics/automatic_drop_down": False, "physics/automatic_push_free": False})

    def test_dispatch_unconfirmed_and_timeout_are_not_safe_preflight_retry(self):
        self.ops.ignore = True
        result = self.adapter.rotate_model(self.object_id, "x", 90)
        self.assertEqual((result["error"]["code"], result["error"]["stage"], result["error"]["operation_started"]), ("model_transform_not_applied", "execution", True))
        self.ops.ignore = False
        worker = self.worker(lambda: self.adapter.rotate_model(self.object_id, "x", 90))
        late = self.app.callbacks.get(timeout=1)
        result = self.finish(worker)
        self.assertEqual(result["error"]["code"], "qt_timeout")
        self.assertEqual(result["error"]["stage"], "unknown")
        self.assertIsNone(result["error"]["operation_started"])
        late()
        self.assertEqual(self.ops.operations, [])

    def test_neighbor_tools_keep_numeric_validation_guards_and_slice_stale_after_undo(self):
        for name, args in (("move_model", {"object_id": self.object_id, "position_mm": {"x": "2", "y": 0, "z": 0}}),
                           ("scale_model", {"object_id": self.object_id, "factors": {"x": 1, "y": "2", "z": 1}}),
                           ("delete_model", {"object_id": 1}), ("arrange_models", {"angle_degrees": 90})):
            rpc = dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, self.tools)
            self.assertEqual(rpc["error"]["code"], -32602)
        self.complete()
        self.assertTrue(self.status()["valid"])
        self.physics["physics/automatic_drop_down"] = True
        for operation in (lambda: self.adapter.move_model(self.object_id, {"x": 2, "y": 3, "z": 4}),
                          lambda: self.adapter.scale_model(self.object_id, {"x": 2, "y": 1, "z": 1}),
                          lambda: self.adapter.delete_model(self.object_id)):
            self.assertEqual(operation()["error"]["stage"], "preflight")
        self.assertTrue(self.status()["valid"])
        self.physics["physics/automatic_drop_down"] = False
        self.assertTrue(self.adapter.rotate_model(self.object_id, "z", 90)["ok"])
        self.ops.undo()
        self.assertFalse(self.status()["valid"])


if __name__ == "__main__":
    unittest.main()
