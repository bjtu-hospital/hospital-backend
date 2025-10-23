from fastapi import APIRouter,Depends,HTTPException,status,Request, Cookie,Body, Path
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from sqlalchemy import select,insert,and_, or_, desc
from fastapi.security import OAuth2PasswordBearer,HTTPBearer, OAuth2PasswordRequestForm
from datetime import timedelta,datetime
from typing import Union, Optional
from jose import jwt, JWTError
import logging
import time
from dateutil.parser import parse as parse_datetime
from io import BytesIO
import random
import string
from PIL import Image, ImageDraw, ImageFont
import json

from app.core.security import get_hash_pwd, verify_pwd, create_access_token, generate_email_verify_token, verify_email_token, send_email
from app.schemas.user import userCreate,user as UserSchema,Token,UserUpdate,UserRoleUpdate, PasswordUpdate, PasswordChangeConfirmInput, PatientLogin, StaffLogin
from app.schemas.admin import AdminRegister, MajorDepartmentCreate, MajorDepartmentUpdate, MinorDepartmentCreate, MinorDepartmentUpdate, DoctorCreate, DoctorUpdate, DoctorAccountCreate, DoctorTransferDepartment
from app.schemas.response import (
    ResponseModel, AuthErrorResponse, LoginResponse, UsersListResponse, SingleUserResponse, RegisterResponse, TokenErrorResponse, UpdateUserResponse, UserRoleResponse, DeleteResponse, UpdateUserRoleResponse, UserAccessLogPageResponse, UserAccessLogItem, PasswordChangeRequestResponse, PasswordChangeConfirmResponse, AdminRegisterResponse, MajorDepartmentListResponse, MinorDepartmentListResponse, DoctorListResponse, DoctorAccountCreateResponse, DoctorTransferResponse
)
from app.db.base import get_db, redis, UserAccessLog, User, Administrator, MajorDepartment, MinorDepartment, Doctor, HospitalArea, Clinic
from app.models.user import UserType
from app.core.config import settings
from app.core.exception_handler import AuthHTTPException



logger = logging.getLogger(__name__)

router = APIRouter()
#指明Token获取路径(即无Token时跳转至这里去进行?)
# oauth2_scheme = HTTPBearer()
oauth2_scheme = OAuth2PasswordBearer("/auth/swagger-login")


async def authenticate_patient(db: AsyncSession, phonenumber: str, password: str):
    """患者端认证 - 通过手机号和密码验证用户登录

    Args:
        db (AsyncSession): 数据库会话
        phonenumber (str): 手机号
        password (str): 密码

    Returns:
        User | None: 认证成功返回用户对象，失败返回None
    """
    try:
        result = await db.execute(select(User).where(
            and_(
                 User.phonenumber == phonenumber,
                 User.is_deleted == 0
                )
            )
        )
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
    """医生/管理端认证 - 通过工号和密码验证用户登录

    Args:
        db (AsyncSession): 数据库会话
        identifier (str): 工号
        password (str): 密码

    Returns:
        User | None: 认证成功返回用户对象，失败返回None
    """
    try:
        result = await db.execute(select(User).where(
            and_(
                 User.identifier == identifier,
                 User.is_deleted == 0
                )
            )
        )
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
    """根据Token获取当前用户信息(请求头中带Token)

    Args:
        token (str, optional): _description_. Defaults to Depends(oauth2_scheme).
        db (AsyncSession, optional): _description_. Defaults to Depends(get_db).

    Raises:
        AuthHTTPException: Token无效或用户不存在时抛出异常

    Returns:
        UserSchema: 当前用户信息
    """

    #检查 Redis 中是否存在 token
    user_id_from_token = await redis.get(f"token:{token}")
    if user_id_from_token is None:

        raise AuthHTTPException(
            code=settings.TOKEN_INVALID_CODE,
            msg="Token无效或已失效",
            status_code=401
        )
    
    #校验 JWT 有效性
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=settings.TOKEN_ALGORITHM)
        user_id: str = payload.get("sub")
        login_time = payload.get("login_time")
        login_ip = payload.get("login_ip")
        if user_id != user_id_from_token:
            raise AuthHTTPException(
                code=settings.TOKEN_INVALID_CODE,
                msg="Token无效或已失效",
                status_code=401
            )
    except JWTError as e:
        logger.error(f"JWT 解码失败: {str(e)}")
        raise AuthHTTPException(
            code=settings.TOKEN_INVALID_CODE,
            msg="Token无效或已失效",
            status_code=401
        )
    
    #从数据库查用户
    result = await db.execute(select(User).where(
            and_(
                User.user_id == user_id,
                User.is_deleted == 0
            )   
        )
    )

    user = result.scalar_one_or_none()
    if user is None:
        logger.warning("找不到用户")
        raise AuthHTTPException(
            code=settings.TOKEN_INVALID_CODE,
            msg="用户不存在",
            status_code=401
        )
    
    # IP异常检测(防御手段: 防爬)
    # if login_ip and user.last_login_ip and login_ip != user.last_login_ip:
    #     raise AuthHTTPException(
    #         code=settings.TOKEN_INVALID_CODE,
    #         msg="检测到异常IP,请重新登入",
    #         status_code=401
    #     )
        
    return_user = UserSchema.from_orm(user)
    #Token验证成功返回用户信息
    return return_user
    
# ====== 邮箱验证Token相关辅助函数 ======
async def set_email_verify_token(email: str, token: str, expire_seconds: int = 1800):
    """双向写入邮箱验证token到Redis"""
    await redis.setex(f"email_verify_token:{email}", expire_seconds, token)
    await redis.setex(f"email_verify_token_reverse:{token}", expire_seconds, email)

async def delete_email_verify_token(email: str, token: str):
    """删除邮箱验证token的双向映射"""
    await redis.delete(f"email_verify_token:{email}")
    await redis.delete(f"email_verify_token_reverse:{token}")    

