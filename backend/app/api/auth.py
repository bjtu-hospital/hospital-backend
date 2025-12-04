from fastapi import APIRouter, Depends, HTTPException, Request, Body
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
from app.models.doctor import Doctor
from app.models.minor_department import MinorDepartment
from app.models.user import UserType
from app.models.patient import Patient, PatientType, Gender
from app.models.patient_relation import PatientRelation
from app.services.risk_detection_service import risk_detection_service
from app.models.user_ban import UserBan
from app.core.config import settings
from app.core.exception_handler import AuthHTTPException, BusinessHTTPException, ResourceHTTPException
from app.services.sms_service import SMSService
import os
import mimetypes
import base64

logger = logging.getLogger(__name__)

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer("/auth/swagger-login", auto_error=False)


@router.post("/sms/send-code", summary="发送手机号验证码", tags=["Auth"]) 
async def send_sms_code(phone: str = Body(..., embed=True)):
    """发送验证码到指定手机号，受限流与TTL控制"""
    try:
        result = await SMSService.send_code(phone)
        return ResponseModel(code=0, message=result)
    except BusinessHTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"发送短信验证码异常: {e}")
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="短信发送失败", status_code=500)


@router.post("/sms/verify-code", summary="校验手机号验证码", tags=["Auth"]) 
async def verify_sms_code(phone: str = Body(..., embed=True), code: str = Body(..., embed=True)):
    """校验验证码，成功后在Redis写入 verified 标记，窗口期内可注册"""
    try:
        result = await SMSService.verify_code(phone, code)
        return ResponseModel(code=0, message=result)
    except BusinessHTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"校验短信验证码异常: {e}")
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="验证码校验失败", status_code=500)

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
            raise HTTPException(status_code=401, detail="账号未验证，请先完成验证")
        
        # 检查用户是否被封禁
        ban_result = await db.execute(
            select(UserBan).where(
                and_(
                    UserBan.user_id == user.user_id,
                    UserBan.is_active == True  # noqa: E712
                )
            )
        )
        active_ban = ban_result.scalar_one_or_none()
        if active_ban:
            # 检查封禁类型是否影响登录
            if active_ban.ban_type in ('login', 'all'):
                ban_msg = f"账号已被封禁，原因: {active_ban.reason or '未说明'}";
                if active_ban.ban_until:
                    ban_msg += f"，封禁至: {active_ban.ban_until.strftime('%Y-%m-%d %H:%M:%S')}"
                else:
                    ban_msg += "，永久封禁"
                raise HTTPException(status_code=403, detail=ban_msg)

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
async def patient_login(login_data: PatientLogin, request: Request, db: AsyncSession = Depends(get_db)):
    """患者端登录接口 - 使用手机号和密码进行认证"""
    user = await authenticate_patient(db, login_data.phonenumber, login_data.password)
    if not user:
        raise AuthHTTPException(
            code=settings.LOGIN_FAILED_CODE,
            msg="用户不存在或密码错误",
            status_code=401
        )
    
    # 检查用户是否已验证
    if not user.is_verified:
        raise AuthHTTPException(
            code=settings.LOGIN_FAILED_CODE,
            msg="账号未验证，请先完成手机号验证",
            status_code=401
        )
    
    # 检查用户是否被封禁
    ban_result = await db.execute(
        select(UserBan).where(
            and_(
                UserBan.user_id == user.user_id,
                UserBan.is_active == True  
            )
        )
    )
    active_ban = ban_result.scalar_one_or_none()
    if active_ban:
        # 检查封禁类型是否影响登录
        if active_ban.ban_type in ('login', 'all'):
            ban_msg = f"账号已被封禁，原因: {active_ban.reason or '未说明'}";
            if active_ban.ban_until:
                ban_msg += f"，封禁至: {active_ban.ban_until.strftime('%Y-%m-%d %H:%M:%S')}"
            else:
                ban_msg += "，永久封禁"
            raise AuthHTTPException(
                code=settings.LOGIN_FAILED_CODE,
                msg=ban_msg,
                status_code=403
            )
    # 登录风险检测(成功认证后执行)
    try:
        await risk_detection_service.detect_login_risk(db, user.user_id, request.client.host if request.client else "unknown")
    except Exception:
        pass

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
async def staff_login(login_data: StaffLogin, request: Request, db: AsyncSession = Depends(get_db)):
    """医生/管理员登录接口 - 使用工号和密码进行认证"""
    user = await authenticate_staff(db, login_data.identifier, login_data.password)
    if not user:
        raise AuthHTTPException(
            code=settings.LOGIN_FAILED_CODE,
            msg="用户不存在或密码错误",
            status_code=401
        )
    
    # 检查用户是否已验证
    if not user.is_verified:
        raise AuthHTTPException(
            code=settings.LOGIN_FAILED_CODE,
            msg="账号未验证，请联系管理员",
            status_code=401
        )
    
    # 检查用户是否被封禁
    ban_result = await db.execute(
        select(UserBan).where(
            and_(
                UserBan.user_id == user.user_id,
                UserBan.is_active == True  # noqa: E712
            )
        )
    )
    active_ban = ban_result.scalar_one_or_none()
    if active_ban:
        # 检查封禁类型是否影响登录
        if active_ban.ban_type in ('login', 'all'):
            ban_msg = f"账号已被封禁，原因: {active_ban.reason or '未说明'}";
            if active_ban.ban_until:
                ban_msg += f"，封禁至: {active_ban.ban_until.strftime('%Y-%m-%d %H:%M:%S')}"
            else:
                ban_msg += "，永久封禁"
            raise AuthHTTPException(
                code=settings.LOGIN_FAILED_CODE,
                msg=ban_msg,
                status_code=403
            )
    # 登录风险检测(成功认证后执行)
    try:
        await risk_detection_service.detect_login_risk(db, user.user_id, request.client.host if request.client else "unknown")
    except Exception:
        pass

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


