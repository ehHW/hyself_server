# Chat V2 重构实施计划

## 1. 文档目标

本文件基于 v2_architecture.md，进一步拆解 chat 模块在 V2 的实际落地步骤。

目标不是一次性推倒重写，而是在保证 V1 可用性的前提下，逐步把 chat 从“能用”升级到“可持续演进”。

## 2. 重构目标

Chat V2 需要解决四类问题：

1. 前端单一大 store 过重
2. 后端 chat 规则虽然完整，但应用层与领域层边界仍混杂
3. 实时事件协议不统一
4. 文件消息、图片消息和审核增强没有稳定扩展点

## 3. 重构范围

### 3.1 本次 V2 必做

1. 拆分前端 chat store
2. 拆分前端 chat 页面场景层
3. 后端 chat 按应用层 / 领域层 / 基础设施层重组
4. 标准化 WebSocket 事件 envelope
5. 统一消息类型扩展接口
6. 为图片消息、文件消息接入资产域预留接口

### 3.2 本次 V2 不立即做

1. 音视频通话
2. 真正的全文检索引擎接入
3. 单条消息级已读回执完整闭环
4. 多端同步冲突解决算法
5. 消息撤回的完整产品规则

## 4. 前端重构计划

### 4.1 现状问题

当前 useChatStore 同时承担：

1. 会话列表
2. 消息列表
3. 好友关系
4. 群成员与群审批
5. 搜索
6. 巡检
7. 失败消息重试
8. WebSocket 事件分发

这会带来以下问题：

1. 修改任一功能都容易影响其它状态
2. 单元测试难做
3. 页面重用困难
4. 后续接入文件消息时 store 会继续膨胀

### 4.2 目标拆分

建议拆为以下模块：

1. useConversationStore
2. useMessageStore
3. useFriendshipStore
4. useGroupStore
5. useChatModerationStore
6. useChatPreferenceStore
7. useChatSearchStore
8. useChatRealtimeStore

### 4.3 场景层拆分

页面层不要直接消费多个底层 store，而是通过场景层聚合：

1. useChatShellScene
2. useConversationListScene
3. useConversationWorkspaceScene
4. useContactScene
5. useAuditScene
6. useChatSettingsScene

目标：

1. 页面组件只处理展示与交互
2. 场景层处理跨 store 协调
3. store 只处理单一领域状态

### 4.4 前端目录目标

```text
src/modules/chat-center/
  api/
  stores/
    conversation.ts
    message.ts
    friendship.ts
    group.ts
    moderation.ts
    preference.ts
    realtime.ts
    search.ts
  composables/
    useChatShellScene.ts
    useConversationListScene.ts
    useConversationWorkspaceScene.ts
    useContactScene.ts
    useAuditScene.ts
  components/
  views/
```

## 5. 后端重构计划

### 5.1 现状问题

当前后端 chat 已有 views、serializers、services、events、models，但仍存在这些问题：

1. APIView 中还残留一部分业务编排
2. services 里既有规则又有序列化与通知协作
3. 领域规则与持久化访问没有彻底解耦
4. 广播逻辑还是函数式散落，不利于事件标准化

### 5.2 目标分层

建议拆成：

1. interfaces
2. application
3. domain
4. infrastructure

#### interfaces

负责：

1. REST API
2. WebSocket 输入协议
3. serializer / DTO 转换
4. permission gateway

#### application

负责：

1. 用例编排
2. 事务边界
3. 调用 repository
4. 发布领域事件

#### domain

负责：

1. 好友申请规则
2. 陌生人私聊规则
3. 群邀请与审批规则
4. 成员角色与禁言规则
5. 消息类型可发送性规则

#### infrastructure

负责：

1. ORM repository
2. Channels 事件广播
3. Redis 缓存
4. 搜索适配器

### 5.3 子域拆分

chat/domain 下拆五个子域：

1. conversation
2. messaging
3. social_graph
4. moderation
5. preference

## 6. 实时事件改造

### 6.1 当前问题

当前 ws.events 已经可用，但事件模型仍然偏扁平，问题如下：

