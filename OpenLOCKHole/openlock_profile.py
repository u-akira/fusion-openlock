"""OpenLOCK profile definitions.

This module intentionally contains no Fusion 360 imports.  Keeping the profile
data independent makes it possible to verify the dimensions and transforms in
ordinary Python tests and makes future OpenLOCK versions easier to add.
"""

from __future__ import annotations

from math import atan2, cos, degrees, hypot, radians, sin


PROFILE_VERSION = "OpenLOCK Clips v5.5"
PROFILE_NAME = "Basic OpenLOCK"

# Dimensions transcribed from the user's dimensioned reference screenshot.
# They are not yet verified against an official v5.5 drawing or source CAD.
BASIC_OPENLOCK_DIMENSIONS_MM = {
    "overall_height": 9.80,
    "outer_half_width": 6.90,
    "top_edge_length": 13.80,
    "outer_step_height": 2.00,
    "outer_step_width": 1.00,
    "height_from_top_to_shoulder": 5.50,
    "shoulder_inner_half_width": 4.76,
    "shoulder_width": 1.00,
    "lower_step_height": 2.00,
    "bottom_flat_from_slot_wall": 2.97,
    "center_slot_half_width": 0.80,
    "center_slot_depth_from_bottom": 7.20,
    "center_slot_corner_fillet_radius": 0.50,
    "slope_angle": 135.0,
}

# Vertex indices in basic_openlock_profile_mm() for the two slot-mouth and
# two slot-ceiling corners, following the polygon's point order.
BASIC_OPENLOCK_FILLET_CORNER_INDICES = (0, 15, 16, 17)


def reference_selection_is_ready(selection_count):
    """Return whether the command has exactly one reference edge selected."""

    return selection_count == 1


def basic_openlock_constraint_plan():
    """Return the stable sketch-constraint topology for the basic profile.

    The plan deliberately keeps the original full top edge and slot ceiling
    as single lines. Fusion applies symmetry to corresponding entities rather
    than creating a half-length mirror layout, so existing dimensions remain
    attached to the same geometric targets.
    """

    return {
        "symmetry_line_pairs": (
            (0, 14),
            (1, 13),
            (2, 12),
            (3, 11),
            (4, 10),
            (5, 9),
            (6, 8),
            (15, 17),
        ),
        "centered_line_indices": (7, 16),
        # Line 2 is the 2.30 mm vertical shoulder segment. Its length is
        # intentionally left solver-driven; the shoulder position is driven
        # by the 4.76 mm horizontal datum below instead.
        # Line 1 is the lower diagonal. Its length must remain solver-driven
        # so the required 135 degree corner and 4.76 mm shoulder datum take
        # priority instead of competing with a second driving target.
        # Line 17 is the slot wall. Both ends are filleted, so its visible
        # segment is 6.20 mm even though the slot depth is 7.20 mm. The depth
        # is driven by the offset between the bottom and ceiling instead.
        "independent_line_indices": (0, 3, 4, 5, 6, 7),
        "shoulder_inner_point_line_index": 4,
        "outer_step_line_index": 6,
        "shoulder_width_line_index": 3,
        "outer_step_width_line_index": 5,
        "priority_angle_pair": (0, 1),
        "angle_pairs": ((0, 1), (3, 4)),
        "parallel_line_indices": (0, 3, 5, 16),
        "perpendicular_line_indices": (2, 6, 17),
    }


def audit_basic_openlock_constraint_plan():
    """Check the planned dimension and relation targets for duplicates.

    This is a solver-independent audit. It cannot reproduce Fusion's VCS
    solver, but it catches duplicate target registrations before geometry is
    created, such as assigning two length dimensions to the same line or
    registering the same relation pair twice.
    """

    plan = basic_openlock_constraint_plan()
    dimension_targets = [
        ("segment_length", ("line", index))
        for index in plan["independent_line_indices"]
    ]
    dimension_targets.extend(
        [
            ("shoulder_inner_half_width", ("axis", "point", 4)),
            ("slot_half_width", ("axis", "line", 17)),
            ("slot_depth", ("line_pair", 0, 16)),
            ("overall_height", ("line_pair", 0, 7)),
            ("fillet_radius", ("arc", 0)),
        ]
    )

    relation_targets = []
    relation_targets.extend(
        ("symmetry", tuple(sorted(pair)))
        for pair in plan["symmetry_line_pairs"]
    )
    relation_targets.extend(
        ("centered", (index, "mirror_axis"))
        for index in plan["centered_line_indices"]
    )
    relation_targets.extend(
        ("parallel", tuple(sorted((index, 7))))
        for index in plan["parallel_line_indices"]
    )
    relation_targets.extend(
        ("perpendicular", tuple(sorted((index, 7))))
        for index in plan["perpendicular_line_indices"]
    )
    relation_targets.extend(
        ("angle", tuple(pair))
        for pair in plan["angle_pairs"]
    )

    priority_angle_conflicts = []
    priority_angle_pair = plan["priority_angle_pair"]
    # Line 0 may retain its bottom-flat dimension, but line 1 must not have
    # its own driving length dimension: the 135 degree angle is the priority
    # driver for this corner.
    if priority_angle_pair[1] in plan["independent_line_indices"]:
        priority_angle_conflicts.append(
            ("required_angle", ("line", priority_angle_pair[1]))
        )

    duplicate_targets = []
    for category, targets in (
        ("dimension", dimension_targets),
        ("relation", relation_targets),
    ):
        seen = set()
        for target in targets:
            if target in seen:
                duplicate_targets.append((category, target))
            seen.add(target)

    return {
        "valid": not duplicate_targets and not priority_angle_conflicts,
        "duplicate_targets": tuple(duplicate_targets),
        "priority_angle_conflicts": tuple(priority_angle_conflicts),
        "dimension_targets": tuple(dimension_targets),
        "relation_targets": tuple(relation_targets),
    }


