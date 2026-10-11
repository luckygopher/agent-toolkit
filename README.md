# Agent Toolkit

可独立使用的 AI skills，以及按需安装的插件。一个仓库，一份 skill 源码，两种安装方式。

首个组件是 **Image Gen**：使用自己的 API 地址、API Key 和图片模型，生成或编辑图片。支持 AiPai 等兼容 OpenAI 图片接口的服务。只需要 Python 3.11+，无需安装第三方 Python 包、MCP 服务或后台进程。

## 目录与引用关系

```text
agent-toolkit/
├── skills/
│   └── image-gen/                # 独立 skill 的唯一源码
│       ├── SKILL.md
│       ├── agents/openai.yaml
│       └── scripts/image_gen.py
├── plugins/
│   └── image-gen/
│       ├── plugin.json           # 插件元数据
│       └── skills.json           # 引用共享 skill：{"skills": ["image-gen"]}
├── scripts/
│   ├── install_skill.py          # 仅安装指定 skill
│   └── build_plugins.py          # 按引用生成独立插件包
├── tests/                        # 本地模拟接口与安装/打包测试
└── dist/                         # 构建产物，不提交到源码仓库
```

`skills/` 不依赖 `plugins/`。插件通过 `skills.json` 引用 skill；构建时将被引用的 skill 收入安装包。源码不重复维护，安装后的插件不需要访问这个仓库或另装 skill。

## 获取项目

```bash
git clone https://github.com/luckygopher/agent-toolkit.git
cd agent-toolkit
```

以下命令使用 `python3`。Windows 可换成 `py -3.11` 或已安装的更新版本 Python。

## 方式一：只安装 skill

```bash
python3 scripts/install_skill.py image-gen
```

默认安装到 `~/.agents/skills/image-gen/`。它只复制对应 skill，不构建或安装插件，不修改 Codex 配置。若同名 skill 已存在，默认拒绝覆盖；确认需要替换后才使用 `--overwrite`。

安装到其他支持本地 skills 的客户端时，可以指定其技能父目录，例如：

```bash
python3 scripts/install_skill.py image-gen --target ~/.claude/skills
```

也可以手工复制 `skills/image-gen/` 整个目录。重新加载客户端或新建聊天后，使用 `$image-gen`，或直接描述要生成/编辑的图片。

## 方式二：只安装插件

先构建，再将**本地构建目录**注册为插件源：

```bash
python3 scripts/build_plugins.py --plugin image-gen
codex plugin marketplace add ./dist
codex plugin add image-gen@agent-toolkit
```

也可以构建后在桌面端插件目录中选择 **Agent Toolkit** 来源，按需安装 **Image Gen**。根据客户端版本，可能需要刷新或重启，再新建聊天。

产物位置：

- `dist/plugins/image-gen/`：自包含插件，包含它引用的 skill 和脚本。
- `dist/.agents/plugins/marketplace.json`：只列出本次构建插件的安装目录。
- `dist/archives/image-gen-0.1.0.zip`：单个插件的分发压缩包。

不需要再执行 skill 安装命令。同一个客户端中通常选择一种安装方式即可，避免重复出现同名技能。

当前 `main` 分支保存源码，`dist/` 不入库。因此直接添加 GitHub 仓库作为插件源不会自动执行构建；首版请使用上述 clone → 构建 → 添加本地 `dist` 的方式。GitHub 源直装和官方公开目录上架属于后续分发工作。

## 首次配置图片服务

插件和独立 skill 使用同一套**独立图片配置**。图片地址、密钥和模型不会覆盖 Codex 的聊天设置。

### 从已有 Codex API 配置导入

在仓库根目录运行：

```bash
python3 skills/image-gen/scripts/image_gen.py configure --from-codex --model gpt-image-2
```

只读取已保存的 Codex 服务地址和 API Key，复制到图片配置；**不复制聊天模型，不修改 `config.toml` 或 `auth.json`，不发起 API 请求**。如果图片服务与聊天服务不同，请使用下面的独立配置方式。

导入不读取 ChatGPT OAuth token、系统钥匙串或动态凭据，也不会推断 CLI 临时覆盖的服务设置。这些情况下需要独立配置。配置已存在时默认不会覆盖，明确替换需加 `--overwrite`。

### 配置不同的图片服务

在自己的本机终端运行交互配置：

```bash
python3 skills/image-gen/scripts/image_gen.py configure
```

依次填写图片 API 地址、API Key 和图片模型。密钥输入隐藏，不需要发到聊天中。

配置位置：

- macOS/Linux：`${XDG_CONFIG_HOME:-~/.config}/agent-toolkit/image-gen.json`
- Windows：`%APPDATA%\agent-toolkit\image-gen.json`
- 自定义位置：环境变量 `IMAGE_GEN_CONFIG_PATH`

新建配置文件在 macOS/Linux 上使用 `0600` 权限。它是本机明文配置，不是系统钥匙串，不能提交到代码仓库。

API 地址一般填写到 `/v1`，例如 `https://aipai.vip/v1`。如果只填域名，脚本自动补 `/v1`；有自定义路径时保留该路径。也支持把完整 `/images/generations` 或 `/images/edits` 地址规范化为共同的 API 根路径。

