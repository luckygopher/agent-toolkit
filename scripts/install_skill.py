#!/usr/bin/env python3
"""Install a standalone skill without installing a plugin or changing Codex config.

Copyright (C) 2026 luckygopher
SPDX-License-Identifier: GPL-3.0-only
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

from console import configure_console

from build_plugins import ignore_files, validate_tree


ROOT = Path(__file__).resolve().parents[1]


def install(root: Path, name: str, target: Path, overwrite: bool = False) -> Path:
    root = root.resolve()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise ValueError("Invalid skill name.")
    source = root / "skills" / name
    if source.is_symlink() or not (source / "SKILL.md").is_file():
        raise ValueError(f"Skill not found: {name}")
    validate_tree(source)
    target = target.expanduser().resolve()
    destination = target / name
    if destination.exists() or destination.is_symlink():
        if not overwrite:
            raise ValueError(f"Skill already exists: {destination}. Use --overwrite only if you intend to replace it.")
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError("An existing symlink or non-directory will not be replaced.")
    if target == source or target.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("The install directory must not overlap the skill source.")
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".install-{name}-", dir=target) as temporary:
        package = Path(temporary) / name
        shutil.copytree(source, package, ignore=ignore_files)
        if not (package / "LICENSE").exists():
            shutil.copy2(root / "LICENSE", package / "LICENSE")
        backup = None
        if destination.exists():
            backup = Path(temporary) / "previous-install"
            destination.rename(backup)
        try:
            package.rename(destination)
        except OSError:
            if backup is not None:
                backup.rename(destination)
            raise
    return destination


def main() -> int:
    configure_console()
    cli = argparse.ArgumentParser(description="独立安装 skill，不安装插件，不修改 Codex 的聊天配置。")
    cli.add_argument("skill", help="例如 image-gen")
    cli.add_argument("--target", type=Path, default=Path.home() / ".agents/skills", help="技能父目录，默认 ~/.agents/skills")
    cli.add_argument("--overwrite", action="store_true", help="明确替换已有的同名 skill")
    args = cli.parse_args()
    try:
        print(install(ROOT, args.skill, args.target, args.overwrite))
        print("安装完成。请新建聊天或重新加载技能。")
        return 0
    except (OSError, ValueError) as exc:
        print(f"安装失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
