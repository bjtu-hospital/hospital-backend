
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
        - GET `/minor-departments`：获取小科室列表（可按大科室过滤，支持分页）
        - PUT `/minor-departments/{minor_dept_id}`：更新（支持将小科室转移到另一个大科室）
        - DELETE `/minor-departments/{minor_dept_id}`：删除（若存在关联医生则拒绝）

    - 医生（Doctor）管理
        - POST `/doctors`：创建医生档案（可选同时在请求中提供 `identifier` 与 `password` 来一并创建用户账号并关联）
            - 如果提供 `identifier` 与 `password`，会在同一事务中创建 `User` 并将 `doctor.user_id` 关联到新用户；若只提供 `identifier` 而未提供 `password` 会返回错误。
        - GET `/doctors`：获取医生列表（可按科室过滤，支持分页）
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

#### 2.6 获取小科室列表（支持分页）
- GET `/minor-departments?major_dept_id={major_dept_id}&page={page}&page_size={page_size}`
- 参数：
  - `major_dept_id` (可选)：按大科室过滤
  - `page` (可选)：页码，从 1 开始，默认 1
  - `page_size` (可选)：每页数量，默认 50

请求示例：
```
GET /minor-departments                        # 获取全部
GET /minor-departments?major_dept_id=1        # 按大科室过滤
GET /minor-departments?page=1&page_size=20    # 分页查询
GET /minor-departments?major_dept_id=1&page=1&page_size=10  # 过滤+分页
```

