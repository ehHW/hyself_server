# Chat 模块 API 设计

## 1. 文档目标

本文件用于定义 `chat` 模块 V1 的接口协议，包括：

1. REST 资源设计
2. 请求参数与响应结构
3. WebSocket 事件协议
4. 错误码与权限约束
5. 前后端协作边界

目标是让后续前后端并行开发时，接口层一次定清，不在实现阶段反复改协议。

## 2. 设计原则

### 2.1 分层原则

REST 负责：

1. 初始化加载
2. 列表查询
3. 历史消息分页
4. 好友申请和审批
5. 群管理和群审批
6. 搜索
7. 用户设置读写

WebSocket 负责：

1. 实时消息发送
2. 消息送达确认
3. 未读数变更通知
4. 会话变化通知
5. 好友申请通知
6. 群审批通知
7. 系统通知

### 2.2 通用响应风格

沿用当前项目习惯，优先使用 DRF 默认响应结构。

成功响应：

1. 列表接口返回具名对象
2. 动作接口返回 `detail`、`item`、`record` 或明确业务字段
3. 分页接口沿用 DRF 分页结构，必要时增加业务字段

失败响应：

1. 优先使用 `detail`
2. 表单校验错误返回字段级错误
3. WebSocket 返回结构化 `error` 事件

## 3. 路由总览

建议统一挂载在：

1. `/api/chat/`

建议 REST 资源如下：

1. `/api/chat/friends/requests/`
2. `/api/chat/friends/`
3. `/api/chat/conversations/`
4. `/api/chat/conversations/{id}/messages/`
5. `/api/chat/conversations/{id}/members/`
6. `/api/chat/conversations/{id}/group-config/`
7. `/api/chat/group-join-requests/`
8. `/api/chat/search/`
9. `/api/chat/settings/`
10. `/api/chat/admin/conversations/`
11. `/api/chat/admin/messages/`

WebSocket 统一复用当前全局连接：

1. `/ws/global/?token=<access_token>`

chat 实时事件通过统一 `type` 分发，不额外创建第二条 WebSocket 连接。

## 4. REST 资源设计

### 4.1 好友申请

#### 4.1.1 发起好友申请

接口：

1. `POST /api/chat/friends/requests/`

请求体：

```json
{
  "to_user_id": 12,
  "request_message": "你好，想加你为好友"
}
```

处理规则：

1. 不能给自己发送申请
2. 已是好友时禁止重复申请
3. 若存在反向 `pending` 申请，则直接自动通过
4. 自动通过时直接创建或恢复好友关系，并创建或恢复单聊会话

成功响应一：普通待审批

```json
{
  "mode": "pending",
  "detail": "好友申请已发送",
  "request": {
    "id": 101,
    "status": "pending",
    "from_user": {
      "id": 3,
      "username": "alice",
      "display_name": "Alice"
    },
    "to_user": {
      "id": 12,
      "username": "bob",
      "display_name": "Bob"
    },
    "request_message": "你好，想加你为好友",
    "auto_accepted": false,
    "created_at": "2026-04-07T10:00:00+08:00"
  }
}
```

成功响应二：互相申请自动通过

```json
{
  "mode": "auto_accepted",
  "detail": "双方已自动成为好友",
  "friendship": {
    "id": 21,
    "status": "active",
    "friend_user": {
      "id": 12,
      "username": "bob",
      "display_name": "Bob"
    },
    "accepted_at": "2026-04-07T10:00:00+08:00"
  },
  "conversation": {
    "id": 88,
    "type": "direct",
    "show_in_list": true
  }
}
```

#### 4.1.2 收到的好友申请列表

接口：

1. `GET /api/chat/friends/requests/?direction=received&status=pending&page=1&page_size=20`

查询参数：

1. `direction`: `received | sent | all`，默认 `received`
2. `status`: 可选
3. `page`
4. `page_size`

