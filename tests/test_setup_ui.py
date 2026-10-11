"""Verify the temporary setup form with isolated credentials and loopback HTTP."""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/image-gen/scripts"))
from setup_ui import SetupServer  # noqa: E402


class SetupUI(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "image-gen.json"
        self.codex = self.root / "codex"
        env = {key: value for key, value in os.environ.items() if not key.startswith("IMAGE_GEN_")}
        env.update(IMAGE_GEN_CONFIG_PATH=str(self.config), CODEX_HOME=str(self.codex), PYTHONDONTWRITEBYTECODE="1")
        self.environment = patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def start(self, existing=None):
        if existing:
            self.config.write_text(json.dumps(existing), encoding="utf-8")
        self.server = SetupServer()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

        def cleanup():
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
        self.addCleanup(cleanup)

    def request(self, route="state", payload=None, headers=None, path=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
        extra = {"Content-Type": "application/json", "Origin": self.server.origin, "X-Setup-Token": self.server.token}
        extra.update(headers or {})
        connection.request("POST" if body is not None else "GET", path or self.server.prefix + route, body=body, headers=extra)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def settings(self):
        return {"base_url": "https://images.invalid/v1", "api_key": "dummy-local-key", "model": "my-image-model", "size": "1024x1024", "timeout": 300}

    def mock_codex(self):
        self.codex.mkdir()
        (self.codex / "config.toml").write_text('model="chat-only"\nmodel_provider="gateway"\n[model_providers.gateway]\nbase_url="https://codex-images.invalid/v1"\nrequires_openai_auth=true\n', encoding="utf-8")
        (self.codex / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "dummy-imported-key"}), encoding="utf-8")

    def test_existing_key_is_not_returned_to_browser(self):
        self.start(self.settings())
        code, _, raw = self.request()
        self.assertEqual(code, 200)
        self.assertNotIn(b"dummy-local-key", raw)
        data = json.loads(raw)
        self.assertTrue(data["api_key_configured"])
        self.assertNotIn("api_key", data)
        self.assertTrue(data["existing_configuration"])

    def test_setup_serves_self_contained_page_and_private_headers(self):
        self.start()
        code, headers, raw = self.request("")
        self.assertEqual(code, 200)
        self.assertIn("从 Codex 导入连接", raw.decode())
        self.assertNotIn(b"__NONCE__", raw)
        self.assertIn(self.server.nonce, headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_save_writes_only_independent_config_and_stops_server(self):
        self.mock_codex()
        before = (self.codex / "config.toml").read_bytes(), (self.codex / "auth.json").read_bytes()
        self.start()
        code, _, raw = self.request("save", self.settings())
        self.assertEqual(code, 200, raw)
        self.assertTrue(json.loads(raw)["saved"])
        self.assertNotIn(b"dummy-local-key", raw)
        self.assertEqual(json.loads(self.config.read_text())["api_key"], "dummy-local-key")
        self.assertEqual(before, ((self.codex / "config.toml").read_bytes(), (self.codex / "auth.json").read_bytes()))
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def test_import_is_staged_until_save_and_never_returns_key(self):
        self.mock_codex()
        self.start()
        code, _, raw = self.request("import", {})
        self.assertEqual(code, 200, raw)
        self.assertNotIn(b"dummy-imported-key", raw)
        self.assertFalse(self.config.exists())
        data = self.settings()
        data.update(base_url="https://codex-images.invalid/v1", api_key="")
        code, _, raw = self.request("save", data)
        self.assertEqual(code, 200, raw)
        saved = json.loads(self.config.read_text())
        self.assertEqual(saved["api_key"], "dummy-imported-key")
        self.assertEqual(saved["model"], "my-image-model")

    def test_replacing_existing_configuration_requires_checkbox(self):
        self.start(self.settings())
        original = self.config.read_bytes()
        payload = {**self.settings(), "api_key": "", "model": "new-image-model"}
        code, _, _ = self.request("save", payload)
        self.assertEqual(code, 409)
        self.assertEqual(self.config.read_bytes(), original)
        code, _, raw = self.request("save", {**payload, "overwrite": True})
        self.assertEqual(code, 200, raw)
        self.assertEqual(json.loads(self.config.read_text())["api_key"], "dummy-local-key")

    def test_retained_key_is_not_sent_to_changed_provider(self):
        self.start(self.settings())
        original = self.config.read_bytes()
        payload = {**self.settings(), "base_url": "https://different.invalid/v1", "api_key": "", "overwrite": True}
        code, _, _ = self.request("save", payload)
        self.assertEqual(code, 400)
        self.assertEqual(self.config.read_bytes(), original)

    def test_clearing_output_directory_restores_default(self):
        self.start({**self.settings(), "output_dir": str(self.root / "previous-output")})
        code, _, raw = self.request("save", {**self.settings(), "output_dir": "", "overwrite": True})
        self.assertEqual(code, 200, raw)
        saved = json.loads(self.config.read_text())
        self.assertEqual(saved["output_dir"], str(Path.home() / "Pictures/agent-toolkit/image-gen"))

    def test_foreign_origin_wrong_host_or_missing_token_cannot_save(self):
        self.start()
        for headers in [{"Origin": "https://foreign.invalid"}, {"X-Setup-Token": ""}, {"X-Setup-Token": "é"}, {"Host": "foreign.invalid"}]:
            with self.subTest(headers=headers):
                code, _, _ = self.request("save", self.settings(), headers=headers)
                self.assertEqual(code, 403)
        self.assertFalse(self.config.exists())

    def test_unknown_setup_link_is_rejected(self):
        self.start()
        code, _, _ = self.request(path="/setup/wrong-token/state")
        self.assertEqual(code, 404)

    def test_non_json_or_oversized_post_is_rejected(self):
        self.start()
        code, _, _ = self.request("save", {}, headers={"Content-Type": "text/plain"})
        self.assertEqual(code, 415)
        code, _, _ = self.request("save", raw=b"x" * 17000)
        self.assertEqual(code, 413)
        self.assertFalse(self.config.exists())

    def test_changed_configuration_is_not_overwritten_by_stale_form(self):
        self.start(self.settings())
        replacement = {**self.settings(), "model": "changed-elsewhere"}
        self.config.write_text(json.dumps(replacement), encoding="utf-8")
        code, _, _ = self.request("save", {**self.settings(), "overwrite": True})
        self.assertEqual(code, 409)
        self.assertEqual(json.loads(self.config.read_text())["model"], "changed-elsewhere")

    def test_cancel_does_not_write_configuration(self):
        self.start()
        code, _, _ = self.request("close", {})
        self.assertEqual(code, 200)
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())
        self.assertFalse(self.config.exists())

    def test_session_expiry_stops_temporary_process(self):
        self.start()
        self.server.stop_after(0.05)
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())
        self.assertFalse(self.config.exists())

    def test_cli_rejects_invalid_setup_timeout_without_traceback(self):
        completed = subprocess.run([sys.executable, str(ROOT / "skills/image-gen/scripts/image_gen.py"), "setup", "--no-open", "--timeout", "1"], env=dict(os.environ), text=True, capture_output=True, timeout=5)
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn("Traceback", completed.stderr)


if __name__ == "__main__":
    unittest.main()