响应（**包含价格信息和分页信息**）：
```json
{
    "code": 0,
    "message": {
        "total": 45,
        "page": 1,
        "page_size": 20,
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
            // ... 其他小科室（当前页共20条）
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

### 3.2 获取医生列表（支持分页）
- GET `/doctors?dept_id={dept_id}&name={name}&page={page}&page_size={page_size}`
- 参数：
  - `dept_id` (可选)：按科室 ID 过滤
  - `name` (可选)：按医生姓名模糊搜索
  - `page` (可选)：页码，从 1 开始，默认 1
  - `page_size` (可选)：每页数量，默认 50

请求示例：
```
GET /doctors?name=张
GET /doctors?dept_id=1&name=王
GET /doctors?page=1&page_size=20           # 分页查询：第1页，每页20条
GET /doctors?dept_id=1&page=2&page_size=10  # 科室过滤+分页
```

响应（**包含价格信息和分页信息**）：
```json
{
    "code": 0,
    "message": {
        "total": 150,
        "page": 1,
        "page_size": 20,
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
            // ... 其他医生（当前页共20条）
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
- 说明：将医生调到新的科室。**若医生当前为科室长，转科时会自动取消其科室长身份**

请求体：
```json
{
    "new_dept_id": 2  // 新科室ID
}
```

业务规则：
1. 医生必须存在
2. 目标科室必须存在
3. **自动取消科室长**：若医生的 `is_department_head` 为 1（是科室长），转科后自动设为 0

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

### 3.10 选择科室长
- POST `/admin/departments/{dept_id}/heads/select`
- 说明：管理员将某个医生设置为科室长

参数：
- `dept_id`：小科室ID（路径参数）

请求体：
```json
{
    "doctor_id": 123
}
```

权限与请求头：
```
Authorization: Bearer <token>
```

业务规则：
1. **职称限制**：仅主任或副主任（title 字段包含"主任"）可被设为科室长
2. **数量限制**：科室长数量受分级配置控制
   - 配置键：`departmentHeadMaxCount`
   - 查询顺序：MINOR_DEPT（小科室）→ GLOBAL（全局）
   - 默认值：2（若未配置）
3. **归属验证**：医生必须属于该科室
4. **重复设置**：若医生已是科室长，不会报错，但计入数量限制时不重复计算

响应示例：
```json
{
    "code": 0,
    "message": {
        "dept_id": 1,
        "doctor_id": 123,
        "is_department_head": true,
        "max_heads": 2
    }
}
```

错误示例：
```json
{
    "code": 99,
    "message": {
        "error": "业务规则校验失败",
        "msg": "仅主任/副主任可设为科室长"
    }
}
```

```json
{
    "code": 99,
    "message": {
        "error": "业务规则校验失败",
        "msg": "科室长数量已达上限(2)"
    }
}
```

### 3.11 取消科室长
- DELETE `/admin/departments/{dept_id}/heads/{doctor_id}`
- 说明：管理员取消某个医生的科室长身份

参数：
- `dept_id`：小科室ID（路径参数）
- `doctor_id`：医生ID（路径参数）

权限与请求头：
```
Authorization: Bearer <token>
```

业务规则：
1. 医生必须属于该科室
2. 若医生本就不是科室长，不会报错，返回 `was_department_head: false`

响应示例：
```json
{
    "code": 0,
    "message": {
        "dept_id": 1,
        "doctor_id": 123,
        "was_department_head": true,
        "is_department_head": false
    }
}
```

字段说明：
- `was_department_head`：操作前是否为科室长
- `is_department_head`：操作后状态（固定为 false）

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

重要更新（2025-11）：
- 路径前缀统一为 `/admin/audit/*`（例如：`/admin/audit/leave`、`/admin/audit/schedule`、`/admin/audit/add-slot`）。
- 审核人字段迁移为 `auditor_user_id`（替代旧字段 `auditor_admin_id`）。响应体中的 `auditor_id` 表示审核人的 `user_id`。
- 请假审核的附件字段 `attachments` 统一为字符串路径数组，例如：`["/static/audit/leave_20251101_123456.jpg"]`。

### A. 排班审核（Schedule Audit）

#### 5.1 获取排班审核列表
- GET `/admin/audit/schedule`
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
- GET `/admin/audit/schedule/{audit_id}`
- 说明：获取指定排班审核申请的详细信息

响应格式同上列表项，包含完整排班 JSON 数据。

#### 5.3 通过排班审核
- POST `/admin/audit/schedule/{audit_id}/approve`
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
- POST `/admin/audit/schedule/{audit_id}/reject`
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
- GET `/admin/audit/leave`
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
- GET `/admin/audit/leave/{audit_id}`
- 说明：获取指定请假审核申请的详细信息

响应格式同上列表项，包含完整请假原因和附件列表。

#### 5.7 通过请假审核
- POST `/admin/audit/leave/{audit_id}/approve`
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
- POST `/admin/audit/leave/{audit_id}/reject`
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

#### 5.11 管理员查看所有加号申请 GET: `/admin/audit/add-slot`
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
- 通过：POST `/admin/audit/add-slot/{audit_id}/approve`（管理员）
- 拒绝：POST `/admin/audit/add-slot/{audit_id}/reject`（管理员）
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

### 7.1 获取科室门诊列表（支持分页）
- GET `/admin/clinics?dept_id={dept_id}&page={page}&page_size={page_size}`
- 说明：获取门诊列表，可按小科室过滤，支持分页
- 参数：
  - `dept_id` (可选)：按小科室过滤
  - `page` (可选)：页码，从 1 开始，默认 1
  - `page_size` (可选)：每页数量，默认 50

请求示例：
```
GET /admin/clinics                           # 获取全部
GET /admin/clinics?dept_id=1                 # 按科室过滤
GET /admin/clinics?page=1&page_size=20       # 分页查询
GET /admin/clinics?dept_id=1&page=1&page_size=10  # 过滤+分页
```

响应（**包含价格信息和分页信息**）：
```json
{
    "code": 0,
    "message": {
        "total": 80,
        "page": 1,
        "page_size": 20,
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
            // ... 其他门诊（当前页共20条）
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

### 8.2 获取门诊排班
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

注意事项：
- 创建时系统会自动计算 `week_day`（星期几），并设置 `remaining_slots` 等于 `total_slots`
- **冲突检测**：系统会检查该医生在同一日期、同一时间段是否已有排班，如存在冲突则创建失败
- 冲突错误示例：`"该医生在 2025-11-20 上午 已有排班(ID: 123)"`

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
- `duration_days` (必需): 封禁天数，0 表示永久封禁
- `reason` (必需): 封禁原因，1-500字符

#### 输出:
```json
{
    "code": 0,
    "message": {
        "detail": "封禁操作成功",
        "user_id": 12,
        "ban_type": "login",
        "ban_until": "2025-11-28T14:30:00.000000",
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
- `reason` (必需): 解封原因，1-500字符

#### 输出:
```json
{
    "code": 0,
    "message": {
        "detail": "解除封禁成功",
        "user_id": 12,
        "ban_type": "login",
        "unban_time": "2025-11-21T14:30:00.000000"
    }
}
```

---

## 10. 医生缺勤管理 API

所有缺勤管理接口均需管理员权限，请求头需包含：
```
Authorization: Bearer <token>
```

### 10.1 手动标记单日缺勤
- POST `/attendance/mark-absent/single?target_date=YYYY-MM-DD`
- 说明：手动触发单日缺勤标记，自动检测该日所有无考勤记录的排班并标记为 ABSENT

参数：
- `target_date`（查询参数，必填）：目标日期，格式 YYYY-MM-DD
  - 只能标记历史日期（不能是今天或未来日期）

响应示例：
```json
{
    "code": 0,
    "message": {
        "detail": "缺勤标记完成",
        "date": "2025-11-15",
        "total_schedules": 45,
        "absent_marked": 3,
        "already_marked": 2
    }
}
```

字段说明：
- `total_schedules`：当日总排班数
- `absent_marked`：本次新标记的缺勤数
- `already_marked`：之前已标记的缺勤数

---

### 10.2 批量标记日期范围缺勤
- POST `/attendance/mark-absent/range?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
- 说明：批量检测并标记日期范围内的缺勤记录，返回每日统计明细

参数：
- `start_date`（查询参数，必填）：开始日期，格式 YYYY-MM-DD
- `end_date`（查询参数，必填）：结束日期，格式 YYYY-MM-DD
  - 结束日期不能包含今天或未来日期
  - 日期范围不能超过 90 天

响应示例：
```json
{
    "code": 0,
    "message": {
        "detail": "批量缺勤标记完成",
        "date_range": {
            "start": "2025-11-01",
            "end": "2025-11-15"
        },
        "total_marked": 12,
        "daily_statistics": [
            {
                "date": "2025-11-01",
                "total_schedules": 50,
                "absent_marked": 2,
                "already_marked": 0
            },
            {
                "date": "2025-11-02",
                "total_schedules": 48,
                "absent_marked": 1,
                "already_marked": 1
            }
        ]
    }
}
```

---

### 10.3 查询缺勤统计
- GET `/attendance/absent-statistics?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&doctor_id={doctor_id}`
- 说明：查询缺勤统计，包括缺勤记录列表和按医生汇总的统计信息

参数：
- `start_date`（查询参数，必填）：开始日期，格式 YYYY-MM-DD
- `end_date`（查询参数，必填）：结束日期，格式 YYYY-MM-DD
- `doctor_id`（查询参数，可选）：医生ID，不指定则查询所有医生

响应示例：
```json
{
    "code": 0,
    "message": {
        "date_range": {
            "start": "2025-11-01",
            "end": "2025-11-15"
        },
        "total_absent_count": 12,
        "absent_records": [
            {
                "record_id": 101,
                "doctor_id": 5,
                "doctor_name": "张三",
                "schedule_id": 4567,
                "schedule_date": "2025-11-05",
                "time_section": "上午",
                "clinic_name": "心内科门诊",
                "is_absent": true,
                "marked_at": "2025-11-06T08:30:00"
            }
        ],
        "doctor_summary": [
            {
                "doctor_id": 5,
                "doctor_name": "张三",
                "absent_count": 3,
                "total_schedules": 15,
                "absence_rate": 0.2
            },
            {
                "doctor_id": 12,
                "doctor_name": "李四",
                "absent_count": 2,
                "total_schedules": 10,
                "absence_rate": 0.2
            }
        ]
    }
}
```

字段说明：
- `absent_records`：缺勤记录详细列表
  - `is_absent`：是否标记为缺勤
  - `marked_at`：标记时间
- `doctor_summary`：按医生汇总的统计
  - `absent_count`：缺勤次数
  - `total_schedules`：总排班次数
  - `absence_rate`：缺勤率（缺勤次数/总排班次数）

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




# 四、统计 API 接口 `/statistics`

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

# 五、 医生 API 接口详细
## 1. 医生工作台接口（Doctor Client）

医生端工作台与医生个人信息相关接口。需通过 `/staff/login` 获取 token 且账号已绑定医生档案。统一成功返回：`{ "code": 0, "message": { ... } }`。

通用要求：
- Header：`Authorization: Bearer <token>` （除非特别说明）
- 返回错误按全局错误码，成功 `code=0`。
- 时间格式：统一使用 `HH:MM`（24 小时制）。

### 1.1 工作台总览 Get: `/workbench/dashboard`
权限：医生本人。

说明：汇总医生今日排班状态、签到/签退进度、接诊统计、提醒与最近接诊记录。

响应示例：
```json
{
    "code": 0,
    "message": {
        "doctor": {
            "id": 12,
            "name": "张三",
            "title": "主治医师",
            "department": "心内科",
            "photo_path": null
        },
        "shiftStatus": {
            "status": "not_checkin",
            "currentShift": {
                "id": 4567,
                "name": "上午门诊",
                "startTime": "08:00",
                "endTime": "12:00",
                "location": "门诊楼 3 层 305",
                "countdown": "1小时20分钟"
            },
            "checkinTime": null,
            "checkoutTime": null,
            "workDuration": null,
            "timeToCheckout": null
        },
        "todayData": {
            "pendingConsultation": 5,
            "ongoingConsultation": 2,
            "completedConsultation": 18,
            "totalConsultation": 25
        },
        "reminders": [
            { "id": 1, "type": "system", "title": "请按时签到", "icon": "bell", "time": "08:00" }
        ],
        "recentRecords": [
            { "id": 9001, "patientName": "李四", "consultationTime": "09:32", "diagnosis": "高血压" },
            { "id": 9002, "patientName": "王五", "consultationTime": "10:15", "diagnosis": "糖尿病" }
        ]
    }
}
```

字段说明：
- `shiftStatus.status`: `not_checkin | checked_in | checkout_pending | checked_out`
- `countdown`: 未签到且班次未开始时的剩余时间。
- `timeToCheckout`: 签到后离班次结束的剩余时间。
- `workDuration`: 已签到后累计工作时长（签到到当前/签退）。
- **`recentRecords.consultationTime`**: 实际就诊时间（HH:MM 格式），从数据库 `visit_times` 字段解析，**仅显示已完成/已确认就诊的患者**

### 1.2. 签到 Post: `/workbench/checkin`
请求体：
```json
{ "shiftId": 4567, "latitude": 39.984, "longitude": 116.318 }
```
响应示例：
```json
{ "code": 0, "message": { "checkinTime": "08:05", "status": "checked_in", "message": "签到成功", "workDuration": "0分钟" } }
```
状态流转：`not_checkin -> checked_in`。

### 1.3. 签退 Post: `/workbench/checkout`
请求体：
```json
{ "shiftId": 4567, "latitude": 39.984, "longitude": 116.318 }
```
响应示例：
```json
{ "code": 0, "message": { "checkoutTime": "11:58", "workDuration": "3小时53分钟", "status": "checked_out", "message": "签退成功" } }
```
状态流转：`checked_in | checkout_pending -> checked_out`。

### 1.4. 班次列表 Get: `/workbench/shifts?doctorId={doctorId}&date=YYYY-MM-DD`
说明：返回指定日期医生的全部班次与当前状态。

响应示例：
```json
{ "code": 0, "message": { "shifts": [ { "id": 4567, "name": "上午门诊", "startTime": "08:00", "endTime": "12:00", "location": "门诊楼 3 层 305", "status": "checking_in" } ] } }
```
`status` 取值：`not_started | checking_in | checkout_pending | checked_out`。

### 1.5. 接诊统计 Get: `/workbench/consultation-stats?doctorId={doctorId}`
说明：统计当天挂号/接诊状态分布。
响应示例：
```json
{ "code": 0, "message": { "pending": 5, "ongoing": 2, "completed": 18, "total": 25 } }
```
统计规则：
- pending: 订单状态 `pending` 或 `waitlist`
- ongoing: 订单状态 `confirmed`
- completed: 订单状态 `completed`
- total: 当日全部匹配订单数（含上述三类）

### 1.6. 最近接诊记录 Get: `/workbench/recent-consultations?doctorId={doctorId}&limit=3`

**重要更新（2025-11）**：
- 现在**仅返回已就诊的患者**（订单状态为 `COMPLETED` 或 `CONFIRMED` 且有实际就诊时间）
- 数据库层面过滤：`visit_times IS NOT NULL` 且 `status IN (COMPLETED, CONFIRMED)`
- `consultationTime` 显示**实际就诊时间**（从 `visit_times` 字段解析），不再出现 "--:--"
- 不再填充待就诊/已取消患者，所有返回记录保证有有效就诊时间

请求参数：
- `doctorId`（必填）：医生ID
- `limit`（可选）：返回记录数，默认 3 条

响应示例：
```json
{
    "code": 0,
    "message": {
        "records": [
            { "id": 9001, "patientName": "李四", "consultationTime": "09:32", "diagnosis": "高血压" },
            { "id": 9002, "patientName": "王五", "consultationTime": "10:15", "diagnosis": "糖尿病" },
            { "id": 9003, "patientName": "赵六", "consultationTime": "11:20", "diagnosis": "感冒" }
        ]
    }
}
```

字段说明：
- **`consultationTime`**: 实际就诊时间（HH:MM 格式），从数据库 `registration_order.visit_times` 字段解析
- **`diagnosis`**: 诊断信息（可能为 null，取决于是否录入）
- 所有记录按就诊时间倒序排列（最新的在前）

数据来源：
- 从 `registration_order` 表读取，条件：
  - `doctor_id = {doctorId}`
  - `slot_date = 今天`
  - `status IN (COMPLETED, CONFIRMED)`
  - `visit_times IS NOT NULL`（数据库级别过滤）

### 1.7. 医生用户信息（含照片） Post: `/auth/user-info` (注意这个在auth中并非doctor)
说明：返回当前登录账号绑定的医生档案及照片二进制（Base64）。若不是医生账号则 `doctor: null`。

成功响应：
```json
{
    "code": 0,
    "message": {
        "doctor": {
            "id": 12,
            "name": "张三",
            "department": "心内科",
            "department_id": "1",
            "hospital": "主院区",
            "title": "主治医师",
            "is_department_head": "True",
            "photo_mime": "image/jpeg",
            "photo_base64": "iVBORw0KGgoAAA..."
        }
    }
}
```
非医生：
```json
{ "code": 0, "message": { "doctor": null } }
```
照片字段：前端可构造：`data:{photo_mime};base64,{photo_base64}` 用于直接展示。

### 1.8. 查询医生排班（按日期范围）Get: `/doctors/schedules`
说明：查询医生在指定日期范围内的排班记录。医生只能查询自己的排班，管理员可查询任意医生的排班。

权限：
- 医生：只能查询自己的排班（`doctor_id` 参数可选，若提供必须与当前用户匹配）
- 管理员：可查询任意医生排班（必须提供 `doctor_id` 参数）

请求参数：
- `doctor_id`（可选）：医生ID
  - 医生用户：可省略或必须等于自己的ID
  - 管理员：必须提供
- `start_date`（必填）：开始日期，格式 YYYY-MM-DD
- `end_date`（必填）：结束日期，格式 YYYY-MM-DD

请求头：
```
Authorization: Bearer <token>
```

响应示例：
```json
{
    "code": 0,
    "message": {
        "schedules": [
            {
                "schedule_id": 123,
                "doctor_id": 1,
                "doctor_name": "张三",
                "clinic_id": 5,
                "clinic_name": "心内科门诊",
                "clinic_type": 0,
                "date": "2025-11-20",
                "week_day": "三",
                "time_section": "上午",
                "slot_type": "普通",
                "total_slots": 20,
                "remaining_slots": 15,
                "status": "正常",
                "price": 60.00
            }
        ]
    }
}
```

### 1.9. 查询医生当日排班 Get: `/doctors/schedules/today`
说明：查询指定医生今天的所有排班记录。医生只能查询自己的，管理员可查询任意医生的。

权限：
- 医生：只能查询自己的排班（`doctor_id` 参数可选，若提供必须与当前用户匹配）
- 管理员：可查询任意医生排班（必须提供 `doctor_id` 参数）

请求参数：
- `doctor_id`（可选）：医生ID
  - 医生用户：可省略或必须等于自己的ID
  - 管理员：必须提供

请求头：
```
Authorization: Bearer <token>
```

响应示例：
```json
{
    "code": 0,
    "message": {
        "schedules": [
            {
                "schedule_id": 101,
                "doctor_id": 1,
                "doctor_name": "张三",
                "department_id": 3,
                "department_name": "心内科",
                "clinic_type": "普通门诊",
                "date": "2025-11-20",
                "time_slot": "上午",
                "total_slots": 20,
                "remaining_slots": 15,
                "available_slot_types": ["普通"]
            },
            {
                "schedule_id": 102,
                "doctor_id": 1,
                "doctor_name": "张三",
                "department_id": 3,
                "department_name": "心内科",
                "clinic_type": "专家门诊",
                "date": "2025-11-20",
                "time_slot": "下午",
                "total_slots": 10,
                "remaining_slots": 8,
                "available_slot_types": ["普通", "专家"]
            }
        ]
    }
}
```

字段说明：
- `clinic_type`：门诊类型字符串（"普通门诊" / "专家门诊" / "特需门诊"）
- `available_slot_types`：该门诊可预约的号源类型列表，根据门诊类型自动计算：
  - 普通门诊 → `["普通"]`
  - 专家门诊 → `["普通", "专家"]`
  - 特需门诊 → `["普通", "专家", "特需"]`

### 1.10. 获取排班详情 Get: `/doctors/schedules/{schedule_id}`
说明：根据排班ID获取排班详细信息。医生只能查询自己的排班详情，管理员可以查询任意排班。

权限：
- 医生：只能查询本人的排班详情
- 管理员：可查询任意排班详情

请求参数：
- `schedule_id`：排班ID（路径参数）

请求头：
```
Authorization: Bearer <token>
```

响应示例：
```json
{
    "code": 0,
    "message": {
        "schedule_id": 123,
        "doctor_id": 1,
        "doctor_name": "张三",
        "doctor_title": "主治医师",
        "department_id": 3,
        "department_name": "心内科",
        "clinic_id": 5,
        "clinic_name": "心内科门诊",
        "clinic_type": "普通门诊",
        "date": "2025-11-20",
        "week_day": "三",
        "time_section": "上午",
        "slot_type": "普通",
        "total_slots": 20,
        "remaining_slots": 15,
        "status": "正常",
        "price": 60.00,
        "available_slot_types": ["普通"]
    }
}
```

字段说明：
- 包含完整的排班信息、医生信息、科室信息和门诊信息
- `doctor_title`：医生职称（主治医师/副主任医师/主任医师等）
- `week_day`：星期几（"一" / "二" / "三" / "四" / "五" / "六" / "日"）
- 其他字段说明同前述接口

---

## 2. 医生就诊

### 2.1 获取接诊队列

**接口地址**：`GET /doctor/consultation/queue`

**请求参数**：
- `schedule_id` (query): 排班ID，必需

**响应示例**：
```json
{
  "code": 0,
  "message": {
    "stats": {
      "totalSlots": 8,
      "confirmedCount": 1,
      "waitlistCount": 0,
      "completedCount": 2,
      "waitingCount": 0,
      "passedCount": 0
    },
    "scheduleInfo": {
      "scheduleId": 5669,
      "doctorId": 6,
      "date": "2025-11-20",
      "timeSection": "上午"
    },
    "currentPatient": {
      "orderId": 117,
      "patientId": 5,
      "patientName": "测试用户003",
      "gender": "女",
      "age": null,
      "queueNumber": "A001",
      "status": "confirmed",
      "isCall": true,
      "callTime": "2025-11-21 00:32:45",
      "visitTime": "2025-11-20 10:23:00",
      "passCount": 0,
      "priority": 0
    },
    "nextPatient": null,
    "queue": [],
    "waitlist": []
  }
}
```

### 2.2 叫下一个患者

**接口地址**：`POST /doctor/consultation/next`

**请求参数**：
```json
{
  "schedule_id": 5669
}
```

**响应示例**：
```json
{
  "code": 0,
  "message": {
    "calledPatient": {
      "orderId": 117,
      "patientId": 5,
      "patientName": "测试用户003",
      "gender": "女",
      "age": null,
      "queueNumber": "A001",
      "status": "confirmed",
      "isCall": true,
      "callTime": "2025-11-21 00:32:45",
      "visitTime": "2025-11-20 10:23:00",
      "passCount": 0,
      "priority": 0
    },
    "scheduleId": 5669
  }
}
```

### 2.3 患者过号（未到场）

**接口地址**：`POST /doctor/consultation/pass`

**请求参数**：
```json
{
  "patient_order_id": 117
}
```

**响应示例**：
```json
{
  "code": 0,
  "message": {
    "passedPatient": {
      "orderId": 117,
      "patientId": 5,
      "patientName": "测试用户003",
      "gender": "女",
      "age": null,
      "queueNumber": "A001",
      "status": "confirmed",
      "isCall": false,
      "callTime": "2025-11-21 00:32:45",
      "visitTime": "2025-11-20 10:23:00",
      "passCount": 1,
      "priority": 0
    },
    "nextPatient": null,
    "scheduleId": 5669
  }
}
```

### 2.4 完成患者就诊

**接口地址**：`POST /doctor/consultation/complete`

**请求参数**：
```json
{
  "patient_id": 5,
  "schedule_id": 5669
}
```

**响应示例**：
```json
{
  "code": 0,
  "message": {
    "detail": "就诊完成",
    "completedPatient": {
      "orderId": 117,
      "patientId": 5,
      "patientName": "测试用户003",
      "gender": "女",
      "age": null,
      "queueNumber": "A001",
      "status": "completed",
      "isCall": false,
      "callTime": "2025-11-21 00:32:45",
      "visitTime": "2025-11-20 10:23:00",
      "passCount": 0,
      "priority": 0
    },
    "scheduleId": 5669
  }
}
```

**字段说明**：
- `stats`: 队列统计信息
  - `totalSlots`: 总号源数量
  - `confirmedCount`: 已确认患者数量
  - `waitlistCount`: 候补患者数量
  - `completedCount`: 已完成就诊数量
  - `waitingCount`: 等待就诊数量
  - `passedCount`: 过号患者数量
- `scheduleInfo`: 排班信息
- `currentPatient`: 当前正在就诊的患者（`isCall: true`）
- `nextPatient`: 下一位待叫号的患者
- `queue`: 正在等待的患者队列（不包含正在就诊的患者）
- `waitlist`: 候补队列
- `passCount`: 患者过号次数
- `priority`: 优先级（数字越小优先级越高）



---
### 2.5 查询患者姓名

**接口地址**：`GET /doctor/patient/{patient_id}`

**路径参数**：
- `patient_id`：患者ID

**权限**：仅医生或管理员可调用

**成功响应示例**：
```json
{
    "code": 0,
    "message": {
        "patient_id": 5,
        "name": "测试用户003",
        "gender": "女",
        "age": 34
    }
}
```

**错误示例（患者不存在）**：
```json
{
    "code": 201,
    "message": {
        "error": "资源获取失败",
        "msg": "患者ID 9999 不存在"
    }
}
```

**错误示例（权限不足）**：
```json
{
    "code": 102,
    "message": {
        "error": "认证时出现异常",
        "msg": "仅医生可查询患者信息"
    }
}
```

**说明**：
- 若患者存在且当前用户为医生/管理员则返回基本信息。
- 年龄通过出生日期动态计算；若无出生日期则返回 `null`。

---

## 3. 医生请假管理接口

医生可以查询可请假日历、提交请假申请、查询请假历史。所有接口需要医生身份认证。

### 3.1 获取请假日历 Get: `/doctor/leave/schedule`

获取指定月份的请假日历，显示每天每个时段的请假状态。

#### Header:
```
Authorization: Bearer <token>
```

#### Query 参数:
- `year` (必填): 年份，格式 YYYY
- `month` (必填): 月份，格式 MM（01-12）

#### 请求示例:
```
GET /doctor/leave/schedule?year=2025&month=12
```

#### 输出:
```json
{
    "code": 0,
    "message": {
        "year": 2025,
        "month": 12,
        "days": [
            {
                "date": "2025-12-01",
                "weekday": "一",
                "hasSchedule": true,
                "canApplyLeave": true,
                "shiftLeaveStatuses": [
                    {
                        "shift": "morning",
                        "leaveStatus": null
                    },
                    {
                        "shift": "afternoon",
                        "leaveStatus": null
                    },
                    {
                        "shift": "night",
                        "leaveStatus": null
                    },
                    {
                        "shift": "full",
                        "leaveStatus": null
                    }
                ]
            },
            {
                "date": "2025-12-20",
                "weekday": "六",
                "hasSchedule": true,
                "canApplyLeave": false,
                "shiftLeaveStatuses": [
                    {
                        "shift": "morning",
                        "leaveStatus": null
                    },
                    {
                        "shift": "afternoon",
                        "leaveStatus": null
                    },
                    {
                        "shift": "night",
                        "leaveStatus": null
                    },
                    {
                        "shift": "full",
                        "leaveStatus": "pending"
                    }
                ]
            }
        ]
    }
}
```

字段说明：
- `hasSchedule`: 该日是否有排班
- `canApplyLeave`: 是否可以申请请假（基于时间规则和现有申请）
- `shiftLeaveStatuses`: 各时段的请假状态数组
  - `shift`: 时段类型（"morning"/"afternoon"/"night"/"full"）
  - `leaveStatus`: 请假状态（null/"pending"/"approved"/"rejected"）
- `weekday`: 星期几（"一"/"二"/"三"/"四"/"五"/"六"/"日"）

---

### 3.2 提交请假申请 Post: `/doctor/leave/apply`

医生提交请假申请，支持上传附件（图片凭证）。

#### Header:
```
Authorization: Bearer <token>
```

#### Body:
```json
{
    "date": "2025-12-20",
    "shift": "full",
    "reason": "因个人原因需要请假",
    "attachments": [
        "static/images/audit/2025/11/26/20251126184713_诊断证明.jpg",
        "static/images/audit/2025/11/26/20251126184716_病历单.jpg"
    ]
}
```

字段说明：
- `date` (必填): 请假日期，格式 YYYY-MM-DD
- `shift` (必填): 请假时段
  - `"morning"`: 上午班
  - `"afternoon"`: 下午班
  - `"night"`: 晚班
  - `"full"`: 全天
- `reason` (必填): 请假原因，1-500字符
- `attachments` (可选): 附件路径字符串数组（通过 `/common/upload` 获取到的路径）

#### 请假时限规则:
1. **全天请假**：需要至少提前一天提交（leave_date > 今天）
2. **单时段请假**：
   - 可以申请当天的请假
   - 但必须在该时段开始之前提交
   - 时段开始时间基于系统配置（morningStart: 08:00, afternoonStart: 13:30, eveningStart: 18:00）

#### 输出:
```json
{
    "code": 0,
    "message": {
        "id": 16,
        "doctor_id": 1,
        "leave_date": "2025-12-20",
        "shift": "full",
        "reason": "因个人原因需要请假",
        "status": "pending",
        "submit_time": "2025-11-26T18:47:20",
        "attachments": [
            "static/images/audit/2025/11/26/20251126184713_诊断证明.jpg",
            "static/images/audit/2025/11/26/20251126184716_病历单.jpg"
        ]
    }
}
```

#### 错误示例:
```json
{
    "code": 99,
    "message": {
        "error": "请求参数错误",
        "msg": "全天请假需至少提前一天提交"
    }
}
```

```json
{
    "code": 99,
    "message": {
        "error": "请求参数错误",
        "msg": "该时段已开始，无法申请当天请假"
    }
}
```

```json
{
    "code": 99,
    "message": {
        "error": "请求参数错误",
        "msg": "该日期该时段已有请假申请，请勿重复提交"
    }
}
```

---

### 3.3 查询请假历史 Get: `/doctor/leave/history`

查询当前医生的请假申请历史记录。

#### Header:
```
Authorization: Bearer <token>
```

#### Query 参数:
- `status` (可选): 状态筛选
  - 不传或 "all": 全部
  - "pending": 待审核
  - "approved": 已通过
  - "rejected": 已拒绝

#### 请求示例:
```
GET /doctor/leave/history
GET /doctor/leave/history?status=pending
```

#### 输出:
```json
{
    "code": 0,
    "message": {
        "records": [
            {
                "id": 16,
                "doctor_id": 1,
                "leave_date": "2025-12-20",
                "shift": "full",
                "reason": "因个人原因需要请假",
                "status": "pending",
                "submit_time": "2025-11-26T18:47:20",
                "auditor_id": null,
                "audit_time": null,
                "audit_remark": null,
                "attachments": [
                    "static/images/audit/2025/11/26/20251126184713_诊断证明.jpg",
                    "static/images/audit/2025/11/26/20251126184716_病历单.jpg"
                ]
            }
        ]
    }
}
```

字段说明：
- `shift`: 请假时段（"morning"/"afternoon"/"night"/"full"）
- `status`: 审核状态（"pending"/"approved"/"rejected"）
- `auditor_id`: 审核人ID（审核后填写）
- `audit_time`: 审核时间（审核后填写）
- `audit_remark`: 审核备注（审核后填写）
- `attachments`: 附件路径字符串数组，可能为空数组

---


## 4、科室长请假审核接口（Doctor - Department Head）

科室长（部门负责人）可对本科室医生的请假申请进行审核。接口均需医生身份且当前用户具备科室长权限（系统以 `Doctor` 档案与科室绑定判定）。

通用要求：
- Header：`Authorization: Bearer <token>`
- 仅能操作/查看同科室医生的申请
- 仅 `pending` 状态的申请可执行批准/驳回

### 4.1 获取请假审核列表 Get: `/doctor/leave/audit`
查询参数：
- `status`（可选）：`pending | approved | rejected | all`，默认 `pending`
- `page`（可选）：页码，默认 1
- `page_size`（可选）：每页数量，默认 20

响应示例：
```json
{
    "code": 0,
    "message": {
        "audits": [
            {
                "id": 24,
                "doctor_id": 118,
                "doctor_name": "王医生",
                "doctor_title": "副主任医师",
                "department_name": "心内科",
                "leave_start_date": "2025-11-27",
                "leave_end_date": "2025-11-27",
                "leave_days": 1,
                "reason": "诊断后休整一天",
                "attachments": [ "static/images/audit/2025/11/26/xxx.jpg" ],
                "submit_time": "2025-11-26T18:47:20",
                "status": "pending",
                "auditor_id": null,
                "audit_time": null
            }
        ],
        "total": 21,
        "page": 1,
        "page_size": 20
    }
}
```

### 4.2 获取请假审核详情 Get: `/doctor/leave/audit/{audit_id}`
返回指定申请的完整信息，字段同列表项并包含完整 `reason` 与 `attachments`。

### 4.3 批准请假申请 Post: `/doctor/leave/audit/{audit_id}/approve`
请求体：
```json
{ "comment": "同意请假" }
```
成功响应：
```json
{ "code": 0, "message": { "audit_id": 24, "status": "approved", "auditor_id": 119, "audit_time": "2025-11-27T10:21:21" } }
```

### 4.4 驳回请假申请 Post: `/doctor/leave/audit/{audit_id}/reject`
请求体：
```json
{ "comment": "资料不充分，请补充附件" }
```
成功响应：
```json
{ "code": 0, "message": { "audit_id": 24, "status": "rejected", "auditor_id": 119, "audit_time": "2025-11-27T10:25:05" } }
```

字段与说明：
- `auditor_id`：当前审核人的 `user_id`（与管理员审核保持一致）
- `attachments`：对象数组 `{url, name}`（与医生提交时保持一致）
- 强校验：仅同科室记录可见/可操作；非科室长将返回权限不足错误

### 4.5 科室长排班管理

科室长可以管理本科室的排班，提交排班调整申请。以下接口均需科室长权限（`is_department_head = 1`）。

#### 4.5.1 获取管理门诊列表 Get: `/doctor/schedule/clinics`

获取科室长管理的门诊/科室列表。

**Header**:
```
Authorization: Bearer <token>
```

**业务规则**：
- 当前用户必须是医生且 `is_department_head = 1`
- 返回该科室长排班涉及的所有门诊

**响应示例**：
```json
{
    "code": 0,
    "message": [
        {
            "id": "1",
            "name": "心血管内科普通门诊",
            "totalSlots": null,
            "filledSlots": null
        }
    ]
}
```

#### 4.5.2 获取排班详情 Get: `/doctor/schedule/list`

获取指定门诊在特定周的排班详情。

**Query 参数**：
- `clinicId` (int, 必填)：门诊ID
- `startDate` (string, 必填)：周起始日期，格式 `YYYY-MM-DD`

**Header**:
```
Authorization: Bearer <token>
```

**业务规则**：
- 当前用户必须是科室长
- 返回一周（7天 × 3班次）的排班状态

**响应示例**：
```json
{
    "code": 0,
    "message": [
        {
            "date": "2025-11-25",
            "dayOfWeek": 1,
            "shift": "morning",
            "status": "filled",
            "doctorId": "10",
            "doctorName": "张三",
            "doctorTitle": null
        },
        {
            "date": "2025-11-25",
            "dayOfWeek": 1,
            "shift": "afternoon",
            "status": "empty"
        }
    ]
}
```

**字段说明**：
- `shift`：班次，`morning`/`afternoon`/`night`
- `status`：排班状态，`empty`（空）/`filled`（已排）/`unavailable`（不可用）

#### 4.5.3 获取可用医生列表 Get: `/doctor/schedule/available-doctors`

获取科室医生在指定日期和班次的可用状态。

**Query 参数**：
- `clinicId` (int, 必填)：门诊ID
- `date` (string, 必填)：日期，格式 `YYYY-MM-DD`
- `shift` (string, 必填)：班次，`morning`/`afternoon`/`night`

**Header**:
```
Authorization: Bearer <token>
```

**业务规则**：
- 当前用户必须是科室长
- 检查医生的排班冲突和请假状态

**响应示例**：
```json
{
    "code": 0,
    "message": [
        {
            "id": "10",
            "name": "张三",
            "title": "主任医师",
            "dept": 1,
            "status": "available",
            "conflictReason": null,
            "assignedCount": null
        },
        {
            "id": "11",
            "name": "李四",
            "title": "副主任医师",
            "dept": 1,
            "status": "conflict",
            "conflictReason": "当天已有排班",
            "assignedCount": null
        },
        {
            "id": "12",
            "name": "王五",
            "title": "主治医师",
            "dept": 1,
            "status": "leave",
            "conflictReason": null,
            "assignedCount": null
        }
    ]
}
```

**字段说明**：
- `status`：可用状态
  - `available`：可用
  - `conflict`：冲突（当天已有排班）
  - `leave`：请假中
- `conflictReason`：冲突原因说明

#### 4.5.4 提交排班调整申请 Post: `/doctor/schedule/submit-change`

科室长提交排班调整申请，生成审核记录。

**Header**:
```
Authorization: Bearer <token>
```

**请求体**：
```json
{
    "clinicId": 1,
    "changes": [
        {
            "date": "2025-11-25",
            "shift": "morning",
            "doctorId": "10"
        },
        {
            "date": "2025-11-25",
            "shift": "afternoon",
            "doctorId": null
        }
    ]
}
```

**字段说明**：
- `clinicId`：门诊ID（必填）
- `changes`：排班变更数组（必填）
  - `date`：日期，格式 `YYYY-MM-DD`
  - `shift`：班次，`morning`/`afternoon`/`night`
  - `doctorId`：医生ID，`null` 表示清空该时段排班

**业务规则**：
1. 当前用户必须是科室长
2. 系统自动将变更转换为周排班矩阵（7天 × 3班次）
3. 生成 `ScheduleAudit` 记录，状态为 `pending`
4. 等待管理员审核通过后生效

**响应示例**：
```json
{
    "code": 0,
    "message": {
        "msg": "申请已提交，等待审核",
        "auditId": 123
    }
}
```

---

### 4.6 科室长请假审核

科室长可以审核本科室医生的请假申请。以下接口均需科室长权限（`is_department_head = 1`）。

#### 4.6.1 获取请假审核列表 Get: `/doctor/leave/audit`

获取本科室医生的请假申请列表。

**Query 参数**：
- `status` (string, 可选)：审核状态，默认 `pending`
  - `pending`：待审核
  - `approved`：已通过
  - `rejected`：已拒绝
  - `all`：全部
- `page` (int, 可选)：页码，从 1 开始，默认 1
- `page_size` (int, 可选)：每页数量，默认 20

**Header**:
```
Authorization: Bearer <token>
```

**业务规则**：
- 当前用户必须是科室长（`is_department_head = 1`）
- 仅显示本科室医生的请假申请

**响应示例**：
```json
{
    "code": 0,
    "message": {
        "audits": [
            {
                "id": 1,
                "doctor_id": 10,
                "doctor_name": "张三",
                "doctor_title": "主任医师",
                "department_name": "心内科",
                "leave_start_date": "2025-12-01",
                "leave_end_date": "2025-12-03",
                "leave_days": 3,
                "reason": "因个人原因需要请假",
                "reason_preview": "因个人原因需要请假",
                "attachments": [
                    "/static/audit/leave_20251127_123456.jpg"
                ],
                "submit_time": "2025-11-27T10:00:00",
                "status": "pending",
                "auditor_id": null,
                "audit_time": null,
                "audit_remark": null
            }
        ]
    }
}
```

#### 4.6.2 获取请假审核详情 Get: `/doctor/leave/audit/{audit_id}`

查看单个请假申请的详细信息。

**路径参数**：
- `audit_id` (int)：审核ID

**Header**:
```
Authorization: Bearer <token>
```

**业务规则**：
- 当前用户必须是科室长
- 不可查看其他科室的请假申请

**响应格式**：同 4.6.1 列表项，包含完整请假原因和附件

#### 4.6.3 批准请假申请 Post: `/doctor/leave/audit/{audit_id}/approve`

科室长批准请假申请。

**路径参数**：
- `audit_id` (int)：审核ID

**Header**:
```
Authorization: Bearer <token>
```

**请求体**：
```json
{
    "comment": "同意请假"
}
```

**业务规则**：
1. 当前用户必须是科室长
2. 仅可审批本科室的请假申请
3. 仅可审批 `status = pending` 的申请
4. 批准后状态改为 `approved`

**响应示例**：
```json
{
    "code": 0,
    "message": {
        "audit_id": 1,
        "status": "approved",
        "auditor_id": 5,
        "audit_time": "2025-11-27T14:30:00"
    }
}
```

#### 4.6.4 驳回请假申请 Post: `/doctor/leave/audit/{audit_id}/reject`

科室长驳回请假申请。

**路径参数**：
- `audit_id` (int)：审核ID

**Header**:
```
Authorization: Bearer <token>
```

**请求体**：
```json
{
    "comment": "请假理由不充分，建议协调时间"
}
```

**业务规则**：
1. 当前用户必须是科室长
2. 仅可审批本科室的请假申请
3. 仅可审批 `status = pending` 的申请
4. **驳回必须提供 `comment`**（必填）
5. 驳回后状态改为 `rejected`

**响应示例**：
```json
{
    "code": 0,
    "message": {
        "audit_id": 1,
        "status": "rejected",
        "auditor_id": 5,
        "audit_time": "2025-11-27T14:35:00"
    }
}
```

---

## 5、医生查看患者详情 API (`/doctor`)

### 5.1 GET `/doctor/patient/{patient_id}`

**描述**: 医生查看患者完整详情，包括基本信息、病史信息、就诊记录。

**权限**: 
- 仅医生可访问（需登录并携带 Token）
- **严格授权模式（方案1）**：仅允许查看该医生曾经接诊过的患者（基于 `VisitHistory` 表的 `doctor_id` 匹配）
- 返回的就诊记录仅限当前医生的记录

**请求参数**:
- `patient_id` (path): 患者ID（整数）

**请求头**:
```
Authorization: Bearer <token>
```

**成功响应示例** (code=0):
```json
{
  "code": 0,
  "message": {
    "patientId": "77",
    "basicInfo": {
      "name": "张三",
      "gender": "男",
      "age": 25,
      "height": null,
      "phone": "138****5678",
      "idCard": "110101********1234",
      "address": "北京市海淀区学院路37号北京交通大学"
    },
    "medicalHistory": {
      "pastHistory": [],
      "allergyHistory": [],
      "familyHistory": []
    },
    "consultationRecords": [
      {
        "id": "224",
        "outpatientNo": "000224",
        "visitDate": "2025-09-08 17:15",
        "department": "呼吸内科",
        "doctorName": "李医生",
        "chiefComplaint": "咳嗽、咳痰",
        "presentIllness": "咳嗽3天，伴咳痰",
        "auxiliaryExam": "胸部X线：未见明显异常",
        "diagnosis": "急性上呼吸道感染",
        "prescription": "1. 阿莫西林胶囊 0.5g tid po\n2. 止咳糖浆 10ml tid po",
        "status": "completed"
      }
    ]
  }
}
```

**授权失败响应** (code=403):
```json
{
  "code": 403,
  "message": {
    "error": "资源操作失败",
    "msg": "无权查看该患者信息"
  }
}
```

**患者不存在响应** (code=404):
```json
{
  "code": 404,
  "message": {
    "error": "资源操作失败",
    "msg": "患者不存在"
  }
}
```

**字段说明**:

**basicInfo（基本信息）**:
- `name`: 患者姓名
- `gender`: 性别（男/女/未知）
- `age`: 年龄（根据出生日期自动计算）
- `height`: 身高（当前数据库暂无此字段，返回 null）
- `phone`: 手机号（脱敏处理，保留前3位和后4位，如 `138****5678`；短号码用星号代替）
- `idCard`: 身份证号（脱敏处理，保留前6位和后4位，如 `110101********1234`）
- `address`: 地址（默认为学校地址）

**medicalHistory（病史信息）**:
- `pastHistory`: 既往病史列表（当前数据库暂无专门病史表，返回空数组）
- `allergyHistory`: 过敏史列表（返回空数组）
- `familyHistory`: 家族病史列表（返回空数组）

**consultationRecords（就诊记录）**:
- `id`: 就诊记录ID
- `outpatientNo`: 门诊号（格式：6位数字，如 `000224`）
- `visitDate`: 就诊时间（格式：`YYYY-MM-DD HH:MM`）
- `department`: 就诊科室名称
- `doctorName`: 接诊医生姓名
- `chiefComplaint`: 主诉
- `presentIllness`: 现病史
- `auxiliaryExam`: 辅助检查结果
- `diagnosis`: 诊断
- `prescription`: 处方/医嘱
- `status`: 就诊状态（`completed`：已完成，`ongoing`：随访中）

**实现要点**:
1. **严格授权检查**：查询 `VisitHistory` 表验证当前医生是否曾接诊该患者，若无记录则返回 403
2. **数据脱敏**：
   - 手机号：11位标准号显示为 `前3位****后4位`，短号码用星号替代
   - 身份证/学号：显示为 `前6位********后4位`
3. **就诊记录过滤**：仅返回当前医生的就诊记录（`doctor_id` 匹配）
4. **年龄自动计算**：根据 `birth_date` 实时计算，考虑月日
5. **记录排序**：就诊记录按 `visit_date` 降序排列（最新在前）

**PowerShell 测试示例**:
```powershell
# 1. 医生登录获取 token
$loginResponse = Invoke-RestMethod -Uri "http://localhost:8000/auth/staff/login" `
    -Method POST -ContentType "application/json" `
    -Body '{"identifier":"doctor001","password":"123456"}'
$token = $loginResponse.message

# 2. 查看患者详情
$headers = @{ "Authorization" = "Bearer $token" }
$patientDetail = Invoke-RestMethod -Uri "http://localhost:8000/doctor/patient/77" `
    -Method GET -Headers $headers

# 3. 查看结果
$patientDetail | ConvertTo-Json -Depth 5
```

该脚本会：
- 自动登录医生账号
- 在患者ID范围内探测可查看与不可查看的患者
- 验证授权逻辑和响应格式

---


# 六、患者 API 接口详细

患者端 API 接口包含公开查询接口（无需登录）和预约管理接口（需要登录）。所有接口统一返回格式：`{ "code": 0, "message": {...} }`。

## 1. 公开查询接口（无需登录）

### 1.1 获取院区列表 Get: `/hospitals`

获取所有院区信息或指定院区信息，包含院区地图图片的base64编码数据。

#### Query 参数:
- `area_id` (可选): 院区ID，不传则返回所有院区

#### 输出:
```json
{
    \"code\": 0,
    \"message\": {
        \"areas\": [
            {
                \"area_id\": 1,
                \"name\": \"东院区\",
                \"destination\": \"北京市海淀区上园村3号\",
                \"latitude\": 39.984,
                \"longitude\": 116.318,
                \"image_type\": \"image/jpeg\",
                \"image_data\": \"base64编码的图片数据\",
                \"create_time\": \"2024-01-01T00:00:00\"
            }
        ]
    }
}
```

字段说明：
- `image_type`: MIME类型（如 \"image/jpeg\", \"image/png\"）
- `image_data`: base64编码的图片数据，前端可构造 `data:{image_type};base64,{image_data}` 直接显示

---

### 1.2 获取大科室列表 Get: `/major-departments`

获取所有大科室信息。

#### 输出:
```json
{
    \"code\": 0,
    \"message\": {
        \"departments\": [
            {
                \"major_dept_id\": 1,
                \"name\": \"内科\",
                \"description\": \"内科相关科室\"
            }
        ]
    }
}
```

---

### 1.3 获取小科室列表 Get: `/minor-departments`

获取小科室列表，支持按大科室过滤和分页。

#### Query 参数:
- `major_dept_id` (可选): 大科室ID，用于过滤
- `page` (可选): 页码，默认 1
- `page_size` (可选): 每页数量，默认 50

#### 请求示例:
```
GET /minor-departments
GET /minor-departments?major_dept_id=1
GET /minor-departments?page=1&page_size=20
```

#### 输出:
```json
{
    \"code\": 0,
    \"message\": {
        \"total\": 45,
        \"page\": 1,
        \"page_size\": 20,
        \"departments\": [
            {
                \"minor_dept_id\": 1,
                \"major_dept_id\": 1,
                \"name\": \"心内科\",
                \"description\": \"心脏内科\",
                \"default_price_normal\": 60.00,
                \"default_price_expert\": null,
                \"default_price_special\": 550.00
            }
        ]
    }
}
```

---

### 1.4 获取门诊列表 Get: `/clinics`

获取门诊列表，支持按科室/院区过滤和分页。

#### Query 参数:
- `dept_id` (可选): 小科室ID
- `area_id` (可选): 院区ID
- `page` (可选): 页码，默认 1
- `page_size` (可选): 每页数量，默认 50

#### 请求示例:
```
GET /clinics
GET /clinics?dept_id=1
GET /clinics?area_id=1&page=1&page_size=20
```

#### 输出:
```json
{
    \"code\": 0,
    \"message\": {
        \"total\": 80,
        \"page\": 1,
        \"page_size\": 20,
        \"clinics\": [
            {
                \"clinic_id\": 1,
                \"area_id\": 1,
                \"name\": \"心血管内科普通门诊\",
                \"address\": \"门诊楼2层\",
                \"minor_dept_id\": 1,
                \"clinic_type\": 0,
                \"default_price_normal\": 60.00,
                \"default_price_expert\": 180.00,
                \"default_price_special\": null
            }
        ]
    }
}
```

字段说明：
- `clinic_type`: 门诊类型，0-普通，1-国疗，2-特需

---

### 1.5 获取医生列表 Get: `/doctors`

获取医生列表，支持按科室过滤、姓名模糊搜索和分页。

#### Query 参数:
- `dept_id` (可选): 小科室ID
- `name` (可选): 医生姓名（模糊搜索）
- `page` (可选): 页码，默认 1
- `page_size` (可选): 每页数量，默认 50

#### 请求示例:
```
GET /doctors
GET /doctors?dept_id=1
GET /doctors?name=张
GET /doctors?dept_id=1&name=王&page=1&page_size=20
```

#### 输出:
```json
{
    \"code\": 0,
    \"message\": {
        \"total\": 150,
        \"page\": 1,
        \"page_size\": 20,
        \"doctors\": [
            {
                \"doctor_id\": 1,
                \"user_id\": 10,
                \"is_registered\": true,
                \"dept_id\": 1,
                \"name\": \"张三\",
                \"title\": \"主治医师\",
                \"specialty\": \"心血管疾病\",
                \"introduction\": \"从事心血管疾病临床工作多年...\",
                \"photo_path\": null,
                \"original_photo_url\": null,
                \"default_price_normal\": 80.00,
                \"default_price_expert\": null,
                \"default_price_special\": 888.00
            }
        ]
    }
}
```

字段说明：
- `is_registered`: 医生是否已有系统账号（is_active=true 且 is_deleted=false）

---

### 1.6 获取科室排班 Get: `/departments/{dept_id}/schedules`

获取指定小科室在日期范围内的所有排班。

#### Path 参数:
- `dept_id`: 小科室ID

#### Query 参数:
- `start_date` (必填): 开始日期，格式 YYYY-MM-DD
- `end_date` (必填): 结束日期，格式 YYYY-MM-DD

#### 请求示例:
```
GET /departments/1/schedules?start_date=2025-11-20&end_date=2025-11-30
```

#### 输出:
```json
{
    \"code\": 0,
    \"message\": {
        \"schedules\": [
            {
                \"schedule_id\": 123,
                \"doctor_id\": 1,
                \"doctor_name\": \"张三\",
                \"clinic_id\": 5,
                \"clinic_name\": \"心内科门诊\",
                \"clinic_type\": 0,
                \"date\": \"2025-11-20\",
                \"week_day\": \"三\",
                \"time_section\": \"上午\",
                \"slot_type\": \"普通\",
                \"total_slots\": 20,
                \"remaining_slots\": 15,
                \"status\": \"正常\",
                \"price\": 60.00
            }
        ]
    }
}
```

字段说明：
- `week_day`: 星期几（\"一\"/\"二\"/\"三\"/\"四\"/\"五\"/\"六\"/\"日\"）
- `time_section`: 时间段（\"上午\"/\"下午\"/\"晚上\"）
- `slot_type`: 号源类型（\"普通\"/\"专家\"/\"特需\"）
- `status`: 排班状态（\"正常\"/\"停诊\"）

---

### 1.7 获取医生排班 Get: `/doctors/{doctor_id}/schedules`

获取指定医生在日期范围内的所有排班。

#### Path 参数:
- `doctor_id`: 医生ID

#### Query 参数:
- `start_date` (必填): 开始日期，格式 YYYY-MM-DD
- `end_date` (必填): 结束日期，格式 YYYY-MM-DD

#### 请求示例:
```
GET /doctors/1/schedules?start_date=2025-11-20&end_date=2025-11-30
```

#### 输出: 同 1.6 科室排班

---

### 1.8 获取门诊排班 Get: `/clinics/{clinic_id}/schedules`

获取指定门诊在日期范围内的所有排班。

#### Path 参数:
- `clinic_id`: 门诊ID

#### Query 参数:
- `start_date` (必填): 开始日期，格式 YYYY-MM-DD
- `end_date` (必填): 结束日期，格式 YYYY-MM-DD

#### 请求示例:
```
GET /clinics/1/schedules?start_date=2025-11-20&end_date=2025-11-30
```

#### 输出: 同 1.6 科室排班

---

### 1.9 获取医生排班列表（综合查询）Get: `/hospitals/schedules`

根据院区、科室、日期查询排班，支持未来7天默认查询。

#### Query 参数:
- `hospitalId` (可选): 院区ID
- `departmentId` (必填): 小科室ID
- `date` (可选): 日期，格式 YYYY-MM-DD，不传则查询未来7天

#### 请求示例:
```
GET /hospitals/schedules?departmentId=1
GET /hospitals/schedules?hospitalId=1&departmentId=1&date=2025-11-20
```

#### 输出:
```json
{
    \"code\": 0,
    \"message\": {
        \"schedules\": [
            {
                \"schedule_id\": 123,
                \"doctor_id\": 1,
                \"doctor_name\": \"张三\",
                \"doctor_title\": \"主治医师\",
                \"clinic_id\": 5,
                \"clinic_name\": \"心内科门诊\",
                \"clinic_type\": 0,
                \"area_id\": 1,
                \"date\": \"2025-11-20\",
                \"week_day\": \"三\",
                \"time_section\": \"上午\",
                \"slot_type\": \"普通\",
                \"total_slots\": 20,
                \"remaining_slots\": 15,
                \"status\": \"正常\",
                \"price\": 60.00
            }
        ]
    }
}
```

---

## 2. 预约管理接口（需要登录）

所有预约管理接口需要在请求头中携带 token：
```
Authorization: Bearer <token>
```

### 2.1 创建预约挂号 Post: `/appointments`

创建新的预约挂号订单。

#### Header:
```
Authorization: Bearer <token>
```

#### Body:
```json
{
    \"scheduleId\": 123,
    \"patientId\": 5,
    \"symptoms\": \"头痛发热\"
}
```

字段说明：
- `scheduleId` (必填): 排班ID
- `patientId` (必填): 患者ID（必须是当前用户的就诊人）
- `symptoms` (可选): 症状描述

#### 业务规则:
1. 预约成功后立即锁定号源（remaining_slots - 1）
2. 同一患者同一天同一排班只能挂1个号
3. 根据配置限制预约数量（默认8天内最多10个号，支持医生级别配置）
4. 检查号源是否充足

#### 输出:
```json
{
    \"code\": 0,
    \"message\": {
        \"id\": 1001,
        \"orderNo\": \"2025112012345678\",
        \"queueNumber\": 5,
        \"needPay\": true,
        \"payAmount\": 60.00,
        \"appointmentDate\": \"2025-11-20\",
        \"appointmentTime\": \"上午\",
        \"status\": \"pending\",
        \"paymentStatus\": \"pending\"
    }
}
```

#### 错误示例:
```json
{
    \"code\": 1001,
    \"message\": {
        \"error\": \"业务规则错误\",
        \"msg\": \"该时段号源已满\"
    }
}
```

```json
{
    \"code\": 1003,
    \"message\": {
        \"error\": \"业务规则错误\",
        \"msg\": \"8天内最多可挂10个号\"
    }
}
```

---

### 2.2 获取我的预约列表 Get: `/appointments`

获取当前用户的所有预约记录，支持状态过滤和分页。

#### Header:
```
Authorization: Bearer <token>
```

#### Query 参数:
- `status` (可选): 状态过滤，默认 \"all\"
  - `all`: 全部
  - `pending`: 待支付
  - `completed`: 已完成
  - `cancelled`: 已取消
- `page` (可选): 页码，默认 1
- `pageSize` (可选): 每页数量，默认 10

#### 请求示例:
```
GET /appointments
GET /appointments?status=pending&page=1&pageSize=20
```

#### 输出:
```json
{
    \"code\": 0,
    \"message\": {
        \"total\": 25,
        \"page\": 1,
        \"pageSize\": 10,
        \"list\": [
            {
                \"id\": 1001,
                \"orderNo\": \"2025112012345678\",
                \"hospitalId\": 1,
                \"hospitalName\": \"东院区\",
                \"departmentId\": 1,
                \"departmentName\": \"心内科\",
                \"doctorName\": \"张三\",
                \"doctorTitle\": \"主治医师\",
                \"scheduleId\": 123,
                \"appointmentDate\": \"2025-11-20\",
                \"appointmentTime\": \"上午\",
                \"patientName\": \"李四\",
                \"patientId\": 5,
                \"queueNumber\": null,
                \"price\": 60.00,
                \"status\": \"pending\",
                \"paymentStatus\": \"pending\",
                \"canCancel\": true,
                \"canReschedule\": false,
                \"createdAt\": \"2025-11-19 14:30:00\"
            }
        ]
    }
}
```

字段说明：
- `canCancel`: 是否可取消，根据配置动态计算（默认需在就诊前2小时）
- `canReschedule`: 是否可改约（暂未实现）
- `queueNumber`: 队列号（TODO: 实时计算）

#### 取消规则计算:
系统根据以下配置动态计算是否允许取消：
1. 从医生级别配置读取 `cancelHoursBefore`（默认2小时）
2. 从排班配置读取对应时段的开始时间：
   - 上午: `morningStart`（默认 08:00）
   - 下午: `afternoonStart`（默认 13:30）
   - 晚上: `eveningStart`（默认 18:00）
3. 计算截止时间 = 就诊开始时间 - cancelHoursBefore
4. 当前时间 < 截止时间 则 canCancel = true

---

### 2.3 取消预约 Put: `/appointments/{appointmentId}/cancel`

取消指定的预约订单。

#### Header:
```
Authorization: Bearer <token>
```

#### Path 参数:
- `appointmentId`: 预约订单ID

#### 请求示例:
```
PUT /appointments/1001/cancel
```

#### 取消规则:
- 根据配置动态计算截止时间（默认就诊前2小时）
- 超过时间需到医院挂号窗口办理
- 取消后释放号源（remaining_slots + 1）
- 已支付订单自动退款

#### 输出:
```json
{
    \"code\": 0,
    \"message\": {
        \"success\": true,
        \"refundAmount\": 60.00
    }
}
```

字段说明：
- `refundAmount`: 退款金额，未支付则为 null

#### 错误示例:
```json
{
    \"code\": 1006,
    \"message\": {
        \"error\": \"业务规则错误\",
        \"msg\": \"需在就诊时间前2小时取消,已超时请到医院窗口办理\"
    }
}
```

---

## 3. 配置说明

患者端挂号受以下配置影响（支持分级配置：DOCTOR > CLINIC > MINOR_DEPT > GLOBAL）：

### 挂号配置（registration）:
- `maxAppointmentsPerPeriod`: 时间段内最多预约数（默认 10）
- `appointmentPeriodDays`: 统计周期天数（默认 8）
- `cancelHoursBefore`: 取消提前小时数（默认 2）
- `advanceBookingDays`: 提前预约天数（默认 14）
- `noShowLimit`: 爽约次数限制（默认 3）
- `sameClinicInterval`: 同科室挂号间隔天数（默认 7）

### 排班配置（schedule）:
- `morningStart/End`: 上午时段时间（默认 08:00-12:00）
- `afternoonStart/End`: 下午时段时间（默认 13:30-17:30）
- `eveningStart/End`: 晚间时段时间（默认 18:00-21:00）
- `consultationDuration`: 单次就诊时长分钟（默认 15）
- `intervalTime`: 就诊间隔时间分钟（默认 5）

---


# 七、通用相关 API (`/common`)

该模块为通用文件（主要是医生请假凭证图片）上传接口，前端在提交请假前应先调用上传接口获取文件 `url` 与 `name`，再把这些对象放入请假申请的 `attachments` 字段。

### 1) POST `/common/upload`

**描述**: 单文件上传，返回 `{url, name}`，`url` 为可通过静态路由访问的相对路径（以 `/static/` 挂载）。

**请求**:
- Content-Type: `multipart/form-data`
- 字段名: `file`

**限制**:
- 支持图片类型: `jpg|jpeg|png|gif|bmp|webp`
- 单文件默认最大 5MB（可在服务端配置调整）

**保存路径示例**:
- 服务器路径: `backend/app/static/images/audit/{YYYY}/{MM}/{DD}/<generated_filename>.jpg`
- 访问路径: `/static/images/audit/2025/11/26/xxx.jpg`

**成功响应示例**:
```json
{
    "code": 0,
    "message": {
        "url": "static/images/audit/2025/11/26/1732619234_abc123.jpg",
        "name": "诊断证明.jpg"
    }
}
```

**实现要点**:
- 前端上传后得到的 `url` 可直接放入 `doctor/leave/apply` 的 `attachments` 字段
- 后端会把 `attachments` 原样保存到 `leave_audit.attachment_data_json`（JSON 列表）
- 在 `GET /doctor/leave/history` 返回时透传附件信息
- 静态文件通过 `app.mount('/static', StaticFiles(directory=...))` 暴露，部署时请确认容器/服务对该目录有读写权限

**示例前端流程**:
1. 调用 `POST /common/upload` 上传凭证图片，保存返回的 `{url, name}`
2. 构造请假请求体，将 `{url, name}` 列表放入 `attachments` 字段
3. 调用 `POST /doctor/leave/apply` 提交请假

**PowerShell 测试示例**:
```powershell
# 1. 先登录获取 token
$loginResponse = Invoke-RestMethod -Uri "http://localhost:8000/auth/staff/login" `
    -Method POST -ContentType "application/json" `
    -Body '{"identifier":"doctor001","password":"123456"}'
$token = $loginResponse.message

# 2. 上传图片文件
$headers = @{ "Authorization" = "Bearer $token" }
$form = @{ file = Get-Item "C:\path\to\image.jpg" }
$uploadResponse = Invoke-RestMethod -Uri "http://localhost:8000/common/upload" `
    -Method POST -Headers $headers -Form $form

# 3. 查看上传结果
$uploadResponse
# 输出: { "code": 0, "message": { "url": "static/images/...", "name": "image.jpg" } }
```

**错误示例**:
```json
{
    "code": 99,
    "message": {
        "error": "请求参数错误",
        "msg": "不支持的文件类型"
    }
}
```

---

### 2) GET `/common/visit-record/{visit_id}`

**描述**: 获取指定就诊记录的详细信息（用于前端渲染病历页面）。

**权限控制**:
- **管理员**：可查看所有病历
- **医生**：仅可查看自己接诊的病历
- **患者**：仅可查看自己的病历

**请求示例**:
```
GET /common/visit-record/194
Authorization: Bearer <token>
```

**成功响应示例**:
```json
{
    "code": 0,
    "message": {
        "basicInfo": {
            "name": "张三",
            "gender": "男",
            "age": 35
        },
        "recordData": {
            "id": "194",
            "outpatientNo": "000194",
            "visitDate": "2025-11-12 13:45",
            "department": "心内科",
            "doctorName": "李医生",
            "chiefComplaint": "胸闷气短3天",
            "presentIllness": "患者3天前无明显诱因出现胸闷...",
            "auxiliaryExam": "心电图示窦性心律",
            "diagnosis": "冠心病",
            "prescription": "阿司匹林 100mg qd\n硝酸甘油 0.5mg prn"
        }
    }
}
```

**错误响应示例**:
```json
{
    "code": 403,
    "message": {
        "error": "资源操作失败",
        "msg": "无权查看该病历"
    }
}
```

---

### 3) POST `/common/medical-record/{visit_id}/pdf`

**描述**: 为指定就诊记录生成病历单PDF文件。

**权限控制**: 同病历详情接口（管理员/接诊医生/患者本人）

**请求示例**:
```
POST /common/medical-record/194/pdf
Authorization: Bearer <token>
```

**成功响应示例**:
```json
{
    "code": 0,
    "message": {
        "url": "/static/pdf/medical_records/medical_record_194_20251127183300.pdf",
        "fileName": "病历单_张三_2025-11-12.pdf",
        "expireTime": "2025-12-04T18:33:00.000000Z"
    }
}
```

**字段说明**:
- `url`: PDF文件的静态访问路径（可直接在浏览器中打开或下载）
- `fileName`: 建议的文件名（前端下载时使用）
- `expireTime`: 文件过期时间（7天后，需配合定时清理任务）

**PDF样式特性**:
- 双logo显示（北京交通大学 + 校医院logo）
- 颜色系统：深蓝色主色调 (#1e3a8a)，红色强调色 (#c41e3a)
- 诊断内容特殊样式：浅蓝背景 + 深蓝左边框
- 医院印章：圆形红色边框 + 半透明文字
- 支持中文字体（Microsoft YaHei / SimHei）

**前端调用示例**:
```javascript
// 生成并下载PDF
async function downloadMedicalRecord(visitId) {
    // 1. 生成PDF
    const response = await fetch(`/common/medical-record/${visitId}/pdf`, {
        method: 'POST',
        headers: {
            'Authorization': `Bearer ${token}`
        }
    });
    
    const result = await response.json();
    
    if (result.code === 0) {
        // 2. 直接打开或下载PDF
        const pdfUrl = result.message.url;
        window.open(pdfUrl, '_blank');  // 新窗口打开
        
        // 或者下载
        const link = document.createElement('a');
        link.href = pdfUrl;
        link.download = result.message.fileName;
        link.click();
    }
}
```

**注意事项**:
1. **PDF文件清理**: PDF文件存储在 `backend/app/static/pdf/medical_records/`，设置7天过期时间，需配合定时任务清理过期文件
2. **静态文件访问**: 所有 `/static/` 路径的文件可直接通过HTTP访问，PDF下载无需额外认证（生成时已验证权限）
3. **性能优化**: PDF生成为同步操作，大量请求时考虑使用任务队列。Logo图片已嵌入PDF，单个文件约462KB

---

### 4) GET `/common/medical-record/{visit_id}/download`

**描述**: 下载病历单PDF（带权限验证的文件流式下载）。

**说明**: 推荐直接使用接口3返回的静态URL访问PDF。此接口保留用于需要服务端验证权限的场景。

**权限控制**: 同病历详情接口

**请求示例**:
```
GET /common/medical-record/194/download
Authorization: Bearer <token>
```

**响应**:
- 成功：返回PDF文件二进制流（`Content-Type: application/pdf`）
- 失败：返回JSON错误信息

---