响应示例：

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 101,
      "status": "pending",
      "from_user": {
        "id": 3,
        "username": "alice",
        "display_name": "Alice"
      },
      "to_user": {
        "id": 12,
        "username": "bob",
        "display_name": "Bob"
      },
      "request_message": "你好，想加你为好友",
      "auto_accepted": false,
      "handled_by": null,
      "handled_at": null,
      "created_at": "2026-04-07T10:00:00+08:00"
    }
  ]
}
```

#### 4.1.3 处理好友申请

接口：

1. `POST /api/chat/friends/requests/{id}/handle/`

请求体：

```json
{
  "action": "accept"
}
```

`action` 允许值：

1. `accept`
2. `reject`
3. `cancel`

处理规则：

1. 只有接收方可以 `accept` 或 `reject`
2. 只有发起方可以 `cancel`
3. `accept` 时创建或恢复 `chat_friendship`
4. `accept` 时确保单聊会话存在，并将双方 `show_in_list` 设为 `true`

成功响应示例：

```json
{
  "detail": "好友申请已通过",
  "request": {
    "id": 101,
    "status": "accepted"
  },
  "friendship": {
    "id": 21,
    "status": "active"
  },
  "conversation": {
    "id": 88,
    "type": "direct"
  }
}
```

### 4.2 好友列表

#### 4.2.1 好友列表

接口：

1. `GET /api/chat/friends/?keyword=&page=1&page_size=50`

响应示例：

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "friendship_id": 21,
      "friend_user": {
        "id": 12,
        "username": "bob",
        "display_name": "Bob",
        "avatar": "/uploads/avatars/bob.png"
      },
      "accepted_at": "2026-04-07T10:00:00+08:00",
      "direct_conversation": {
        "id": 88,
        "show_in_list": true
      }
    }
  ]
}
```

#### 4.2.2 删除好友

接口：

1. `POST /api/chat/friends/{friend_user_id}/delete/`

处理规则：

1. 不是物理删除记录，而是将 `chat_friendship.status` 设为 `deleted`
2. 不删除单聊会话
3. 不删除历史消息
4. 后续仍可从同群成员中再次私聊

响应示例：

```json
{
  "detail": "已删除好友",
  "friend_user_id": 12
}
```

### 4.3 会话列表

#### 4.3.1 会话列表

接口：

1. `GET /api/chat/conversations/?category=all&page=1&page_size=50&keyword=`

查询参数：

1. `category`: `all | direct | group`
2. `keyword`: 可选，仅搜索当前可见会话名称
3. `page`
4. `page_size`

返回内容要求：

1. 只返回当前用户可见的会话
2. 普通用户只看到自己的真实会话
3. 开启隐身巡检的超级管理员可额外看到系统所有会话
4. 隐身巡检看到的会话需标记 `access_mode = stealth_readonly`

响应示例：

```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 88,
      "type": "direct",
      "name": "Bob",
      "avatar": "/uploads/avatars/bob.png",
      "access_mode": "member",
      "member_role": "member",
      "show_in_list": true,
      "unread_count": 3,
      "last_message_preview": "你好",
      "last_message_at": "2026-04-07T10:05:00+08:00",
      "member_count": 2,
      "can_send_message": true
    },
    {
      "id": 120,
      "type": "group",
      "name": "开发测试群",
      "avatar": "",
      "access_mode": "stealth_readonly",
      "member_role": null,
      "show_in_list": true,
      "unread_count": 0,
      "last_message_preview": "今晚发版",
      "last_message_at": "2026-04-07T10:08:00+08:00",
      "member_count": 18,
      "can_send_message": false
    }
  ]
}
```

#### 4.3.2 会话详情

接口：

1. `GET /api/chat/conversations/{id}/`

响应示例：

```json
{
  "id": 120,
  "type": "group",
  "name": "开发测试群",
  "avatar": "",
  "access_mode": "member",
  "member_role": "admin",
  "status": "active",
  "member_count": 18,
  "owner": {
    "id": 2,
    "username": "owner",
    "display_name": "群主"
  },
  "group_config": {
    "join_approval_required": true,
    "allow_member_invite": true,
    "max_members": null,
    "mute_all": false
  },
  "can_send_message": true,
  "show_in_list": true,
  "unread_count": 3,
  "last_read_sequence": 55,
  "last_message_preview": "今晚发版",
  "last_message_at": "2026-04-07T10:08:00+08:00"
}
```

