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

            created_lines, created_arcs = _add_closed_profile(
                sketch,
                points_mm,
                profile["BASIC_OPENLOCK_DIMENSIONS_MM"]["center_slot_corner_fillet_radius"],
                profile["BASIC_OPENLOCK_FILLET_CORNER_INDICES"],
            )
            try:
                _constrain_profile_shape(
                    sketch,
                    created_lines,
                    created_arcs,
                    reference_line,
                    points_mm,
                )
                _write_profile_attributes(
                    created_lines + list(created_arcs.values()),
                    origin_mm,
                    rotation_deg,
                    flip,
                    profile["PROFILE_NAME"],
                    profile["PROFILE_VERSION"],
                    alignment_mode="referenceEdge",
                    offset_mm=offset_mm,
                )
            except Exception:
                _delete_profile_geometry(created_lines, created_arcs)
                raise

        except Exception:
            if _ui:
                _ui.messageBox("Could not create OpenLOCK sketch:\n{}".format(traceback.format_exc()))


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

            profile, points_mm, _, _, _, _, _ = _reference_profile_placement(sketch, inputs)
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
    lines = sketch.sketchCurves.sketchLines
    created_lines = []
    created_arcs = {}
    first_line = None
    previous_end = None

    try:
        for index in range(len(points_mm)):
            start_mm = points_mm[index]
            end_mm = points_mm[(index + 1) % len(points_mm)]
            start = previous_end or _point3d_mm(start_mm)
            end = first_line.startSketchPoint if index == len(points_mm) - 1 else _point3d_mm(end_mm)
            line = lines.addByTwoPoints(start, end)
            if not line:
                raise RuntimeError("Fusion could not create a profile segment.")
            created_lines.append(line)
            if first_line is None:
                first_line = line
            previous_end = line.endSketchPoint

        arcs = sketch.sketchCurves.sketchArcs
        radius_cm = fillet_radius_mm * MM_TO_CM
        for corner_index in fillet_corner_indices:
            incoming_line = created_lines[(corner_index - 1) % len(created_lines)]
            outgoing_line = created_lines[corner_index]
            arc = arcs.addFillet(
                incoming_line,
                incoming_line.endSketchPoint.geometry,
                outgoing_line,
                outgoing_line.startSketchPoint.geometry,
                radius_cm,
            )
            if not arc:
                raise RuntimeError("Fusion could not create an OpenLOCK sketch fillet.")
            created_arcs[corner_index] = arc
    except Exception:
        _delete_profile_geometry(created_lines, created_arcs)
        raise

    return created_lines, created_arcs


def _delete_profile_geometry(lines, arcs_by_corner):
    """Delete added arcs before their trimmed profile lines."""

    for arc in reversed(list(arcs_by_corner.values())):
        if arc.isValid and arc.isDeletable:
            arc.deleteMe()
    for line in reversed(lines):
        if line.isValid and line.isDeletable:
            line.deleteMe()


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


def _has_radial_dimension(arc):
    radial_type = adsk.fusion.SketchRadialDimension.classType()
    for index in range(arc.sketchDimensions.count):
        if arc.sketchDimensions.item(index).objectType == radial_type:
            return True
    return False


def _constrain_profile_shape(sketch, lines, arcs_by_corner, reference_line, points_mm):
    """Constrain lengths, fillet radii, tangencies, and angles; allow baseline sliding."""

    dimensions = sketch.sketchDimensions
    geometric_constraints = sketch.geometricConstraints
    added_constraints = []

    signed_area = sum(
        points_mm[index][0] * points_mm[(index + 1) % len(points_mm)][1]
        - points_mm[(index + 1) % len(points_mm)][0] * points_mm[index][1]
        for index in range(len(points_mm))
    )
    counter_clockwise = signed_area > 0

    try:
        # A driving length for each unique segment size plus Equal constraints
        # for matching segments keeps the outline exact without dimensioning
        # both mirrored sides independently.
        length_groups = {}
        for line in lines:
            length_key = round(_line_length_cm(line), 8)
            length_groups.setdefault(length_key, []).append(line)

        for matching_lines in length_groups.values():
            base_line = matching_lines[0]
            dimension = dimensions.addDistanceDimension(
                base_line.startSketchPoint,
                base_line.endSketchPoint,
                adsk.fusion.DimensionOrientations.AlignedDimensionOrientation,
                _dimension_text_point(base_line, counter_clockwise),
            )
            if not dimension:
                raise RuntimeError("Fusion could not add an OpenLOCK segment length constraint.")
            added_constraints.append(dimension)

            for matching_line in matching_lines[1:]:
                equal_constraint = geometric_constraints.addEqual(base_line, matching_line)
                if not equal_constraint:
                    raise RuntimeError("Fusion could not add an equal-length OpenLOCK constraint.")
                added_constraints.append(equal_constraint)

        # The fillet API may already add a radial dimension. If it did not,
        # dimension one arc and use equal-radius constraints for the others.
        dimensioned_arcs = [arc for arc in arcs_by_corner.values() if _has_radial_dimension(arc)]
        base_arc = dimensioned_arcs[0] if dimensioned_arcs else next(iter(arcs_by_corner.values()))
        if not _has_radial_dimension(base_arc):
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

        for arc in arcs_by_corner.values():
            if arc == base_arc or _has_radial_dimension(arc):
                continue
            equal_constraint = geometric_constraints.addEqual(base_arc, arc)
            if not equal_constraint:
                raise RuntimeError("Fusion could not add equal-radius OpenLOCK constraints.")
            added_constraints.append(equal_constraint)

        for corner_index, arc in arcs_by_corner.items():
            incoming_line = lines[(corner_index - 1) % len(lines)]
            outgoing_line = lines[corner_index]
            for adjacent_line in (incoming_line, outgoing_line):
                tangent_constraint = _add_tangent_constraint(
                    geometric_constraints,
                    arc,
                    adjacent_line,
                )
                if tangent_constraint:
                    added_constraints.append(tangent_constraint)

        # Keep the independent angles from the original polygon. At a rounded
        # vertex, the angle is applied to the supporting lines while the arc is
        # held by its radius and two tangent constraints.
        for vertex_index in range(len(lines) - 3):
            incoming_line = lines[(vertex_index - 1) % len(lines)]
            outgoing_line = lines[vertex_index]
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
                if not perpendicular_method:
                    raise RuntimeError("Fusion does not expose a perpendicular sketch constraint.")
                angle_constraint = perpendicular_method(incoming_line, outgoing_line)
            else:
                angle_constraint = dimensions.addAngularDimension(
                    incoming_line,
                    outgoing_line,
                    _angle_dimension_text_point(incoming_line, outgoing_line),
                )
            if not angle_constraint:
                raise RuntimeError("Fusion could not constrain an OpenLOCK corner angle.")
            added_constraints.append(angle_constraint)

        alignment_line = max(lines, key=_line_length_cm)
        collinear_constraint = geometric_constraints.addCollinear(
            alignment_line,
            reference_line,
        )
        if not collinear_constraint:
            raise RuntimeError("Fusion could not align the OpenLOCK profile edge to the reference line.")
        added_constraints.append(collinear_constraint)
        return added_constraints
    except Exception:
        for constraint in reversed(added_constraints):
            try:
                if constraint.isValid and constraint.isDeletable:
                    constraint.deleteMe()
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
