# 参与贡献

感谢你帮助改进 NewChatLearning。项目目前处于公开预览 Beta，优先接受可复现的错误报告、兼容性反馈、测试补充和文档改进。

## 开始之前

请先阅读：

- [README](./README.md)
- [系统设计](./docs/architecture/system-design.md)
- [数据模型](./docs/architecture/data-model.md)
- [Beta 验收标准](./docs/testing/beta-acceptance.md)
- [WebUI 与安全规范](./docs/security/webui-security.md)

## 提交 Issue

提交 Bug 时请说明：

- NewChatLearning 版本、AstrBot 版本、Windows 版本
- NapCat 和 OneBot v11 相关信息
- 是否能稳定复现
- 最小复现步骤和预期/实际结果
- 脱敏后的日志或截图

不要在 Issue 中发布 `.cl` 文件、运行数据库、聊天记录、媒体文件、备份、Cookie 或 API 密钥。安全漏洞请按 [安全策略](./SECURITY.md) 私下报告。

## 提交代码

1. 从 `main` 创建分支。
2. 保持改动聚焦，不提交运行时数据、词库样本、媒体、备份、日志或凭据。
3. 为行为变化补充针对性测试。
4. 运行 `python -m pytest -q` 和 Markdown 链接检查。
5. 同步更新受影响的 README、`docs/` 文档和 Beta 验收状态。
6. 在 Pull Request 中说明测试环境和未覆盖的风险。

核心学习与回复流程必须保持零 LLM Token；任何云端 TTS 或外部服务调用都必须由管理员主动配置并清楚标注费用与隐私影响。

## 许可证

NewChatLearning 使用 AGPL-3.0-only。贡献代码应在该许可证下发布，并保留 [NOTICE](./NOTICE) 中的上游归属说明。
