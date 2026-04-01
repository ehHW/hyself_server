# bbot_server 后端

基于 Django + DRF + Channels + JWT 的服务端，提供认证鉴权、用户/角色/权限管理、文件管理与上传相关接口。

## 技术栈

- Python
- Django 5.2
- Django REST Framework
- SimpleJWT
- Django Channels + channels-redis
- Celery + Redis
- MySQL（默认配置）

## 运行环境

- Python: `>=3.14`（以 `pyproject.toml` 为准）
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

项目通过 `.env` 加载配置。常用变量如下：

```env
DEBUG=True
SECRET_KEY=请替换为你自己的密钥

DATABASE_HOST=localhost
DATABASE_PORT=3306
DATABASE_NAME=bbot_server
DATABASE_USER=root
DATABASE_PASSWORD=your_password

# 可选
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

可选：创建管理后台账号

```bash
python manage.py createsuperuser
```

## 启动服务

### 方式一：Django 开发服务

```bash
python manage.py runserver 127.0.0.1:8000
```

### 方式二：ASGI（推荐用于 websocket 调试）

```bash
uvicorn bbot_server.asgi:application --host 127.0.0.1 --port 8000 --reload --lifespan off
```

## Celery（可选）

如果你使用了异步任务，另开终端启动 worker：

```bash
celery -A bbot_server worker -l info
celery -A bbot_server worker -l info --pool=solo -c 1
```

Windows 推荐（避免 billiard 权限问题）：

```bash
celery -A bbot_server worker -l info --pool=solo -c 1
```

## 定时任务（Cron Jobs）

项目级定时任务统一放在 `bbot_server/cron_jobs/`。

已提供回收站清理任务：

```bash
python manage.py cleanup_recycle_bin
```

建议每天凌晨执行一次。

Linux crontab 示例（每天 00:00）：

```bash
0 0 * * * cd /path/to/bbot_server && /path/to/python manage.py cleanup_recycle_bin
```

## 接口路由（简要）

- `/api/auth/*`：登录、刷新、用户信息
- `/api/users|roles|permissions`：用户角色权限管理
- `/api/upload/*`：文件列表、上传、分片、合并、重命名、删除
- `/uploads/*`：媒体文件访问（DEBUG 模式）

## 目录结构（简要）

```text
bbot_server/
  bbot_server/     # 项目配置（settings、urls、asgi、celery）
  user/            # 用户/角色/权限与认证
  bbot/            # 文件管理与上传业务
  manage.py
```

## 联调建议

1. 确保 MySQL、Redis 已启动。
2. 完成 `.env` 配置并执行迁移。
3. 启动后端服务（8000 端口）。
4. 再启动前端进行联调。
