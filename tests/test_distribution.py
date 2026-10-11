"""Verify plugin bundling and standalone installation in isolated directories."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_plugins import build  # noqa: E402
from install_skill import install  # noqa: E402
from build_native import host_platform, binary_name, package_standalone  # noqa: E402


class Distribution(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        for name in ("plugins", "skills", ".agents"):
            shutil.copytree(ROOT / name, self.root / name)
        shutil.copy2(ROOT / "LICENSE", self.root / "LICENSE")

    def test_plugin_contains_referenced_skill_and_standalone_license(self):
        packages = build(self.root, ["image-gen"], native=False)
        self.assertEqual(len(packages), 1)
        package = packages[0]
        script = "skills/image-gen/scripts/run.sh"
        self.assertEqual((package / script).read_bytes(), (self.root / script).read_bytes())
        self.assertEqual((package / "LICENSE").read_bytes(), (self.root / "LICENSE").read_bytes())
        self.assertFalse((package / "skills.json").exists())
        manifest = json.loads((package / "plugin.json").read_text(encoding="utf-8"))
        onboarding = manifest["extensions"]["com.openai"]["onboardingSkill"]
        self.assertTrue((package / onboarding).is_file())
        self.assertEqual(manifest["$schema"], "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json")
        interface = manifest["extensions"]["com.openai"]["interface"]
        for name in ("logo", "composerIcon"):
            self.assertTrue((package / interface[name]).is_file())
        self.assertTrue((package / "skills/image-gen/bin" / binary_name(host_platform())).is_file())
        self.assertTrue((package / "skills/image-gen/bin/SHA256SUMS").is_file())
        self.assertFalse((package / "skills/image-gen/scripts/image_gen.py").exists())
        with zipfile.ZipFile(self.root / "dist/archives/image-gen-0.3.0.zip") as archive:
            self.assertIn(script, archive.namelist())
            self.assertIn("plugin.json", archive.namelist())
            self.assertNotIn("skills.json", archive.namelist())
            self.assertTrue(all(".." not in Path(name).parts for name in archive.namelist()))
        catalog = json.loads((self.root / "dist/.agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        self.assertTrue((self.root / "dist" / catalog["plugins"][0]["source"]["path"]).is_dir())

    def test_build_uses_current_shared_source_and_removes_stale_artifact(self):
        package = build(self.root, ["image-gen"], native=False)[0]
        stale = package / "stale.txt"
        stale.write_text("old", encoding="utf-8")
        source = self.root / "skills/image-gen/SKILL.md"
        source.write_text(source.read_text(encoding="utf-8") + "\nUpdated shared source.\n", encoding="utf-8")
        build(self.root, ["image-gen"], native=False)
        self.assertFalse(stale.exists())
        self.assertEqual((package / "skills/image-gen/SKILL.md").read_bytes(), source.read_bytes())

    def test_build_excludes_local_credentials_and_bytecode(self):
        skill = self.root / "skills/image-gen"
        (skill / ".env").write_text("IMAGE_GEN_API_KEY=never-publish", encoding="utf-8")
        (skill / "config.local.json").write_text("{}", encoding="utf-8")
        (skill / "__pycache__").mkdir()
        (skill / "__pycache__/cached.pyc").write_bytes(b"cached")
        package = build(self.root, ["image-gen"], native=False)[0]
        self.assertFalse((package / "skills/image-gen/.env").exists())
        self.assertFalse((package / "skills/image-gen/config.local.json").exists())
        self.assertFalse((package / "skills/image-gen/__pycache__").exists())
        archive = package_standalone(self.root, skill, "0.3.0")
        with zipfile.ZipFile(archive) as bundle:
            self.assertNotIn("image-gen/.env", bundle.namelist())
            self.assertNotIn("image-gen/config.local.json", bundle.namelist())
            self.assertFalse(any("__pycache__" in name for name in bundle.namelist()))

    def test_invalid_reference_is_rejected(self):
        references = self.root / "plugins/image-gen/skills.json"
        references.write_text(json.dumps({"skills": ["../../outside"]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            build(self.root, ["image-gen"], native=False)

    def test_missing_reference_is_rejected(self):
        references = self.root / "plugins/image-gen/skills.json"
        references.write_text(json.dumps({"skills": ["missing-skill"]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            build(self.root, ["image-gen"], native=False)

    @unittest.skipIf(os.name == "nt", "Creating symlinks may require Windows administrator privileges")
    def test_symlink_cannot_pull_files_outside_skill_into_bundle(self):
        private = Path(self.temporary.name) / "private.txt"
        private.write_text("private", encoding="utf-8")
        (self.root / "skills/image-gen/private.txt").symlink_to(private)
        with self.assertRaises(ValueError):
            build(self.root, ["image-gen"], native=False)

    def test_skill_can_be_installed_without_any_plugin(self):
        shutil.rmtree(self.root / "plugins")
        shutil.rmtree(self.root / ".agents")
        target = Path(self.temporary.name) / "standalone-skills"
        destination = install(self.root, "image-gen", target)
        self.assertTrue((destination / "SKILL.md").is_file())
        self.assertTrue((destination / "scripts/run.sh").is_file())
        self.assertTrue((destination / "LICENSE").is_file())
        self.assertFalse((destination / "plugin.json").exists())

    def test_existing_skill_requires_explicit_replacement(self):
        target = Path(self.temporary.name) / "standalone-skills"
        destination = install(self.root, "image-gen", target)
        marker = destination / "existing-user-file.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(ValueError):
            install(self.root, "image-gen", target)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        install(self.root, "image-gen", target, overwrite=True)
        self.assertFalse(marker.exists())


    def test_standalone_skill_runs_without_python_node_go_or_plugin(self):
        target = Path(self.temporary.name) / "standalone-skills"
        destination = install(self.root, "image-gen", target)
        poison = Path(self.temporary.name) / "forbidden-runtimes"
        poison.mkdir()
        marker = poison / "called.txt"
        for name in ("python", "python3", "node", "go"):
            if os.name == "nt":
                (poison / (name + ".cmd")).write_text('@echo forbidden >> "' + str(marker) + '"\n@exit /b 99\n')
            else:
                path = poison / name
                path.write_text('#!/bin/sh\necho forbidden >> "' + str(marker) + '"\nexit 99\n')
                path.chmod(0o755)
        env = {key: value for key, value in os.environ.items() if not key.startswith("IMAGE_GEN_")}
        env["PATH"] = str(poison) + os.pathsep + env.get("PATH", "")
        env["IMAGE_GEN_CONFIG_PATH"] = str(Path(self.temporary.name) / "no-config.json")
        if os.name == "nt":
            command = ["powershell.exe", "-NoProfile", "-File", str(destination / "scripts/run.ps1"), "status"]
        else:
            command = ["/bin/sh", str(destination / "scripts/run.sh"), "status"]
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["runtime"], "native")
        self.assertFalse(marker.exists())
        self.assertFalse((destination / "plugin.json").exists())

    def test_modified_runtime_is_not_executed(self):
        destination = install(self.root, "image-gen", Path(self.temporary.name) / "standalone-skills")
        runtime = destination / "bin" / binary_name(host_platform())
        with runtime.open("ab") as stream:
            stream.write(b"modified-runtime")
        if os.name == "nt":
            command = ["powershell.exe", "-NoProfile", "-File", str(destination / "scripts/run.ps1"), "--version"]
        else:
            command = ["/bin/sh", str(destination / "scripts/run.sh"), "--version"]
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('"runtime": "native"', result.stdout)

    def test_maintainer_cli_outputs_utf8_on_legacy_windows_encoding(self):
        environment = {**os.environ, "PYTHONIOENCODING": "ascii:strict"}
        for script in ("build_plugins.py", "install_skill.py"):
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--help"],
                                    env=environment, capture_output=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("UnicodeEncodeError", result.stderr)
            self.assertIn("安装" if script == "install_skill.py" else "打包", result.stdout)

    def test_skill_cannot_be_installed_over_its_source(self):
        with self.assertRaises(ValueError):
            install(self.root, "image-gen", self.root / "skills", overwrite=True)


if __name__ == "__main__":
    unittest.main()
