
# BJTU 校医院挂号系统（后端）


# BJTU 校医院挂号系统（后端）

此仓库为后端服务（FastAPI + 异步 SQLAlchemy + Redis）。本 README 以中文编写，涵盖：统一错误码与格式、主要 API 参考，以及如何部署和维护。

> 备注：README 以仓库当前实现为准（包含 `app/api/auth.py` 与 `app/api/admin.py` 中的路由）。如果你需要额外生成 API 文档（OpenAPI/Swagger），可以通过运行服务后访问 `/docs`。

## 目录（快速导航）
- 环境准备与依赖
- 部署指南（本地/Docker）
- 常见问题与排查
- 统一错误码与格式
- API 权限与标准响应
- 认证 API 接口（`/auth`）
- 管理员 API 接口（`/admin`）

---

# 开发环境与部署

## 一、环境与依赖

- 语言/框架：Python 3.11、FastAPI、async SQLAlchemy、redis.asyncio
- 推荐在虚拟环境中运行（venv / conda）

安装依赖：

```pwsh
# 进入 backend 目录
cd backend
pip install -r requirements.txt
```

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

## 三、本地运行（开发）

在 `backend/` 目录下：

```pwsh
# 在 backend 根目录下
pip install -r requirements.txt
# 启动（带热重载）
uvicorn app.main:app --reload
```

启动后：
- Swagger UI: http://127.0.0.1:8000/docs
- Redoc: http://127.0.0.1:8000/redoc

注意：应用在 Lifespan 阶段会尝试连接 Redis（若不可达，应用会在启动时报错并退出），请确保 Redis 可用，且 `DATABASE_URL` 指向可用数据库。

## 四、容器运行（Docker）

项目含 `backend/Dockerfile`。若想构建镜像并运行，可在 `backend/` 下执行构建与运行（示例）：

```pwsh
# 构建镜像
docker build -t bjtu-hospital-backend:latest -f backend/Dockerfile backend
# 运行（示例：将环境变量从主机注入/挂载 .env）
docker run -d --name hospital-backend -p 8000:8000 --env-file backend/.env bjtu-hospital-backend:latest
```

建议使用 docker-compose 编排数据库、redis 与后端服务以便调试。

## 五、常见问题与排查

- 启动报错：Redis 连接失败 → 请检查 `.env` 中的 `REDIS_HOST`/`REDIS_PORT` 是否正确，Redis 服务是否启动。
- 启动报错：数据库连接/迁移错误 → 请确保 `DATABASE_URL` 正确且数据库可达；可先用 CLI 测试连接。
- 登录后访问受保护接口返回 Token 无效 → 检查请求头 `Authorization: Bearer <token>` 是否正确；检查 Redis 中是否存在 `token:{token}` 与 `user_token:{user_id}`（可能被清理或过期）。

---

# API 参考文档

## 一、统一错误码与格式

所有 API 响应均使用统一的 JSON 结构：
```json
{
    "code": int,
    "message": object | list | str
}
```

其中 `code` 为 0 表示成功，非 0 表示错误。错误时 `message` 通常包含 `error` 与 `msg` 说明。

## 标准错误码定义

```python
SUCCESS_CODE: int = 0            # 成功
UNKNOWN_ERROR_CODE: int = 97     # 未知错误
HTTP_ERROR_CODE: int = 98        # HTTP错误
REQ_ERROR_CODE: int = 99         # 请求参数错误
REGISTER_FAILED_CODE: int = 100  # 注册失败
LOGIN_FAILED_CODE: int = 101     # 登入失败
INSUFFICIENT_AUTHORITY_CODE: int = 102  # 权限不足
USER_GET_FAILED_CODE: int = 103  # 用户获取失败
UPDATEPROFILE_FAILED_CODE: int = 104    # 用户个人信息更新失败
TOKEN_INVALID_CODE: int = 105    # Token失效
DATA_GET_FAILED_CODE: int = 106  # 数据获取失败
```

错误响应示例：
```json
{
    "code": 102,
    "message": {
        "error": "权限不足",
        "msg": "无权限"
    }
}
```

## 异常分类与处理

项目使用全局异常处理器统一格式化以下类型的异常：

- **AuthHTTPException**: 认证/鉴权相关错误（token 无效、权限不足等）
- **BusinessHTTPException**: 业务规则或参数校验失败
- **ResourceHTTPException**: 资源或 IO 相关错误（文件不存在、数据库记录未找到等）

这些异常会被转换为统一格式：
```json
{
    "code": "<对应错误码>",
    "message": {
        "error": "<分类描述>",
        "msg": "<详细信息>"
    }
}
```

---

# 开发环境与部署

## 一、环境与依赖

Python后端服务：
- Python 3.11
- FastAPI
- async SQLAlchemy
- redis.asyncio

数据库与缓存：
- PostgreSQL/MySQL（可通过 SQLAlchemy URL 配置）
- Redis（用于 token 管理与邮箱验证）

推荐使用虚拟环境（venv/conda），安装依赖：

```pwsh
# 进入 backend 目录
cd backend
pip install -r requirements.txt
```

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
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
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
    - 院区（HospitalArea）
        - GET `/hospital-areas`：获取全部院区列表（仅管理员）
        - GET `/hospital-areas?area_id={id}`：根据院区ID获取单个院区信息（仅管理员）

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


---

# API 接口详细说明

## 一、认证 API 接口 `/auth`

## 1. 注册接口 Post: `/auth/register`

### 输入：JSON格式的注册数据
```json
{
    "email": "user@example.com",
    "username": "testuser",
    "phonenumber": "+86 12345678901",
    "password": "securepass"
}
```

### 输出:
```json
{
    "code": 0,
    "message": {
        "detail": "注册成功，请前往邮箱验证"
    }
}
```

## 2. 登录接口

### 患者登录 Post: `/auth/patient/login`
使用手机号登录

### 员工登录 Post: `/auth/staff/login`
使用工号（identifier）登录

### Swagger OAuth2 登录 Post: `/auth/swagger-login`
用于 Swagger UI 中的表单登录测试

### 请求示例：
```json
{
    "username": "huashen",  // 或手机号/工号
    "password": "123456"
}
```

### 响应示例:
```json
{
    "code": 0,
    "message": {
        "userid": 1,
        "access_token": "...",
        "token_type": "Bearer"
    }
}
```

## 3. 用户管理接口

### 获取所有用户 Get: `/auth/users`
仅管理员可用

#### 响应示例:
```json
{
    "code": 0,
    "message": [
        {
            "username": "huashen",
            "email": "1137746306sssss@qq.com",
            "phonenumber": "18279073254",
            "userid": 1,
            "is_admin": true
        }
    ]
}
```

### 获取单个用户 Get: `/auth/users/{user_id}`
管理员可查所有用户，普通用户只能查看自己

### 更新用户信息 Put: `/auth/users/{user_id}/updateProfile`
管理员可改所有，普通用户只能改自己

#### 请求示例：
```json
{
    "username": "newname",
    "email": "newemail@example.com",
    "phonenumber": "12345678901"
}
```

### 删除用户 Delete: `/auth/users/{user_id}`
仅管理员可用，且不能删除其他管理员

### 获取当前用户 Get: `/auth/me`

#### 响应示例:
```json
{
    "code": 0,
    "message": {
        "role": "admin"
    }
}
```

---

## 8. 管理员注册（开发/运维） Post: `/auth/register-admin`
### 用途
用于创建管理员账户。逻辑如下：
- 如果系统尚无 Administrator 记录（首次引导），允许无认证创建第一个管理员（bootstrap）。
- 否则，仅允许当前已认证且 `is_admin==True` 的用户创建新管理员。

### 请求参数（表单 / JSON 均可，示例为字段说明）

