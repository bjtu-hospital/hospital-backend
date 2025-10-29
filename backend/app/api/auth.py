from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Union, Optional
from jose import jwt, JWTError
from datetime import timedelta
import logging
import time

from app.core.security import get_hash_pwd, verify_pwd, create_access_token
from app.schemas.user import user as UserSchema, PatientLogin, StaffLogin
from app.schemas.response import ResponseModel, AuthErrorResponse, UserRoleResponse, DeleteResponse, UpdateUserRoleResponse, UserAccessLogPageResponse
from app.db.base import get_db, redis, User, UserAccessLog
from app.core.config import settings
from app.core.exception_handler import AuthHTTPException

logger = logging.getLogger(__name__)

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer("/auth/swagger-login")


@router.post("/swagger-login", summary="Swagger UI 登录", tags=["Auth"])
async def swagger_login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    """
    用于 Swagger UI 的标准 OAuth2 登录接口（仅返回 access_token 和 token_type）
    支持手机号或工号登录
    """
    try:
        # 尝试患者端登录（手机号）
        user = await authenticate_patient(db, form_data.username, form_data.password)
        
        # 如果患者端登录失败，尝试员工端登录（工号）
        if not user:
            user = await authenticate_staff(db, form_data.username, form_data.password)
        
        if not user:
            raise HTTPException(status_code=401, detail="手机号/工号或密码错误")
        if user.is_deleted:
            raise HTTPException(status_code=401, detail="用户不存在或已被删除")
        if not user.is_verified:
            raise HTTPException(status_code=401, detail="邮箱未验证，请先完成邮箱验证")

        now_ts = int(time.time())
        login_ip = request.client.host if request.client else "unknown"
        # Swagger 登录使用的凭证可以是手机号或工号(form_data.username)，记录为凭证字符串
        logger.info(f"Swagger登录 - IP: {login_ip}, 凭证: {form_data.username}")
        access_token_expires = timedelta(minutes=settings.TOKEN_EXPIRE_TIME)

        token = create_access_token(
            data={
                "sub": str(user.user_id), 
                "login_time": now_ts, 
                "login_ip": login_ip,
                "user_type": user.user_type.value
            },
            expires_delta=access_token_expires
        )
        await redis.setex(f"token:{token}", settings.TOKEN_EXPIRE_TIME * 60, user.user_id)
        await redis.setex(f"user_token:{user.user_id}", settings.TOKEN_EXPIRE_TIME * 60, token)

        return {
            "access_token": token,
            "token_type": "bearer"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Swagger登录异常: {str(e)}")
        raise HTTPException(status_code=500, detail="内部服务异常")


@router.post("/patient/login", response_model=ResponseModel[Union[str, AuthErrorResponse]])
async def patient_login(login_data: PatientLogin, db: AsyncSession = Depends(get_db)):
    """患者端登录接口 - 使用手机号和密码进行认证"""
    user = await authenticate_patient(db, login_data.phonenumber, login_data.password)
    if not user:
        raise AuthHTTPException(
            code=settings.LOGIN_FAILED_CODE,
            msg="用户不存在或密码错误",
            status_code=401
        )
    
    # 生成并保存 token
    token = create_access_token({"sub": str(user.user_id)})
    
    # 将 token 保存到 Redis，设置过期时间
    try:
        # 清除旧 token
        old_token = await redis.get(f"user_token:{user.user_id}")
        if old_token:
            await redis.delete(f"token:{old_token}")
        
        # 设置新 token 映射
        await redis.set(f"token:{token}", str(user.user_id), ex=settings.TOKEN_EXPIRE_TIME * 60)
        await redis.set(f"user_token:{user.user_id}", token, ex=settings.TOKEN_EXPIRE_TIME * 60)
    except Exception as e:
        logger.error(f"保存 token 到 Redis 时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.LOGIN_FAILED_CODE,
            msg="登录失败，请稍后重试",
            status_code=500
        )
    
    return ResponseModel(code=0, message=token)


@router.post("/staff/login", response_model=ResponseModel[Union[str, AuthErrorResponse]])
async def staff_login(login_data: StaffLogin, db: AsyncSession = Depends(get_db)):
    """医生/管理员登录接口 - 使用工号和密码进行认证"""
    user = await authenticate_staff(db, login_data.identifier, login_data.password)
    if not user:
        raise AuthHTTPException(
            code=settings.LOGIN_FAILED_CODE,
            msg="用户不存在或密码错误",
            status_code=401
        )
    
    # 生成并保存 token
    token = create_access_token({"sub": str(user.user_id)})
    
    # 将 token 保存到 Redis，设置过期时间
    try:
        # 清除旧 token
        old_token = await redis.get(f"user_token:{user.user_id}")
        if old_token:
            await redis.delete(f"token:{old_token}")
        
        # 设置新 token 映射
        await redis.set(f"token:{token}", str(user.user_id), ex=settings.TOKEN_EXPIRE_TIME * 60)
        await redis.set(f"user_token:{user.user_id}", token, ex=settings.TOKEN_EXPIRE_TIME * 60)
    except Exception as e:
        logger.error(f"保存 token 到 Redis 时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.LOGIN_FAILED_CODE,
            msg="登录失败，请稍后重试",
            status_code=500
        )
    
    return ResponseModel(code=0, message=token)


async def authenticate_patient(db: AsyncSession, phonenumber: str, password: str):
    """患者端认证 - 通过手机号和密码验证用户登录"""
    try:
        result = await db.execute(select(User).where(and_(User.phonenumber == phonenumber, User.is_deleted == 0)))
        user = result.scalar_one_or_none()
        if not user:
            return None
        if not verify_pwd(password, user.hashed_password):
            return None
        return user
    except Exception as e:
        logger.error(f"患者认证时发生异常: {str(e)}")
        return None


async def authenticate_staff(db: AsyncSession, identifier: str, password: str):
    """医生/管理端认证 - 通过工号和密码验证用户登录"""
    try:
        result = await db.execute(select(User).where(and_(User.identifier == identifier, User.is_deleted == 0)))
        user = result.scalar_one_or_none()
        if not user:
            return None
        if not verify_pwd(password, user.hashed_password):
            return None
        return user
    except Exception as e:
        logger.error(f"员工认证时发生异常: {str(e)}")
        return None


async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    """根据 Token 获取当前用户信息 (请求头中带 Token)"""
    try:
        if not token:
            raise AuthHTTPException(
                code=settings.TOKEN_INVALID_CODE,
                msg="Token无效或已失效",
                status_code=401
            )

        try:
            user_id = await redis.get(f"token:{token}")
        except Exception as e:
            logger.error(f"访问 Redis 时发生异常: {e}")
            raise AuthHTTPException(
                code=settings.TOKEN_INVALID_CODE,
                msg="Token 无效或已失效",
                status_code=401
            )

        if not user_id:
            raise AuthHTTPException(
                code=settings.TOKEN_INVALID_CODE,
                msg="Token 无效或已失效",
                status_code=401
            )

        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.TOKEN_ALGORITHM])
            sub = payload.get("sub")
            if sub is None or str(sub) != str(user_id):
                raise AuthHTTPException(
                    code=settings.TOKEN_INVALID_CODE,
                    msg="Token 无效或已失效",
                    status_code=401
                )
        except JWTError:
            raise AuthHTTPException(
                code=settings.TOKEN_INVALID_CODE,
                msg="Token 无效或已失效",
                status_code=401
            )

        result = await db.execute(select(User).where(and_(User.user_id == int(sub), User.is_deleted == 0)))
        db_user = result.scalar_one_or_none()
        if not db_user:
            raise AuthHTTPException(
                code=settings.TOKEN_INVALID_CODE,
                msg="Token 无效或用户不存在",
                status_code=401
            )
        return UserSchema.from_orm(db_user)
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取当前用户时发生未处理异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.TOKEN_INVALID_CODE,
            msg="Token 无效或已失效",
            status_code=401
        )


