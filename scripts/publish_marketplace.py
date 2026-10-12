#!/usr/bin/env python3
"""Publish already-built, checksummed artifacts into this repository's market branch."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args, cwd=ROOT, capture=False):
    result = subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else None


def validate_artifacts(root: Path):
    catalog = json.loads((root / "dist/.agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    tags = []
    for entry in catalog["plugins"]:
        package = root / "dist" / entry["source"]["path"]
        manifest = json.loads((package / "plugin.json").read_text(encoding="utf-8"))
        for skill in (package / "skills").iterdir():
            spec_path = skill / "native-runtime.json"
            if not spec_path.is_file():
                continue
            from build_native import binary_name
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            expected = {binary_name(target) for target in spec["platforms"]}
            checksums = {}
            for line in (skill / "bin/SHA256SUMS").read_text().splitlines():
                digest, filename = line.split()
                checksums[filename] = digest
            if set(checksums) != expected:
                raise ValueError("Publication requires all declared platforms, not a host-only build.")
            for filename, digest in checksums.items():
                if hashlib.sha256((skill / "bin" / filename).read_bytes()).hexdigest() != digest:
                    raise ValueError(f"Checksum mismatch: {filename}")
            version = (skill / spec["version_file"]).read_text().strip()
            if version != manifest["version"]:
                raise ValueError("Plugin and native runtime versions must match for this release.")
            tags.append((f"{entry['name']}-v{version}", entry["name"], version))
    return catalog, tags


def publish(root: Path, push: bool):
    catalog, tags = validate_artifacts(root)
    revision = git("rev-parse", "HEAD", cwd=root, capture=True)
    remote = git("remote", "get-url", "origin", cwd=root, capture=True)
    if not push:
        print(json.dumps({"validated": True, "source_revision": revision, "tags": [tag for tag, _, _ in tags], "push": False}, indent=2))
        return
    with tempfile.TemporaryDirectory(prefix="agent-toolkit-marketplace-") as temporary:
        checkout = Path(temporary) / "marketplace"
        existing = git("ls-remote", "--heads", remote, "refs/heads/marketplace", cwd=root, capture=True)
        if existing:
            git("clone", "--single-branch", "--branch", "marketplace", remote, str(checkout), cwd=root)
        else:
            checkout.mkdir()
            git("init", "-b", "marketplace", cwd=checkout)
            git("remote", "add", "origin", remote, cwd=checkout)
        for key, fallback in [("user.name", "github-actions[bot]"), ("user.email", "41898282+github-actions[bot]@users.noreply.github.com")]:
            result = subprocess.run(["git", "config", "--get", key], cwd=root, capture_output=True, text=True)
            git("config", key, result.stdout.strip() or fallback, cwd=checkout)
        for name in ["plugins", ".agents"]:
            destination = checkout / name
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(root / "dist" / name, destination)
        (checkout / "README.md").write_text("# Agent Toolkit Marketplace\n\nThis branch contains prebuilt plugins. Users do not install Python, Node or Go. Add this repository with Git ref `marketplace`, install Image Gen, and run Setup.\n\nSource revision: " + revision + "\nSource: https://github.com/luckygopher/agent-toolkit/tree/" + revision + "\n", encoding="utf-8")
        (checkout / "source-revision.json").write_text(json.dumps({"repository": "https://github.com/luckygopher/agent-toolkit", "commit": revision}, indent=2) + "\n")
        git("add", "--", "plugins", ".agents", "README.md", "source-revision.json", cwd=checkout)
        changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=checkout)
        if changed.returncode == 1:
            git("commit", "-m", "build: publish prebuilt Image Gen runtime", cwd=checkout)
        elif changed.returncode != 0:
            raise RuntimeError("Unable to inspect marketplace changes.")
        for tag, name, _ in tags:
            previous = git("ls-remote", "--tags", remote, "refs/tags/" + tag, cwd=root, capture=True)
            if previous:
                git("fetch", "origin", "tag", tag, cwd=checkout)
                old = git("show", f"{tag}:plugins/{name}/skills/{name}/bin/SHA256SUMS", cwd=checkout, capture=True)
                current = (checkout / "plugins" / name / "skills" / name / "bin/SHA256SUMS").read_text().strip()
                if old != current:
                    raise ValueError(f"Refusing to overwrite immutable runtime tag {tag}; bump the version.")
            else:
                git("tag", tag, cwd=checkout)
        git("push", "origin", "marketplace", *[tag for tag, _, _ in tags], cwd=checkout)
    print("Prebuilt marketplace and pinned runtime tags published.")


def main():
    cli = argparse.ArgumentParser(description="Validate or publish already-built native plugins.")
    cli.add_argument("--push", action="store_true", help="Explicitly publish to the configured origin; default validates only")
    args = cli.parse_args()
    publish(ROOT, args.push)


if __name__ == "__main__":
    main()
