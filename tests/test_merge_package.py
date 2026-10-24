"""Integration branch: portable ZIP and direct-tool/chat exclusion, no STE/LLM."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import zipfile

import test_agent_integration as integration
from AIAssistant.agent.service import AgentService, APIError
from AIAssistant.agent.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("plugin_packager", str(ROOT / "scripts" / "package_plugin.py"))
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class MergePackageTests(unittest.TestCase):
    def test_real_plugin_archive_roundtrip_and_copy_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "plugin.zip"
            count = packager.package_plugin(packager.SOURCE, str(output))
            with zipfile.ZipFile(str(output)) as archive:
                self.assertEqual(len(archive.namelist()), count + 1)
                self.assertIn("INSTALL.txt", archive.namelist())
                self.assertIn("AIAssistant/plugin.json", archive.namelist())
                self.assertFalse(any("__pycache__" in n or n.endswith(".pyc") for n in archive.namelist()))
                destination = Path(temp) / "plugins"
                archive.extractall(str(destination))
            self.assertFalse((destination / "AIAssistant" / "AIAssistant").exists())
            for file in (destination / "AIAssistant").rglob("*"):
                if file.is_file():
                    original = Path(packager.SOURCE) / file.relative_to(destination / "AIAssistant")
                    self.assertEqual(hashlib.sha256(file.read_bytes()).digest(), hashlib.sha256(original.read_bytes()).digest())
            with self.assertRaises(FileExistsError):
                packager.package_plugin(packager.SOURCE, str(output))

    def test_package_rejects_incomplete_source_and_excludes_private_cache_files(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            output = Path(temp) / "plugin.zip"
            with self.assertRaises(ValueError):
                packager.package_plugin(str(source), str(output))
            for name in packager.REQUIRED:
                file = source / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("", encoding="utf8")
            (source / "plugin.json").write_text(json.dumps({"api": 5, "version": "test"}), encoding="utf8")
            (source / ".env").write_text("private", encoding="utf8")
            (source / "__pycache__").mkdir()
            (source / "__pycache__" / "private.py").write_text("private", encoding="utf8")
            packager.package_plugin(str(source), str(output))
            with zipfile.ZipFile(str(output)) as archive:
                self.assertEqual(set(archive.namelist()), {"AIAssistant/" + n for n in packager.REQUIRED} | {"INSTALL.txt"})

    def test_chat_cannot_start_while_direct_scene_write_is_in_flight(self):
        llm = integration.ScriptedLLM({"content": "Можно продолжить"})
        agent = AgentService(config_provider=lambda: dict(integration.CONFIG), llm_client=llm, run_async=False)
        self.addCleanup(agent.close)
        sid = agent.create_session()["session_id"]
        entered, release = threading.Event(), threading.Event()
        results = []
        def blocked(name, arguments):
            entered.set()
            self.assertTrue(release.wait(3))
            return {"ok": True, "data": {}}
        with mock.patch.object(agent.tools, "call", side_effect=blocked):
            worker = threading.Thread(target=lambda: results.append(agent.direct_tool("set_setting", {
                "key": "layer_height", "scope": "global", "value": 0.12})))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(agent.get_status()["operation_busy"])
                with self.assertRaises(APIError) as caught:
                    agent.submit_message(sid, {"text": "Измени настройку", "client_message_id": "blocked"})
                self.assertEqual((caught.exception.status, caught.exception.code), (409, "agent_busy"))
                self.assertEqual(llm.requests, [])
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(results[0]["ok"])
        self.assertFalse(agent.get_status()["operation_busy"])
        agent.submit_message(sid, {"text": "Объясни высоту слоя", "client_message_id": "allowed"})
        self.assertEqual(len(llm.requests), 1)

    def test_bad_key_shape_is_left_for_validation_instead_of_typeerror(self):
        for key in (["layer_height"], {"name": "layer_height"}):
            args = {"key": key, "scope": "global", "value": "0.12"}
            self.assertEqual(ToolRegistry.coerce_llm_arguments("set_setting", args), (args, []))


if __name__ == "__main__":
    unittest.main()