@router.get("/me", response_model=ResponseModel[Union[UserRoleResponse, AuthErrorResponse]])
async def get_me(current_user: UserSchema = Depends(get_current_user)):
    """获取当前用户角色,Token无效时抛出统一异常"""
    try:
        role = "admin" if getattr(current_user, "is_admin", False) else "user"
        role_response = UserRoleResponse(role=role)
        return ResponseModel(code=0, message=role_response)
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取当前用户角色时发生未处理异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.TOKEN_INVALID_CODE,
            msg="Token无效或已失效",
            status_code=401
        )


@router.delete("/users/{user_id}", response_model=ResponseModel[Union[DeleteResponse, AuthErrorResponse]])
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db), current_user: UserSchema = Depends(get_current_user)):
    """删除用户，只允许管理员进行删除，且管理员不能删除其他管理员"""
    try:
        # (此接口已移入 `app.api.admin`，请在 admin 模块中调用)
        raise AuthHTTPException(
            code=settings.INSUFFICIENT_AUTHORITY_CODE,
            msg="此接口已移至 admin 模块",
            status_code=410
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除用户时发生未处理异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.USER_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/users/{user_id}/role", response_model=ResponseModel[Union[UpdateUserRoleResponse, AuthErrorResponse]])
async def update_user_role(
    user_id: int,
    role_data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """修改用户角色,目前只允许管理员将普通用户提升为管理员,管理员间不能互相更改"""
    try:
        # (此接口已移入 `app.api.admin`，请在 admin 模块中调用)
        raise AuthHTTPException(
            code=settings.INSUFFICIENT_AUTHORITY_CODE,
            msg="此接口已移至 admin 模块",
            status_code=410
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新用户角色时发生未处理异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.USER_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/user-logs", response_model=ResponseModel[UserAccessLogPageResponse])
async def get_user_logs(
    page: int = 1,
    page_size: int = 20,
    user_id: Optional[int] = None,
    ip: Optional[str] = None,
    action: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    管理员获取用户访问日志，支持多条件过滤和分页，按时间倒序。
    """
    try:
        # (此接口已移入 `app.api.admin`，请在 admin 模块中调用)
        raise AuthHTTPException(
            code=settings.INSUFFICIENT_AUTHORITY_CODE,
            msg="此接口已移至 admin 模块",
            status_code=410
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户访问日志异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.post("/register", response_model=ResponseModel[Union[str, AuthErrorResponse]])
async def register_patient(
    phonenumber: str,
    password: str,
    name: str,
    email: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """患者注册接口"""
    try:
        # 检查手机号是否已被注册
        result = await db.execute(select(User).where(User.phonenumber == phonenumber))
        if result.scalar_one_or_none():
            raise AuthHTTPException(
                code=settings.REGISTER_FAILED_CODE,
                msg="该手机号已被注册",
                status_code=400
            )

        # 创建新用户
        new_user = User(
            phonenumber=phonenumber,
            name=name,
            hashed_password=get_hash_pwd(password),
            email=email,
            is_admin=False
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        # 生成并返回 token
        token = create_access_token({"sub": str(new_user.user_id)})
        
        # 保存 token 到 Redis
        await redis.set(f"token:{token}", str(new_user.user_id), ex=settings.TOKEN_EXPIRE_TIME * 60)
        await redis.set(f"user_token:{new_user.user_id}", token, ex=settings.TOKEN_EXPIRE_TIME * 60)

        return ResponseModel(code=0, message=token)
    except AuthHTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"注册用户时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REGISTER_FAILED_CODE,
            msg="注册失败，请稍后重试",
            status_code=500
        )


@router.post("/logout")
async def logout(current_user: UserSchema = Depends(get_current_user)):
    """用户登出接口"""
    try:
        # 清除 Redis 中的 token
        token = await redis.get(f"user_token:{current_user.user_id}")
        if token:
            await redis.delete(f"token:{token}")
            await redis.delete(f"user_token:{current_user.user_id}")
        return ResponseModel(code=0, message="登出成功")
    except Exception as e:
        logger.error(f"用户登出时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.DATA_DELETE_FAILED_CODE,
            msg="登出失败，请稍后重试",
            status_code=500
        )








