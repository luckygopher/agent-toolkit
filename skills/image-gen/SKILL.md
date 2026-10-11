---
name: image-gen
description: Generate or edit raster images through the user's configured OpenAI-compatible image API. Use for illustrations, photos, posters, reference-image edits, or masked edits, and for configuring Image Gen. Requires local Python execution. Do not use for SVG or vector requests.
license: GPL-3.0-only
---

# Image Gen

Use this skill's `scripts/image_gen.py` to call the user's image service. It supports text-to-image, reference-image editing, transparent-mask editing, and multiple outputs. This is a local script, not an MCP server. No third-party Python packages are needed.

## Locate the script

Resolve `scripts/image_gen.py` relative to **this SKILL.md**, using the actual installed skill path. Never assume the user's clone path or a global skill directory. Use Python 3.11 or later: `python3` on macOS/Linux, or `py -3.11` (or a newer installed Python) on Windows.

## Configuration and setup

Run `python3 <SCRIPT> status` to inspect settings. This reads local configuration without making an API request and never displays the key.

The image service has independent settings. Never edit Codex's `config.toml` or `auth.json` to configure Image Gen, and never substitute a ChatGPT access token for an API key.

If the user explicitly asks to reuse their Codex API address and API key, run:

```bash
python3 <SCRIPT> configure --from-codex --model gpt-image-2
```

Use the user's chosen image model instead of `gpt-image-2` when specified. This copies only the API address/key pair into an independent image configuration; it never imports the chat model. With an existing configuration, explain that it will be replaced and use `--overwrite` only when the user requests the change.

To configure a different service, have the user run `python3 <SCRIPT> configure` in their terminal. It prompts locally and hides the API key. Do not ask users to paste keys in chat or pass a literal key in a shell command. Environment variables `IMAGE_GEN_BASE_URL`, `IMAGE_GEN_API_KEY`, and `IMAGE_GEN_MODEL` are another supported configuration path. `status` identifies which source is active.

An installed plugin does not automatically expose host credentials to a script. If no API key is available locally, report that fact and explain the local setup step. Do not scrape application databases or attempt to reuse OAuth tokens.

## Generate or edit

Collect the user's prompt and preserve exact text, requested style, size, model, and constraints. A reference image used for editing must be a real local PNG, JPEG, or WebP file. For a local file used as a style reference, explain its role in the prompt; the edit endpoint accepts both reference-driven generation and edits. Do not fabricate missing references.

Choose an absolute output directory inside the current task/project when available (usually its `outputs` directory). Do not put deliverables in the installed plugin's cache. The script's fallback is `~/Pictures/agent-toolkit/image-gen`.

```bash
# New image
python3 <SCRIPT> generate "用户的图片描述" --output-dir "/absolute/project/outputs"

# Edit or use references
python3 <SCRIPT> edit "只更换背景，保持人物身份和构图" "/absolute/reference.png" --output-dir "/absolute/project/outputs"

# Multiple references and a transparent PNG mask
python3 <SCRIPT> edit "用户的修改要求" "/absolute/ref1.png" "/absolute/ref2.png" --mask "/absolute/mask.png" --output-dir "/absolute/project/outputs"

# Size, quality, number of variants
python3 <SCRIPT> generate "用户的图片描述" --size 1536x1024 --quality high --count 2 --output-dir "/absolute/project/outputs"
```

For long or complex prompts, save UTF-8 text in a task-local file and pass `--prompt-file /absolute/prompt.txt` instead of a positional prompt. For edit mode, use `--reference /absolute/ref.png` (repeatable) with `--prompt-file`. Never interpolate unescaped user text into shell commands.

Options: `--model`, `--size` (`auto`, `1k`, `2k`, `4k`, or `WIDTHxHEIGHT`), `--quality` (`auto`, `low`, `medium`, `high`), `--count` (1–4), `--format` (`png`, `jpeg`, `webp`), `--background` (`auto`, `opaque`, `transparent`), and `--output-dir`. Only request optional format/background parameters when the user needs them; provider support varies. `4k` means `3840x2160`. Do not claim that `4096x4096` is supported by GPT Image 2.

For transparent output use PNG or WebP. A PNG mask's transparent area identifies the edit region; it must match the first input image's dimensions. Some providers or models do not support editing, masks, transparency, or all sizes. Report the actual provider error rather than silently switching models or providers.

## Return the image

Read the script's JSON result. Each `images` entry contains an absolute `path`, `mime_type`, and optional `revised_prompt`. Show each requested deliverable with `![description](/absolute/path.png)` and include a file link. Never substitute SVG, a screenshot, or a placeholder for an API-generated image.

If `status` is `partial`, show the saved outputs and report the failed count. If the request fails or times out, do not claim success. Do not automatically retry a generation or edit: it may already have been billed. Users can choose to retry. The script never automatically opens system preview applications.
