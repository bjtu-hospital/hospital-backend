
# BJTU 校医院挂号系统（后端）

此仓库为后端服务（FastAPI + 异步 SQLAlchemy + Redis）。本 README 以中文编写，涵盖：如何本地运行、主要配置、认证/Token 流程、统一返回格式与错误码、以及目前实现的 API 列表与示例。

> 备注：README 以仓库当前实现为准（包含 `app/api/auth.py` 与 `app/api/admin.py` 中的路由）。如果你需要额外生成 API 文档（OpenAPI/Swagger），可以通过运行服务后访问 `/docs` 或 `/redoc`。

## 目录（快速导航）
- 环境与依赖
- 运行（开发 / Docker）
- 环境变量（.env）
- 认证与 Token（行为说明）
- 统一返回格式与常用错误码
- 主要 API 列表（示例请求与返回）
- 常见问题与排查

---

## 一、环境与依赖

- 语言/框架：Python 3.11、FastAPI、async SQLAlchemy、redis.asyncio
- 推荐在虚拟环境中运行（venv / conda）

安装依赖：

```pwsh
# 进入 backend 目录
cd backend
pip install -r requirements.txt
```

---

## 二、配置（.env）

在 `backend/` 下将 `.env.example` 复制为 `.env` 并填写：

目前邮箱相关无实际作用

- DATABASE_URL：SQLAlchemy 异步连接字符串（例如 postgresql+asyncpg://user:pass@host:5432/dbname）
- REDIS_HOST / REDIS_PORT / REDIS_PASSWORD
- SECRET_KEY：JWT 签名密钥
- TOKEN_EXPIRE_TIME：token 到期时间（分钟）
- 邮件发送相关：EMAIL_FROM、SMTP_SERVER、SMTP_PORT、SMTP_USER、SMTP_PASSWORD、YUN_URL（用于邮箱验证链接）

示例：

```
DATABASE_URL=postgresql+asyncpg://postgres:password@127.0.0.1:5432/hospital
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=
SECRET_KEY=your-secret-here
TOKEN_EXPIRE_TIME=60
EMAIL_FROM=no-reply@example.com
SMTP_SERVER=smtp.example.com
SMTP_PORT=587
SMTP_USER=smtp_user
SMTP_PASSWORD=smtp_password
YUN_URL=https://yun.example.com/verify?token=
```

---

## 三、本地运行（开发）

在 `backend/` 目录下：

