# Image Gen Plugin

插件通过 `skills.json` 引用根目录的共享 skill，源码不重复维护。

分发包内置 macOS、Linux、Windows 的 arm64/amd64 运行程序。安装后通过 Setup 配置 API 地址、Key 和模型，用户无需安装 Python、Node、Go 或 MCP。

维护者在仓库根目录执行 `python3 scripts/build_plugins.py --plugin image-gen` 生成完整插件、独立 skill ZIP 及本地市场。`--host-only` 仅用于本机开发验证，不作为完整跨平台发布。

应安装生成的 `dist/plugins/image-gen/` 或已发布的 marketplace 包，不直接安装这个源码声明目录。