async def send_email_verification(email: str):
    """发送邮箱验证邮件的通用函数"""
    try:
        # 重新生成邮箱验证token，保证唯一性
        old_token = await redis.get(f"email_verify_token:{email}")
        if old_token:
            await delete_email_verify_token(email, old_token)
        
        token = generate_email_verify_token(email)
        await set_email_verify_token(email, token, settings.EMAIL_VERIFY_EXPIRE_MINUTES * 60)
        
        # verify_url = f"{settings.LOCAL_URL}{token}"
        verify_url = f"{settings.YUN_URL}{token}"
        email_content = f"<p>请点击以下链接验证邮箱(30分钟内有效):<a href='{verify_url}'>{verify_url}</a></p>"
        email_body = build_email_html("邮箱验证", email_content)
        send_email(email, "邮箱验证", email_body)
        
        logger.info(f"已发送邮箱验证邮件到: {email}")
    except Exception as e:
        logger.error(f"发送邮箱验证邮件失败: {str(e)}")
        raise AuthHTTPException(
            code=settings.REGISTER_FAILED_CODE,
            msg="发送验证邮件失败",
            status_code=500
        )

def generate_captcha_image(captcha_text: str,width=120, height=40):
    """生成验证码图片和文本"""
    # 创建空白图片
    image = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    
    # 使用默认字体
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except:
        font = ImageFont.load_default()
    
    # 绘制字符（添加随机位置偏移）
    x = 10
    for char in captcha_text:
        y = random.randint(5, 15)
        draw.text((x, y), char, fill=(random.randint(0, 100), random.randint(0, 100), random.randint(0, 100)), font=font)
        x += 28
    
    # 添加干扰线
    for _ in range(5):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)), width=1)
    
    # 转换为字节流
    img_byte_arr = BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    return img_byte_arr

# 新增：美化邮件HTML并抽取为函数
def build_email_html(title: str, content: str) -> str:
    """
    构建美观正式的邮件HTML内容
    :param title: 邮件标题
    :param content: 邮件正文（支持HTML）
    :return: 完整HTML字符串
    """
    return f"""
    <html>
    <head>
        <meta charset='UTF-8'>
        <title>{title}</title>
        <style>
            body {{ font-family: 'Segoe UI', 'Arial', 'Microsoft YaHei', sans-serif; background: #f7f7f7; margin: 0; padding: 0; }}
            .container {{ max-width: 480px; margin: 40px auto; background: #fff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); padding: 32px 28px; }}
            h2 {{ color: #2d3a4b; margin-bottom: 18px; }}
            .content {{ color: #444; font-size: 16px; line-height: 1.7; }}
            .footer {{ margin-top: 32px; color: #888; font-size: 13px; text-align: center; }}
        </style>
    </head>
    <body>
        <div class='container'>
            <h2>{title}</h2>
            <div class='content'>
                {content}
            </div>
            <div class='footer'>
                本邮件由系统自动发送，请勿直接回复。<br/>
                如有疑问请联系管理员。
            </div>
        </div>
    </body>
    </html>
    """



    
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
    
#患者端登入
@router.post("/patient/login", response_model=ResponseModel[Union[LoginResponse, AuthErrorResponse]])
async def patient_login(patientData: PatientLogin, request: Request, db: AsyncSession = Depends(get_db)):
    """患者端登录 - 通过手机号和密码验证登录"""
    try:
        user = await authenticate_patient(db, patientData.phonenumber, patientData.password)
        if not user:
            raise AuthHTTPException(
                code=settings.LOGIN_FAILED_CODE,
                msg="手机号或密码错误",
                status_code=401
            )
        
        now_ts = int(time.time())
        login_ip = request.client.host if request.client else "unknown"
        
        logger.info(f"患者登录 - IP: {login_ip}, 用户: {user.phonenumber}")
        
        # 检查IP异常和登录时间过期
        need_reverify = False
        if not user.is_verified:
            need_reverify = True
        else:
            # 登录时间过期检测
            expire_seconds = settings.LOGIN_EXPIRE_DAYS * 86400
            if user.last_login_time and now_ts - user.last_login_time > expire_seconds:
                logger.warning(f"患者 {user.phonenumber} 登录已过期: 上次登录时间={user.last_login_time}, 当前时间={now_ts}")
                need_reverify = True
        
        # 需要重新验证，设置is_verified为false并发送验证邮件
        if need_reverify:
            user.is_verified = False
            user.last_login_ip = login_ip
            user.last_login_time = now_ts
            db.add(user)
            await db.commit()
            await db.refresh(user)
            
            if user.email:
                await send_email_verification(user.email)
                raise AuthHTTPException(
                    code=settings.LOGIN_FAILED_CODE,
                    msg="需要重新验证邮箱，请前往邮箱验证",
                    status_code=401
                )
            else:
                raise AuthHTTPException(
                    code=settings.LOGIN_FAILED_CODE,
                    msg="账户需要验证，请联系管理员",
                    status_code=401
                )
        
        # 正常登录流程，更新用户登录信息
        user.last_login_ip = login_ip
        user.last_login_time = now_ts
        db.add(user)
        await db.commit()
        await db.refresh(user)
        
        access_token_expires = timedelta(minutes=settings.TOKEN_EXPIRE_TIME)
        redis_user_token_key = f"user_token:{user.user_id}"
        old_token = await redis.get(redis_user_token_key)
        if old_token:
            await redis.delete(f"token:{old_token}")
        
        # 创建包含user_type的token
        access_token = create_access_token(
            data={
                "sub": str(user.user_id), 
                "login_ip": login_ip, 
                "login_time": now_ts,
                "user_type": user.user_type.value
            }, 
            expires_delta=access_token_expires
        )
        
        await redis.setex(f"token:{access_token}", settings.TOKEN_EXPIRE_TIME*60, user.user_id)
        await redis.setex(f"user_token:{user.user_id}", settings.TOKEN_EXPIRE_TIME*60, access_token)
        
        return_token = LoginResponse(
            userid=user.user_id,
            access_token=access_token,
            token_type="Bearer",
            user_type=user.user_type.value
        )
        return ResponseModel(code=0, message=return_token)
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"患者登录时发生未处理异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.LOGIN_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )

