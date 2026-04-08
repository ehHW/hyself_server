# Chat 模块 Schema 设计

## 1. 文档目标

本文件用于确定 `chat` 模块 V1 的数据库表结构、字段语义、唯一约束、索引策略、状态约束和建模边界。

目标是让后续模型、迁移、接口和 WebSocket 逻辑都围绕同一份结构设计实现，避免边做边改表。

## 2. 设计原则

### 2.1 基础原则

1. 优先满足 V1 的单聊、群聊、好友审批、群审批、文本消息、会话级未读数、基础搜索和超级管理员隐身巡检需求
2. 保留后续文件消息、群人数限制、已读回执、消息撤回等扩展空间
3. 区分“真实成员关系”和“只读旁观能力”，避免超级管理员隐身巡检污染群成员状态
4. 区分“会话存在”和“会话是否展示在当前用户列表中”
5. 尽量将高频查询需要的状态缓存到成员表，避免每次都全量聚合消息表

### 2.2 命名与公共字段原则

建议遵循当前项目风格：

1. 数据表名使用小写下划线风格
2. 主键使用 Django 默认 `BigAutoField`
3. 业务表优先显式带 `created_at`、`updated_at`
4. 是否使用 `SoftDeleteModel` 取决于实体是否存在“逻辑删除后可能恢复”的需求

建议分层如下：

1. 适合 `SoftDeleteModel`：`chat_conversation`、`chat_group_config`
2. 不建议使用软删除、而是保留状态字段：`chat_friend_request`、`chat_friendship`、`chat_group_join_request`、`chat_conversation_member`
3. 不建议做软删除、以不可变追加为主：`chat_message`

原因：

1. 请求流和关系流更适合用状态字段表示生命周期
2. 消息是事件流，V1 不做物理删除，也不做软删除恢复
3. 成员关系需要保留“加入、退出、踢出、隐藏列表”等状态，单纯软删除不够表达

## 3. 实体总览

V1 最终确定以下核心表：

1. `chat_friend_request`
2. `chat_friendship`
3. `chat_conversation`
4. `chat_group_config`
5. `chat_group_join_request`
6. `chat_conversation_member`
7. `chat_message`
8. `user_preference`

其中：

1. 好友体系由 `chat_friend_request` + `chat_friendship` 构成
2. 会话体系由 `chat_conversation` + `chat_group_config` + `chat_conversation_member` + `chat_message` 构成
3. 群审批由 `chat_group_join_request` 承担
4. 用户个性化设置与超级管理员隐身巡检开关放入 `user_preference`

## 4. 表结构定义

### 4.1 `chat_friend_request`

#### 4.1.1 作用

记录好友申请流。

该表是审批流表，不直接代表好友关系是否存在。

#### 4.1.2 字段设计

1. `id`：主键
2. `from_user_id`：申请发起人，FK -> `user_user.id`
3. `to_user_id`：申请接收人，FK -> `user_user.id`
4. `pair_key`：好友对唯一标准键，`char(64)`，由较小用户 ID 和较大用户 ID 计算得到
5. `status`：申请状态，`varchar(20)`
6. `request_message`：申请备注，`varchar(255)`，默认空字符串
7. `auto_accepted`：是否因双方互相申请而自动通过，`bool`，默认 `false`
8. `handled_by_id`：处理人，FK -> `user_user.id`，允许空
9. `handled_at`：处理时间，允许空
10. `created_at`
11. `updated_at`

#### 4.1.3 状态枚举

建议固定为：

1. `pending`
2. `accepted`
3. `rejected`
4. `canceled`
5. `expired`

#### 4.1.4 约束

1. `from_user_id != to_user_id`
2. `pair_key` 必须由应用层按稳定规则生成
3. 同一方向不允许同时存在多条 `pending` 申请
4. 若反方向已存在 `pending` 申请，则本次申请不新增 `pending`，直接走自动通过逻辑

由于 MySQL 对“部分唯一索引”支持不适合作为 Django 常规迁移默认方案，建议：

1. 建立普通联合索引 `from_user_id + to_user_id + status`
2. 在 service 层确保“同方向唯一 pending”

#### 4.1.5 索引

