# bbot_server 后端

基于 Django + DRF + Channels + JWT 的服务端。当前主线在 V1 可用版本基础上，已经落地一批 Chat V2 分层重构与附件消息能力。

项目当前覆盖以下核心业务：

- 认证鉴权与权限控制
- 用户 / 角色 / 权限管理
- 资源中心文件管理与上传
- 回收站与去重恢复
- 聊天室 V1 + V2 过渡能力
- WebSocket 实时事件
- Celery 异步上传合并与相关任务支撑

## 技术栈

- Python
- Django 5.2
- Django REST Framework
- SimpleJWT
- Django Channels + channels-redis
- Celery + Redis
- MySQL（默认配置）

## 运行环境

- Python: >=3.14
- MySQL: 建议 8.x
- Redis: 建议 6.x 及以上

## 安装依赖

可任选一种方式。

### 方式一：使用 venv + pip

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -e .
```

### 方式二：使用 uv

```bash
uv sync
```

## 环境变量

项目通过 .env 加载配置。常用变量如下：

```env
DEBUG=True
SECRET_KEY=请替换为你自己的密钥

DATABASE_HOST=localhost
DATABASE_PORT=3306
DATABASE_NAME=bbot_server
DATABASE_USER=root
DATABASE_PASSWORD=your_password

ALLOWED_HOSTS=127.0.0.1,localhost,testserver
CORS_ALLOW_ALL_ORIGINS=True
CORS_ALLOW_CREDENTIALS=False

