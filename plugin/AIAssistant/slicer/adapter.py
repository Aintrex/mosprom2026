# -*- coding: utf-8 -*-
import os
import threading

try:
    from PyQt5.QtCore import QUrl
    from UM.Logger import Logger
    from steslicer.SteSlicerApplication import SteSlicerApplication
    from steslicer.Settings.ExtruderManager import ExtruderManager
    HAS_SLICER = True
except Exception:
    HAS_SLICER = False


class QtBridge(object):
    def __init__(self, timeout=10.0):
        self._timeout = timeout

    def call_on_qt_thread(self, func, *args, **kwargs):
        if not HAS_SLICER:
            return func(*args, **kwargs)

        app = SteSlicerApplication.getInstance()
        if app is None:
            return func(*args, **kwargs)

        done = threading.Event()
        box = {"result": None, "error": None}

        def _runner():
            try:
                box["result"] = func(*args, **kwargs)
            except Exception as e:
                box["error"] = e
            finally:
                done.set()

        app.callLater(_runner)
        if not done.wait(self._timeout):
            raise RuntimeError("mutation_timeout")
        if box["error"] is not None:
            raise box["error"]
        return box["result"]


class SlicerAdapter(object):
    def __init__(self, qt_bridge=None):
        self._qt = qt_bridge or QtBridge()
        self._mock_state = {
            "printer": "Stereotech Hybrid 530 V5.2.4",
            "mode": "classic",
            "quality_type": "normal",
            "extruder_0_material": "generic_petg",
            "global_settings": {
                "layer_height": 0.2,
                "wall_line_count": 3,
                "infill_sparse_density": 20,
                "support_enable": False,
                "speed_print": 50
            },
            "extruder_settings": {
                "0": {
                    "layer_height": 0.2,
                    "wall_line_count": 3,
                    "infill_sparse_density": 20,
                    "material_print_temp": 240,
                    "material_bed_temp": 75,
                    "speed_print": 50
                }
            },
            "models": []
        }

    def get_printer_state(self):
        return self._qt.call_on_qt_thread(self._get_printer_state_impl)

    def _get_printer_state_impl(self):
        if HAS_SLICER and SteSlicerApplication.getInstance() is not None:
            app = SteSlicerApplication.getInstance()
            stack = app.getGlobalContainerStack()
            mm = app.getMachineManager()
            mode = stack.getProperty("printing_mode", "value") if stack else "classic"
            lh = stack.getProperty("layer_height", "value") if stack else None
            return {
                "runtime": "steslicer",
                "printer": stack.getName() if stack else "Stereotech Hybrid 530 V5.2.4",
                "printing_mode": str(mode or "classic"),
                "active_quality_type": str(getattr(mm, "activeQualityType", "") or ""),
                "active_material_id": str(getattr(mm, "activeMaterialId", "") or ""),
                "layer_height": lh
            }
        return {
            "runtime": "mock",
            "printer": self._mock_state["printer"],
            "printing_mode": self._mock_state["mode"],
            "active_quality_type": self._mock_state["quality_type"],
            "active_material_id": self._mock_state["extruder_0_material"],
            "layer_height": self._mock_state["global_settings"].get("layer_height", 0.2),
            "settings": dict(self._mock_state["global_settings"]),
            "models": list(self._mock_state["models"])
        }

    def get_setting(self, scope, key, extruder_id="0"):
        return self._qt.call_on_qt_thread(self._get_setting_impl, scope, key, extruder_id)

    def _get_setting_impl(self, scope, key, extruder_id):
        if HAS_SLICER and SteSlicerApplication.getInstance() is not None:
            app = SteSlicerApplication.getInstance()
            if scope == "global":
                stack = app.getGlobalContainerStack()
            else:
                stack = ExtruderManager.getInstance().getActiveExtruderStack()
            if stack is None:
                raise RuntimeError("Контейнер настроек недоступен.")
            val = stack.getProperty(key, "value")
            if val is None:
                raise ValueError("Настройка не найдена: %s" % key)
            return {"scope": scope, "key": key, "value": val}

        if scope == "global":
            if key not in self._mock_state["global_settings"]:
                raise ValueError("Неизвестный ключ настройки: %s" % key)
            val = self._mock_state["global_settings"][key]
        else:
            ext = self._mock_state["extruder_settings"].get(str(extruder_id), {})
            if key not in ext:
                raise ValueError("Неизвестный ключ настройки экструдера: %s" % key)
            val = ext[key]
        return {"scope": scope, "key": key, "value": val, "extruder_id": str(extruder_id)}

    def set_setting(self, scope, key, value, extruder_id="0"):
        return self._qt.call_on_qt_thread(self._set_setting_impl, scope, key, value, extruder_id)

    def _set_setting_impl(self, scope, key, value, extruder_id):
        if key == "layer_height":
            val_f = float(value)
            if val_f < 0.04 or val_f > 0.6:
                raise ValueError("layer_height должен быть в диапазоне 0.04..0.6 мм.")
            value = round(val_f, 3)

        if HAS_SLICER and SteSlicerApplication.getInstance() is not None:
            app = SteSlicerApplication.getInstance()
            if scope == "global":
                stack = app.getGlobalContainerStack()
            else:
                stack = ExtruderManager.getInstance().getActiveExtruderStack()
            if stack is None:
                raise RuntimeError("Контейнер настроек недоступен.")
            stack.userChanges.setProperty(key, "value", value)
            actual = stack.getProperty(key, "value")
            return {"scope": scope, "key": key, "value": actual}

        if scope == "global":
            self._mock_state["global_settings"][key] = value
            self._mock_state["extruder_settings"]["0"][key] = value
        else:
            ext = self._mock_state["extruder_settings"].setdefault(str(extruder_id), {})
            ext[key] = value
            if str(extruder_id) == "0":
                self._mock_state["global_settings"][key] = value
        return {"scope": scope, "key": key, "value": value, "extruder_id": str(extruder_id)}

    def set_print_mode(self, mode):
        return self._qt.call_on_qt_thread(self._set_print_mode_impl, mode)

    def _set_print_mode_impl(self, mode):
        if HAS_SLICER and SteSlicerApplication.getInstance() is not None:
            app = SteSlicerApplication.getInstance()
            stack = app.getGlobalContainerStack()
            if stack is None:
                raise RuntimeError("GlobalContainerStack недоступен.")
            stack.userChanges.setProperty("printing_mode", "value", mode)
            actual = stack.getProperty("printing_mode", "value")
            return {"printing_mode": str(actual)}

        self._mock_state["mode"] = mode
        return {"printing_mode": mode}

    def list_quality_profiles(self):
        return {
            "profiles": [
                {"quality_type": "high", "name": "High (0.12 mm)", "layer_height": 0.12},
                {"quality_type": "fine", "name": "Fine (0.15 mm)", "layer_height": 0.15},
                {"quality_type": "normal", "name": "Normal (0.20 mm)", "layer_height": 0.20},
                {"quality_type": "draft", "name": "Draft (0.25 mm)", "layer_height": 0.25}
            ]
        }

    def select_profile(self, quality_type):
        self._mock_state["quality_type"] = quality_type
        lh_map = {"high": 0.12, "fine": 0.15, "normal": 0.20, "draft": 0.25}
        if quality_type in lh_map:
            self._mock_state["global_settings"]["layer_height"] = lh_map[quality_type]
        return {"active_quality_type": quality_type}

    def list_materials(self, extruder_id="0"):
        return {
            "extruder_id": str(extruder_id),
            "materials": [
                {"root_material_id": "generic_pla", "name": "PLA"},
                {"root_material_id": "generic_petg", "name": "PETG"},
                {"root_material_id": "generic_asa", "name": "ASA"},
                {"root_material_id": "stereotech_pa6_nylon", "name": "Stereotech PA6 / Nylon"},
                {"root_material_id": "stereotech_sealant_tpu", "name": "Stereotech Sealant TPU 95A"},
                {"root_material_id": "generic_pps_gf", "name": "PPS-GF"}
            ]
        }

    def select_material(self, extruder_id, root_material_id):
        self._mock_state["extruder_0_material"] = root_material_id
        return {"extruder_id": str(extruder_id), "active_material_id": root_material_id}

    def list_models(self):
        return {"models": list(self._mock_state["models"])}

    def arrange_models(self):
        return {"arranged": True, "models_count": len(self._mock_state["models"])}

    def load_model(self, path):
        if not os.path.isfile(path):
            raise ValueError("Файл модели не найден: %s" % path)
        item = {"id": "model_%d" % (len(self._mock_state["models"]) + 1), "name": os.path.basename(path), "path": path}
        self._mock_state["models"].append(item)
        return item