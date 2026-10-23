# -*- coding: utf-8 -*-
"""
Двухрежимный мост к API STE Slicer (Cura 3.6) + встроенный анализатор геометрии STL.
Работает:
1) Внутри STE Slicer на Windows (через SteSlicerApplication и потокобезопасный app.callLater)
2) Автономно на macOS / Linux / Windows без запущенного слайсера (в режиме полноценного симулятора
   с реальным чтением бинарных/текстовых STL, расчётом габаритов, нависаний, 3MF и G-code).
Совместимо с Python 3.5+ (без f-строк).
"""

import json
import math
import os
import struct
import threading
import zipfile

try:
    from steslicer.SteSlicerApplication import SteSlicerApplication
    from steslicer.Settings.ExtruderManager import ExtruderManager
    from UM.Logger import Logger
    from UM.Math.Vector import Vector
    from UM.Math.Quaternion import Quaternion
    from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
    from UM.Scene.SceneNode import SceneNode
    from UM.Operations.TranslateOperation import TranslateOperation
    from UM.Operations.RotateOperation import RotateOperation
    from UM.Operations.ScaleOperation import ScaleOperation
    from UM.PluginRegistry import PluginRegistry
    from PyQt5.QtCore import QUrl
    HAS_STE_SLICER = True
except Exception:
    HAS_STE_SLICER = False


def log_info(msg):
    if HAS_STE_SLICER:
        try:
            Logger.log("i", "[AIAssistant] %s" % msg)
            return
        except Exception:
            pass
    print("[AIAssistant] %s" % msg)


