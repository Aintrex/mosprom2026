"""Native UM operations with world-space confirmation; all methods run on Qt."""
import math

from .models import bounds, mesh_nodes, walk
from .qt_bridge import SlicerError
from .slicing import backend_busy


AXES = ("x", "y", "z")
LIMIT = 1000000.0  # Technical float32/geometry bound, not a printer dimension.
TRANSFORM_FIELDS = {"get_model_state": ("object_id",), "move_model": ("object_id", "position_mm"),
                    "rotate_model": ("object_id", "axis", "angle_degrees"),
                    "scale_model": ("object_id", "factors"), "delete_model": ("object_id",)}


def number(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        return False


def validate_transform_arguments(name, arguments):
    if not isinstance(arguments, dict) or set(arguments) != set(TRANSFORM_FIELDS[name]):
        raise ValueError("Укажите только поля: " + ", ".join(TRANSFORM_FIELDS[name]))
    if not isinstance(arguments["object_id"], str) or not arguments["object_id"]:
        raise ValueError("object_id должен быть непустой строкой")
    if name in ("move_model", "scale_model"):
        values = arguments["position_mm" if name == "move_model" else "factors"]
        if not isinstance(values, dict) or set(values) != set(AXES) or not all(number(v) for v in values.values()):
            raise ValueError("Нужны конечные числа x/y/z без bool")
        if name == "scale_model" and not all(0.01 <= v <= 100 for v in values.values()):
            raise ValueError("Факторы масштаба должны быть в пределах 0.01–100")
        if name == "move_model" and any(abs(v) > LIMIT for v in values.values()):
            raise ValueError("Координаты ограничены ±1000000 мм для прототипа")
    if name == "rotate_model":
        if arguments["axis"] not in AXES or not isinstance(arguments["axis"], str):
            raise ValueError("axis должен быть x/y/z")
        if not number(arguments["angle_degrees"]) or abs(arguments["angle_degrees"]) > LIMIT:
            raise ValueError("Нужен конечный угол в пределах ±1000000 градусов")


def near(a, b):
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-5)


def same(a, b):
    return all(near(x, y) for row_a, row_b in zip(a, b) for x, y in zip(row_a, row_b))


def matrix(node):
    raw = node.getWorldTransformation().getData()
    result = [[float(raw[i][j]) for j in range(4)] for i in range(4)]
    if not all(math.isfinite(v) and abs(v) <= LIMIT for row in result for v in row):
        raise SlicerError("model_transform_unsupported", "Матрица вне конечных технических границ прототипа")
    if any(not near(result[3][j], float(j == 3)) for j in range(4)):
        raise SlicerError("model_transform_unsupported", "Поддерживаются только аффинные преобразования")
    return result


def identity(value):
    return same(value, [[float(i == j) for j in range(4)] for i in range(4)])