@router.get("/user-info", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def get_user_info(current_user: UserSchema = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """多角色通用用户信息接口

    返回结构包含患者与医生信息（按角色返回可用字段）：
    - patient: 按 USER-API 约定返回患者个人信息字段
    - doctor: 若当前用户绑定医生记录则返回医生资料，否则为 None
    """
    try:
        # 查询医生信息（如有）
        doctor_res = await db.execute(select(Doctor).where(Doctor.user_id == current_user.user_id))
        doctor = doctor_res.scalar_one_or_none()
        dept = None
        photo_base64 = None
        photo_mime = None
        if doctor:
            dept_res = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == doctor.dept_id))
            dept = dept_res.scalar_one_or_none()
            # 读取并编码医生照片（如果存在）
            if doctor.photo_path:
                base_dir = os.path.dirname(os.path.dirname(__file__))  # .../app
                rel_path = doctor.photo_path.lstrip("/")
                if rel_path.startswith("app/"):
                    rel_path = rel_path[4:]
                fs_path = os.path.normpath(os.path.join(base_dir, rel_path))
                if os.path.exists(fs_path) and os.path.isfile(fs_path):
                    mime_type, _ = mimetypes.guess_type(fs_path)
                    if not mime_type:
                        mime_type = "application/octet-stream"
                    try:
                        with open(fs_path, "rb") as f:
                            bdata = f.read()
                            photo_base64 = base64.b64encode(bdata).decode("utf-8")
                            photo_mime = mime_type
                    except Exception:
                        photo_base64 = None
                        photo_mime = None

        # 查询患者信息（如有）
        patient_res = await db.execute(select(Patient).where(Patient.user_id == current_user.user_id))
        patient = patient_res.scalar_one_or_none()

        # 计算年龄
        age = None
        if patient and patient.birth_date:
            from datetime import date as date_type
            today = date_type.today()
            age = today.year - patient.birth_date.year
            if (today.month, today.day) < (patient.birth_date.month, patient.birth_date.day):
                age -= 1

        # 敏感信息脱敏
        phone_masked = None
        if getattr(current_user, "phonenumber", None):
            phone = str(current_user.phonenumber)
            if len(phone) >= 11:
                phone_masked = phone[:3] + "****" + phone[-4:]
            elif len(phone) >= 7:
                phone_masked = phone[:3] + "****" + phone[-4:]
            else:
                phone_masked = "*" * len(phone)

        # 身份证脱敏
        idcard_masked = None
        id_card_val = getattr(patient, "id_card", None) if patient else None
        if id_card_val and len(id_card_val) >= 10:
            idcard_masked = id_card_val[:6] + "********" + id_card_val[-4:]
        elif id_card_val:
            idcard_masked = id_card_val
        
        # 学号/工号
        identifier_val = getattr(patient, "identifier", None) if patient else None

        # 构建患者信息（遵循 USER-API 字段命名）
        patient_info = None
        if patient:
            patient_info = {
                "id": str(patient.patient_id),
                "identifier": identifier_val,  # 学号/工号/证件号
                "phonenumber": current_user.phonenumber,
                "realName": patient.name,
                "idCard": idcard_masked,  # 身份证号（已脱敏）
                "email": getattr(current_user, "email", None),
                "gender": (patient.gender.value if patient.gender else "未知"),
                "birthDate": (patient.birth_date.strftime("%Y-%m-%d") if patient.birth_date else None),
                "patientType": (patient.patient_type if isinstance(patient.patient_type, str) else getattr(patient.patient_type, "value", None)),
                "avatar": None,
                "verified": bool(getattr(patient, "is_verified", False)),
                "createdAt": (patient.create_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(patient, "create_time", None) else None),
                "updatedAt": None,
                # 额外可选：返回脱敏后的手机号与证件信息，便于前端直接展示
                "maskedInfo": {
                    "phone": phone_masked,
                    "idCard": idcard_masked
                },
                "age": age
            }

        # 构建医生信息（保持原有字段）
        doctor_info = None
        if doctor:
            doctor_info = {
                "id": doctor.doctor_id,
                "name": doctor.name,
                "department": dept.name if dept else None,
                "department_id": doctor.dept_id,
                "hospital": "主院区",
                "title": doctor.title,
                "is_department_head": bool(getattr(doctor, "is_department_head", False)),
                "photo_mime": photo_mime,
                "photo_base64": photo_base64
            }

        return ResponseModel(code=0, message={
            "patient": patient_info,
            "doctor": doctor_info
        })
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户信息异常: {e}")
        raise BusinessHTTPException(code=settings.USER_GET_FAILED_CODE, msg="获取用户信息失败", status_code=500)


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
    phonenumber: str = Body(...),
    password: str = Body(...),
    name: str = Body(...),
    email: Optional[str] = Body(None),
    gender: Optional[str] = Body(None),
    birth_date: Optional[str] = Body(None),
    db: AsyncSession = Depends(get_db)
):
    """患者注册接口
    
    新注册患者默认为校外人员（EXTERNAL），身份认证由认证模块单独处理。
    """
    try:
        # 校验手机号是否通过短信验证（窗口期内）
        try:
            verified = await redis.get(f"sms:verified:{phonenumber}")
        except Exception:
            verified = None
        if not verified:
            raise BusinessHTTPException(
                code=settings.REGISTER_FAILED_CODE,
                msg="手机号未验证或验证已过期，请先完成验证码验证",
                status_code=400
            )
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
            user_type=UserType.EXTERNAL,
            is_verified=True
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        # 一次性消费 verified 标记
        try:
            await redis.delete(f"sms:verified:{phonenumber}")
        except Exception:
            pass

        # 生成并返回 token
        token = create_access_token({"sub": str(new_user.user_id)})
        
        # 保存 token 到 Redis
        await redis.set(f"token:{token}", str(new_user.user_id), ex=settings.TOKEN_EXPIRE_TIME * 60)
        await redis.set(f"user_token:{new_user.user_id}", token, ex=settings.TOKEN_EXPIRE_TIME * 60)

        # 必须创建 Patient 记录（无论是否提供可选信息）
        # 注册必然产生一条患者记录，确保 user_id 总是被正确设置
        try:
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

                # 创建 Patient 记录，默认身份为 EXTERNAL（校外人员）
                # 身份认证由认证模块单独处理
                patient = Patient(
                    user_id=new_user.user_id,
                    name=name,
                    gender=(g.value if g else Gender.UNKNOWN.value),
                    birth_date=bdate,
                    patient_type=PatientType.EXTERNAL.value,  # 默认为校外人员
                    identifier=None,  # 注册时不设置，由认证模块处理
                    is_verified=True,
                    create_time=date.today()
                )
                db.add(patient)
                await db.commit()
                await db.refresh(patient)

                # 创建“本人”就诊关系，并将默认就诊人设置为本人（写入 Redis）
                try:
                    # 避免重复创建
                    rel_exist_res = await db.execute(
                        select(PatientRelation).where(
                            and_(
                                PatientRelation.user_patient_id == patient.patient_id,
                                PatientRelation.related_patient_id == patient.patient_id
                            )
                        )
                    )
                    rel_exist = rel_exist_res.scalar_one_or_none()
                    if not rel_exist:
                        self_rel = PatientRelation(
                            user_patient_id=patient.patient_id,
                            related_patient_id=patient.patient_id,
                            relation_type="本人",
                            is_default=False,
                            remark=None
                        )
                        db.add(self_rel)
                        await db.commit()

                    # 以 Redis 为权威设置默认就诊人
                    await redis.set(f"user_default_patient:{patient.patient_id}", str(patient.patient_id))
                except Exception:
                    logger.exception("注册时创建本人关系或设置默认就诊人失败（已忽略以不影响注册）")
        except Exception:
            # 如果创建 Patient 失败，不影响用户注册，记录错误并回滚本次子事务以清理 Session 状态
            logger.exception("创建 Patient 记录失败，已回滚 Patient 子记录，但用户已创建")
            try:
                await db.rollback()
            except Exception:
                # 忽略回滚期间的错误，至少记录日志
                logger.exception("回滚 Patient 子记录时发生异常")

        # 兜底：若前面未创建 Patient，也保证至少创建本人 Patient 与关系，并设置默认
        try:
            ensure_res = await db.execute(select(Patient).where(Patient.user_id == new_user.user_id))
            ensured_patient = ensure_res.scalar_one_or_none()
            if not ensured_patient:
                ensured_patient = Patient(
                    user_id=new_user.user_id,
                    name=name,
                    gender=Gender.UNKNOWN.value,
                    birth_date=None,
                    patient_type=PatientType.EXTERNAL.value,  # 默认为校外人员
                    identifier=None,  # 注册时不设置，由认证模块处理
                    is_verified=True,
                    create_time=date.today()
                )
                db.add(ensured_patient)
                await db.commit()
                await db.refresh(ensured_patient)

            # 创建本人关系（若不存在）
            rel_exist_res2 = await db.execute(
                select(PatientRelation).where(
                    and_(
                        PatientRelation.user_patient_id == ensured_patient.patient_id,
                        PatientRelation.related_patient_id == ensured_patient.patient_id
                    )
                )
            )
            if not rel_exist_res2.scalar_one_or_none():
                self_rel2 = PatientRelation(
                    user_patient_id=ensured_patient.patient_id,
                    related_patient_id=ensured_patient.patient_id,
                    relation_type="本人",
                    is_default=False,
                    remark=None
                )
                db.add(self_rel2)
                await db.commit()

            # 默认就诊人写入 Redis（若原本未设置）
            try:
                cache_key = f"user_default_patient:{ensured_patient.patient_id}"
                cached = await redis.get(cache_key)
                if not cached:
                    await redis.set(cache_key, str(ensured_patient.patient_id))
            except Exception:
                logger.warning("注册兜底阶段写入默认就诊人缓存失败（忽略）")
        except Exception:
            logger.exception("注册兜底阶段创建 Patient/本人关系失败（忽略继续返回 token）")

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