1. `idx_chat_friend_request_from_status`：`(from_user_id, status, created_at)`
2. `idx_chat_friend_request_to_status`：`(to_user_id, status, created_at)`
3. `idx_chat_friend_request_pair_status`：`(pair_key, status, created_at)`

### 4.2 `chat_friendship`

#### 4.2.1 作用

记录两人之间是否为好友。

最终采用“单条规范化关系记录”，不采用双向两条记录。原因如下：

1. 更容易保证唯一性
2. 更容易表达解除好友后再次恢复的关系状态
3. 更适合和单聊唯一键使用同一套 `pair_key`

#### 4.2.2 字段设计

1. `id`：主键
2. `pair_key`：唯一键，`char(64)`
3. `user_low_id`：较小用户 ID，FK -> `user_user.id`
4. `user_high_id`：较大用户 ID，FK -> `user_user.id`
5. `status`：好友关系状态，`varchar(20)`
6. `source_request_id`：来源申请，FK -> `chat_friend_request.id`，允许空
7. `accepted_at`：成为好友时间
8. `deleted_at`：解除好友时间，允许空
9. `created_at`
10. `updated_at`

#### 4.2.3 状态枚举

1. `active`
2. `deleted`

#### 4.2.4 约束

1. `user_low_id < user_high_id`，由应用层保证
2. `pair_key` 全局唯一
3. 一对用户在任意时刻最多只有一条好友关系记录
4. 删除好友时，不删除记录，而是将 `status` 改为 `deleted`
5. 再次成为好友时，恢复该记录为 `active`

#### 4.2.5 索引

1. `uk_chat_friendship_pair_key`：唯一索引 `pair_key`
2. `idx_chat_friendship_user_low_status`：`(user_low_id, status)`
3. `idx_chat_friendship_user_high_status`：`(user_high_id, status)`

### 4.3 `chat_conversation`

#### 4.3.1 作用

表示一个聊天会话，支持单聊和群聊。

#### 4.3.2 字段设计

1. `id`：主键
2. `type`：会话类型，`varchar(20)`
3. `name`：群聊名称或系统生成名称，`varchar(150)`，允许空字符串
4. `avatar`：会话头像，`varchar(500)`，默认空字符串
5. `owner_id`：群主，FK -> `user_user.id`，单聊允许空，群聊必填
6. `direct_pair_key`：单聊唯一键，`char(64)`，群聊为空
7. `status`：会话状态，`varchar(20)`
8. `last_message_id`：最后一条消息 ID，允许空
9. `last_message_preview`：最后一条消息预览，`varchar(255)`，默认空字符串
10. `last_message_at`：最后消息时间，允许空
11. `member_count_cache`：真实成员数缓存，`int`，默认 0
12. `created_at`
13. `updated_at`
14. `deleted_at`

#### 4.3.3 状态枚举

1. `active`
2. `disbanded`

#### 4.3.4 约束

1. `type` 只允许 `direct` 或 `group`
2. 当 `type = direct` 时，`direct_pair_key` 必填且唯一，`owner_id` 可为空
3. 当 `type = group` 时，`direct_pair_key` 必须为空，`owner_id` 必填
4. `member_count_cache` 只统计真实成员，不统计超级管理员隐身巡检
5. `last_message_id` 对应消息必须属于当前会话，由 service 层保证

#### 4.3.5 索引

1. `uk_chat_conversation_direct_pair_key`：唯一索引 `direct_pair_key`
2. `idx_chat_conversation_type_status_updated`：`(type, status, updated_at)`
3. `idx_chat_conversation_owner_status`：`(owner_id, status, updated_at)`
4. `idx_chat_conversation_last_message_at`：`(last_message_at)`
5. `idx_chat_conversation_name`：`(name)`

### 4.4 `chat_group_config`

#### 4.4.1 作用

存放群聊配置，避免将群规则字段全部堆入主会话表。

#### 4.4.2 字段设计

1. `id`：主键
2. `conversation_id`：OneToOne FK -> `chat_conversation.id`
3. `join_approval_required`：是否开启入群审批，`bool`，默认 `false`
4. `allow_member_invite`：普通成员是否允许拉人，`bool`，默认 `true`
5. `max_members`：群成员上限，`int`，允许空
6. `mute_all`：是否全员禁言，`bool`，默认 `false`
7. `created_at`
8. `updated_at`
9. `deleted_at`