- `identifier` (string) — 工号，用于登录（必填）
- `password` (string) — 登录密码（必填）
- `name` (string) — 管理员姓名（必填）
- `email` (string, optional) — 邮箱（可选，但若提供必须唯一）
- `job_title` (string, optional) — 职位

### 权限与行为
- 首次引导（无 Administrator 记录）时允许匿名调用以创建第一个管理员。
- 若已有管理员存在，则调用者必须携带有效 token，并且 `current_user.is_admin == True`，否则返回权限不足错误（HTTP 403，框架中抛出 `AuthHTTPException`，错误码为 `INSUFFICIENT_AUTHORITY_CODE`）。
- 会校验 `identifier` 与 `email` 的唯一性，若冲突返回业务错误（400，`REGISTER_FAILED_CODE`）。

### 成功响应示例
```json
{
    "code": 0,
    "message": {
        "detail": "成功创建管理员 张三"
    }
}
```

### 可能的错误示例
- 权限不足（已有管理员但当前请求者非管理员）
```json
{
    "code": 102,
    "message": {
        "error": "权限不足",
        "msg": "仅管理员可创建新管理员"
    }
}
```
- 注册失败（identifier 或 email 冲突）
```json
{
    "code": 100,
    "message": {
        "error": "注册失败",
        "msg": "该工号(identifier)已被占用"
    }
}
``` 

---

---

# 二、管理员 API 接口

以下接口都需要：
1. 管理员权限（`current_user.is_admin == true`）
2. 请求头：`Authorization: Bearer <token>`

## 1. 分级价格配置管理

系统实现了四级价格配置体系：**GLOBAL（全局）→ MINOR_DEPT（小科室）→ CLINIC（诊室）→ DOCTOR（医生）**

### 核心特性

1. **分级查询优先级**：创建/更新排班时，若 `price <= 0`，系统按 DOCTOR → CLINIC → MINOR_DEPT → GLOBAL 顺序查询价格配置
2. **灵活配置**：每个层级可独立配置三种号源价格（普通号、专家号、特需号），未配置则继承上级
3. **null 语义**：配置值为 `null` 表示该层级不设置该类型价格，继续向上级查找

### 1.1 获取全局价格配置
- GET `/admin/global-prices`

响应：
```json
{
    "code": 0,
    "message": {
        "default_price_normal": 50.00,
        "default_price_expert": 100.00,
        "default_price_special": 500.00
    }
}
```

### 1.2 更新全局价格配置
- PUT `/admin/global-prices`

请求参数（Query Parameters，至少提供一个）：
- `default_price_normal` (float, optional): 普通号默认价格
- `default_price_expert` (float, optional): 专家号默认价格
- `default_price_special` (float, optional): 特需号默认价格

请求示例：
```
PUT /admin/global-prices?default_price_normal=60&default_price_expert=120
```

响应：
```json
{
    "code": 0,
    "message": {
        "detail": "全局价格配置更新成功"
    }
}
```

---

## 2. 科室管理

### A. 大科室管理

#### 2.1 创建大科室
- POST `/major-departments`

请求体：
```json
{
    "name": "内科",
    "description": "内科相关"
}
```

响应：
```json
{
    "code": 0,
    "message": {
        "detail": "成功创建大科室：内科"
    }
}
```

#### 2.2 获取大科室列表
- GET `/major-departments`

响应：
```json
{
    "code": 0,
    "message": {
        "departments": [
            {
                "major_dept_id": 1,
                "name": "内科",
                "description": "内科相关",
                "create_time": "2024-01-01T10:00:00"
            },
            // ... 其他大科室
        ]
    }
}
```

#### 2.3 更新大科室
- PUT `/major-departments/{dept_id}`

请求体：
```json
{
    "name": "内科（更新）",
    "description": "内科相关科室"
}
```

响应：
```json
{
    "code": 0,
    "message": {
        "detail": "成功更新大科室信息"
    }
}
```

#### 2.4 删除大科室
- DELETE `/major-departments/{dept_id}`
- 注意：若存在关联的小科室，则不允许删除

响应：
```json
{
    "code": 0,
    "message": {
        "detail": "成功删除大科室"
    }
}
```

### B. 小科室管理

#### 2.5 创建小科室
- POST `/minor-departments`

请求体（**支持价格配置**）：
```json
{
    "major_dept_id": 1,
    "name": "心内科",
    "description": "心脏内科",
    "default_price_normal": 60.00,     // 可选：普通号价格
    "default_price_expert": null,      // 可选：null表示不设置，继承上级
    "default_price_special": 550.00    // 可选：特需号价格
}
```

响应（包含价格信息）：
```json
{
    "code": 0,
    "message": {
        "minor_dept_id": 101,
        "major_dept_id": 1,
        "name": "心内科",
        "description": "心脏内科",
        "default_price_normal": 60.00,
        "default_price_expert": null,
        "default_price_special": 550.00
    }
}
```

#### 2.6 获取小科室列表
- GET `/minor-departments?major_dept_id={major_dept_id}`
- 参数 `major_dept_id` 可选，用于按大科室过滤

响应（**包含价格信息**）：
```json
{
    "code": 0,
    "message": {
        "departments": [
            {
                "minor_dept_id": 1,
                "major_dept_id": 1,
                "name": "心内科",
                "description": "心脏内科",
                "default_price_normal": 60.00,
                "default_price_expert": null,
                "default_price_special": 550.00,
                "create_time": "2024-01-01T10:00:00"
            },
            // ... 其他小科室
        ]
    }
}
```

#### 2.7 更新小科室
- PUT `/minor-departments/{minor_dept_id}`

请求体（**支持更新价格配置**）：
```json
{
    "major_dept_id": 1,  // 可选，用于调整所属大科室
    "name": "心内科（更新）",
    "description": "心脏内科相关",
    "default_price_normal": 65.00,     // 可选：更新普通号价格
    "default_price_expert": 150.00,    // 可选：更新专家号价格
    "default_price_special": null      // 可选：设置为null取消该类型价格
}
```

响应（包含价格信息）：
```json
{
    "code": 0,
    "message": {
        "minor_dept_id": 101,
        "major_dept_id": 1,
        "name": "心内科（更新）",
        "description": "心脏内科相关",
        "default_price_normal": 65.00,
        "default_price_expert": 150.00,
        "default_price_special": null
    }
}
```

#### 2.8 删除小科室
- DELETE `/minor-departments/{minor_dept_id}`
- 注意：若存在关联的医生，则不允许删除
 - 同时会删除该小科室的价格配置（system_config: scope_type=MINOR_DEPT, config_key=registration.price）

响应：
```json
{
    "code": 0,
    "message": {
        "detail": "成功删除小科室 心内科"
    }
}
```

## 2.9 园区相关接口

### 2.9.1 获取全部园区信息
- GET `/hospital-areas`
- 权限：仅管理员

**响应示例**：
```json
{
    "code": 0,
    "message": {
        "areas": [
            {
                "area_id": 1,
                "name": "东院区",
                "destination": "北京市海淀区上园村3号",
                "create_time": "2024-01-01T00:00:00"
            },
            {
                "area_id": 2,
                "name": "西院区",
                "destination": "北京市海淀区西土城路10号",
                "create_time": "2024-01-01T00:00:00"
            }
        ]
    }
}
```

### 2.9.2 根据园区ID获取单个园区
- GET `/hospital-areas?area_id={id}`
- 权限：仅管理员

**请求参数**：
- `area_id`（可选）：院区ID，如果提供则返回该院区信息

**请求示例**：
```
GET /hospital-areas?area_id=1
```

**响应示例**：
```json
{
    "code": 0,
    "message": {
        "areas": [
            {
                "area_id": 1,
                "name": "东院区",
                "destination": "北京市海淀区上园村3号",
                "create_time": "2024-01-01T00:00:00"
            }
        ]
    }
}
```

## 2.10 排班爬虫数据导入接口

用于将爬虫流程生成的 `all.json` 文件内容导入到系统的院区 / 门诊 / 排班表，或直接触发完整的爬虫+导入流程。

