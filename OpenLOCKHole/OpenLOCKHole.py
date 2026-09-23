"""Fusion 360 add-in for adding a Basic OpenLOCK profile to an active sketch."""

import math
import os
import sys
import traceback
import uuid

import adsk.core
import adsk.fusion


# Fusion loads this file as a standalone add-in module, so make the sibling
# profile module importable without requiring a Python package install.
ADDIN_DIR = os.path.dirname(os.path.realpath(__file__))
if ADDIN_DIR not in sys.path:
    sys.path.insert(0, ADDIN_DIR)

from profile_loader import load_profile_namespace  # noqa: E402

_PROFILE = load_profile_namespace(ADDIN_DIR)
PROFILE_NAME = _PROFILE["PROFILE_NAME"]
PROFILE_VERSION = _PROFILE["PROFILE_VERSION"]


COMMAND_ID = "fusion_openlock_create_hole"
COMMAND_NAME = "Create OpenLOCK Hole"
COMMAND_DESCRIPTION = "Add a Basic OpenLOCK profile to the active sketch."
REMOVED_MOVE_COMMAND_ID = "fusion_openlock_move_hole"
SKETCH_PANEL_IDS = ("SketchCreatePanel", "SketchPanel")
ATTRIBUTE_GROUP = "FusionOpenLOCK"
MM_TO_CM = 0.1

_app = None
_ui = None
_command_definition = None
_command_control = None
_handlers = []


def run(context):
    """Entry point called by Fusion when the add-in starts."""

    global _app, _ui, _command_definition, _command_control

    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        # Fusion can call run more than once while developing an add-in.
        old_definition = _ui.commandDefinitions.itemById(COMMAND_ID)
        if old_definition:
            old_definition.deleteMe()
        _remove_legacy_move_command()

        _command_definition = _ui.commandDefinitions.addButtonDefinition(
            COMMAND_ID, COMMAND_NAME, COMMAND_DESCRIPTION, ""
        )
        created_handler = CommandCreatedHandler()
        _command_definition.commandCreated.add(created_handler)
        _handlers.append(created_handler)
        _command_control = _add_command_to_sketch_workspace(_command_definition)

    except Exception:
        if _ui:
            _ui.messageBox("OpenLOCK add-in failed to start:\n{}".format(traceback.format_exc()))


def stop(context):
    """Entry point called by Fusion when the add-in stops."""

    global _command_definition, _command_control

    try:
        if _command_control:
            _command_control.deleteMe()
        if _command_definition:
            _command_definition.deleteMe()
        _remove_legacy_move_command()
    except Exception:
        if _ui:
            _ui.messageBox("OpenLOCK add-in failed to stop:\n{}".format(traceback.format_exc()))
    finally:
        _command_control = None
        _command_definition = None
        _handlers[:] = []


def _remove_legacy_move_command():
    """Remove the former Move command from older running add-in versions."""

    workspace = _ui.workspaces.itemById("FusionSolidEnvironment") if _ui else None
    if workspace:
        for panel_id in SKETCH_PANEL_IDS:
            panel = workspace.toolbarPanels.itemById(panel_id)
            control = panel.controls.itemById(REMOVED_MOVE_COMMAND_ID) if panel else None
            if control:
                try:
                    control.deleteMe()
                except Exception:
                    pass

    definition = (
        _ui.commandDefinitions.itemById(REMOVED_MOVE_COMMAND_ID) if _ui else None
    )
    if definition:
        try:
            definition.deleteMe()
        except Exception:
            pass


def _add_command_to_sketch_workspace(command_definition):
    """Add the command to Fusion's contextual Sketch Create panel."""

    workspace = _ui.workspaces.itemById("FusionSolidEnvironment")
    if not workspace:
        return None

    panel = None
    for panel_id in SKETCH_PANEL_IDS:
        panel = workspace.toolbarPanels.itemById(panel_id)
        if panel:
            break

    if not panel:
        return None

    old_control = panel.controls.itemById(command_definition.id)
    if old_control:
        old_control.deleteMe()

    control = panel.controls.addCommand(command_definition)
    control.isPromoted = True
    return control


class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        try:
            command = args.command
            inputs = command.commandInputs

            clip_type = inputs.addTextBoxCommandInput(
                "clipType",
                "Clip Type",
                "{} {}".format(PROFILE_NAME, PROFILE_VERSION.split()[-1]),
                1,
                True,
            )
            clip_type.isEnabled = False

            reference_edge = inputs.addSelectionInput(
                "referenceEdge",
                "Reference Edge",
                "Select a sketch line. The 13.8 mm edge is previewed on the same line, with both midpoints initially aligned; use Flip to switch sides.",
            )
            reference_edge.addSelectionFilter("SketchLines")
            reference_edge.setSelectionLimits(1, 1)
            reference_edge.hasFocus = True

            inputs.addBoolValueInput("flip", "Flip", True, "", False)

            preview_handler = PreviewHandler()
            command.executePreview.add(preview_handler)
            _handlers.append(preview_handler)

            validate_handler = ValidateInputsHandler()
            command.validateInputs.add(validate_handler)
            _handlers.append(validate_handler)

            execute_handler = ExecuteHandler()
            command.execute.add(execute_handler)
            _handlers.append(execute_handler)

        except Exception:
            if _ui:
                _ui.messageBox("Could not create OpenLOCK command:\n{}".format(traceback.format_exc()))


