# BJTU校医院挂号系统(Fake)后端

## 环境配置

1. 复制 `.env.example` 文件为 `.env`
```bash
cp .env.example .env
```

2. 编辑 `.env` 文件，填入实际的配置信息：
   - 数据库连接信息
   - 邮箱配置

## 运行需下载的库

```
pip install -r requirements.txt
```

## 后端运行方式

将目录跳转至后端根目录后运行下面的指令(reload表热更新可删)

```
uvicorn app.main:app --reload
```


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
