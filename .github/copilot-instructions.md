
## 项目快速导览（对 AI 代理）

- 语言/框架：Python + FastAPI（异步 SQLAlchemy + Redis）
- 入口：`backend/app/main.py`（lifespan 中初始化 Redis 与 DB，使用 `uvicorn app.main:app` 启动）
- 主要模块：
  - `app/api/`：路由实现（例如 `auth.py`, `statistics.py`）
  - `app/core/`：核心工具与中间件（`config.py` 配置/错误码、`security.py` 认证/加密、`exception_handler.py` 统一错误输出、`log_middleware.py` 请求日志）
  - `app/db/`：数据库与 Redis 连接（见 `base.py`）
  - `app/models/` 与 `app/schemas/`：ORM 模型与 Pydantic 模式

## 关键设计与数据流要点（可直接利用）

- 启动顺序：lifespan 初始化 Redis（必须可连通，否则进程退出）并创建 DB 表（`Base.metadata.create_all`）。见 `app/main.py`。
- Token 流程（重要）：
  - Token 用 JWT（`app/core/security.py:create_access_token`）编码，`sub` 存放 `user_id`。
  - 同时在 Redis 中保存映射：`token:{token}` -> user_id，`user_token:{user_id}` -> token（过期以 settings.TOKEN_EXPIRE_TIME 控制，单位分钟，存储时乘以60）。
  - 获取当前用户的依赖：`auth.get_current_user` 会先检查 Redis，再解 JWT，最后从 DB 读取用户。
- 授权约定：管理员检查普遍通过 `current_user.is_admin`；患者使用手机号登录（`patient/login`），员工使用 `identifier`（工号）登录（`staff/login`）。
- 邮箱验证流程：注册后会向 `settings.YUN_URL + token` 发送包含邮箱验证链接的邮件；Redis 同时维护 `email_verify_token:{email}` 和 `email_verify_token_reverse:{token}`。

## 开发/运行须知（命令与依赖）

- 本地运行（在 `backend/` 目录）：
  - 安装依赖：`pip install -r requirements.txt`（已有 `backend/requirements.txt`）
  - 启动：`uvicorn app.main:app --reload`（或使用 Dockerfile 中的容器启动 uvicorn）
- 镜像构建：`backend/Dockerfile` 使用 `python:3.11-alpine` 并运行 `uvicorn app.main:app --host 0.0.0.0 --port 8000`。
- 环境变量：使用 `pydantic_settings.BaseSettings` 从 `.env` 读取（见 `app/core/config.py`），必需项包括：
  - `DATABASE_URL`（SQLAlchemy）
  - `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`
  - 邮件相关：`EMAIL_FROM`, `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `YUN_URL`
  - `SECRET_KEY`、`TOKEN_EXPIRE_TIME` 等
- 重要运行前检查：Redis 必须可达（app 启动会 ping Redis 并在失败时退出），DB 请确保 `DATABASE_URL` 指向可用数据库。

## 项目特有约定与示例（直接可用的片段）

- 统一返回格式：JSON 结构为 `{ code: int, message: ... }`，成功 `code=0`（定义见 `app/core/config.py` 和 `backend/README.md`）。示例：`{ "code":0, "message": {...} }`。
- 错误/权限处理：抛出 `AuthHTTPException`（或其他模块内定义的 HTTPException wrapper）以便 `register_exception_handlers` 统一格式化。
- 常用 Redis key：
  - 登录 Token：`token:{token}`、`user_token:{user_id}`
  - 邮箱验证：`email_verify_token:{email}`、`email_verify_token_reverse:{token}`
  - 密码修改临时缓存：`pwd_change:{user_id}`
- 提取用户 id 的兼容方式（header/cookie/query）：`app/core/security.py:get_user_id_from_request` 展示了从 Authorization header、cookie 或 query 参数中读取 token 的实现，可供异步任务/脚本复用。

## 编辑与扩展注意事项（对 AI 代理）

- 小心改动认证流程：`auth.get_current_user` 强依赖 Redis 中的 token 映射，若只改 JWT 验证而不维护 Redis 映射，会导致登录失效。
- 不要移除或静默更改 `settings` 中的错误码或 key 名称：许多地方以这些常量判定行为/返回码（见 `app/core/config.py` 与 `backend/README.md`）。
- 日志路径：默认写入 `logs/app.log`（`app/main.py`），修改路径时请确保容器/环境有写权限。

## 快速参考（便于复制使用）

- 启动（开发）：在 `backend/` 下

```
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- 得到 token 并调用受保护接口（示例）：

1. 获取 token（使用 `/auth/swagger-login` 或 `/auth/patient/login`）
2. 在请求头加入：`Authorization: Bearer <token>`

## 我需要你反馈的点

- 是否希望把更多文件（例如 `app/db/base.py`, `app/core/exception_handler.py` 等）作为“必须引用”的短示例包含进来？
- 是否要同时生成一个简短的 `DEVELOPER-notes.md` 来列出本地一键启动（docker-compose + redis + mysql 示例）？

请审阅以上说明，告诉我哪些部分需要更详细的示例或需要合并仓库中已有的文档内容。 