#### 4.4.3 约束

1. 仅群聊允许存在 `chat_group_config`
2. 每个群聊最多一条配置
3. `max_members` 为预留字段，V1 不做硬性校验逻辑，但要求大于 0 或为空

#### 4.4.4 索引

1. `uk_chat_group_config_conversation_id`：唯一索引 `conversation_id`
2. `idx_chat_group_config_join_approval_required`：`(join_approval_required)`

### 4.5 `chat_group_join_request`

#### 4.5.1 作用

表示“邀请加入群聊”或“申请加入群聊”的审批记录。

V1 的主场景是“群友拉好友进群，若开启审批则由群主/管理员审批”。

#### 4.5.2 字段设计

1. `id`：主键
2. `conversation_id`：FK -> `chat_conversation.id`
3. `request_type`：请求类型，`varchar(20)`
4. `inviter_id`：邀请人，FK -> `user_user.id`
5. `target_user_id`：目标入群用户，FK -> `user_user.id`
6. `status`：审批状态，`varchar(20)`
7. `reviewer_id`：审批人，FK -> `user_user.id`，允许空
8. `review_note`：审批备注，`varchar(255)`，默认空字符串
9. `reviewed_at`：审批时间，允许空
10. `created_at`
11. `updated_at`

#### 4.5.3 状态枚举

1. `pending`
2. `approved`
3. `rejected`
4. `canceled`
5. `expired`

#### 4.5.4 请求类型

V1 先固定为：

1. `invite`

后续如开放“主动申请加群”，再增加：

1. `apply`

#### 4.5.5 约束

1. 仅群聊允许产生该记录
2. `target_user_id` 不能已是当前群有效成员
3. 同一个群、同一个目标用户，在同一时刻不应存在多条 `pending` 记录
4. 若群未开启审批，则不落 `pending` 记录，直接入群
5. 审批人必须是群主或群管理员

同样由于部分唯一约束不适合默认实现，建议：

1. 建立普通索引 `(conversation_id, target_user_id, status)`
2. 在 service 层保证“同群同用户唯一 pending”

#### 4.5.6 索引

1. `idx_chat_group_join_request_conversation_status`：`(conversation_id, status, created_at)`
2. `idx_chat_group_join_request_target_status`：`(target_user_id, status, created_at)`
3. `idx_chat_group_join_request_inviter_status`：`(inviter_id, status, created_at)`
4. `idx_chat_group_join_request_conversation_target_status`：`(conversation_id, target_user_id, status)`

### 4.6 `chat_conversation_member`

#### 4.6.1 作用

表示用户与会话之间的真实成员关系，以及该用户在该会话内的展示、未读、阅读游标和管理状态。

该表同时适用于单聊和群聊。

#### 4.6.2 字段设计

1. `id`：主键
2. `conversation_id`：FK -> `chat_conversation.id`
3. `user_id`：FK -> `user_user.id`
4. `role`：成员角色，`varchar(20)`
5. `status`：成员状态，`varchar(20)`
6. `joined_at`：加入时间
7. `left_at`：主动退出时间，允许空
8. `removed_at`：被移出时间，允许空
9. `removed_by_id`：移出操作者，FK -> `user_user.id`，允许空
10. `mute_until`：禁言截止时间，允许空
11. `mute_reason`：禁言原因，`varchar(255)`，默认空字符串
12. `is_pinned`：是否置顶，`bool`，默认 `false`
13. `show_in_list`：是否在当前用户会话列表中展示，`bool`，默认 `true`
14. `unread_count`：未读数缓存，`int`，默认 0
15. `last_read_message_id`：最后已读消息 ID，允许空
16. `last_read_sequence`：最后已读消息序号，`bigint`，默认 0
17. `last_delivered_message_id`：最近下发到客户端的消息 ID，允许空
18. `last_delivered_sequence`：最近下发到客户端的消息序号，`bigint`，默认 0
19. `extra_settings`：JSON 配置，默认空对象
20. `created_at`
21. `updated_at`

#### 4.6.3 角色枚举

1. `owner`
2. `admin`
3. `member`

单聊中双方统一写入：

1. `member`

群主使用：

1. `owner`

#### 4.6.4 状态枚举

1. `active`
2. `left`
3. `removed`

