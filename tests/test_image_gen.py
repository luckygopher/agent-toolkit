"""Exercise the standalone CLI against a local, non-billable image API."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/image-gen/scripts/image_gen.py"


def png(width=2, height=2):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    pixels = b"".join(b"\x00" + b"\x20\x80\xa0\xff" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


@contextlib.contextmanager
def api_service(*, mode="base64", count=1, status_code=200):
    records = []
    image = png()

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            records.append((self.command, self.path, dict(self.headers), b""))
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            records.append((self.command, self.path, dict(self.headers), body))
            if mode == "redirect":
                self.send_response(307)
                self.send_header("Location", self.server.base + "/redirect-target")
                self.end_headers()
                return
            if status_code != 200:
                result = {"error": {"message": "Rejected Bearer isolated-secret-key"}}
            elif mode == "missing":
                result = {"data": []}
            else:
                items = []
                for index in range(count):
                    if mode == "url":
                        item = {"url": self.server.base + "/image.png"}
                    elif mode == "partial" and index == 1:
                        item = {"b64_json": "invalid%base64"}
                    else:
                        item = {"b64_json": base64.b64encode(image).decode(), "revised_prompt": "A cycling pelican"}
                    items.append(item)
                result = {"data": items}
            payload = json.dumps(result).encode()
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            records.append((self.command, self.path, dict(self.headers), b""))
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(image)))
            self.end_headers()
            self.wfile.write(image)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server.base, records
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class ImageGenCLI(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "image-gen.json"
        self.output = self.root / "outputs"
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("IMAGE_GEN_") and key not in {"CODEX_HOME", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"}}
        self.env.update({"IMAGE_GEN_CONFIG_PATH": str(self.config), "CODEX_HOME": str(self.root / "codex"), "NO_PROXY": "127.0.0.1,localhost", "PYTHONDONTWRITEBYTECODE": "1"})

    def configured(self, base):
        self.config.write_text(json.dumps({"base_url": base + "/v1", "api_key": "isolated-secret-key", "model": "gpt-image-2"}), encoding="utf-8")

    def run_cli(self, *args, script=SCRIPT, env=None):
        return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True, encoding="utf-8", env=env or self.env, timeout=15)

    def test_generation_contract_and_saved_image(self):
        with api_service() as (base, records):
            self.configured(base)
            original = self.config.read_bytes()
            completed = self.run_cli("generate", "鹈鹕骑自行车", "--size", "1536x1024", "--output-dir", self.output)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "success")
        path = Path(result["images"][0]["path"])
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.read_bytes(), png())
        self.assertEqual(records[0][1], "/v1/images/generations")
        self.assertEqual(records[0][2]["Authorization"], "Bearer isolated-secret-key")
        self.assertEqual(json.loads(records[0][3]), {"model": "gpt-image-2", "prompt": "鹈鹕骑自行车", "size": "1536x1024", "n": 1})
        self.assertEqual(self.config.read_bytes(), original)
        self.assertNotIn("isolated-secret-key", completed.stdout + completed.stderr)

    def test_doctor_uses_only_unauthenticated_non_billable_head(self):
        with api_service() as (base, records):
            self.configured(base)
            completed = self.run_cli("doctor")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        self.assertTrue(data["reachable"])
        self.assertFalse(data["credentials_sent"])
        self.assertEqual(data["http_status"], 401)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][0], "HEAD")
        self.assertEqual(records[0][1], "/v1/models")
        self.assertNotIn("Authorization", records[0][2])
        self.assertNotIn("isolated-secret-key", completed.stdout + completed.stderr)

    def test_multiple_images_have_distinct_paths(self):
        with api_service(count=2) as (base, records):
            self.configured(base)
            completed = self.run_cli("generate", "two variants", "--count", "2", "--output-dir", self.output)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        images = json.loads(completed.stdout)["images"]
        self.assertEqual(len({item["path"] for item in images}), 2)
        self.assertTrue(all(Path(item["path"]).read_bytes() == png() for item in images))
        self.assertEqual(len(records), 1)

    def test_image_download_does_not_send_api_key(self):
        with api_service(mode="url") as (base, records):
            self.configured(base)
            completed = self.run_cli("generate", "image", "--output-dir", self.output)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual([record[0] for record in records], ["POST", "GET"])
        self.assertNotIn("Authorization", records[1][2])
        self.assertEqual(Path(json.loads(completed.stdout)["images"][0]["path"]).read_bytes(), png())

    def test_edit_sends_references_and_mask_as_multipart(self):
        reference = self.root / "reference.png"
        mask = self.root / "mask.png"
        prompt_file = self.root / "prompt.txt"
        reference.write_bytes(png())
        mask.write_bytes(png())
        prompt_file.write_text("replace only background", encoding="utf-8")
        with api_service() as (base, records):
            self.configured(base)
            completed = self.run_cli("edit", "--prompt-file", prompt_file, "--reference", reference, "--mask", mask, "--output-dir", self.output)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(records[0][1], "/v1/images/edits")
        self.assertIn("multipart/form-data; boundary=", records[0][2]["Content-Type"])
        self.assertIn(b'name="image"; filename="reference.png"', records[0][3])
        self.assertIn(b'name="mask"; filename="mask.png"', records[0][3])
        self.assertIn(b"replace only background", records[0][3])

    def test_invalid_edit_is_rejected_before_api_request(self):
        reference = self.root / "reference.png"
        mask = self.root / "mask.png"
        reference.write_bytes(png())
        mask.write_bytes(png(3, 3))
        with api_service() as (base, records):
            self.configured(base)
            completed = self.run_cli("edit", "modify", reference, "--mask", mask, "--output-dir", self.output)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(records, [])
        self.assertIn("蒙版尺寸", completed.stderr)

    def test_errors_are_redacted_and_billable_calls_not_retried(self):
        with api_service(status_code=429) as (base, records):
            self.configured(base)
            completed = self.run_cli("generate", "image", "--output-dir", self.output)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(len(records), 1)
        self.assertIn("429", completed.stderr)
        self.assertNotIn("isolated-secret-key", completed.stdout + completed.stderr)

    def test_authenticated_redirect_is_not_followed(self):
        with api_service(mode="redirect") as (base, records):
            self.configured(base)
            completed = self.run_cli("generate", "image", "--output-dir", self.output)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(len(records), 1)
        self.assertIn("重定向", completed.stderr)

    def test_partial_batch_preserves_successful_outputs(self):
        with api_service(mode="partial", count=2) as (base, records):
            self.configured(base)
            completed = self.run_cli("generate", "image", "--count", "2", "--output-dir", self.output)
        self.assertNotEqual(completed.returncode, 0)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["images"]), 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(Path(result["images"][0]["path"]).read_bytes(), png())
        self.assertEqual(len(records), 1)

    def test_codex_import_copies_credentials_without_modifying_chat_settings(self):
        codex = self.root / "codex"
        codex.mkdir()
        config = 'model="chat-only-model"\nmodel_provider="gateway"\n[model_providers.gateway]\nbase_url="https://example.invalid"\nrequires_openai_auth=true\n'
        auth = json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "isolated-secret-key", "tokens": {"access_token": "must-not-be-used"}})
        (codex / "config.toml").write_text(config, encoding="utf-8")
        (codex / "auth.json").write_text(auth, encoding="utf-8")
        completed = self.run_cli("configure", "--from-codex", "--model", "my-image-model")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(saved["base_url"], "https://example.invalid/v1")
        self.assertEqual(saved["model"], "my-image-model")
        self.assertEqual(saved["api_key"], "isolated-secret-key")
        self.assertEqual((codex / "config.toml").read_text(encoding="utf-8"), config)
        self.assertEqual((codex / "auth.json").read_text(encoding="utf-8"), auth)
        self.assertNotIn("isolated-secret-key", completed.stdout + completed.stderr)
        if os.name != "nt":
            self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_oauth_token_is_not_imported_as_image_api_key(self):
        codex = self.root / "codex"
        codex.mkdir()
        (codex / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "oauth-secret"}}), encoding="utf-8")
        completed = self.run_cli("configure", "--from-codex")
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(self.config.exists())
        self.assertNotIn("oauth-secret", completed.stdout + completed.stderr)

    def test_environment_overrides_and_status_never_show_credentials(self):
        self.configured("https://saved.invalid")
        env = {**self.env, "IMAGE_GEN_BASE_URL": "https://override.invalid/v1", "IMAGE_GEN_API_KEY": "override-secret", "IMAGE_GEN_MODEL": "override-image-model"}
        completed = self.run_cli("status", env=env)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["base_url"], "https://override.invalid/v1")
        self.assertEqual(result["model"], "override-image-model")
        self.assertEqual(result["sources"]["api_key"], "IMAGE_GEN_API_KEY")
        self.assertNotIn("override-secret", completed.stdout + completed.stderr)

    def test_skill_runs_without_repository_or_plugin_files(self):
        standalone = self.root / "standalone-skill"
        shutil.copytree(ROOT / "skills/image-gen", standalone)
        completed = self.run_cli("status", script=standalone / "scripts/image_gen.py")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(json.loads(completed.stdout)["configured"])

    def test_existing_image_configuration_is_not_silently_replaced(self):
        self.configured("https://saved.invalid")
        previous = self.config.read_bytes()
        completed = self.run_cli("configure", "--from-codex")
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self.config.read_bytes(), previous)

    def test_non_image_reference_cannot_be_uploaded(self):
        reference = self.root / "credentials.png"
        reference.write_text('{"api_key":"must-not-upload"}', encoding="utf-8")
        with api_service() as (base, records):
            self.configured(base)
            completed = self.run_cli("edit", "modify", reference, "--output-dir", self.output)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(records, [])
        self.assertNotIn("must-not-upload", completed.stdout + completed.stderr)

    def test_invalid_gpt_image_size_is_rejected_before_request(self):
        with api_service() as (base, records):
            self.configured(base)
            completed = self.run_cli("generate", "image", "--size", "4096x4096", "--output-dir", self.output)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(records, [])

    def test_empty_provider_result_is_not_reported_as_success(self):
        with api_service(mode="missing") as (base, records):
            self.configured(base)
            completed = self.run_cli("generate", "image", "--output-dir", self.output)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(len(records), 1)
        self.assertFalse(list(self.output.glob("image-*")))

    def test_custom_provider_import_uses_its_declared_environment_key(self):
        codex = self.root / "codex"
        codex.mkdir()
        (codex / "config.toml").write_text('model_provider="gateway"\n[model_providers.gateway]\nbase_url="https://gateway.invalid/custom/v1"\nenv_key="MOCK_IMAGE_GATEWAY_KEY"\nrequires_openai_auth=false\n', encoding="utf-8")
        (codex / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "wrong-provider-key"}), encoding="utf-8")
        completed = self.run_cli("configure", "--from-codex", env={**self.env, "MOCK_IMAGE_GATEWAY_KEY": "right-provider-key"})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(saved["api_key"], "right-provider-key")
        self.assertEqual(saved["base_url"], "https://gateway.invalid/custom/v1")
        self.assertNotIn("right-provider-key", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
