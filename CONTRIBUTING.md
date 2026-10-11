# 贡献指南

本项目使用 Python 3.11+ 标准库，不需要安装开发依赖。

## 修改原则

- skill 源码只放在 `skills/`。不要在 `plugins/` 维护技能副本。
- `plugins/<name>/skills.json` 声明引用；构建时自动包含对应技能。
- 保持 skill 可脱离仓库和插件系统独立使用。相对资源路径以其 `SKILL.md` 所在目录为准。
- 用户图片配置与客户端聊天配置独立。仅在用户明确选择导入时读取 Codex 的服务地址和 API Key。
- 不提交密钥、个人配置或生成图片，不记录 Authorization 或原始服务响应。
- 生图调用可能产生费用；失败后不自动重试，不在测试或构建中调用真实服务。
- 保持标准插件清单与 `.codex-plugin/plugin.json` 的名称和版本一致。

## 提交前

```bash
python3 -m unittest discover -s tests -v
python3 scripts/build_plugins.py
```

自动化测试只使用本机模拟服务与临时目录。修改接口行为、配置或分发方式时，补充能验证用户可见行为的测试。

贡献按仓库的 GPL-3.0-only 许可证提供。