### 2.10.1 完整爬虫流程（一键执行）
- POST `/crawler/schedules/run`
- 权限：仅管理员

**功能**：自动执行完整流程：爬取医院官网排班 → 合并数据 → 导入数据库

**请求参数**：
- `skip_crawl`（可选，bool）：是否跳过爬虫步骤，直接使用已有 all.json（默认 false）

**流程说明**：
1. 从 `final/crawler_data.json` 读取医生基础信息列表
2. 并发爬取所有医生的排班数据（异步 HTTP 请求）
3. 保存到 `schedule/年份i周/` 目录
4. 合并所有 JSON 文件为 `all.json`
5. 解析并导入数据库：
   - 创建/匹配院区（幂等）
   - 创建/匹配门诊（幂等）
   - 根据医生姓名匹配已有医生记录
   - 插入新排班或更新已有排班（避免重复）

**请求示例**：
```pwsh
# 完整流程（爬取+导入）
curl -X POST "http://127.0.0.1:8000/crawler/schedules/run" -H "Authorization: Bearer <token>"

# 跳过爬虫，仅导入已有数据
curl -X POST "http://127.0.0.1:8000/crawler/schedules/run?skip_crawl=true" -H "Authorization: Bearer <token>"
```

**响应示例**：
```json
{
  "code": 0,
  "message": {
    "crawl_stats": {
      "success": 245,
      "total": 250,
      "output_dir": "schedule/2025年47周(11.17-11.23)"
    },
    "merge_count": 245,
    "import_stats": {
      "areas_created": 0,
      "clinics_created": 0,
      "schedules_inserted": 456,
      "schedules_updated": 123,
      "schedules_skipped_no_doctor": 12,
      "schedules_skipped_duplicate": 5
    }
  }
}
```

### 3.1 创建医生
- POST `/doctors`

请求体（**支持价格配置**，可选是否同时创建账号）：
```json
{
    "dept_id": 1,
    "name": "张三",
    "title": "主治医师",
    "specialty": "心血管疾病",
    "introduction": "从事心血管疾病临床工作多年...",
    "identifier": "doc1001",  // 可选，工号（若要创建账号）
    "password": "StrongP@ss", // 可选，密码（若要创建账号）
    "email": "zhangsan@example.com",
    "phonenumber": "13800000000",
    "default_price_normal": 80.00,     // 可选：普通号价格
    "default_price_expert": null,      // 可选：null表示不设置，继承上级
    "default_price_special": 888.00    // 可选：特需号价格
}
```

响应（包含价格信息）：
```json
{
    "code": 0,
    "message": {
        "doctor_id": 1,
        "dept_id": 1,
        "name": "张三",
        "title": "主治医师",
        "specialty": "心血管疾病",
        "introduction": "从事心血管疾病临床工作多年...",
        "default_price_normal": 80.00,
        "default_price_expert": null,
        "default_price_special": 888.00,
        "account_provided": false
    }
}
```

### 3.2 获取医生列表
- GET `/doctors?dept_id={dept_id}&name={name}`
- 参数：
  - `dept_id` (可选)：按科室 ID 过滤
  - `name` (可选)：按医生姓名模糊搜索

请求示例：
```
GET /doctors?name=张
GET /doctors?dept_id=1&name=王
```

响应（**包含价格信息**）：
```json
{
    "code": 0,
    "message": {
        "doctors": [
            {
                "doctor_id": 1,
                "user_id": 10,
                "dept_id": 1,
                "name": "张三",
                "title": "主治医师",
                "specialty": "心血管疾病",
                "introduction": "从事心血管疾病临床工作多年...",
                "photo_path": null,
                "original_photo_url": null,
                "is_registered": true,
                "default_price_normal": 80.00,
                "default_price_expert": null,
                "default_price_special": 888.00,
                "create_time": "2024-01-01T10:00:00"
            },
            // ... 其他医生
        ]
    }
}
```

字段说明：
- `is_registered`：布尔值，表示该医生是否已在系统中有可用的用户账号。严格判定规则为：
  1) `doctor.user_id` 不为空且能在 `User` 表中找到对应记录；
  2) 对应的 `User.is_active` 为 True；
  3) 对应的 `User.is_deleted` 为 False（即未被软删除）。
- `default_price_normal/expert/special`：三种号源的价格配置，null 表示该层级未配置

示例中 `is_registered: true` 表示张三已有激活且未删除的用户账号；若医生档案存在但未创建账号或账号被停用/删除，则该字段为 `false`。

### 3.3 更新医生信息
- PUT `/doctors/{doctor_id}`

请求体（**支持更新价格配置**，所有字段可选）：
```json
{
    "name": "张三（更新）",
    "title": "副主任医师",
    "specialty": "心血管疾病，高血压",
    "introduction": "更新的简介...",
    "default_price_normal": 90.00,
    "default_price_expert": 200.00,
    "default_price_special": 999.00
}
```

响应（包含价格信息）：
```json
{
    "code": 0,
    "message": {
        "doctor_id": 1,
        "dept_id": 1,
        "name": "张三（更新）",
        "title": "副主任医师",
        "specialty": "心血管疾病，高血压",
        "introduction": "更新的简介...",
        "photo_path": null,
        "original_photo_url": null,
        "default_price_normal": 90.00,
        "default_price_expert": 200.00,
        "default_price_special": 999.00
    }
}
```

### 3.4 删除医生
- DELETE `/doctors/{doctor_id}`
- 说明：如果医生有关联的用户账号，会执行以下操作：
  1. 将用户标记为已删除（`is_deleted=True`）
  2. 停用账号（`is_active=False`）
  3. 清理 Redis 中的 token 映射
  4. 解除医生-用户关联并删除医生记录
    5. 同时删除该医生的价格配置（system_config: scope_type=DOCTOR, config_key=registration.price）

响应：
```json
{
    "code": 0,
    "message": {
        "detail": "成功删除医生 张三"
    }
}
```

### 3.5 为医生创建账号
- POST `/doctors/{doctor_id}/create-account`
- 说明：为已有的医生记录创建关联的用户账号

请求体：
```json
{
    "identifier": "doc1001",  // 工号作为登录用户名
    "password": "StrongP@ss",
    "email": "doctor@example.com",  // 可选
    "phonenumber": "13800000000"    // 可选
}
```

响应：
```json
{
    "code": 0,
    "message": {
        "detail": "成功为医生创建账号",
        "user_id": 10,
        "doctor_id": 1
    }
}
```

### 3.6 医生调科室
- PUT `/doctors/{doctor_id}/transfer`
- 说明：将医生调到新的科室

请求体：
```json
{
    "new_dept_id": 2  // 新科室ID
}
```

响应示例：
```json
{
    "code": 0,
    "message": {
        "detail": "成功将医生 张三 调至新科室",
        "doctor_id": 1,
        "old_dept_id": 1,
        "new_dept_id": 2
    }
}
```

### 3.7 医生照片上传
- POST `/doctors/{doctor_id}/photo`
- 说明：管理员为医生上传照片（multipart/form-data）。接口会将文件异步保存到 `app/static/image/`，并在数据库中更新 `Doctor.photo_path`（内部访问路径，如 `/static/image/<filename>`）以及 `Doctor.original_photo_url`（若来源为外部 URL，可保留）。

请求：Content-Type: multipart/form-data，字段名 `file`（文件）

响应示例：
```json
{
    "code": 0,
    "message": {
        "photo_path": "/static/image/doctor_12345_20251030.jpg",
        "original_photo_url": null
    }
}
```

注意：
- 上传接口会对文件写入进行异常分类，如果写文件失败或磁盘问题，会被标记为资源错误并上抛 `ResourceHTTPException`（由全局异常处理器统一响应）；如果请求参数不满足业务规则会抛 `BusinessHTTPException`。