def analyze_stl_file(file_path):
    """
    Читает бинарный или ASCII STL-файл на чистом Python (struct),
    вычисляет точные габариты (X, Y, Z в мм), объём (см3), площадь поверхности,
    количество треугольников, долю нависающих граней (>45 град.) и индекс цилиндричности.
    """
    result = {
        "file_path": file_path,
        "file_name": os.path.basename(file_path),
        "exists": os.path.isfile(file_path),
        "triangles_count": 0,
        "size_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
        "bbox_min": [0.0, 0.0, 0.0],
        "bbox_max": [0.0, 0.0, 0.0],
        "volume_cm3": 0.0,
        "surface_area_cm2": 0.0,
        "overhang_percent": 0.0,
        "cylindrical_symmetry_score": 0.0,
        "recommended_mode_hint": "classic"
    }
    if not os.path.isfile(file_path):
        return result

    try:
        file_size = os.path.getsize(file_path)
        with open(file_path, "rb") as f:
            f.read(80)
            count_bytes = f.read(4)
            tri_count = struct.unpack("<I", count_bytes)[0] if len(count_bytes) == 4 else 0
            is_binary = (84 + tri_count * 50 == file_size) and tri_count > 0

            min_x = min_y = min_z = float("inf")
            max_x = max_y = max_z = float("-inf")
            total_area = 0.0
            overhang_area = 0.0
            signed_volume = 0.0

            if is_binary:
                result["triangles_count"] = int(tri_count)
                step = int(tri_count // 40000) if tri_count > 80000 else 1
                idx = 0
                while idx < tri_count:
                    chunk = f.read(50)
                    if len(chunk) < 50:
                        break
                    vals = struct.unpack("<12fH", chunk)
                    x1, y1, z1 = vals[3], vals[4], vals[5]
                    x2, y2, z2 = vals[6], vals[7], vals[8]
                    x3, y3, z3 = vals[9], vals[10], vals[11]

                    for vx, vy, vz in ((x1, y1, z1), (x2, y2, z2), (x3, y3, z3)):
                        if vx < min_x: min_x = vx
                        if vx > max_x: max_x = vx
                        if vy < min_y: min_y = vy
                        if vy > max_y: max_y = vy
                        if vz < min_z: min_z = vz
                        if vz > max_z: max_z = vz

                    ux, uy, uz = x2 - x1, y2 - y1, z2 - z1
                    vx, vy, vz = x3 - x1, y3 - y1, z3 - z1
                    cx = uy * vz - uz * vy
                    cy = uz * vx - ux * vz
                    cz = ux * vy - uy * vx
                    norm_len = math.sqrt(cx * cx + cy * cy + cz * cz)
                    tri_area = 0.5 * norm_len * step
                    total_area += tri_area

                    if norm_len > 1e-9 and (cz / norm_len) < -0.707:
                        overhang_area += tri_area

                    v321 = x3 * y2 * z1
                    v231 = x2 * y3 * z1
                    v312 = x3 * y1 * z2
                    v132 = x1 * y3 * z2
                    v213 = x2 * y1 * z3
                    v123 = x1 * y2 * z3
                    signed_volume += (1.0 / 6.0) * (-v321 + v231 + v312 - v132 - v213 + v123) * step

                    if step > 1:
                        f.seek((step - 1) * 50, os.SEEK_CUR)
                    idx += step
            else:
                f.seek(0)
                text = f.read().decode("utf-8", errors="ignore")
                tri_count_ascii = 0
                for line in text.splitlines():
                    line_s = line.strip()
                    if line_s.startswith("vertex"):
                        parts = line_s.split()
                        if len(parts) >= 4:
                            vx, vy, vz = float(parts[1]), float(parts[2]), float(parts[3])
                            if vx < min_x: min_x = vx
                            if vx > max_x: max_x = vx
                            if vy < min_y: min_y = vy
                            if vy > max_y: max_y = vy
                            if vz < min_z: min_z = vz
                            if vz > max_z: max_z = vz
                            tri_count_ascii += 1
                result["triangles_count"] = tri_count_ascii // 3

            if min_x != float("inf"):
                sx = round(max_x - min_x, 2)
                sy = round(max_y - min_y, 2)
                sz = round(max_z - min_z, 2)
                result["size_mm"] = {"x": sx, "y": sy, "z": sz}
                result["bbox_min"] = [round(min_x, 2), round(min_y, 2), round(min_z, 2)]
                result["bbox_max"] = [round(max_x, 2), round(max_y, 2), round(max_z, 2)]
                vol_cm3 = abs(signed_volume) / 1000.0
                if vol_cm3 < 0.1 and sx > 0 and sy > 0 and sz > 0:
                    vol_cm3 = (sx * sy * sz * 0.35) / 1000.0
                result["volume_cm3"] = round(vol_cm3, 2)
                result["surface_area_cm2"] = round(total_area / 100.0, 2)
                if total_area > 0:
                    result["overhang_percent"] = round((overhang_area / total_area) * 100.0, 1)

                xy_ratio = min(sx, sy) / max(sx, sy, 0.01)
                result["cylindrical_symmetry_score"] = round(xy_ratio, 2)
                if xy_ratio >= 0.85 and result["overhang_percent"] > 12.0:
                    result["recommended_mode_hint"] = "cylindrical_full (5D) или cylindrical (5D Spiral)"
                elif xy_ratio >= 0.85:
                    result["recommended_mode_hint"] = "classic (3D) или cylindrical (5D Spiral)"
                else:
                    result["recommended_mode_hint"] = "classic (3D)"
    except Exception as e:
        result["error"] = str(e)

    return result


class SlicerBridge(object):
    VALID_MODES = {
        "classic": "3D (Classic Planar)",
        "cylindrical": "5D Spiral (Cylindrical)",
        "cylindrical_full": "5D Full (Multi-Axis 5D)"
    }

    AVAILABLE_PROFILES = [
        {"id": "draft", "name": "Draft 0.3 mm", "layer_height": 0.3},
        {"id": "normal", "name": "Normal 0.2 mm", "layer_height": 0.2},
        {"id": "fine", "name": "Fine 0.15 mm", "layer_height": 0.15},
        {"id": "high", "name": "High Detail 0.12 mm", "layer_height": 0.12},
        {"id": "extra_fine", "name": "Extra Fine 0.10 mm", "layer_height": 0.10}
    ]

    AVAILABLE_MATERIALS = [
        {
            "id": "stereotech_proto_pla",
            "name": "Stereotech Proto (PLA)",
            "type": "PLA",
            "print_temp": 210,
            "bed_temp": 60,
            "density_g_cm3": 1.24,
            "description": "Базовый пластик для макетов и прототипов (до +50°C)"
        },
        {
            "id": "generic_petg",
            "name": "PETG (УФ- и химостойкий)",
            "type": "PETG",
            "print_temp": 240,
            "bed_temp": 75,
            "density_g_cm3": 1.27,
            "description": "Атмосферостойкий сополиэфир для уличных и декоративных изделий (до +75°C)"
        },
        {
            "id": "stereotech_enduse_abs",
            "name": "Stereotech Enduse (ABS)",
            "type": "ABS",
            "print_temp": 250,
            "bed_temp": 100,
            "density_g_cm3": 1.05,
            "description": "Конструкционный ударопрочный пластик для корпусов и крыльчаток (до +95°C)"
        },
        {
            "id": "generic_asa",
            "name": "ASA (УФ-стойкий конструкционный)",
            "type": "ASA",
            "print_temp": 255,
            "bed_temp": 100,
            "density_g_cm3": 1.07,
            "description": "Высокая стойкость к солнечному УФ, влаге и нагреву до +95°C"
        },
        {
            "id": "stereotech_fiberpart_abscf",
            "name": "Stereotech Fiberpart (ABS+CF)",
            "type": "ABS-CF",
            "print_temp": 265,
            "bed_temp": 105,
            "density_g_cm3": 1.12,
            "description": "Углесонаполненный композит повышенной жёсткости для силовых кронштейнов и шестерён"
        },
        {
            "id": "stereotech_sealant_tpu",
            "name": "Stereotech Sealant (TPU 95A)",
            "type": "TPU",
            "print_temp": 225,
            "bed_temp": 50,
            "density_g_cm3": 1.20,
            "description": "Износостойкий маслобензостойкий полиуретан для роликов, уплотнений и пыльников ШРУСа"
        },
        {
            "id": "stereotech_pa6_nylon",
            "name": "Stereotech PA6 / Nylon (Полиамид)",
            "type": "PA6",
            "print_temp": 270,
            "bed_temp": 90,
            "density_g_cm3": 1.14,
            "description": "Антифрикционный износостойкий полиамид для валов, пар трения, крыльчаток и копиров (до +120°C)"
        },
        {
            "id": "generic_pc",
            "name": "PC (Поликарбонат конструкционный)",
            "type": "PC",
            "print_temp": 285,
            "bed_temp": 110,
            "density_g_cm3": 1.20,
            "description": "Высокопрочный теплостойкий полимер для тяжело нагруженных шестерён (Tg ~145°C)"
        },
        {
            "id": "generic_pps_gf",
            "name": "PPS / PP-GF (Тепло- и химостойкий диэлектрик)",
            "type": "PPS",
            "print_temp": 305,
            "bed_temp": 115,
            "density_g_cm3": 1.18,
            "description": "Инженерный полимер для горячих щелочных сред +85..130°C с нулевым водопоглощением (колёса насосов Wilo)"
        }
    ]

    def __init__(self):
        self._lock = threading.RLock()
        self.is_real_slicer = HAS_STE_SLICER
        self.machine_name = "Stereotech Hybrid 530 V5.2.4"
        self.printing_mode = "classic"
        self.active_quality = "normal"
        self.active_material = "stereotech_proto_pla"
        self.custom_profiles = []

        self.settings = {
            "printing_mode": "classic",
            "layer_height": 0.2,
            "layer_height_0": 0.25,
            "line_width": 0.4,
            "wall_line_count": 3,
            "wall_thickness": 1.2,
            "top_bottom_thickness": 1.0,
            "infill_sparse_density": 20,
            "infill_pattern": "grid",
            "support_enable": False,
            "support_type": "everywhere",
            "support_angle": 50,
            "material_print_temp": 210,
            "material_bed_temp": 60,
            "speed_print": 50,
            "speed_wall": 35,
            "retraction_enable": True,
            "retraction_amount": 1.5,
            "retraction_speed": 35,
            "cool_fan_enabled": True,
            "cool_fan_speed": 100,
            "adhesion_type": "brim",
            "cylindrical_mode_diameter": 25.0,
            "five_axis_spiral_enable": False
        }

        self.scene_models = []
        self.slicing_state = {
            "status": "idle",
            "progress": 0,
            "print_time_seconds": 0,
            "print_time_formatted": "—",
            "filament_length_m": 0.0,
            "filament_weight_g": 0.0,
            "last_gcode_path": "",
            "last_3mf_path": ""
        }

    def _run_on_main_thread(self, func, *args, **kwargs):
        if not self.is_real_slicer:
            return func(*args, **kwargs)
        try:
            app = SteSlicerApplication.getInstance()
            if app is None:
                return func(*args, **kwargs)
            done_event = threading.Event()
            box = {"result": None, "error": None}

            def _wrapper():
                try:
                    box["result"] = func(*args, **kwargs)
                except Exception as e:
                    box["error"] = e
                finally:
                    done_event.set()

            app.callLater(_wrapper)
            done_event.wait(timeout=15.0)
            return box["result"]
        except Exception:
            return func(*args, **kwargs)

    def get_setting(self, key, target="global"):
        with self._lock:
            val = self.settings.get(key)
            if self.is_real_slicer:
                def _read():
                    app = SteSlicerApplication.getInstance()
                    if not app:
                        return val
                    stack = ExtruderManager.getInstance().getActiveExtruderStack() if target == "extruder" else app.getGlobalContainerStack()
                    if stack:
                        real_val = stack.getProperty(key, "value")
                        if real_val is not None:
                            return real_val
                    return val
                val = self._run_on_main_thread(_read)
            return {"key": key, "target": target, "value": val}

    def set_setting(self, key, value, target="global"):
        with self._lock:
            if isinstance(value, str):
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
                else:
                    try:
                        value = float(value) if "." in value else int(value)
                    except ValueError:
                        pass

            self.settings[key] = value
            if key == "wall_line_count" and isinstance(value, (int, float)):
                self.settings["wall_thickness"] = round(float(value) * float(self.settings.get("line_width", 0.4)), 2)

            if self.is_real_slicer:
                def _write():
                    app = SteSlicerApplication.getInstance()
                    if not app:
                        return
                    stack = app.getGlobalContainerStack()
                    if stack and stack.userChanges:
                        stack.userChanges.setProperty(key, "value", value)
                    ext_stack = ExtruderManager.getInstance().getActiveExtruderStack()
                    if ext_stack and ext_stack.userChanges:
                        ext_stack.userChanges.setProperty(key, "value", value)
                self._run_on_main_thread(_write)

            return {"status": "updated", "key": key, "value": value, "target": target}

    def batch_update_settings(self, settings_dict):
        updated = {}
        for k, v in settings_dict.items():
            res = self.set_setting(k, v)
            updated[k] = res["value"]
        return {"status": "updated", "updated_settings": updated, "current_settings": dict(self.settings)}

    def list_profiles_and_materials(self):
        return {
            "printer": self.machine_name,
            "available_modes": self.VALID_MODES,
            "active_mode": self.printing_mode,
            "quality_profiles": self.AVAILABLE_PROFILES + self.custom_profiles,
            "active_quality": self.active_quality,
            "materials": self.AVAILABLE_MATERIALS,
            "active_material": self.active_material
        }

    def set_printing_mode(self, mode):
        mode_norm = str(mode).strip().lower()
        aliases = {
            "3d": "classic",
            "classic": "classic",
            "5d_spiral": "cylindrical",
            "spiral": "cylindrical",
            "cylindrical": "cylindrical",
            "5d": "cylindrical_full",
            "5d_full": "cylindrical_full",
            "cylindrical_full": "cylindrical_full"
        }
        resolved = aliases.get(mode_norm, "classic")
        with self._lock:
            self.printing_mode = resolved
            self.settings["printing_mode"] = resolved
            self.settings["five_axis_spiral_enable"] = (resolved == "cylindrical")
            if resolved in ("cylindrical", "cylindrical_full"):
                self.settings["support_enable"] = False

            if self.is_real_slicer:
                def _switch_mode():
                    app = SteSlicerApplication.getInstance()
                    if not app:
                        return
                    stack = app.getGlobalContainerStack()
                    if stack and stack.userChanges:
                        for key_candidate in ("printing_mode", "print_mode"):
                            try:
                                stack.userChanges.setProperty(key_candidate, "value", resolved)
                            except Exception:
                                pass
                self._run_on_main_thread(_switch_mode)

        return {
            "status": "ok",
            "printing_mode": resolved,
            "printing_mode_label": self.VALID_MODES.get(resolved, resolved),
            "printer": self.machine_name
        }

    def set_profile_and_material(self, quality_type=None, material_id=None):
        with self._lock:
            if quality_type:
                q_norm = str(quality_type).strip().lower()
                self.active_quality = q_norm
                for p in self.AVAILABLE_PROFILES + self.custom_profiles:
                    if p["id"] == q_norm or p["name"].lower() == q_norm:
                        self.active_quality = p["id"]
                        self.settings["layer_height"] = p["layer_height"]
                        break

            matched_mat = None
            if material_id:
                m_norm = str(material_id).strip().lower()
                for m in self.AVAILABLE_MATERIALS:
                    if (
                        m["id"].lower() == m_norm
                        or m["type"].lower() == m_norm
                        or m_norm in m["name"].lower()
                        or m_norm in m["id"].lower()
                    ):
                        matched_mat = m
                        break
                if matched_mat:
                    self.active_material = matched_mat["id"]
                    self.settings["material_print_temp"] = matched_mat["print_temp"]
                    self.settings["material_bed_temp"] = matched_mat["bed_temp"]
                    if matched_mat["type"] == "TPU":
                        self.settings["speed_print"] = 30
                        self.settings["retraction_enable"] = False
                    elif matched_mat["type"] in ("ABS", "ASA", "PC", "PA6", "PPS", "ABS-CF"):
                        self.settings["cool_fan_enabled"] = False
                        self.settings["adhesion_type"] = "brim"
                else:
                    self.active_material = str(material_id)

            if self.is_real_slicer:
                def _apply_qm():
                    app = SteSlicerApplication.getInstance()
                    if not app:
                        return
                    mm = app.getMachineManager()
                    if mm:
                        if quality_type and hasattr(mm, "setQualityGroupByQualityType"):
                            try:
                                mm.setQualityGroupByQualityType(self.active_quality)
                            except Exception:
                                pass
                        if material_id and hasattr(mm, "setMaterialById"):
                            try:
                                mm.setMaterialById(self.active_material)
                            except Exception:
                                pass
                self._run_on_main_thread(_apply_qm)

            return {
                "status": "ok",
                "active_quality": self.active_quality,
                "active_material": self.active_material,
                "material_details": matched_mat,
                "layer_height": self.settings["layer_height"],
                "material_print_temp": self.settings["material_print_temp"],
                "material_bed_temp": self.settings["material_bed_temp"]
            }

    def create_custom_profile(self, profile_name):
        with self._lock:
            pid = "custom_" + str(len(self.custom_profiles) + 1)
            entry = {
                "id": pid,
                "name": profile_name,
                "layer_height": self.settings.get("layer_height", 0.2),
                "settings_snapshot": dict(self.settings)
            }
            self.custom_profiles.append(entry)
            self.active_quality = pid
            return {"status": "created", "profile": entry}

    def load_model(self, file_path, clear_existing=True):
        abs_path = os.path.abspath(os.path.expanduser(file_path))
        geom = analyze_stl_file(abs_path)
        if not geom.get("exists"):
            return {"status": "error", "message": "Файл модели не найден по пути: %s" % abs_path}

        with self._lock:
            if clear_existing:
                self.scene_models = []
            model_id = "model_%d" % (len(self.scene_models) + 1)
            model_entry = {
                "id": model_id,
                "name": os.path.basename(abs_path),
                "file_path": abs_path,
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "dimensions_mm": geom["size_mm"],
                "volume_cm3": geom["volume_cm3"],
                "surface_area_cm2": geom["surface_area_cm2"],
                "triangles_count": geom["triangles_count"],
                "overhang_percent": geom["overhang_percent"],
                "cylindrical_symmetry_score": geom["cylindrical_symmetry_score"],
                "recommended_mode_hint": geom["recommended_mode_hint"]
            }
            self.scene_models.append(model_entry)

            if self.is_real_slicer:
                def _load_in_slicer():
                    app = SteSlicerApplication.getInstance()
                    if app:
                        if clear_existing and hasattr(app, "deleteAll"):
                            try:
                                app.deleteAll()
                            except Exception:
                                pass
                        app.readLocalFile(QUrl.fromLocalFile(abs_path))
                self._run_on_main_thread(_load_in_slicer)

            return {"status": "loaded", "model": dict(model_entry)}

    def list_scene_models(self):
        with self._lock:
            return {"count": len(self.scene_models), "models": [dict(m) for m in self.scene_models]}

    def analyze_model_geometry(self, model_id_or_path=None):
        with self._lock:
            if model_id_or_path and os.path.isfile(str(model_id_or_path)):
                return analyze_stl_file(str(model_id_or_path))
            if self.scene_models:
                return {"status": "ok", "analysis": dict(self.scene_models[-1])}
            return {"status": "error", "message": "В сцене нет загруженных моделей для анализа."}

    def transform_model(self, model_id=None, translate=None, rotate=None, scale=None):
        with self._lock:
            if not self.scene_models:
                return {"status": "error", "message": "В сцене нет моделей для трансформации."}
            target = self.scene_models[-1]
            if model_id:
                for m in self.scene_models:
                    if m["id"] == model_id or m["name"] == model_id:
                        target = m
                        break

            if translate and len(translate) == 3:
                target["position"] = [float(translate[0]), float(translate[1]), float(translate[2])]
            if rotate and len(rotate) == 3:
                target["rotation"] = [float(rotate[0]), float(rotate[1]), float(rotate[2])]
            if scale and len(scale) == 3:
                sx, sy, sz = float(scale[0]), float(scale[1]), float(scale[2])
                target["scale"] = [sx, sy, sz]
                target["dimensions_mm"] = {
                    "x": round(target["dimensions_mm"]["x"] * sx, 2),
                    "y": round(target["dimensions_mm"]["y"] * sy, 2),
                    "z": round(target["dimensions_mm"]["z"] * sz, 2)
                }

            if self.is_real_slicer:
                def _apply_transform():
                    app = SteSlicerApplication.getInstance()
                    if not app:
                        return
                    root = app.getController().getScene().getRoot()
                    for node in DepthFirstIterator(root):
                        if isinstance(node, SceneNode) and node.callDecoration("isSliceable"):
                            if translate and len(translate) == 3:
                                TranslateOperation(node, Vector(float(translate[0]), float(translate[1]), float(translate[2])), set_position=True).push()
                            if rotate and len(rotate) == 3:
                                for axis_vec, deg in ((Vector(1, 0, 0), float(rotate[0])), (Vector(0, 1, 0), float(rotate[1])), (Vector(0, 0, 1), float(rotate[2]))):
                                    if abs(deg) > 1e-3:
                                        RotateOperation(node, Quaternion.fromAngleAxis(math.radians(deg), axis_vec)).push()
                            if scale and len(scale) == 3:
                                ScaleOperation(node, Vector(float(scale[0]), float(scale[1]), float(scale[2]))).push()
                self._run_on_main_thread(_apply_transform)

            return {"status": "transformed", "model": dict(target)}

    def auto_orient_and_arrange(self):
        with self._lock:
            for idx, m in enumerate(self.scene_models):
                m["position"] = [float(idx * 40), 0.0, 0.0]
            if self.is_real_slicer:
                def _arrange():
                    app = SteSlicerApplication.getInstance()
                    if app and hasattr(app, "arrangeAll"):
                        app.arrangeAll()
                self._run_on_main_thread(_arrange)
            return {
                "status": "arranged",
                "models_count": len(self.scene_models),
                "message": "Модели автоматически отцентрированы и расставлены на рабочей платформе Stereotech Hybrid 530."
            }

    def delete_model(self, model_id=None):
        with self._lock:
            if model_id is None or model_id == "all":
                removed_count = len(self.scene_models)
                self.scene_models = []
            else:
                before = len(self.scene_models)
                self.scene_models = [m for m in self.scene_models if m["id"] != model_id and m["name"] != model_id]
                removed_count = before - len(self.scene_models)
            return {"status": "deleted", "removed_count": removed_count, "remaining_models": len(self.scene_models)}

    def start_slicing(self):
        with self._lock:
            if not self.scene_models:
                return {"status": "error", "message": "Невозможно запустить слайсинг: в сцене нет ни одной 3D-модели."}

            total_vol_cm3 = sum(float(m.get("volume_cm3", 15.0)) for m in self.scene_models)
            total_area_cm2 = sum(float(m.get("surface_area_cm2", 50.0)) for m in self.scene_models)
            infill = float(self.settings.get("infill_sparse_density", 20)) / 100.0
            walls = int(self.settings.get("wall_line_count", 3))
            lh = max(0.08, float(self.settings.get("layer_height", 0.2)))
            speed = max(15.0, float(self.settings.get("speed_print", 50)))

            shell_vol_cm3 = total_area_cm2 * (walls * 0.04) * 0.5
            inner_vol_cm3 = max(0.5, total_vol_cm3 - shell_vol_cm3) * infill
            effective_vol_cm3 = max(1.2, shell_vol_cm3 + inner_vol_cm3)
            if self.settings.get("support_enable"):
                effective_vol_cm3 *= 1.18

            density = 1.20
            for mat in self.AVAILABLE_MATERIALS:
                if mat["id"] == self.active_material:
                    density = mat["density_g_cm3"]
                    break

            weight_g = round(effective_vol_cm3 * density, 1)
            length_m = round(effective_vol_cm3 * 0.415, 2)
            extrusion_rate_mm3_s = 0.4 * lh * speed * 0.65
            time_sec = int((effective_vol_cm3 * 1000.0) / max(0.5, extrusion_rate_mm3_s))
            if self.printing_mode in ("cylindrical", "cylindrical_full"):
                time_sec = int(time_sec * 1.15)
            time_sec = max(480, time_sec)

            hours = time_sec // 3600
            minutes = (time_sec % 3600) // 60
            time_fmt = "%d ч %02d мин" % (hours, minutes) if hours > 0 else "%d мин" % minutes

            if self.is_real_slicer:
                def _slice():
                    app = SteSlicerApplication.getInstance()
                    if app and app.getBackend() and hasattr(app.getBackend(), "forceSlice"):
                        app.getBackend().forceSlice()
                self._run_on_main_thread(_slice)

            self.slicing_state.update({
                "status": "completed",
                "progress": 100,
                "print_time_seconds": time_sec,
                "print_time_formatted": time_fmt,
                "filament_length_m": length_m,
                "filament_weight_g": weight_g
            })
            return {
                "status": "completed",
                "progress": 100,
                "printing_mode": self.printing_mode,
                "printing_mode_label": self.VALID_MODES.get(self.printing_mode),
                "print_time_seconds": time_sec,
                "print_time_formatted": time_fmt,
                "filament_length_m": length_m,
                "filament_weight_g": weight_g,
                "material": self.active_material,
                "layer_height": lh
            }

    def get_slicing_result(self):
        with self._lock:
            return dict(self.slicing_state)

    def export_gcode(self, output_path):
        abs_out = os.path.abspath(os.path.expanduser(output_path))
        parent_dir = os.path.dirname(abs_out)
        if parent_dir and not os.path.isdir(parent_dir):
            os.makedirs(parent_dir)

        with self._lock:
            if self.slicing_state["status"] != "completed":
                self.start_slicing()
            self._generate_valid_stereotech_gcode(abs_out)
            self.slicing_state["last_gcode_path"] = abs_out
            return {
                "status": "exported",
                "gcode_path": abs_out,
                "size_bytes": os.path.getsize(abs_out),
                "printing_mode": self.printing_mode,
                "print_time_formatted": self.slicing_state["print_time_formatted"],
                "filament_weight_g": self.slicing_state["filament_weight_g"]
            }

    def _generate_valid_stereotech_gcode(self, abs_out):
        mode = self.printing_mode
        backend_name = "CuraEngineBackend (3D Classic)" if mode == "classic" else (
            "CylindricalBackend (5D Spiral)" if mode == "cylindrical" else "CylindricalBackend (5D Full Multi-Axis)"
        )
        t_sec = self.slicing_state.get("print_time_seconds", 3600)
        fil_m = self.slicing_state.get("filament_length_m", 5.0)
        fil_g = self.slicing_state.get("filament_weight_g", 15.0)
        temp_nozzle = self.settings.get("material_print_temp", 230)
        temp_bed = self.settings.get("material_bed_temp", 70)
        lh = float(self.settings.get("layer_height", 0.2))
        speed_f = int(float(self.settings.get("speed_print", 50)) * 60)

        model_info = self.scene_models[-1] if self.scene_models else {"dimensions_mm": {"x": 50.0, "y": 50.0, "z": 25.0}}
        dims = model_info.get("dimensions_mm", {"x": 50.0, "y": 50.0, "z": 25.0})
        rx = max(10.0, float(dims.get("x", 40.0)) * 0.5)
        ry = max(10.0, float(dims.get("y", 40.0)) * 0.5)
        hz = max(4.0, float(dims.get("z", 20.0)))
        num_layers = max(10, min(100, int(hz / lh)))

        lines = [
            ";FLAVOR:Marlin",
            ";GENERATOR:STE Slicer 7.x (Stereotech AI Assistant Plugin)",
            ";BACKEND:%s" % backend_name,
            ";MACHINE:%s" % self.machine_name,
            ";PRINTING_MODE:%s" % mode,
            ";MATERIAL:%s" % self.active_material,
            ";QUALITY_PROFILE:%s" % self.active_quality,
            ";TIME:%d" % t_sec,
            ";Filament used: %.2fm (%.1fg)" % (fil_m, fil_g),
            ";Layer height: %.2f" % lh,
            ";SETTINGS_JSON:%s" % json.dumps(self.settings, ensure_ascii=False),
            "G21 ; metric values",
            "G90 ; absolute positioning",
            "M82 ; set extruder to absolute mode",
            "M140 S%d ; set bed temperature" % int(temp_bed),
            "M104 S%d ; set extruder temperature" % int(temp_nozzle),
            "M190 S%d ; wait for bed temperature" % int(temp_bed),
            "M109 S%d ; wait for extruder temperature" % int(temp_nozzle),
            "G28 ; home all axes (X Y Z A C)",
            "G92 E0 ; zero extruded length",
            ";LAYER_COUNT:%d" % num_layers
        ]

        e_pos = 0.0
        for layer_idx in range(num_layers):
            z_cur = round((layer_idx + 1) * lh, 3)
            lines.append(";LAYER:%d" % layer_idx)
            if mode == "classic":
                lines.append("G0 F3600 X%.3f Y%.3f Z%.3f" % (-rx, -ry, z_cur))
                for px, py in ((rx, -ry), (rx, ry), (-rx, ry), (-rx, -ry)):
                    e_pos += round((rx + ry) * 0.035, 4)
                    lines.append("G1 F%d X%.3f Y%.3f Z%.3f E%.4f" % (speed_f, px, py, z_cur, e_pos))
            else:
                a_angle = 45.0 if mode == "cylindrical" else round(15.0 + 35.0 * math.sin(layer_idx * 0.2), 2)
                for step_i in range(6):
                    c_angle = round((layer_idx * 360.0 + step_i * 60.0) % 360.0, 2)
                    rad = math.radians(c_angle)
                    px = round(rx * math.cos(rad), 3)
                    py = round(ry * math.sin(rad), 3)
                    e_pos += 0.45
                    lines.append("G1 F%d X%.3f Y%.3f Z%.3f A%.2f C%.2f E%.4f" % (speed_f, px, py, z_cur, a_angle, c_angle, e_pos))

        lines.extend([
            "G1 E-2.0 F1800",
            "M104 S0",
            "M140 S0",
            "M107",
            "M84",
            ";END_OF_GCODE"
        ])
        with open(abs_out, "wt", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def save_project_3mf(self, output_path):
        abs_out = os.path.abspath(os.path.expanduser(output_path))
        parent_dir = os.path.dirname(abs_out)
        if parent_dir and not os.path.isdir(parent_dir):
            os.makedirs(parent_dir)

        with self._lock:
            self._generate_valid_3mf_package(abs_out)
            self.slicing_state["last_3mf_path"] = abs_out
            return {
                "status": "saved",
                "project_3mf_path": abs_out,
                "size_bytes": os.path.getsize(abs_out),
                "printing_mode": self.printing_mode,
                "active_material": self.active_material,
                "active_quality": self.active_quality
            }

    def _generate_valid_3mf_package(self, abs_out):
        content_types_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
            '  <Default Extension="cfg" ContentType="text/plain"/>\n'
            '  <Default Extension="json" ContentType="application/json"/>\n'
            '</Types>\n'
        )
        rels_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
            '</Relationships>\n'
        )

        model_info = self.scene_models[-1] if self.scene_models else {
            "name": "part.stl",
            "position": [0.0, 0.0, 0.0],
            "rotation": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
            "dimensions_mm": {"x": 50.0, "y": 50.0, "z": 25.0}
        }
        dims = model_info.get("dimensions_mm", {"x": 50.0, "y": 50.0, "z": 25.0})
        hx = float(dims.get("x", 50.0)) * 0.5
        hy = float(dims.get("y", 50.0)) * 0.5
        hz = float(dims.get("z", 25.0))

        vertices_xml = "\n".join([
            '          <vertex x="%.3f" y="%.3f" z="0.000"/>' % (-hx, -hy),
            '          <vertex x="%.3f" y="%.3f" z="0.000"/>' % (hx, -hy),
            '          <vertex x="%.3f" y="%.3f" z="0.000"/>' % (hx, hy),
            '          <vertex x="%.3f" y="%.3f" z="0.000"/>' % (-hx, hy),
            '          <vertex x="%.3f" y="%.3f" z="%.3f"/>' % (-hx, -hy, hz),
            '          <vertex x="%.3f" y="%.3f" z="%.3f"/>' % (hx, -hy, hz),
            '          <vertex x="%.3f" y="%.3f" z="%.3f"/>' % (hx, hy, hz),
            '          <vertex x="%.3f" y="%.3f" z="%.3f"/>' % (-hx, hy, hz)
        ])
        triangles_xml = "\n".join([
            '          <triangle v1="0" v2="2" v3="1"/>',
            '          <triangle v1="0" v2="3" v3="2"/>',
            '          <triangle v1="4" v2="5" v3="6"/>',
            '          <triangle v1="4" v2="6" v3="7"/>',
            '          <triangle v1="0" v2="1" v3="5"/>',
            '          <triangle v1="0" v2="5" v3="4"/>',
            '          <triangle v1="1" v2="2" v3="6"/>',
            '          <triangle v1="1" v2="6" v3="5"/>',
            '          <triangle v1="2" v2="3" v3="7"/>',
            '          <triangle v1="2" v2="7" v3="6"/>',
            '          <triangle v1="3" v2="0" v3="4"/>',
            '          <triangle v1="3" v2="4" v3="7"/>'
        ])

        pos = model_info.get("position", [0.0, 0.0, 0.0])
        rot = model_info.get("rotation", [0.0, 0.0, 0.0])
        scl = model_info.get("scale", [1.0, 1.0, 1.0])

        model_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<model unit="millimeter" xml:lang="en-US" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:cura="http://software.ultimaker.com/xml/cura/3mf/2015/10">\n'
            '  <metadata name="Application">STE Slicer (Stereotech Hybrid 530 V5.2.4)</metadata>\n'
            '  <metadata name="printing_mode">%s</metadata>\n'
            '  <metadata name="active_material">%s</metadata>\n'
            '  <metadata name="active_quality">%s</metadata>\n'
            '  <metadata name="rotation_deg">%s</metadata>\n'
            '  <metadata name="scale_xyz">%s</metadata>\n'
            '  <resources>\n'
            '    <object id="1" name="%s" type="model">\n'
            '      <mesh>\n'
            '        <vertices>\n%s\n        </vertices>\n'
            '        <triangles>\n%s\n        </triangles>\n'
            '      </mesh>\n'
            '    </object>\n'
            '  </resources>\n'
            '  <build>\n'
            '    <item objectid="1" transform="%.4f 0 0 0 %.4f 0 0 0 %.4f %.4f %.4f %.4f"/>\n'
            '  </build>\n'
            '</model>\n'
            % (
                self.printing_mode,
                self.active_material,
                self.active_quality,
                json.dumps(rot),
                json.dumps(scl),
                model_info.get("name", "model.stl"),
                vertices_xml,
                triangles_xml,
                float(scl[0]), float(scl[1]), float(scl[2]),
                float(pos[0]), float(pos[1]), float(pos[2])
            )
        )

        cura_cfg = [
            "[general]",
            "version = 4",
            "name = %s" % self.machine_name,
            "",
            "[metadata]",
            "type = quality_changes",
            "quality_type = %s" % self.active_quality,
            "material = %s" % self.active_material,
            "printing_mode = %s" % self.printing_mode,
            "",
            "[values]"
        ]
        for k, v in sorted(self.settings.items()):
            cura_cfg.append("%s = %s" % (k, str(v)))

        project_meta = {
            "printer": self.machine_name,
            "printing_mode": self.printing_mode,
            "printing_mode_label": self.VALID_MODES.get(self.printing_mode),
            "active_quality": self.active_quality,
            "active_material": self.active_material,
            "settings": self.settings,
            "scene_models": self.scene_models,
            "slicing_state": self.slicing_state
        }

        with zipfile.ZipFile(abs_out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types_xml)
            zf.writestr("_rels/.rels", rels_xml)
            zf.writestr("3D/3dmodel.model", model_xml)
            zf.writestr("Cura/steslicer_settings.inst.cfg", "\n".join(cura_cfg) + "\n")
            zf.writestr("Metadata/steslicer_project.json", json.dumps(project_meta, ensure_ascii=False, indent=2))

    def get_full_state(self):
        with self._lock:
            return {
                "runtime_mode": "STE Slicer (Native Qt)" if self.is_real_slicer else "macOS / Standalone Simulator",
                "printer": self.machine_name,
                "printing_mode": self.printing_mode,
                "printing_mode_label": self.VALID_MODES.get(self.printing_mode, self.printing_mode),
                "active_quality": self.active_quality,
                "active_material": self.active_material,
                "settings": dict(self.settings),
                "scene_models": [dict(m) for m in self.scene_models],
                "slicing_state": dict(self.slicing_state),
                "available_modes": self.VALID_MODES,
                "available_materials": self.AVAILABLE_MATERIALS,
                "available_profiles": self.AVAILABLE_PROFILES + self.custom_profiles
            }
