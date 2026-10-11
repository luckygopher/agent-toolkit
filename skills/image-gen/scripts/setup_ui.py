"""Short-lived, loopback-only configuration form. No image API requests.

Copyright (C) 2026 luckygopher
SPDX-License-Identifier: GPL-3.0-only
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from image_gen import ImageGenError, codex_credentials, config_path, load_config, normalize_base_url, save_config


MAX_BODY = 16 * 1024
PAGE = Path(__file__).resolve().parent.parent / "assets/setup.html"


def fingerprint(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


class SetupServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        self.settings, self.sources = load_config(require_credentials=False)
        self.original = fingerprint(config_path())
        self.key_address = self.settings["base_url"]
        self.token = secrets.token_urlsafe(32)
        self.nonce = secrets.token_urlsafe(24)
        self.saved = False
        self.lock = threading.Lock()
        self.timers = []
        super().__init__(("127.0.0.1", 0), SetupHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.prefix = f"/setup/{self.token}/"
        self.url = self.origin + self.prefix

    def stop_after(self, seconds):
        timer = threading.Timer(seconds, self.shutdown)
        timer.daemon = True
        self.timers.append(timer)
        timer.start()

    def server_close(self):
        for timer in self.timers:
            timer.cancel()
        self.settings["api_key"] = ""
        super().server_close()

    def public_settings(self):
        return {
            "base_url": self.settings["base_url"],
            "model": self.settings["model"],
            "size": self.settings["size"],
            "timeout": self.settings["timeout"],
            "output_dir": self.settings["output_dir"],
            "api_key_configured": bool(self.settings["api_key"]),
            "existing_configuration": self.original is not None,
            "environment_overrides": [name for name, source in self.sources.items() if source.startswith("IMAGE_GEN_")],
        }


class SetupHandler(BaseHTTPRequestHandler):
    server_version = "ImageGenSetup/0.2.0"

    def log_message(self, *_args):
        # Do not log request URLs, tokens, POST bodies, or credentials.
        pass

    def reply(self, code, data, content_type="application/json; charset=utf-8"):
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{self.server.nonce}'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def route(self):
        if self.headers.get("Host") != urllib.parse.urlsplit(self.server.origin).netloc:
            self.reply(403, {"error": "无效的本机页面地址。"})
            return None
        path = urllib.parse.urlsplit(self.path).path
        if not path.startswith(self.server.prefix):
            self.reply(404, {"error": "配置链接无效或已过期。"})
            return None
        return path[len(self.server.prefix):]

    def do_GET(self):
        route = self.route()
        if route is None:
            return
        if route == "":
            page = PAGE.read_text(encoding="utf-8").replace("__NONCE__", self.server.nonce)
            self.reply(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif route == "state":
            self.reply(200, self.server.public_settings())
        else:
            self.reply(404, {"error": "页面不存在。"})

    def do_POST(self):
        route = self.route()
        if route is None:
            return
        supplied = self.headers.get("X-Setup-Token", "")
        if not supplied.isascii() or self.headers.get("Origin") != self.server.origin or not secrets.compare_digest(supplied, self.server.token):
            self.reply(403, {"error": "只能从本机配置页面操作。"})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            self.reply(415, {"error": "无效的配置格式。"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                self.reply(413, {"error": "配置内容过大或为空。"})
                return
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
        except (ValueError, UnicodeError):
            self.reply(400, {"error": "配置内容无法解析。"})
            return
        with self.server.lock:
            if self.server.saved:
                self.reply(409, {"error": "配置已保存，请重新打开 Setup 修改。"})
                return
            try:
                if route == "import":
                    base, key = codex_credentials()
                    self.server.settings.update(base_url=base, api_key=key)
                    self.server.key_address = base
                    self.reply(200, {"base_url": base, "api_key_configured": True})
                elif route == "save":
                    self.save(payload)
                elif route == "close":
                    self.reply(200, {"closed": True})
                    self.server.stop_after(0.5)
                else:
                    self.reply(404, {"error": "操作不存在。"})
            except ImageGenError as exc:
                self.reply(400, {"error": str(exc)})
            except OSError:
                self.reply(500, {"error": "无法保存本机配置，请检查文件权限。"})

    def save(self, payload):
        if self.server.original is not None and payload.get("overwrite") is not True:
            self.reply(409, {"error": "请确认替换已有的生图配置。"})
            return
        if fingerprint(config_path()) != self.server.original:
            self.reply(409, {"error": "配置已被其他程序修改，请重新打开 Setup 后再保存。"})
            return
        base = normalize_base_url(payload.get("base_url", ""))
        key = payload.get("api_key", "")
        if not isinstance(key, str):
            raise ImageGenError("API Key 格式无效。")
        if not key.strip():
            if base != self.server.key_address:
                raise ImageGenError("API 地址已改变，请填写该服务的 API Key，或重新从 Codex 导入。")
            key = self.server.settings["api_key"]
        cfg = {**self.server.settings, "base_url": base, "api_key": key}
        for name in ("model", "size", "timeout", "output_dir"):
            if name in payload:
                cfg[name] = payload[name]
        if cfg.get("output_dir") == "":
            cfg["output_dir"] = str(Path.home() / "Pictures/agent-toolkit/image-gen")
        save_config(cfg, overwrite=self.server.original is not None)
        self.server.saved = True
        self.reply(200, {"saved": True, "message": "配置已保存。回到聊天即可开始生成图片。", "environment_overrides": self.server.public_settings()["environment_overrides"]})
        self.server.stop_after(1)


def run_setup(*, open_browser=True, timeout=600):
    if not 10 <= timeout <= 3600:
        raise ImageGenError("配置页面有效期必须在 10–3600 秒之间。")
    with SetupServer() as server:
        print(json.dumps({"setup_url": server.url, "expires_in_seconds": timeout}, ensure_ascii=False), flush=True)
        if open_browser:
            webbrowser.open(server.url, new=1)
        server.stop_after(timeout)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            return 130
        return 0