### 3.8 医生照片删除
- DELETE `/doctors/{doctor_id}/photo`
- 说明：删除医生已上传的本地照片（清理 `app/static/image` 中对应文件，并将 `Doctor.photo_path` 设为 `None`/空）。

响应示例：
```json
{
    "code": 0,
    "message": {
        "detail": "成功删除医生照片"
    }
}
```

注意：若文件不存在或删除期间发生 IO 错误，会抛出 `ResourceHTTPException` 并由全局异常处理器返回语义化错误信息。

---

### 3.9 获取医生照片（原始数据）
- GET `/admin/doctors/{doctor_id}/photo`
- 说明：根据医生 ID 返回真实图片二进制数据（非静态文件路径）。仅管理员可访问。

参数：
- `doctor_id`：医生 ID（路径参数）

权限与请求头：
```
Authorization: Bearer <token>
```

响应：
- 成功时返回图片二进制流，`Content-Type` 将根据文件扩展名自动推断（如 `image/jpeg`、`image/png`）。
- 失败时返回统一错误格式，例如医生不存在、未上传照片或文件缺失：
```json
{
  "code": 106,
  "message": {
    "error": "资源错误",
    "msg": "医生照片文件不存在"
  }
}
```

注意：
- 服务端会将 `Doctor.photo_path`（如 `/static/image/xxx.jpg` 或 `app/static/image/xxx.jpg`）规范化为相对 `app/` 的文件路径读取，避免暴露绝对路径。
- 若需要在浏览器直接预览，可在请求中不设置 `Accept` 限制，或将响应保存为本地文件。

---


---

## 4. 患者信息查询

### 4.1 搜索患者
- GET `/patients?name={name}&phone={phone}&patient_id={patient_id}`
- 说明：管理员搜索患者信息，支持按姓名、手机号或患者 ID 查询

参数（所有参数可选，至少提供一个）：
- `name` (string, optional)：按姓名模糊搜索
- `phone` (string, optional)：按手机号模糊搜索
- `patient_id` (int, optional)：按患者 ID 精确查询

请求示例：
```
GET /patients?name=王
GET /patients?phone=138
GET /patients?patient_id=12345
GET /patients?name=张&phone=139
```

权限与请求头：
```
Authorization: Bearer <token>
```

响应示例：
```json
{
    "code": 0,
    "message": {
        "patients": [
            {
                "patient_id": 12345,
                "name": "王小明",
                "phone": "13812345678",
                "gender": "男",
                "age": 28,
                "id_card": "110101199501011234"
            },
            {
                "patient_id": 12346,
                "name": "王丽",
                "phone": "13898765432",
                "gender": "女",
                "age": 32,
                "id_card": "110102198901012345"
            }
        ]
    }
}
```

字段说明：
- `patient_id`：患者唯一标识
- `name`：患者姓名
- `phone`：从关联的 User 表中获取的手机号
- `gender`：性别（中文字符串："男"/"女"/"其他"）
- `age`：根据出生日期自动计算的年龄
- `id_card`：身份证号（脱敏处理由前端实现）

注意：
- 至少需要提供一个搜索条件，否则返回参数错误
- 姓名和手机号使用模糊匹配（SQL LIKE 查询）
- 患者 ID 使用精确匹配
- 年龄计算考虑了当年是否已过生日

---

## 5. 审核管理

所有审核接口均需管理员权限，请求头需包含：
```
Authorization: Bearer <token>
```

### A. 排班审核（Schedule Audit）

#### 5.1 获取排班审核列表
- GET `/audit/schedule`
- 说明：获取所有排班审核申请列表（无分页），按提交时间倒序排列

响应示例：
```json
{
    "code": 0,
    "message": {
        "audits": [
            {
                "id": 1,
                "department_id": 1,
                "department_name": "心内科",
                "clinic_id": 56,
                "clinic_name": "心血管科门诊",
                "submitter_id": 10,
                "submitter_name": "李医生",
                "submit_time": "2025-11-01T10:30:00",
                "week_start": "2025-11-04",
                "week_end": "2025-11-10",
                "remark": "下周排班申请",
                "status": "pending",
                "auditor_id": null,
                "audit_time": null,
                "audit_remark": null,
                "schedule": [[...], [...], ...] // 7x3 排班数据
            }
        ]
    }
}
```

#### 5.2 获取排班审核详情
- GET `/audit/schedule/{audit_id}`
- 说明：获取指定排班审核申请的详细信息

响应格式同上列表项，包含完整排班 JSON 数据。

#### 5.3 通过排班审核
- POST `/audit/schedule/{audit_id}/approve`
- 说明：管理员审核通过排班申请，系统会将排班数据写入 `Schedule` 表，生成实际排班记录

请求体：
```json
{
    "comment": "审核通过，排班合理"
}
```

响应示例：
```json
{
    "code": 0,
    "message": {
        "audit_id": 1,
        "status": "approved",
        "auditor_id": 5,
        "audit_time": "2025-11-01T14:30:00"
    }
}
```

业务逻辑：
1. 更新审核表状态为 `approved`
2. 记录审核人和审核时间
3. 解析排班 JSON 数据，为每个时间段生成 `Schedule` 记录（包括医生、门诊、日期、时段等）
4. 事务提交，确保数据一致性

#### 5.4 拒绝排班审核
- POST `/audit/schedule/{audit_id}/reject`
- 说明：管理员拒绝排班申请

请求体：
```json
{
    "comment": "排班冲突，请重新提交"
}
```

响应格式同通过审核。

---

### B. 请假审核（Leave Audit）

#### 5.5 获取请假审核列表
- GET `/audit/leave`
- 说明：获取所有请假审核申请列表（无分页），按提交时间倒序排列

响应示例：
```json
{
    "code": 0,
    "message": {
        "audits": [
            {
                "id": 1,
                "doctor_id": 10,
                "doctor_name": "李医生",
                "doctor_title": "主治医师",
                "department_name": "心内科",
                "leave_start_date": "2025-11-05",
                "leave_end_date": "2025-11-07",
                "leave_days": 3,
                "reason": "因个人原因需要请假三天...",
                "reason_preview": "因个人原因需要请假三天...",
                "attachments": [
                    "/static/audit/leave_20251101_123456.jpg"
                ],
                "submit_time": "2025-11-01T09:00:00",
                "status": "pending",
                "auditor_id": null,
                "audit_time": null,
                "audit_remark": null
            }
        ]
    }
}
```

字段说明：
- `leave_days`：自动计算的请假天数（包含起止日期）
- `reason_preview`：原因前 50 字符的预览（若超出则添加 `...`）
- `attachments`：附件文件路径列表（可用于后续获取附件内容）

#### 5.6 获取请假审核详情
- GET `/audit/leave/{audit_id}`
- 说明：获取指定请假审核申请的详细信息

响应格式同上列表项，包含完整请假原因和附件列表。

#### 5.7 通过请假审核
- POST `/audit/leave/{audit_id}/approve`
- 说明：管理员审核通过请假申请，系统会**自动将医生在请假期间的所有排班记录调为停诊状态**

请求体：
```json
{
    "comment": "同意请假申请"
}
```

响应示例：
```json
{
    "code": 0,
    "message": {
        "audit_id": 1,
        "status": "approved",
        "auditor_id": 5,
        "audit_time": "2025-11-01T14:45:00"
    }
}
```

业务逻辑：
1. 删除医生在请假期间（`leave_start_date` 至 `leave_end_date`）的所有排班记录
2. 更新审核表状态为 `approved`
3. 记录审核人和审核时间
4. 事务提交，确保数据一致性


#### 5.8 拒绝请假审核
- POST `/audit/leave/{audit_id}/reject`
- 说明：管理员拒绝请假申请，不会影响现有排班

请求体：
```json
{
    "comment": "请假理由不充分，建议协调其他时间"
}
```

响应格式同通过审核。

---



