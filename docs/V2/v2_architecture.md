# SolBot V2 架构方案

## 1. 文档目标

本文件用于定义 SolBot 在 V2 阶段的全栈架构方向。

V1 的目标是把产品跑通；V2 的目标是把系统做成一个可持续演进的平台。

因此，V2 架构关注的重点不是继续堆功能，而是解决以下问题：

1. 前端状态与页面职责过于集中，扩展成本高
2. 后端 chat 与 file 业务已经成型，但领域边界还不够清晰
3. 实时事件、文件资产、用户偏好、权限规则还没有形成统一抽象
4. V1 只支持文本消息，V2 要为文件消息、媒体消息、内容治理和多端一致性做准备

## 2. V2 总体目标

V2 的目标不是推翻 V1，而是在保留 V1 已验证业务规则的前提下，完成一次结构升级。

V2 要达成以下结果：

1. 前端从“单页面大 store 驱动”升级为“领域模块化 + 场景组合”
2. 后端从“视图 + services”升级为“API 层 / 应用层 / 领域层 / 基础设施层”
3. 聊天与资源中心共享统一的文件资产能力
4. WebSocket 事件从零散广播升级为标准事件总线协议
5. 为图片消息、文件消息、音乐页、内容审核、消息回执、多端接入预留稳定边界

## 3. V1 现状与瓶颈

### 3.1 前端瓶颈

从当前代码看，前端主要瓶颈如下：

1. 聊天主状态集中在一个超大 store 中，承担列表、消息、好友、搜索、巡检、群管理、失败重试等多类职责
2. 聊天页面组件很多，但大部分仍然依赖共享大 store，模块边界弱
3. 资源中心与聊天未来都需要文件能力，但当前前端只在资源中心内建模上传队列
4. 路由已经分组，但还没有形成“领域模块”目录结构
5. 页面能跑通，但“UI 组合层”和“业务状态层”耦合仍偏高

### 3.2 后端瓶颈

从当前 chat / bbot / ws 结构看，后端主要瓶颈如下：

1. APIView + services 已经初步分层，但领域服务、应用服务、序列化职责仍有交叉
2. chat 模块已经包含好友、会话、成员、消息、群审批、设置、审计，但还没有明确拆成子域
3. 文件上传、文件去重、回收站恢复已经具备复杂规则，但还没有沉淀为统一文件资产服务
4. WebSocket 目前可用，但事件类型和广播路径仍然是“够用型实现”，缺少标准化事件契约
5. V1 只做文本消息，V2 若直接加图片/文件消息，现有 message payload 体系会迅速变复杂

### 3.3 产品层瓶颈

V1 已完成核心可用性，但 V2 必须开始解决这些产品级问题：

1. 文件如何在“资源中心”和“聊天消息”之间复用
2. 消息类型如何扩展而不破坏历史兼容
3. 群管理、审核、巡检、通知如何形成统一治理链路
4. 用户偏好如何从散落字段升级为一致的配置体系
5. 后续音乐页、内容页、工具页如何以插件化导航方式接入，而不是继续堆到 routes 里

## 4. V2 架构原则

V2 采用以下原则：

1. 领域优先，不按“页面”或“接口列表”组织核心代码
2. 前后端都做模块边界，不再接受单个超大 store 或单个超大 service 持续膨胀
3. 事件优先于直接耦合，实时变化统一通过标准事件模型传播
4. 文件能力做统一资产层，不把上传逻辑散落到聊天、头像、资源中心多个子系统
5. 保持 V1 路由和数据兼容，V2 优先增量重构，不做破坏性重写

## 5. V2 目标架构总览

### 5.1 前端目标结构

前端按领域拆为以下模块：

1. app-shell
2. auth-access
3. resource-center
4. chat-center
5. account-center
6. entertainment-center
7. shared-ui
8. shared-infra

建议目录形态：

```text
src/
  app/
    router/
    layout/
    bootstrap/
  modules/
    auth-access/
      api/
      stores/
      views/
      composables/
    resource-center/
      api/
      stores/
      views/
      components/
      composables/
    chat-center/
      api/
      stores/
      views/
      components/
      composables/
      services/
    account-center/
    entertainment-center/
  shared/
    api/
    components/
    composables/
    types/
    utils/
    workers/
```

