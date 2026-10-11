---
name: image-gen
description: Generate or edit images through the user's configured OpenAI-compatible image API, or open Image Gen setup. Uses a bundled native runtime on macOS, Windows and Linux; users do not install Python, Node or Go. Do not use for SVG or vector requests.
license: GPL-3.0-only
---

# Image Gen

Use the packaged native Image Gen program. The plugin contains the runtime; it does not require Python, Node, Go, MCP, or a permanent service. The standalone skill uses the same program and configuration.

## Locate and launch

Resolve paths relative to this actual `SKILL.md`, not the user's clone or a global skills path.

- macOS/Linux: `sh "<SKILL_ROOT>/scripts/run.sh" <COMMAND> <OPTIONS>`.
- Windows: `powershell.exe -NoProfile -File "<SKILL_ROOT>/scripts/run.ps1" <COMMAND> <OPTIONS>`.

The launcher selects the OS/CPU and verifies the runtime checksum before executing it. Packaged plugins and skill ZIPs include the runtime. A source-only skill can download its pinned, checksummed runtime from the same project's published artifact tag on first use; do not compile it or ask the user to install a language runtime. If the executor blocks this harmless download, request network/package-directory access and rerun the launcher before any image request.

Windows script policy may block `.ps1` files. In that case, inspect the OS architecture and invoke the matching `bin/image-gen-windows-amd64.exe` or `bin/image-gen-windows-arm64.exe` directly after comparing its SHA-256 with `bin/SHA256SUMS`. Do not weaken the user's execution policy. Supported platforms are macOS, Linux, Windows, each with arm64/amd64.

Below, `<IMAGE_GEN>` means the platform-specific launcher above. Preserve argument boundaries and quote paths. Never call a Python script, use a private app runtime, or install a compiler as an end-user workaround.

## Setup

For the plugin's Setup action, first-time setup, or visual configuration, run:

```text
<IMAGE_GEN> setup --no-open
```

The process prints `setup_url` and stays running. Open that exact URL in a visible browser or Codex browser panel while keeping the process alive. Do not wait for it to exit before opening the page. If no browser tool is available, provide the clickable URL. The user can enter their API address/key/model locally or explicitly choose to import a Codex address/key pair. Saving, cancelling, or expiry stops the temporary setup process. The default lifetime is 600 seconds; `--timeout` can adjust it.

Do not fill, inspect, screenshot, or print a real API key. The form never returns a saved/imported key to the browser. Do not ask users to paste keys into chat. For UI testing, use dummy values with a temporary `IMAGE_GEN_CONFIG_PATH`.

`<IMAGE_GEN> status` reports non-secret settings and `runtime: native`; it makes no network request. Existing 0.2.x configuration works without migration. Image settings are independent of host chat settings. Never edit Codex `config.toml` or `auth.json` to configure this tool.

If the user explicitly chooses to import Codex's saved API connection without opening the form:

```text
<IMAGE_GEN> configure --from-codex --model gpt-image-2
```

Use the image model the user selected. This copies the address/key pair into independent image settings; it never imports a chat model or an OAuth token. Existing image settings require explicit replacement with `--overwrite`.

## Network access

Before the first image request in a chat, run `<IMAGE_GEN> doctor` in the environment that will execute the image request. It sends only an unauthenticated HEAD request and cannot generate or bill an image. HTTP 401/404/405 can still demonstrate transport connectivity; it does not validate the key, image endpoint, or model.

If `dns_error` or `network_permission_denied` occurs and the executor restricts outbound networking, request network access (for example `sandbox_permissions: require_escalated`) and rerun **doctor**. Generate in the same permitted environment after it succeeds. Do not retry a billable request to diagnose network permissions. Report a rejected approval and its actual reason if access is denied.

TLS and hostname verification remain enabled and use the system trust mechanism. The user does not need a Python certificate installation. Explicit `SSL_CERT_FILE`/`SSL_CERT_DIR` choices are supported; do not disable TLS verification.

## Generate and edit

Preserve the user's prompt, exact in-image text, style, model, dimensions and constraints. With reference images, describe their role in the prompt and use `edit`. References must be actual local PNG, JPEG or WebP files; do not fabricate missing paths.

Choose an absolute output directory in the current task/project, typically `outputs/`. Never put generated images in the plugin cache. Without an override, the program uses the user's saved default directory.

```text
<IMAGE_GEN> generate "用户的图片描述" --output-dir "/absolute/project/outputs"
<IMAGE_GEN> generate "用户的图片描述" --size 1536x1024 --quality high --count 2 --output-dir "/absolute/project/outputs"
<IMAGE_GEN> edit "只更换背景，保持主体" "/absolute/reference.png" --output-dir "/absolute/project/outputs"
<IMAGE_GEN> edit "用户的修改要求" "/absolute/ref1.png" "/absolute/ref2.png" --mask "/absolute/mask.png" --output-dir "/absolute/project/outputs"
<IMAGE_GEN> edit --prompt-file "/absolute/prompt.txt" --reference "/absolute/reference.png" --output-dir "/absolute/project/outputs"
```

For long/complex prompts, write a task-local UTF-8 prompt file and use `--prompt-file`; with edits, provide repeatable `--reference` options. Do not combine a positional prompt with `--prompt-file`. Never interpolate unescaped user text into shell code.

Options include `--model`, `--size` (`auto`, `1k`, `2k`, `4k`, `WIDTHxHEIGHT`), `--quality` (`auto`, `low`, `medium`, `high`), `--count` (1–4), `--format` (`png`, `jpeg`, `webp`), `--background` (`auto`, `opaque`, `transparent`), `--mask`, `--output-dir`. `4k` means `3840x2160`. Only request optional output format/transparency when needed; provider support varies. PNG/WebP support transparency; JPEG does not. A PNG mask's transparent area selects the edit region and must match the first reference's dimensions.

Settings can also be supplied through `IMAGE_GEN_BASE_URL`, `IMAGE_GEN_API_KEY`, `IMAGE_GEN_MODEL`, `IMAGE_GEN_SIZE`, `IMAGE_GEN_OUTPUT_DIR`, `IMAGE_GEN_TIMEOUT`, `IMAGE_GEN_CONFIG_PATH`. Environment overrides take priority over the independent saved configuration. Do not assume a desktop app inherits terminal variables.

## Return the result

Read the JSON result. Each `images` entry contains an absolute `path`, actual `mime_type`, and optional `revised_prompt`. Embed each deliverable as `![description](/absolute/path.png)` and provide a file link. Do not substitute a vector, screenshot or placeholder for an API-generated image.

With `partial`, show saved images and explain the failed count. Do not claim success after an error. The program never automatically retries generation or editing because it may already have been billed; let the user choose whether to retry. It does not automatically open system image preview apps.
