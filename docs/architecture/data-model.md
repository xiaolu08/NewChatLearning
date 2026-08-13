---
title: NewChatLearning 数据模型
version: 0.4
date: 2026-08-13
status: 当前有效
---

# NewChatLearning 数据模型

## 摘要

SQLite 数据库需要同时支持原版问答权重语义、群/标签/全局检索、贡献追踪、媒体生命周期、权限和审计。问题与答案必须分表，消息组件使用版本化 JSON；所有删除默认软删除或先备份，以支持预览和恢复。

## 数据设计

### 主要实体

| 实体 | 关键字段 | 用途 |
| --- | --- | --- |
| `groups` | `group_id`、模式、学习/回复覆盖配置、保留期 | 群级控制 |
| `learning_targets` | 群、目标 QQ、启用时间 | 定向用户学习 |
| `questions` | 群/词库域、规范化哈希、组件 JSON、纯文本、频次、正则标志 | 问题索引与三级匹配 |
| `answers` | 问题 ID、组件 JSON、权重、首次/最后出现、状态 | 候选答案 |
| `contributions` | 消息 ID、发送者、问题/答案关联、时间 | 撤回和按成员删除 |
| `pending_messages` | 平台、群、发送者、消息 ID、时间、组件 JSON | 跨重启保存尚未固化的学习链尾部 |
| `media_assets` | 哈希、类型、路径、大小、状态、来源、最后检查 | 媒体去重和清理 |
| `answer_media` | 答案与媒体关系、组件位置 | 失效组件降级 |
| `tags` / `group_tags` | 标签和群关系 | 标签词库 |
| `filters` | 规则、类型、作用域、启用状态 | 回复前过滤 |
| `blacklist_events` | 用户、群、命中规则、计数 | 黑名单容错 |
| `admins` | QQ、角色、群范围 | 三层权限 |
| `reply_records` | QQ 消息 ID、答案 ID、发送时间 | 快速删除和追踪 |
| `scheduled_tasks` | 计划、动作、目标、状态 | 定时任务 |
| `settings` | 键、JSON 值、版本、来源、时间 | 单一配置源 |
| `audit_logs` | 操作者、动作、对象、摘要、时间、结果 | 审计 |
| `backups` | 路径、范围、校验和、创建原因 | 恢复 |

### 组件 JSON

每个组件包含 `schema_version`、`type`、稳定匹配字段、可发送字段和平台扩展。问题匹配表示必须排除临时 URL、文件路径、OneBot 消息 ID；答案发送表示可引用本地媒体哈希。未知类型原样保存在 `raw` 中并标为不可发送，不得因新版组件导致整个词条损坏。

### 一致性规则

- 相同规范化问题在同一词库域内唯一。
- 相同答案组件序列增加权重，不重复创建答案。
- 媒体按内容哈希去重；删除答案只在无引用时回收媒体。
- 撤回尚未固化消息时回滚学习链；已固化贡献保留审计并按配置处理。
- 删除成员贡献前建立范围备份；只删除其答案贡献，问题无答案后再删除。
- 配置更新使用递增版本进行乐观并发控制。

### 索引

- 问题：`library_scope + normalized_hash` 唯一索引。
- 答案：`question_id + normalized_hash` 唯一索引。
- 贡献：`platform_message_id`、`sender_id + group_id + created_at`。
- 媒体：`content_hash` 唯一索引，`status + last_checked_at` 维护索引。
- 文本候选：SQLite FTS 表保存可检索纯文本；最终相似度仍使用兼容算法确认。

当前 schema v3 已在 `questions` 中加入 `plain_text` 与 `is_regex`。从 schema v1/v2 升级时，会从问题组件 JSON 的首个文本组件回填纯文本；自动学习的问题保持非正则，只有受权限控制的自定义问答和旧词库迁移可以设置正则标记。FTS 候选索引留待大词库性能阶段加入。

schema v4 为 `media_assets` 增加原始名称和远程来源字段，并把旧 `available` 状态迁移为 `healthy`。消息组件只保存 `media_path` 相对路径和内容哈希，不保存成功本地化后的 Base64 或本机绝对路径。

## 相关文档

- [系统设计](./system-design.md)
- [NapCat 消息兼容矩阵](../compatibility/napcat-messages.md)
- [首个 Beta 验收标准](../testing/beta-acceptance.md)

## 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-08-13 | 0.1 | 建立首版 SQLite 实体和一致性规则。 |
| 2026-08-13 | 0.2 | 实现 schema v2，相邻消息固化、待固化消息与 v1 原地升级。 |
| 2026-08-13 | 0.3 | 实现 schema v3，加入纯文本匹配字段、正则标记及旧数据回填。 |
| 2026-08-13 | 0.4 | 实现 schema v4，加入媒体来源元数据、健康状态迁移和相对路径约束。 |
