# ChatLearning 命令兼容性核对

核对日期：2026-08-14  
对照范围：上游 ChatLearning `ChatClass.py`、`Chatmain.py` 与 README 中公开的命令；NewChatLearning 当前 `main.py`、配置 schema、WebUI 路由和测试。

## 结论摘要

NewChatLearning 已完整恢复上游群管理命令的主要语义，但尚未完整复现上游所有聊天命令字符串。当前分为三类：

1. **命令兼容**：可以使用原命令格式，并写入 NewChatLearning 的统一配置服务。
2. **功能等价**：能力已存在，但入口迁移到 `/ncl`、AstrBot 插件配置或 WebUI。
3. **尚未复现**：当前没有等价行为，或原功能与 AstrBot 架构冲突，需要后续设计。

普通成员、群聊子管理员和全局/插件管理员的权限边界以 NewChatLearning 当前权限模型为准；“功能等价”不代表可以继续发送原命令字符串。

## 命令矩阵

| 上游命令 | 状态 | NewChatLearning 入口或说明 |
| --- | --- | --- |
| `learning` | 命令兼容 | 管理员私聊 `!learning` 切换全局学习主开关；群聊 `!learning` 切换当前群学习；`/ncl learning on\|off` |
| `learning <秒>` | 功能等价 | WebUI/插件配置中的 `learning.interval_seconds`；未保留旧参数命令 |
| `reply` | 命令兼容 | 管理员私聊 `!reply` 切换全局回复主开关；群聊 `!reply` 切换当前群回复；`/ncl reply on\|off` |
| `reply <百分比>` | 功能等价 | WebUI/插件配置中的 `reply.probability_percent`；旧参数命令未保留 |
| `reply -s <百分比> <群号>` | 功能等价 | 设置目标群独立回复概率，未覆盖群继承全局概率 |
| `reply -d <群号>` | 功能等价 | 删除目标群独立概率并恢复继承全局概率 |
| `voicereply` 及其概率参数 | 功能等价 | WebUI TTS 页面；不通过聊天命令配置语音驱动或额度 |
| `cosmatch`、`cosmatch <匹配率>` | 功能等价 | WebUI/插件配置中的本地相似度开关和阈值 |
| `atreply` | 功能等价 | WebUI/插件配置中的 `reply.at_force_reply` |
| `replywait <基准> <浮动>` | 功能等价 | WebUI/插件配置中的等待时间与抖动 |
| `replycd <秒>` | 功能等价 | WebUI/插件配置中的回复冷却时间 |
| `replylength <字数>` | 功能等价 | WebUI/插件配置中的纯文本答案长度上限 |
| `merge <秒>` | 架构差异 | 全局词库改为实时联合查询，不再执行原版定时合并；通过 `library.mode` 配置 |
| `typefreq <消息类型> <次数>` | 功能等价 | WebUI/插件配置中的消息类型频次阈值 |
| `add/remove learning <群号...>` | 命令兼容 | 仅全局管理员/插件管理员；原子更新学习群 |
| `add/remove learnings <群号...>` | 命令兼容 | 同时更新学习群与回复群 |
| `add/remove reply <群号...>` | 命令兼容 | 原子更新回复群 |
| `add/remove tag <标签> <群号...>` | 命令兼容 | 添加标签；移除时清除目标群全部标签 |
| `add/remove subadmin <群号...>` | 命令兼容 | 添加时读取目标群群主和群管理员；移除群级授权 |
| `add/remove unmerge <群号...>` | 命令兼容 | 更新不进入全局词库的群 |
| `add/remove globe <群号...>` | 扩展命令 | 按目标群允许或禁止查询全局/标签共享词库；与控制来源群的 `unmerge` 独立 |
| `add/remove share <群号...> <联动组名>` | 扩展命令 | 创建命名联动组或调整成员；仅共享直接成员群的本群词库，不传播其他词库权限 |
| `add/remove autotask <任务名>` | 功能等价 | WebUI 任务页面；仅允许内建安全任务，不执行任意命令或脚本 |
| `autotaskinfo` | 功能等价 | WebUI 任务列表与运行历史 |
| `autotaskcommand` | 功能等价 | WebUI 任务页面说明；原版任意特殊命令机制不开放 |
| `fastdelete` | 功能等价 | `!d`/`!delete` 引用 Bot 回复或按最近序号删除；权限由插件管理员和群子管理员控制 |
| `check` | 功能等价 | `/ncl status` 与 WebUI 概览/诊断页面 |
| `grouplist` | 命令兼容 | `!grouplist` 显示学习、回复、自主管理、全局来源排除、仅本群查询群和联动词库；可从管理群或管理员私聊发送 |
| `globe` | 语义改进 | 不提供容易影响全部群的无参数总开关；使用 `!add/remove globe <群号...>` 按目标群控制共享词库查询 |
| `setadmin <QQ号>` | 功能等价 | WebUI 权限页中的插件管理员 QQ 号 |
| `setbotname <昵称>` | 尚未复现 | Bot 身份由 AstrBot 平台配置管理，插件没有独立昵称配置 |
| `settemp <条数>` | 尚未复现 | 当前没有上游同名的按群消息缓存数量命令 |
| `blackfreq <次数>` | 功能等价 | WebUI/插件配置中的敏感词黑名单阈值 |
| `importstock <文件名>` | 功能等价 | WebUI 旧 `.cl` 隔离扫描、转换计划和确认导入；不执行任意旧文件格式 |
| `setvoicept <训练集>` | 功能等价 | WebUI TTS 页面配置 GPT-SoVITS/HTTP 驱动与音色 |
| `uploadwav` | 尚未复现 | 不提供聊天中上传训练音频；语音驱动按现有本地/云端接口配置 |
| `admin` | 功能等价 | `/ncl` 当前群管理命令；不建立原版阻塞式管理会话 |
| `help`、`?`、`？` | 功能等价 | `/ncl help` 与 README/文档中心 |
| `exit` | 尚未复现 | 插件不能通过群消息退出 AstrBot 或卸载自身 |

## 当前风险与后续决策

- 按群独立回复概率已通过统一配置服务、WebUI 群设置和 `!reply -s/-d` 跨群命令实现，不直接修改配置文件。
- **`settemp`** 需要先定义它对应的是待固化消息缓存、历史消息缓存还是媒体队列，不能仅按名称猜测。
- **`merge`** 不应机械恢复定时合并线程；当前全局模式的实时联合查询是有意的架构差异。
- **`autotaskcommand`、`uploadwav`、`exit`** 涉及任意命令执行、外部文件注入或进程控制，不应为了字符串兼容而恢复原行为。
- 本矩阵只证明离线代码入口和配置映射；NapCat 目标群资料读取、AstrBot 命令分发和真实 QQ 群行为仍需 Windows 实机验收。
