#!/usr/bin/env python3
"""Maintainer-only cross compilation; users install the prebuilt runtime."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from distribution_files import include_file


def host_platform() -> str:
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}[platform.system()]
    architecture = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "amd64": "amd64"}[platform.machine().lower()]
    return system + "/" + architecture


def binary_name(target: str) -> str:
    system, architecture = target.split("/")
    return f"image-gen-{system}-{architecture}" + (".exe" if system == "windows" else "")


def prepare_runtime(root: Path, skill: Path, *, host_only: bool = False) -> None:
    spec_file = skill / "native-runtime.json"
    if not spec_file.exists():
        return
    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    command = spec["command"]
    if not isinstance(command, str) or not command.startswith("./cmd/") or ".." in Path(command).parts:
        raise ValueError("Native runtime command must be a package inside ./cmd/.")
    version_file = skill / spec["version_file"]
    if not version_file.resolve().is_relative_to(skill.resolve()):
        raise ValueError("Runtime version path escapes the skill.")
    version = version_file.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?", version):
        raise ValueError("Invalid native runtime version.")
    targets = spec["platforms"]
    if not isinstance(targets, list) or any(not re.fullmatch(r"(darwin|linux|windows)/(arm64|amd64)", target) for target in targets):
        raise ValueError("Unsupported native runtime platform.")
    chosen = [host_platform()] if host_only else targets
    if any(target not in targets for target in chosen):
        raise ValueError("Host platform is not supported by this runtime.")
    if not shutil.which("go"):
        raise ValueError("Maintainer builds require Go. End users should install a prebuilt plugin or skill; no Go installation is needed.")
    destination = skill / "bin"
    if destination.is_symlink():
        raise ValueError("Native output must not be a symbolic link.")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".native-build-", dir=root / "dist") as temporary:
        temporary = Path(temporary)

        def compile_target(target):
            system, architecture = target.split("/")
            filename = binary_name(target)
            environment = {**os.environ, "CGO_ENABLED": "0", "GOOS": system, "GOARCH": architecture}
            print(f"Building Image Gen {version} for {target}...", flush=True)
            subprocess.run(["go", "build", "-trimpath", "-buildvcs=false", "-ldflags", f"-s -w -X main.version={version}", "-o", str(temporary / filename), command], cwd=root, env=environment, check=True)
            return filename

        with ThreadPoolExecutor(max_workers=3) as pool:
            names = list(pool.map(compile_target, chosen))
        for target in targets:
            if target not in chosen:
                (destination / binary_name(target)).unlink(missing_ok=True)
        for name in names:
            path = temporary / name
            path.chmod(0o755)
            os.replace(path, destination / name)
        checksums = [hashlib.sha256((destination / name).read_bytes()).hexdigest() + "  " + name for name in sorted(names)]
        (destination / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")


def package_standalone(root: Path, skill: Path, version: str) -> Path:
    import zipfile
    destination = root / "dist" / "archives" / f"{skill.name}-skill-{version}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(skill.rglob("*")):
            if path.is_file() and include_file(path.relative_to(skill)):
                bundle.write(path, (Path(skill.name) / path.relative_to(skill)).as_posix())
    return destination
