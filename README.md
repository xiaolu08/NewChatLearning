# NewChatLearning

> **ChatLearning for AstrBot · 公开预览 Beta**

将 QQ 群聊里的相邻消息整理成可检索的问答词库，让 AstrBot 用本地算法学习和回复。核心学习、匹配和回复流程不调用 LLM，不主动消耗 Token。

![Version](https://img.shields.io/badge/version-0.1.0--beta.31.post9-orange)
![Stage](https://img.shields.io/badge/stage-public%20beta-orange)
![Platform](https://img.shields.io/badge/platform-Windows-blue)
![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.27.2-4c8eda)
![License](https://img.shields.io/badge/license-AGPL--3.0-green)

这是 [ChatLearning](https://github.com/JHue58/ChatLearning) 面向 AstrBot 生态的独立移植与现代化项目，适用于 **Windows + AstrBot + NapCat + OneBot v11**。当前版本用于社区测试，不是稳定版，也不建议直接用于陌生的QQ群聊。

## 你可以用它做什么

- 让机器人学习群聊中的相邻消息，并按精确、正则或本地相似度匹配回复。
- 为不同群设置学习、回复、仅学习、仅回复或静默学习模式。
- 通过定向学习，收集指定群成员的回复风格。
- 保存图片、语音、视频和文件等消息组件，去重并按配额管理媒体。
- 导入旧版 `.cl` 词库：先隔离扫描，再生成群绑定计划，确认后备份并导入。
- 在 WebUI 中管理词库、过滤、权限、媒体、任务、备份、诊断和 TTS。
- 使用 Windows 本地 TTS、GPT-SoVITS、本地 HTTP TTS，或主动配置可选云端 TTS。

## 安装

1. 下载 [Releases](https://github.com/xiaolu08/NewChatLearning/releases) 资产
2. 打开 AstrBot Webui → **插件管理** → **从本地文件安装**，选择 ZIP。
3. 确认插件正常加载，并打开插件的内嵌 WebUI。
4. 点击 **进入** 管理会话。
5. 建议先关闭自动回复，完成群设置、备份和词库迁移检查后再逐步启用。

> 异常时重启 AstrBot，再确认插件版本为 `0.1.0-beta.31.post9`。

## 常用命令

```text
/ncl help                         查看帮助
/ncl status                       查看运行状态与统计
/ncl mode learning                当前群仅学习
/ncl mode reply                   当前群仅回复
/ncl mode learning_reply          当前群学习并回复
/ncl mode silent                  当前群静默学习
/ncl target list                  查看定向学习目标
/ncl media-scan                   扫描当前群失效媒体
/ncl migrate-scan                 扫描旧 .cl 词库
```

普通成员触发管理命令时保持静默；管理命令、AstrBot 消息和 NewChatLearning 自身回复不会进入学习链。完整命令说明见[系统设计](./docs/architecture/system-design.md)。

### 旧版命令对应关系

> 以下跨群命令从 `0.1.0-beta.31.post5` 开始提供；首发包 `0.1.0-beta.31.post4` 不包含本次补齐。

下列旧版跨群命令已经恢复，且只允许 AstrBot 全局管理员或 NewChatLearning 插件管理员执行。群聊子管理员只能使用 `/ncl` 命令管理自己获授权的群，不能执行跨群命令。当前群开关仍建议使用统一的 `/ncl` 命令：

| 旧版命令 | 新版命令 | 含义 |
| --- | --- | --- |
| `!grouplist` | 原命令可直接使用 | 查看已开启学习、回复、允许自主管理及不进入全局词库的群 |
| `!add learning <群号...>` | 原命令可直接使用 | 添加开启学习的群；`/ncl learning on` 只作用于当前群 |
| `!remove learning <群号...>` | 原命令可直接使用 | 移除开启学习的群；同时退出这些群的静默学习状态 |
| `!add learnings <群号...>` | 原命令可直接使用 | 同时开启学习和回复 |
| `!remove learnings <群号...>` | 原命令可直接使用 | 同时移除学习和回复 |
| `!add reply <群号...>` | 原命令可直接使用 | 添加开启回复的群；同时退出这些群的静默学习状态 |
| `!remove reply <群号...>` | 原命令可直接使用 | 移除开启回复的群 |
| `!add tag <标签> <群号...>` / `!remove tag <群号...>` | 原命令可直接使用 | 添加共享标签；移除时清除目标群的全部标签 |
| `!add subadmin <群号...>` / `!remove subadmin <群号...>` | 原命令可直接使用 | 从 NapCat 读取目标群当前群主/群管理员并授予本群管理权，或移除该群授权 |
| `!add unmerge <群号...>` / `!remove unmerge <群号...>` | 原命令可直接使用 | 添加/移除不进入全局词库的群 |
| `!learning` | 私聊控制全局；群聊控制当前群 | 兼容入口：私聊时切换全局学习主开关，群聊时切换当前群学习 |
| `!reply` | 私聊控制全局；群聊控制当前群 | 兼容入口：私聊时切换全局回复主开关，群聊时切换当前群词库回复 |

跨群写入使用与 WebUI 相同的配置 revision、持久化与审计服务；任一目标群或配置保存失败时不会保留部分修改。管理员可以在任意管理群或私聊 Bot 中发送带群号的跨群命令，不需要进入目标群。`!add subadmin` 需要 Bot 能通过 NapCat 读取目标群资料，并保存执行时的群主和群管理员 QQ 号；群管理成员发生变化后可再次执行该命令刷新授权名单。

## WebUI

插件页面提供概览、群设置、词库、媒体、过滤、权限、迁移、TTS、任务、备份、审计和诊断功能。高风险操作会要求二次确认，并由服务端继续校验 CSRF、配置版本、计划绑定、备份和审计。

## 兼容范围

| 项目 | 支持范围 |
| --- | --- |
| 操作系统 | Windows |
| Bot 框架 | AstrBot 4.27.2+ |
| QQ 接入 | NapCat / OneBot v11 |
| 会话类型 | QQ 群聊 |

其他操作系统、其他 QQ 协议端、私聊和非 QQ 平台暂不属于当前支持范围。

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
- `/ncl mode`、`learning`、`reply`、`silent` 与 `target` 当前群运行模式和定向学习管理命令
- 可关闭的 `!learning`、`!reply`，以及 `!grouplist`、`!add/!remove learning|learnings|reply|tag|subadmin|unmerge` 原版兼容入口
- NapCat/OneBot 群消息规范化与版本化组件 JSON
- 可配置群白名单的相邻消息学习、重复答案增权和超时断链
- 尚未固化消息的持久化暂存与群消息撤回清理
- SQLite 分群精确匹配与原版语义的答案权重随机
- 回复群白名单、静默学习群、概率、等待、冷却和文本长度限制
- 本地回复成功后停止当次 LLM，并写入 AstrBot 群消息历史
- 精确、受限正则、jieba 词频余弦三级匹配及原版默认阈值
- 按答案权重或答案内容作为问题的频次判断消息类型发送阈值
- SQLite schema v3 问题纯文本索引、正则标记与旧库自动回填
- 图片、闪照、语音、视频和文件的本地持久化、SHA-256 去重与配额保护
- 媒体使用相对路径保存，本地缺失时回退远程 URL；拒绝私网媒体下载
- `/ncl media-scan` 对本群答案执行只读媒体健康检查并持久化失效标记
- `/ncl media-preview` 汇总失效组件、受影响问答及清理后可能为空的答案
- 两阶段媒体清理默认只移除失效组件；可显式选择整条答案删除，并在执行前备份
- 内置管理页面使用无密码“进入”入口、一小时服务端会话、CSRF、入口版本校验和认证审计
- WebUI 登录会话有效期为一小时；会话内功能不重复要求密码，高风险操作使用明确二次确认弹窗
- WebUI 媒体页支持按群扫描、影响预览、两阶段清理、执行前确认和备份结果
- WebUI 词库页支持按群搜索、问题详情、文本/正则问答添加、答案权重修改和带备份的安全删除
- WebUI 可按群导出 ZIP 词库包，包含公式注入防护的 XLSX 预览和保留完整问答组件的 JSONL
- WebUI 可上传旧 `.cl` 文件，完成隔离安全扫描、兼容性摘要、按群转换计划、二次确认导入和导入前备份
- WebUI 群聊页支持停用、仅学习、仅回复、学习并回复、静默学习及定向用户设置，并持久化到 AstrBot 插件配置
- 回复前内容过滤支持包含、完全匹配、正则和组件类型规则，全局规则可叠加群聊附加规则
- 敏感词命中计数、全局/按群黑名单、人工封禁与解封，以及 WebUI 规则测试和命中统计
- WebUI 按群扫描命中过滤规则的历史答案，生成一小时有效的清理预览；确认后先备份再事务删除
- WebUI 浏览、分类和校验插件数据库备份；恢复前自动保存当前状态，成功后使全部管理会话失效
- 按群预览并删除指定成员的可追踪学习贡献；共享答案只扣除对应权重，独占答案删除前自动备份
- 概览页可原地刷新运行状态与统计，不重载页面或重新进入登录流程
- WebUI 审计页支持动作筛选和游标分页，只展示白名单摘要并隐藏客户端地址、路径及未知字段
- WebUI 权限页管理插件管理员和按群子管理员，使用 revision 冲突保护、二次确认和最小化审计
- Windows 系统语音、GPT-SoVITS 与通用本地 HTTP TTS；仅转换纯文本词库答案，失败时自动回退文本
- WebUI 语音页支持概率、长度、音色、超时、GPT-SoVITS 参考信息、驱动状态和测试合成
- 分享、音乐和骰子组件原生重建，闪照按普通图片安全降级
- 合并转发消息受限拉取与节点重建、商城表情原始段补全、XML 卡片原生段重发
- SQLite schema v4 媒体来源元数据与健康状态迁移
- 分群词库默认隔离，以及实时全局联合查询
- 定向学习只固化指定用户紧随上一条群消息给出的答案，非目标用户消息仍参与后续相邻消息链
- 全局词库排除群与多群共享标签，多标签候选保持原版权重叠加语义
- AstrBot 全局管理员、插件管理员与按群子管理员权限
- 管理员引用词库回复执行 `!d` / `!delete`，或用 `!d <序号>` 删除最近回复
- SQLite schema v5 回复消息追踪、精确答案删除、空问题清理与审计
- 本群词库搜索、详情、文本/正则问答添加、权重修改和按 ID 删除命令
- 旧 `.cl` 受限扫描器：独立进程、opcode 拒绝、基础容器结构统计
- 旧词库隔离转换为 JSONL，并在单个 SQLite 事务中合并频次和原版权重
- 2.1 MB 真实共享词库已通过临时 SQLite 完整导入验证，未写入插件运行数据
- 当前群词库可反向导出原版协议 4 `.cl`，保留频次、正则与答案权重语义
- SQLite schema v9 定时任务定义与执行历史，插件重载后不会立即重复已认领任务
- WebUI 任务页支持无重载刷新、新建编辑、启停、立即执行和最近执行历史
- 内建媒体扫描、数据库备份、过期产物清理与过滤词库预演/自动清理；自动删除默认不创建且每次执行前备份并复核

公开预览版已知限制：

- 词库管理的媒体问答人工编辑与批量操作暂不纳入当前产品范围。
- 火山引擎、阿里云、腾讯云、Azure、OpenAI、OpenAI 风格和自定义 HTTP 云端 TTS 已实现配置、DPAPI 密钥保护和调用额度，但尚未逐一连接真实付费账户验收。
- Windows + AstrBot + NapCat 已完成插件安装、WebUI 会话、旧 `.cl` 扫描与导入、备份和统计核对；消息类型、长时间任务、全部管理页和大规模词库仍需更广泛实机验证。
- 其他操作系统、其他 QQ 协议端、私聊和非 QQ 平台暂不属于支持范围。

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

jieba 相似度匹配同样默认关闭。启用后只消耗本机 CPU，不下载模型、不访问网络，也不会产生 LLM Token。

新媒体本地保存默认启用，单文件默认限制为 50 MB，总配额默认 10 GB。达到配额后仍继续学习文本和消息结构，不删除已有媒体，也不会主动扩容。

## 项目关系

NewChatLearning 是基于 ChatLearning 功能与受协议保护实现进行开发的独立移植项目，不是 ChatLearning、AstrBot、NapCat 或腾讯 QQ 的官方版本，也不代表上述项目维护者的立场。

原项目归属与修改说明见 [NOTICE](./NOTICE)。

## 开源协议

NewChatLearning 使用 [GNU Affero General Public License v3.0](./LICENSE)。原 ChatLearning 项目的著作权仍归其相应作者与贡献者所有。