#### 4.6.5 约束

1. `(conversation_id, user_id)` 全局唯一
2. 删除会话列表不改变 `status`，只改 `show_in_list = false`
3. 再次打开会话时，恢复 `show_in_list = true`
4. 被踢出群聊时，改为 `status = removed`
5. 主动退群时，改为 `status = left`
6. 被禁言不改变成员状态，只更新 `mute_until`
7. 单聊会话下理论上应始终保持 2 个真实成员，service 层保证
8. 超级管理员隐身巡检不写入本表

#### 4.6.6 索引

1. `uk_chat_conversation_member_conversation_user`：唯一索引 `(conversation_id, user_id)`
2. `idx_chat_conversation_member_user_status`：`(user_id, status, updated_at)`
3. `idx_chat_conversation_member_user_show_in_list`：`(user_id, show_in_list, updated_at)`
4. `idx_chat_conversation_member_conversation_status`：`(conversation_id, status, role)`
5. `idx_chat_conversation_member_conversation_unread`：`(conversation_id, unread_count)`

### 4.7 `chat_message`

#### 4.7.1 作用

记录消息正文，是聊天系统的主事件流表。

#### 4.7.2 字段设计

1. `id`：主键
2. `conversation_id`：FK -> `chat_conversation.id`
3. `sequence`：会话内自增序号，`bigint`
4. `sender_id`：发送人，FK -> `user_user.id`，系统消息允许空
5. `message_type`：消息类型，`varchar(20)`
6. `content`：消息文本，`text`
7. `payload`：扩展载荷，JSON，默认空对象
8. `client_message_id`：客户端消息 ID，`varchar(64)`，允许空
9. `is_system`：是否系统消息，`bool`，默认 `false`
10. `created_at`
11. `updated_at`

#### 4.7.3 消息类型枚举

1. `text`
2. `system`

预留但 V1 不允许写入：

1. `image`
2. `file`

#### 4.7.4 约束

1. `(conversation_id, sequence)` 唯一
2. `client_message_id` 允许空，但非空时建议全局唯一，用于客户端幂等重试和 WebSocket `message_ack`
3. `is_system = true` 时，`sender_id` 可为空
4. 普通文本消息要求 `sender_id` 非空
5. 若发送人为非好友且未来尝试发送非文本消息，应在 service 层阻止
6. 超级管理员隐身巡检模式下，不允许写入消息

#### 4.7.5 索引

1. `uk_chat_message_conversation_sequence`：唯一索引 `(conversation_id, sequence)`
2. `uk_chat_message_client_message_id`：唯一索引 `client_message_id`
3. `idx_chat_message_conversation_created_at`：`(conversation_id, created_at)`
4. `idx_chat_message_conversation_id_desc`：`(conversation_id, id)`
5. `idx_chat_message_sender_created_at`：`(sender_id, created_at)`

关于消息搜索：

1. V1 可先基于 `content LIKE` 实现
2. 若后续消息量增长，再通过自定义 migration 增加 MySQL FULLTEXT 索引
3. 因此 V1 不强制将 FULLTEXT 写入首版 schema，但保留后续升级空间

### 4.8 `user_preference`

#### 4.8.1 作用

保存用户个性化配置，包括通用 UI 设置和 chat 相关配置。

#### 4.8.2 字段设计

1. `id`：主键
2. `user_id`：OneToOne FK -> `user_user.id`
3. `theme_mode`：`varchar(20)`，默认 `light`
4. `chat_receive_notification`：是否接收聊天通知，`bool`，默认 `true`
5. `chat_list_sort_mode`：会话排序方式，`varchar(20)`，默认 `recent`
6. `chat_stealth_inspect_enabled`：超级管理员隐身巡检开关，`bool`，默认 `false`
7. `settings_json`：JSON 扩展字段，默认空对象
8. `created_at`
9. `updated_at`

#### 4.8.3 约束

1. 每个用户最多一条配置
2. 非超级管理员即使将 `chat_stealth_inspect_enabled` 写为 `true` 也不生效，service 层必须二次校验

#### 4.8.4 索引

1. `uk_user_preference_user_id`：唯一索引 `user_id`
2. `idx_user_preference_chat_stealth_inspect_enabled`：`(chat_stealth_inspect_enabled)`

