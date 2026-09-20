import os
import sys
import unittest

ADDIN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "OpenLOCKHole")
)
sys.path.insert(0, ADDIN_DIR)

from profile_loader import load_profile_namespace


class ProfileLoaderTests(unittest.TestCase):
    def test_each_load_reads_fresh_profile_source(self):
        first = load_profile_namespace(ADDIN_DIR)
        first["PROFILE_VERSION"] = "stale cached version"
        first["BASIC_OPENLOCK_DIMENSIONS_MM"]["center_slot_half_width"] = 0.4

        second = load_profile_namespace(ADDIN_DIR)

        self.assertIsNot(first, second)
        self.assertEqual(second["PROFILE_VERSION"], "OpenLOCK Clips v5.5")
        self.assertEqual(
            second["BASIC_OPENLOCK_DIMENSIONS_MM"]["center_slot_half_width"],
            0.8,
        )


if __name__ == "__main__":
    unittest.main()