#### 4.3.3 创建或打开单聊

接口：

1. `POST /api/chat/conversations/direct/open/`

请求体：

```json
{
  "target_user_id": 12
}
```

处理规则：

1. 若 direct 会话已存在，则恢复当前用户 `show_in_list = true`
2. 若不存在，则创建会话和双方成员记录
3. 同群非好友之间允许打开单聊

响应示例：

```json
{
  "detail": "会话已打开",
  "created": false,
  "conversation": {
    "id": 88,
    "type": "direct",
    "show_in_list": true
  }
}
```

#### 4.3.4 创建群聊

接口：

1. `POST /api/chat/conversations/groups/`

请求体：

```json
{
  "name": "开发测试群",
  "member_user_ids": [12, 18, 20],
  "join_approval_required": true,
  "allow_member_invite": true
}
```

处理规则：

1. 任意用户可创建群聊
2. 创建者自动成为 `owner`
3. `member_user_ids` 中的初始成员直接加入，不走审批
4. 创建群时自动生成 `chat_group_config`

响应示例：

```json
{
  "detail": "群聊创建成功",
  "conversation": {
    "id": 120,
    "type": "group",
    "name": "开发测试群"
  }
}
```

#### 4.3.5 删除会话

接口：

1. `POST /api/chat/conversations/{id}/hide/`

处理规则：

1. 只对当前用户生效
2. 将当前用户成员记录 `show_in_list` 改为 `false`
3. 不删除消息和成员关系

响应示例：

```json
{
  "detail": "会话已从列表移除",
  "conversation_id": 88,
  "show_in_list": false
}
```

#### 4.3.6 标记会话已读

接口：

1. `POST /api/chat/conversations/{id}/read/`

请求体：

```json
{
  "last_read_sequence": 58
}
```

处理规则：

1. 将当前用户该会话未读数清零
2. 更新 `last_read_message_id` 或 `last_read_sequence`
3. REST 与 WebSocket 都可以触发该动作，最终共享同一 service

响应示例：

```json
{
  "detail": "已标记为已读",
  "conversation_id": 120,
  "unread_count": 0,
  "last_read_sequence": 58
}
```

### 4.4 消息历史

#### 4.4.1 历史消息分页

接口：

1. `GET /api/chat/conversations/{id}/messages/?before_sequence=58&limit=30`

查询参数：

1. `before_sequence`: 向上翻页游标，可选
2. `after_sequence`: 向下补拉游标，可选
3. `limit`: 默认 30，最大建议 100

响应示例：

```json
{
  "conversation": {
    "id": 120,
    "type": "group",
    "access_mode": "member",
    "can_send_message": true
  },
  "cursor": {
    "before_sequence": 29,
    "after_sequence": 58,
    "has_more_before": true,
    "has_more_after": false
  },
  "items": [
    {
      "id": 2001,
      "sequence": 57,
      "message_type": "text",
      "content": "今晚发版",
      "payload": {},
      "is_system": false,
      "sender": {
        "id": 2,
        "username": "owner",
        "display_name": "群主"
      },
      "created_at": "2026-04-07T10:08:00+08:00"
    }
  ]
}
```

### 4.5 群成员与群配置

#### 4.5.1 成员列表

接口：

1. `GET /api/chat/conversations/{id}/members/`

响应示例：

```json
{
  "conversation_id": 120,
  "items": [
    {
      "user": {
        "id": 2,
        "username": "owner",
        "display_name": "群主"
      },
      "role": "owner",
      "status": "active",
      "mute_until": null,
      "joined_at": "2026-04-07T09:00:00+08:00"
    }
  ]
}
```

#### 4.5.2 邀请成员

接口：

1. `POST /api/chat/conversations/{id}/members/invite/`

请求体：

```json
{
  "target_user_id": 12
}
```

处理规则：