class ModelTransforms(object):
    def __init__(self, registry, change_lock, invalidate):
        self.registry, self.lock, self.invalidate = registry, change_lock, invalidate

    def state(self, app, object_id):
        return self._state(app, self.registry.resolve(app, object_id), object_id)

    def _state(self, app, node, object_id):
        transform, box = matrix(node), bounds(node)
        if box is None:
            raise SlicerError("model_bounds_unavailable", "Габариты модели недоступны; преобразование не подтверждено")
        if any(abs(box[edge][axis]) > LIMIT for edge in ("min", "max") for axis in AXES):
            raise SlicerError("model_transform_unsupported", "Геометрия вне ±1000000 мм")
        world_position = node.getWorldPosition()
        position = [float(getattr(world_position, a)) for a in AXES]
        if not all(math.isfinite(v) and near(v, transform[i][3]) for i, v in enumerate(position)):
            raise SlicerError("model_transform_unsupported", "API не подтвердил мировую позицию и матрицу")
        # BuildVolume may not exist yet or may have no bounding box during startup.
        volume = app.getBuildVolume()
        outside = None
        if volume is not None and volume.getBoundingBox() is not None:
            targets = [node] + [part for part in mesh_nodes(node) if part is not node]
            if all(callable(getattr(part, "isOutsideBuildArea", None)) for part in targets):
                for part in targets:
                    volume.checkBoundsAndUpdate(part)
                values = [part.isOutsideBuildArea() for part in targets]
                if all(isinstance(v, bool) for v in values):
                    outside = any(values)
        plate = node.callDecoration("getBuildPlateNumber")
        return {"object_id": object_id, "position_mm": dict(zip(AXES, position)),
                "world_transform": transform, "bounds": box,
                "build_plate": int(plate) if isinstance(plate, int) and not isinstance(plate, bool) else None,
                "outside_build_area": outside}

    @staticmethod
    def _context(app):
        scene = app.getController().getScene()
        return app.getGlobalContainerStack(), scene, scene.getRoot(), app.getMultiBuildPlateModel().activeBuildPlate

    def apply(self, app, name, arguments):
        # Hold the existing lock on Qt through confirmation, even if the worker times out.
        if not self.lock.acquire(False):
            error = SlicerError("model_operation_busy", "Заняты импорт, нарезка, экспорт или другая запись; чтения доступны")
            error.operation_started, error.operation_stage = False, "preflight"
            raise error
        admission = {"started": False}
        try:
            return self._apply(app, name, arguments, admission)
        except Exception as exc:
            # Preserve the API exception/traceback; describe dispatch certainty,
            # not whether a failed native operation managed to change geometry.
            exc.operation_started = admission["started"]
            exc.operation_stage = "execution" if admission["started"] else "preflight"
            raise
        finally:
            self.lock.release()

    def _apply(self, app, name, arguments, admission):
        object_id = arguments["object_id"]
        node = self.registry.resolve(app, object_id)
        context = self._context(app)
        machine, scene, root, plate = context
        if machine is None:
            raise SlicerError("no_active_machine", "В STE Slicer не выбрана машина")
        backend = app.getBackendManager().getCurrentBackend()
        local = app.getOutputDeviceManager().getOutputDevice("local_file")
        if backend is not None and backend_busy(backend) or local is not None and local._writing:
            raise SlicerError("model_operation_busy", "Штатный backend или запись файла ещё заняты")
        if any(n.callDecoration("isBlockSlicing") for n in walk(root)):
            raise SlicerError("model_transform_unsupported", "Закройте G-code preview перед изменением модели")
        if (node.callDecoration("getBuildPlateNumber") != plate or
                any(part.callDecoration("getBuildPlateNumber") != plate for part in mesh_nodes(node))):
            raise SlicerError("model_transform_unsupported", "Изменения разрешены только на активной платформе")
        if not node.isEnabled() or node.getParent() is None or not identity(matrix(node.getParent())):
            raise SlicerError("model_transform_unsupported", "Нужна включённая верхняя модель/группа с единичной мировой матрицей родителя")
        before = self._state(app, node, object_id)
        linear = before["world_transform"]
        magnitude = max(abs(v) for row in linear[:3] for v in row[:3])
        a = [[v / magnitude for v in row[:3]] for row in linear[:3]] if magnitude else [[0] * 3] * 3
        determinant = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) -
                       a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0]) +
                       a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
        if name != "delete_model" and abs(determinant) < 1e-12:
            raise SlicerError("model_transform_unsupported", "Вырожденная матрица не допускает штатное мировое преобразование")
        noop = (name == "move_model" and arguments["position_mm"] == before["position_mm"] or
                name == "rotate_model" and arguments["angle_degrees"] % 360 == 0 or
                name == "scale_model" and all(v == 1 for v in arguments["factors"].values()))
        if noop:
            return {"object_id": object_id, "before": before, "after": before, "changed": False, "warnings": []}
        expected = [list(row) for row in before["world_transform"]]
        from UM.Math.Vector import Vector
        if name == "move_model":
            from UM.Operations.TranslateOperation import TranslateOperation
            position = arguments["position_mm"]
            for i, a in enumerate(AXES):
                expected[i][3] = float(position[a])
            operation = TranslateOperation(node, Vector(*[position[a] for a in AXES]), set_position=True)
        elif name == "rotate_model":
            from UM.Math.Quaternion import Quaternion
            from UM.Operations.RotateOperation import RotateOperation
            angle = math.radians(arguments["angle_degrees"] % 360)
            rotation = Quaternion.fromAngleAxis(angle, Vector(*[float(a == arguments["axis"]) for a in AXES]))
            r = rotation.toMatrix().getData()
            for i in range(3):
                for j in range(3):
                    expected[i][j] = sum(float(r[i][k]) * before["world_transform"][k][j] for k in range(3))
            operation = RotateOperation(node, rotation, rotate_around_point=node.getPosition())
        elif name == "scale_model":
            from UM.Operations.ScaleOperation import ScaleOperation
            factors = arguments["factors"]
            for i, a in enumerate(AXES):
                for j in range(3):
                    expected[i][j] *= factors[a]
            operation = ScaleOperation(node, Vector(*[factors[a] for a in AXES]), set_scale=False,
                                       add_scale=False, relative_scale=False, scale_around_point=node.getWorldPosition())
        else:
            from UM.Operations.RemoveSceneNodeOperation import RemoveSceneNodeOperation
            operation = RemoveSceneNodeOperation(node)
        prefs = app.getPreferences()
        # push_free (default off) may move neighbours later; drop_down (default on in STE)
        # only re-seats this model on the bed after the confirmed operation, as in the UI.
        if prefs.getValue("physics/automatic_push_free") is not False:
            raise SlicerError("model_transform_unsupported", "Выключите «Автоматически раздвигать модели» (push free) в настройках STE: физика может сдвинуть соседние модели. Preferences не изменены")
        drop_down = prefs.getValue("physics/automatic_drop_down") is not False
        if name != "delete_model":
            self._check_expected(before, expected)
        others = [(n, matrix(n)) for n in self.registry.nodes(app) if n is not node]
        # Repeat ID/context immediately before the native push; never trust a worker-held node.
        if self._context(app) != context or self.registry.resolve(app, object_id) is not node:
            raise SlicerError("model_transform_unsupported", "Машина, сцена или платформа изменилась до записи")
        push = app.getOperationStack().push
        try:
            admission["started"] = True
            push(operation)
        finally:
            # A native redo may have changed geometry before raising; never roll it back silently.
            self.invalidate()
        if self._context(app) != context:
            raise SlicerError("model_transform_not_applied", "Контекст изменился во время операции; перечитайте list_models/get_model_state. Автоповтора/отката нет")
        current = self.registry.nodes(app)
        if any(n not in current or not same(matrix(n), old) for n, old in others) or set(current) - {n for n, old in others} - {node}:
            raise SlicerError("model_transform_not_applied", "Во время операции изменились другие модели; проверьте сцену без автоматического отката")
        if name == "delete_model":
            if node in current or node.getParent() is not None or any(n is node for n in walk(root)):
                raise SlicerError("model_transform_not_applied", "Удаление не подтверждено сценой; вызовите list_models")
            return {"removed_object_id": object_id, "before": before, "after": None, "removed": True}
        if self.registry.resolve(app, object_id) is not node:
            raise SlicerError("model_transform_not_applied", "Объект изменился во время операции")
        after = self._state(app, node, object_id)
        if (after["world_transform"] == before["world_transform"] or not same(after["world_transform"], expected) or
                after["build_plate"] != before["build_plate"]):
            raise SlicerError("model_transform_not_applied", "Фактическое преобразование отличается от запроса. Перечитайте get_model_state/UI; автоповтора/отката нет")
        warnings = []
        if drop_down:
            warnings.append("Включено автоматическое опускание на стол: после операции STE может сдвинуть модель по мировой Y (вертикаль), чтобы она стояла на столе")
        if after["outside_build_area"] is True:
            warnings.append("Модель вне разрешённой области; положение не означает готовность к печати")
        elif after["outside_build_area"] is None:
            warnings.append("BuildVolume не подтвердил положение в области печати")
        return {"object_id": object_id, "before": before, "after": after, "changed": True, "warnings": warnings}

    @staticmethod
    def _check_expected(before, expected):
        if any(not math.isfinite(v) or abs(v) > LIMIT for row in expected for v in row):
            raise SlicerError("model_transform_unsupported", "Итоговая матрица вне технических границ")
        # Conservative world AABB bound; no mesh edits or separate geometry engine.
        origin = [before["position_mm"][a] for a in AXES]
        radius = sum(max(abs(before["bounds"][e][a] - origin[i]) for e in ("min", "max"))
                     for i, a in enumerate(AXES))
        maximum_change = max(1.0, max(abs(v) for row in expected[:3] for v in row[:3]))
        if max(abs(row[3]) for row in expected[:3]) + radius * maximum_change > LIMIT:
            raise SlicerError("model_transform_unsupported", "Геометрия может выйти за ±1000000 мм; уменьшите запрос")