CHANNEL_REDIS_URL=redis://127.0.0.1:6379/1
CELERY_BROKER_URL=redis://127.0.0.1:6379/2
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/3
```

## 数据库初始化

```bash
python manage.py migrate
```

运行聊天相关回归测试：

```bash
python manage.py test chat.tests
```

可选：创建管理后台账号

```bash
python manage.py createsuperuser
```

## 启动服务

### 方式一：Django 开发服务

```bash
python manage.py runserver 127.0.0.1:8000
```

说明：仅调普通 REST 接口时可用，但如果要完整验证聊天室实时推送，建议使用 ASGI 方式。

### 方式二：ASGI

```bash
uvicorn bbot_server.asgi:application --host 127.0.0.1 --port 8000 --reload --lifespan off
```

如果你是在 workspace 根目录 `d:/work/SolBot` 启动，而不是先切进 `bbot_server/`，请使用下面这条更稳的显式命令，避免 `Could not import module "bbot_server.asgi"` 这类路径问题：

```bash
d:/work/SolBot/bbot_server/.venv/Scripts/python.exe -m uvicorn --app-dir d:/work/SolBot/bbot_server bbot_server.asgi:application --host 127.0.0.1 --port 8000 --reload --lifespan off
```

当前 WebSocket 入口：

- /ws/global/

前端会通过 access token 建立全局连接，用于消息推送、未读同步、好友申请和群通知等事件。

## Celery

如果你需要大文件分片合并或其他异步任务，另开终端启动 worker：

```bash
celery -A bbot_server worker -l info --pool=solo -c 1
```

如果你是在 workspace 根目录 `d:/work/SolBot` 启动，请改用下面这条显式工作目录与 app 路径的命令，避免 `Module 'bbot_server' has no attribute 'celery'`：

```bash
d:/work/SolBot/bbot_server/.venv/Scripts/python.exe -m celery --workdir d:/work/SolBot/bbot_server -A bbot_server.celery:app worker -l info --pool=solo -c 1
```

Windows 推荐固定使用上面的 solo 模式，避免 billiard 相关兼容问题。

## 定时任务

项目级定时任务统一放在 bbot_server/cron_jobs/。

当前已提供回收站清理命令：

```bash
python manage.py cleanup_recycle_bin
```

建议每天定时执行一次。

## 当前模块说明

### 认证与权限

- JWT 登录、刷新、用户信息获取
- 当前登录用户资料更新与自助修改密码
- 用户、角色、权限三套管理接口
- 基于权限码的菜单与接口访问控制

### 资源中心接口

- 文件树与目录浏览
- 文件搜索
- 新建文件夹、重命名、软删除
- 回收站浏览、恢复、彻底删除、清空
- 小文件直传
- 大文件分片上传、续传、合并
- 基于 MD5 的重复文件识别
- 回收站同 MD5 文件恢复到用户当前选定目录
- 用户资源列表仅返回活动中的 RESOURCE_CENTER 引用；删除或还原时会同步更新兼容 UploadedFile 与 AssetReference 状态
- 回收站目录内列出真实回收文件树，避免资源中心活动引用与回收站视图不一致

### 聊天室

- 单聊与群聊会话
- 好友申请、好友列表、好友备注
- 群创建、邀请、退群、成员管理
- 文本消息与附件消息
- 视频附件处理会写入 HLS 播放地址与缩略图地址；缩略图和播放列表 URL 带版本参数，降低浏览器旧缓存干扰
- 多消息逐条转发与合并转发
- chat_record 聊天记录消息类型
- 文本 / 附件消息历史查询
- 未读数更新与已读同步
- 会话隐藏、置顶、个人会话偏好
- 聊天搜索
- 管理员聊天巡检接口
- 全员禁言、群角色与群主权限控制

## Chat V2 进展

当前后端已经完成以下 V2 方向改造：

- chat 模块已从单一 services 模式演进为 application commands / queries 与 domain 规则分层
- WebSocket 广播事件已收敛为标准化 envelope，便于前端统一消费
- 附件消息发送已经接入 chat/application/commands/attachments.py
- 自聊、自身会话、群权限与禁言等规则已补充回归测试
- 转发链路已支持 forward_mode，统一编排逐条转发与合并转发
- 合并转发已落为正式 chat_record 消息，并补充递归 payload 序列化与回归测试

## 主要 REST 路由

### 用户与认证

- /api/auth/*
- /api/users/*
- /api/roles/*
- /api/permissions/*

### 资源中心

- /api/upload/files/
- /api/upload/search/
- /api/upload/recycle-bin/
- /api/upload/recycle-bin/restore/
- /api/upload/recycle-bin/clear/
- /api/upload/folders/
- /api/upload/delete/
- /api/upload/rename/
- /api/upload/small/
- /api/upload/precheck/
- /api/upload/chunk/
- /api/upload/chunks/
- /api/upload/merge/

### 聊天模块

- /api/chat/friends/*
- /api/chat/conversations/*
- /api/chat/group-join-requests/*
- /api/chat/search/
- /api/chat/settings/
- /api/chat/admin/conversations/
- /api/chat/admin/messages/

当前 WebSocket 命令除文本消息外，还包含附件消息发送相关动作；事件广播统一通过聊天域事件下发。

### 其他

- /api/game/*
- /uploads/* 仅在 DEBUG 模式下由 Django 直接提供媒体访问

## 目录结构

```text
bbot_server/
  bbot_server/     # 项目配置（settings、urls、asgi、celery）
  bbot/            # 资源中心、文件上传、回收站
  chat/            # 聊天模块
  game/            # 游戏相关接口
  user/            # 用户、角色、权限、认证
  utils/           # 通用工具
  ws/              # WebSocket 鉴权、路由、事件、消费者
  docs/            # 设计文档与 V1 方案文档
  manage.py
```

## 联调建议

1. 确保 MySQL、Redis 已启动。
2. 完成 .env 配置并执行迁移。
3. 使用 uvicorn 启动 ASGI 服务，便于同时验证 REST 和 WebSocket。
4. 如需测试大文件上传，额外启动 Celery worker。
5. 再启动前端进行联调。

## 相关文档

- docs/V1/chat_v1_plan.md
- docs/V1/chat_v1_api_design.md
- docs/V1/chat_v1_schema_design.md
- docs/V2/v2_architecture.md
- docs/V2/chat_v2_refactor_plan.md
- docs/V2/assets_v2_design.md
- docs/implementation_notes/README.md
- docs/implementation_notes/chat_multi_forward_design.md

## 备注

- 当前仓库仍保留部分 V1 文档与命名，但聊天主线代码已经进入 V2 渐进重构阶段。
- 回收站去重恢复逻辑会优先尝试复用同 MD5 文件，并将恢复目标落到用户当前所选目录。
