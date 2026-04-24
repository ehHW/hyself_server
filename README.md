# hyself_server 后端

基于 Django、DRF、Channels、JWT 和 Celery 的服务端项目，当前主线已经完成 Chat V2 分层重构、资源中心资产兼容和浏览器回归所需的联调能力。

仓库地址：<https://github.com/ehHW/hyself_server.git>

## 技术栈

- Python 3.14+
- Django 5.2
- Django REST Framework
- SimpleJWT
- Channels + channels-redis
- Celery + Redis
- MySQL

## 安装依赖

方式一：venv + pip

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

方式二：uv

```bash
uv sync
```

## 常用环境变量

```env
DEBUG=True
SECRET_KEY=replace-me

DATABASE_HOST=localhost
DATABASE_PORT=3306
DATABASE_NAME=hyself_server
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

如果是从旧 bbot / bbot_server 数据库迁移过来，先执行：

```bash
python manage.py align_hyself_rename_metadata
```

## 本地数据重置

如果你要清空本地上传文件、数据库中的用户与关联数据，并重建干净的基础账号与角色，执行：

```bash
python manage.py reset_local_data --yes
```

执行后会重建以下正式角色：

- 超级管理员
- 系统管理员
- 普通用户

并创建以下基础账号：

- admin
- user01
- user02
- user03
- user04
- user05

命令会在终端输出本次生成的密码。

如果你希望直接通过独立脚本执行，也可以使用：

```bash
python tools/reset_local_data.py --yes
```

## 浏览器烟雾测试账号

浏览器回归账号改为按需创建的临时数据：

```bash
python manage.py seed_smoke_data
```

测试完成后请立即清理：

```bash
python manage.py cleanup_smoke_data
```

也可以直接执行工具脚本：

```bash
python tools/seed_smoke_data.py
python tools/cleanup_smoke_data.py
```

烟雾测试账号只用于本地开发与回归验证，不应长期保留。

## 测试命令

```bash
python manage.py test chat.tests
python manage.py test chat.tests user.tests hyself.tests game.tests
```

## 启动服务

仅调 REST 时可以用 Django 开发服务器：

```bash
python manage.py runserver 127.0.0.1:8000
```

聊天联调建议使用 ASGI：

```bash
uvicorn hyself_server.asgi:application --host 127.0.0.1 --port 8000 --reload --lifespan off
```

如果你从工作区根目录启动，请使用：

```bash
hyself_server/.venv/Scripts/python.exe -m uvicorn --app-dir ./hyself_server hyself_server.asgi:application --host 127.0.0.1 --port 8000 --reload --lifespan off
```

WebSocket 入口：

- /ws/global/

## Celery

```bash
celery -A hyself_server worker -l info --pool=solo -c 1
```

从工作区根目录启动：

```bash
hyself_server/.venv/Scripts/python.exe -m celery --workdir ./hyself_server -A hyself_server.celery:app worker -l info --pool=solo -c 1
```

Windows 下建议固定使用 solo 池，避免 billiard 兼容问题。

## 定时任务

回收站清理命令：

```bash
python manage.py cleanup_recycle_bin
```

独立工具入口：

```bash
python tools/cleanup_recycle_bin.py
```

## 功能范围

- 认证鉴权与权限控制
- 用户、角色、权限管理
- 资源中心文件管理、回收站与去重恢复
- Chat V1 到 V2 的过渡能力
- WebSocket 实时事件
- Celery 异步上传与视频处理

## Chat V2 已完成项

- chat 模块已演进到 application commands / queries / domain 分层
- WebSocket 广播事件统一为 envelope 结构
- 附件消息走 chat/application/commands/attachments.py
- 自聊、自身会话、群权限、禁言和转发规则均有回归测试
- 合并转发已经落为 chat_record 消息，并补齐递归 payload 序列化

## 主要路由

用户与认证：

- /api/auth/\*
- /api/users/\*
- /api/roles/\*
- /api/permissions/\*

资源中心：

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

聊天模块：

- /api/chat/friends/\*
- /api/chat/conversations/\*
- /api/chat/group-join-requests/\*
- /api/chat/search/
- /api/chat/settings/
- /api/chat/admin/conversations/
- /api/chat/admin/messages/

其他：

- /api/game/\*
- /uploads/\* 仅在 DEBUG 模式下由 Django 直接提供媒体访问

## 目录结构

```text
hyself_server/
  tools/            # 可独立执行的脚本入口
  auth/             # 全局通用鉴权
  validators/       # 全局通用校验
  hyself_server/    # 项目配置（settings、urls、asgi、celery）
  hyself/           # 资源中心、文件上传、回收站
    auth/           # 资源中心专用鉴权
    utils/          # 资源中心专用工具
    validators/     # 资源中心专用校验
  chat/             # 聊天模块
    auth/           # 聊天专用鉴权
    validators/     # 聊天专用校验
  game/             # 游戏相关接口
    validators/     # 游戏专用校验
  user/             # 用户、角色、权限、认证
    auth/           # 用户与 RBAC 专用鉴权
    validators/     # 用户专用校验
  utils/            # 通用工具
  ws/               # WebSocket 鉴权、路由、事件、消费端
  docs/             # 设计文档与实现记录
  manage.py
```

## 相关文档

- docs/implementation_notes/update-2026-04-24.md

## 联调建议

1. 先确保 MySQL、Redis 已启动。
2. 配置 .env 并执行迁移。
3. 使用 uvicorn 启动 ASGI 服务，完整验证 REST 和 WebSocket。
4. 如需大文件联调，额外启动 Celery worker。
5. 再启动 hyself 前端进行联调。

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

- 回收站去重恢复会优先复用同 MD5 文件，并将恢复结果放到用户当前选择目录。