1. 事件命名不统一
2. 事件上下文不足
3. 前端消费侧只能靠大量 if 分支判断

### 6.2 V2 目标

统一事件 envelope：

```json
{
  "event_id": "uuid",
  "event_type": "chat.message.created",
  "domain": "chat",
  "occurred_at": "2026-04-08T20:00:00+08:00",
  "payload": {}
}
```

当前实现补充：

1. 服务端广播给前端的聊天域事件已经统一输出为 `type: "event"`
2. 事件名当前落地值为：
   - `chat.message.created`
   - `chat.message.ack`
   - `chat.conversation.updated`
   - `chat.unread.updated`
3. `chat.message.acknowledged` 这一命名保留为概念目标，当前代码与测试已统一收口到 `chat.message.ack`
4. WebSocket 输入动作如 `chat_send_message`、`chat_send_asset_message` 的校验失败或权限失败，当前仍返回兼容结构：

```json
{
  "type": "error",
  "event": "chat_send_asset_message",
  "message": "你们还不是好友，当前私聊暂不支持发送附件"
}
```

这类 `error` 响应不属于领域广播事件，不进入标准 envelope，而是作为连接级命令回执保留。

### 6.3 首批事件清单

1. chat.conversation.updated
2. chat.message.created
3. chat.message.ack
4. chat.unread.updated
5. chat.friend_request.updated
6. chat.friendship.updated
7. chat.group_join_request.updated
8. chat.moderation.notice

## 7. 消息模型升级

### 7.1 当前状态

当前 V1 主要使用：

1. text
2. system

V2 需要开始支持：

1. image
2. file

### 7.2 目标方案

ChatMessage 保留主表，但 payload 只保存轻量描述字段。

对于图片和文件消息：

1. 不直接把复杂文件信息堆到 payload
2. 使用 asset_id 或 asset_reference_id 关联资产域

推荐结构：

```json
{
  "message_type": "file",
  "content": "",
  "payload": {
    "asset_reference_id": 123,
    "display_name": "需求文档.pdf"
  }
}
```

## 8. 接口演进策略

### 8.1 保持兼容

V2 第一阶段不主动破坏现有 V1 REST 路径。

### 8.2 内部实现替换

优先做：

1. 原路径不变
2. 内部改调 application command/query
3. serializer 输出保持兼容

### 8.3 允许新增的接口

后续为图片/文件消息可新增：

1. chat asset picker 接口
2. 消息附件预签名 / 上传初始化接口
3. 消息媒体预览接口

## 9. 数据迁移建议

### 9.1 不做破坏式迁移

V2 第一阶段尽量不改动会引发全量迁移风险的核心表结构。

### 9.2 可接受的增量迁移

1. 新增资产引用表
2. 新增用户偏好扩展字段
3. 新增事件日志表
4. 新增消息附件映射表

## 10. 实施顺序

### Step 1

先拆前端 chat store 与 composable。

### Step 2

后端引入 application / domain 目录，并让现有 views 改调 command/query。

### Step 3

建立标准事件 envelope，并替换现有散落通知函数的输出结构。

### Step 4

接资产域，为图片/文件消息做第一批接入。

### Step 5

再做聊天搜索增强、审核增强、消息能力增强。

## 11. 里程碑验收

### M1：结构拆分完成

验收标准：

1. 前端 chat store 已拆分
2. 后端 chat 具备 command/query 入口
3. 原有 V1 功能回归通过

### M2：事件统一完成

验收标准：

1. 前端统一消费 event envelope
2. 后端广播统一输出 event_type
3. 输入命令失败场景继续返回显式 `error` 回执，前端可按 `event` 字段回滚发送态
4. 实时消息、好友申请、群通知链路无回归

### M3：文件消息接入完成

验收标准：

1. 文件消息可发送
2. 资源中心与聊天复用资产能力
3. 同一文件不重复造轮子

## 12. 风险与注意事项

1. 不要先做图片/文件消息，再回头补重构
2. 不要在现有大 store 上继续加功能
3. 不要让 application 层重新长成新的大 service
4. 不要把事件 envelope 设计成只服务 chat，应该兼容系统通知和后续模块