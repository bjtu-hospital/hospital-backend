from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Union, Optional
from jose import jwt, JWTError
from datetime import timedelta, date
import logging
import time

from app.core.security import get_hash_pwd, verify_pwd, create_access_token
from app.schemas.user import user as UserSchema, PatientLogin, StaffLogin
from app.schemas.response import ResponseModel, AuthErrorResponse, UserRoleResponse, DeleteResponse, UpdateUserRoleResponse, UserAccessLogPageResponse, AdminRegisterResponse
from app.db.base import get_db, redis, User, UserAccessLog, Administrator
from app.models.user import UserType
from app.models.patient import Patient, PatientType, Gender
from app.core.config import settings
from app.core.exception_handler import AuthHTTPException, BusinessHTTPException, ResourceHTTPException

logger = logging.getLogger(__name__)

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer("/auth/swagger-login", auto_error=False)

async def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> Optional[UserSchema]:
    """
    可选的用户认证（用于支持首次创建管理员时无需认证）
    - 如果有 token 且有效，返回用户
    - 如果无 token 或 token 无效，返回 None（不抛异常）
    """
    if not token:
        return None
        
    try:
        user_id = await redis.get(f"token:{token}")
        if not user_id:
            return None
            
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.TOKEN_ALGORITHM])
            sub = payload.get("sub")
            if sub is None or str(sub) != str(user_id):
                return None
        except JWTError:
            return None

        result = await db.execute(select(User).where(and_(User.user_id == int(user_id), User.is_deleted == 0)))
        db_user = result.scalar_one_or_none()
        if not db_user:
            return None
            
        return UserSchema.from_orm(db_user)
    except Exception as e:
        logger.error(f"获取当前用户时发生异常（可选认证）: {str(e)}")
        return None


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
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
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
        raise BusinessHTTPException(
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
        raise BusinessHTTPException(
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
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
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
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
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
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除用户时发生未处理异常: {str(e)}")
        raise BusinessHTTPException(
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
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新用户角色时发生未处理异常: {str(e)}")
        raise BusinessHTTPException(
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
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户访问日志异常: {str(e)}")
        raise BusinessHTTPException(
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
    patient_type: Optional[str] = None,
    gender: Optional[str] = None,
    birth_date: Optional[str] = None,
    student_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """患者注册接口"""
    try:
        # 检查手机号是否已被注册
        result = await db.execute(select(User).where(User.phonenumber == phonenumber))
        if result.scalar_one_or_none():
            raise BusinessHTTPException(
                code=settings.REGISTER_FAILED_CODE,
                msg="该手机号已被注册",
                status_code=400
            )

        # 创建新用户
        new_user = User(
            phonenumber=phonenumber,
            hashed_password=get_hash_pwd(password),
            email=email,
            is_admin=False,
            # 新注册的患者默认不是管理员，user_type 暂设为 EXTERNAL
            user_type=UserType.EXTERNAL
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        # 生成并返回 token
        token = create_access_token({"sub": str(new_user.user_id)})
        
        # 保存 token 到 Redis
        await redis.set(f"token:{token}", str(new_user.user_id), ex=settings.TOKEN_EXPIRE_TIME * 60)
        await redis.set(f"user_token:{new_user.user_id}", token, ex=settings.TOKEN_EXPIRE_TIME * 60)

        # 若提供了患者详细信息，则同时创建 Patient 记录
        try:
            create_patient = any([patient_type, gender, birth_date, student_id])
            if create_patient:
                # 解析 patient_type
                p_type = None
                if patient_type:
                    pt = str(patient_type).strip()
                    # 支持中文/英文输入（如 '学生' 或 'STUDENT'）
                    if pt in ("学生", "STUDENT", "student", "Student"):
                        p_type = PatientType.STUDENT
                    elif pt in ("教师", "TEACHER", "teacher", "Teacher"):
                        p_type = PatientType.TEACHER
                    elif pt in ("职工", "STAFF", "staff", "Staff"):
                        p_type = PatientType.STAFF
                # 解析 gender
                g = None
                if gender:
                    gg = str(gender).strip()
                    if gg in ("男", "MALE", "male", "Male"):
                        g = Gender.MALE
                    elif gg in ("女", "FEMALE", "female", "Female"):
                        g = Gender.FEMALE
                    else:
                        g = Gender.UNKNOWN

                # 解析 birth_date (YYYY-MM-DD)
                bdate = None
                if birth_date:
                    try:
                        from datetime import datetime as _dt
                        bdate = _dt.strptime(birth_date, "%Y-%m-%d").date()
                    except Exception:
                        bdate = None

                # 为避免 Enum 存储值/名称与数据库已有 ENUM 定义不一致，直接写入枚举的 value（中文描述）
                # 插入数据库时传入 Enum 成员（SQLAlchemy 会处理到数据库的表示），
                # 避免直接写入枚举的 value 导致与数据库列定义不一致的问题。
                # 将解析结果存入数据库时使用枚举的 value（存储为数据库定义的字符串），
                # 以兼容数据库中 ENUM 的定义（数据库中使用中文值：'男','女','未知' 等）。
                patient = Patient(
                    user_id=new_user.user_id,
                    name=name,
                    gender=(g.value if g else Gender.UNKNOWN.value),
                    birth_date=bdate,
                    patient_type=(p_type.value if p_type else PatientType.STUDENT.value),
                    student_id=student_id,
                    is_verified=False,
                    create_time=date.today()
                )
                db.add(patient)
                await db.commit()
                await db.refresh(patient)
        except Exception:
            # 如果创建 Patient 失败，不影响用户注册，记录错误并回滚本次子事务以清理 Session 状态
            logger.exception("创建 Patient 记录失败，已回滚 Patient 子记录，但用户已创建")
            try:
                await db.rollback()
            except Exception:
                # 忽略回滚期间的错误，至少记录日志
                logger.exception("回滚 Patient 子记录时发生异常")

        return ResponseModel(code=0, message=token)
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"注册用户时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REGISTER_FAILED_CODE,
            msg="注册失败，请稍后重试",
            status_code=500
        )


@router.post("/register-admin", response_model=ResponseModel[Union[AdminRegisterResponse, AuthErrorResponse]])
async def register_admin(
    identifier: str,
    password: str,
    name: str,
    email: Optional[str] = None,
    job_title: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[UserSchema] = Depends(get_current_user_optional)
):
    """管理员注册接口（开发/运维用）

    逻辑：
    - 如果系统中尚无 Administrator 记录（首次引导），允许无认证创建第一个管理员（bootstrap）。
    - 否则，仅允许已认证且具有 is_admin=True 的用户创建新管理员。
    """
    try:
        # 检查是否已有管理员存在
        result = await db.execute(select(Administrator))
        exists = result.first() is not None

        # 如果已有管理员，要求调用者为管理员
        if exists:
            if not current_user:
                raise AuthHTTPException(
                    code=settings.INSUFFICIENT_AUTHORITY_CODE,
                    msg="仅管理员可创建新管理员",
                    status_code=403
                )

            if not getattr(current_user, "is_admin", False):
                raise AuthHTTPException(
                    code=settings.INSUFFICIENT_AUTHORITY_CODE,
                    msg="仅管理员可创建新管理员",
                    status_code=403
                )

        # 校验 identifier / email 唯一性
        if identifier:
            res = await db.execute(select(User).where(User.identifier == identifier))
            if res.scalar_one_or_none():
                raise BusinessHTTPException(
                    code=settings.REGISTER_FAILED_CODE,
                    msg="该工号(identifier)已被占用",
                    status_code=400
                )
        if email:
            res2 = await db.execute(select(User).where(User.email == email))
            if res2.scalar_one_or_none():
                raise BusinessHTTPException(
                    code=settings.REGISTER_FAILED_CODE,
                    msg="该邮箱已被占用",
                    status_code=400
                )

        # 创建 User
        new_user = User(
            identifier=identifier,
            hashed_password=get_hash_pwd(password),
            email=email,
            is_admin=True,
            is_verified=True,
            user_type=UserType.ADMIN
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        # 创建 Administrator 详细信息
        admin = Administrator(
            user_id=new_user.user_id,
            name=name,
            job_title=job_title
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

        return ResponseModel(code=0, message=AdminRegisterResponse(detail=f"成功创建管理员 {name}"))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"创建管理员时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REGISTER_FAILED_CODE,
            msg="创建管理员失败，请稍后重试",
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
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"用户登出时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_DELETE_FAILED_CODE,
            msg="登出失败，请稍后重试",
            status_code=500
        )