### C. 附件管理

#### 5.9 获取审核附件（二进制数据）
- GET `/audit/attachment/raw?path={file_path}`
- 说明：根据附件的相对路径返回文件二进制数据，用于查看请假申请等审核中的附件（图片/文件）

参数：
- `path`（查询参数）：附件的相对路径（存储在 `LeaveAudit.attachment_data_json` 中）
  - 示例：`/static/audit/leave_20251101_123456.jpg` 或 `static/audit/leave_20251101_123456.jpg`

响应：
- 成功时返回文件二进制流（`StreamingResponse`），`Content-Type` 自动根据文件扩展名推断
  - 示例：`image/jpeg`、`image/png`、`application/pdf` 等
- 失败时返回统一错误格式：
```json
{
    "code": 106,
    "message": {
        "error": "资源错误",
        "msg": "附件文件不存在或路径错误"
    }
}
```

安全性说明：
- 路径会经过规范化处理，防止 `../` 等目录遍历攻击
- 强制校验文件路径必须在应用基础目录内
- 仅管理员可访问


### D. 加号申请（AddSlotAudit）

#### 概述
加号申请用于医生为某患者在已有排班上增加号源或供管理员直接为患者创建加号并生成挂号记录。流程支持：医生发起申请（需管理员审批）或管理员直接执行加号（跳过审批）。

#### 5.10 医生/管理员发起加号 POST: `/schedules/add-slot`  (注意这里是doctor/schedules/add-slot)
- 权限：需登录；管理员可直接执行加号并同时创建挂号记录，医生仅能提交申请由管理员审批。
- 请求体（JSON）：

```json
{
    "schedule_id": 12345,
    "patient_id": 67890,
    "slot_type": "普通",    // 号源类型：普通/专家/特需
    "reason": "需要加号给病患" // 医生发起时可选
}
```

- 行为：
    - 管理员调用：系统直接在事务内为患者创建 `RegistrationOrder`（同时更新 `Schedule` 的 `total_slots` 与 `remaining_slots`），响应包含新订单 `order_id`。
    - 医生调用：系统在 `add_slot_audit` 表中创建申请记录，等待管理员审批，响应包含 `audit_id`。

- 成功响应示例（管理员直接创建挂号）：
```json
{
    "code": 0,
    "message": {
        "detail": "加号记录已创建",
        "order_id": 1001
    }
}
```

- 成功响应示例（医生提交申请）：
```json
{
    "code": 0,
    "message": {
        "detail": "加号申请已提交，等待审核",
        "audit_id": 2001
    }
}
```

#### 5.11 管理员查看所有加号申请 GET: `/audit/add-slot`
- 权限：仅管理员。
- 说明：返回所有 `AddSlotAudit` 记录（当前实现无分页）。建议在记录量大时加入分页与筛选参数（如 status/doctor_id/patient_id/date range）。
- 响应示例：
```json
{
    "code": 0,
    "message": {
        "audits": [
            {
                "audit_id": 2001,
                "schedule_id": 12345,
                "doctor_id": 10,
                "patient_id": 67890,
                "slot_type": "普通",
                "reason": "病人有特殊情况",
                "applicant_id": 10,
                "submit_time": "2025-11-13T10:00:00",
                "status": "pending",
                "auditor_admin_id": null,
                "audit_time": null,
                "audit_remark": null
            }
        ]
    }
}
```

#### 5.12 管理员审批（已有接口）
- 通过：POST `/audit/add-slot/{audit_id}/approve`（管理员）
- 拒绝：POST `/audit/add-slot/{audit_id}/reject`（管理员）
- 说明：审批通过时，系统会在事务内调用加号服务创建 `RegistrationOrder` 并更新对应 `Schedule`；审批结果会写回 `add_slot_audit` 表（status、auditor_admin_id、audit_time、audit_remark）。

---

## 6. 系统配置管理

所有系统配置接口均需管理员权限，请求头需包含：
```
Authorization: Bearer <token>
```

### 6.1 获取系统配置
- GET `/config`
- 说明：获取系统所有配置信息，包括挂号配置和排班配置

响应示例：
```json
{
    "code": 0,
    "message": {
        "registration": {
            "advanceBookingDays": 14,
            "sameDayDeadline": "08:00",
            "noShowLimit": 3,
            "cancelHoursBefore": 24,
            "sameClinicInterval": 7
        },
        "schedule": {
            "maxFutureDays": 60,
            "morningStart": "08:00",
            "morningEnd": "12:00",
            "afternoonStart": "14:00",
            "afternoonEnd": "18:00",
            "eveningStart": "18:30",
            "eveningEnd": "21:00",
            "consultationDuration": 15,
            "intervalTime": 5
        }
    }
}
```

#### registration (挂号配置) 字段说明

| 字段名 | 类型 | 说明 | 范围 |
|--------|------|------|------|
| advanceBookingDays | number | 提前挂号天数 | 1-90 |
| sameDayDeadline | string | 当日挂号截止时间，格式: HH:mm | 例: "08:00" |
| noShowLimit | number | 爽约次数限制 | 1-10 |
| cancelHoursBefore | number | 退号提前时间（小时） | 1-72 |
| sameClinicInterval | number | 同科室挂号间隔（天） | 1-30 |

#### schedule (排班配置) 字段说明

| 字段名 | 类型 | 说明 | 范围 |
|--------|------|------|------|
| maxFutureDays | number | 最多排未来天数 | 7-180 |
| morningStart | string | 上午班开始时间，格式: HH:mm | 例: "08:00" |
| morningEnd | string | 上午班结束时间，格式: HH:mm | 例: "12:00" |
| afternoonStart | string | 下午班开始时间，格式: HH:mm | 例: "14:00" |
| afternoonEnd | string | 下午班结束时间，格式: HH:mm | 例: "18:00" |
| eveningStart | string | 晚班开始时间，格式: HH:mm | 例: "18:30" |
| eveningEnd | string | 晚班结束时间，格式: HH:mm | 例: "21:00" |
| consultationDuration | number | 单次就诊时长（分钟） | 5-60 |
| intervalTime | number | 就诊间隔时间（分钟） | 0-30 |

---

### 6.2 更新系统配置
- PUT `/config`
- 说明：更新系统配置信息，可选择性更新挂号配置和/或排班配置

请求体（所有字段可选，只需传递需要更新的字段）：
```json
{
    "registration": {
        "advanceBookingDays": 30,
        "noShowLimit": 5
    },
    "schedule": {
        "maxFutureDays": 90,
        "morningStart": "07:30"
    }
}
```

响应示例：
```json
{
    "code": 0,
    "message": {
        "detail": "配置更新成功"
    }
}
```

#### 数据验证规则

**数值范围验证**：
- `advanceBookingDays`: 1 ≤ value ≤ 90
- `noShowLimit`: 1 ≤ value ≤ 10
- `cancelHoursBefore`: 1 ≤ value ≤ 72
- `sameClinicInterval`: 1 ≤ value ≤ 30
- `maxFutureDays`: 7 ≤ value ≤ 180
- `consultationDuration`: 5 ≤ value ≤ 60
- `intervalTime`: 0 ≤ value ≤ 30

**时间格式验证**：
- 所有时间字段必须符合 HH:mm 格式（24小时制）
- 例如: "08:00", "14:30", "23:59"

**逻辑验证**：
- 上午班: `morningStart < morningEnd`
- 下午班: `afternoonStart < afternoonEnd`
- 晚班: `eveningStart < eveningEnd`

错误响应示例：
```json
{
    "code": 99,
    "message": {
        "error": "请求参数错误",
        "msg": "上午班开始时间必须小于结束时间"
    }
}
```

---

## 7. 门诊管理

### 7.1 获取科室门诊列表
- GET `/admin/clinics?dept_id={dept_id}`
- 说明：获取门诊列表，可按小科室过滤
- 参数 `dept_id` 可选，用于按小科室过滤