### 环境变量

| 变量 | 用途 |
| --- | --- |
| `IMAGE_GEN_BASE_URL` | 图片 API 根地址 |
| `IMAGE_GEN_API_KEY` | 图片 API Key |
| `IMAGE_GEN_MODEL` | 图片模型，默认 `gpt-image-2` |
| `IMAGE_GEN_SIZE` | 默认尺寸，默认 `1024x1024` |
| `IMAGE_GEN_OUTPUT_DIR` | 默认图片目录 |
| `IMAGE_GEN_TIMEOUT` | 超时秒数，默认 300，范围 10–1800 |
| `IMAGE_GEN_CONFIG_PATH` | 独立配置文件位置 |

优先级：当前请求的参数（模型、尺寸、输出目录等）→ 对应环境变量 → 图片配置文件 → 默认值。环境变量只对实际继承它们的进程生效；从桌面图标启动的应用未必继承终端变量，桌面端推荐使用持久配置文件。不会自动读取项目 `.env`。

查看配置来源，不显示密钥，也不调用图片 API：

```bash
python3 skills/image-gen/scripts/image_gen.py status
```

## 生成和编辑图片

安装后可以直接在聊天中说：

> 使用 Image Gen 生成一张鹈鹕骑自行车的插画，横版，图片保存到当前项目并直接展示。

也可以直接运行脚本：

```bash
# 文生图
python3 skills/image-gen/scripts/image_gen.py generate "鹈鹕骑自行车的插画" --output-dir ./outputs

# 横版、高质量、两张变体
python3 skills/image-gen/scripts/image_gen.py generate "鹈鹕骑自行车的插画" --size 1536x1024 --quality high --count 2 --output-dir ./outputs

# 参考图编辑
python3 skills/image-gen/scripts/image_gen.py edit "只更换背景，保持主体" ./reference.png --output-dir ./outputs

# 多参考图与透明 PNG 蒙版
python3 skills/image-gen/scripts/image_gen.py edit "用户的修改要求" ./reference.png ./style.png --mask ./mask.png --output-dir ./outputs

# 从 UTF-8 文件读取长提示词
python3 skills/image-gen/scripts/image_gen.py edit --prompt-file ./prompt.txt --reference ./reference.png --output-dir ./outputs
```

脚本输出 JSON，包含每张图片的绝对路径、实际图片格式和可能返回的修订提示词；skill 据此在聊天里展示图片。输出使用唯一文件名，不覆盖已有图片。不指定目录时保存到 `~/Pictures/agent-toolkit/image-gen/`。

支持 `--size auto|1k|2k|4k|WIDTHxHEIGHT`、`--quality`、`--count 1–4`、`--model`、`--format png|jpeg|webp`、`--background`。`4k` 对应 `3840x2160`。请求只在用户选择时发送格式和背景等可选参数，以减少兼容性问题。

## 支持范围

- 文生图：`POST <base_url>/images/generations`，JSON 请求。
- 编辑：`POST <base_url>/images/edits`，multipart 请求；支持 1–16 张 PNG/JPEG/WebP 参考图和可选 PNG 蒙版。
- 标准响应：`data[].b64_json` 或 `data[].url`；图片下载不会附带 API Key。
- 服务需要支持指定的图片模型及相应参数；兼容聊天接口不代表兼容图片接口。
- GPT Image 2 使用其尺寸限制；其他模型由服务校验尺寸。蒙版需与第一张参考图同尺寸；脚本能提前核对 PNG 参考图尺寸，JPEG/WebP 的蒙版尺寸由服务校验。
- 单张输入/输出上限 32 MiB，上传总量上限 64 MiB，接口 JSON 上限 128 MiB。
- 不自动重试生图请求，避免重复计费。部分图片下载失败时保留已保存的图片，并返回 `partial` 状态和错误信息。
- 客户端必须能运行本地 Python 脚本并展示本机图片。本版面向 Codex CLI、Codex/Work 桌面端的本地执行环境；未提供网页或云端运行的托管服务。
- 本项目没有默认公共服务器，不代付 API 费用，也不将用户密钥发送给仓库维护者。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试使用临时配置、临时安装目录和本机模拟 HTTP 服务，不调用真实生图 API、不读取真实密钥、不产生生图费用。覆盖接口格式、编辑上传、凭据隔离、错误脱敏、不重试、图片下载、插件引用打包和 skill 独立安装。

## 新增组件

1. 在 `skills/<name>/` 维护可独立使用的 skill 与所需资源。
2. 如果需要插件入口，在 `plugins/<name>/` 添加 `plugin.json`、兼容清单和 `skills.json`。
3. 在 `.agents/plugins/marketplace.json` 添加可选安装项。
4. 更新版本号后运行 `python3 scripts/build_plugins.py`。

一个插件可以引用多个共享 skill。同一 skill 也可以被多个插件引用。`dist/` 是生成物，不手工维护。

## 许可证

GPL-3.0-only。见 [LICENSE](LICENSE)。