### 5.2 后端目标结构

后端按分层 + 子域组织：

1. interface 层：REST / WebSocket / serializer / permissions
2. application 层：用例编排、事务边界、命令与查询
3. domain 层：核心规则、实体策略、领域服务
4. infrastructure 层：ORM、缓存、对象存储、消息广播、异步任务

建议以 chat 为例拆成：

```text
chat/
  interfaces/
    api/
    ws/
    serializers/
  application/
    commands/
    queries/
    dto/
  domain/
    conversation/
    friendship/
    message/
    moderation/
    preference/
  infrastructure/
    repositories/
    event_bus/
    search/
```

资源中心建议逐步演进为独立资产域：

```text
assets/
  interfaces/
  application/
  domain/
  infrastructure/
```

V2 不要求一次性物理重命名全部目录，但代码组织要按这个目标收敛。

## 6. 领域拆分设计

### 6.1 身份与权限域

职责：

1. 登录鉴权
2. 用户、角色、权限
3. 菜单权限映射
4. 会话级行为权限判断

V2 要求：

1. 把页面菜单权限、接口权限、聊天巡检权限统一成可复用的权限判定服务
2. 前端不直接散落 permissionCode 判断，而是通过模块级 capability 输出

### 6.2 资产域

职责：

1. 文件上传
2. 文件去重
3. 回收站
4. 文件元数据
5. 媒体访问地址
6. 聊天文件消息复用

V2 的关键改造：

1. 引入 Asset 实体，作为“物理文件”抽象
2. 引入 AssetReference，作为“业务引用”抽象
3. 资源中心文件、头像、聊天文件消息都引用 Asset，而不是各自维护一套文件语义

目标收益：

1. 同一物理文件可被多个业务对象引用
2. 回收站恢复、同 MD5 去重、聊天文件发送可以共用底层能力
3. 为未来对象存储切换预留空间

### 6.3 聊天域

聊天域在 V2 拆为五个子域：

1. Conversation
2. Messaging
3. SocialGraph
4. Moderation
5. Preference

#### Conversation

负责：

1. 单聊 / 群聊生命周期
2. 成员关系
3. 会话展示状态
4. 未读摘要

#### Messaging

负责：

1. 消息序列号
2. 消息创建与持久化
3. 文本 / 图片 / 文件 / 系统消息类型扩展
4. 消息状态
5. 历史查询与定位

#### SocialGraph

负责：

1. 好友申请
2. 好友关系
3. 陌生人私聊规则
4. 好友备注

#### Moderation

负责：

1. 群审批
2. 禁言
3. 巡检
4. 审计日志
5. 内容治理扩展点

#### Preference

负责：

1. 用户聊天偏好
2. 会话偏好
3. 发送快捷键
4. 消息通知、排序、隐身巡检等用户设置

### 6.4 娱乐与扩展域

娱乐中心在 V2 不再作为“直接写死在 routes 的附属页”，而是演进为扩展域。

目标：

1. 2048 保持独立
2. 音乐页作为 V2 新模块回归
3. 后续工具页、内容页、活动页按同一模块注册机制接入

## 7. 前端详细架构

### 7.1 分层

前端按四层组织：

1. 页面层：负责路由和页面布局
2. 场景层：负责页面内多个组件组合
3. 模块状态层：负责某个领域模块的数据与行为
4. 基础设施层：负责请求、缓存、WebSocket、上传、Worker、格式化

### 7.2 Store 重构策略

V2 不再维护一个超大的 chat store，而拆成：

1. useConversationStore
2. useMessageStore
3. useFriendshipStore
4. useChatModerationStore
5. useChatPreferenceStore

页面通过 composable 聚合，例如：

1. useChatShellScene
2. useChatConversationScene
3. useContactScene
4. useAuditScene

这样做的目的：

1. 状态边界更清晰
2. 热更新和调试更容易
3. 未来移动端或桌面端可以复用模块层逻辑

### 7.3 WebSocket 事件接入

前端引入统一事件分发器：

1. ws connection manager
2. event decoder
3. domain event dispatcher

事件流：

