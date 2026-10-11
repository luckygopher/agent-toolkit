#!/usr/bin/env python3
"""Local OpenAI-compatible image API client. Python 3.11+, standard library only.

Copyright (C) 2026 luckygopher
SPDX-License-Identifier: GPL-3.0-only
"""

from __future__ import annotations

import argparse
import base64
import binascii
import getpass
import json
import os
import re
import socket
import struct
import sys
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path


MAX_RESPONSE = 128 * 1024 * 1024
MAX_IMAGE = 32 * 1024 * 1024
MAX_UPLOAD = 64 * 1024 * 1024
DEFAULT_MODEL = "gpt-image-2"
SIZES = {"1k": "1024x1024", "2k": "2048x2048", "4k": "3840x2160"}
ENV_FIELDS = {
    "base_url": "IMAGE_GEN_BASE_URL",
    "api_key": "IMAGE_GEN_API_KEY",
    "model": "IMAGE_GEN_MODEL",
    "size": "IMAGE_GEN_SIZE",
    "output_dir": "IMAGE_GEN_OUTPUT_DIR",
    "timeout": "IMAGE_GEN_TIMEOUT",
}


class ImageGenError(Exception):
    pass


def config_path() -> Path:
    explicit = os.environ.get("IMAGE_GEN_CONFIG_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return root / "agent-toolkit" / "image-gen.json"


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ImageGenError(f"无法读取 JSON 配置：{path}。请检查文件格式和权限。") from exc
    if not isinstance(data, dict):
        raise ImageGenError(f"配置必须是 JSON 对象：{path}")
    return data


def normalize_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ImageGenError("缺少图片 API 地址，请先执行 configure 或设置 IMAGE_GEN_BASE_URL。")
    parts = urllib.parse.urlsplit(value.strip())
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ImageGenError("API 地址不能包含账号、密码、查询参数或片段。")
    if not parts.hostname:
        raise ImageGenError("API 地址必须是完整的 HTTP(S) 地址。")
    local = parts.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ImageGenError("远程 API 必须使用 HTTPS；本机 localhost 接口可以使用 HTTP。")
    path = parts.path.rstrip("/")
    for suffix in ("/images/generations", "/images/edits"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    if not path:
        path = "/v1"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def load_config(require_credentials: bool = True) -> tuple[dict, dict]:
    saved = read_json(config_path())
    cfg = {
        "base_url": "",
        "api_key": "",
        "model": DEFAULT_MODEL,
        "size": "1024x1024",
        "timeout": 300,
        "output_dir": str(Path.home() / "Pictures/agent-toolkit/image-gen"),
        **{key: value for key, value in saved.items() if key in ENV_FIELDS},
    }
    sources = {key: ("config" if key in saved else "default") for key in ENV_FIELDS}
    for key, env_name in ENV_FIELDS.items():
        if os.environ.get(env_name):
            cfg[key] = os.environ[env_name]
            sources[key] = env_name
    if cfg["base_url"]:
        cfg["base_url"] = normalize_base_url(cfg["base_url"])
    if not isinstance(cfg["api_key"], str) or any(c in cfg["api_key"] for c in "\r\n"):
        raise ImageGenError("API Key 格式无效。请在本机重新配置，不要将密钥粘贴到聊天中。")
    for name in ("model", "size", "output_dir"):
        if not isinstance(cfg[name], str) or not cfg[name].strip():
            raise ImageGenError(f"配置项 {name} 必须是非空字符串。")
    try:
        cfg["timeout"] = int(cfg["timeout"])
    except (ValueError, TypeError) as exc:
        raise ImageGenError("timeout 必须是整数秒。") from exc
    if not 10 <= cfg["timeout"] <= 1800:
        raise ImageGenError("timeout 必须在 10–1800 秒之间。")
    if require_credentials and (not cfg["base_url"] or not cfg["api_key"]):
        raise ImageGenError("尚未配置图片 API。请在本机执行 configure，或设置 IMAGE_GEN_BASE_URL 和 IMAGE_GEN_API_KEY。")
    return cfg, sources


def status() -> dict:
    cfg, sources = load_config(require_credentials=False)
    return {
        "configured": bool(cfg["base_url"] and cfg["api_key"]),
        "config_path": str(config_path()),
        "base_url": cfg["base_url"],
        "api_key_configured": bool(cfg["api_key"]),
        "model": cfg["model"],
        "size": cfg["size"],
        "output_dir": cfg["output_dir"],
        "timeout": cfg["timeout"],
        "sources": sources,
    }


def codex_credentials() -> tuple[str, str]:
    root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    try:
        saved = tomllib.loads((root / "config.toml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        saved = {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ImageGenError("无法读取 Codex 的 TOML 配置。") from exc
    profile = saved.get("profile")
    if profile:
        saved = {**saved, **saved.get("profiles", {}).get(profile, {})}
    provider_id = saved.get("model_provider", "openai")
    provider = saved.get("model_providers", {}).get(provider_id, {})
    official = provider_id == "openai"
    base = provider.get("base_url") or ("https://api.openai.com/v1" if official else "")
    env_name = provider.get("env_key")
    key = os.environ.get(env_name, "") if isinstance(env_name, str) else ""
    if not key and (official or provider.get("requires_openai_auth") is True):
        auth = read_json(root / "auth.json")
        # ChatGPT OAuth tokens are not image API credentials.
        key = auth.get("OPENAI_API_KEY", "")
    if not isinstance(key, str) or not key.strip():
        raise ImageGenError("Codex 没有可导入的 API Key。ChatGPT 登录 token、系统钥匙串和动态 token 不会被导入，请独立配置图片服务。")
    if not base:
        raise ImageGenError("Codex 中没有可导入的服务地址，请独立配置图片服务。")
    return normalize_base_url(base), key.strip()


def configure(args: argparse.Namespace) -> dict:
    path = config_path()
    if path.exists() and not args.overwrite:
        raise ImageGenError("图片配置已存在。若确实需要替换，请使用 --overwrite；现有配置没有修改。")
    if args.from_codex:
        base, key = codex_credentials()
        model = args.model or DEFAULT_MODEL
    else:
        if not sys.stdin.isatty():
            raise ImageGenError("独立配置需要在本机交互终端执行 configure；也可以通过环境变量配置。")
        base = normalize_base_url(input("图片 API 地址（例如 https://aipai.vip/v1）：").strip())
        key = getpass.getpass("API Key（输入不会显示）：").strip()
        model = args.model or input(f"图片模型 [{DEFAULT_MODEL}]：").strip() or DEFAULT_MODEL
    if not key or any(c in key for c in "\r\n"):
        raise ImageGenError("API Key 不能为空且不能包含换行。")
    if not model.strip():
        raise ImageGenError("图片模型不能为空。")
    cfg = {"base_url": base, "api_key": key, "model": model.strip(), "size": "1024x1024", "timeout": 300}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Write a private file, then atomically replace the destination.
    fd, temporary = tempfile.mkstemp(prefix=".image-gen-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(cfg, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {
        "configured": True,
        "config_path": str(path),
        "base_url": base,
        "api_key_configured": True,
        "model": model,
        "codex_modified": False,
        "note": "仅保存独立图片配置；未调用 API。环境变量若存在，会覆盖此文件。",
    }


def clean_error(message: str, key: str = "") -> str:
    if key:
        message = message.replace(key, "[REDACTED]")
    message = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", message)
    message = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", message, flags=re.IGNORECASE)
    return message[:800]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirected authenticated POST could disclose the user's API key.
        raise ImageGenError("图片 API 返回了重定向。请直接配置最终 API 地址；未向重定向目标发送密钥。")


class SecureDownloadRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_image_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_image_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if parts.username or parts.password or not parts.hostname:
        raise ImageGenError("服务返回了无效的图片下载地址。")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ImageGenError("图片下载地址必须使用 HTTPS，或本机 HTTP 地址。")
    return value


def read_limited(response, limit: int) -> bytes:
    content_length = response.headers.get("Content-Length", "")
    if content_length.isdigit() and int(content_length) > limit:
        raise ImageGenError("服务返回的数据超过允许的大小。")
    data = response.read(limit + 1)
    if len(data) > limit:
        raise ImageGenError("服务返回的数据超过允许的大小。")
    return data


def post_api(cfg: dict, route: str, body: bytes, content_type: str) -> dict:
    request = urllib.request.Request(
        cfg["base_url"] + route,
        data=body,
        headers={"Authorization": "Bearer " + cfg["api_key"], "Content-Type": content_type, "Accept": "application/json", "User-Agent": "agent-toolkit-image-gen/0.1.0"},
        method="POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=cfg["timeout"]) as response:
            raw = read_limited(response, MAX_RESPONSE)
    except urllib.error.HTTPError as exc:
        raw = exc.read(8192)
        try:
            parsed = json.loads(raw)
            error = parsed.get("error", parsed) if isinstance(parsed, dict) else {}
            message = error.get("message", "") if isinstance(error, dict) else str(error)
        except (ValueError, UnicodeError):
            message = "服务没有返回 JSON 错误详情，请检查地址、模型权限和服务状态。"
        raise ImageGenError(f"图片 API 返回 HTTP {exc.code}：{clean_error(str(message), cfg['api_key'])}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise ImageGenError("生图请求超时。服务可能已受理或计费，脚本不会自动重试。") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise ImageGenError("生图请求超时。服务可能已受理或计费，脚本不会自动重试。") from exc
        raise ImageGenError("无法连接图片 API，请检查网络、代理、HTTPS 证书和服务地址。脚本不会自动重试。") from exc
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ImageGenError("图片 API 没有返回有效 JSON。请确认地址对应图片 API，而不是网页或聊天接口。") from exc
    if not isinstance(data, dict) or not isinstance(data.get("data"), list) or not data["data"]:
        error = data.get("error", {}) if isinstance(data, dict) else {}
        detail = error.get("message", "") if isinstance(error, dict) else str(error)
        raise ImageGenError("服务没有返回标准 data 图片列表。" + clean_error(str(detail), cfg["api_key"]))
    if len(data["data"]) > 4:
        raise ImageGenError("服务返回了超过 4 张图片，无法处理该响应。")
    return data


def image_type(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise ImageGenError("文件不是可识别的 PNG、JPEG 或 WebP 图片。")


def local_image(value: str, *, mask: bool = False) -> tuple[bytes, str, str]:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ImageGenError(f"找不到图片文件：{path}")
    if path.stat().st_size > MAX_IMAGE:
        raise ImageGenError(f"图片超过 32 MiB：{path.name}")
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        raise ImageGenError(f"图片超过 32 MiB：{path.name}")
    mime, suffix = image_type(data)
    if mask and mime != "image/png":
        raise ImageGenError("蒙版必须是带透明区域的 PNG 图片。")
    return data, mime, "mask" + suffix if mask else "reference" + suffix


def multipart(fields: dict, references: list[str], mask: str | None) -> tuple[bytes, str]:
    boundary = "agentplugins" + uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    uploads = []
    uploaded_bytes = 0
    for path in references:
        image = local_image(path)
        uploaded_bytes += len(image[0])
        if uploaded_bytes > MAX_UPLOAD:
            raise ImageGenError("参考图片和蒙版的合计大小不能超过 64 MiB。")
        uploads.append(("image" if len(references) == 1 else "image[]", image))
    if mask:
        mask_data = local_image(mask, mask=True)
        first = uploads[0][1][0]
        if first.startswith(b"\x89PNG") and len(first) >= 24 and len(mask_data[0]) >= 24:
            if struct.unpack(">II", first[16:24]) != struct.unpack(">II", mask_data[0][16:24]):
                raise ImageGenError("蒙版尺寸必须与第一张参考图片一致。")
        uploads.append(("mask", mask_data))
    if sum(len(item[1][0]) for item in uploads) > MAX_UPLOAD:
        raise ImageGenError("参考图片和蒙版的合计大小不能超过 64 MiB。")
    for name, (data, mime, filename) in uploads:
        chunks.extend([
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode(),
            data,
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), "multipart/form-data; boundary=" + boundary


def resolve_size(value: str, model: str) -> str:
    value = SIZES.get(value.lower(), value.lower())
    if value == "auto":
        return value
    match = re.fullmatch(r"([1-9]\d{0,4})x([1-9]\d{0,4})", value)
    if not match:
        raise ImageGenError("尺寸应为 auto、1k、2k、4k，或 WIDTHxHEIGHT，例如 1536x1024。")
    width, height = map(int, match.groups())
    if model == "gpt-image-2" or model.startswith("gpt-image-2-"):
        if max(width, height) > 3840 or width % 16 or height % 16 or max(width, height) > 3 * min(width, height) or not 655360 <= width * height <= 8294400:
            raise ImageGenError("GPT Image 2 尺寸要求：边长为 16 的倍数，最长边不超过 3840，比例不超过 3:1，像素总数在 655360–8294400 之间。")
    return value


def get_prompt(args: argparse.Namespace) -> str:
    if bool(args.prompt) == bool(args.prompt_file):
        raise ImageGenError("请提供一个提示词参数或 --prompt-file，不能同时提供或全部省略。")
    if args.prompt_file:
        prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
    else:
        prompt = args.prompt
    if not prompt.strip():
        raise ImageGenError("图片描述不能为空。")
    if len(prompt) > 32000:
        raise ImageGenError("图片描述不能超过 32000 个字符。")
    return prompt


def output_bytes(item: dict, cfg: dict) -> bytes:
    if not isinstance(item, dict):
        raise ImageGenError("服务返回的图片条目不是 JSON 对象。")
    if item.get("b64_json"):
        encoded = item["b64_json"]
        if not isinstance(encoded, str) or len(encoded) > ((MAX_IMAGE + 2) // 3) * 4:
            raise ImageGenError("图片 base64 数据无效或超过 32 MiB。")
        try:
            return base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ImageGenError("服务返回了无效的 base64 图片。") from exc
    if item.get("url"):
        url = item["url"]
        if not isinstance(url, str):
            raise ImageGenError("图片下载地址不是字符串。")
        validate_image_url(url)
        # Image CDN requests never receive the user's API authorization header.
        request = urllib.request.Request(url, headers={"User-Agent": "agent-toolkit-image-gen/0.1.0"})
        try:
            with urllib.request.build_opener(SecureDownloadRedirect()).open(request, timeout=cfg["timeout"]) as response:
                return read_limited(response, MAX_IMAGE)
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise ImageGenError("图片已生成，但下载失败；脚本不会重新发起生图请求。") from exc
    raise ImageGenError("图片条目缺少 b64_json 或 url。")


def generate(args: argparse.Namespace) -> dict:
    cfg, _ = load_config()
    model = args.model or cfg["model"]
    if not model.strip():
        raise ImageGenError("图片模型不能为空。")
    fields = {"model": model, "prompt": get_prompt(args), "size": resolve_size(args.size or cfg["size"], model), "n": args.count}
    for option, field in ((args.quality, "quality"), (args.format, "output_format"), (args.background, "background")):
        if option is not None:
            fields[field] = option
    if args.background == "transparent" and args.format == "jpeg":
        raise ImageGenError("透明背景不能使用 JPEG，请选择 PNG 或 WebP。")
    references = getattr(args, "references", []) + (getattr(args, "reference", None) or [])
    if args.command == "edit" and not 1 <= len(references) <= 16:
        raise ImageGenError("编辑图片需要 1–16 张本地参考图片。")
    directory = Path(args.output_dir or cfg["output_dir"]).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # Fail on a non-writable destination before a potentially billable request.
    fd, probe = tempfile.mkstemp(prefix=".image-gen-", dir=directory)
    os.close(fd)
    Path(probe).unlink()
    if args.command == "edit":
        body, content_type = multipart(fields, references, args.mask)
        route = "/images/edits"
    else:
        body = json.dumps(fields).encode("utf-8")
        content_type = "application/json"
        route = "/images/generations"
    print(f"正在请求 {urllib.parse.urlsplit(cfg['base_url']).hostname}，模型 {model}，数量 {args.count}…", file=sys.stderr)
    data = post_api(cfg, route, body, content_type)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    images, errors = [], []
    for index, item in enumerate(data["data"], 1):
        try:
            blob = output_bytes(item, cfg)
            mime, suffix = image_type(blob)
            path = directory / f"image-{stamp}-{index}{suffix}"
            with path.open("xb") as stream:
                stream.write(blob)
            result = {"path": str(path), "mime_type": mime, "bytes": len(blob)}
            if isinstance(item.get("revised_prompt"), str):
                result["revised_prompt"] = item["revised_prompt"]
            images.append(result)
        except (ImageGenError, OSError) as exc:
            errors.append({"index": index, "message": clean_error(str(exc), cfg["api_key"])})
    if len(data["data"]) != args.count:
        errors.append({"message": f"请求 {args.count} 张，服务实际返回 {len(data['data'])} 张。"})
    return {"status": "partial" if errors and images else "error" if errors else "success", "model": model, "size": fields["size"], "requested_count": args.count, "images": images, "errors": errors}


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Image Gen：使用独立 API 配置生成或编辑图片，不修改 Codex 配置。")
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="显示脱敏配置；不发起网络请求")
    setup = commands.add_parser("configure", help="在本机保存独立图片配置；不发起网络请求")
    setup.add_argument("--from-codex", action="store_true", help="显式复制 Codex 的 API 地址和 API Key，不复制聊天模型")
    setup.add_argument("--model", help="图片模型，默认 gpt-image-2")
    setup.add_argument("--overwrite", action="store_true", help="替换已存在的图片配置")
    for name, help_text in (("generate", "文生图"), ("edit", "参考图片或蒙版编辑")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("prompt", nargs="?", help="图片描述，与 --prompt-file 二选一")
        command.add_argument("--prompt-file", help="包含图片描述的 UTF-8 文件")
        command.add_argument("--model", help="仅覆盖此次请求的图片模型")
        command.add_argument("--size", help="auto、1k、2k、4k 或 WIDTHxHEIGHT")
        command.add_argument("--quality", choices=("auto", "low", "medium", "high"))
        command.add_argument("--count", type=int, choices=range(1, 5), default=1)
        command.add_argument("--format", choices=("png", "jpeg", "webp"))
        command.add_argument("--background", choices=("auto", "opaque", "transparent"))
        command.add_argument("--output-dir", help="图片输出目录；每张图片使用唯一文件名")
        if name == "edit":
            command.add_argument("references", nargs="*", help="本地参考图片路径")
            command.add_argument("--reference", action="append", help="可重复；与 --prompt-file 配合时使用")
            command.add_argument("--mask", help="与第一张参考图同尺寸的透明 PNG 蒙版")
    return cli


def main() -> int:
    args = parser().parse_args()
    try:
        result = status() if args.command == "status" else configure(args) if args.command == "configure" else generate(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("status") in {"partial", "error"} else 0
    except (ImageGenError, OSError, ValueError, EOFError) as exc:
        # Never log configuration objects or raw service responses.
        key = os.environ.get("IMAGE_GEN_API_KEY", "")
        print(json.dumps({"status": "error", "message": clean_error(str(exc), key)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("操作已取消。若生成请求已发出，服务仍可能完成或计费，请勿盲目重试。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
