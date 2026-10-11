# 贡献指南

## 用户与开发环境分开

用户只安装预构建插件或 skill 并配置 API，不安装语言环境。原生实现使用 Go；Python 只用于维护者打包和分发测试，不应回到用户运行链路。

## 源码组织

- `skills/` 维护独立 skill 和薄启动器。
- `plugins/` 只声明元数据与 skill 引用，禁止手工维护副本。
- 原生业务代码位于 `internal/imagegen/`，入口为 `cmd/image-gen/`。
- 配置页面内嵌到可执行程序，独立 skill 不依赖仓库其他目录。
- API 配置与聊天配置独立，不记录密钥或原始服务响应。
- 不自动重试可能计费的请求，不在测试中使用真实账户。

## 提交前检查

```sh
go test -race ./...
python3 scripts/build_plugins.py --plugin image-gen --host-only
python3 -m unittest discover -s tests -v
```

发布前使用默认全平台构建，并同时更新插件版本、`scripts/runtime-version` 和变更记录。源代码、原生文件和 SHA-256 对应同一发布版本。不要提交 bin/ 或 dist/ 到 main；预构建分发由 marketplace 分支管理。

贡献使用 GPL-3.0-only。
