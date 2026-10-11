#!/usr/bin/env python3
"""Build self-contained plugins from references to the shared skills directory.

Copyright (C) 2026 luckygopher
SPDX-License-Identifier: GPL-3.0-only
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from build_native import prepare_runtime, package_standalone
from distribution_files import ignore_files


ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"[a-z0-9][a-z0-9-]*\Z")


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def validate_tree(path: Path) -> None:
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ValueError(f"Symbolic links cannot be bundled: {item}")


def build_plugin(root: Path, name: str, *, native: bool = True, host_only: bool = False) -> Path:
    root = root.resolve()
    if (root / "dist").is_symlink():
        raise ValueError("Build output must not be a symbolic link.")
    if not NAME.fullmatch(name):
        raise ValueError(f"Invalid plugin name: {name}")
    source = root / "plugins" / name
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"Plugin not found: {name}")
    manifest = read_json(source / "plugin.json")
    if manifest.get("name") != name:
        raise ValueError(f"Plugin manifest name must match its directory: {name}")
    version = manifest.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?", version):
        raise ValueError(f"Invalid plugin version: {name}")
    references = read_json(source / "skills.json").get("skills")
    if not isinstance(references, list) or not references or any(not isinstance(ref, str) or not NAME.fullmatch(ref) for ref in references):
        raise ValueError(f"skills.json must contain a nonempty list of skill names: {name}")
    if len(set(references)) != len(references):
        raise ValueError(f"Duplicate skill references: {name}")
    validate_tree(source)
    resolved = []
    for ref in references:
        skill = root / "skills" / ref
        if skill.is_symlink() or not (skill / "SKILL.md").is_file():
            raise ValueError(f"Referenced skill is missing: {ref}")
        validate_tree(skill)
        if native and (skill / "native-runtime.json").exists():
            (root / "dist").mkdir(exist_ok=True)
            prepare_runtime(root, skill, host_only=host_only)
            version_file = skill / "scripts/runtime-version"
            package_standalone(root, skill, version_file.read_text().strip())
        resolved.append(skill)
    output = root / "dist" / "plugins"
    if (root / "dist").is_symlink() or output.is_symlink():
        raise ValueError("Build output directories must not be symbolic links.")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / name
    if destination.is_symlink():
        raise ValueError("Build destination must not be a symbolic link.")
    with tempfile.TemporaryDirectory(prefix=f".build-{name}-", dir=output) as temporary:
        package = Path(temporary) / name
        shutil.copytree(source, package, ignore=ignore_files)
        (package / "skills.json").unlink()
        if (package / "skills").exists():
            raise ValueError("Do not maintain bundled skills in plugins/. Put their source in skills/ and declare references in skills.json.")
        for skill in resolved:
            shutil.copytree(skill, package / "skills" / skill.name, ignore=ignore_files)
        shutil.copy2(root / "LICENSE", package / "LICENSE")
        if destination.exists():
            if not destination.is_dir():
                raise ValueError(f"Build destination is not a directory: {destination}")
            shutil.rmtree(destination)
        package.rename(destination)
    archives = root / "dist" / "archives"
    archives.mkdir(parents=True, exist_ok=True)
    archive = archives / f"{name}-{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for item in sorted(destination.rglob("*")):
            if item.is_file():
                bundle.write(item, item.relative_to(destination).as_posix())
    return destination


def write_marketplace(root: Path, built_names: list[str]) -> None:
    root = root.resolve()
    catalog = read_json(root / ".agents/plugins/marketplace.json")
    entries = []
    for entry in catalog.get("plugins", []):
        if entry.get("name") in built_names:
            entries.append({**entry, "source": {"source": "local", "path": f"./plugins/{entry['name']}"}})
    if {entry["name"] for entry in entries} != set(built_names):
        raise ValueError("Every built plugin must have an entry in .agents/plugins/marketplace.json.")
    destination = root / "dist/.agents/plugins/marketplace.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({**catalog, "plugins": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(root: Path, plugins: list[str] | None = None, *, native: bool = True, host_only: bool = False) -> list[Path]:
    names = plugins or sorted(path.parent.name for path in (root / "plugins").glob("*/plugin.json"))
    if not names:
        raise ValueError("No plugins found.")
    packages = [build_plugin(root, name, native=native, host_only=host_only) for name in names]
    write_marketplace(root, names)
    return packages


def main() -> int:
    cli = argparse.ArgumentParser(description="将 plugins/ 的声明与 skills/ 的共享源码打包为独立插件。")
    cli.add_argument("--plugin", action="append", help="仅构建指定插件，可重复；默认构建全部")
    cli.add_argument("--host-only", action="store_true", help="开发测试仅构建本机平台；分发默认构建全部六个平台")
    args = cli.parse_args()
    try:
        for package in build(ROOT, args.plugin, host_only=args.host_only):
            print(package)
        print(f"插件源：{ROOT / 'dist'}")
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"构建失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