1. 若 `allow_member_invite = false`，则只有 `owner/admin` 可邀请
2. 若 `join_approval_required = false`，直接加入
3. 若 `join_approval_required = true`，则创建 `pending` 审批记录

成功响应一：直接加入

```json
{
  "mode": "joined",
  "detail": "成员已加入群聊",
  "member": {
    "user_id": 12,
    "status": "active"
  }
}
```

成功响应二：进入审批

```json
{
  "mode": "pending_approval",
  "detail": "已提交群审批",
  "join_request": {
    "id": 301,
    "status": "pending"
  }
}
```

#### 4.5.3 退出群聊

接口：

1. `POST /api/chat/conversations/{id}/leave/`

响应示例：

```json
{
  "detail": "已退出群聊",
  "conversation_id": 120
}
```

#### 4.5.4 踢出成员

接口：

1. `POST /api/chat/conversations/{id}/members/{user_id}/remove/`

权限：

1. `owner`
2. `admin`

处理规则：

1. 超级管理员若是真实成员，也可被踢出
2. 不能通过该接口踢出隐身巡检态超级管理员，因为其并非真实成员

响应示例：

```json
{
  "detail": "已移出群成员",
  "conversation_id": 120,
  "user_id": 12
}
```

#### 4.5.5 设置成员角色

接口：

1. `POST /api/chat/conversations/{id}/members/{user_id}/role/`

请求体：

```json
{
  "role": "admin"
}
```

#### 4.5.6 禁言成员

接口：

1. `POST /api/chat/conversations/{id}/members/{user_id}/mute/`

请求体：

```json
{
  "mute_minutes": 30,
  "reason": "刷屏"
}
```

说明：

1. 这是 V1 的基础禁言能力
2. V1 不做复杂禁言策略，但允许设置 `mute_until`

#### 4.5.7 更新群配置

接口：

1. `PATCH /api/chat/conversations/{id}/group-config/`

请求体：

```json
{
  "join_approval_required": true,
  "allow_member_invite": true,
  "max_members": null,
  "mute_all": false
}
```

权限：

1. `owner`
2. `admin`

### 4.6 群审批

#### 4.6.1 待审批列表

接口：

1. `GET /api/chat/group-join-requests/?conversation_id=120&status=pending&page=1&page_size=20`

#### 4.6.2 审批处理

接口：

1. `POST /api/chat/group-join-requests/{id}/handle/`

请求体：

```json
{
  "action": "approve",
  "review_note": "通过"
}
```

`action` 允许值：

1. `approve`
2. `reject`
3. `cancel`

处理规则：

1. `approve/reject` 仅群主或群管理员可执行
2. `cancel` 仅发起邀请人可执行
3. 审批通过时创建或恢复成员记录

### 4.7 搜索

#### 4.7.1 综合搜索

接口：

1. `GET /api/chat/search/?keyword=开发&limit=5`

查询规则：

1. `keyword` 去首尾空白后不能为空
2. `limit` 为每个分组的上限，不是全局上限
3. 搜索结果必须限制在当前用户可访问范围内
4. 隐身巡检开启的超级管理员，额外拥有系统全会话搜索范围

响应示例：

```json
{
  "keyword": "开发",
  "conversations": [
    {
      "id": 120,
      "type": "group",
      "name": "开发测试群",
      "access_mode": "member"
    }
  ],
  "users": [
    {
      "id": 12,
      "username": "dev_bob",
      "display_name": "开发 Bob",
      "can_open_direct": true
    }
  ],
  "messages": [
    {
      "conversation_id": 120,
      "conversation_name": "开发测试群",
      "message_id": 2001,
      "sequence": 57,
      "message_type": "text",
      "content_preview": "今晚发版",
      "sender": {
        "id": 2,
        "username": "owner",
        "display_name": "群主"
      },
      "created_at": "2026-04-07T10:08:00+08:00"
    }
  ]
}
```

### 4.8 用户设置

#### 4.8.1 获取设置

接口：

1. `GET /api/chat/settings/`

响应示例：

```json
{
  "theme_mode": "light",
  "chat_receive_notification": true,
  "chat_list_sort_mode": "recent",
  "chat_stealth_inspect_enabled": false,
  "settings_json": {
    "search_history": []
  }
}
```