@router.put("/profile", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_profile(
    realName: Optional[str] = Body(None),
    email: Optional[str] = Body(None),
    gender: Optional[str] = Body(None),
    birthDate: Optional[str] = Body(None),
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新当前登录用户的个人信息
    
    允许用户更新以下字段（可选）：
    - realName: 真实姓名
    - email: 邮箱
    - gender: 性别（男/女/未知）
    - birthDate: 出生日期（YYYY-MM-DD格式）
    """
    try:
        # 查询患者记录
        patient_res = await db.execute(select(Patient).where(Patient.user_id == current_user.user_id))
        patient = patient_res.scalar_one_or_none()
        
        if not patient:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="患者记录不存在，请先完成注册",
                status_code=404
            )
        
        # 更新姓名
        if realName is not None:
            patient.name = realName
        
        # 更新邮箱（更新 User 表）
        if email is not None:
            # 检查邮箱是否已被其他用户使用
            email_check = await db.execute(
                select(User).where(
                    and_(
                        User.email == email,
                        User.user_id != current_user.user_id
                    )
                )
            )
            if email_check.scalar_one_or_none():
                raise BusinessHTTPException(
                    code=settings.DATA_UPDATE_FAILED_CODE,
                    msg="该邮箱已被其他用户使用",
                    status_code=400
                )
            
            user_res = await db.execute(select(User).where(User.user_id == current_user.user_id))
            user = user_res.scalar_one_or_none()
            if user:
                user.email = email
        
        # 更新性别
        if gender is not None:
            g = str(gender).strip()
            if g in ("男", "MALE", "male", "Male"):
                patient.gender = Gender.MALE.value
            elif g in ("女", "FEMALE", "female", "Female"):
                patient.gender = Gender.FEMALE.value
            elif g in ("未知", "UNKNOWN", "unknown", "Unknown"):
                patient.gender = Gender.UNKNOWN.value
            else:
                raise BusinessHTTPException(
                    code=settings.DATA_UPDATE_FAILED_CODE,
                    msg="性别参数无效，请使用：男/女/未知",
                    status_code=400
                )
        
        # 更新出生日期
        if birthDate is not None:
            try:
                from datetime import datetime as _dt
                bdate = _dt.strptime(birthDate, "%Y-%m-%d").date()
                patient.birth_date = bdate
            except ValueError:
                raise BusinessHTTPException(
                    code=settings.DATA_UPDATE_FAILED_CODE,
                    msg="出生日期格式错误，请使用 YYYY-MM-DD 格式",
                    status_code=400
                )
        
        await db.commit()
        await db.refresh(patient)
        
        return ResponseModel(code=0, message={
            "detail": "个人信息更新成功",
            "updatedFields": {
                "realName": patient.name if realName is not None else None,
                "email": email if email is not None else None,
                "gender": patient.gender if gender is not None else None,
                "birthDate": patient.birth_date.strftime("%Y-%m-%d") if birthDate is not None and patient.birth_date else None
            }
        })
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"更新用户信息时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_UPDATE_FAILED_CODE,
            msg="更新用户信息失败，请稍后重试",
            status_code=500
        )








