# NewChatLearning

> **Beta 开发项目**
>
> NewChatLearning 目前处于设计与开发阶段，尚未发布可安装的 AstrBot 插件，也不应被视为可用于生产环境的正式版本。

NewChatLearning 是 [ChatLearning](https://github.com/JHue58/ChatLearning) 面向 AstrBot 生态的移植与现代化项目。首个版本以 Windows、AstrBot、NapCat（OneBot v11）和 QQ 群聊为目标运行环境。

项目保留 ChatLearning 的无 Token 学习模式：根据群聊中的相邻消息建立问答关系，并通过本地算法完成匹配与回复选择，不依赖 LLM 生成内容。

## 设计原则

- **核心流程不依赖 LLM**：学习、匹配、排序和回复选择不会调用大语言模型，也不会主动产生模型 Token 费用。
- **完整行为兼容**：首个可用 Beta 以覆盖原版 ChatLearning 的用户可观察功能为发布前提。
- **原生接入 AstrBot**：消息事件、配置、生命周期和消息发送均使用当前 AstrBot 接口。
- **可靠的本地存储**：结构化数据使用 SQLite，媒体资源在本地持久化，并提供配额、健康检查和清理能力。
- **明确的管理边界**：命令、WebUI、导入、删除和配置修改均受权限控制并记录审计日志。
- **安全迁移旧词库**：旧 `.cl` 文件通过受限迁移流程检查和导入，不在插件主进程中直接反序列化未知数据。

## 计划功能

- 按相邻群消息建立问答链，支持自定义会话间隔
- 分群词库、共享词库、群标签和合并词库
- 精确匹配、正则匹配和基于 jieba 的文本相似度匹配
- 答案权重、回复概率、冷却时间、等待时间和类型阈值
- 文本、图片、语音、@、引用、转发消息、卡片和文件处理
- 内容过滤、敏感词、黑名单、自动清理和快速删除
- 兼容原版命令，并提供统一的 `/ncl` 管理命令
- 静默学习：持续学习指定群聊，但不在该群触发自动词库回复
- 定向学习：学习指定群成员的回复方式
- 媒体持久化、内容去重、空间配额和失效媒体清理
- 受限导入旧 `.cl` 词库，并生成兼容性与媒体状态报告
- 定时任务、备份恢复、导入导出和成员贡献删除
- Windows 本地 TTS、GPT-SoVITS、本地 HTTP TTS 和可选云端 TTS
- 内置管理 WebUI，覆盖统计、词库、群组、媒体、语音、权限、任务、备份和诊断

## 兼容范围

首个 Beta 的兼容承诺如下：

| 组件 | 目标环境 |
| --- | --- |
| 操作系统 | Windows |
| Bot 框架 | AstrBot |
| QQ 协议端 | NapCat |
| 通信协议 | OneBot v11 |
| 会话类型 | QQ 群聊 |

其他操作系统、QQ 协议实现和消息平台暂不属于首版兼容范围。

## 开发进度

已经完成：

- ChatLearning 原版源码与功能审计
- AstrBot 4.27.3 插件接口研究
- 三份旧版共享 `.cl` 词库的非执行式结构检查
- 系统架构、数据存储和 NapCat 消息兼容设计
- WebUI、安全、TTS 与首个 Beta 发布标准
- 私有 GitHub 仓库、数据排除规则和开源协议文件
- 可由 AstrBot 4.27.3 加载的插件代码骨架
- SQLite schema、统一配置读取和运行状态服务骨架
- `/ncl help`、`/ncl status` 管理命令与只读 Dashboard 状态页
- NapCat/OneBot 群消息规范化与版本化组件 JSON
- 可配置群白名单的相邻消息学习、重复答案增权和超时断链
- 尚未固化消息的持久化暂存与群消息撤回清理
- SQLite 分群精确匹配与原版语义的答案权重随机
- 回复群白名单、静默学习群、概率、等待、冷却和文本长度限制
- 本地回复成功后停止当次 LLM，并写入 AstrBot 群消息历史

尚未完成：

- 正则匹配、jieba 余弦相似度和候选类型阈值
- NapCat 长尾消息组件、媒体持久化与回复降级
- 完整管理 WebUI 与独立登录安全机制
- TTS 驱动与旧词库迁移工具
- Windows + AstrBot + NapCat 实机验收

## 技术文档

- [文档中心](./docs/README.md)
- [产品需求与项目约束](./docs/product/requirements.md)
- [系统架构与实现规格](./docs/architecture/system-design.md)
- [数据库与持久化设计](./docs/architecture/data-model.md)
- [NapCat 消息兼容矩阵](./docs/compatibility/napcat-messages.md)
- [WebUI 与安全规范](./docs/security/webui-security.md)
- [首个 Beta 验收标准](./docs/testing/beta-acceptance.md)

## 数据与隐私

群聊词库可能包含个人信息、攻击性内容、政治内容或已经失效的媒体引用。共享 `.cl` 样本、运行数据库、下载媒体、备份、日志和密钥均被排除在版本控制之外。

云端 TTS 是默认关闭的可选功能，与无 Token 的学习和回复流程相互独立。只有管理员主动配置并启用后，才可能产生对应服务商的费用。

## 项目关系

NewChatLearning 是基于 ChatLearning 功能与受协议保护实现进行开发的独立移植项目，不是 ChatLearning、AstrBot、NapCat 或腾讯 QQ 的官方版本，也不代表上述项目维护者的立场。

原项目归属与修改说明见 [NOTICE](./NOTICE)。

## 开源协议

NewChatLearning 使用 [GNU Affero General Public License v3.0](./LICENSE)。原 ChatLearning 项目的著作权仍归其相应作者与贡献者所有。