```text
WebSocket -> event decoder -> domain dispatcher -> 对应模块 store -> UI
```

这样可以避免所有事件都直接塞进单个 store 中处理。

### 7.4 上传与文件选择

V2 将上传系统拆为：

1. upload session manager
2. file snapshot adapter
3. chunk scheduler
4. asset picker

其中 asset picker 是聊天文件消息、头像上传、资源中心导入的统一入口。

## 8. 后端详细架构

### 8.1 API 层

API 层只负责：

1. 参数校验
2. 权限门禁
3. 调用应用服务
4. DTO 转响应结构

API 层不再直接拼装复杂业务规则。

### 8.2 应用层

应用层负责用例编排，例如：

1. SendMessageCommand
2. OpenConversationCommand
3. InviteGroupMemberCommand
4. RestoreAssetCommand
5. CreateAssetReferenceCommand

应用层负责事务边界与跨域协调。

### 8.3 领域层

领域层负责规则，例如：

1. 非好友只能发送哪些消息
2. 群管理员是否有审批权
3. 某条消息是否可撤回
4. 某个 Asset 是否可复用
5. 恢复回收站文件时如何处理冲突

### 8.4 基础设施层

基础设施层负责：

1. ORM repository
2. Redis 缓存
3. WebSocket 广播实现
4. Celery 异步任务
5. 本地磁盘或对象存储接入
6. 搜索索引接入

## 9. 消息模型升级方案

V2 的消息实体不再只靠 `message_type + payload` 走通全部能力，而是采用“基础消息 + 负载对象”方案。

建议：

1. ChatMessage 继续保留为主表
2. payload 内只保留轻量可序列化字段
3. 图片 / 文件消息使用 `asset_id` 或 `asset_ref_id`
4. 系统消息使用 `system_action` + `system_context`

推荐的消息类型：

1. text
2. system
3. image
4. file
5. rich_text
6. event

其中 V2 必做的是：

1. image
2. file

这样聊天文件就不需要另起一套上传语义，而是直接复用资产域。

## 10. 实时事件架构

V2 的实时层统一采用“标准事件 envelope”。

推荐结构：

```json
{
  "event_id": "uuid",
  "event_type": "chat.message.created",
  "domain": "chat",
  "occurred_at": "2026-04-08T20:00:00+08:00",
  "actor_id": 1,
  "target_scope": {
    "user_ids": [1, 2]
  },
  "payload": {}
}
```

当前代码态补充说明：

1. 广播事件已经统一为 `type: "event" + event_type + domain + occurred_at + payload`
2. 聊天域已实际落地的核心事件包括：
   - `chat.message.created`
   - `chat.message.ack`
   - `chat.conversation.updated`
   - `chat.unread.updated`
3. 前端消费端会先按 envelope 解包，再映射回现阶段兼容使用的消息分发语义
4. 命令级错误回执目前仍保留非 envelope 结构，例如：

```json
{
  "type": "error",
  "event": "chat_send_asset_message",
  "message": "你们还不是好友，当前私聊暂不支持发送附件"
}
```

这样可以把领域广播事件与一次性命令失败回执分开处理，避免错误消息被误当成可订阅事件。

目标收益：

1. 前端事件消费统一
2. 后端广播逻辑不再按函数名散落
3. 后续可平滑扩展站内通知、系统公告、运营事件

## 11. 数据与存储策略

### 11.1 数据库

V2 继续以 MySQL 为主库。

### 11.2 Redis

Redis 在 V2 中承担：

1. Channels layer
2. 临时会话状态
3. 消息热点缓存
4. 上传过程状态
5. 限流与短期去重

### 11.3 文件存储

V2 存储抽象要兼容两种后端：

1. 本地文件系统
2. 对象存储

V2 第一阶段可以继续用本地磁盘，但代码层必须通过 storage provider 抽象访问。

## 12. 搜索架构

V1 搜索是“数据库可用型搜索”；V2 搜索升级为两层：

1. 结构化搜索：会话、用户、群、资源文件
2. 内容搜索：消息内容、文件名、系统通知

V2 第一阶段仍可继续走 MySQL 条件检索。

V2 第二阶段建议抽象 SearchProvider，为后续接入 Elasticsearch 或 Meilisearch 预留。

