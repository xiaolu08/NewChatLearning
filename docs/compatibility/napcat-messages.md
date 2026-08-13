---
title: NewChatLearning NapCat 消息兼容矩阵
version: 0.1
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
| `Image` | Image/CQ image | 是 | 下载本地并哈希 | 本地文件优先，失败则跳过 |
| `FlashImage` | 闪照或图片降级 | 是 | 下载本地 | 平台支持则闪照，否则普通图片 |
| `Face` | Face | 是 | ID + 名称 | 按 ID 重发，失败用文本占位 |
| `MarketFace` | 商城表情/原始 CQ | 尽力 | 原始字段 + 可用媒体 | 支持时重发，否则跳过 |
| `At` | At | 是 | QQ + 显示名 | 重发前验证目标，必要时降级文本 |
| `Quote` | Reply | 不作为稳定问题字段 | 引用摘要 | 新回复不复用旧消息 ID，降级为摘要 |
| `Voice` | Record | 可选 | 下载音频 | 本地音频重发，失败跳过 |
| `Forward` | Forward/Nodes | 尽力 | 规范化节点摘要 | 支持时重建，否则文本摘要 |
| `App` | JSON 卡片 | 谨慎 | 原始 JSON | 通过白名单验证后重发，否则跳过 |
| `Xml` | XML 卡片 | 谨慎 | 原始 XML | 通过白名单验证后重发，否则跳过 |
| `Json` | JSON 卡片 | 谨慎 | 原始 JSON | 同 App |
| `File` | File | 不参与文本相似度 | 元数据，可选下载 | 仅在文件存在且平台允许时重发 |
| `MusicShare` | 音乐分享/JSON | 是 | 结构字段 | 重建分享或降级为链接文本 |
| `Dice` | Dice | 是 | 点数/类型 | 平台支持则发送，避免重新随机改变语义 |
| `Video` | Video | 可选 | 配额允许时下载 | 本地文件重发 |

### 撤回

- 监听群消息撤回通知并使用 OneBot 消息 ID 定位贡献。
- 尚在学习链中的消息立即移除并修复前后关系。
- 已形成问答的消息默认不自动删除，以避免历史权重突然变化；提供配置项和审计操作按贡献删除。

### 媒体健康

- 状态：`healthy`、`missing`、`expired_remote`、`unsupported`、`quarantined`。
- 扫描只读检测，清理必须先预览。
- 默认删除失效组件；答案无剩余可发送组件时删除答案。

## 相关文档

- [系统设计](../architecture/system-design.md)
- [数据模型](../architecture/data-model.md)
- [首个 Beta 验收标准](../testing/beta-acceptance.md)

## 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-08-13 | 0.1 | 建立原版消息类型到 NapCat/AstrBot 的兼容策略。 |
