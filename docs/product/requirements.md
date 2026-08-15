---
title: NewChatLearning 产品需求与约束
version: 1.0
date: 2026-08-13
status: 当前有效
---

# NewChatLearning 产品需求与约束

## 摘要

NewChatLearning 是 ChatLearning 面向 AstrBot + NapCat 的 QQ 群完整移植项目。首个对用户可运行的版本必须覆盖原版全部能力，同时加入静默学习、定向用户学习、现代 WebUI、媒体持久化和安全迁移；在用户批准前始终标记为 Beta。核心学习和回复不调用 LLM，云端费用只允许来自用户主动启用的 TTS。

## 需求定义

### 核心约束

1. 项目名为 `NewChatLearning`。
2. 首版平台为 Windows、AstrBot、NapCat、OneBot v11、QQ 群。
3. 首个公开预览 Beta 可以在明确披露未完成实机项目的前提下发布；完成版仍以覆盖原 ChatLearning 全部用户可观察功能为目标。
4. 核心学习、匹配、选择和回复路径零 LLM Token。
5. 本地词库回复成功后停止当次 LLM，尽可能写入后续会话历史。
6. 保留相邻消息问答链和同一成员连续发言学习语义。
7. 普通成员不能使用插件命令，匹配后静默忽略。
8. 自动回复前过滤，学习时保留原始内容。
9. 在用户批准前所有版本和仓库说明保持 Beta / 非正式状态。
10. 许可证采用与原项目兼容的 AGPL-3.0，并保留原作者归属和修改说明。

### NewChatLearning 扩展能力

- 静默学习：指定群完整学习但绝不自动词库回复，管理员命令除外。
- 定向学习：只采集目标用户紧随群消息给出的答案。
- 媒体长期保存、10 GB 默认配额、失效扫描和清理。
- 完整管理 WebUI，与 AstrBot 插件配置共享同一配置服务。
- 三层权限、审计日志、危险操作二次认证。
- 多厂商 TTS 和通用 HTTP TTS，云端驱动默认关闭。
- 受限 `.cl` 导入、迁移报告、外部词库按群绑定与独立启停/更新/删除、备份恢复和成员贡献删除。

### 实施范围

进入本地项目工程阶段，依次建立插件骨架、数据库与领域层、NapCat 适配、命令与权限、WebUI、TTS、迁移工具、全功能测试和 Beta 发布材料。GitHub 推送在用户提供并确认目标仓库后进行。

## 相关文档

- [项目概述](./project-overview.md)
- [决策记录](./decision-log.md)
- [系统设计](../architecture/system-design.md)
- [数据模型](../architecture/data-model.md)
- [NapCat 消息兼容矩阵](../compatibility/napcat-messages.md)
- [WebUI 与安全规范](../security/webui-security.md)
- [首个 Beta 验收标准](../testing/beta-acceptance.md)

## 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-08-13 | 1.0 | 将探索结论收口为项目执行约定。 |