#### 4.8.2 更新设置

接口：

1. `PATCH /api/chat/settings/`

请求体：

```json
{
  "chat_receive_notification": true,
  "chat_list_sort_mode": "recent",
  "chat_stealth_inspect_enabled": true,
  "settings_json": {
    "search_history": ["开发"]
  }
}
```

处理规则：

1. 非超级管理员提交 `chat_stealth_inspect_enabled = true` 时，后端应忽略或报错

### 4.9 管理员聊天审计

#### 4.9.1 查看全量会话

接口：

1. `GET /api/chat/admin/conversations/?keyword=&type=group&page=1&page_size=20`

权限：

1. `chat.review_all_messages`

#### 4.9.2 查看全量消息

接口：

1. `GET /api/chat/admin/messages/?conversation_id=120&keyword=发版&page=1&page_size=50`

权限：

1. `chat.review_all_messages`

## 5. REST 序列化建议

建议统一几个基础对象，减少前后端重复适配。

### 5.1 UserBrief

```json
{
  "id": 12,
  "username": "bob",
  "display_name": "Bob",
  "avatar": "/uploads/avatars/bob.png"
}
```

### 5.2 ConversationBrief

```json
{
  "id": 120,
  "type": "group",
  "name": "开发测试群",
  "avatar": "",
  "access_mode": "member",
  "member_role": "admin",
  "show_in_list": true,
  "unread_count": 3,
  "last_message_preview": "今晚发版",
  "last_message_at": "2026-04-07T10:08:00+08:00",
  "member_count": 18,
  "can_send_message": true
}
```

### 5.3 MessageItem

```json
{
  "id": 2001,
  "sequence": 57,
  "message_type": "text",
  "content": "今晚发版",
  "payload": {},
  "is_system": false,
  "sender": {
    "id": 2,
    "username": "owner",
    "display_name": "群主"
  },
  "created_at": "2026-04-07T10:08:00+08:00"
}
```

## 6. WebSocket 事件协议

### 6.1 连接与身份

继续复用：

1. `/ws/global/?token=<access_token>`

连接建立成功后，服务端先返回：

```json
{
  "type": "system",
  "message": "WebSocket 已连接: alice"
}
```

### 6.2 客户端 -> 服务端事件

#### 6.2.1 心跳

```json
{
  "type": "ping",
  "timestamp": 1743990000000
}
```

响应：

```json
{
  "type": "pong",
  "timestamp": 1743990000000
}
```

#### 6.2.2 发送消息

```json
{
  "type": "chat_send_message",
  "client_message_id": "cfd6cbf4-0e2f-4fa9-bf58-9f758bc60a90",
  "conversation_id": 120,
  "message_type": "text",
  "content": "今晚发版",
  "payload": {}
}
```

规则：

1. V1 只允许 `message_type = text`
2. 非成员不能发消息
3. 被禁言成员不能发消息
4. 隐身巡检态超级管理员不能发消息
5. 发消息成功后先落库，再广播

#### 6.2.3 标记已读

```json
{
  "type": "chat_mark_read",
  "conversation_id": 120,
  "last_read_sequence": 58
}
```

#### 6.2.4 输入中状态

```json
{
  "type": "chat_typing",
  "conversation_id": 120,
  "is_typing": true
}
```

说明：

1. 这是弱状态，不落库
2. 服务端只做短时广播

#### 6.2.5 上传任务订阅

保留现有：

1. `subscribe_upload_task`
2. `unsubscribe_upload_task`

chat 模块不覆盖现有上传事件。

### 6.3 服务端 -> 客户端事件

#### 6.3.1 发送确认 `chat_message_ack`

```json
{
  "type": "chat_message_ack",
  "client_message_id": "cfd6cbf4-0e2f-4fa9-bf58-9f758bc60a90",
  "conversation_id": 120,
  "message": {
    "id": 2001,
    "sequence": 57,
    "message_type": "text",
    "content": "今晚发版",
    "payload": {},
    "is_system": false,
    "sender": {
      "id": 2,
      "username": "owner",
      "display_name": "群主"
    },
    "created_at": "2026-04-07T10:08:00+08:00"
  }
}
```