#医生/管理端登入
@router.post("/staff/login", response_model=ResponseModel[Union[LoginResponse, AuthErrorResponse]])
async def staff_login(staffData: StaffLogin, request: Request, db: AsyncSession = Depends(get_db)):
    """医生/管理端登录 - 通过工号和密码验证登录"""
    try:
        user = await authenticate_staff(db, staffData.identifier, staffData.password)
        if not user:
            raise AuthHTTPException(
                code=settings.LOGIN_FAILED_CODE,
                msg="工号或密码错误",
                status_code=401
            )
        
        now_ts = int(time.time())
        login_ip = request.client.host if request.client else "unknown"
        
        logger.info(f"员工登录 - IP: {login_ip}, 用户: {user.identifier}")
        
        # 检查IP异常和登录时间过期
        need_reverify = False
        if not user.is_verified:
            need_reverify = True
        else:
            # 登录时间过期检测
            expire_seconds = settings.LOGIN_EXPIRE_DAYS * 86400
            if user.last_login_time and now_ts - user.last_login_time > expire_seconds:
                logger.warning(f"员工 {user.identifier} 登录已过期: 上次登录时间={user.last_login_time}, 当前时间={now_ts}")
                need_reverify = True
        
        # 需要重新验证，设置is_verified为false并发送验证邮件
        if need_reverify:
            user.is_verified = False
            user.last_login_ip = login_ip
            user.last_login_time = now_ts
            db.add(user)
            await db.commit()
            await db.refresh(user)
            
            if user.email:
                await send_email_verification(user.email)
                raise AuthHTTPException(
                    code=settings.LOGIN_FAILED_CODE,
                    msg="需要重新验证邮箱，请前往邮箱验证",
                    status_code=401
                )
            else:
                    raise AuthHTTPException(
                        code=settings.LOGIN_FAILED_CODE,
                        msg="账户需要验证，请联系管理员",
                        status_code=401
                    )
        
        # 正常登录流程，更新用户登录信息
        user.last_login_ip = login_ip
        user.last_login_time = now_ts
        db.add(user)
        await db.commit()
        await db.refresh(user)
        
        access_token_expires = timedelta(minutes=settings.TOKEN_EXPIRE_TIME)
        redis_user_token_key = f"user_token:{user.user_id}"
        old_token = await redis.get(redis_user_token_key)
        if old_token:
            await redis.delete(f"token:{old_token}")
        
        # 创建包含user_type的token
        access_token = create_access_token(
            data={
                "sub": str(user.user_id), 
                "login_ip": login_ip, 
                "login_time": now_ts,
                "user_type": user.user_type.value
            }, 
            expires_delta=access_token_expires
        )
        
        await redis.setex(f"token:{access_token}", settings.TOKEN_EXPIRE_TIME*60, user.user_id)
        await redis.setex(f"user_token:{user.user_id}", settings.TOKEN_EXPIRE_TIME*60, access_token)
        
        return_token = LoginResponse(
            userid=user.user_id,
            access_token=access_token,
            token_type="Bearer",
            user_type=user.user_type.value
        )
        return ResponseModel(code=0, message=return_token)
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"员工登录时发生未处理异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.LOGIN_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )

