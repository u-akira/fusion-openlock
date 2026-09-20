import os
import sys
import unittest
from math import atan2, degrees

# The profile module ships inside the installable Fusion add-in folder.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "OpenLOCKHole")))

from openlock_profile import (
    basic_openlock_profile_mm,
    profile_bounds_mm,
    transform_profile_mm,
    transform_profile_to_reference_mm,
)


class OpenLockProfileTests(unittest.TestCase):
    def test_profile_matches_dimensioned_reference_bounds(self):
        points = basic_openlock_profile_mm()
        min_x, min_y, max_x, max_y = profile_bounds_mm(points)

        self.assertAlmostEqual(max_x - min_x, 13.80)
        self.assertAlmostEqual(max_y - min_y, 9.80)
        self.assertNotEqual(points[0], points[-1])

    def test_profile_has_wide_end_at_top_like_reference(self):
        points = basic_openlock_profile_mm()
        top_half_width = max(abs(x) for x, y in points if abs(y - 9.80) < 1e-6)
        bottom_half_width = max(abs(x) for x, y in points if abs(y) < 1e-6)

        self.assertAlmostEqual(top_half_width, 6.90)
        self.assertLess(bottom_half_width, 6.90)

    def test_13_8_mm_alignment_edge_is_the_longest_profile_edge(self):
        points = basic_openlock_profile_mm()
        edge_lengths = [
            ((points[(index + 1) % len(points)][0] - point[0]) ** 2
             + (points[(index + 1) % len(points)][1] - point[1]) ** 2) ** 0.5
            for index, point in enumerate(points)
        ]

        self.assertAlmostEqual(max(edge_lengths), 13.8)

    def test_profile_matches_all_dimensioned_reference_stations(self):
        points = basic_openlock_profile_mm()
        point_set = {(round(x, 6), round(y, 6)) for x, y in points}

        for expected in {
            (-0.8, 0.0), (0.8, 0.0), (-0.8, 7.2), (0.8, 7.2),
            (-3.77, 0.0), (3.77, 0.0), (-5.76, 2.0), (5.76, 2.0),
            (-5.76, 4.3), (5.76, 4.3), (-4.76, 4.3), (4.76, 4.3),
            (-5.9, 7.8), (5.9, 7.8), (-6.9, 7.8), (6.9, 7.8),
            (-6.9, 9.8), (6.9, 9.8),
        }:
            self.assertIn(expected, point_set)

        normalized_points = [(round(x, 6), round(y, 6)) for x, y in points]
        edges = set(zip(normalized_points, normalized_points[1:] + normalized_points[:1]))
        normalized_edges = {frozenset(edge) for edge in edges}
        self.assertNotIn(frozenset({(-0.8, 0.0), (0.8, 0.0)}), normalized_edges)
        self.assertIn(frozenset({(-0.8, 7.2), (0.8, 7.2)}), normalized_edges)
        self.assertIn(frozenset({(-6.9, 9.8), (6.9, 9.8)}), normalized_edges)
        self.assertIn(frozenset({(0.8, 0.0), (0.8, 7.2)}), normalized_edges)
        self.assertIn(frozenset({(0.8, 0.0), (3.77, 0.0)}), normalized_edges)
        self.assertIn(frozenset({(3.77, 0.0), (5.76, 2.0)}), normalized_edges)
        self.assertIn(frozenset({(5.76, 2.0), (5.76, 4.3)}), normalized_edges)
        self.assertIn(frozenset({(4.76, 4.3), (5.76, 4.3)}), normalized_edges)
        self.assertIn(frozenset({(4.76, 4.3), (5.9, 7.8)}), normalized_edges)
        self.assertIn(frozenset({(5.9, 7.8), (6.9, 7.8)}), normalized_edges)
        self.assertIn(frozenset({(6.9, 7.8), (6.9, 9.8)}), normalized_edges)

        self.assertAlmostEqual(0.8 * 2.0, 1.6)
        self.assertAlmostEqual(3.77 - 0.8, 2.97)
        self.assertAlmostEqual(5.76 - 4.76, 1.0)
        self.assertAlmostEqual(9.8 - 4.3, 5.5)
        self.assertAlmostEqual(9.8 - 7.8, 2.0)
        self.assertAlmostEqual(7.2 - 0.0, 7.2)
        self.assertAlmostEqual(
            135.0,
            180.0 - degrees(atan2(2.0, 5.76 - 3.77)),
            places=0,
        )

    def test_profile_is_symmetric(self):
        points = basic_openlock_profile_mm()
        point_set = {(round(x, 6), round(y, 6)) for x, y in points}
        mirrored = {(-x, y) for x, y in point_set}
        self.assertEqual(point_set, mirrored)

    def test_rotation_90_degrees(self):
        transformed = transform_profile_mm([(1.0, 0.0)], rotation_deg=90.0)
        self.assertAlmostEqual(transformed[0][0], 0.0, places=6)
        self.assertAlmostEqual(transformed[0][1], 1.0, places=6)

    def test_flip_is_reflection_about_x_axis(self):
        transformed = transform_profile_mm([(1.0, 2.0)], flip=True)
        self.assertAlmostEqual(transformed[0][0], 1.0, places=6)
        self.assertAlmostEqual(transformed[0][1], -2.0, places=6)

    def test_reference_edge_midpoint_aligns_with_profile_top_edge_midpoint(self):
        profile = basic_openlock_profile_mm()
        start = (2.0, 3.0)
        end = (2.0, 13.0)

        transformed = transform_profile_to_reference_mm(profile, start, end)

        top_edge_midpoint = (
            (transformed[7][0] + transformed[8][0]) / 2,
            (transformed[7][1] + transformed[8][1]) / 2,
        )
        self.assertAlmostEqual(top_edge_midpoint[0], 2.0, places=6)
        self.assertAlmostEqual(top_edge_midpoint[1], 8.0, places=6)
        self.assertAlmostEqual(transformed[8][0] - transformed[7][0], 0.0, places=6)
        self.assertAlmostEqual(abs(transformed[8][1] - transformed[7][1]), 13.8, places=6)

    def test_reference_flip_mirrors_profile_across_selected_edge(self):
        profile = basic_openlock_profile_mm()
        start = (-4.0, 1.0)
        end = (6.0, 1.0)

        normal = transform_profile_to_reference_mm(profile, start, end)
        flipped = transform_profile_to_reference_mm(profile, start, end, flip=True)

        # The profile's wide 13.8 mm edge remains on the datum line. The
        # rest of the shape changes sides when flipped.
        self.assertAlmostEqual((normal[7][1] + normal[8][1]) / 2, 1.0, places=6)
        self.assertAlmostEqual((flipped[7][1] + flipped[8][1]) / 2, 1.0, places=6)
        self.assertLess(normal[16][1], 1.0)
        self.assertGreater(flipped[16][1], 1.0)

    def test_reference_offset_moves_profile_perpendicular_without_changing_shape(self):
        profile = basic_openlock_profile_mm()
        start = (1.0, 2.0)
        end = (7.0, 10.0)

        aligned = transform_profile_to_reference_mm(profile, start, end)
        offset = transform_profile_to_reference_mm(profile, start, end, offset_mm=2.5)

        self.assertAlmostEqual(offset[7][0] - aligned[7][0], -2.0, places=6)
        self.assertAlmostEqual(offset[7][1] - aligned[7][1], 1.5, places=6)
        for before, after in zip(aligned, offset):
            self.assertAlmostEqual(after[0] - before[0], -2.0, places=6)
            self.assertAlmostEqual(after[1] - before[1], 1.5, places=6)

    def test_zero_length_reference_edge_is_rejected(self):
        with self.assertRaises(ValueError):
            transform_profile_to_reference_mm(
                basic_openlock_profile_mm(), (1.0, 2.0), (1.0, 2.0)
            )


if __name__ == "__main__":
    unittest.main()