用途：

1. 客户端将 optimistic message 替换为正式消息
2. 实现幂等重试

#### 6.3.2 新消息 `chat_new_message`

```json
{
  "type": "chat_new_message",
  "conversation_id": 120,
  "message": {
    "id": 2001,
    "sequence": 57,
    "message_type": "text",
    "content": "今晚发版",
    "payload": {},
    "is_system": false,
    "sender": {
      "id": 2,
      "username": "owner",
      "display_name": "群主"
    },
    "created_at": "2026-04-07T10:08:00+08:00"
  }
}
```

#### 6.3.3 会话更新 `chat_conversation_updated`

```json
{
  "type": "chat_conversation_updated",
  "conversation": {
    "id": 120,
    "type": "group",
    "name": "开发测试群",
    "access_mode": "member",
    "show_in_list": true,
    "unread_count": 3,
    "last_message_preview": "今晚发版",
    "last_message_at": "2026-04-07T10:08:00+08:00",
    "member_count": 18,
    "can_send_message": true
  }
}
```

触发场景：

1. 新消息
2. 恢复显示会话
3. 群名称变更
4. 群配置变更
5. 群成员变化

#### 6.3.4 未读更新 `chat_unread_updated`

```json
{
  "type": "chat_unread_updated",
  "conversation_id": 120,
  "unread_count": 3,
  "total_unread_count": 12
}
```

#### 6.3.5 好友申请更新 `chat_friend_request_updated`

```json
{
  "type": "chat_friend_request_updated",
  "request": {
    "id": 101,
    "status": "pending",
    "from_user": {
      "id": 3,
      "username": "alice",
      "display_name": "Alice"
    },
    "to_user": {
      "id": 12,
      "username": "bob",
      "display_name": "Bob"
    },
    "request_message": "你好，想加你为好友",
    "auto_accepted": false,
    "created_at": "2026-04-07T10:00:00+08:00"
  }
}
```

#### 6.3.6 好友关系更新 `chat_friendship_updated`

```json
{
  "type": "chat_friendship_updated",
  "action": "accepted",
  "friend_user": {
    "id": 12,
    "username": "bob",
    "display_name": "Bob"
  },
  "conversation": {
    "id": 88,
    "type": "direct",
    "show_in_list": true
  }
}
```

`action` 建议值：

1. `accepted`
2. `deleted`
3. `restored`

#### 6.3.7 群审批更新 `chat_group_join_request_updated`

```json
{
  "type": "chat_group_join_request_updated",
  "join_request": {
    "id": 301,
    "conversation_id": 120,
    "status": "pending",
    "target_user": {
      "id": 12,
      "username": "bob",
      "display_name": "Bob"
    },
    "created_at": "2026-04-07T10:09:00+08:00"
  }
}
```

#### 6.3.8 输入中 `chat_typing`

```json
{
  "type": "chat_typing",
  "conversation_id": 120,
  "user": {
    "id": 2,
    "username": "owner",
    "display_name": "群主"
  },
  "is_typing": true
}
```

#### 6.3.9 系统通知 `system_notice`

```json
{
  "type": "system_notice",
  "category": "chat",
  "message": "你已被移出群聊",
  "payload": {
    "conversation_id": 120
  }
}
```

#### 6.3.10 错误事件 `error`

```json
{
  "type": "error",
  "code": "CHAT_FORBIDDEN",
  "message": "当前无权发送消息",
  "request_type": "chat_send_message",
  "conversation_id": 120
}
```

## 7. WebSocket 路由与分发建议

继续使用当前 `GlobalWebSocketConsumer`，建议在其中新增 chat 分发分支。

建议内部结构：

1. `ping/pong`
2. `upload task subscribe`
3. `chat_send_message`
4. `chat_mark_read`
5. `chat_typing`
6. 统一错误处理

后端事件封装建议增加：