class ValidateInputsHandler(adsk.core.ValidateInputsEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        try:
            inputs = args.inputs
            reference_edge = inputs.itemById("referenceEdge")

            args.areInputsValid = bool(
                reference_edge
                and reference_edge.selectionCount == 1
            )

        except Exception:
            args.areInputsValid = False


class ExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        try:
            inputs = args.command.commandInputs
            design = adsk.fusion.Design.cast(_app.activeProduct)
            if not design:
                raise RuntimeError("Open an editable Fusion design before running the command.")

            sketch = adsk.fusion.Sketch.cast(design.activeEditObject)
            if not sketch:
                raise RuntimeError("Edit a sketch before adding an OpenLOCK profile.")

            profile, points_mm, origin_mm, rotation_deg, flip, offset_mm, reference_line = (
                _reference_profile_placement(sketch, inputs)
            )

            profile_geometry = _add_closed_profile(
                sketch,
                points_mm,
                profile["BASIC_OPENLOCK_DIMENSIONS_MM"]["center_slot_corner_fillet_radius"],
                profile["BASIC_OPENLOCK_FILLET_CORNER_INDICES"],
            )
            try:
                _constrain_openlock_profile(
                    sketch,
                    profile_geometry,
                    profile,
                    reference_line,
                    points_mm,
                )
                _write_profile_attributes(
                    profile_geometry["lines"] + list(profile_geometry["arcs"].values()),
                    origin_mm,
                    rotation_deg,
                    flip,
                    profile["PROFILE_NAME"],
                    profile["PROFILE_VERSION"],
                    alignment_mode="referenceEdge",
                    offset_mm=offset_mm,
                )
            except Exception:
                _delete_profile_geometry(
                    profile_geometry["lines"],
                    profile_geometry["arcs"],
                    profile_geometry["mirror_line"],
                )
                raise

        except Exception:
            if _ui:
                _ui.messageBox("Could not create OpenLOCK sketch:\n{}".format(traceback.format_exc()))


def _constrain_openlock_profile(sketch, profile_geometry, profile, reference_line, points_mm):
    """Apply the same constraint plan during Execute and command Preview."""

    audit = profile["audit_basic_openlock_constraint_plan"]()
    if not audit["valid"]:
        raise RuntimeError(
            "OpenLOCK constraint plan is invalid: duplicate targets={}, "
            "priority angle conflicts={}".format(
                audit["duplicate_targets"],
                audit["priority_angle_conflicts"],
            )
        )

    dimensions = profile["BASIC_OPENLOCK_DIMENSIONS_MM"]
    _constrain_profile_shape(
        sketch,
        profile_geometry["lines"],
        profile_geometry["arcs"],
        reference_line,
        points_mm,
        source_lines=profile_geometry["independent_lines"],
        source_arc_constraints=profile_geometry["independent_arc_constraints"],
        source_angle_pairs=profile_geometry["angle_pairs"],
        symmetry_pairs=profile_geometry["symmetry_pairs"],
        direction_constraints=profile_geometry["direction_constraints"],
        mirror_line=profile_geometry["mirror_line"],
        alignment_line=profile_geometry["alignment_line"],
        shoulder_inner_point=profile_geometry["shoulder_inner_point"],
        shoulder_inner_width_mm=dimensions["shoulder_inner_half_width"],
        outer_step_line=profile_geometry["outer_step_line"],
        outer_step_height_mm=dimensions["outer_step_height"],
        shoulder_width_line=profile_geometry["shoulder_width_line"],
        shoulder_width_mm=dimensions["shoulder_width"],
        outer_step_width_line=profile_geometry["outer_step_width_line"],
        outer_step_width_mm=dimensions["outer_step_width"],
        top_edge_length_mm=dimensions["top_edge_length"],
        fillet_radius_mm=dimensions["center_slot_corner_fillet_radius"],
        bottom_flat_line=profile_geometry["bottom_flat_line"],
        slot_wall_line=profile_geometry["slot_wall_line"],
        slot_half_width_axis=profile_geometry["mirror_line"],
        slot_half_width_line=profile_geometry["slot_wall_line"],
        slot_depth_base_line=profile_geometry["bottom_flat_line"],
        slot_depth_ceiling_line=profile_geometry["slot_ceiling_line"],
        slope_angle_pair=profile_geometry["slope_angle_pair"],
        overall_height_mm=dimensions["overall_height"],
        # 2.97 mm is measured between the sharp, pre-fillet corners. The
        # visible line loses one R0.50 trim length.
        bottom_flat_length_mm=(
            dimensions["bottom_flat_from_slot_wall"]
            - dimensions["center_slot_corner_fillet_radius"]
        ),
        slot_depth_mm=dimensions["center_slot_depth_from_bottom"],
        slot_half_width_mm=dimensions["center_slot_half_width"],
        slope_angle_deg=dimensions["slope_angle"],
    )


class PreviewHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        try:
            design = adsk.fusion.Design.cast(_app.activeProduct)
            active_edit_object = design.activeEditObject if design else None
            sketch = adsk.fusion.Sketch.cast(active_edit_object) if design else None
            if not sketch:
                args.isValidResult = False
                return

            inputs = args.command.commandInputs
            reference_edge = inputs.itemById("referenceEdge")
            if not reference_edge or reference_edge.selectionCount != 1:
                args.isValidResult = False
                return

            profile, points_mm, _, _, _, _, _ = _reference_profile_placement(
                sketch,
                inputs,
            )
            _add_closed_profile(
                sketch,
                points_mm,
                profile["BASIC_OPENLOCK_DIMENSIONS_MM"]["center_slot_corner_fillet_radius"],
                profile["BASIC_OPENLOCK_FILLET_CORNER_INDICES"],
            )
            # Keep the preview transaction temporary. Fusion rolls it back before
            # ExecuteHandler runs, which then creates the fully constrained result.
            args.isValidResult = False
        except Exception:
            args.isValidResult = False


def _reference_profile_placement(sketch, inputs):
    reference_edge = inputs.itemById("referenceEdge")
    if not reference_edge or reference_edge.selectionCount != 1:
        raise RuntimeError("Select one sketch line as the reference edge.")

    selected_line = adsk.fusion.SketchLine.cast(reference_edge.selection(0).entity)
    if not selected_line or selected_line.parentSketch != sketch:
        raise RuntimeError("Select a line in the sketch currently being edited.")

    start_mm, end_mm = _sketch_line_endpoints_mm(selected_line)
    origin_mm = (
        (start_mm[0] + end_mm[0]) / 2,
        (start_mm[1] + end_mm[1]) / 2,
    )
    rotation_deg = math.degrees(
        math.atan2(end_mm[1] - start_mm[1], end_mm[0] - start_mm[0])
    )
    flip = inputs.itemById("flip").value
    offset_mm = 0.0
    profile = load_profile_namespace(ADDIN_DIR)
    points_mm = profile["transform_profile_to_reference_mm"](
        profile["basic_openlock_profile_mm"](),
        start_mm,
        end_mm,
        flip=flip,
    )
    return profile, points_mm, origin_mm, rotation_deg, flip, offset_mm, selected_line


def _sketch_line_endpoints_mm(line):
    start = line.startSketchPoint.geometry
    end = line.endSketchPoint.geometry
    return (
        (start.x / MM_TO_CM, start.y / MM_TO_CM),
        (end.x / MM_TO_CM, end.y / MM_TO_CM),
    )


def _add_closed_profile(sketch, points_mm, fillet_radius_mm, fillet_corner_indices):
    """Create the original full outline and prepare symmetry constraints.

    Keeping the original 18-line topology is important: it preserves the
    existing dimension targets (including the single 13.8 mm top edge). The
    left and right entities remain separate sketch entities, but Fusion's
    symmetry constraints keep the left side driven by the right side without
    splitting dimensioned edges into half-length segments.
    """

    sketch_lines = sketch.sketchCurves.sketchLines
    sketch_arcs = sketch.sketchCurves.sketchArcs
    created_lines = []
    created_arcs = {}
    mirror_line = None
    first_line = None
    previous_end = None

    try:
        for index in range(len(points_mm)):
            start_mm = points_mm[index]
            end_mm = points_mm[(index + 1) % len(points_mm)]
            start = previous_end or _point3d_mm(start_mm)
            end = (
                first_line.startSketchPoint
                if index == len(points_mm) - 1
                else _point3d_mm(end_mm)
            )
            line = sketch_lines.addByTwoPoints(start, end)
            if not line:
                raise RuntimeError("Fusion could not create a profile segment.")
            created_lines.append(line)
            if first_line is None:
                first_line = line
            previous_end = line.endSketchPoint

        radius_cm = fillet_radius_mm * MM_TO_CM
        for corner_index in fillet_corner_indices:
            incoming_line = created_lines[(corner_index - 1) % len(created_lines)]
            outgoing_line = created_lines[corner_index]
            arc = sketch_arcs.addFillet(
                incoming_line,
                incoming_line.endSketchPoint.geometry,
                outgoing_line,
                outgoing_line.startSketchPoint.geometry,
                radius_cm,
            )
            if not arc:
                raise RuntimeError("Fusion could not create an OpenLOCK sketch fillet.")
            # SketchArcs.addFillet can inherit the construction state of the
            # trimmed sketch geometry in some Fusion builds. The fillets are
            # part of the actual closed profile, so force them to normal
            # sketch geometry explicitly.
            arc.isConstruction = False
            created_arcs[corner_index] = arc

        # The axis is intentionally a construction line. Its endpoints are
        # placed at the centers of the top edge and slot ceiling so the axis
        # follows the profile when the symmetry constraints solve.
        top_center_mm = _midpoint_mm(points_mm[7], points_mm[8])
        slot_center_mm = _midpoint_mm(points_mm[16], points_mm[17])
        mirror_line = sketch_lines.addByTwoPoints(
            _point3d_mm(top_center_mm),
            _point3d_mm(slot_center_mm),
        )
        if not mirror_line:
            raise RuntimeError("Fusion could not create the OpenLOCK centerline.")
        mirror_line.isConstruction = True

        # Pair corresponding entities across the axis. The top edge and slot
        # ceiling are each a single centered line, so their endpoints are
        # paired instead of pairing the lines with themselves.
        constraint_plan = _PROFILE["basic_openlock_constraint_plan"]()
        symmetry_pairs = [
            (created_lines[right], created_lines[left])
            for right, left in constraint_plan["symmetry_line_pairs"]
        ]
        symmetry_pairs.extend(
            [
                (created_lines[index].startSketchPoint, created_lines[index].endSketchPoint)
                for index in constraint_plan["centered_line_indices"]
            ]
        )
        symmetry_pairs.extend(
            [
                (created_arcs[0], created_arcs[15]),
                (created_arcs[17], created_arcs[16]),
            ]
        )

        return {
            "lines": created_lines,
            "arcs": created_arcs,
            "mirror_line": mirror_line,
            "alignment_line": created_lines[7],
            "shoulder_inner_point": created_lines[
                constraint_plan["shoulder_inner_point_line_index"]
            ].startSketchPoint,
            "outer_step_line": created_lines[
                constraint_plan["outer_step_line_index"]
            ],
            "shoulder_width_line": created_lines[
                constraint_plan["shoulder_width_line_index"]
            ],
            "outer_step_width_line": created_lines[
                constraint_plan["outer_step_width_line_index"]
            ],
            "symmetry_pairs": symmetry_pairs,
            "independent_lines": [
                created_lines[index]
                for index in constraint_plan["independent_line_indices"]
            ],
            "independent_arc_constraints": [
                (created_arcs[0], created_lines[17], created_lines[0]),
                (created_arcs[17], created_lines[16], created_lines[17]),
            ],
            "angle_pairs": [
                (created_lines[incoming], created_lines[outgoing])
                for incoming, outgoing in constraint_plan["angle_pairs"]
            ],
            "direction_constraints": {
                "parallel": [
                    (created_lines[index], created_lines[7])
                    for index in constraint_plan["parallel_line_indices"]
                ],
                "perpendicular": [
                    (created_lines[index], created_lines[7])
                    for index in constraint_plan["perpendicular_line_indices"]
                ],
                "axis_perpendicular": (mirror_line, created_lines[7]),
            },
            "bottom_flat_line": created_lines[0],
            "slot_wall_line": created_lines[17],
            "slot_ceiling_line": created_lines[16],
            "slope_angle_pair": (
                created_lines[constraint_plan["priority_angle_pair"][0]],
                created_lines[constraint_plan["priority_angle_pair"][1]],
            ),
        }
    except Exception:
        _delete_profile_geometry(
            created_lines,
            created_arcs,
            mirror_line,
        )
        raise


def _delete_profile_geometry(lines, arcs_by_corner, mirror_line=None):
    """Delete added arcs before their trimmed profile lines."""

    for arc in reversed(list(arcs_by_corner.values())):
        if arc and arc.isValid and arc.isDeletable:
            arc.deleteMe()
    for line in reversed(lines):
        if line.isValid and line.isDeletable:
            line.deleteMe()
    if mirror_line and mirror_line.isValid and mirror_line.isDeletable:
        mirror_line.deleteMe()


def _midpoint_mm(point_one, point_two):
    return (
        (point_one[0] + point_two[0]) / 2,
        (point_one[1] + point_two[1]) / 2,
    )


def _line_length_cm(line):
    start = line.startSketchPoint.geometry
    end = line.endSketchPoint.geometry
    return math.hypot(end.x - start.x, end.y - start.y)


def _dimension_text_point(line, counter_clockwise, offset_cm=0.45):
    start = line.startSketchPoint.geometry
    end = line.endSketchPoint.geometry
    dx = end.x - start.x
    dy = end.y - start.y
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        raise RuntimeError("Cannot constrain a zero-length OpenLOCK segment.")

    # Keep linear dimension labels outside the closed outline.
    normal_x, normal_y = (dy / length, -dx / length)
    if not counter_clockwise:
        normal_x, normal_y = -normal_x, -normal_y
    return adsk.core.Point3D.create(
        (start.x + end.x) / 2 + normal_x * offset_cm,
        (start.y + end.y) / 2 + normal_y * offset_cm,
        0,
    )


def _offset_dimension_text_point(line_one, line_two, offset_cm=0.45):
    """Place an offset dimension label between two parallel sketch lines."""

    first_start = line_one.startSketchPoint.geometry
    first_end = line_one.endSketchPoint.geometry
    second_start = line_two.startSketchPoint.geometry
    second_end = line_two.endSketchPoint.geometry
    first_mid_x = (first_start.x + first_end.x) / 2
    first_mid_y = (first_start.y + first_end.y) / 2
    second_mid_x = (second_start.x + second_end.x) / 2
    second_mid_y = (second_start.y + second_end.y) / 2
    dx = first_end.x - first_start.x
    dy = first_end.y - first_start.y
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        raise RuntimeError("Cannot place an offset dimension on a zero-length line.")

    normal_x, normal_y = dy / length, -dx / length
    return adsk.core.Point3D.create(
        (first_mid_x + second_mid_x) / 2 + normal_x * offset_cm,
        (first_mid_y + second_mid_y) / 2 + normal_y * offset_cm,
        0,
    )


def _horizontal_dimension_text_point(point_one, point_two, offset_cm=0.45):
    """Place a horizontal point-to-point dimension below the target point."""

    first = point_one.geometry
    second = point_two.geometry
    return adsk.core.Point3D.create(
        (first.x + second.x) / 2,
        second.y - offset_cm,
        0,
    )


def _point_on_line_through_point_parallel_to_line(axis_line, target_point, direction_line):
    """Return the axis point at the target's profile-height station."""

    axis_start = axis_line.startSketchPoint.geometry
    axis_end = axis_line.endSketchPoint.geometry
    target = target_point.geometry
    direction_start = direction_line.startSketchPoint.geometry
    direction_end = direction_line.endSketchPoint.geometry
    point_mm = _PROFILE["point_on_line_through_point_parallel_to_line_mm"](
        (axis_start.x, axis_start.y),
        (axis_end.x, axis_end.y),
        (target.x, target.y),
        (direction_start.x, direction_start.y),
        (direction_end.x, direction_end.y),
    )
    return adsk.core.Point3D.create(point_mm[0], point_mm[1], 0)


def _aligned_dimension_text_point(point_one, point_two, offset_cm=0.45):
    """Place an aligned dimension label beside a point-to-point datum."""

    first = point_one.geometry
    second = point_two.geometry
    dx = second.x - first.x
    dy = second.y - first.y
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        raise RuntimeError("Cannot place an aligned dimension on coincident points.")
    normal_x, normal_y = dy / length, -dx / length
    return adsk.core.Point3D.create(
        (first.x + second.x) / 2 + normal_x * offset_cm,
        (first.y + second.y) / 2 + normal_y * offset_cm,
        0,
    )


def _angle_dimension_text_point(incoming_line, outgoing_line, offset_cm=0.25):
    incoming_start = incoming_line.startSketchPoint.geometry
    incoming_end = incoming_line.endSketchPoint.geometry
    outgoing_start = outgoing_line.startSketchPoint.geometry
    outgoing_end = outgoing_line.endSketchPoint.geometry

    incoming_dx = incoming_end.x - incoming_start.x
    incoming_dy = incoming_end.y - incoming_start.y
    outgoing_dx = outgoing_end.x - outgoing_start.x
    outgoing_dy = outgoing_end.y - outgoing_start.y
    denominator = incoming_dx * outgoing_dy - incoming_dy * outgoing_dx
    if abs(denominator) <= 1e-9:
        raise RuntimeError("Cannot place an angle dimension on parallel OpenLOCK segments.")

    between_x = outgoing_start.x - incoming_start.x
    between_y = outgoing_start.y - incoming_start.y
    along_incoming = (
        between_x * outgoing_dy - between_y * outgoing_dx
    ) / denominator
    vertex_x = incoming_start.x + along_incoming * incoming_dx
    vertex_y = incoming_start.y + along_incoming * incoming_dy

    incoming_length = math.hypot(incoming_start.x - vertex_x, incoming_start.y - vertex_y)
    outgoing_length = math.hypot(outgoing_end.x - vertex_x, outgoing_end.y - vertex_y)
    if incoming_length <= 1e-9 or outgoing_length <= 1e-9:
        raise RuntimeError("Cannot constrain an angle at a zero-length OpenLOCK segment.")

    # The normalized sum bisects the smaller angle between the two rays,
    # selecting the same angular quadrant as the already-created profile.
    bisector_x = (incoming_start.x - vertex_x) / incoming_length
    bisector_x += (outgoing_end.x - vertex_x) / outgoing_length
    bisector_y = (incoming_start.y - vertex_y) / incoming_length
    bisector_y += (outgoing_end.y - vertex_y) / outgoing_length
    bisector_length = math.hypot(bisector_x, bisector_y)
    if bisector_length <= 1e-9:
        raise RuntimeError("Cannot determine the angle quadrant for an OpenLOCK corner.")

    return adsk.core.Point3D.create(
        vertex_x + bisector_x / bisector_length * offset_cm,
        vertex_y + bisector_y / bisector_length * offset_cm,
        0,
    )


def _radial_dimension_text_point(arc, offset_cm=0.25):
    center = arc.centerSketchPoint.geometry
    start = arc.startSketchPoint.geometry
    end = arc.endSketchPoint.geometry
    direction_x = (start.x + end.x) / 2 - center.x
    direction_y = (start.y + end.y) / 2 - center.y
    direction_length = math.hypot(direction_x, direction_y)
    if direction_length <= 1e-9:
        raise RuntimeError("Cannot place a radius dimension on an OpenLOCK fillet.")

    distance = arc.radius + offset_cm
    return adsk.core.Point3D.create(
        center.x + direction_x / direction_length * distance,
        center.y + direction_y / direction_length * distance,
        0,
    )


def _same_curve(curve_one, curve_two):
    return curve_one == curve_two


def _has_tangent_constraint(geometric_constraints, curve_one, curve_two):
    tangent_type = adsk.fusion.TangentConstraint.classType()
    for index in range(geometric_constraints.count):
        constraint = geometric_constraints.item(index)
        if constraint.objectType != tangent_type:
            continue
        existing_one = constraint.curveOne
        existing_two = constraint.curveTwo
        if (
            _same_curve(existing_one, curve_one)
            and _same_curve(existing_two, curve_two)
        ) or (
            _same_curve(existing_one, curve_two)
            and _same_curve(existing_two, curve_one)
        ):
            return True
    return False


def _add_tangent_constraint(geometric_constraints, curve_one, curve_two):
    if _has_tangent_constraint(geometric_constraints, curve_one, curve_two):
        return None
    constraint = geometric_constraints.addTangent(curve_one, curve_two)
    if not constraint:
        raise RuntimeError("Fusion could not add an OpenLOCK fillet tangent constraint.")
    return constraint


def _radial_dimension(arc):
    radial_type = adsk.fusion.SketchRadialDimension.classType()
    for index in range(arc.sketchDimensions.count):
        dimension = arc.sketchDimensions.item(index)
        if dimension.objectType == radial_type:
            return dimension
    return None


def _has_radial_dimension(arc):
    return _radial_dimension(arc) is not None


def _set_dimension_expression(dimension, expression):
    """Set a driving sketch dimension using a unit-qualified Fusion expression."""

    parameter = dimension.parameter
    if not parameter:
        raise RuntimeError("Fusion did not expose a parameter for an OpenLOCK dimension.")
    parameter.expression = expression


def _constrain_profile_shape(
    sketch,
    lines,
    arcs_by_corner,
    reference_line,
    points_mm,
    source_lines=None,
    source_arc_constraints=None,
    source_angle_pairs=None,
    symmetry_pairs=None,
    direction_constraints=None,
    mirror_line=None,
    shoulder_inner_point=None,
    shoulder_inner_width_mm=None,
    alignment_line=None,
    bottom_flat_line=None,
    slot_wall_line=None,
    outer_step_line=None,
    outer_step_height_mm=None,
    shoulder_width_line=None,
    shoulder_width_mm=None,
    outer_step_width_line=None,
    outer_step_width_mm=None,
    top_edge_length_mm=None,
    fillet_radius_mm=None,
    slot_half_width_axis=None,
    slot_half_width_line=None,
    slot_depth_base_line=None,
    slot_depth_ceiling_line=None,
    slope_angle_pair=None,
    overall_height_mm=None,
    bottom_flat_length_mm=None,
    slot_depth_mm=None,
    slot_half_width_mm=None,
    slope_angle_deg=None,
):
    """Constrain independent right-side geometry; the left side follows by symmetry."""

    dimensions = sketch.sketchDimensions
    geometric_constraints = sketch.geometricConstraints
    added_constraints = []
    dimension_lines = source_lines or lines
    arc_constraints = source_arc_constraints
    if arc_constraints is None:
        arc_constraints = [
            (
                arc,
                lines[(corner_index - 1) % len(lines)],
                lines[corner_index],
            )
            for corner_index, arc in arcs_by_corner.items()
        ]
    constrained_arcs = [item[0] for item in arc_constraints]
    angle_pairs = source_angle_pairs
    if angle_pairs is None:
        angle_pairs = [
            (lines[(vertex_index - 1) % len(lines)], lines[vertex_index])
            for vertex_index in range(len(lines) - 3)
        ]

    signed_area = sum(
        points_mm[index][0] * points_mm[(index + 1) % len(points_mm)][1]
        - points_mm[(index + 1) % len(points_mm)][0] * points_mm[index][1]
        for index in range(len(points_mm))
    )
    counter_clockwise = signed_area > 0
    temporary_points = []

    try:
        # Anchor the profile to the selected reference before adding driving
        # dimensions. Adding this after the dimensions lets the solver move
        # the top edge away from the selected reference line.
        alignment_line = alignment_line or max(dimension_lines, key=_line_length_cm)
        collinear_constraint = geometric_constraints.addCollinear(
            alignment_line,
            reference_line,
        )
        if not collinear_constraint:
            raise RuntimeError(
                "Fusion could not align the OpenLOCK profile edge to the reference line."
            )
        added_constraints.append(collinear_constraint)

        if symmetry_pairs:
            for entity_one, entity_two in symmetry_pairs:
                symmetry_constraint = geometric_constraints.addSymmetry(
                    entity_one,
                    entity_two,
                    mirror_line,
                )
                if not symmetry_constraint:
                    raise RuntimeError(
                        "Fusion could not add an OpenLOCK symmetry constraint."
                    )
                added_constraints.append(symmetry_constraint)

        if direction_constraints:
            parallel_method = getattr(geometric_constraints, "addParallel", None)
            if parallel_method:
                for line_one, line_two in direction_constraints.get("parallel", []):
                    try:
                        parallel_constraint = parallel_method(line_one, line_two)
                    except Exception:
                        # The line may already be parallel through a symmetry,
                        # perpendicular, or angular constraint. Fusion reports
                        # that redundant optional constraint as over-constrained;
                        # keep the stronger existing constraint instead.
                        parallel_constraint = None
                    if parallel_constraint:
                        added_constraints.append(parallel_constraint)

            perpendicular_method = getattr(geometric_constraints, "addPerpendicular", None)
            if not perpendicular_method:
                perpendicular_method = getattr(
                    geometric_constraints,
                    "addPerpendicular2",
                    None,
                )
            if perpendicular_method:
                perpendicular_pairs = list(direction_constraints.get("perpendicular", []))
                axis_pair = direction_constraints.get("axis_perpendicular")
                if axis_pair:
                    perpendicular_pairs.append(axis_pair)
                for line_one, line_two in perpendicular_pairs:
                    try:
                        perpendicular_constraint = perpendicular_method(line_one, line_two)
                    except Exception:
                        perpendicular_constraint = None
                    if perpendicular_constraint:
                        added_constraints.append(perpendicular_constraint)

        if (
            mirror_line is not None
            and shoulder_inner_point is not None
            and shoulder_inner_width_mm is not None
        ):
            shoulder_point_geometry = shoulder_inner_point.geometry
            shoulder_axis_point = sketch.sketchPoints.add(
                _point_on_line_through_point_parallel_to_line(
                    mirror_line,
                    shoulder_inner_point,
                    alignment_line,
                )
            )
            if not shoulder_axis_point:
                raise RuntimeError("Fusion could not create the shoulder datum point.")
            temporary_points.append(shoulder_axis_point)

            point_on_axis_constraint = geometric_constraints.addCoincident(
                shoulder_axis_point,
                mirror_line,
            )
            if not point_on_axis_constraint:
                raise RuntimeError("Fusion could not place the shoulder datum on the centerline.")
            added_constraints.append(point_on_axis_constraint)

            shoulder_dimension = dimensions.addDistanceDimension(
                shoulder_axis_point,
                shoulder_inner_point,
                adsk.fusion.DimensionOrientations.AlignedDimensionOrientation,
                _aligned_dimension_text_point(
                    shoulder_axis_point,
                    shoulder_inner_point,
                ),
            )
            if not shoulder_dimension:
                raise RuntimeError(
                    "Fusion could not dimension the OpenLOCK shoulder datum."
                )
            _set_dimension_expression(
                shoulder_dimension,
                "{:.6f} mm".format(shoulder_inner_width_mm),
            )
            added_constraints.append(shoulder_dimension)

        if (
            slot_half_width_axis is not None
            and slot_half_width_line is not None
            and slot_half_width_mm is not None
        ):
            try:
                slot_width_dimension = dimensions.addOffsetDimension(
                    slot_half_width_axis,
                    slot_half_width_line,
                    _offset_dimension_text_point(
                        slot_half_width_axis,
                        slot_half_width_line,
                    ),
                )
            except Exception:
                slot_width_dimension = None
            if not slot_width_dimension:
                raise RuntimeError("Fusion could not dimension the OpenLOCK slot half-width.")
            _set_dimension_expression(
                slot_width_dimension,
                "{:.6f} mm".format(slot_half_width_mm),
            )
            added_constraints.append(slot_width_dimension)

        if (
            slot_depth_base_line is not None
            and slot_depth_ceiling_line is not None
            and slot_depth_mm is not None
        ):
            try:
                slot_depth_dimension = dimensions.addOffsetDimension(
                    slot_depth_base_line,
                    slot_depth_ceiling_line,
                    _offset_dimension_text_point(
                        slot_depth_base_line,
                        slot_depth_ceiling_line,
                    ),
                )
            except Exception:
                slot_depth_dimension = None
            if not slot_depth_dimension:
                raise RuntimeError("Fusion could not dimension the OpenLOCK slot depth.")
            _set_dimension_expression(
                slot_depth_dimension,
                "{:.6f} mm".format(slot_depth_mm),
            )
            added_constraints.append(slot_depth_dimension)

        if (
            bottom_flat_line is not None
            and alignment_line is not None
            and overall_height_mm is not None
        ):
            overall_height_dimension = dimensions.addOffsetDimension(
                bottom_flat_line,
                alignment_line,
                _offset_dimension_text_point(
                    bottom_flat_line,
                    alignment_line,
                ),
            )
            if not overall_height_dimension:
                raise RuntimeError(
                    "Fusion could not dimension the OpenLOCK overall height."
                )
            _set_dimension_expression(
                overall_height_dimension,
                "{:.6f} mm".format(overall_height_mm),
            )
            added_constraints.append(overall_height_dimension)

        # A driving length for each unique independent segment plus Equal
        # constraints for matching independent segments keeps the right side
        # exact. The left side is driven by the symmetry constraints above.
        length_groups = {}
        for line_index, line in enumerate(dimension_lines):
            length_key = round(_line_length_cm(line), 8)
            length_groups.setdefault(length_key, []).append((line_index, line))

        for matching_lines in length_groups.values():
            base_line_index, base_line = matching_lines[0]
            dimension = dimensions.addDistanceDimension(
                base_line.startSketchPoint,
                base_line.endSketchPoint,
                adsk.fusion.DimensionOrientations.AlignedDimensionOrientation,
                _dimension_text_point(base_line, counter_clockwise),
            )
            if not dimension:
                raise RuntimeError("Fusion could not add an OpenLOCK segment length constraint.")
            added_constraints.append(dimension)

            if bottom_flat_length_mm is not None and (
                base_line is bottom_flat_line
                or (bottom_flat_line is None and base_line_index in {0, len(lines) - 4})
            ):
                _set_dimension_expression(
                    dimension,
                    "{:.6f} mm".format(bottom_flat_length_mm),
                )

            if outer_step_height_mm is not None and base_line is outer_step_line:
                _set_dimension_expression(
                    dimension,
                    "{:.6f} mm".format(outer_step_height_mm),
                )

            if shoulder_width_mm is not None and base_line is shoulder_width_line:
                _set_dimension_expression(
                    dimension,
                    "{:.6f} mm".format(shoulder_width_mm),
                )

            if outer_step_width_mm is not None and base_line is outer_step_width_line:
                _set_dimension_expression(
                    dimension,
                    "{:.6f} mm".format(outer_step_width_mm),
                )

            if top_edge_length_mm is not None and base_line is alignment_line:
                _set_dimension_expression(
                    dimension,
                    "{:.6f} mm".format(top_edge_length_mm),
                )

            for _, matching_line in matching_lines[1:]:
                equal_constraint = geometric_constraints.addEqual(base_line, matching_line)
                if not equal_constraint:
                    raise RuntimeError("Fusion could not add an equal-length OpenLOCK constraint.")
                added_constraints.append(equal_constraint)

        # The fillet API may already add a radial dimension. If it did not,
        # dimension one arc and use equal-radius constraints for the others.
        dimensioned_arcs = [arc for arc in constrained_arcs if _has_radial_dimension(arc)]
        base_arc = dimensioned_arcs[0] if dimensioned_arcs else constrained_arcs[0]
        radial_dimension = _radial_dimension(base_arc)
        if radial_dimension is None:
            dimension_text = _radial_dimension_text_point(base_arc)
            try:
                radial_dimension = dimensions.addRadialDimension(base_arc, dimension_text)
            except Exception:
                radial_dimension = None
            if not radial_dimension:
                radial_dimension = dimensions.addRadialDimension(
                    base_arc,
                    dimension_text,
                    False,
                )
            if not radial_dimension:
                raise RuntimeError("Fusion could not dimension an OpenLOCK fillet radius.")
            added_constraints.append(radial_dimension)
        if fillet_radius_mm is not None:
            _set_dimension_expression(
                radial_dimension,
                "{:.6f} mm".format(fillet_radius_mm),
            )

        for arc in constrained_arcs:
            if arc == base_arc or _has_radial_dimension(arc):
                continue
            # addFillet can already determine the radius of this arc.  In
            # that case an additional Equal constraint is redundant and
            # Fusion rejects it as VCS_SKETCH_OVER_CONSTRAINTS.  The base
            # radial dimension remains the required radius driver; this
            # relation is only an optional reinforcement.
            try:
                equal_constraint = geometric_constraints.addEqual(base_arc, arc)
            except Exception:
                equal_constraint = None
            if equal_constraint:
                added_constraints.append(equal_constraint)

        for arc, incoming_line, outgoing_line in arc_constraints:
            for adjacent_line in (incoming_line, outgoing_line):
                tangent_constraint = _add_tangent_constraint(
                    geometric_constraints,
                    arc,
                    adjacent_line,
                )
                if tangent_constraint:
                    added_constraints.append(tangent_constraint)

        # Keep the independent angles from the right side. At a rounded
        # vertex, the angle is applied to the supporting lines while the arc is
        # held by its radius and two tangent constraints. Mirrored angles are
        # implied by the symmetry constraints and are deliberately omitted.
        for incoming_line, outgoing_line in angle_pairs:
            is_required_slope_angle = (
                slope_angle_pair is not None
                and incoming_line is slope_angle_pair[0]
                and outgoing_line is slope_angle_pair[1]
            )
            previous_start = incoming_line.startSketchPoint.geometry
            incoming_end = incoming_line.endSketchPoint.geometry
            outgoing_start = outgoing_line.startSketchPoint.geometry
            next_end = outgoing_line.endSketchPoint.geometry
            ax, ay = previous_start.x - incoming_end.x, previous_start.y - incoming_end.y
            bx, by = next_end.x - outgoing_start.x, next_end.y - outgoing_start.y
            a_length = math.hypot(ax, ay)
            b_length = math.hypot(bx, by)
            cosine = (ax * bx + ay * by) / (a_length * b_length)
            corner_angle = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))

            if abs(corner_angle - 90.0) <= 1e-5:
                perpendicular_method = getattr(
                    geometric_constraints,
                    "addPerpendicular2",
                    None,
                ) or getattr(geometric_constraints, "addPerpendicular", None)
                try:
                    angle_constraint = (
                        perpendicular_method(incoming_line, outgoing_line)
                        if perpendicular_method
                        else None
                    )
                except Exception:
                    if is_required_slope_angle:
                        raise RuntimeError(
                            "Fusion could not add the required 135 degree OpenLOCK angle."
                        )
                    angle_constraint = None
            else:
                try:
                    angle_constraint = dimensions.addAngularDimension(
                        incoming_line,
                        outgoing_line,
                        _angle_dimension_text_point(incoming_line, outgoing_line),
                    )
                except Exception as error:
                    # An angle can already be implied by the symmetry and
                    # direction constraints. Fusion rejects that redundant
                    # dimension as over-constrained; keep the existing relation
                    # for optional angles only. The 135 degree slope is a
                    # required design dimension and must not silently degrade
                    # to a nearby solved value such as 135.1 degrees.
                    if is_required_slope_angle:
                        raise RuntimeError(
                            "Fusion could not add the required 135 degree OpenLOCK angle."
                        ) from error
                    angle_constraint = None
            if not angle_constraint:
                if is_required_slope_angle:
                    raise RuntimeError(
                        "Fusion did not create the required 135 degree OpenLOCK angle."
                    )
                continue
            if is_required_slope_angle and slope_angle_deg is not None:
                _set_dimension_expression(
                    angle_constraint,
                    "{:.6f} deg".format(slope_angle_deg),
                )
            added_constraints.append(angle_constraint)

        return added_constraints
    except Exception:
        for constraint in reversed(added_constraints):
            try:
                if constraint.isValid and constraint.isDeletable:
                    constraint.deleteMe()
            except Exception:
                pass
        for point in reversed(temporary_points):
            try:
                if point.isValid and point.isDeletable:
                    point.deleteMe()
            except Exception:
                pass
        raise