响应（**包含价格信息**）：
```json
{
    "code": 0,
    "message": {
        "clinics": [
            {
                "clinic_id": 1,
                "area_id": 1,
                "name": "心血管内科普通门诊",
                "address": "门诊楼2层",
                "minor_dept_id": 1,
                "clinic_type": 0,
                "default_price_normal": 60.00,
                "default_price_expert": 180.00,
                "default_price_special": null,
                "create_time": "2025-10-17T00:51:23"
            }
        ]
    }
}
```

字段说明：
- `clinic_type`：门诊类型，0-普通，1-国疗，2-特需
- `default_price_normal/expert/special`：三种号源的价格配置，null 表示该层级未配置

### 7.2 创建门诊
- POST `/admin/clinics`
- 说明：创建新的门诊地点

请求体（**支持价格配置**）：
```json
{
    "minor_dept_id": 1,
    "name": "心血管内科普通门诊",
    "clinic_type": 0,
    "address": "门诊楼2层",
    "default_price_normal": 60.00,     // 可选：普通号价格
    "default_price_expert": 180.00,    // 可选：专家号价格
    "default_price_special": null      // 可选：null表示不设置，继承上级
}
```

字段说明：
- `minor_dept_id`：小科室ID（必填）
- `name`：门诊名称（必填）
- `clinic_type`：门诊类型，0-普通，1-国疗，2-特需（必填，默认0）
- `address`：门诊地址描述（可选）
- `default_price_normal/expert/special`：三种号源的价格配置（可选）

响应（包含价格信息）：
```json
{
    "code": 0,
    "message": {
        "clinic_id": 123,
        "name": "心血管内科普通门诊",
        "address": "门诊楼2层",
        "minor_dept_id": 1,
        "clinic_type": 0,
        "default_price_normal": 60.00,
        "default_price_expert": 180.00,
        "default_price_special": null,
        "detail": "门诊创建成功"
    }
}
```

### 7.3 更新门诊信息
- PUT `/admin/clinics/{clinic_id}`
- 说明：更新门诊信息

请求体（**支持更新价格配置**，所有字段可选）：
```json
{
    "name": "心内科VIP诊室",
    "address": "内科楼3楼",
    "clinic_type": 2,
    "default_price_normal": 70.00,
    "default_price_expert": 200.00,
    "default_price_special": 600.00
}
```

响应（包含价格信息）：
```json
{
    "code": 0,
    "message": {
        "clinic_id": 123,
        "name": "心内科VIP诊室",
        "address": "内科楼3楼",
        "minor_dept_id": 1,
        "clinic_type": 2,
        "default_price_normal": 70.00,
        "default_price_expert": 200.00,
        "default_price_special": 600.00,
        "detail": "门诊信息更新成功"
    }
}
```

---

## 8. 排班管理

### 排班价格处理逻辑

创建/更新排班时的价格处理规则：
- **如果 `price > 0`**: 直接使用提供的价格
- **如果 `price <= 0`**: 自动按优先级查询价格配置
  1. 查询医生级别配置（DOCTOR）
  2. 若未找到，查询诊室级别配置（CLINIC）
  3. 若未找到，查询小科室级别配置（MINOR_DEPT）
  4. 若未找到，查询全局配置（GLOBAL）
  5. 若仍未找到，使用系统默认价格（普通50元，专家100元，特需500元）

### 8.1 获取科室排班
- GET `/admin/departments/{dept_id}/schedules?start_date=2025-10-31&end_date=2025-11-30`
- 说明：获取指定小科室在日期范围内的所有排班

参数：
- `dept_id`：小科室ID（路径参数）
- `start_date`：开始日期，格式 YYYY-MM-DD（查询参数）
- `end_date`：结束日期，格式 YYYY-MM-DD（查询参数）

响应：
```json
{
    "code": 0,
    "message": {
        "schedules": [
            {
                "schedule_id": 1,
                "doctor_id": 1,
                "doctor_name": "陈明哲",
                "clinic_id": 1,
                "clinic_name": "心血管内科普通门诊",
                "clinic_type": 0,
                "date": "2025-10-31",
                "week_day": "五",
                "time_section": "上午",
                "slot_type": "专家",
                "total_slots": 20,
                "remaining_slots": 15,
                "status": "正常",
                "price": 100.00,
                "create_time": "2025-10-20T23:44:28"
            }
        ]
    }
}
```

字段说明：
- `time_section`：时间段，值为"上午"、"下午"、"晚上"
- `slot_type`：号源类型，值为"普通"、"专家"、"特需"
- `status`：排班状态，如"正常"、"停诊"
- `week_day`：星期几，值为"一"、"二"、"三"、"四"、"五"、"六"、"日"

### 8.2 获取医生排班
- GET `/admin/doctors/{doctor_id}/schedules?start_date=2025-10-31&end_date=2025-11-30`
- 说明：获取指定医生在日期范围内的所有排班

参数：
- `doctor_id`：医生ID（路径参数）
- `start_date`：开始日期，格式 YYYY-MM-DD（查询参数）
- `end_date`：结束日期，格式 YYYY-MM-DD（查询参数）

响应：同 4.1 获取科室排班的响应格式

### 8.3 获取门诊排班
- GET `/admin/clinics/{clinic_id}/schedules?start_date=2025-10-31&end_date=2025-11-30`
- 说明：获取指定门诊在日期范围内的所有排班

参数：
- `clinic_id`：门诊ID（路径参数）
- `start_date`：开始日期，格式 YYYY-MM-DD（查询参数）
- `end_date`：结束日期，格式 YYYY-MM-DD（查询参数）

响应：同 4.1 获取科室排班的响应格式

### 8.4 创建排班
- POST `/admin/schedules`
- 说明：为医生创建新的排班记录

请求体（**支持分级价格查询**）：
```json
{
    "doctor_id": 1,
    "clinic_id": 1,
    "schedule_date": "2025-11-01",
    "time_section": "上午",
    "slot_type": "专家",
    "status": "正常",
    "price": 0,            // 0 或负数将触发分级查询价格
    "total_slots": 20
}
```

字段说明：
- `doctor_id`：医生ID（必填）
- `clinic_id`：门诊ID（必填）
- `schedule_date`：出诊日期，格式 YYYY-MM-DD（必填）
- `time_section`：时间段，"上午"/"下午"/"晚上"（必填）
- `slot_type`：号源类型，"普通"/"专家"/"特需"（必填）
- `status`：排班状态（必填，默认"正常"）
- `price`：挂号原价，单位元（必填，≥0）
  - **若 `price > 0`**：直接使用提供的价格
  - **若 `price <= 0`**：按 DOCTOR → CLINIC → MINOR_DEPT → GLOBAL 顺序查询价格配置
- `total_slots`：总号源数（必填，≥0）

价格查询示例：
```
医生301在诊室201出诊，slot_type="普通"，price=0
查询顺序：
1. 医生301的普通号价格 → 找到 80.00 ✓
2. 最终使用价格：80.00元
```

注意：创建时系统会自动计算 `week_day`（星期几），并设置 `remaining_slots` 等于 `total_slots`。

响应：
```json
{
    "code": 0,
    "message": {
        "schedule_id": 123,
        "detail": "排班创建成功"
    }
}
```

### 8.5 更新排班
- PUT `/admin/schedules/{schedule_id}`
- 说明：更新排班信息，支持部分字段更新

参数：
- `schedule_id`：排班ID（路径参数）

请求体（**支持分级价格查询**，所有字段可选）：
```json
{
    "doctor_id": 1,
    "clinic_id": 1,
    "schedule_date": "2025-11-02",
    "time_section": "下午",
    "slot_type": "特需",
    "status": "停诊",
    "price": 0,            // 0 或负数将触发分级查询价格
    "total_slots": 25
}
```