## 13. 权限与治理架构

V2 将治理能力前置，尤其是聊天与资源中心。

需要统一的能力包括：

1. 资源访问权限
2. 会话访问权限
3. 巡检权限
4. 消息发送权限
5. 群管理权限
6. 审计日志落库

建议输出统一 capability 模型：

1. can_view
2. can_edit
3. can_manage
4. can_moderate
5. can_audit
6. can_send_message

前端与后端都消费同一语义，而不是分别写一套判断。

## 14. V2 阶段拆解

### Phase 1：结构重构

目标：不明显改变功能，只重构边界。

包括：

1. 前端 chat store 拆分
2. 前端模块目录重组
3. 后端 chat 应用层与领域层拆分
4. WebSocket 标准事件 envelope 建立

当前已落地：

1. 前端已先完成 chat shell、转发流程、聊天记录弹窗的场景层 / 组件层抽离，目录开始向 `src/modules/chat-center/` 收敛
2. 后端 chat HTTP 接口已物理迁移到 `chat/interfaces/api/`，并继续按 `endpoints/` 与场景化 serializer 模块拆分
3. 后端聊天广播事件已收口到通用 `ws/event_bus.py` + `chat/infrastructure/event_bus.py`，不再直接散落依赖旧通知函数

当前未完成：

1. 前端 chat store 尚未完成全面拆分
2. 后端 chat 的 application / domain / repository 仍有继续收口空间
3. 前端对 envelope 的消费虽然已兼容，但还没有完全抽成统一实时层

### Phase 2：资产统一

目标：把资源中心、头像上传、聊天文件统一到资产域。

包括：

1. 建立 Asset / AssetReference 模型
2. 聊天文件消息接入资产域
3. 上传入口统一为 asset picker

当前已落地：

1. 聊天附件发送创建 `AssetReference` 已统一收口到 application service
2. 头像引用更新与资源中心引用同步已复用同一批 asset reference service
3. `UploadedFile` 当前作为兼容层继续存在，但引用关系已经开始向 Asset / AssetReference 语义收敛

当前未完成：

1. 统一 asset picker 入口还未建立
2. 上传初始化、分片、合并等接口尚未统一成资产域协议
3. 存量 UploadedFile 的彻底目录视图化仍在后续阶段

### Phase 3：聊天能力升级

包括：

1. 图片消息
2. 文件消息
3. 消息状态增强
4. 更清晰的消息定位与回执预留

### Phase 4：扩展模块回归

包括：

1. 音乐页回归
2. 内容页 / 工具页模块化接入
3. 统一扩展菜单注册机制

## 15. 本阶段建议先做什么

如果马上进入 V2 实施，建议按这个顺序开始：

1. 先拆前端 chat store，不先加新功能
2. 再拆后端 chat 应用层与领域层
3. 然后抽象资产域接口，不急着一次迁移全部存量文件
4. 最后接入图片 / 文件消息

这是最稳妥的顺序，因为它优先消除结构债，而不是继续把新功能堆在 V1 的骨架上。

## 16. V2 验收标准

V2 架构完成的标志不是文档存在，而是达到以下结果：

1. 前端 chat 不再依赖单个超大 store
2. 后端 chat 不再主要靠 views + 大 service 承担全部规则
3. 文件上传与聊天文件消息共享统一资产抽象
4. WebSocket 事件统一成标准 envelope
5. 新增图片 / 文件消息时，不需要重写一轮资源中心或上传体系

按当前代码状态，可确认的进度是：

1. 第 2、3、4 条已经进入真实落地阶段，其中 chat 接口物理分层、资产引用服务收口、事件 envelope 广播骨架都已完成第一轮收敛
2. 第 1 条仍未完成，前端只完成了场景层与局部模块化切分，store 还没有全部脱离单体结构
3. 第 5 条只完成了一半，聊天文件消息已经复用资产抽象，但统一上传入口与完整媒体能力还未闭环

---

这份架构文档是 V2 的总蓝图。

下一步建议基于这份总蓝图继续拆两份实施文档：

1. V2/chat_v2_refactor_plan.md
2. V2/assets_v2_design.md
