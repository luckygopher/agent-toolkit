# Image Gen Plugin

此目录维护插件声明，不维护 skill 副本。

- `plugin.json`：标准插件元数据。
- `.codex-plugin/plugin.json`：Codex 兼容入口。
- `skills.json`：引用仓库根目录 `skills/` 中的技能名称。

本插件引用 `skills/image-gen/`。从仓库根目录运行：

```bash
python3 scripts/build_plugins.py --plugin image-gen
```

生成的 `dist/plugins/image-gen/` 包含完整技能及脚本，可以独立安装；`dist/archives/image-gen-0.1.0.zip` 可用于分发。应安装构建产物，不要直接安装此源码声明目录。

配置和使用方式见仓库 README。无 MCP 服务，无后台进程。
