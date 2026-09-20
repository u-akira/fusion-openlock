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