## 5. 跨表规则与核心约束

### 5.1 好友关系与单聊关系

1. `chat_friendship.pair_key` 与 `chat_conversation.direct_pair_key` 使用同一套生成规则
2. 直接打开某个用户的单聊时，优先按 `direct_pair_key` 查已有会话
3. 若不存在则创建新 direct 会话和两个成员关系
4. 是否为好友不决定“能否建立单聊”，只决定未来扩展消息类型的限制

### 5.2 删除会话只隐藏列表

1. 用户删除会话时，不修改 `chat_conversation`
2. 不删除 `chat_message`
3. 不删除 `chat_conversation_member`
4. 仅将当前用户的 `chat_conversation_member.show_in_list` 置为 `false`

### 5.3 群成员人数缓存

1. `member_count_cache` 只统计 `chat_conversation_member.status = active` 的真实成员
2. `left`、`removed` 不计入
3. 超级管理员隐身巡检永不计入

### 5.4 超级管理员隐身巡检

1. 不写 `chat_conversation_member`
2. 不增加群人数
3. 不触发成员加入/退出广播
4. 不允许发消息
5. 会话列表聚合时，通过“普通可见会话 + 巡检可见会话”两路数据合并实现

### 5.5 群审批流

1. 若 `chat_group_config.join_approval_required = false`，邀请用户时直接创建或恢复成员记录
2. 若为 `true`，则先落 `chat_group_join_request.pending`
3. 审批通过后，再创建或恢复成员记录
4. 审批拒绝后，不创建成员记录

### 5.6 禁言规则

1. 禁言通过 `chat_conversation_member.mute_until` 表示
2. `mute_until > now()` 时，禁止发送消息
3. 支持对超级管理员生效，只要其当前是该群真实成员
4. 对隐身巡检态的超级管理员，天然不可发送消息，因此无需额外禁言记录

## 6. 搜索设计相关约束

V1 搜索范围包括：

1. 会话名
2. 用户名 / 显示名
3. 消息内容

建议查询策略：

1. 会话搜索：基于 `chat_conversation.name` 和当前用户可见会话范围
2. 用户搜索：基于 `user_user.username`、`user_user.display_name`
3. 消息搜索：基于 `chat_message.content` 并限制在当前用户可见会话内

建议结果分组返回：

1. `conversations`
2. `users`
3. `messages`

消息点击跳转定位建议依赖：

1. `conversation_id`
2. `message_id`
3. `sequence`

因此 `chat_message.sequence` 是必备字段，不建议省略。

## 7. 推荐的 Django Model 落地映射

建议代码归属如下：

1. `chat/models.py`
   这里放：`ChatFriendRequest`、`ChatFriendship`、`ChatConversation`、`ChatGroupConfig`、`ChatGroupJoinRequest`、`ChatConversationMember`、`ChatMessage`
2. `user/models.py`
   这里新增：`UserPreference`

这样可以保持：

1. chat 业务表集中在 `chat` app
2. 用户个性化配置仍然属于 `user` 域

## 8. 迁移落地顺序

建议迁移顺序如下：

1. 在 `user` app 中增加 `UserPreference`
2. 在 `chat` app 中创建 `ChatFriendRequest`
3. 创建 `ChatFriendship`
4. 创建 `ChatConversation`
5. 创建 `ChatGroupConfig`
6. 创建 `ChatGroupJoinRequest`
7. 创建 `ChatConversationMember`
8. 创建 `ChatMessage`
9. 增加必要索引和唯一约束
10. 初始化 `chat.*` 权限

## 9. 最终定稿摘要

本次 schema 定稿后的关键结论：

1. 好友关系采用规范化单表，不采用双向两条记录
2. 单聊会话通过 `direct_pair_key` 保证唯一
3. 删除会话只隐藏列表，不删除成员和消息
4. 群配置和群审批拆表，不和主会话表混写
5. 成员表承载未读、阅读游标、禁言和列表展示状态
6. 消息表采用会话内 `sequence` 作为稳定定位游标
7. 超级管理员隐身巡检不进入成员表，只通过配置和查询逻辑实现
8. 用户配置表承载个性化设置和超级管理员巡检开关

该结构可直接进入 Django model 与 migration 实现阶段。
