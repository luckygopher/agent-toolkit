# Agent Toolkit

可独立使用的 AI skills，以及按需安装的插件。skill 源码只维护一份，插件按声明引用并打包。

**用户体验：安装 → 配置 → 直接使用。无需安装 Python、Node 或 Go。**

首个组件 **Image Gen** 支持用户自己的 API 地址、API Key 和图片模型，兼容 OpenAI 图片接口的服务（包括相应的 AiPai 接口）。文生图、参考图编辑、蒙版编辑和多张图片输出均在本机执行。

## 安装插件

在 Codex/Work 桌面端的“添加插件市场”中填写：

| 输入框 | 内容 |
| --- | --- |
| 来源 | `luckygopher/agent-toolkit` |
| Git 引用 | `marketplace` |
| 稀疏路径 | 留空 |

市场中的插件指向已经构建好的 `marketplace` 分支，不需要用户 clone 后手动构建。找到 **Image Gen** 并安装，安装包自带六个平台的运行程序：macOS、Linux、Windows，各含 arm64 和 amd64。

本机开发或试用构建包也可把 `dist/` 的绝对路径作为市场来源。

安装后点击 **Setup**，填写或导入图片 API 地址、Key、模型，保存后直接在聊天中说：

> 使用 Image Gen 生成一张鹈鹕骑自行车的插画，并直接展示。

## 只安装 skill

skill 不依赖插件系统。可以下载独立 skill ZIP，解压后将整个 `image-gen/` 文件夹放到客户端的技能目录，例如 `~/.agents/skills/`。ZIP 同样含完整运行程序，无需安装语言环境。

从 GitHub 源码直接安装 `skills/image-gen/` 也可以：首次启动时，薄启动脚本下载同项目固定版本、校验 SHA-256 的运行程序，后续直接使用本地文件；不自动安装语言环境或编译代码。

同一客户端通常选择插件或独立 skill 其中一种方式，避免重复出现同名技能。

## 可视化配置

安装后使用 Setup，或对助手说“打开 Image Gen 可视化配置”。也可直接执行启动器：

```sh
# macOS / Linux：路径以实际安装位置为准
sh /absolute/image-gen/scripts/run.sh setup
```

```powershell
# Windows：系统自带 Windows PowerShell 即可，不要求 PowerShell 7
powershell.exe -NoProfile -File C:\absolute\image-gen\scripts\run.ps1 setup
```

Windows 若有策略禁止运行脚本，可直接运行包内对应 CPU 的 `bin/image-gen-windows-*.exe`，无需修改执行策略。

页面支持手动填写、从 Codex 导入连接、默认尺寸、超时与输出目录。已有密钥不会回显到浏览器，替换已有配置需要明确确认。配置页面只监听本机，保存、取消或十分钟到期后退出，无常驻服务或 MCP。

**既有 0.2.x 配置可直接复用，无需重新填写。** 图片配置始终独立于客户端聊天配置：

- macOS/Linux：`${XDG_CONFIG_HOME:-~/.config}/agent-toolkit/image-gen.json`
- Windows：`%APPDATA%\agent-toolkit\image-gen.json`
- 自定义：`IMAGE_GEN_CONFIG_PATH`

配置保存在用户本机；不会修改 Codex 聊天的地址、Key 或模型。导入只读取用户明确选择的 API 地址/Key，不读取 ChatGPT OAuth token。项目维护者不接收用户密钥。

## 命令与参数

以下 `<IMAGE_GEN>` 指对应平台的启动器，或包内的原生程序。

```text
<IMAGE_GEN> status
<IMAGE_GEN> doctor
<IMAGE_GEN> setup --no-open
<IMAGE_GEN> configure --from-codex --model gpt-image-2
<IMAGE_GEN> generate "图片描述" --size 1536x1024 --quality high --count 2 --output-dir /absolute/outputs
<IMAGE_GEN> edit "只更换背景" /absolute/reference.png --output-dir /absolute/outputs
<IMAGE_GEN> edit --prompt-file /absolute/prompt.txt --reference /absolute/reference.png --mask /absolute/mask.png
```

