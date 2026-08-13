---
title: NewChatLearning NapCat 消息兼容矩阵
version: 0.2
date: 2026-08-13
status: 当前有效
---

# NewChatLearning NapCat 消息兼容矩阵

## 摘要

首版以 AstrBot 的 OneBot v11 组件为主接口，必要时读取 NapCat 原始事件补齐撤回、卡片和转发信息。所有类型都要区分“可匹配、可长期保存、可重新发送”，旧 Mirai 数据只承诺尽力降级，不承诺过期媒体恢复。

## 兼容策略

| 原版类型 | NapCat/AstrBot 方向 | 匹配 | 保存 | 重发策略 |
| --- | --- | --- | --- | --- |
| `Plain` | Plain | 是 | JSON | 原文发送 |
| `Image` | Image/CQ image | 是 | 已实现本地下载与 SHA-256 去重 | 本地文件优先，缺失时回退远程 URL |
| `FlashImage` | 闪照或图片降级 | 是 | 已实现本地下载 | 当前按普通图片稳定降级 |
| `Face` | Face | 是 | ID + 名称 | 按 ID 重发，失败用文本占位 |
| `MarketFace` | 商城表情/原始 CQ | 尽力 | 原始字段 + 可用媒体 | 支持时重发，否则跳过 |
| `At` | At | 是 | QQ + 显示名 | 重发前验证目标，必要时降级文本 |
| `Quote` | Reply | 不作为稳定问题字段 | 引用摘要 | 新回复不复用旧消息 ID，降级为摘要 |
| `Voice` | Record | 可选 | 已实现本地下载 | 本地音频重发，缺失时回退远程 URL |
| `Forward` | Forward/Nodes | 尽力 | 规范化节点摘要 | 支持时重建，否则文本摘要 |
| `App` | JSON 卡片 | 谨慎 | 原始 JSON | 通过白名单验证后重发，否则跳过 |
| `Xml` | XML 卡片 | 谨慎 | 原始 XML | 通过白名单验证后重发，否则跳过 |
| `Json` | JSON 卡片 | 谨慎 | 原始 JSON | 同 App |
| `File` | File | 不参与文本相似度 | 已实现元数据与可选下载 | 仅在本地文件存在或仍有远程 URL 时重发 |
| `MusicShare` | Music | 是 | 结构字段 | 已使用 AstrBot Music 组件重建 |
| `Dice` | Dice | 是 | 点数/类型 | 已按记录点数重建，避免重新随机改变语义 |
| `Video` | Video | 可选 | 已按单文件与总配额下载 | 本地文件优先，缺失时回退远程 URL |

### 撤回

- 监听群消息撤回通知并使用 OneBot 消息 ID 定位贡献。
- 尚在学习链中的消息立即移除并修复前后关系。
- 已形成问答的消息默认不自动删除，以避免历史权重突然变化；提供配置项和审计操作按贡献删除。

### 媒体健康

- 状态：`healthy`、`missing`、`expired_remote`、`unsupported`、`quarantined`。
- 扫描只读检测，清理必须先预览。
- 默认删除失效组件；答案无剩余可发送组件时删除答案。

### 当前媒体持久化实现

- 只在启用学习的群保存新媒体，纯回复群不会因查询词库而下载内容。
- 支持 HTTP(S)、Base64、本地路径和 Windows `file:` URI；写入时流式检查单文件上限。
- HTTP(S) 下载拒绝本机、内网、保留地址及指向这些地址的重定向，避免 SSRF。
- 文件使用 SHA-256 内容寻址并保存相对路径；同一内容即使原扩展名不同也只保留一份。
- Base64 原文和本机绝对路径不会写入词库；远程 URL 可保留为本地文件丢失时的降级来源。
- 总配额达到后停止新增文件，但允许复用已有哈希，文本和结构学习继续。

## 相关文档

- [系统设计](../architecture/system-design.md)
- [数据模型](../architecture/data-model.md)
- [首个 Beta 验收标准](../testing/beta-acceptance.md)

## 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-08-13 | 0.1 | 建立原版消息类型到 NapCat/AstrBot 的兼容策略。 |
| 2026-08-13 | 0.2 | 实现核心媒体本地化、去重、配额、SSRF 防护与本地优先重发。 |