字段说明：
- 更新 `schedule_date` 时，系统会自动重新计算 `week_day`
- 更新 `total_slots` 时，系统会自动调整 `remaining_slots`（保持差额不变，但不允许为负数）
- 更新 `price` 时：
  - **若 `price > 0`**：直接使用提供的价格
  - **若 `price <= 0`**：按 DOCTOR → CLINIC → MINOR_DEPT → GLOBAL 顺序查询价格配置

响应：
```json
{
    "code": 0,
    "message": {
        "detail": "排班更新成功"
    }
}
```

### 8.6 删除排班
- DELETE `/admin/schedules/{schedule_id}`
- 说明：删除指定的排班记录

参数：
- `schedule_id`：排班ID（路径参数）

响应：
```json
{
    "code": 0,
    "message": {
        "detail": "排班删除成功"
    }
}
```

### 8.7 获取指定医生今日排班
- GET `/doctors/{doctor_id}/schedules/today`
- 说明：获取指定医生今天的所有排班记录，包括可预约的号源类型信息

参数：
- `doctor_id`：医生 ID（路径参数）

权限与请求头：
```
Authorization: Bearer <token>
```

响应示例：
```json
{
    "code": 0,
    "message": {
        "doctor_id": 1,
        "date": "2025-11-13",
        "schedules": [
            {
                "schedule_id": 101,
                "time_section": "上午",
                "clinic_id": 5,
                "clinic_name": "心内科门诊",
                "clinic_type": 0,
                "minor_dept_name": "心内科",
                "slot_type": "普通",
                "total_slots": 20,
                "remaining_slots": 15,
                "price": 60.00,
                "status": "正常",
                "available_slot_types": ["普通"]
            },
            {
                "schedule_id": 102,
                "time_section": "下午",
                "clinic_id": 6,
                "clinic_name": "国疗门诊",
                "clinic_type": 1,
                "minor_dept_name": "心内科",
                "slot_type": "专家",
                "total_slots": 10,
                "remaining_slots": 8,
                "price": 120.00,
                "status": "正常",
                "available_slot_types": ["普通", "专家"]
            }
        ]
    }
}
```

字段说明：
- `clinic_type`：门诊类型
  - 0 = 普通门诊
  - 1 = 专家门诊（国疗）
  - 2 = 特需门诊
- `available_slot_types`：该门诊可预约的号源类型列表，根据 `clinic_type` 自动计算：
  - 普通门诊 (0) → `["普通"]`
  - 专家门诊 (1) → `["普通", "专家"]`
  - 特需门诊 (2) → `["普通", "专家", "特需"]`

---

## 9. 用户风险管理接口（管理员专用）

### 9.1 获取风险用户列表 Get: `/admin/anti-scalper/users`

管理员获取用户列表，支持按风险等级和封禁状态筛选。

#### Header:
```
Authorization: Bearer <token>
```

#### Query 参数:
- `user_type` (可选): 用户类型筛选，默认 `normal`
  - `high`: 高风险用户（风险等级为 MEDIUM 或 HIGH）
  - `low`: 低风险用户（风险等级为 LOW）
  - `normal`: 正常用户（无风险记录或风险等级为 SAFE）
  - `banned`: 已封禁用户
- `page` (可选): 页码，默认 1
- `page_size` (可选): 每页数量，默认 10

#### 输出:
```json
{
    "code": 0,
    "message": {
        "total": 1,
        "page": 1,
        "page_size": 10,
        "users": [
            {
                "user_id": 12,
                "username": "user12@example.com",
                "risk_score": 85,
                "risk_level": "HIGH",
                "banned": true,
                "ban_type": "all",
                "ban_until": "2025-12-14T01:24:39"
            }
        ]
    }
}
```

---

### 9.2 获取用户详细信息 Get: `/admin/anti-scalper/users/{user_id}`

管理员查看指定用户的详细风险信息和封禁记录。

#### Header:
```
Authorization: Bearer <token>
```

#### 输出:
```json
{
    "code": 0,
    "message": {
        "user_id": 12,
        "username": "user12@example.com",
        "is_admin": false,
        "risk_score": 85,
        "risk_level": "HIGH",
        "ban_active": true,
        "ban_type": "all",
        "ban_until": "2025-12-14T01:24:39",
        "ban_reason": "风险分数达到 92",
        "unban_time": null,
        "risk_logs": [
            {
                "log_id": 123,
                "risk_score": 60,
                "risk_level": "MEDIUM",
                "behavior_type": "no_show",
                "description": "2 次爽约累积 +60",
                "alert_time": "2025-11-14T01:24:39"
            }
        ],
        "ban_records": [
            {
                "ban_id": 5,
                "ban_type": "all",
                "ban_until": "2025-12-14T01:24:39",
                "is_active": true,
                "reason": "风险分数达到 92",
                "banned_at": "2025-11-14T01:24:39",
                "deactivated_at": null
            }
        ]
    }
}
```

---

### 9.3 获取用户统计信息（时间范围内行为） Get: `/admin/anti-scalper/users/{user_id}/stats` 

管理员查看指定用户在时间范围内的行为统计（挂号、取消、爽约、登录次数等）。

#### Header:
```
Authorization: Bearer <token>
```

#### Query 参数:
- `start_date` (必需): 开始日期，格式 `YYYY-MM-DD`
- `end_date` (必需): 结束日期，格式 `YYYY-MM-DD`

#### 输出:
```json
{
    "code": 0,
    "message": {
        "user_id": 12,
        "start_date": "2025-11-01",
        "end_date": "2025-11-14",
        "total_registrations": 15,
        "total_cancellations": 3,
        "cancellation_rate": 0.2,
        "total_completed": 10,
        "total_no_show": 2,
        "total_confirmed": 0,
        "total_pending": 0,
        "total_waitlist": 0,
        "login_count": 8
    }
}
```

---

### 9.4 封禁用户 Post: `/admin/anti-scalper/ban`

管理员封禁指定用户，禁止其注册、登录或全部操作。如果用户已有封禁记录，则更新现有记录。

#### Header:
```
Authorization: Bearer <token>
```

#### Body:
```json
{
    "user_id": 12,
    "ban_type": "login",
    "duration_days": 7,
    "reason": "多次异常行为检测"
}
```

参数说明：
- `user_id` (必需): 要封禁的用户ID
- `ban_type` (必需): 封禁类型
  - `register` - 禁止注册新号
  - `login` - 禁止登录
  - `all` - 全部禁止
- `duration_days` (可选): 封禁天数，不传或为 0 表示永久封禁
- `reason` (可选): 封禁原因

#### 输出:
```json
{
    "code": 0,
    "message": {
        "detail": "封禁操作成功",
        "user_id": 12,
        "ban_type": "login",
        "ban_until": "2025-11-21T14:30:00",
        "is_active": true
    }
}
```

---

### 9.5 解封用户 Post: `/admin/anti-scalper/unban`

管理员解除用户的活跃封禁。

#### Header:
```
Authorization: Bearer <token>
```

#### Body:
```json
{
    "user_id": 12,
    "reason": "申诉通过"
}
```

参数说明：
- `user_id` (必需): 要解封的用户ID
- `reason` (可选): 解封原因

#### 输出:
```json
{
    "code": 0,
    "message": {
        "detail": "解除封禁成功",
        "user_id": 12,
        "ban_type": "login",
        "unban_time": "2025-11-14T01:30:00"
    }
}
```

---


# 三、认证 API 接口详情

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




## 四、统计 API 接口 `/statistics`

**概览**
- 本项目在 `backend/app/api/statistics.py` 提供了一组统计接口，用于运营与统计报表。接口路径前缀为 `/statistics`，统一返回 `ResponseModel`：`{ "code": int, "message": ... }`。
- 大部分统计接口需登录鉴权（通过 `Authorization: Bearer <token>`），医院/院区/科室/医生级别的统计与排行榜要求用户为管理员（`is_admin==True`）。