1. `notify_chat_new_message(user_id, payload)`
2. `notify_chat_conversation_updated(user_id, payload)`
3. `notify_chat_unread_updated(user_id, payload)`
4. `notify_chat_friend_request_updated(user_id, payload)`
5. `notify_chat_friendship_updated(user_id, payload)`
6. `notify_chat_group_join_request_updated(user_id, payload)`

## 8. 权限与访问控制

### 8.1 普通访问控制

1. 只有成员可读取普通会话详情和消息历史
2. 同群非好友可打开 direct 会话
3. direct 会话中普通文本消息可发送
4. 群成员邀请权限受 `allow_member_invite` 和角色限制
5. 群审批处理仅 `owner/admin` 可执行

### 8.2 超级管理员特殊访问控制

1. 超级管理员默认按普通成员规则处理
2. 开启 `chat_stealth_inspect_enabled` 后，可查看系统所有会话
3. 隐身巡检只读，不能发消息
4. 隐身巡检不改变成员关系与群人数
5. `chat.review_all_messages` 是后台全量审计接口权限，与隐身巡检是两条不同能力线

说明：

1. `chat.review_all_messages` 用于后台审计型 REST 接口
2. `chat_stealth_inspect_enabled` 用于聊天 UI 内部的只读旁观能力
3. 两者可以同时存在，也可以只存在其一

## 9. 错误码建议

建议先在 service 层统一错误语义，HTTP 和 WebSocket 共享。

建议错误码：

1. `CHAT_INVALID_TARGET`
2. `CHAT_ALREADY_FRIEND`
3. `CHAT_FRIEND_REQUEST_DUPLICATED`
4. `CHAT_FRIEND_REQUEST_NOT_FOUND`
5. `CHAT_FRIEND_REQUEST_FORBIDDEN`
6. `CHAT_CONVERSATION_NOT_FOUND`
7. `CHAT_CONVERSATION_FORBIDDEN`
8. `CHAT_MESSAGE_FORBIDDEN`
9. `CHAT_MESSAGE_MUTED`
10. `CHAT_MESSAGE_TEXT_ONLY`
11. `CHAT_GROUP_CONFIG_FORBIDDEN`
12. `CHAT_GROUP_JOIN_REQUEST_NOT_FOUND`
13. `CHAT_GROUP_JOIN_REQUEST_FORBIDDEN`
14. `CHAT_SEARCH_KEYWORD_REQUIRED`
15. `CHAT_STEALTH_READONLY`

HTTP 返回建议：

```json
{
  "code": "CHAT_MESSAGE_MUTED",
  "detail": "你当前已被禁言，暂时不能发送消息"
}
```

WebSocket 返回建议：

```json
{
  "type": "error",
  "code": "CHAT_MESSAGE_MUTED",
  "message": "你当前已被禁言，暂时不能发送消息",
  "request_type": "chat_send_message",
  "conversation_id": 120
}
```

## 10. 前后端协作建议

前端建议采用以下流程：

1. 页面初始化先拉会话列表、好友列表、待处理申请数
2. 用户进入会话时，先调消息历史，再建立当前会话阅读状态
3. 发送消息走 WebSocket，失败时按 `client_message_id` 回滚
4. 搜索统一走 REST，不通过 WebSocket
5. 好友申请、群审批、群配置修改等管理操作统一走 REST

后端建议采用以下分层：

1. serializer 做格式校验
2. service 做业务规则
3. event dispatcher 做 WebSocket 推送
4. consumer 只负责协议层解析和分发

## 11. 最终定稿摘要

本次 API 定稿后的关键结论：

1. REST 负责资源管理与初始化，WebSocket 负责实时事件
2. 好友申请、群审批、搜索、设置全部走 REST
3. 实时发消息、已读、输入中走 WebSocket
4. 所有实时事件统一复用 `/ws/global/`
5. `chat_message_ack` + `client_message_id` 用于前端发送幂等和状态回填
6. 超级管理员隐身巡检通过会话列表和历史消息接口实现只读访问，不通过真实成员关系实现

该文档可直接作为后端接口实现与前端联调的协议依据。