```pwsh
# 在 backend 根目录下
pip install -r requirements.txt
# 启动（带热重载）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

启动后：
- Swagger UI: http://127.0.0.1:8000/docs
- Redoc: http://127.0.0.1:8000/redoc

注意：应用在 Lifespan 阶段会尝试连接 Redis（若不可达，应用会在启动时报错并退出），请确保 Redis 可用，且 `DATABASE_URL` 指向可用数据库。

---

## 四、容器运行（Docker）

项目含 `backend/Dockerfile`。若想构建镜像并运行，可在 `backend/` 下执行构建与运行（示例）：

```pwsh
# 构建镜像
docker build -t bjtu-hospital-backend:latest -f backend/Dockerfile backend
# 运行（示例：将环境变量从主机注入/挂载 .env）
docker run -d --name hospital-backend -p 8000:8000 --env-file backend/.env bjtu-hospital-backend:latest
```

建议使用 docker-compose 编排数据库、redis 与后端服务以便调试。

---

## 五、认证 / Token 流程（实现细节）

- 后端使用 JWT（见 `app/core/security.py:create_access_token`），JWT 中的 `sub` 字段存放 `user_id`。
- 同时在 Redis 中保存映射，保证可撤销/提前失效：
    - `token:{token}` -> user_id
    - `user_token:{user_id}` -> token
    存储过期时间以 settings.TOKEN_EXPIRE_TIME（分钟）为准（存储时乘以 60）。
- 获取当前用户的依赖 `auth.get_current_user` 会先尝试通过 Redis 校验 token（提高撤销能力），若 Redis 中无映射则回退到解 JWT 并从 DB 读取用户。

因此：
- 注销时会删除 Redis 中的两个 key，确保 token 被立即失效。
- 在对用户做“软删除”时（例如删除医生关联的 user），代码也会尝试清理 Redis 中的 token 映射，避免被删除用户继续使用旧 token。

---

## 六、统一返回格式与常用错误码

所有接口返回结构统一为：

```json
{ "code": int, "message": object | list | str }
```

常用错误码（项目中定义，部分示例）：

- SUCCESS_CODE = 0
- REQ_ERROR_CODE = 99 （请求参数错误）
- REGISTER_FAILED_CODE = 100
- LOGIN_FAILED_CODE = 101
- INSUFFICIENT_AUTHORITY_CODE = 102
- TOKEN_INVALID_CODE = 105
- DATA_GET_FAILED_CODE = 106

错误时 `code` 会是非 0 值，`message` 一般包含 `error` / `msg` 或具体提示。

---

## 七、主要 API 列表（概要）

下面列出仓库中实现的主要路由与用途（简要说明与示例）。对于每个路由，启动服务后可在 Swagger 中查看更详细的请求/响应模式。

注意：所有受保护接口均需在请求头中添加 `Authorization: Bearer <token>`。

- Auth（认证）相关（路径前缀：`/auth`）
    - POST `/auth/swagger-login`（用于 Swagger OAuth2 登录）
        - 表单登陆（OAuth2PasswordRequestForm），返回 access_token。
    - POST `/auth/patient/login`：患者手机号登录，返回 token
    - POST `/auth/staff/login`：员工（工号）登录，返回 token
    - POST `/auth/register`：注册（患者/员工注册流程视具体实现）
    - POST `/auth/logout`：注销（删除 Redis 中 token 映射）
    - GET `/auth/me`：获取当前用户信息
    - 其它用户管理接口（部分在 `auth.py` 中实现，管理员可管理用户）

- 管理员（admin）相关（路径前缀视 `main.py` 注册，通常单独路由，例如 `/admin` 或直接根下）
    - 大科室（MajorDepartment）
        - POST `/major-departments`：创建大科室（仅管理员）
        - GET `/major-departments`：获取大科室列表
        - PUT `/major-departments/{dept_id}`：更新
        - DELETE `/major-departments/{dept_id}`：删除（若存在小科室依赖则拒绝）

    - 小科室（MinorDepartment）
        - POST `/minor-departments`：创建小科室（需指定所属大科室 id）
        - GET `/minor-departments`：获取小科室列表（可按大科室过滤）
        - PUT `/minor-departments/{minor_dept_id}`：更新（支持将小科室转移到另一个大科室）
        - DELETE `/minor-departments/{minor_dept_id}`：删除（若存在关联医生则拒绝）

    - 医生（Doctor）管理
        - POST `/doctors`：创建医生档案（可选同时在请求中提供 `identifier` 与 `password` 来一并创建用户账号并关联）
            - 如果提供 `identifier` 与 `password`，会在同一事务中创建 `User` 并将 `doctor.user_id` 关联到新用户；若只提供 `identifier` 而未提供 `password` 会返回错误。
        - GET `/doctors`：获取医生列表（可按科室过滤）
        - PUT `/doctors/{doctor_id}`：更新医生信息
        - DELETE `/doctors/{doctor_id}`：删除医生。实现说明：若医生有关联的 `User`，会对该 User 做“懒删除（软删除）`is_deleted=True` 并 `is_active=False`”，同时尝试清理 Redis 中该用户的 token 映射，然后解除关联并删除医生记录。
        - POST `/doctors/{doctor_id}/create-account`：单独为医生创建账号并关联（管理员操作）
        - PUT `/doctors/{doctor_id}/transfer`：将医生调到另一个小科室（管理员）

更多细节请在启动后访问 Swagger（`/docs`）查看每个接口的 request/response model。

---

## 八、示例请求（PowerShell / curl）

1) 使用 Swagger 表单登录（示例在 Swagger UI）：

2) 使用 curl 获取受保护数据（示例）：

```pwsh
# 假设已得到 token
$token = "<your_token>"
curl -H "Authorization: Bearer $token" http://127.0.0.1:8000/auth/me
```

3) 创建大科室（管理员）：

```pwsh
curl -X POST http://127.0.0.1:8000/major-departments \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{"name":"内科","description":"内科相关"}'
```

4) 创建医生并同时创建账号（管理员），示例 JSON：

```pwsh
curl -X POST http://127.0.0.1:8000/doctors \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
        "dept_id": 1,
        "name": "张三",
        "title": "主治医师",
        "identifier": "doc1001",
        "password": "StrongP@ssw0rd",
        "email": "zhangsan@example.com",
        "phonenumber": "13800000000"
}'
```

---

## 九、常见问题与排查

- 启动报错：Redis 连接失败 → 请检查 `.env` 中的 `REDIS_HOST`/`REDIS_PORT` 是否正确，Redis 服务是否启动。
- 启动报错：数据库连接/迁移错误 → 请确保 `DATABASE_URL` 正确且数据库可达；可先用 CLI 测试连接。
- 登录后访问受保护接口返回 Token 无效 → 检查请求头 `Authorization: Bearer <token>` 是否正确；检查 Redis 中是否存在 `token:{token}` 与 `user_token:{user_id}`（可能被清理或过期）。

---

## 十、开发建议 / 后续改进

- 添加自动化测试（单元/集成），覆盖认证、医生创建/删除（包含 soft-delete 行为）与科室迁移等关键路径。
- 补充 API 文档（可导出 OpenAPI JSON 并生成客户端 SDK）。
- 在软删除用户时，是否需要同时清理敏感字段（如 `hashed_password`、email）或保留以支持恢复？请在业务上确认。
- 建议写一个 `docker-compose.yml` 将 Postgres/MariaDB、Redis 与后端服务组合，方便本地一键启动测试环境。

---

如果你想，我可以：
- 生成一个 `docker-compose.yml` 示例来快速本地启动依赖；
- 为 `DELETE /doctors/{doctor_id}` 的 soft-delete 行为添加集成测试（需要可用测试 DB）；
- 或把 `README` 中的 API 列表自动从代码生成（解析路由）以保证文档与实现同步。

---

更新说明：本次我将根 README 扩充为更全面的项目指南，包含运行步骤、配置与目前实现的主要 API 概要与示例请求。如需我把每个接口的请求/返回完整示例列入 README（逐个字段），我可以继续扫描代码并补全详细示例。


# 统一异常错误输出

```
#错误码
UNKNOWN_ERROR_CODE: int = 97 #未知错误
HTTP_ERROR_CODE: int = 98 #HTTP错误
REQ_ERROR_CODE: int = 99 #请求参数错误
REGISTER_FAILED_CODE: int = 100 #注册失败
LOGIN_FAILED_CODE: int = 101 #登入失败
INSUFFICIENT_AUTHORITY_CODE: int = 102 #权限不足
USER_GET_FAILED_CODE: int = 103 #用户获取失败
UPDATEPROFILE_FAILED_CODE: int = 104 #用户个人信息更新失败
TOKEN_INVALID_CODE: int = 105 #Token失效
DATA_GET_FAILED_CODE: int = 106 #交通数据获取失败
SUCCESS_CODE: int = 0 #成功
```


```
{
    code: int,
    message: object | list | str
}
```

错误示例:
```
{
    "code": 102,
    "message": {
        "error": "权限不足",
        "msg": "无权限"
    }
}
```


# 一、auth接口: /auth

## 1. 注册接口 Post: `/auth/register`

### 输入：JSON格式的注册数据

```
{
    "email": "user@example.com",
    "username": "testuser",
    "phonenumber": "+86 12345678901",
    "password": "securepass"
}
```

#### 输出:
```
{
    "code": 0,
    "message": {
        "detail": "注册成功，请前往邮箱验证"
    }
}
```


## 2. 登入接口 Post: `/auth/login`

### 输入：
```
{
    "username": "huashen",
    "password": "123456"
}
```

#### 输出:
```
{
    "code": 0,
    "message": {
        "userid": 1,
        "access_token": "...",
        "token_type": "Bearer"
    }
}
```


## 3. 获取所有用户信息 (仅管理员) Get: `/auth/users`

### Header:
```
Authorization: Bearer <token>
```

#### 输出:
```
{
    "code": 0,
    "message": [
        {
            "username": "huashen",
            "email": "1137746306sssss@qq.com",
            "phonenumber": "18279073254",
            "userid": 1,
            "is_admin": true
        },
        ...
    ]
}
```


## 4. 获取单个用户信息 (管理员可查所有, 普通用户只能查自己) Get: `/auth/users/{user_id}`

### Header:
```
Authorization: Bearer <token>
```

#### 输出:
```
{
    "code": 0,
    "message": {
        "username": "wyq",
        "email": "1903910367@qq.com",
        "phonenumber": "",
        "userid": 33,
        "is_admin": false
    }
}
```


## 5. 修改用户信息 Put: `/auth/users/{user_id}/updateProfile`

### Header:
```
Authorization: Bearer <token>
```

### Body (全部参数可选):
```
{
    "username": "newname",
    "email": "newemail@example.com",
    "phonenumber": "12345678901"
}
```

#### 权限:
- 管理员可修改所有用户
- 普通用户只能修改自己的信息

#### 输出:
```
{
    "code": 0,
    "message": {
        "user": {
            "username": "huashen",
            "email": "2937746306@qq.com",
            "phonenumber": "18279073253",
            "userid": 1,
            "is_admin": true
        }
    }
}
```


## 6. 删除用户 Delete: `/auth/users/{user_id}`

### Header:
```
Authorization: Bearer <token>
```

#### 权限:
- 仅管理员可删除用户，且管理员不能删除其他管理员

#### 输出:
```
{
    "code": 0,
    "message": {
        "detail": "成功删除用户huashen"
    }
}
```


## 7. 获取当前用户角色 Get: `/auth/me`

### Header:
```
Authorization: Bearer <token>
```

#### 输出:
```
{
    "code": 0,
    "message": {
        "role": "admin"
    }
}
```