def _point3d_mm(point_mm):
    return adsk.core.Point3D.create(
        point_mm[0] * MM_TO_CM,
        point_mm[1] * MM_TO_CM,
        0,
    )


def _write_profile_attributes(
    curves,
    origin_mm,
    rotation_deg,
    flip,
    profile_name,
    profile_version,
    alignment_mode="referenceEdge",
    offset_mm=0.0,
):
    profile_id = uuid.uuid4().hex
    alignment_curve_index = max(
        range(len(curves)),
        key=lambda index: math.hypot(
            curves[index].endSketchPoint.geometry.x
            - curves[index].startSketchPoint.geometry.x,
            curves[index].endSketchPoint.geometry.y
            - curves[index].startSketchPoint.geometry.y,
        ),
    )
    for index, curve in enumerate(curves):
        attributes = curve.attributes
        attributes.add(ATTRIBUTE_GROUP, "profileId", profile_id)
        attributes.add(ATTRIBUTE_GROUP, "clipType", profile_name)
        attributes.add(ATTRIBUTE_GROUP, "profileVersion", profile_version)
        attributes.add(
            ATTRIBUTE_GROUP,
            "profileSource",
            "User dimensioned screenshot dated 2026-09-19; interpretation not officially verified",
        )
        attributes.add(ATTRIBUTE_GROUP, "placementPointMm", "{:.4f}, {:.4f}".format(*origin_mm))
        attributes.add(ATTRIBUTE_GROUP, "rotationDeg", "{:.4f}".format(rotation_deg))
        attributes.add(ATTRIBUTE_GROUP, "flip", "true" if flip else "false")
        attributes.add(ATTRIBUTE_GROUP, "alignmentMode", alignment_mode)
        attributes.add(ATTRIBUTE_GROUP, "offsetMm", "{:.4f}".format(offset_mm))
        attributes.add(
            ATTRIBUTE_GROUP,
            "alignmentLine",
            "true" if index == alignment_curve_index else "false",
        )