**通用参数**
- `date` (query, string, 格式 `YYYY-MM-DD`): 统计日期。默认为当天，建议明确传参。
- `date_range` (query, string): `today` / `7days` / `30days`。若同时传 `date_range`，`date_range` 优先于 `date`。
  - `today`: 仅统计当天
  - `7days`: 统计最近7天（含今天）
  - `30days`: 统计最近30天（含今天）

**注意**: 
- 若 `date` 为空字符串或格式不正确，接口会返回错误提示。
- 所有统计排除 `status == 'cancelled'` 的挂号记录。
- 收入统计基于 `Schedule.price` 字段。

---

### 4.1 医院总体挂号统计 Get: `/statistics/hospital/registrations`

获取医院总体挂号统计数据（管理员专用）。

#### Header:
```
Authorization: Bearer <token>
```

#### Query 参数:
- `date` (可选): 统计日期，格式 `YYYY-MM-DD`，默认当天
- `date_range` (可选): 时间范围 `today`/`7days`/`30days`

#### 输出:
```json
{
    "code": 0,
    "message": {
        "date": "2025-11-14",
        "date_range": "today",
        "start_date": "2025-11-14",
        "end_date": "2025-11-14",
        "total_registrations": 150,
        "by_slot_type": {
            "普通": 80,
            "专家": 50,
            "特需": 20
        },
        "total_revenue": 15000.0,
        "completed_consultations": 120
    }
}
```

---

### 4.2 院区挂号统计 Get: `/statistics/areas/{area_id}/registrations`

按院区统计挂号数据，包含分科室明细（管理员专用）。

#### Header:
```
Authorization: Bearer <token>
```

#### Path 参数:
- `area_id`: 院区ID

#### Query 参数:
- `date` (可选): 统计日期，格式 `YYYY-MM-DD`，默认当天

#### 输出:
```json
{
    "code": 0,
    "message": {
        "area_id": 1,
        "start_date": "2025-11-14",
        "end_date": "2025-11-14",
        "total_registrations": 50,
        "by_slot_type": {
            "普通": 30,
            "专家": 20
        },
        "total_revenue": 5000.0,
        "departments": [
            {
                "minor_dept_id": 10,
                "registrations": 30,
                "revenue": 3000.0
            },
            {
                "minor_dept_id": 11,
                "registrations": 20,
                "revenue": 2000.0
            }
        ]
    }
}
```

---

### 4.3 科室挂号统计 Get: `/statistics/departments/{minor_dept_id}/registrations`

获取某小科室的挂号统计，包含按医生分解（管理员专用）。

#### Header:
```
Authorization: Bearer <token>
```

#### Path 参数:
- `minor_dept_id`: 小科室ID

#### Query 参数:
- `date` (可选): 统计日期，格式 `YYYY-MM-DD`，默认当天
- `date_range` (可选): 时间范围 `today`/`7days`/`30days`

#### 输出:
```json
{
    "code": 0,
    "message": {
        "minor_dept_id": 10,
        "start_date": "2025-11-14",
        "end_date": "2025-11-14",
        "total_registrations": 30,
        "by_slot_type": {
            "普通": 20,
            "专家": 10
        },
        "total_revenue": 3000.0,
        "completed_consultations": 25,
        "doctors": [
            {
                "doctor_id": 5,
                "doctor_name": "张医生",
                "title": "主任医师",
                "registrations": 20,
                "revenue": 2000.0
            },
            {
                "doctor_id": 6,
                "doctor_name": "李医生",
                "title": "副主任医师",
                "registrations": 10,
                "revenue": 1000.0
            }
        ]
    }
}
```

---

### 4.4 医生挂号统计 Get: `/statistics/doctors/{doctor_id}/registrations`

获取某医生的详细挂号统计，包含排班利用率（管理员专用）。

#### Header:
```
Authorization: Bearer <token>
```

#### Path 参数:
- `doctor_id`: 医生ID

#### Query 参数:
- `date` (可选): 统计日期，格式 `YYYY-MM-DD`，默认当天
- `date_range` (可选): 时间范围 `today`/`7days`/`30days`

#### 输出:
```json
{
    "code": 0,
    "message": {
        "doctor_id": 5,
        "doctor_name": "张医生",
        "title": "主任医师",
        "dept_name": null,
        "start_date": "2025-11-14",
        "end_date": "2025-11-14",
        "total_registrations": 20,
        "by_slot_type": {
            "普通": 12,
            "专家": 8
        },
        "total_revenue": 2000.0,
        "completed_consultations": 18,
        "by_time_section": {
            "上午": 12,
            "下午": 8
        },
        "schedules": [
            {
                "schedule_id": 100,
                "clinic_name": "内科门诊",
                "time_section": "上午",
                "slot_type": "专家",
                "registrations": 8,
                "total_slots": 10,
                "utilization_rate": 0.8
            },
            {
                "schedule_id": 101,
                "clinic_name": "内科门诊",
                "time_section": "下午",
                "slot_type": "普通",
                "registrations": 12,
                "total_slots": 15,
                "utilization_rate": 0.8
            }
        ]
    }
}
```

---

### 4.5 科室排行榜 Get: `/statistics/departments/ranking`

获取科室挂号排行榜（管理员专用）。

#### Header:
```
Authorization: Bearer <token>
```

#### Query 参数:
- `date` (可选): 统计日期，格式 `YYYY-MM-DD`，默认当天
- `order_by` (可选): 排序依据，`registrations`（挂号数）或 `revenue`（收入），默认 `registrations`
- `limit` (可选): 返回数量，默认 10

#### 输出:
```json
{
    "code": 0,
    "message": {
        "date": "2025-11-14",
        "order_by": "registrations",
        "ranking": [
            {
                "minor_dept_id": 10,
                "dept_name": "内科",
                "registrations": 50,
                "revenue": 5000.0
            },
            {
                "minor_dept_id": 11,
                "dept_name": "外科",
                "registrations": 40,
                "revenue": 4500.0
            }
        ]
    }
}
```

---

### 4.6 医生排行榜 Get: `/statistics/doctors/ranking`

获取医生挂号排行榜（管理员专用）。

#### Header:
```
Authorization: Bearer <token>
```

#### Query 参数:
- `dept_id` (可选): 限定科室ID，不传则全院统计
- `date` (可选): 统计日期，格式 `YYYY-MM-DD`，默认当天
- `order_by` (可选): 排序依据，`registrations`（挂号数）或 `revenue`（收入），默认 `registrations`
- `limit` (可选): 返回数量，默认 10

#### 输出:
```json
{
    "code": 0,
    "message": {
        "date": "2025-11-14",
        "order_by": "registrations",
        "ranking": [
            {
                "doctor_id": 5,
                "doctor_name": "张医生",
                "title": "主任医师",
                "dept_name": "内科",
                "registrations": 30,
                "revenue": 3000.0
            },
            {
                "doctor_id": 6,
                "doctor_name": "李医生",
                "title": "副主任医师",
                "dept_name": "外科",
                "registrations": 25,
                "revenue": 2800.0
            }
        ]
    }
}
```

---

### 4.7 用户统计 Get: `/statistics/users`

获取系统用户总数（需登录）。

#### Header:
```
Authorization: Bearer <token>
```

#### 输出:
```json
{
    "code": 0,
    "message": {
        "total_users": 1250
    }
}
```

---

### 4.8 访问量统计 Get: `/statistics/visits`

获取网站访问总量及增长比例（需登录）。

#### Header:
```
Authorization: Bearer <token>
```

#### Query 参数:
- `compare_days` (可选): 对比天数，默认 3 天

#### 输出:
```json
{
    "code": 0,
    "message": {
        "total_visits": 15000,
        "growth_percent": 12.5,
        "compare_days": 3
    }
}
```

**说明**: `growth_percent` 表示相对于 `compare_days` 天前的增长百分比。

---