def point_on_line_through_point_parallel_to_line_mm(
    axis_start_mm,
    axis_end_mm,
    target_point_mm,
    direction_start_mm,
    direction_end_mm,
):
    """Find the axis point on a line through the target parallel to a datum."""

    axis_x, axis_y = axis_start_mm
    axis_dx = axis_end_mm[0] - axis_x
    axis_dy = axis_end_mm[1] - axis_y
    direction_dx = direction_end_mm[0] - direction_start_mm[0]
    direction_dy = direction_end_mm[1] - direction_start_mm[1]
    denominator = axis_dx * direction_dy - axis_dy * direction_dx
    if abs(denominator) <= 1e-9:
        raise ValueError("The axis and datum direction must not be parallel.")

    target_dx = target_point_mm[0] - axis_x
    target_dy = target_point_mm[1] - axis_y
    axis_parameter = (
        target_dx * direction_dy - target_dy * direction_dx
    ) / denominator
    return (
        axis_x + axis_parameter * axis_dx,
        axis_y + axis_parameter * axis_dy,
    )


def basic_openlock_profile_mm():
    """Return the nominal sharp-corner Basic OpenLOCK outline in mm.

    The profile is a single concave polygon, symmetric about the vertical
    centerline. The central slot opens from the bottom and is 1.6 mm wide by
    7.20 mm deep. The profile preserves the measured 4.76 mm shelf position,
    5.50 mm top-to-shelf height, 1.00 mm shoulder, and the two distinct
    diagonal transitions visible in the reference drawing. The Fusion add-in
    replaces the four listed central-slot corners with R0.50 mm sketch arcs.
    """

    d = BASIC_OPENLOCK_DIMENSIONS_MM
    outer = d["outer_half_width"]
    top_y = d["overall_height"]
    slot_half_width = d["center_slot_half_width"]
    slot_floor_y = d["center_slot_depth_from_bottom"]
    outer_inset_x = outer - d["outer_step_width"]
    shoulder_y = top_y - d["height_from_top_to_shoulder"]
    shoulder_inner_x = d["shoulder_inner_half_width"]
    shoulder_outer_x = shoulder_inner_x + d["shoulder_width"]
    lower_step_y = d["lower_step_height"]
    bottom_ramp_start_x = slot_half_width + d["bottom_flat_from_slot_wall"]
    outer_step_y = top_y - d["outer_step_height"]

    # Walk the outline clockwise from the right edge of the bottom-open slot. The
    # first and last points are intentionally not duplicated.
    return [
        (slot_half_width, 0.0),
        (bottom_ramp_start_x, 0.0),
        (shoulder_outer_x, lower_step_y),
        (shoulder_outer_x, shoulder_y),
        (shoulder_inner_x, shoulder_y),
        (outer_inset_x, outer_step_y),
        (outer, outer_step_y),
        (outer, top_y),
        (-outer, top_y),
        (-outer, outer_step_y),
        (-outer_inset_x, outer_step_y),
        (-shoulder_inner_x, shoulder_y),
        (-shoulder_outer_x, shoulder_y),
        (-shoulder_outer_x, lower_step_y),
        (-bottom_ramp_start_x, 0.0),
        (-slot_half_width, 0.0),
        (-slot_half_width, slot_floor_y),
        (slot_half_width, slot_floor_y),
    ]


def transform_profile_mm(
    points,
    origin_mm=(0.0, 0.0),
    rotation_deg=0.0,
    flip=False,
    anchor_mm=(0.0, 0.0),
    normal_offset_mm=0.0,
):
    """Transform local points around an anchor and offset along its local normal.

    ``anchor_mm`` is the local profile point placed at ``origin_mm``. A normal
    offset is applied after mirroring, so the datum itself remains fixed by the
    flip operation and the entire profile is translated rigidly from it.
    """

    origin_x, origin_y = origin_mm
    anchor_x, anchor_y = anchor_mm
    angle = radians(rotation_deg)
    c = cos(angle)
    s = sin(angle)
    offset_origin_x = origin_x - normal_offset_mm * s
    offset_origin_y = origin_y + normal_offset_mm * c
    transformed = []

    for x, y in points:
        x -= anchor_x
        y -= anchor_y
        if flip:
            y = -y
        transformed.append(
            (
                offset_origin_x + (x * c - y * s),
                offset_origin_y + (x * s + y * c),
            )
        )

    return transformed


def transform_profile_to_reference_mm(
    points,
    reference_start_mm,
    reference_end_mm,
    flip=False,
    offset_mm=0.0,
):
    """Align the profile's 13.8 mm top edge midpoint to a reference line.

    The reference line's direction determines the profile rotation. ``flip``
    mirrors the profile across that line, while ``offset_mm`` translates the
    whole profile along the line's normal without changing its shape.
    """

    start_x, start_y = reference_start_mm
    end_x, end_y = reference_end_mm
    dx = end_x - start_x
    dy = end_y - start_y
    if hypot(dx, dy) <= 1e-9:
        raise ValueError("The selected reference edge must have non-zero length.")

    profile_top_y = BASIC_OPENLOCK_DIMENSIONS_MM["overall_height"]
    return transform_profile_mm(
        points,
        origin_mm=((start_x + end_x) / 2, (start_y + end_y) / 2),
        rotation_deg=degrees(atan2(dy, dx)),
        flip=flip,
        anchor_mm=(0.0, profile_top_y),
        normal_offset_mm=offset_mm,
    )


def profile_bounds_mm(points):
    """Return ``(min_x, min_y, max_x, max_y)`` for a point collection."""

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)