# 管理员注册API（无需认证）
@router.post("/admin/register", response_model=ResponseModel[Union[AdminRegisterResponse, AuthErrorResponse]])
async def admin_register(admin_data: AdminRegister, db: AsyncSession = Depends(get_db)):
    """管理员注册 - 无需认证，用于系统初始化"""
    try:
        # 检查工号是否已存在
        result = await db.execute(select(User).where(
            and_(User.identifier == admin_data.identifier, User.is_deleted == 0)
        ))
        if result.scalar_one_or_none():
            raise AuthHTTPException(
                code=settings.REGISTER_FAILED_CODE,
                msg="工号已被使用",
                status_code=400
            )
        
        # 检查邮箱是否已存在（如果提供了邮箱）
        if admin_data.email:
            result = await db.execute(select(User).where(
                and_(User.email == admin_data.email, User.is_deleted == 0)
            ))
            if result.scalar_one_or_none():
                raise AuthHTTPException(
                    code=settings.REGISTER_FAILED_CODE,
                    msg="邮箱已被使用",
                    status_code=400
                )
        
        # username 字段已移除，跳过用户名唯一性检查
        
        # 检查手机号是否已存在（如果提供了手机号）
        if admin_data.phonenumber:
            result = await db.execute(select(User).where(
                and_(User.phonenumber == admin_data.phonenumber, User.is_deleted == 0)
            ))
            if result.scalar_one_or_none():
                raise AuthHTTPException(
                    code=settings.REGISTER_FAILED_CODE,
                    msg="手机号已被使用",
                    status_code=400
                )
        
        # 创建用户账号
        hashed_password = get_hash_pwd(admin_data.password)
        db_user = User(
            identifier=admin_data.identifier,  # 工号作为必填项
            email=admin_data.email,            # 邮箱可选
            phonenumber=admin_data.phonenumber, # 手机号可选
            hashed_password=hashed_password,
            user_type=UserType.ADMIN,  # 设置为管理员类型
            is_admin=True,      # 设置为管理员权限
            is_verified=True    # 管理员注册后直接验证
        )
        # 注意：get_db 依赖在请求结束时会进行 commit/rollback，如果在此处再次
        # 显式开启事务（async with db.begin()），当 Session 已经处于事务中会抛出
        # "A transaction is already begun on this Session."。因此这里只执行 add/flush，
        # 由依赖统一管理提交。
        logger.debug(f"admin_register: db.in_transaction()={db.in_transaction()}")
        db.add(db_user)
        await db.flush()
        if admin_data.name:
            db_admin = Administrator(
                user_id=db_user.user_id,
                name=admin_data.name,
                job_title=admin_data.job_title
            )
            db.add(db_admin)
        # 事务提交后，刷新 db_user
        await db.refresh(db_user)
        
        logger.info(f"管理员注册成功: 工号 {admin_data.identifier}")
        
        return ResponseModel(
            code=0, 
            message=AdminRegisterResponse(detail="管理员注册成功")
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"管理员注册时发生未处理异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REGISTER_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )

        
@router.get("/users", response_model=ResponseModel[Union[UsersListResponse, AuthErrorResponse]])
async def get_all_users(db: AsyncSession = Depends(get_db), current_user: UserSchema = Depends(get_current_user)):
    """仅管理员可获取所有用户信息"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限",
                status_code=403
            )
        result = await db.execute(select(User).where(User.is_deleted == 0))
        users = result.scalars().all()
        users_list = UsersListResponse(users=[UserSchema.from_orm(u) for u in users])
        return ResponseModel(code=0, message=users_list)
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取所有用户时发生未处理异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.USER_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )

@router.get("/users/{user_id}", response_model=ResponseModel[Union[SingleUserResponse, AuthErrorResponse]])
async def get_user_by_id(user_id: int, db: AsyncSession = Depends(get_db), current_user: UserSchema = Depends(get_current_user)):
    """根据id获取用户信息,管理员可查所有,普通用户只能查自己"""
    try:
        result = await db.execute(select(User).where(User.user_id == user_id, User.is_deleted == 0))
        user = result.scalar_one_or_none()
        if not user:
            raise AuthHTTPException(
                code=settings.USER_GET_FAILED_CODE,
                msg="用户不存在",
                status_code=404
            )
        if not current_user.is_admin and user.user_id != current_user.user_id:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限",
                status_code=403
            )
        single_user = SingleUserResponse(user=UserSchema.from_orm(user))
        return ResponseModel(code=0, message=single_user)
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"根据ID获取用户时发生未处理异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.USER_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )

@router.post("/register", response_model=ResponseModel[Union[RegisterResponse, AuthErrorResponse]])
async def register(user: userCreate, db: AsyncSession=Depends(get_db)):
    """注册(发送邮箱验证)"""
    try:
        result = await db.execute(select(User).where(
            and_(
                    User.email == user.email,
                    User.is_deleted == 0
                )
            )
        )
        if result.scalar_one_or_none():
            raise AuthHTTPException(
                code=settings.REGISTER_FAILED_CODE,
                msg="Email already taken",
                status_code=400
            )
        # username 字段已移除，跳过用户名重复校验
        hashed_password= get_hash_pwd(user.password)
        db_user = User(
            email=user.email,
            phonenumber=user.phonenumber,
            hashed_password=hashed_password,
            is_verified=False
        )
        db.add(db_user)
        await db.commit()
        await db.refresh(db_user)
        
        # 发送邮箱验证
        await send_email_verification(db_user.email)
        
        register_response = RegisterResponse(detail="注册成功，请前往邮箱验证")
        return ResponseModel(code=0, message=register_response)
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"用户注册时发生未处理异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REGISTER_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )

@router.get("/verifyEmail")
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    """邮箱验证(仅返回HTML以保证外部邮箱正确解析)"""
    try:
        email = verify_email_token(token)
        if not email:
            html = build_email_html("验证失败", "验证链接无效或已过期。<br>请重新注册或联系管理员。")
            return HTMLResponse(content=html, status_code=200)
        result = await db.execute(select(User).where(User.email == email, User.is_deleted == 0))
        user = result.scalar_one_or_none()
        if not user:
            html = build_email_html("用户不存在", "用户不存在。<br>请检查邮箱地址是否正确。")
            return HTMLResponse(content=html, status_code=200)
        if user.is_verified:
            html = build_email_html("邮箱已验证", "邮箱已验证。<br>您可以关闭此页面或直接登录系统。")
            return HTMLResponse(content=html, status_code=200)
        user.is_verified = True
        db.add(user)
        await db.commit()
        html = build_email_html("邮箱验证成功", "邮箱验证成功。<br>您可以关闭此页面或返回登录。")
        return HTMLResponse(content=html, status_code=200)
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"邮箱验证时发生未处理异常: {str(e)}")
        html = build_email_html("验证失败", "验证过程中发生错误。<br>请重新注册或联系管理员。")
        return HTMLResponse(content=html, status_code=200)

        
@router.post("/users/{user_id}/updatePasswordRequire", response_model=ResponseModel[Union[PasswordChangeRequestResponse, AuthErrorResponse]])
async def request_update_password_by_userid(
    input_data: PasswordUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    发送修改用户密码请求, 向修改密码的用户邮箱发送含验证码的邮件
    管理员有权对所有用户修改密码, 普通用户只能修改自己的密码, 但均需先输入原密码
    """
    try:
        # 权限校验
        if not current_user.is_admin and current_user.user_id != input_data.user_id:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限修改他人密码",
                status_code=403
            )
        # 查询用户
        result = await db.execute(select(User).where(User.user_id == input_data.user_id, User.is_deleted == 0))
        user = result.scalar_one_or_none()
        if not user:
            raise AuthHTTPException(
                code=settings.USER_GET_FAILED_CODE,
                msg="用户不存在",
                status_code=404
            )
        # 校验旧密码
        if not verify_pwd(input_data.old_password, user.hashed_password):
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="旧密码错误",
                status_code=400
            )
        # 校验新密码合规
        if input_data.new_password != input_data.confirm_password:
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="两次输入的新密码不一致",
                status_code=400
            )
        if len(input_data.new_password) < 6 or len(input_data.new_password) > 18:
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="新密码长度需为6-18位",
                status_code=400
            )
        if verify_pwd(input_data.new_password, user.hashed_password):
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="新密码不能与旧密码相同",
                status_code=400
            )
        # 生成验证码
        code = str(random.randint(100000, 999999))
        # Redis缓存
        value = {
            "new_password_hash": get_hash_pwd(input_data.new_password),
            "email": user.email,
            "code": code
        }
        await redis.setex(f"pwd_change:{input_data.user_id}", 300, json.dumps(value))
        # 发送邮件
        email_content = f"您的密码修改验证码为：<b>{code}</b>，5分钟内有效。如非本人操作请忽略。"
        email_body = build_email_html("密码修改验证码", email_content)
        send_email(user.email, "密码修改验证码", email_body)
        return ResponseModel(code=0, message=PasswordChangeRequestResponse(detail="验证码已发送，请在5分钟内输入验证码"))
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"发送密码修改请求异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="发送密码修改请求失败",
            status_code=500
        )



