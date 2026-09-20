"""Load the add-in's profile source without Python's module cache."""

import os


def load_profile_namespace(addin_dir):
    """Execute the current profile source from this add-in directory."""

    profile_path = os.path.join(addin_dir, "openlock_profile.py")
    with open(profile_path, "r", encoding="utf-8") as profile_file:
        source = profile_file.read()

    namespace = {
        "__file__": profile_path,
        "__name__": "_fusion_openlock_profile",
    }
    exec(compile(source, profile_path, "exec"), namespace)
    return namespace
