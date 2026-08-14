# 更新日志

## 0.1.0-beta.31.post4 - 2026-08-14

首个公开预览版本，基于 Beta 31 云端 TTS 能力和 Beta 31.post2 的 WebUI 会话基础。

### 主要内容

- AstrBot + NapCat + OneBot v11 + QQ 群的 ChatLearning 兼容核心。
- 相邻消息学习、精确/正则/本地相似度匹配、权重和回复策略。
- 分群词库、静默学习、定向学习、过滤、黑名单和成员贡献清理。
- 图片、语音、视频、文件、商城表情、XML、转发等消息组件的本地化与降级处理。
- 旧 `.cl` 词库隔离扫描、群绑定导入计划、自动备份和统计核对。
- WebUI 概览、群设置、词库、媒体、过滤、权限、迁移、TTS、任务、备份、审计和诊断页面。
- Windows 本地 TTS、GPT-SoVITS、通用本地 HTTP TTS，以及可选云端 TTS 驱动。

### WebUI 与热更新

- WebUI 使用无密码“进入”入口，不保存旧版独立管理密码。
- 进入后建立一小时服务端会话，写操作继续使用 CSRF，高风险操作需要二次确认。
- 启动时清理旧 `webui-password.json`。
- 修复 AstrBot 热更新后顶层 `new_chat_learning.*` 模块缓存残留导致前后端版本错配的问题。
- 页面检测到前后端入口模式不一致时停止请求并提示重新安装。

### 已知限制

- 当前仅支持 Windows + AstrBot + NapCat + QQ 群。
- 完整 NapCat 消息类型、长时间任务、大规模词库和全部 WebUI 页面仍需社区实机验证。
- 词库媒体问答人工编辑与批量操作暂不纳入当前产品范围。
- 云端 TTS 已实现配置、DPAPI 密钥保护和额度控制，但真实付费服务商调用需要用户自行验收。

### 校验

安装包：`NewChatLearning-0.1.0-beta.31.post4.zip`

SHA-256：`3FC9A9150DA0EE1E0088CB970473909B4A83DF728CCFAA7D35BBDCB5C554E9C3`