支持 `--model`、`--size auto|1k|2k|4k|WIDTHxHEIGHT`、`--quality`、`--count 1–4`、`--format png|jpeg|webp`、`--background`、`--output-dir`。`4k` 为 `3840x2160`。参考图支持 PNG/JPEG/WebP，编辑最多 16 张；PNG 蒙版尺寸需与第一张参考图相同。

输出 JSON 包含每张图片的绝对路径和实际图片格式，skill 会据此在聊天里展示图片。不覆盖已有图片，不自动重试生图请求；部分图片保存失败会保留成功输出并返回 `partial`。

## 配置优先级

当前请求参数（模型、尺寸等）→ 对应环境变量 → 本机图片配置 → 默认值。

| 环境变量 | 用途 |
| --- | --- |
| `IMAGE_GEN_BASE_URL` | 图片 API 根地址 |
| `IMAGE_GEN_API_KEY` | 图片 API Key |
| `IMAGE_GEN_MODEL` | 图片模型 |
| `IMAGE_GEN_SIZE` | 默认尺寸 |
| `IMAGE_GEN_TIMEOUT` | 请求超时秒数，10–1800 |
| `IMAGE_GEN_OUTPUT_DIR` | 默认图片目录 |
| `IMAGE_GEN_CONFIG_PATH` | 独立配置文件 |

远程 API 使用 HTTPS；本机 HTTP 服务也受支持。HTTPS 使用系统信任机制，正常用户无需额外配置证书。显式 `SSL_CERT_FILE`/`SSL_CERT_DIR` 也受支持。

`doctor` 只发起不带密钥的 HEAD 请求，不生成图片或消耗图片额度。401/404/405 可以说明传输连接可达，并不代表 Key 和模型权限已验证。受限执行环境可能仍需由助手通过执行器申请联网权限；程序不绕过客户端安全控制。

## 仓库结构

```text
agent-toolkit/
├── skills/image-gen/           # 独立 skill、启动器和运行声明
├── plugins/image-gen/          # 插件清单；skills.json 引用共享 skill
├── cmd/image-gen/              # 原生程序入口
├── internal/imagegen/          # 唯一运行实现与内嵌配置页面
├── scripts/                    # 维护者的构建、打包辅助工具
├── .github/workflows/          # 跨平台测试与预构建包发布
└── dist/                       # 可直接安装的产物，不提交到 main
```

`plugins/image-gen/skills.json` 引用根目录中的 `image-gen` skill。构建时把 skill 和运行程序收进独立插件包，不需要维护 skill 副本。编译程序、依赖下载和平台选择由维护者构建流程完成，**不由终端用户承担**。

## 开发与发布（仅维护者）

开发环境需要 Go 1.23+，构建辅助工具使用 Python 3.11+。这两项仅用于维护者构建，用户安装包不需要它们。

```sh
go test -race ./...
python3 scripts/build_plugins.py --plugin image-gen --host-only
python3 -m unittest discover -s tests -v
# 分发版本默认构建全部六个平台
python3 scripts/build_plugins.py --plugin image-gen
```

完整构建产物：

- `dist/plugins/image-gen/`：自包含插件，含六个平台的原生程序。
- `dist/archives/image-gen-0.3.0.zip`：插件 ZIP。
- `dist/archives/image-gen-skill-0.3.0.zip`：独立 skill ZIP。
- `dist/.agents/plugins/marketplace.json`：可直接添加的本地市场。

测试覆盖本机模拟图片服务、HTTPS、配置隔离、Setup 安全与退出、独立安装、启动校验，以及禁止调用 Python/Node/Go 时的运行行为。测试不读取真实密钥或调用真实生图服务。

发布使用同一仓库的 `marketplace` 分支保存预构建插件，并以 `image-gen-v<版本>` 固定运行文件。分发文件附带 SHA-256 和第三方许可声明。

## 许可证

GPL-3.0-only，见 [LICENSE](LICENSE)。原生程序中包含的第三方组件许可见 `skills/image-gen/THIRD_PARTY_NOTICES.md`。
