"""Shared inclusion rules for plugin and standalone skill packages."""

IGNORED = {".git", "__pycache__", ".venv", ".DS_Store", ".env"}


def ignore_files(directory, names):
    return [name for name in names if name in IGNORED or name.startswith(".env.") or name.endswith((".pyc", ".pyo", ".local.json"))]


def include_file(relative):
    return all(not ignore_files("", [part]) for part in relative.parts)
