# NewChatLearning 文档中心

这里汇总 NewChatLearning 的产品需求、技术设计、兼容性研究、安全规范和发布验收标准。

## 产品

- [项目概述](./product/project-overview.md)：项目定位、目标环境、核心约束和当前状态。
- [产品需求与约束](./product/requirements.md)：首个 Beta 的功能范围和不可变要求。
- [决策记录](./product/decision-log.md)：需求讨论中已经确认的关键产品与技术决策。

## 架构

- [系统设计](./architecture/system-design.md)：组件分层、消息流水线、运行模式、命令和迁移方案。
- [数据模型](./architecture/data-model.md)：SQLite 实体、消息组件格式、索引和一致性规则。

## 兼容性

- [NapCat 消息兼容矩阵](./compatibility/napcat-messages.md)：原版消息类型在 AstrBot/NapCat 中的保存、匹配和重发策略。

## 安全

- [WebUI 与安全规范](./security/webui-security.md)：管理页面、认证、网络访问、密钥和配置一致性要求。

## 测试与发布

- [首个 Beta 验收标准](./testing/beta-acceptance.md)：功能、消息兼容、安全、可靠性和发布门槛。
- [0.1.0-beta.31.post4 Release Notes](./releases/0.1.0-beta.31.post4.md)：首个公开预览版本的功能、限制和安装包校验信息。
- [0.1.0-beta.31.post5 Release Notes](./releases/0.1.0-beta.31.post5.md)：跨群管理命令与私聊全局开关更新。
- [0.1.0-beta.31.post6 Release Notes](./releases/0.1.0-beta.31.post6.md)：AstrBot 4.27.2 私聊事件过滤器加载热修复。
- [0.1.0-beta.31.post12 Release Notes](./releases/0.1.0-beta.31.post12.md)：按群控制全局词库查询范围及群列表展示更新。
- [0.1.0-beta.31.post13 Release Notes](./releases/0.1.0-beta.31.post13.md)：补充全局词库跨群命令的完整聊天帮助。
- [0.1.0-beta.31.post14 Release Notes](./releases/0.1.0-beta.31.post14.md)：处理失效媒体发送失败，避免回复处理异常中断。
- [0.1.0-beta.31.post15 Release Notes](./releases/0.1.0-beta.31.post15.md)：修复原始 OneBot 组件写入 AstrBot 消息历史时的兼容性错误。
- [0.1.0-beta.31.post16 Release Notes](./releases/0.1.0-beta.31.post16.md)：补强 NapCat 原始媒体与长尾消息补全，优先使用本地媒体。
- [0.1.0-beta.31.post17 Release Notes](./releases/0.1.0-beta.31.post17.md)：修复原始消息补全导致文本与 QQ 表情组件重复的问题。
- [0.1.0-beta.31.post18 Release Notes](./releases/0.1.0-beta.31.post18.md)：整理更新日志时间线并恢复插件管理页的完整版本展示。
- [0.1.0-beta.31.post19 Release Notes](./releases/0.1.0-beta.31.post19.md)：补齐按群设置独立回复概率的跨群命令与 WebUI 设置。
- [0.1.0-beta.31.post20 Release Notes](./releases/0.1.0-beta.31.post20.md)：限制群聊跨群管理回执，避免披露其他群配置与群号。
- [0.1.0-beta.31.post21 Release Notes](./releases/0.1.0-beta.31.post21.md)：导入词库独立管理、按群绑定、启停、替换更新和带备份删除。
- [0.1.0-beta.31.post22 Release Notes](./releases/0.1.0-beta.31.post22.md)：WebUI 紧凑控制台、响应式侧栏和可访问交互升级。

## 研究资料

- [上游项目评估](./research/upstream-assessment.md)：原 ChatLearning 的价值、限制、迁移风险和验证建议。
- [功能兼容矩阵](./research/feature-parity.md)：原版能力到 NewChatLearning 的实现映射。
- [命令兼容性核对](./research/command-parity.md)：上游聊天命令与当前命令、WebUI、配置映射及未复现项。

文档以当前有效版本为准。实现变化如果影响外部行为、数据格式、安全边界或兼容范围，必须同步更新相关文档。