@router.post("/users/{user_id}/updatePasswordConfirm", response_model=ResponseModel[Union[PasswordChangeConfirmResponse, AuthErrorResponse]])
async def confirm_update_password_requirement(
    input_data: PasswordChangeConfirmInput,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    验证修改用户密码请求, 比对输入的验证码, 返回密码是否修改成功
    """
    try:
        # 权限校验
        if not current_user.is_admin and current_user.user_id != input_data.user_id:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限修改他人密码",
                status_code=403
            )
        # 查询用户
        result = await db.execute(select(User).where(User.user_id == input_data.user_id, User.is_deleted == 0))
        user = result.scalar_one_or_none()
        if not user:
            raise AuthHTTPException(
                code=settings.USER_GET_FAILED_CODE,
                msg="用户不存在",
                status_code=404
            )
        # 读取Redis
        cache = await redis.get(f"pwd_change:{input_data.user_id}")
        if not cache:
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="验证码已过期或未请求修改",
                status_code=400
            )
        value = json.loads(cache)
        if input_data.code != value.get("code"):
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="验证码错误",
                status_code=400
            )
        # 更新密码
        user.hashed_password = value["new_password_hash"]
        db.add(user)
        await db.commit()
        await db.refresh(user)
        # 删除Redis
        await redis.delete(f"pwd_change:{input_data.user_id}")
        return ResponseModel(code=0, message=PasswordChangeConfirmResponse(detail="密码修改成功"))
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"确认密码修改异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="密码修改失败",
            status_code=500
        )

        
@router.put("/users/{user_id}/updateProfile", response_model=ResponseModel[Union[UpdateUserResponse, AuthErrorResponse]])
async def update_profile(
    user_id: int,
    update_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """修改用户个人信息,管理员可修改所有用户,普通用户只能修改自己的信息"""
    try:
        # 权限检查：管理员可以修改所有用户，普通用户只能修改自己
        if not current_user.is_admin and user_id != current_user.user_id:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限修改其他用户信息",
                status_code=403
            )
        
        # 获取要修改的用户
        result = await db.execute(select(User).where(
            and_(User.user_id == user_id, User.is_deleted == 0)
        ))
        
        db_user = result.scalar_one_or_none()
        if not db_user:
            raise AuthHTTPException(
                code=settings.UPDATEPROFILE_FAILED_CODE,
                msg="用户不存在",
                status_code=404
            )
        
        # 检查邮箱是否被其他用户占用
        if update_data.email and update_data.email != db_user.email:
            result = await db.execute(select(User).where(
                and_(User.email == update_data.email, User.is_deleted == 0, User.user_id != user_id)
            ))
            if result.scalar_one_or_none():
                raise AuthHTTPException(
                    code=settings.UPDATEPROFILE_FAILED_CODE,
                    msg="邮箱已被占用",
                    status_code=400
                )
        
        # username 字段已移除，跳过用户名占用与更新
        # 更新用户信息
        updated = False
        if update_data.email and update_data.email != db_user.email:
            db_user.email = update_data.email
            updated = True
        if update_data.phonenumber and update_data.phonenumber != db_user.phonenumber:
            db_user.phonenumber = update_data.phonenumber
            updated = True
        
        if updated:
            db.add(db_user)
            await db.commit()
            await db.refresh(db_user)
        
        return_user = UserSchema.from_orm(db_user)
        update_response = UpdateUserResponse(user=return_user)
        return ResponseModel(code=0, message=update_response)
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新用户信息时发生未处理异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.UPDATEPROFILE_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
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
        # 只允许管理员操作
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可删除用户",
                status_code=403
            )
        # 查询要删除的用户
        result = await db.execute(select(User).where(User.user_id == user_id, User.is_deleted == 0))
        user = result.scalar_one_or_none()
        if not user:
            raise AuthHTTPException(
                code=settings.USER_GET_FAILED_CODE,
                msg="用户不存在",
                status_code=404
            )
        # 管理员不能删除其他管理员
        if getattr(user, "is_admin", False):
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="管理员不能删除其他管理员",
                status_code=403
            )
        # 逻辑删除
        user.is_deleted = 1
        db.add(user)
        await db.commit()
        await db.refresh(user)
        display_name = getattr(user, 'phonenumber', None) or getattr(user, 'identifier', user.user_id)
        return ResponseModel(code=0, message=DeleteResponse(detail=f"成功删除用户{display_name}"))
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
    role_data: UserRoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """修改用户角色,目前只允许管理员将普通用户提升为管理员,管理员间不能互相更改"""
    try:
        # 只允许管理员操作
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可修改用户角色",
                status_code=403
            )
        
        # 查询要修改的用户
        result = await db.execute(select(User).where(User.user_id == user_id, User.is_deleted == 0))
        user = result.scalar_one_or_none()
        if not user:
            raise AuthHTTPException(
                code=settings.USER_GET_FAILED_CODE,
                msg="用户不存在",
                status_code=404
            )
        
        # 管理员不能修改其他管理员的角色
        if getattr(user, "is_admin", False):
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="管理员不能修改其他管理员的角色",
                status_code=403
            )
        
        # 只能将普通用户提升为管理员，不能降级
        if not role_data.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="只能将用户提升为管理员，不能降级",
                status_code=400
            )
        
        # 更新用户角色
        user.is_admin = role_data.is_admin
        db.add(user)
        await db.commit()
        await db.refresh(user)
        
        return_user = UserSchema.from_orm(user)
        role_action = "提升为管理员" if role_data.is_admin else "降级为普通用户"
        display_name = getattr(user, 'phonenumber', None) or getattr(user, 'identifier', user.user_id)
        update_response = UpdateUserRoleResponse(
            user=return_user,
            detail=f"成功将用户 {display_name} {role_action}"
        )
        return ResponseModel(code=0, message=update_response)
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
    user_id: int | None = None,
    ip: str | None = None,
    action: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    管理员获取用户访问日志，支持多条件过滤和分页，按时间倒序。

    请求参数：
        page (int): 当前页码，从1开始，默认1。
        page_size (int): 每页条数，默认20。
        user_id (int|None): 按用户ID过滤，若不传则不限制。
        ip (str|None): 按访问IP过滤，若不传则不限制。
        action (str|None): 按行为类型（method，如GET/POST/DELETE请求方法等）过滤，若不传则不限制。
        start_time (str|None): 起始时间，支持宽松日期时间字符串格式，过滤大于等于该时间的日志。
        end_time (str|None): 结束时间，支持宽松日期时间字符串格式，过滤小于等于该时间的日志。
    返回：
        ResponseModel[UserAccessLogPageResponse]: 分页后的日志数据及分页信息。
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可查看日志",
                status_code=403
            )
        filters = []
        if user_id is not None:
            filters.append(UserAccessLog.user_id == user_id)
        if ip is not None:
            filters.append(UserAccessLog.ip == ip)
        if action is not None:
            filters.append(UserAccessLog.method == action)
        if start_time is not None:
            try:
                start_dt = parse_datetime(start_time)
                # 转换为 'YYYY-MM-DD HH:MM:SS' 格式再转回 datetime
                start_dt_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
                start_dt = datetime.strptime(start_dt_str, '%Y-%m-%d %H:%M:%S')
                filters.append(UserAccessLog.access_time >= start_dt)
            except Exception:
                raise AuthHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="start_time格式错误,需为日期时间字符串",
                    status_code=400
                )
        if end_time is not None:
            try:
                end_dt = parse_datetime(end_time)
                end_dt_str = end_dt.strftime('%Y-%m-%d %H:%M:%S')
                end_dt = datetime.strptime(end_dt_str, '%Y-%m-%d %H:%M:%S')
                filters.append(UserAccessLog.access_time <= end_dt)
            except Exception:
                raise AuthHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="end_time格式错误,需为日期时间字符串",
                    status_code=400
                )
        stmt = select(UserAccessLog).where(and_(*filters)).order_by(desc(UserAccessLog.access_time))
        total = (await db.execute(select(UserAccessLog.user_access_log_id).where(and_(*filters)))).scalars().all()
        total_count = len(total)
        total_pages = (total_count + page_size - 1) // page_size
        offset = (page - 1) * page_size
        result = await db.execute(stmt.offset(offset).limit(page_size))
        logs = result.scalars().all()
        log_items = [UserAccessLogItem(
            user_access_log_id=log.user_access_log_id,
            user_id=log.user_id,
            ip=log.ip,
            ua=log.ua,
            url=log.url,
            method=log.method,
            status_code=log.status_code,
            response_code=log.response_code,
            access_time=log.access_time.strftime('%Y-%m-%d %H:%M:%S') if log.access_time else None,
            duration_ms=log.duration_ms
        ) for log in logs]
        page_response = UserAccessLogPageResponse(
            logs=log_items,
            total=total_count,
            total_pages=total_pages,
            page=page,
            page_size=page_size
        )
        return ResponseModel(code=0, message=page_response)
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户访问日志异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ====== 管理员科室管理接口 ======

# 大科室管理
@router.post("/admin/major-departments", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def create_major_department(
    dept_data: MajorDepartmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """创建大科室 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 检查科室名称是否已存在
        result = await db.execute(select(MajorDepartment).where(MajorDepartment.name == dept_data.name))
        if result.scalar_one_or_none():
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="科室名称已存在",
                status_code=400
            )
        
        # 创建大科室
        db_dept = MajorDepartment(
            name=dept_data.name,
            description=dept_data.description
        )
        db.add(db_dept)
        await db.commit()
        await db.refresh(db_dept)
        
        logger.info(f"创建大科室成功: {dept_data.name}")
        
        return ResponseModel(
            code=0,
            message={
                "major_dept_id": db_dept.major_dept_id,
                "name": db_dept.name,
                "description": db_dept.description,
                "create_time": db_dept.create_time
            }
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"创建大科室时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/admin/major-departments", response_model=ResponseModel[Union[MajorDepartmentListResponse, AuthErrorResponse]])
async def get_major_departments(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取所有大科室 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        result = await db.execute(select(MajorDepartment))
        departments = result.scalars().all()
        
        dept_list = []
        for dept in departments:
            dept_list.append({
                "major_dept_id": dept.major_dept_id,
                "name": dept.name,
                "description": dept.description,
                "create_time": dept.create_time
            })
        
        return ResponseModel(
            code=0,
            message=MajorDepartmentListResponse(departments=dept_list)
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取大科室列表时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/admin/major-departments/{dept_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_major_department(
    dept_id: int,
    dept_data: MajorDepartmentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """更新大科室信息 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 获取科室
        result = await db.execute(select(MajorDepartment).where(MajorDepartment.major_dept_id == dept_id))
        db_dept = result.scalar_one_or_none()
        if not db_dept:
            raise AuthHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="科室不存在",
                status_code=404
            )
        
        # 检查新名称是否与其他科室冲突
        if dept_data.name and dept_data.name != db_dept.name:
            result = await db.execute(select(MajorDepartment).where(
                and_(MajorDepartment.name == dept_data.name, MajorDepartment.major_dept_id != dept_id)
            ))
            if result.scalar_one_or_none():
                raise AuthHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="科室名称已存在",
                    status_code=400
                )
        
        # 更新科室信息
        if dept_data.name:
            db_dept.name = dept_data.name
        if dept_data.description is not None:
            db_dept.description = dept_data.description
        
        db.add(db_dept)
        await db.commit()
        await db.refresh(db_dept)
        
        logger.info(f"更新大科室成功: {db_dept.name}")
        
        return ResponseModel(
            code=0,
            message={
                "major_dept_id": db_dept.major_dept_id,
                "name": db_dept.name,
                "description": db_dept.description,
                "create_time": db_dept.create_time
            }
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新大科室时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.delete("/admin/major-departments/{dept_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def delete_major_department(
    dept_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    删除大科室 - 仅管理员可操作。若存在小科室依赖则拒绝删除。
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 检查大科室是否存在
        result = await db.execute(select(MajorDepartment).where(MajorDepartment.major_dept_id == dept_id))
        db_dept = result.scalar_one_or_none()
        if not db_dept:
            raise AuthHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="科室不存在",
                status_code=404
            )

        # 检查是否存在小科室依赖
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.major_dept_id == dept_id))
        if result.scalar_one_or_none():
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="存在下属小科室，无法删除",
                status_code=400
            )

        # 删除大科室
        await db.delete(db_dept)
        await db.commit()

        logger.info(f"删除大科室成功: {db_dept.name}")
        return ResponseModel(code=0, message={"detail": f"成功删除大科室 {db_dept.name}"})
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除大科室时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# 小科室管理
@router.post("/admin/minor-departments", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def create_minor_department(
    dept_data: MinorDepartmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """创建小科室 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 检查大科室是否存在
        result = await db.execute(select(MajorDepartment).where(MajorDepartment.major_dept_id == dept_data.major_dept_id))
        if not result.scalar_one_or_none():
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="大科室不存在",
                status_code=400
            )
        
        # 检查小科室名称是否已存在
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.name == dept_data.name))
        if result.scalar_one_or_none():
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="科室名称已存在",
                status_code=400
            )
        
        # 创建小科室
        db_dept = MinorDepartment(
            major_dept_id=dept_data.major_dept_id,
            name=dept_data.name,
            description=dept_data.description
        )
        db.add(db_dept)
        await db.commit()
        await db.refresh(db_dept)
        
        logger.info(f"创建小科室成功: {dept_data.name}")
        
        return ResponseModel(
            code=0,
            message={
                "minor_dept_id": db_dept.minor_dept_id,
                "major_dept_id": db_dept.major_dept_id,
                "name": db_dept.name,
                "description": db_dept.description,
                "create_time": db_dept.create_time
            }
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"创建小科室时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.delete("/admin/minor-departments/{minor_dept_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def delete_minor_department(
    minor_dept_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    删除小科室 - 仅管理员可操作。若存在关联医生则拒绝删除。
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 检查小科室是否存在
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == minor_dept_id))
        db_dept = result.scalar_one_or_none()
        if not db_dept:
            raise AuthHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="科室不存在",
                status_code=404
            )

        # 检查是否有医生关联
        result = await db.execute(select(Doctor).where(Doctor.dept_id == minor_dept_id))
        if result.scalar_one_or_none():
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="存在关联医生，无法删除",
                status_code=400
            )

        # 删除小科室
        await db.delete(db_dept)
        await db.commit()

        logger.info(f"删除小科室成功: {db_dept.name}")
        return ResponseModel(code=0, message={"detail": f"成功删除小科室 {db_dept.name}"})
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除小科室时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/admin/minor-departments", response_model=ResponseModel[Union[MinorDepartmentListResponse, AuthErrorResponse]])
async def get_minor_departments(
    major_dept_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取小科室列表 - 仅管理员可操作，可按大科室过滤"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 构建查询条件
        filters = []
        if major_dept_id:
            filters.append(MinorDepartment.major_dept_id == major_dept_id)
        
        result = await db.execute(select(MinorDepartment).where(and_(*filters) if filters else True))
        departments = result.scalars().all()
        
        dept_list = []
        for dept in departments:
            dept_list.append({
                "minor_dept_id": dept.minor_dept_id,
                "major_dept_id": dept.major_dept_id,
                "name": dept.name,
                "description": dept.description,
                "create_time": dept.create_time
            })
        
        return ResponseModel(
            code=0,
            message=MinorDepartmentListResponse(departments=dept_list)
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取小科室列表时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ====== 管理员医生管理接口 ======

# 医生管理
@router.post("/admin/doctors", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def create_doctor(
    doctor_data: DoctorCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """创建医生信息 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 检查小科室是否存在
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == doctor_data.dept_id))
        if not result.scalar_one_or_none():
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="小科室不存在",
                status_code=400
            )
        
        # 检查工号是否已存在
        result = await db.execute(select(User).where(User.identifier == doctor_data.identifier))
        if result.scalar_one_or_none():
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="工号已被使用",
                status_code=400
            )
        
        # 创建医生信息
        db_doctor = Doctor(
            dept_id=doctor_data.dept_id,
            name=doctor_data.name,
            title=doctor_data.title,
            specialty=doctor_data.specialty,
            introduction=doctor_data.introduction
        )
        db.add(db_doctor)
        await db.commit()
        await db.refresh(db_doctor)
        
        logger.info(f"创建医生信息成功: {doctor_data.name}")
        
        return ResponseModel(
            code=0,
            message={
                "doctor_id": db_doctor.doctor_id,
                "dept_id": db_doctor.dept_id,
                "name": db_doctor.name,
                "title": db_doctor.title,
                "specialty": db_doctor.specialty,
                "introduction": db_doctor.introduction,
                "create_time": db_doctor.create_time
            }
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"创建医生信息时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/admin/doctors", response_model=ResponseModel[Union[DoctorListResponse, AuthErrorResponse]])
async def get_doctors(
    dept_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取医生列表 - 仅管理员可操作，可按科室过滤"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 构建查询条件
        filters = []
        if dept_id:
            filters.append(Doctor.dept_id == dept_id)
        
        result = await db.execute(select(Doctor).where(and_(*filters) if filters else True))
        doctors = result.scalars().all()
        
        doctor_list = []
        for doctor in doctors:
            doctor_list.append({
                "doctor_id": doctor.doctor_id,
                "user_id": doctor.user_id,
                "dept_id": doctor.dept_id,
                "name": doctor.name,
                "title": doctor.title,
                "specialty": doctor.specialty,
                "introduction": doctor.introduction,
                "photo_path": doctor.photo_path,
                "original_photo_url": doctor.original_photo_url,
                "create_time": doctor.create_time
            })
        
        return ResponseModel(
            code=0,
            message=DoctorListResponse(doctors=doctor_list)
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取医生列表时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/admin/doctors/{doctor_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_doctor(
    doctor_id: int,
    doctor_data: DoctorUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """更新医生信息 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 获取医生
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
            raise AuthHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )
        
        # 如果更新科室，检查新科室是否存在
        if doctor_data.dept_id and doctor_data.dept_id != db_doctor.dept_id:
            result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == doctor_data.dept_id))
            if not result.scalar_one_or_none():
                raise AuthHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="目标科室不存在",
                    status_code=400
                )
        
        # 更新医生信息
        if doctor_data.dept_id:
            db_doctor.dept_id = doctor_data.dept_id
        if doctor_data.name:
            db_doctor.name = doctor_data.name
        if doctor_data.title is not None:
            db_doctor.title = doctor_data.title
        if doctor_data.specialty is not None:
            db_doctor.specialty = doctor_data.specialty
        if doctor_data.introduction is not None:
            db_doctor.introduction = doctor_data.introduction
        if doctor_data.photo_path is not None:
            db_doctor.photo_path = doctor_data.photo_path
        if doctor_data.original_photo_url is not None:
            db_doctor.original_photo_url = doctor_data.original_photo_url
        
        db.add(db_doctor)
        await db.commit()
        await db.refresh(db_doctor)
        
        logger.info(f"更新医生信息成功: {db_doctor.name}")
        
        return ResponseModel(
            code=0,
            message={
                "doctor_id": db_doctor.doctor_id,
                "dept_id": db_doctor.dept_id,
                "name": db_doctor.name,
                "title": db_doctor.title,
                "specialty": db_doctor.specialty,
                "introduction": db_doctor.introduction,
                "photo_path": db_doctor.photo_path,
                "original_photo_url": db_doctor.original_photo_url,
                "create_time": db_doctor.create_time
            }
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新医生信息时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.delete("/admin/doctors/{doctor_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def delete_doctor(
    doctor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """删除医生信息 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 获取医生
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
            raise AuthHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )
        
        # 如果医生有关联的用户账号，需要先处理用户账号
        if db_doctor.user_id:
            # 可以选择删除用户账号或者解除关联
            # 这里选择解除关联，保留用户账号
            db_doctor.user_id = None
        
        # 删除医生信息
        await db.delete(db_doctor)
        await db.commit()
        
        logger.info(f"删除医生信息成功: {db_doctor.name}")
        
        return ResponseModel(
            code=0,
            message={"detail": f"成功删除医生 {db_doctor.name}"}
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除医生信息时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# 医生调科室
@router.put("/admin/doctors/{doctor_id}/transfer", response_model=ResponseModel[Union[DoctorTransferResponse, AuthErrorResponse]])
async def transfer_doctor_department(
    doctor_id: int,
    transfer_data: DoctorTransferDepartment,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """医生调科室 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 获取医生
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
            raise AuthHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )
        
        # 检查目标科室是否存在
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == transfer_data.new_dept_id))
        if not result.scalar_one_or_none():
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="目标科室不存在",
                status_code=400
            )
        
        # 记录原科室ID
        old_dept_id = db_doctor.dept_id
        
        # 更新医生科室
        db_doctor.dept_id = transfer_data.new_dept_id
        db.add(db_doctor)
        await db.commit()
        await db.refresh(db_doctor)
        
        logger.info(f"医生调科室成功: {db_doctor.name} 从科室 {old_dept_id} 调到科室 {transfer_data.new_dept_id}")
        
        return ResponseModel(
            code=0,
            message=DoctorTransferResponse(
                detail=f"成功将医生 {db_doctor.name} 调至新科室",
                doctor_id=doctor_id,
                old_dept_id=old_dept_id,
                new_dept_id=transfer_data.new_dept_id
            )
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"医生调科室时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# 为医生创建账号
@router.post("/admin/doctors/{doctor_id}/create-account", response_model=ResponseModel[Union[DoctorAccountCreateResponse, AuthErrorResponse]])
async def create_doctor_account(
    doctor_id: int,
    account_data: DoctorAccountCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """为医生创建登录账号 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 获取医生
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
            raise AuthHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )
        
        # 检查医生是否已有账号
        if db_doctor.user_id:
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="该医生已有登录账号",
                status_code=400
            )
        
        # 检查工号是否已被使用
        result = await db.execute(select(User).where(User.identifier == account_data.identifier))
        if result.scalar_one_or_none():
            raise AuthHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="工号已被使用",
                status_code=400
            )
        
        # 检查邮箱是否已被使用
        if account_data.email:
            result = await db.execute(select(User).where(User.email == account_data.email))
            if result.scalar_one_or_none():
                raise AuthHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="邮箱已被使用",
                    status_code=400
                )
        
        # 检查手机号是否已被使用
        if account_data.phonenumber:
            result = await db.execute(select(User).where(User.phonenumber == account_data.phonenumber))
            if result.scalar_one_or_none():
                raise AuthHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="手机号已被使用",
                    status_code=400
                )
        
        # 创建用户账号
        hashed_password = get_hash_pwd(account_data.password)
        db_user = User(
            identifier=account_data.identifier,
            email=account_data.email,
            phonenumber=account_data.phonenumber,
            hashed_password=hashed_password,
            user_type="doctor",  # 设置为医生类型
            is_admin=False,      # 医生不是管理员
            is_verified=True     # 管理员创建的账号直接验证
        )
        db.add(db_user)
        await db.commit()
        await db.refresh(db_user)
        
        # 更新医生信息，关联用户账号
        db_doctor.user_id = db_user.user_id
        db.add(db_doctor)
        await db.commit()
        
        logger.info(f"为医生创建账号成功: {db_doctor.name} (工号: {account_data.identifier})")
        
        return ResponseModel(
            code=0,
            message=DoctorAccountCreateResponse(
                detail=f"成功为医生 {db_doctor.name} 创建登录账号",
                user_id=db_user.user_id,
                doctor_id=doctor_id
            )
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"为医生创建账号时发生异常: {str(e)}")
        raise AuthHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )







