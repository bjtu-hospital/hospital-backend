from fastapi import APIRouter, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, update
from typing import Optional
from datetime import datetime, timedelta, date as date_type

from app.core.datetime_utils import get_now_naive
import logging
import base64
import os
import aiofiles

from app.db.base import get_db, User, MajorDepartment, MinorDepartment, Doctor, Clinic, Schedule, redis
from app.models.hospital_area import HospitalArea
from app.models.registration_order import RegistrationOrder, OrderStatus, PaymentStatus
from app.models.patient import Patient
from app.models.patient import PatientType
from app.models.user import UserType
from app.models.visit_history import VisitHistory
from app.models.patient_relation import PatientRelation
from app.schemas.response import ResponseModel
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentListResponse,
    AppointmentListItem,
    CancelAppointmentResponse,
    CancelAppointmentRequest,
    RescheduleOption,
    RescheduleOptionsResponse,
    RescheduleRequest,
    RescheduleResponse,
)
from app.schemas.waitlist import (
    WaitlistCreate,
    WaitlistCreateResponse,
    WaitlistItem,
    WaitlistListResponse,
    WaitlistConvertRequest,
    WaitlistConvertResponse,
)
from app.schemas.health_record import (
    HealthRecordResponse,
    BasicInfo,
    MedicalHistory,
    ConsultationRecord,
    VisitRecordDetailResponse,
    VisitRecordDetail,
    RecordData
)
from app.schemas.patient_relation import (
    PatientRelationCreate,
    PatientRelationUpdate,
    PatientRelationResponse,
    PatientRelationListResponse,
    PatientInfo
)
from app.core.config import settings
from app.core.exception_handler import BusinessHTTPException, ResourceHTTPException, AuthHTTPException
from app.api.auth import get_current_user
from app.schemas.user import user as UserSchema
from app.services.admin_helpers import (
    bulk_get_doctor_prices,
    bulk_get_clinic_prices,
    bulk_get_minor_dept_prices,
    _weekday_to_cn,
    _slot_type_to_str,
)
from app.services.config_service import (
    get_registration_config,
    get_schedule_config,
    parse_time_to_hour_minute,
    get_patient_identity_discounts,
    calculate_final_price
)
from app.schemas.payment import (
    PaymentRequest,
    PaymentResponse,
    PaymentMethodEnum,
    CancelPaymentRequest,
    CancelPaymentResponse
)
from app.services.waitlist_service import WaitlistService
from app.services.wechat_service import WechatService
from app.schemas.wechat import WechatLoginRequest
from app.schemas.wechat import WechatCodeToOpenIdResponse
from app.schemas.wechat import SubscribeAuthResult
import requests
import urllib3
import hashlib
from app.schemas.patient_identity import IdentityVerifyRequest

logger = logging.getLogger(__name__)
router = APIRouter()


# ====== 患者端公开查询接口(无需登录) ======


# ====== 校园认证辅助函数（内联实现，后续移除 app.verify 依赖） ======

def _md5_encrypt(text: str) -> str:
    md5 = hashlib.md5()
    md5.update(text.encode('utf-8'))
    return md5.hexdigest()


def _get_captcha() -> tuple[str, str]:
    url = "https://10.126.59.109:6440/cp/auth/captchaImage"
    response = requests.get(url, verify=False)
    res = response.json()
    code = res.get("data", {}).get("code", -1)
    uuid = res.get("data", {}).get("uuid", -1)
    return (code, uuid)


def login_to_iclass(loginName: str, password: str) -> dict:
    """
    校园登录：成功返回非空字典，失败返回 {}。
    仅在校园网可用。
    """
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    url = "https://10.126.59.109:6440/cp/auth/signIn"
    headers = {
        'accept': 'application/json, text/plain, */*',
        'content-type': 'application/json;charset=UTF-8',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    code, uuid = _get_captcha()
    data = {
        "loginDeviceType": "1",
        "loginName": loginName,
        "password": _md5_encrypt(password),
        "verifyCode": code,
        "verifyUuid": uuid
    }

    session = requests.Session()
    session.trust_env = True
    session.proxies = {
        'http': None,
        'https': None,
        'no_proxy': 'bjju.edu.cn,*.bjju.edu.cn'
    }

    raw_data = {}
    try:
        response = session.post(url, headers=headers, json=data, verify=False)
        raw = response.json()
        raw_data = raw
    except Exception:
        raw_data = {}
    finally:
        if raw_data.get("meta", {}).get("success", False):
            return raw_data
        return {}


def getInfoById(userId: str) -> dict:
    base_info_url = f"http://123.121.147.7:88/ve/back/coursePlatform/coursePlatform.shtml?method=getUserInfo&userId={userId}"
    response = requests.get(url=base_info_url)
    raw_data = response.json()
    return {
        "status": raw_data.get("STATUS", -1),
        "userId": raw_data.get("result", {}).get("userId", ""),
        "userName": raw_data.get("result", {}).get("userName", ""),
        "roleCode": raw_data.get("result", {}).get("roleCode", ""),
        "roleName": raw_data.get("result", {}).get("roleName", ""),
    }


async def _load_image_as_base64(image_path: str) -> Optional[dict]:
    """
    加载图片文件并转换为base64编码
    
    参数:
    - image_path: 图片相对路径或绝对路径
    
    返回:
    - 字典格式: {"type": "image/jpeg", "data": "base64编码数据"}
    - 如果文件不存在或读取失败,返回 None
    """
    if not image_path:
        return None
    
    try:
        # 解析本地文件系统路径(相对 app 目录)
        base_dir = os.path.dirname(os.path.dirname(__file__))  # .../app
        rel_path = image_path.lstrip("/")  # 移除开头的斜杠
        
        # 如果路径以 app/ 开头,去掉这个前缀
        if rel_path.startswith("app/"):
            rel_path = rel_path[4:]
        
        # 归一化路径并拼接
        fs_path = os.path.normpath(os.path.join(base_dir, rel_path))
        
        # 安全检查:确保路径在基础目录内,防止目录遍历攻击
        if not fs_path.startswith(os.path.normpath(base_dir)):
            logger.warning(f"检测到目录遍历尝试: {fs_path}")
            return None
        
        # 检查文件是否存在
        if not os.path.exists(fs_path) or not os.path.isfile(fs_path):
            logger.warning(f"图片文件不存在: {fs_path}")
            return None
        
        # 读取文件并转换为base64
        async with aiofiles.open(fs_path, 'rb') as f:
            image_data = await f.read()
        
        # 获取文件扩展名以确定MIME类型
        _, ext = os.path.splitext(fs_path)
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.svg': 'image/svg+xml'
        }
        mime_type = mime_types.get(ext.lower(), 'image/jpeg')
        
        # 编码为base64
        base64_data = base64.b64encode(image_data).decode('utf-8')
        
        # 返回分离的格式: type 和 data
        return {
            "type": mime_type,
            "data": base64_data
        }
        
    except Exception as e:
        logger.error(f"加载图片失败 {image_path}: {str(e)}")
        return None


@router.get("/hospitals", response_model=ResponseModel)
async def get_hospitals(
    area_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """获取院区列表 - 公开接口,无需登录
    
    参数:
    - area_id: 可选,指定院区ID则返回该院区信息,不传则返回全部院区
    
    返回:
    - areas: 院区列表,包含 area_id, name, destination, latitude, longitude, image (base64编码)
    """
    try:
        # 构建查询
        stmt = select(HospitalArea)
        if area_id is not None:
            stmt = stmt.where(HospitalArea.area_id == area_id)
        
        result = await db.execute(stmt)
        areas = result.scalars().all()
        
        # 构建响应
        area_list = []
        for area in areas:
            # 加载图片并转换为base64
            image_type = None
            image_data = None
            if area.image_url:
                image_result = await _load_image_as_base64(area.image_url)
                if image_result:
                    image_type = image_result["type"]
                    image_data = image_result["data"]
            
            area_list.append({
                "area_id": area.area_id,
                "name": area.name,
                "destination": area.destination,
                "latitude": float(area.latitude) if area.latitude else None,
                "longitude": float(area.longitude) if area.longitude else None,
                "image_type": image_type,  # MIME类型,如 "image/jpeg"
                "image_data": image_data,  # base64编码数据
                "create_time": area.create_time.isoformat() if area.create_time else None
            })
        
        return ResponseModel(code=0, message={"areas": area_list})
        
    except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
        raise
    except Exception as e:
        logger.error(f"查询院区失败: {e}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg=f"查询院区失败: {str(e)}"
        )


@router.get("/major-departments", response_model=ResponseModel)
async def get_major_departments(
    db: AsyncSession = Depends(get_db)
):
    """获取所有大科室 - 公开接口,无需登录
    
    返回:
    - departments: 大科室列表,包含 major_dept_id, name, description
    """
    try:
        result = await db.execute(select(MajorDepartment))
        departments = result.scalars().all()
        
        dept_list = [
            {
                "major_dept_id": dept.major_dept_id,
                "name": dept.name,
                "description": dept.description
            }
            for dept in departments
        ]
        
        return ResponseModel(code=0, message={"departments": dept_list})
        
    except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
        raise
    except Exception as e:
        logger.error(f"获取大科室列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/minor-departments", response_model=ResponseModel)
async def get_minor_departments(
    major_dept_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """获取小科室列表 - 公开接口,无需登录,可按大科室过滤,支持分页
    
    参数:
    - major_dept_id: 可选,按大科室ID过滤
    - page: 页码,默认1
    - page_size: 每页数量,默认50
    
    返回:
    - total: 总记录数
    - page: 当前页码
    - page_size: 每页数量
    - departments: 小科室列表,包含价格配置
    """
    try:
        # 构建查询条件
        filters = []
        if major_dept_id is not None:
            filters.append(MinorDepartment.major_dept_id == major_dept_id)

        # 查询总数
        count_query = select(func.count()).select_from(MinorDepartment).where(and_(*filters) if filters else True)
        total = await db.scalar(count_query)
        
        # 分页查询
        offset = (page - 1) * page_size
        result = await db.execute(
            select(MinorDepartment)
            .where(and_(*filters) if filters else True)
            .offset(offset)
            .limit(page_size)
        )
        depts = result.scalars().all()

        # 批量获取所有小科室的价格配置,避免 N+1 查询
        prices_map = await bulk_get_minor_dept_prices(db, depts)

        dept_list = []
        for d in depts:
            prices = prices_map.get(d.minor_dept_id, {
                "default_price_normal": None,
                "default_price_expert": None,
                "default_price_special": None
            })

            dept_list.append({
                "minor_dept_id": d.minor_dept_id,
                "major_dept_id": d.major_dept_id,
                "name": d.name,
                "description": d.description,
                "default_price_normal": prices.get("default_price_normal"),
                "default_price_expert": prices.get("default_price_expert"),
                "default_price_special": prices.get("default_price_special")
            })

        return ResponseModel(code=0, message={
            "total": total,
            "page": page,
            "page_size": page_size,
            "departments": dept_list
        })
        
    except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
        raise
    except Exception as e:
        logger.error(f"获取小科室列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/clinics", response_model=ResponseModel)
async def get_clinics(
    dept_id: Optional[int] = None,
    area_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """获取门诊列表 - 公开接口,无需登录,可按科室/院区过滤,支持分页
    
    参数:
    - dept_id: 可选,按小科室ID过滤
    - area_id: 可选,按院区ID过滤
    - page: 页码,默认1
    - page_size: 每页数量,默认50
    
    返回:
    - total: 总记录数
    - page: 当前页码
    - page_size: 每页数量
    - clinics: 门诊列表,包含价格配置
    """
    try:
        filters = []
        if dept_id:
            filters.append(Clinic.minor_dept_id == dept_id)
        if area_id:
            filters.append(Clinic.area_id == area_id)

        # 查询总数
        count_query = select(func.count()).select_from(Clinic).where(and_(*filters) if filters else True)
        total = await db.scalar(count_query)
        
        # 分页查询
        offset = (page - 1) * page_size
        result = await db.execute(
            select(Clinic)
            .where(and_(*filters) if filters else True)
            .offset(offset)
            .limit(page_size)
        )
        clinics = result.scalars().all()

        # 批量获取所有门诊的价格配置,避免 N+1 查询
        prices_map = await bulk_get_clinic_prices(db, clinics)

        clinic_list = []
        for c in clinics:
            prices = prices_map.get(c.clinic_id, {
                "default_price_normal": None,
                "default_price_expert": None,
                "default_price_special": None
            })
            
            clinic_list.append({
                "clinic_id": c.clinic_id,
                "area_id": c.area_id,
                "name": c.name,
                "address": c.address,
                "minor_dept_id": c.minor_dept_id,
                "clinic_type": c.clinic_type,
                "default_price_normal": prices["default_price_normal"],
                "default_price_expert": prices["default_price_expert"],
                "default_price_special": prices["default_price_special"]
            })

        return ResponseModel(code=0, message={
            "total": total,
            "page": page,
            "page_size": page_size,
            "clinics": clinic_list
        })
        
    except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
        raise
    except Exception as e:
        logger.error(f"获取门诊列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/doctors", response_model=ResponseModel)
async def get_doctors(
    dept_id: Optional[int] = None,
    name: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """获取医生列表 - 公开接口,无需登录,可按科室过滤和姓名模糊搜索,支持分页
    
    参数:
    - dept_id: 可选,按小科室ID过滤
    - name: 可选,按医生姓名模糊搜索
    - page: 页码,默认1
    - page_size: 每页数量,默认50
    
    返回:
    - total: 总记录数
    - page: 当前页码
    - page_size: 每页数量
    - doctors: 医生列表,包含价格配置和注册状态
    """
    try:
        # 构建查询条件
        filters = []
        if dept_id:
            filters.append(Doctor.dept_id == dept_id)
        if name:
            filters.append(Doctor.name.like(f"%{name}%"))
        
        # 查询总数
        count_query = select(func.count()).select_from(Doctor).where(and_(*filters) if filters else True)
        total = await db.scalar(count_query)
        
        # 分页查询
        offset = (page - 1) * page_size
        result = await db.execute(
            select(Doctor)
            .where(and_(*filters) if filters else True)
            .offset(offset)
            .limit(page_size)
        )
        doctors = result.scalars().all()
        
        # 预取所有关联的 user(避免循环中多次查询)
        user_ids = [d.user_id for d in doctors if d.user_id]
        users_map = {}
        if user_ids:
            res_users = await db.execute(select(User).where(User.user_id.in_(user_ids)))
            users = res_users.scalars().all()
            users_map = {u.user_id: u for u in users}

        # 批量获取价格,避免循环内 await 造成 N+1 查询
        prices_map = await bulk_get_doctor_prices(db, doctors)

        doctor_list = []
        for doctor in doctors:
            # 判断是否已注册账号
            is_registered = False
            if doctor.user_id:
                u = users_map.get(doctor.user_id)
                if u and getattr(u, "is_active", False) and not getattr(u, "is_deleted", False):
                    is_registered = True

            prices = prices_map.get(doctor.doctor_id, {
                "default_price_normal": None,
                "default_price_expert": None,
                "default_price_special": None
            })

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
                "default_price_normal": prices["default_price_normal"],
                "default_price_expert": prices["default_price_expert"],
                "default_price_special": prices["default_price_special"]
            })
        
        return ResponseModel(
            code=0,
            message={
                "total": total,
                "page": page,
                "page_size": page_size,
                "doctors": doctor_list
            }
        )
        
    except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
        raise
    except Exception as e:
        logger.error(f"获取医生列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/doctors/{doctor_id}", response_model=ResponseModel)
async def get_doctor_detail(
    doctor_id: int,
    db: AsyncSession = Depends(get_db)
):
    """获取医生详情 - 公开接口,无需登录,根据医生ID精准查询
    
    参数:
    - doctor_id: 医生ID
    
    返回:
    - 医生的完整信息,包含基本信息、科室、价格、注册状态等
    """
    try:
        # 查询医生及其关联信息
        result = await db.execute(
            select(Doctor, MinorDepartment, Clinic, HospitalArea)
            .join(MinorDepartment, MinorDepartment.minor_dept_id == Doctor.dept_id)
            .join(Clinic, Clinic.minor_dept_id == MinorDepartment.minor_dept_id)
            .join(HospitalArea, HospitalArea.area_id == Clinic.area_id)
            .where(Doctor.doctor_id == doctor_id)
            .distinct()
        )
        row = result.first()
        
        if not row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )
        
        doctor, minor_dept, clinic, area = row
        
        # 判断是否已注册账号
        is_registered = False
        if doctor.user_id:
            user_res = await db.execute(
                select(User).where(User.user_id == doctor.user_id)
            )
            user = user_res.scalar_one_or_none()
            if user and getattr(user, "is_active", False) and not getattr(user, "is_deleted", False):
                is_registered = True
        
        # 获取医生价格配置
        prices = await bulk_get_doctor_prices(db, [doctor])
        price_info = prices.get(doctor.doctor_id, {
            "default_price_normal": None,
            "default_price_expert": None,
            "default_price_special": None
        })
        
        return ResponseModel(code=0, message={
            "doctor_id": doctor.doctor_id,
            "user_id": doctor.user_id,
            "name": doctor.name,
            "title": doctor.title,
            "specialty": doctor.specialty,
            "introduction": doctor.introduction,
            "photo_path": doctor.photo_path,
            "original_photo_url": doctor.original_photo_url,
            "department_id": minor_dept.minor_dept_id if minor_dept else None,
            "department_name": minor_dept.name if minor_dept else None,
            "area_id": area.area_id if area else None,
            "area_name": area.name if area else None,
            "is_department_head": doctor.is_department_head,
            "default_price_normal": price_info["default_price_normal"],
            "default_price_expert": price_info["default_price_expert"],
            "default_price_special": price_info["default_price_special"]
        })
        
    except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
        raise
    except Exception as e:
        logger.error(f"获取医生详情时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/search/global", response_model=ResponseModel)
async def global_search(
    keyword: str,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """全局搜索接口 - 公开接口,无需登录,支持按关键词搜索医生、科室
    
    参数:
    - keyword: 搜索关键词(必填),支持搜索医生姓名、科室名称
    - page: 页码,默认1
    - page_size: 每页数量,默认50
    
    返回:
    - doctors: 匹配的医生列表
    - departments: 匹配的科室列表
    - total: 总匹配数
    """
    try:
        if not keyword or keyword.strip() == "":
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="搜索关键词不能为空",
                status_code=400
            )
        
        keyword = keyword.strip()
        offset = (page - 1) * page_size
        
        # 搜索医生
        doctor_result = await db.execute(
            select(Doctor)
            .where(Doctor.name.like(f"%{keyword}%"))
            .offset(offset)
            .limit(page_size)
        )
        doctors = doctor_result.scalars().all()
        
        # 搜索科室
        dept_result = await db.execute(
            select(MinorDepartment)
            .where(MinorDepartment.name.like(f"%{keyword}%"))
            .offset(offset)
            .limit(page_size)
        )
        departments = dept_result.scalars().all()
        
        # 查询医生总数
        doctor_count = await db.scalar(
            select(func.count()).select_from(Doctor)
            .where(Doctor.name.like(f"%{keyword}%"))
        )
        
        # 查询科室总数
        dept_count = await db.scalar(
            select(func.count()).select_from(MinorDepartment)
            .where(MinorDepartment.name.like(f"%{keyword}%"))
        )
        
        # 预取医生关联的user信息
        doctor_ids = [d.doctor_id for d in doctors]
        user_ids = [d.user_id for d in doctors if d.user_id]
        users_map = {}
        if user_ids:
            res_users = await db.execute(select(User).where(User.user_id.in_(user_ids)))
            users = res_users.scalars().all()
            users_map = {u.user_id: u for u in users}
        
        # 批量获取医生价格
        prices_map = await bulk_get_doctor_prices(db, doctors)
        
        # 构建医生列表
        doctor_list = []
        for doctor in doctors:
            is_registered = False
            if doctor.user_id:
                u = users_map.get(doctor.user_id)
                if u and getattr(u, "is_active", False) and not getattr(u, "is_deleted", False):
                    is_registered = True
            
            prices = prices_map.get(doctor.doctor_id, {
                "default_price_normal": None,
                "default_price_expert": None,
                "default_price_special": None
            })
            
            doctor_list.append({
                "type": "doctor",
                "doctor_id": doctor.doctor_id,
                "name": doctor.name,
                "title": doctor.title,
                "specialty": doctor.specialty,
                "introduction": doctor.introduction,
                "photo_path": doctor.photo_path,
                "original_photo_url": doctor.original_photo_url,
                "dept_id": doctor.dept_id,
                "default_price_normal": prices["default_price_normal"],
                "default_price_expert": prices["default_price_expert"],
                "default_price_special": prices["default_price_special"]
            })
        
        # 构建科室列表
        dept_list = []
        for dept in departments:
            dept_list.append({
                "type": "department",
                "minor_dept_id": dept.minor_dept_id,
                "major_dept_id": dept.major_dept_id,
                "name": dept.name,
                "description": dept.description
            })
        
        # 合并结果
        results = doctor_list + dept_list
        total = doctor_count + dept_count
        
        return ResponseModel(
            code=0,
            message={
                "keyword": keyword,
                "total": total,
                "page": page,
                "page_size": page_size,
                "results": results,
                "doctor_count": doctor_count,
                "department_count": dept_count
            }
        )
        
    except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
        raise
    except Exception as e:
        logger.error(f"全局搜索发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="搜索失败",
            status_code=500
        )


@router.get("/doctors/{doctor_id}/photo")
async def get_doctor_photo(
    doctor_id: int,
    db: AsyncSession = Depends(get_db)
):
    """获取医生照片二进制数据 - 公开接口,无需登录
    
    参数:
    - doctor_id: 医生ID
    
    返回:
    - 医生照片的二进制数据,MIME类型根据文件后缀自动判断
    - 如果医生不存在或照片不存在,返回404错误
    """
    try:
        # 查询医生
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        
        if not db_doctor:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )
        
        # 检查是否有本地照片路径
        if not db_doctor.photo_path:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="该医生暂无本地照片",
                status_code=404
            )
        
        # 解析本地文件系统路径(相对 app 目录)
        base_dir = os.path.dirname(os.path.dirname(__file__))  # .../app
        rel_path = db_doctor.photo_path.lstrip("/")  # 移除开头的斜杠
        
        # 如果路径以 app/ 开头,去掉这个前缀
        if rel_path.startswith("app/"):
            rel_path = rel_path[4:]
        
        # 归一化路径并拼接
        fs_path = os.path.normpath(os.path.join(base_dir, rel_path))
        
        # 安全检查:确保路径在基础目录内,防止目录遍历攻击
        if not fs_path.startswith(os.path.normpath(base_dir)):
            logger.warning(f"检测到目录遍历尝试: {fs_path}")
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生照片文件不存在",
                status_code=404
            )
        
        # 检查文件是否存在且是文件(不是目录)
        if not os.path.exists(fs_path) or os.path.isdir(fs_path):
            logger.warning(f"医生照片文件不存在: {fs_path}")
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生照片文件不存在",
                status_code=404
            )
        
        # 判断MIME类型
        import mimetypes
        mime_type, _ = mimetypes.guess_type(fs_path)
        if not mime_type:
            mime_type = "application/octet-stream"
        
        # 定义流式读取函数
        def file_iterator(path: str, chunk_size: int = 8192):
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        
        # 返回流式响应
        from fastapi.responses import StreamingResponse
        return StreamingResponse(file_iterator(fs_path), media_type=mime_type)
        
    except ResourceHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取医生照片数据时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/departments/{dept_id}/schedules", response_model=ResponseModel)
async def get_department_schedules(
    dept_id: int,
    start_date: str,
    end_date: str,
    db: AsyncSession = Depends(get_db)
):
    """获取科室排班 - 公开接口,无需登录
    
    参数:
    - dept_id: 小科室ID
    - start_date: 开始日期,格式 YYYY-MM-DD
    - end_date: 结束日期,格式 YYYY-MM-DD
    
    返回:
    - schedules: 排班列表,包含医生、门诊、时间、号源等信息
    """
    try:
        # 校验科室
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == dept_id))
        if not result.scalar_one_or_none():
            raise ResourceHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="小科室不存在",
                status_code=400
            )

        # 日期解析
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

        # 查询:该小科室下的门诊 -> 排班
        result = await db.execute(select(Clinic.clinic_id).where(Clinic.minor_dept_id == dept_id))
        clinic_ids = [row[0] for row in result.all()]
        if not clinic_ids:
            return ResponseModel(code=0, message={"schedules": []})

        result = await db.execute(
            select(Schedule, Doctor.name, Clinic.name, Clinic.clinic_type)
            .join(Doctor, Doctor.doctor_id == Schedule.doctor_id)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .where(
                and_(
                    Schedule.clinic_id.in_(clinic_ids),
                    Schedule.date >= start_dt,
                    Schedule.date <= end_dt,
                )
            )
            .order_by(Schedule.date, Schedule.time_section)
        )

        rows = result.all()
        data = []
        for sch, doctor_name, clinic_name, clinic_type in rows:
            data.append({
                "schedule_id": sch.schedule_id,
                "doctor_id": sch.doctor_id,
                "doctor_name": doctor_name,
                "clinic_id": sch.clinic_id,
                "clinic_name": clinic_name,
                "clinic_type": clinic_type,
                "date": str(sch.date),
                "week_day": _weekday_to_cn(sch.week_day),
                "time_section": sch.time_section,
                "slot_type": _slot_type_to_str(sch.slot_type),
                "total_slots": sch.total_slots,
                "remaining_slots": sch.remaining_slots,
                "status": sch.status,
                "price": float(sch.price)
            })

        return ResponseModel(code=0, message={"schedules": data})
        
    except ResourceHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取科室排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/doctors/{doctor_id}/schedules", response_model=ResponseModel)
async def get_doctor_schedules(
    doctor_id: int,
    start_date: str,
    end_date: str,
    db: AsyncSession = Depends(get_db)
):
    """获取医生排班 - 公开接口,无需登录
    
    参数:
    - doctor_id: 医生ID
    - start_date: 开始日期,格式 YYYY-MM-DD
    - end_date: 结束日期,格式 YYYY-MM-DD
    
    返回:
    - schedules: 该医生的排班列表
    """
    try:
        # 校验医生
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        if not result.scalar_one_or_none():
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

        result = await db.execute(
            select(Schedule, Doctor.name, Clinic.name, Clinic.clinic_type)
            .join(Doctor, Doctor.doctor_id == Schedule.doctor_id)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .where(
                and_(
                    Schedule.doctor_id == doctor_id,
                    Schedule.date >= start_dt,
                    Schedule.date <= end_dt,
                )
            )
            .order_by(Schedule.date, Schedule.time_section)
        )

        rows = result.all()
        data = []
        for sch, doctor_name, clinic_name, clinic_type in rows:
            data.append({
                "schedule_id": sch.schedule_id,
                "doctor_id": sch.doctor_id,
                "doctor_name": doctor_name,
                "clinic_id": sch.clinic_id,
                "clinic_name": clinic_name,
                "clinic_type": clinic_type,
                "date": str(sch.date),
                "week_day": _weekday_to_cn(sch.week_day),
                "time_section": sch.time_section,
                "slot_type": _slot_type_to_str(sch.slot_type),
                "total_slots": sch.total_slots,
                "remaining_slots": sch.remaining_slots,
                "status": sch.status,
                "price": float(sch.price)
            })

        return ResponseModel(code=0, message={"schedules": data})
        
    except ResourceHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取医生排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/clinics/{clinic_id}/schedules", response_model=ResponseModel)
async def get_clinic_schedules(
    clinic_id: int,
    start_date: str,
    end_date: str,
    db: AsyncSession = Depends(get_db)
):
    """获取门诊排班 - 公开接口,无需登录
    
    参数:
    - clinic_id: 门诊ID
    - start_date: 开始日期,格式 YYYY-MM-DD
    - end_date: 结束日期,格式 YYYY-MM-DD
    
    返回:
    - schedules: 该门诊的排班列表
    """
    try:
        # 校验门诊
        result = await db.execute(select(Clinic).where(Clinic.clinic_id == clinic_id))
        if not result.scalar_one_or_none():
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="门诊不存在",
                status_code=404
            )

        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

        result = await db.execute(
            select(Schedule, Doctor.name, Clinic.name, Clinic.clinic_type)
            .join(Doctor, Doctor.doctor_id == Schedule.doctor_id)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .where(
                and_(
                    Schedule.clinic_id == clinic_id,
                    Schedule.date >= start_dt,
                    Schedule.date <= end_dt,
                )
            )
            .order_by(Schedule.date, Schedule.time_section)
        )

        rows = result.all()
        data = []
        for sch, doctor_name, clinic_name, clinic_type in rows:
            data.append({
                "schedule_id": sch.schedule_id,
                "doctor_id": sch.doctor_id,
                "doctor_name": doctor_name,
                "clinic_id": sch.clinic_id,
                "clinic_name": clinic_name,
                "clinic_type": clinic_type,
                "date": str(sch.date),
                "week_day": _weekday_to_cn(sch.week_day),
                "time_section": sch.time_section,
                "slot_type": _slot_type_to_str(sch.slot_type),
                "total_slots": sch.total_slots,
                "remaining_slots": sch.remaining_slots,
                "status": sch.status,
                "price": float(sch.price)
            })

        return ResponseModel(code=0, message={"schedules": data})
        
    except ResourceHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取门诊排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/hospitals/schedules", response_model=ResponseModel)
async def get_schedules(
    hospitalId: Optional[int] = None,
    departmentId: Optional[int] = None,
    date: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """获取医生排班列表 - 公开接口,无需登录
    
    参数:
    - hospitalId: 可选,院区ID(hospital_area.area_id)
    - departmentId: 必填,科室ID(minor_department.minor_dept_id)
    - date: 可选,日期(格式: YYYY-MM-DD),不传则返回未来7天
    
    返回:
    - schedules: 排班列表,按日期、时间段排序
    """
    try:
        # departmentId 必填
        if not departmentId:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="departmentId 参数为必填项",
                status_code=400
            )
        
        # 校验科室是否存在
        result = await db.execute(
            select(MinorDepartment).where(MinorDepartment.minor_dept_id == departmentId)
        )
        if not result.scalar_one_or_none():
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="科室不存在",
                status_code=404
            )

        # 日期处理:如果不传 date,则默认查询未来7天
        if date:
            # 传了 date,则只查询该天
            start_dt = datetime.strptime(date, "%Y-%m-%d").date()
            end_dt = start_dt
        else:
            # 未传 date,查询从今天开始的未来7天
            start_dt = datetime.now().date()
            end_dt = start_dt + timedelta(days=6)  # 今天 + 未来6天 = 共7天

        # 查询该小科室下的所有门诊
        clinic_query = select(Clinic.clinic_id).where(Clinic.minor_dept_id == departmentId)
        
        # 如果指定了 hospitalId,进一步过滤
        if hospitalId is not None:
            clinic_query = clinic_query.where(Clinic.area_id == hospitalId)
        
        result = await db.execute(clinic_query)
        clinic_ids = [row[0] for row in result.all()]
        
        if not clinic_ids:
            return ResponseModel(code=0, message={"schedules": []})

        # 查询排班
        result = await db.execute(
            select(Schedule, Doctor.name, Doctor.title, Clinic.name, Clinic.clinic_type, Clinic.area_id)
            .join(Doctor, Doctor.doctor_id == Schedule.doctor_id)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .where(
                and_(
                    Schedule.clinic_id.in_(clinic_ids),
                    Schedule.date >= start_dt,
                    Schedule.date <= end_dt,
                )
            )
            .order_by(Schedule.date, Schedule.time_section)
        )

        rows = result.all()
        data = []
        for sch, doctor_name, doctor_title, clinic_name, clinic_type, area_id in rows:
            data.append({
                "schedule_id": sch.schedule_id,
                "doctor_id": sch.doctor_id,
                "doctor_name": doctor_name,
                "doctor_title": doctor_title,
                "clinic_id": sch.clinic_id,
                "clinic_name": clinic_name,
                "clinic_type": clinic_type,
                "area_id": area_id,
                "date": str(sch.date),
                "week_day": _weekday_to_cn(sch.week_day),
                "time_section": sch.time_section,
                "slot_type": _slot_type_to_str(sch.slot_type),
                "total_slots": sch.total_slots,
                "remaining_slots": sch.remaining_slots,
                "status": sch.status,
                "price": float(sch.price)
            })

        return ResponseModel(code=0, message={"schedules": data})
        
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取排班列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ====== 预约管理接口(需要登录) ======


def _generate_order_no() -> str:
    """生成订单号: YYYYMMDD + 8位随机数"""
    import random
    date_str = datetime.now().strftime("%Y%m%d")
    random_num = random.randint(10000000, 99999999)
    return f"{date_str}{random_num}"

def _get_time_section_start(time_section: str, schedule_config: dict) -> str:
    """根据时间段获取开始时间字符串"""
    section = (time_section or "").strip()
    if section in ["上午", "早上", "morning"]:
        return schedule_config.get("morningStart", "08:00")
    if section in ["下午", "中午", "afternoon"]:
        return schedule_config.get("afternoonStart", "13:30")
    return schedule_config.get("eveningStart", "18:00")

def _build_schedule_start_datetime(schedule: Schedule, schedule_config: dict) -> datetime:
    """构建排班的开始时间点"""
    time_str = _get_time_section_start(schedule.time_section, schedule_config or {})
    hour, minute = parse_time_to_hour_minute(time_str)
    return datetime.combine(schedule.date, datetime.min.time()).replace(hour=hour, minute=minute)


def _format_wechat_datetime(
    slot_date: datetime.date,
    time_section: str,
    schedule_config: Optional[dict] = None,
) -> str:
    """格式化微信消息用的就诊时间字符串，仅返回日期+时间，不含时段文字（符合微信模板格式要求）。"""
    section = (time_section or "").strip()
    time_str = _get_time_section_start(section, schedule_config or {})
    # 仅返回 "YYYY年MM月DD日 HH:MM" 格式，不包含"上午/下午"等文字
    return f"{slot_date.strftime('%Y年%m月%d日')} {time_str}"


async def _load_doctor_and_dept(db: AsyncSession, schedule: Schedule) -> tuple[Optional[Doctor], Optional[Clinic], Optional[MinorDepartment]]:
    doctor = await db.get(Doctor, schedule.doctor_id) if schedule and schedule.doctor_id else None
    clinic = await db.get(Clinic, schedule.clinic_id) if schedule and schedule.clinic_id else None
    dept = await db.get(MinorDepartment, clinic.minor_dept_id) if clinic and clinic.minor_dept_id else None
    return doctor, clinic, dept


def _wechat_payload_appointment(patient_name: str, datetime_str: str, location: str, doctor_name: str, status: str) -> dict:
    """预约成功通知模板 (thing65就诊人, time67就诊时间, thing2预约地点, thing69预约医师, phrase14预约状态)。
    
    模板ID: RFZQNIC-vGQC_mkDcqAneHMamQUhmWIn82L2FwsiC5A (模板编号461)
    适用场景: 预约成功
    """
    return {
        "thing65": {"value": patient_name or ""},
        "time67": {"value": datetime_str},
        "thing2": {"value": location or ""},
        "thing69": {"value": doctor_name or ""},
        "phrase14": {"value": status},
    }


def _wechat_payload_waitlist(patient_name: str, datetime_str: str, location: str, doctor_name: str, status: str) -> dict:
    """候补成功通知模板。

    根据微信后台字段：
    - thing6: 姓名
    - phrase1: 候补结果
    - thing4: 活动地点（这里用门诊/诊室名）
    - time3: 活动时间
    - thing5: 温馨提示（复用医生/备注）
    """
    return {
        "thing6": {"value": patient_name or "就诊人"},
        "phrase1": {"value": status or "候补成功"},
        "thing4": {"value": location or ""},
        "time3": {"value": datetime_str},
        "thing5": {"value": doctor_name or ""},
    }


def _wechat_payload_reschedule(patient_name: str, original_datetime_str: str, new_datetime_str: str, clinic_name: str, reason: str) -> dict:
    """改约成功通知模板 (预约人、原预约时间、现预约时间、活动名称、修改原因)。

    模板字段对应微信后台展示：
    - name1: 预约人
    - time3: 原预约时间
    - time14: 现预约时间
    - thing17: 活动名称（这里填门诊/地点）
    - thing2: 修改原因
    """
    return {
        "name1": {"value": patient_name or "就诊人"},
        "time3": {"value": original_datetime_str},
        "time14": {"value": new_datetime_str},
        "thing17": {"value": clinic_name or ""},
        "thing2": {"value": reason or "改约"},
    }


def _wechat_payload_reminder(patient_name: str, datetime_str: str, location: str, tip: str) -> dict:
    """就诊提醒模板 (thing65, time67, thing18, thing8)。"""
    return {
        "thing65": {"value": patient_name or ""},
        "time67": {"value": datetime_str},
        "thing18": {"value": location or ""},
        "thing8": {"value": tip or ""},
    }


def _wechat_payload_cancel(patient_name: str, datetime_str: str, doctor_name: str, reason: str, order_status: str) -> dict:
    """取消通知模板 (thing65, time67, thing69, thing72, phrase26)。"""
    return {
        "thing65": {"value": patient_name or ""},
        "time67": {"value": datetime_str},
        "thing69": {"value": doctor_name or ""},
        "thing72": {"value": reason or ""},
        "phrase26": {"value": order_status or ""},
    }


async def _wechat_prepare_and_send(
    db: AsyncSession,
    target_user_id: int,
    wx_code: Optional[str],
    subscribe_auth: Optional[dict],
    subscribe_scene: Optional[str],
    template_id: str,
    data: dict,
    scene: str,
    order_id: Optional[int],
    page: Optional[str] = None,
) -> None:
    """统一处理 code->openid、授权记录、并发送订阅消息。失败只记录日志不抛错。"""
    if not template_id:
        return

    wechat = WechatService()
    openid = None

    try:
        if wx_code:
            wx_res = await wechat.code_to_openid(wx_code)
            if wx_res and wx_res.get("openid"):
                openid = wx_res.get("openid")
                await wechat.save_user_openid(
                    db,
                    target_user_id,
                    openid,
                    wx_res.get("session_key"),
                    wx_res.get("unionid"),
                )

        if not openid:
            openid = await wechat.get_user_openid(db, target_user_id)

        if subscribe_auth:
            await wechat.save_subscribe_auth(db, target_user_id, subscribe_auth, subscribe_scene or scene)

        if not openid:
            return

        authorized = await wechat.check_user_authorized(db, target_user_id, template_id)
        if not authorized:
            return

        await wechat.send_subscribe_message(
            db,
            target_user_id,
            openid,
            template_id,
            data,
            scene=scene,
            order_id=order_id,
            page=page,
        )
    except Exception as exc:
        logger.warning(f"微信订阅消息处理失败: {exc}")


@router.post("/appointments", response_model=ResponseModel[AppointmentResponse])
async def create_appointment(
    data: AppointmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """创建预约挂号 - 需要登录
    
    业务规则:
    1. 预约成功后立即锁定号源(remaining_slots - 1)
    2. 同一患者同一诊疗单元内,最多可挂同一科室同一类别各1个号
    3. 同一就诊人8天内最多可挂10个号
    4. 检查号源是否充足,不足则返回错误
    """
    try:
        # 1. 验证排班是否存在
        schedule_res = await db.execute(
            select(Schedule).where(Schedule.schedule_id == data.scheduleId)
        )
        schedule = schedule_res.scalar_one_or_none()
        if not schedule:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="排班不存在",
                status_code=404
            )
        
        # 2. 验证患者是否存在且属于当前用户或是当前用户的就诊人
        patient_res = await db.execute(
            select(Patient).where(Patient.patient_id == data.patientId)
        )
        patient = patient_res.scalar_one_or_none()
        if not patient:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="患者不存在",
                status_code=404
            )
        
        # 验证患者归属: 当前用户的患者记录或关联的就诊人
        user_patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        user_patient = user_patient_res.scalar_one_or_none()
        
        if not user_patient:
            raise ResourceHTTPException(
                code=settings.RESOURCE_NOT_FOUND_CODE,
                msg="当前用户未绑定患者信息"
            )
        
        # 检查是否是本人或关联的就诊人
        is_self = (patient.patient_id == user_patient.patient_id)
        is_related = False
        
        if not is_self:
            # 检查是否在就诊人关系表中
            relation_res = await db.execute(
                select(PatientRelation).where(
                    and_(
                        PatientRelation.user_patient_id == user_patient.patient_id,
                        PatientRelation.related_patient_id == data.patientId
                    )
                )
            )
            if relation_res.scalar_one_or_none():
                is_related = True
        
        if not is_self and not is_related:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权为该患者预约",
                status_code=403
            )
        
        # 3. 检查号源是否充足
        if schedule.remaining_slots <= 0:
            raise BusinessHTTPException(
                code=1001,  # 号源已满
                msg="该时段号源已满",
                status_code=400
            )
        
        # 4. 业务规则: 同一患者同一天同一科室同一类别只能挂1个号
        existing_res = await db.execute(
            select(RegistrationOrder).where(
                and_(
                    RegistrationOrder.patient_id == data.patientId,
                    RegistrationOrder.slot_date == schedule.date,
                    RegistrationOrder.schedule_id == data.scheduleId,
                    RegistrationOrder.status.in_([OrderStatus.PENDING, OrderStatus.CONFIRMED])
                )
            )
        )
        if existing_res.scalar_one_or_none():
            raise BusinessHTTPException(
                code=1002,  # 预约冲突
                msg="该时段已有预约",
                status_code=400
            )
        
        # 5. 业务规则: 根据配置限制预约数量
        # 获取挂号配置(支持分级: DOCTOR > CLINIC > GLOBAL)
        reg_config = await get_registration_config(
            db,
            scope_type="DOCTOR",
            scope_id=schedule.doctor_id
        )
        
        # 确保配置不为 None
        if not reg_config:
            reg_config = {}
        
        max_appointments = reg_config.get("maxAppointmentsPerPeriod", 10)
        period_days = reg_config.get("appointmentPeriodDays", 8)
        
        period_start = datetime.now().date() - timedelta(days=period_days)
        count_res = await db.execute(
            select(func.count()).select_from(RegistrationOrder).where(
                and_(
                    RegistrationOrder.patient_id == data.patientId,
                    RegistrationOrder.slot_date >= period_start,
                    RegistrationOrder.status.in_([OrderStatus.PENDING, OrderStatus.CONFIRMED])
                )
            )
        )
        appointment_count = count_res.scalar()
        if appointment_count >= max_appointments:
            raise BusinessHTTPException(
                code=1003,  # 超过预约限制
                msg=f"{period_days}天内最多可挂{max_appointments}个号",
                status_code=400
            )
        
        # 6. 创建订单
        order_no = _generate_order_no()
        
        # 从数据库获取身份折扣配置
        discounts = await get_patient_identity_discounts(db)
        
        # 根据患者身份应用价格折扣
        base_price = schedule.price if schedule.price else 0.0
        discount_rate = 1.0  # 默认无折扣
        
        if patient.patient_type:
            patient_type_value = patient.patient_type
            if isinstance(patient.patient_type, PatientType):
                patient_type_value = patient.patient_type.value
            
            # 从数据库配置中获取折扣率
            discount_rate = discounts.get(patient_type_value, 1.0)
        
        # 计算最终价格，精确到小数点后2位
        final_price = calculate_final_price(base_price, discount_rate)
        
        new_order = RegistrationOrder(
            order_no=order_no,
            patient_id=data.patientId,
            user_id=patient.user_id,  # 就诊患者的user_id，可能为null
            initiator_user_id=current_user.user_id,  # 发起人是当前用户
            doctor_id=schedule.doctor_id,
            schedule_id=data.scheduleId,
            slot_date=schedule.date,
            time_section=schedule.time_section,
            slot_type=str(schedule.slot_type.value if hasattr(schedule.slot_type, 'value') else schedule.slot_type),
            price=final_price,  # 应用折扣后的价格（Decimal，精确到2位小数）
            symptoms=data.symptoms,
            status=OrderStatus.PENDING,  # 待支付
            payment_status=PaymentStatus.PENDING,  # 待支付
            source_type="normal",  # 普通预约
            create_time=datetime.now(),
            update_time=datetime.now()
        )
        
        db.add(new_order)
        
        # 7. 锁定号源 - 减少 remaining_slots
        schedule.remaining_slots -= 1
        db.add(schedule)
        
        await db.commit()
        await db.refresh(new_order)
        
        # 8. 预约创建阶段不再发送“预约成功”订阅消息，改为在支付成功后推送
        
        # 9. 队列号码在就诊时动态分配，创建时不设置
        logger.info(f"创建预约成功: order_id={new_order.order_id}, order_no={order_no}, patient_id={data.patientId}")
        
        return ResponseModel(code=0, message=AppointmentResponse(
            id=new_order.order_id,
            orderNo=order_no,
            queueNumber=None,
            needPay=True,
            payAmount=float(final_price) if final_price else 0.0,
            appointmentDate=str(schedule.date),
            appointmentTime=f"{schedule.time_section}",
            status=new_order.status.value,
            paymentStatus=new_order.payment_status.value
        ))
        
    except AuthHTTPException:
        await db.rollback()
        raise
    except ResourceHTTPException:
        await db.rollback()
        raise
    except BusinessHTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"创建预约时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="创建预约失败",
            status_code=500
        )


@router.get("/appointments", response_model=ResponseModel[AppointmentListResponse])
async def get_my_appointments(
    status: Optional[str] = "all",
    page: int = 1,
    pageSize: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取我的预约列表 - 需要登录
    
    参数:
    - status: 预约状态过滤 (all/pending/completed/cancelled)
    - page: 页码
    - pageSize: 每页条数
    """
    try:
        # 构建查询条件
        filters = [RegistrationOrder.user_id == current_user.user_id]
        
        if status and status != "all":
            status_map = {
                "pending": OrderStatus.PENDING,
                "completed": OrderStatus.COMPLETED,
                "cancelled": OrderStatus.CANCELLED
            }
            if status in status_map:
                filters.append(RegistrationOrder.status == status_map[status])
        
        # 查询总数
        count_res = await db.execute(
            select(func.count()).select_from(RegistrationOrder).where(and_(*filters))
        )
        total = count_res.scalar()
        
        # 分页查询
        offset = (page - 1) * pageSize
        result = await db.execute(
            select(RegistrationOrder, Schedule, Doctor, Clinic, MinorDepartment, HospitalArea, Patient)
            .join(Schedule, Schedule.schedule_id == RegistrationOrder.schedule_id)
            .join(Doctor, Doctor.doctor_id == RegistrationOrder.doctor_id)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .join(MinorDepartment, MinorDepartment.minor_dept_id == Clinic.minor_dept_id)
            .join(HospitalArea, HospitalArea.area_id == Clinic.area_id)
            .join(Patient, Patient.patient_id == RegistrationOrder.patient_id)
            .where(and_(*filters))
            .order_by(RegistrationOrder.create_time.desc())
            .offset(offset)
            .limit(pageSize)
        )
        
        rows = result.all()
        
        # 获取排班配置用于判断取消时间(只查询一次)
        schedule_config = await get_schedule_config(db)
        
        # 按医生分组获取挂号配置(减少重复查询)
        doctor_ids = list(set(order.doctor_id for order, *_ in rows))
        doctor_configs = {}
        for doctor_id in doctor_ids:
            doctor_configs[doctor_id] = await get_registration_config(
                db,
                scope_type="DOCTOR",
                scope_id=doctor_id
            )
        
        appointment_list = []
        for order, schedule, doctor, clinic, dept, area, patient in rows:
            # 判断是否可取消(根据配置动态计算)
            can_cancel = False
            if order.status in [OrderStatus.PENDING, OrderStatus.CONFIRMED]:
                # 使用缓存的配置
                reg_config = doctor_configs.get(order.doctor_id, {})
                cancel_hours_before = reg_config.get("cancelHoursBefore", 2)
                
                now = datetime.now()
                appointment_datetime = datetime.combine(order.slot_date, datetime.min.time())
                
                # 根据时间段从配置中获取开始时间
                if order.time_section == "上午":
                    time_str = schedule_config.get("morningStart", "08:00")
                elif order.time_section == "下午":
                    time_str = schedule_config.get("afternoonStart", "13:30")
                else:
                    time_str = schedule_config.get("eveningStart", "18:00")
                
                hour, minute = parse_time_to_hour_minute(time_str)
                cancel_deadline = appointment_datetime.replace(hour=hour, minute=minute) - timedelta(hours=cancel_hours_before)
                
                can_cancel = now < cancel_deadline
            can_reschedule = False
            if order.status in [OrderStatus.PENDING, OrderStatus.CONFIRMED] and schedule:
                start_dt = _build_schedule_start_datetime(schedule, schedule_config or {})
                can_reschedule = start_dt > datetime.now()
            
            appointment_list.append(AppointmentListItem(
                id=order.order_id,
                orderNo=order.order_no if order.order_no else _generate_order_no(),
                hospitalId=area.area_id,
                hospitalName=area.name,
                departmentId=dept.minor_dept_id,
                departmentName=dept.name,
                doctorName=doctor.name,
                doctorTitle=doctor.title or "",
                scheduleId=schedule.schedule_id,
                appointmentDate=str(order.slot_date),
                appointmentTime=f"{order.time_section}",
                patientName=patient.name,
                patientId=patient.patient_id,
                queueNumber=None,  # TODO: 实时计算队列号
                price=float(order.price) if order.price else 0.0,
                status=order.status.value,
                paymentStatus=order.payment_status.value,
                canCancel=can_cancel,
                canReschedule=can_reschedule,
                sourceType=order.source_type if hasattr(order, 'source_type') and order.source_type else "normal",
                createdAt=order.create_time.strftime("%Y-%m-%d %H:%M:%S") if order.create_time else ""
            ))
        
        return ResponseModel(code=0, message=AppointmentListResponse(
            total=total,
            page=page,
            pageSize=pageSize,
            list=appointment_list
        ))
        
    except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
        raise
    except Exception as e:
        logger.error(f"获取预约列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="获取预约列表失败",
            status_code=500
        )


@router.put("/appointments/{appointmentId}/cancel", response_model=ResponseModel[CancelAppointmentResponse])
async def cancel_appointment(
    appointmentId: int,
    payload: CancelAppointmentRequest | None = Body(None),
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """取消预约 - 需要登录
    
    取消规则:
    - 上午号: 最晚于就诊日当天 8:00前 取消
    - 下午号: 最晚于就诊日当天 13:00前 取消
    - 晚间号: 最晚于就诊日当天 18:00前 取消
    - 超过时间需到医院挂号窗口办理
    - 取消后释放号源
    """
    try:
        # 1. 查询订单
        order_res = await db.execute(
            select(RegistrationOrder, Schedule).join(
                Schedule, Schedule.schedule_id == RegistrationOrder.schedule_id
            ).where(RegistrationOrder.order_id == appointmentId)
        )
        row = order_res.first()
        
        if not row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="预约不存在",
                status_code=404
            )
        
        order, schedule = row
        
        # 2. 验证订单归属: 发起人或就诊人本人都可以取消
        # 获取当前用户的患者记录
        user_patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        user_patient = user_patient_res.scalar_one_or_none()
        
        if not user_patient:
            raise ResourceHTTPException(
                code=settings.RESOURCE_NOT_FOUND_CODE,
                msg="当前用户未绑定患者信息"
            )
        
        # 检查是否是发起人或就诊人本人
        is_initiator = (order.initiator_user_id == current_user.user_id)
        is_patient = (order.patient_id == user_patient.patient_id)
        
        if not is_initiator and not is_patient:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权操作该预约",
                status_code=403
            )
        
        # 3. 检查订单状态
        if order.status not in [OrderStatus.PENDING, OrderStatus.CONFIRMED]:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="该预约无法取消",
                status_code=400
            )
        
        # 4. 若为待支付订单，先检查是否支付超时并自动取消
        if order.status == OrderStatus.PENDING and order.payment_status == PaymentStatus.PENDING:
            reg_config = await get_registration_config(
                db,
                scope_type="DOCTOR",
                scope_id=order.doctor_id
            )
            timeout_minutes = int(reg_config.get("paymentTimeoutMinutes", 30))
            now = datetime.now()
            create_time = order.create_time or now
            if now >= (create_time + timedelta(minutes=timeout_minutes)):
                # 超时自动取消：标记为已超时，支付失败
                order.status = OrderStatus.TIMEOUT
                order.payment_status = PaymentStatus.FAILED
                order.cancel_time = now
                order.update_time = now
                db.add(order)
                # 释放号源
                if schedule:
                    schedule.remaining_slots = (schedule.remaining_slots or 0) + 1
                    db.add(schedule)
                await db.commit()
                # 发送微信取消通知（支付超时）
                try:
                    patient_obj = await db.get(Patient, order.patient_id) if order.patient_id else None
                    doctor, clinic, _ = await _load_doctor_and_dept(db, schedule)
                    patient_name = patient_obj.name if patient_obj else ""
                    doctor_name = doctor.name if doctor else ""
                    schedule_config = await get_schedule_config(db)
                    datetime_str = _format_wechat_datetime(order.slot_date, order.time_section, schedule_config)
                    reason_text = "支付超时"
                    status_text = "已超时"
                    template_id = settings.WECHAT_TEMPLATE_CANCEL_SUCCESS
                    data_payload = _wechat_payload_cancel(
                        patient_name,
                        datetime_str,
                        doctor_name,
                        reason_text,
                        status_text,
                    )
                    target_user_id = patient_obj.user_id if patient_obj and patient_obj.user_id else current_user.user_id
                    wx_code = payload.wxCode if payload else None
                    subscribe_auth = payload.subscribeAuthResult if payload else None
                    subscribe_scene = payload.subscribeScene if payload else "cancel"
                    await _wechat_prepare_and_send(
                        db,
                        target_user_id,
                        wx_code,
                        subscribe_auth,
                        subscribe_scene,
                        template_id,
                        data_payload,
                        scene="cancel",
                        order_id=order.order_id,
                    )
                except Exception as exc:
                    logger.warning(f"取消预约(支付超时)微信通知失败: {exc}")
                return ResponseModel(code=0, message=CancelAppointmentResponse(success=True, refundAmount=None))

        # 5. 检查取消时间限制(根据配置动态计算)
        # 获取排班配置和挂号配置
        schedule_config = await get_schedule_config(db)
        reg_config = await get_registration_config(
            db,
            scope_type="DOCTOR",
            scope_id=order.doctor_id
        )
        
        # 确保配置不为 None
        if not schedule_config:
            schedule_config = {}
        if not reg_config:
            reg_config = {}
        
        cancel_hours_before = reg_config.get("cancelHoursBefore", 2)
        
        now = datetime.now()
        appointment_datetime = datetime.combine(order.slot_date, datetime.min.time())
        
        # 根据时间段从配置中获取开始时间
        if order.time_section == "上午":
            time_str = schedule_config.get("morningStart", "08:00")
        elif order.time_section == "下午":
            time_str = schedule_config.get("afternoonStart", "13:30")
        else:
            time_str = schedule_config.get("eveningStart", "18:00")
        
        hour, minute = parse_time_to_hour_minute(time_str)
        cancel_deadline = appointment_datetime.replace(hour=hour, minute=minute) - timedelta(hours=cancel_hours_before)
        
        if now >= cancel_deadline:
            raise BusinessHTTPException(
                code=1006,  # 超过取消时间
                msg=f"需在就诊时间前{cancel_hours_before}小时取消,已超时请到医院窗口办理",
                status_code=400
            )
        
        # 6. 取消订单
        order.status = OrderStatus.CANCELLED
        order.cancel_time = now
        order.update_time = now
        
        # 6. 处理退款
        refund_amount = 0.0
        if order.payment_status == PaymentStatus.PAID:
            order.payment_status = PaymentStatus.REFUNDED
            order.refund_time = now
            order.refund_amount = order.price
            refund_amount = float(order.price) if order.price else 0.0
        else:
            order.payment_status = PaymentStatus.CANCELLED
        
        db.add(order)
        
        # 7. 释放号源
        schedule.remaining_slots += 1
        db.add(schedule)
        
        await db.commit()

        # 8. 发送微信取消通知
        try:
            patient_obj = await db.get(Patient, order.patient_id) if order.patient_id else None
            doctor, clinic, _ = await _load_doctor_and_dept(db, schedule)
            patient_name = patient_obj.name if patient_obj else ""
            doctor_name = doctor.name if doctor else ""
            datetime_str = _format_wechat_datetime(order.slot_date, order.time_section, schedule_config)
            reason_text = "用户取消预约"
            status_text = "已取消"
            template_id = settings.WECHAT_TEMPLATE_CANCEL_SUCCESS
            data_payload = _wechat_payload_cancel(
                patient_name,
                datetime_str,
                doctor_name,
                reason_text,
                status_text,
            )
            target_user_id = patient_obj.user_id if patient_obj and patient_obj.user_id else current_user.user_id
            wx_code = payload.wxCode if payload else None
            subscribe_auth = payload.subscribeAuthResult if payload else None
            subscribe_scene = payload.subscribeScene if payload else "cancel"
            await _wechat_prepare_and_send(
                db,
                target_user_id,
                wx_code,
                subscribe_auth,
                subscribe_scene,
                template_id,
                data_payload,
                scene="cancel",
                order_id=order.order_id,
            )
        except Exception as exc:
            logger.warning(f"取消预约微信通知失败: {exc}")
        
        # 8. 检查是否有候补，有的话自动转化第一个候补到预约（触发SMS通知）
        # 核心逻辑：级联转换所有候补，直到没有候补或没有剩余号源为止
        try:
            converted_count = 0
            max_attempts = 10  # 防止无限循环，最多尝试转换10个
            
            for attempt in range(max_attempts):
                # 获取下一个候补
                converted_order_id = await WaitlistService.notify_and_convert_first_in_queue(
                    db, 
                    order.schedule_id
                )
                
                if not converted_order_id:
                    # 没有候补了，跳出循环
                    break
                
                converted_count += 1
                logger.info(f"候补已转预约: order_id={converted_order_id}")
                
            if converted_count > 0:
                logger.info(f"自动转化候补成功: 共转化{converted_count}个候补")
            else:
                logger.info(f"号源释放后无候补订单")
        except Exception as e:
            logger.error(f"自动转化候补失败: {str(e)}")
        
        logger.info(f"取消预约成功: order_id={appointmentId}, refund={refund_amount}")
        
        return ResponseModel(code=0, message=CancelAppointmentResponse(
            success=True,
            refundAmount=refund_amount if refund_amount > 0 else None
        ))
        
    except AuthHTTPException:
        await db.rollback()
        raise
    except ResourceHTTPException:
        await db.rollback()
        raise
    except BusinessHTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"取消预约时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="取消预约失败",
            status_code=500
        )


@router.get("/appointments/{appointmentId}/reschedule-options", response_model=ResponseModel[RescheduleOptionsResponse])
async def get_reschedule_options(
    appointmentId: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取可改约的排班列表（同医生、同诊室、同号源）"""
    try:
        order_res = await db.execute(
            select(RegistrationOrder, Schedule, Doctor, Clinic, MinorDepartment, HospitalArea, Patient)
            .join(Schedule, Schedule.schedule_id == RegistrationOrder.schedule_id)
            .join(Doctor, Doctor.doctor_id == RegistrationOrder.doctor_id)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .join(MinorDepartment, MinorDepartment.minor_dept_id == Clinic.minor_dept_id)
            .join(HospitalArea, HospitalArea.area_id == Clinic.area_id)
            .join(Patient, Patient.patient_id == RegistrationOrder.patient_id)
            .where(RegistrationOrder.order_id == appointmentId)
        )
        row = order_res.first()

        if not row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="预约不存在",
                status_code=404
            )

        order, current_schedule, doctor, clinic, dept, area, patient = row

        user_patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        user_patient = user_patient_res.scalar_one_or_none()

        is_initiator = order.initiator_user_id == current_user.user_id
        is_patient = user_patient and order.patient_id == user_patient.patient_id

        if not is_initiator and not is_patient:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权查看改约选项",
                status_code=403
            )

        if not current_schedule:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="原排班不存在，无法改约",
                status_code=404
            )

        if order.status not in [OrderStatus.PENDING, OrderStatus.CONFIRMED]:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="当前状态不可改约",
                status_code=400
            )

        schedule_config = await get_schedule_config(db)
        # 使用北京时间的无时区时间戳，避免服务器时区为 UTC 时误判“已过就诊时间”
        now = get_now_naive()

        # 确保当前排班仍在开始前
        if _build_schedule_start_datetime(current_schedule, schedule_config or {}) <= now:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="已过就诊时间，无法改约",
                status_code=400
            )

        options_res = await db.execute(
            select(Schedule, Clinic, MinorDepartment, HospitalArea)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .join(MinorDepartment, MinorDepartment.minor_dept_id == Clinic.minor_dept_id)
            .join(HospitalArea, HospitalArea.area_id == Clinic.area_id)
            .where(
                Schedule.doctor_id == order.doctor_id,
                Schedule.clinic_id == current_schedule.clinic_id,
                Schedule.slot_type == current_schedule.slot_type,
                Schedule.remaining_slots > 0,
                Schedule.schedule_id != current_schedule.schedule_id,
                Schedule.date >= now.date()
            )
            .order_by(Schedule.date.asc(), Schedule.time_section.asc())
        )

        options_rows = options_res.all()
        options: list[RescheduleOption] = []

        for opt_schedule, opt_clinic, opt_dept, opt_area in options_rows:
            start_dt = _build_schedule_start_datetime(opt_schedule, schedule_config or {})
            if start_dt <= now:
                continue
            options.append(RescheduleOption(
                scheduleId=opt_schedule.schedule_id,
                date=str(opt_schedule.date),
                timeSection=opt_schedule.time_section,
                remainingSlots=opt_schedule.remaining_slots,
                price=float(opt_schedule.price) if opt_schedule.price else 0.0,
                hospitalId=opt_area.area_id if opt_area else None,
                hospitalName=opt_area.name if opt_area else None,
                departmentId=opt_dept.minor_dept_id if opt_dept else None,
                departmentName=opt_dept.name if opt_dept else None,
                clinicId=opt_clinic.clinic_id if opt_clinic else None,
                clinicName=opt_clinic.name if opt_clinic else None,
                slotType=str(opt_schedule.slot_type.value if hasattr(opt_schedule.slot_type, 'value') else opt_schedule.slot_type)
            ))

        return ResponseModel(code=0, message=RescheduleOptionsResponse(
            appointmentId=order.order_id,
            currentScheduleId=current_schedule.schedule_id if current_schedule else None,
            currentDate=str(order.slot_date) if order.slot_date else None,
            currentTimeSection=order.time_section,
            options=options
        ))

    except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
        raise
    except Exception as e:
        logger.error(f"获取改约可选排班失败: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="获取改约可选排班失败",
            status_code=500
        )


@router.put("/appointments/{appointmentId}/reschedule", response_model=ResponseModel[RescheduleResponse])
async def reschedule_appointment(
    appointmentId: int,
    payload: RescheduleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """改约到同医生同诊室的其他排班"""
    try:
        order_res = await db.execute(
            select(RegistrationOrder, Schedule, Patient, Clinic)
            .join(Schedule, Schedule.schedule_id == RegistrationOrder.schedule_id)
            .join(Patient, Patient.patient_id == RegistrationOrder.patient_id)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .where(RegistrationOrder.order_id == appointmentId)
        )
        row = order_res.first()

        if not row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="预约不存在",
                status_code=404
            )

        order, current_schedule, patient, clinic = row

        user_patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        user_patient = user_patient_res.scalar_one_or_none()

        is_initiator = order.initiator_user_id == current_user.user_id
        is_patient = user_patient and order.patient_id == user_patient.patient_id

        if not is_initiator and not is_patient:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权改约该订单",
                status_code=403
            )

        if not current_schedule:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="原排班不存在，无法改约",
                status_code=404
            )

        if order.status not in [OrderStatus.PENDING, OrderStatus.CONFIRMED]:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="当前状态不可改约",
                status_code=400
            )

        if order.payment_status not in [PaymentStatus.PENDING, PaymentStatus.PAID]:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="当前支付状态不可改约",
                status_code=400
            )

        schedule_config = await get_schedule_config(db)
        now = datetime.now()

        if _build_schedule_start_datetime(current_schedule, schedule_config or {}) <= now:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="已过就诊时间，无法改约",
                status_code=400
            )

        target_res = await db.execute(
            select(Schedule, Clinic)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .where(Schedule.schedule_id == payload.scheduleId)
        )
        target_row = target_res.first()

        if not target_row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="目标排班不存在",
                status_code=404
            )

        target_schedule, target_clinic = target_row

        if target_schedule.remaining_slots <= 0:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="目标排班号源不足",
                status_code=400
            )

        if target_schedule.schedule_id == current_schedule.schedule_id:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="请选择不同的排班进行改约",
                status_code=400
            )

        if target_schedule.doctor_id != order.doctor_id:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="仅支持同医生改约",
                status_code=400
            )

        if target_schedule.clinic_id != current_schedule.clinic_id:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="仅支持同诊室同号源改约",
                status_code=400
            )

        target_slot_type = target_schedule.slot_type.value if hasattr(target_schedule.slot_type, "value") else target_schedule.slot_type
        current_slot_type = current_schedule.slot_type.value if hasattr(current_schedule.slot_type, "value") else current_schedule.slot_type

        if target_slot_type != current_slot_type:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="仅支持同号源类型改约",
                status_code=400
            )

        if _build_schedule_start_datetime(target_schedule, schedule_config or {}) <= now:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="目标排班已过开始时间，无法改约",
                status_code=400
            )

        # 价格按患者身份折扣重新计算，保持与预约创建一致
        discounts = await get_patient_identity_discounts(db)
        discount_rate = 1.0
        if patient and patient.patient_type:
            patient_type = patient.patient_type.value if hasattr(patient.patient_type, "value") else patient.patient_type
            discount_rate = discounts.get(patient_type, 1.0)

        base_price = target_schedule.price if target_schedule.price else 0.0
        final_price = calculate_final_price(base_price, discount_rate)
        original_price = float(order.price) if order.price else 0.0
        price_diff = float(final_price) - original_price

        # 已支付订单仅允许同价改约
        if order.payment_status == PaymentStatus.PAID and abs(price_diff) > 0.0001:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="已支付订单仅支持同价改约",
                status_code=400
            )

        # 释放原排班号源，锁定新排班
        current_schedule.remaining_slots += 1
        target_schedule.remaining_slots -= 1

        order.schedule_id = target_schedule.schedule_id
        order.slot_date = target_schedule.date
        order.time_section = target_schedule.time_section
        order.slot_type = target_slot_type
        order.doctor_id = target_schedule.doctor_id
        order.price = final_price
        order.update_time = get_now_naive()

        db.add(order)
        db.add(current_schedule)
        db.add(target_schedule)

        await db.commit()
        await db.refresh(order)

        # 发送微信改约成功通知
        try:
            doctor = await db.get(Doctor, target_schedule.doctor_id) if target_schedule and target_schedule.doctor_id else None
            patient_name = patient.name if patient else ""
            # 计算原预约和新预约的时间字符串
            current_datetime_str = _format_wechat_datetime(current_schedule.date, current_schedule.time_section, schedule_config)
            target_datetime_str = _format_wechat_datetime(target_schedule.date, target_schedule.time_section, schedule_config)
            clinic_name = (target_clinic.address or target_clinic.name) if target_clinic else ""
            template_id = settings.WECHAT_TEMPLATE_RESCHEDULE_SUCCESS
            data_payload = _wechat_payload_reschedule(
                patient_name,
                current_datetime_str,
                target_datetime_str,
                clinic_name,
                reason="时间冲突调整",
            )

            target_user_id = patient.user_id if patient and patient.user_id else current_user.user_id
            await _wechat_prepare_and_send(
                db,
                target_user_id,
                payload.wxCode,
                payload.subscribeAuthResult,
                payload.subscribeScene or "reschedule",
                template_id,
                data_payload,
                scene="reschedule",
                order_id=order.order_id,
            )
        except Exception as exc:
            logger.warning(f"改约微信通知失败: {exc}")

        # 改约成功后：对原排班进行候补级联转换（释放了一个号源）
        # 与取消预约/取消支付的逻辑保持一致：循环转换直到没有候补或没有可用号源
        try:
            converted_count = 0
            max_attempts = 10  # 防止无限循环，最多尝试转换10个

            for attempt in range(max_attempts):
                converted_order_id = await WaitlistService.notify_and_convert_first_in_queue(
                    db,
                    current_schedule.schedule_id
                )

                if not converted_order_id:
                    break

                converted_count += 1
                logger.info(f"候补已转预约(改约触发): order_id={converted_order_id}")

            if converted_count > 0:
                logger.info(f"改约释放号源后自动转化候补成功: 共转化{converted_count}个候补")
            else:
                logger.info("改约释放号源后无候补订单")
        except Exception as e:
            logger.error(f"改约后自动转化候补失败: {str(e)}")

        return ResponseModel(code=0, message=RescheduleResponse(
            id=order.order_id,
            appointmentDate=str(order.slot_date),
            appointmentTime=f"{order.time_section}",
            price=float(final_price) if final_price else 0.0,
            priceDiff=round(price_diff, 2),
            status=order.status.value,
            paymentStatus=order.payment_status.value
        ))

    except (AuthHTTPException, ResourceHTTPException, BusinessHTTPException):
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"改约失败: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="改约失败",
            status_code=500
        )


@router.get("/my-initiated-appointments", response_model=ResponseModel[AppointmentListResponse])
async def get_my_initiated_appointments(
    status: Optional[str] = "all",
    page: int = 1,
    pageSize: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取我创建的预约列表 - 需要登录
    
    参数:
    - status: 预约状态过滤 (all/pending/completed/cancelled)
    - page: 页码
    - pageSize: 每页条数
    
    返回:
    - 当前用户作为发起人创建的所有订单(包括为他人代约的)
    """
    try:
        # 构建查询条件: initiator_user_id = current_user.user_id
        filters = [RegistrationOrder.initiator_user_id == current_user.user_id]
        
        if status and status != "all":
            if status == "pending":
                filters.append(RegistrationOrder.status == OrderStatus.PENDING)
            elif status == "completed":
                filters.append(RegistrationOrder.status == OrderStatus.CONFIRMED)
            elif status == "cancelled":
                filters.append(RegistrationOrder.status == OrderStatus.CANCELLED)
        
        # 查询总数
        count_res = await db.execute(
            select(func.count()).select_from(RegistrationOrder).where(and_(*filters))
        )
        total = count_res.scalar()
        
        # 分页查询
        offset = (page - 1) * pageSize
        result = await db.execute(
            select(RegistrationOrder, Schedule, Doctor, Clinic, MinorDepartment, HospitalArea, Patient)
            .join(Schedule, Schedule.schedule_id == RegistrationOrder.schedule_id)
            .join(Doctor, Doctor.doctor_id == RegistrationOrder.doctor_id)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .join(MinorDepartment, MinorDepartment.minor_dept_id == Clinic.minor_dept_id)
            .join(HospitalArea, HospitalArea.area_id == Clinic.area_id)
            .join(Patient, Patient.patient_id == RegistrationOrder.patient_id)
            .where(and_(*filters))
            .order_by(RegistrationOrder.create_time.desc())
            .offset(offset)
            .limit(pageSize)
        )
        
        rows = result.all()
        
        # 获取排班配置用于判断取消时间(只查询一次)
        schedule_config = await get_schedule_config(db)
        
        # 按医生分组获取挂号配置(减少重复查询)
        doctor_ids = list(set(order.doctor_id for order, *_ in rows))
        doctor_configs = {}
        for doctor_id in doctor_ids:
            config = await get_registration_config(db, scope_type="DOCTOR", scope_id=doctor_id)
            doctor_configs[doctor_id] = config
        
        appointment_list = []
        for order, schedule, doctor, clinic, dept, area, patient in rows:
            # 计算是否可取消
            can_cancel = False
            if order.status in [OrderStatus.PENDING, OrderStatus.CONFIRMED]:
                reg_config = doctor_configs.get(order.doctor_id, {})
                cancel_hours_before = reg_config.get("cancelHoursBefore", 2) if reg_config else 2
                
                now = datetime.now()
                appointment_datetime = datetime.combine(order.slot_date, datetime.min.time())
                
                if order.time_section == "上午":
                    time_str = schedule_config.get("morningStartTime", "08:00") if schedule_config else "08:00"
                elif order.time_section == "下午":
                    time_str = schedule_config.get("afternoonStartTime", "13:00") if schedule_config else "13:00"
                else:
                    time_str = schedule_config.get("eveningStartTime", "18:00") if schedule_config else "18:00"
                
                hour, minute = parse_time_to_hour_minute(time_str)
                cancel_deadline = appointment_datetime.replace(hour=hour, minute=minute) - timedelta(hours=cancel_hours_before)
                can_cancel = now < cancel_deadline
            can_reschedule = False
            if order.status in [OrderStatus.PENDING, OrderStatus.CONFIRMED] and schedule:
                start_dt = _build_schedule_start_datetime(schedule, schedule_config or {})
                can_reschedule = start_dt > datetime.now()
            
            # 排队号码由就诊系统动态管理，预约阶段不提供
            queue_number = None
            
            appointment_list.append(AppointmentListItem(
                id=order.order_id,
                orderNo=order.order_no if order.order_no else _generate_order_no(),
                hospitalId=area.area_id,
                hospitalName=area.name,
                departmentId=dept.minor_dept_id,
                departmentName=dept.name,
                doctorName=doctor.name,
                doctorTitle=doctor.title,
                scheduleId=schedule.schedule_id,
                patientName=patient.name,
                patientId=patient.patient_id,
                queueNumber=queue_number,
                appointmentDate=str(order.slot_date),
                appointmentTime=f"{order.time_section}",
                status=order.status.value,
                paymentStatus=order.payment_status.value,
                price=float(order.price) if order.price else 0.0,
                canCancel=can_cancel,
                canReschedule=can_reschedule,
                sourceType=order.source_type if hasattr(order, 'source_type') and order.source_type else "normal",
                createdAt=order.create_time.strftime("%Y-%m-%d %H:%M:%S") if order.create_time else ""
            ))
        
        return ResponseModel(code=0, message=AppointmentListResponse(
            total=total,
            page=page,
            pageSize=pageSize,
            list=appointment_list
        ))
        
    except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
        raise
    except Exception as e:
        logger.error(f"获取发起人订单列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="获取订单列表失败",
            status_code=500
        )


# ====== 支付接口 ======


@router.post("/appointments/{appointmentId}/pay", response_model=ResponseModel[PaymentResponse])
async def pay_appointment(
    appointmentId: int,
    payload: PaymentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """支付预约订单 - 方案二：模拟支付
    
    支持支付方式: bank(银行卡)、alipay(支付宝)、wechat(微信)
    支付成功后订单状态变更为 CONFIRMED
    """
    try:
        # 1. 查询订单
        order_res = await db.execute(
            select(RegistrationOrder).where(RegistrationOrder.order_id == appointmentId)
        )
        order = order_res.scalar_one_or_none()
        
        if not order:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="订单不存在",
                status_code=404
            )
        
        # 2. 验证权限：发起人或就诊人
        user_patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        user_patient = user_patient_res.scalar_one_or_none()
        
        is_initiator = order.initiator_user_id == current_user.user_id
        is_patient = user_patient and order.patient_id == user_patient.patient_id
        
        if not is_initiator and not is_patient:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权支付该订单",
                status_code=403
            )
        
        # 3. 检查订单状态
        if order.payment_status != PaymentStatus.PENDING:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg=f"订单状态不允许支付（当前: {order.payment_status.value}）",
                status_code=400
            )
        
        if order.status != OrderStatus.PENDING:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg=f"订单状态错误（当前: {order.status.value}）",
                status_code=400
            )
        
        # 4. 验证金额有效性
        if not order.price or order.price <= 0:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="订单金额无效",
                status_code=400
            )
        
        # 5. 模拟支付流程（开发/测试环境直接标记为已支付）
        now = datetime.now()
        order.payment_status = PaymentStatus.PAID
        order.payment_time = now
        order.payment_method = payload.method.value
        order.status = OrderStatus.CONFIRMED  # 支付成功 → 订单确认
        order.update_time = now
        
        db.add(order)
        await db.commit()
        await db.refresh(order)
        
        logger.info(f"支付成功: order_id={appointmentId}, method={payload.method.value}, amount={order.price}")

        # 支付成功后发送“预约成功”微信订阅消息
        try:
            # 加载患者、医生、门诊信息
            patient_obj = await db.get(Patient, order.patient_id) if order.patient_id else None
            schedule = await db.get(Schedule, order.schedule_id) if order.schedule_id else None
            doctor, clinic, dept = await _load_doctor_and_dept(db, schedule) if schedule else (None, None, None)

            patient_name = patient_obj.name if patient_obj else ""
            doctor_name = doctor.name if doctor else ""
            # 预约地点优先使用 address，其次使用 name
            location = (clinic.address or clinic.name) if clinic else ""

            schedule_config = await get_schedule_config(db)
            datetime_str = _format_wechat_datetime(order.slot_date, order.time_section, schedule_config)

            template_id = settings.WECHAT_TEMPLATE_APPOINTMENT_SUCCESS
            data_payload = _wechat_payload_appointment(
                patient_name,
                datetime_str,
                location,
                doctor_name,
                status="预约成功",
            )

            # 目标用户：患者绑定的 user_id 优先，其次为当前用户
            target_user_id = (patient_obj.user_id if patient_obj and patient_obj.user_id else current_user.user_id)

            await _wechat_prepare_and_send(
                db,
                target_user_id,
                wx_code=payload.wxCode,
                subscribe_auth=payload.subscribeAuthResult,
                subscribe_scene=payload.subscribeScene or "appointment_paid",
                template_id=template_id,
                data=data_payload,
                scene="appointment_paid",
                order_id=order.order_id,
            )
        except Exception as exc:
            logger.warning(f"支付成功后预约通知发送失败: {exc}")
        
        return ResponseModel(code=0, message=PaymentResponse(
            success=True,
            orderId=order.order_id,
            orderNo=order.order_no,
            paymentStatus=order.payment_status.value,
            paymentTime=order.payment_time.strftime("%Y-%m-%d %H:%M:%S") if order.payment_time else "",
            method=payload.method.value,
            amount=float(order.price) if order.price else 0.0
        ))
        
    except (AuthHTTPException, ResourceHTTPException, BusinessHTTPException):
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"支付失败: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="支付失败",
            status_code=500
        )


@router.post("/appointments/{appointmentId}/cancel-payment", response_model=ResponseModel[CancelPaymentResponse])
async def cancel_payment(
    appointmentId: int,
    payload: Optional[CancelPaymentRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """取消已支付或待支付的订单（申请退款）
    
    规则：
    - PENDING 状态可直接取消
    - PAID 状态需检查是否超过预约时间
    """
    try:
        # 1. 查询订单
        order_res = await db.execute(
            select(RegistrationOrder, Schedule).
            outerjoin(Schedule, Schedule.schedule_id == RegistrationOrder.schedule_id).
            where(RegistrationOrder.order_id == appointmentId)
        )
        row = order_res.first()
        
        if not row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="订单不存在",
                status_code=404
            )
        
        order, schedule = row
        
        # 2. 验证权限
        user_patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        user_patient = user_patient_res.scalar_one_or_none()
        
        is_initiator = order.initiator_user_id == current_user.user_id
        is_patient = user_patient and order.patient_id == user_patient.patient_id
        
        if not is_initiator and not is_patient:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权取消该订单",
                status_code=403
            )
        
        # 3. 检查订单状态
        if order.status == OrderStatus.CANCELLED:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="订单已取消",
                status_code=400
            )
        
        if order.status == OrderStatus.COMPLETED or order.status == OrderStatus.NO_SHOW:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="已完成或未到场的订单不可取消",
                status_code=400
            )
        
        # 4. 处理待支付的超时自动取消
        if order.payment_status == PaymentStatus.PENDING and order.status == OrderStatus.PENDING:
            # 读取挂号配置中的支付超时（分钟）
            reg_config = await get_registration_config(
                db,
                scope_type="DOCTOR",
                scope_id=order.doctor_id
            )
            timeout_minutes = int(reg_config.get("paymentTimeoutMinutes", 30))
            now = datetime.now()
            create_time = order.create_time or now
            if now >= (create_time + timedelta(minutes=timeout_minutes)):
                # 超时自动取消：标记为已超时，支付失败
                order.status = OrderStatus.TIMEOUT
                order.payment_status = PaymentStatus.FAILED
                order.cancel_time = now
                order.update_time = now
                # 释放号源
                if schedule:
                    schedule.remaining_slots = (schedule.remaining_slots or 0) + 1
                    db.add(schedule)
                db.add(order)
                await db.commit()
                await db.refresh(order)
                logger.info(f"订单支付超时自动取消: order_id={appointmentId}, timeout_minutes={timeout_minutes}")
                return ResponseModel(code=0, message=CancelPaymentResponse(
                    success=True,
                    orderId=order.order_id,
                    status=order.status.value,
                    cancelTime=order.cancel_time.strftime("%Y-%m-%d %H:%M:%S") if order.cancel_time else "",
                    reason="支付超时"
                ))

        # 5. 如果已支付，检查是否超过取消时间
        if order.payment_status == PaymentStatus.PAID:
            # 获取挂号配置
            reg_config = await get_registration_config(
                db,
                scope_type="DOCTOR",
                scope_id=order.doctor_id
            )
            cancel_hours_before = reg_config.get("cancelHoursBefore", 2)
            
            # 获取排班配置
            schedule_config = await get_schedule_config(db)
            
            now = datetime.now()
            appointment_datetime = datetime.combine(order.slot_date, datetime.min.time())
            
            # 根据时间段获取开始时间
            if order.time_section == "上午":
                time_str = schedule_config.get("morningStart", "08:00")
            elif order.time_section == "下午":
                time_str = schedule_config.get("afternoonStart", "13:30")
            else:
                time_str = schedule_config.get("eveningStart", "18:00")
            
            hour, minute = parse_time_to_hour_minute(time_str)
            cancel_deadline = appointment_datetime.replace(hour=hour, minute=minute) - timedelta(hours=cancel_hours_before)
            
            if now > cancel_deadline:
                raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="已超过取消时间，需到医院办理退号",
                    status_code=400
                )
        
        # 6. 执行取消逻辑
        now = datetime.now()
        order.status = OrderStatus.CANCELLED
        
        # 如果已支付，标记为退款
        if order.payment_status == PaymentStatus.PAID:
            order.payment_status = PaymentStatus.REFUNDED
            order.refund_time = now
            order.refund_amount = order.price
        else:
            order.payment_status = PaymentStatus.CANCELLED
        
        order.cancel_time = now
        order.update_time = now
        
        db.add(order)
        
        # 7. 如果关联了排班，释放号源
        if order.schedule_id and schedule:
            schedule.remaining_slots = (schedule.remaining_slots or 0) + 1
            db.add(schedule)
        
        await db.commit()
        await db.refresh(order)
        
        # 8. 级联转化候补到预约（和 cancel_appointment 保持一致）
        if order.schedule_id and schedule:
            try:
                converted_count = 0
                max_attempts = 10  # 防止无限循环
                
                for attempt in range(max_attempts):
                    converted_order_id = await WaitlistService.notify_and_convert_first_in_queue(
                        db, 
                        order.schedule_id
                    )
                    
                    if not converted_order_id:
                        break
                    
                    converted_count += 1
                    logger.info(f"候补已转预约: order_id={converted_order_id}")
                
                if converted_count > 0:
                    logger.info(f"订单支付取消后自动转化候补成功: 共转化{converted_count}个候补")
            except Exception as e:
                logger.warning(f"自动转化候补失败: {str(e)}")
        
        logger.info(f"订单取消成功: order_id={appointmentId}, status={order.payment_status.value}")
        
        return ResponseModel(code=0, message=CancelPaymentResponse(
            success=True,
            orderId=order.order_id,
            status=order.status.value,
            cancelTime=order.cancel_time.strftime("%Y-%m-%d %H:%M:%S") if order.cancel_time else "",
            reason=(payload.reason if payload and payload.reason else None)
        ))
        
    except (AuthHTTPException, ResourceHTTPException, BusinessHTTPException):
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"取消订单失败: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="取消订单失败",
            status_code=500
        )

# ====== 微信绑定与订阅授权查询 ======

@router.post("/wechat/bind-by-code", response_model=ResponseModel[dict])
async def wechat_bind_by_code(
    data: WechatLoginRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """使用 wx.login() 的 code 绑定当前用户的微信 openid。"""
    try:
        wechat = WechatService()
        wx_res = await wechat.code_to_openid(data.code)
        if not wx_res or not wx_res.get("openid"):
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="code 无效或换取 openid 失败",
                status_code=400
            )
        await wechat.save_user_openid(
            db,
            current_user.user_id,
            wx_res.get("openid"),
            wx_res.get("session_key"),
            wx_res.get("unionid"),
        )
        masked = WechatService.mask_openid(wx_res.get("openid"))
        return ResponseModel(code=0, message={"bound": True, "openid": masked})
    except (AuthHTTPException, ResourceHTTPException, BusinessHTTPException):
        raise
    except Exception as e:
        logger.error(f"微信绑定失败: {e}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="微信绑定失败",
            status_code=500
        )

@router.get("/wechat/authorized", response_model=ResponseModel[dict])
async def wechat_is_authorized(
    templateId: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """查询当前用户是否已授权指定订阅消息模板。"""
    try:
        wechat = WechatService()
        authorized = await wechat.check_user_authorized(db, current_user.user_id, templateId)
        return ResponseModel(code=0, message={"authorized": bool(authorized), "templateId": templateId})
    except (AuthHTTPException, ResourceHTTPException, BusinessHTTPException):
        raise
    except Exception as e:
        logger.error(f"查询订阅授权失败: {e}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="查询订阅授权失败",
            status_code=500
        )


# ====== 候补挂号接口 ======


@router.post("/waitlist", response_model=ResponseModel[WaitlistCreateResponse])
async def join_waitlist(
    data: WaitlistCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """加入候补队列 - 号源满时使用"""
    try:
        schedule_res = await db.execute(select(Schedule).where(Schedule.schedule_id == data.scheduleId))
        schedule = schedule_res.scalar_one_or_none()
        if not schedule:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="排班不存在",
                status_code=404
            )

        patient_res = await db.execute(select(Patient).where(Patient.patient_id == data.patientId))
        patient = patient_res.scalar_one_or_none()
        if not patient:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="患者不存在",
                status_code=404
            )

        user_patient_res = await db.execute(select(Patient).where(Patient.user_id == current_user.user_id))
        user_patient = user_patient_res.scalar_one_or_none()
        if not user_patient:
            raise ResourceHTTPException(
                code=settings.RESOURCE_NOT_FOUND_CODE,
                msg="当前用户未绑定患者信息"
            )

        is_self = patient.patient_id == user_patient.patient_id
        is_related = False
        if not is_self:
            relation_res = await db.execute(
                select(PatientRelation).where(
                    and_(
                        PatientRelation.user_patient_id == user_patient.patient_id,
                        PatientRelation.related_patient_id == data.patientId
                    )
                )
            )
            if relation_res.scalar_one_or_none():
                is_related = True

        if not is_self and not is_related:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权为该患者候补",
                status_code=403
            )

        # 仅号源已满时允许候补
        if schedule.remaining_slots is not None and schedule.remaining_slots > 0:
            raise BusinessHTTPException(
                code=1005,
                msg="该时段有号源，无需候补",
                status_code=400
            )

        # 防重复候补
        dup_res = await db.execute(
            select(RegistrationOrder).where(
                and_(
                    RegistrationOrder.patient_id == data.patientId,
                    RegistrationOrder.schedule_id == data.scheduleId,
                    RegistrationOrder.status == OrderStatus.WAITLIST
                )
            )
        )
        if dup_res.scalar_one_or_none():
            raise BusinessHTTPException(
                code=1006,
                msg="已在候补队列中",
                status_code=400
            )

        # 计算排位
        max_pos_res = await db.execute(
            select(func.max(RegistrationOrder.waitlist_position)).where(
                and_(
                    RegistrationOrder.schedule_id == data.scheduleId,
                    RegistrationOrder.status == OrderStatus.WAITLIST
                )
            )
        )
        max_pos = max_pos_res.scalar() or 0
        position = max_pos + 1

        # 从数据库获取身份折扣配置
        discounts = await get_patient_identity_discounts(db)
        
        # 根据患者身份应用价格折扣
        base_price = schedule.price if schedule.price else 0.0
        discount_rate = 1.0  # 默认无折扣
        
        if patient.patient_type:
            patient_type_value = patient.patient_type
            if isinstance(patient.patient_type, PatientType):
                patient_type_value = patient.patient_type.value
            
            # 从数据库配置中获取折扣率
            discount_rate = discounts.get(patient_type_value, 1.0)
        
        # 计算最终价格，精确到小数点后2位
        final_price = calculate_final_price(base_price, discount_rate)

        now = datetime.now()
        order_no = _generate_order_no()

        order = RegistrationOrder(
            order_no=order_no,
            patient_id=data.patientId,
            user_id=patient.user_id,
            initiator_user_id=current_user.user_id,
            doctor_id=schedule.doctor_id,
            schedule_id=data.scheduleId,
            slot_date=schedule.date,
            time_section=schedule.time_section,
            slot_type=str(schedule.slot_type.value if hasattr(schedule.slot_type, "value") else schedule.slot_type),
            price=final_price,  # 使用折扣后的价格
            status=OrderStatus.WAITLIST,
            payment_status=PaymentStatus.PENDING,
            is_waitlist=True,
            waitlist_position=position,
            source_type="waitlist",  # 候补订单直接标记为waitlist
            create_time=now,
            update_time=now,
        )
        db.add(order)
        await db.commit()
        await db.refresh(order)

        # 添加到 Redis 候补队列
        try:
            queue_position = await WaitlistService.add_to_queue(
                data.scheduleId,
                data.patientId,
                order.order_id
            )
            estimated_time = f"{queue_position * 10}分钟" if queue_position else None
        except Exception as e:
            logger.warning(f"Redis 队列添加失败: {str(e)}")
            estimated_time = f"{position * 10}分钟" if position else None

        # 微信订阅授权处理（仅保存授权，不发送消息，留给候补转预约成功时使用）
        try:
            target_user_id = patient.user_id if patient and patient.user_id else current_user.user_id
            wechat = WechatService()
            
            # 1. 处理 code 换 openid
            if data.wxCode:
                wx_res = await wechat.code_to_openid(data.wxCode)
                if wx_res and wx_res.get("openid"):
                    await wechat.save_user_openid(
                        db,
                        target_user_id,
                        wx_res.get("openid"),
                        wx_res.get("session_key"),
                        wx_res.get("unionid"),
                    )

            # 2. 保存订阅授权（关键：这里保存的授权将在候补转预约成功时被消耗）
            if data.subscribeAuthResult:
                await wechat.save_subscribe_auth(
                    db, 
                    target_user_id, 
                    data.subscribeAuthResult, 
                    data.subscribeScene or "waitlist"
                )
                logger.info(f"候补加入成功: 已保存订阅授权，不发送即时通知(留给转预约使用). user_id={target_user_id}")
                
        except Exception as exc:
            logger.warning(f"微信订阅授权保存失败: {exc}")

        return ResponseModel(code=0, message=WaitlistCreateResponse(
            id=order.order_id,
            queueNumber=position,
            estimatedTime=estimated_time,
            createdAt=order.create_time.strftime("%Y-%m-%d %H:%M:%S") if order.create_time else ""
        ))

    except (AuthHTTPException, ResourceHTTPException, BusinessHTTPException):
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"加入候补失败: {e}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="加入候补失败",
            status_code=500
        )


@router.get("/waitlist", response_model=ResponseModel[WaitlistListResponse])
async def get_waitlist(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取当前用户的候补记录"""
    try:
        filters = [
            RegistrationOrder.status == OrderStatus.WAITLIST,
            RegistrationOrder.is_waitlist == True,  # noqa: E712
            or_(
                RegistrationOrder.initiator_user_id == current_user.user_id,
                RegistrationOrder.user_id == current_user.user_id
            )
        ]

        result = await db.execute(
            select(RegistrationOrder, Schedule, Doctor, Clinic, MinorDepartment, HospitalArea, Patient)
            .join(Schedule, Schedule.schedule_id == RegistrationOrder.schedule_id)
            .join(Doctor, Doctor.doctor_id == RegistrationOrder.doctor_id)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .join(MinorDepartment, MinorDepartment.minor_dept_id == Clinic.minor_dept_id)
            .join(HospitalArea, HospitalArea.area_id == Clinic.area_id)
            .join(Patient, Patient.patient_id == RegistrationOrder.patient_id)
            .where(and_(*filters))
            .order_by(RegistrationOrder.waitlist_position.asc(), RegistrationOrder.create_time.asc())
        )

        rows = result.all()
        items = []
        for order, schedule, doctor, clinic, dept, area, patient in rows:
            # 判断是否有号源可转预约
            can_convert = schedule.remaining_slots is not None and schedule.remaining_slots > 0
            
            items.append(WaitlistItem(
                id=order.order_id,
                scheduleId=schedule.schedule_id,
                hospitalName=area.name if area else None,
                departmentName=dept.name if dept else None,
                doctorName=doctor.name if doctor else None,
                doctorTitle=doctor.title if doctor else None,
                appointmentDate=str(order.slot_date) if order.slot_date else None,
                appointmentTime=order.time_section,
                price=float(order.price) if order.price else (float(schedule.price) if schedule and schedule.price else None),
                status=order.status.value,
                queueNumber=order.waitlist_position,
                patientName=patient.name if patient else None,
                createdAt=order.create_time.strftime("%Y-%m-%d %H:%M:%S") if order.create_time else "",
                canConvert=can_convert
            ))

        return ResponseModel(code=0, message=WaitlistListResponse(list=items))

    except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
        raise
    except Exception as e:
        logger.error(f"获取候补列表失败: {e}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="获取候补列表失败",
            status_code=500
        )


@router.delete("/waitlist/{waitlistId}", response_model=ResponseModel[dict])
async def cancel_waitlist(
    waitlistId: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """取消候补 - 发起人或就诊人均可"""
    try:
        res = await db.execute(
            select(RegistrationOrder, Schedule)
            .join(Schedule, Schedule.schedule_id == RegistrationOrder.schedule_id)
            .where(RegistrationOrder.order_id == waitlistId)
        )
        row = res.first()
        if not row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="候补记录不存在",
                status_code=404
            )

        order, schedule = row

        if order.status != OrderStatus.WAITLIST:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="当前状态不可取消",
                status_code=400
            )

        user_patient_res = await db.execute(select(Patient).where(Patient.user_id == current_user.user_id))
        user_patient = user_patient_res.scalar_one_or_none()
        is_initiator = order.initiator_user_id == current_user.user_id
        is_patient = user_patient and order.patient_id == user_patient.patient_id
        if not is_initiator and not is_patient:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权取消该候补",
                status_code=403
            )

        now = datetime.now()
        order.status = OrderStatus.CANCELLED
        order.payment_status = PaymentStatus.CANCELLED
        order.is_waitlist = False
        order.waitlist_position = None
        order.update_time = now

        db.add(order)
        await db.commit()
        
        # 从 Redis 队列中移除
        try:
            await WaitlistService.remove_from_queue(order.schedule_id, order.patient_id)
        except Exception as e:
            logger.warning(f"从 Redis 队列移除失败: {str(e)}")

        return ResponseModel(code=0, message={"success": True})

    except (AuthHTTPException, ResourceHTTPException, BusinessHTTPException):
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"取消候补失败: {e}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="取消候补失败",
            status_code=500
        )


@router.post("/waitlist/{waitlistId}/convert", response_model=ResponseModel[WaitlistConvertResponse])
async def convert_waitlist(
    waitlistId: int,
    payload: WaitlistConvertRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """候补转预约 - 需要号源"""
    try:
        res = await db.execute(
            select(RegistrationOrder, Schedule, Doctor, Patient)
            .join(Schedule, Schedule.schedule_id == RegistrationOrder.schedule_id)
            .join(Doctor, Doctor.doctor_id == RegistrationOrder.doctor_id)
            .join(Patient, Patient.patient_id == RegistrationOrder.patient_id)
            .where(RegistrationOrder.order_id == waitlistId)
        )
        row = res.first()

        if not row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="候补记录不存在",
                status_code=404
            )

        order, schedule, doctor, patient = row

        user_patient_res = await db.execute(select(Patient).where(Patient.user_id == current_user.user_id))
        user_patient = user_patient_res.scalar_one_or_none()
        is_initiator = order.initiator_user_id == current_user.user_id
        is_patient = user_patient and order.patient_id == user_patient.patient_id
        if not is_initiator and not is_patient:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权操作该候补记录",
                status_code=403
            )

        if order.status != OrderStatus.WAITLIST:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="当前状态不可转预约",
                status_code=400
            )

        if schedule.remaining_slots is not None and schedule.remaining_slots <= 0:
            raise BusinessHTTPException(
                code=1008,
                msg="候补转预约失败，号源不足",
                status_code=400
            )

        # 从数据库获取身份折扣配置
        discounts = await get_patient_identity_discounts(db)
        
        # 计算最终价格（应用身份折扣）
        base_price = order.price if order.price else (schedule.price if schedule else 0.0)
        discount_rate = 1.0  # 默认无折扣
        
        if patient.patient_type:
            patient_type_value = patient.patient_type
            if isinstance(patient.patient_type, PatientType):
                patient_type_value = patient.patient_type.value
            
            # 从数据库配置中获取折扣率
            discount_rate = discounts.get(patient_type_value, 1.0)
        
        # 计算最终价格，精确到小数点后2位
        final_price = calculate_final_price(base_price, discount_rate)

        now = datetime.now()
        order.status = OrderStatus.PENDING
        order.payment_status = PaymentStatus.PENDING
        order.is_waitlist = False
        order.waitlist_position = None
        order.order_no = order.order_no or _generate_order_no()
        order.price = final_price  # 设置折扣后的价格（精确到2位小数）
        # source_type保持为waitlist，不需要修改
        order.update_time = now

        if schedule.remaining_slots is not None:
            schedule.remaining_slots -= 1

        db.add(order)
        db.add(schedule)
        await db.commit()
        await db.refresh(order)

        expires_at = (now + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

        return ResponseModel(code=0, message=WaitlistConvertResponse(
            id=order.order_id,
            appointmentDate=str(order.slot_date) if order.slot_date else None,
            appointmentTime=order.time_section,
            queueNumber=None,
            doctorName=doctor.name if doctor else None,
            price=float(final_price) if final_price else 0.0,
            status=order.status.value,
            paymentStatus=order.payment_status.value,
            sourceType=order.source_type if order.source_type else "waitlist",
            createdAt=order.create_time.strftime("%Y-%m-%d %H:%M:%S") if order.create_time else "",
            expiresAt=expires_at
        ))

    except (AuthHTTPException, ResourceHTTPException, BusinessHTTPException):
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"候补转预约失败: {e}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="候补转预约失败",
            status_code=500
        )


# ====== 健康档案相关接口 ======


@router.get("/health-record", response_model=ResponseModel[HealthRecordResponse])
async def get_my_health_record(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取我的健康档案（完整数据）- 需要登录
    
    返回内容:
    - 基本信息（姓名、性别、年龄、身高、电话、证件号、地址）
    - 病史信息（既往病史、过敏史、家族病史）
    - 就诊记录列表
    """
    try:
        # 1. 查询患者信息
        patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        patient = patient_res.scalar_one_or_none()
        
        if not patient:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="患者信息不存在",
                status_code=404
            )
        
        # 2. 计算年龄
        age = None
        if patient.birth_date:
            today = date_type.today()
            age = today.year - patient.birth_date.year
            if (today.month, today.day) < (patient.birth_date.month, patient.birth_date.day):
                age -= 1
        
        # 3. 脱敏处理
        phone_masked = None
        # 优先从 current_user 获取手机号(患者绑定了账号)
        if getattr(current_user, "phonenumber", None):
            phone = str(current_user.phonenumber)
            if len(phone) >= 11:
                phone_masked = phone[:3] + "****" + phone[-4:]
            elif len(phone) >= 7:
                phone_masked = phone[:3] + "****" + phone[-4:]
            else:
                phone_masked = "*" * len(phone)
        # 如果患者没有绑定账号,phone_masked 保持为 None
        
        # 身份证脱敏（前6后4）
        idcard_masked = None
        id_card_val = getattr(patient, "id_card", None)
        if id_card_val and len(id_card_val) >= 10:
            idcard_masked = id_card_val[:6] + "********" + id_card_val[-4:]
        elif id_card_val:
            idcard_masked = id_card_val
        
        # 4. 构建基本信息
        basic_info = BasicInfo(
            name=patient.name,
            gender=patient.gender.value if hasattr(patient.gender, 'value') else str(patient.gender),
            age=age,
            height=None,  # TODO: 需要在 Patient 模型中添加身高字段
            phone=phone_masked or "",
            identifier=patient.identifier,
            idCard=idcard_masked,
            address=None  # TODO: 需要在 Patient 模型中添加地址字段
        )
        
        # 5. 病史信息（目前使用空列表，待后续扩展）
        # TODO: 需要创建 MedicalHistory 表来存储既往病史、过敏史、家族病史
        medical_history = MedicalHistory(
            pastHistory=[],
            allergyHistory=[],
            familyHistory=[]
        )
        
        # 6. 查询就诊记录
        visit_res = await db.execute(
            select(VisitHistory, Doctor, MinorDepartment, RegistrationOrder)
            .outerjoin(Doctor, Doctor.doctor_id == VisitHistory.doctor_id)
            .outerjoin(MinorDepartment, MinorDepartment.minor_dept_id == Doctor.dept_id)
            .outerjoin(RegistrationOrder, RegistrationOrder.order_id == VisitHistory.order_id)
            .where(VisitHistory.patient_id == patient.patient_id)
            .order_by(VisitHistory.visit_date.desc())
        )
        
        visit_rows = visit_res.all()
        
        consultation_records = []
        for visit, doctor, dept, order in visit_rows:
            # 判断状态
            status = "completed"
            if visit.followup_required:
                status = "ongoing"
            
            consultation_records.append(ConsultationRecord(
                id=str(visit.visit_id),
                outpatientNo=order.order_no if order else None,
                visitDate=visit.visit_date.strftime("%Y-%m-%d %H:%M") if isinstance(visit.visit_date, datetime) else str(visit.visit_date),
                department=dept.name if dept else "未知科室",
                doctorName=doctor.name if doctor else "未知医生",
                chiefComplaint=None,  # VisitHistory 表中没有主诉字段
                presentIllness=None,  # VisitHistory 表中没有现病史字段
                auxiliaryExam=None,  # VisitHistory 表中没有辅助检查字段
                diagnosis=visit.diagnosis,
                prescription=visit.prescription,
                status=status
            ))
        
        # 7. 构建响应
        response = HealthRecordResponse(
            patientId=str(patient.patient_id),
            basicInfo=basic_info,
            medicalHistory=medical_history,
            consultationRecords=consultation_records
        )
        
        return ResponseModel(code=0, message=response)
        
    except ResourceHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取健康档案时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="获取健康档案失败",
            status_code=500
        )


@router.get("/visit-record/{visitId}", response_model=ResponseModel[VisitRecordDetailResponse])
async def get_visit_record_detail(
    visitId: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取就诊记录详情 - 需要登录
    
    参数:
    - visitId: 就诊记录ID
    
    返回:
    - 基本信息（患者、医生、科室等）
    - 记录数据（主诉、现病史、辅助检查、诊断、处方）
    """
    try:
        # 1. 查询就诊记录及关联信息
        visit_res = await db.execute(
            select(VisitHistory, Patient, Doctor, MinorDepartment, RegistrationOrder)
            .join(Patient, Patient.patient_id == VisitHistory.patient_id)
            .outerjoin(Doctor, Doctor.doctor_id == VisitHistory.doctor_id)
            .outerjoin(MinorDepartment, MinorDepartment.minor_dept_id == Doctor.dept_id)
            .outerjoin(RegistrationOrder, RegistrationOrder.order_id == VisitHistory.order_id)
            .where(VisitHistory.visit_id == visitId)
        )
        
        row = visit_res.first()
        
        if not row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="就诊记录不存在",
                status_code=404
            )
        
        visit, patient, doctor, dept, order = row
        
        # 2. 验证权限（只能查看自己的就诊记录）
        if patient.user_id != current_user.user_id:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权查看该就诊记录",
                status_code=403
            )
        
        # 3. 计算年龄
        age = None
        if patient.birth_date:
            today = date_type.today()
            age = today.year - patient.birth_date.year
            if (today.month, today.day) < (patient.birth_date.month, patient.birth_date.day):
                age -= 1
        
        # 4. 构建基本信息
        basic_info = VisitRecordDetail(
            patientName=patient.name,
            gender=patient.gender.value if hasattr(patient.gender, 'value') else str(patient.gender),
            age=age,
            outpatientNo=order.order_no if order else None,
            visitDate=visit.visit_date.strftime("%Y-%m-%d %H:%M") if isinstance(visit.visit_date, datetime) else str(visit.visit_date),
            department=dept.name if dept else "未知科室",
            doctorName=doctor.name if doctor else "未知医生"
        )
        
        # 5. 构建记录数据
        record_data = RecordData(
            chiefComplaint=None,  # VisitHistory 表中没有主诉字段
            presentIllness=None,  # VisitHistory 表中没有现病史字段
            auxiliaryExam=None,  # VisitHistory 表中没有辅助检查字段
            diagnosis=visit.diagnosis,
            prescription=visit.prescription
        )
        
        # 6. 构建响应
        response = VisitRecordDetailResponse(
            basicInfo=basic_info,
            recordData=record_data
        )
        return ResponseModel(code=0, message=response)
        
    except AuthHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取就诊记录详情时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="获取就诊记录详情失败",
            status_code=500
        )


# ====== 就诊人管理接口 ======


@router.post("/identity/verify", response_model=ResponseModel)
async def verify_identity(
    data: IdentityVerifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """校内身份校验并更新患者信息 - 需要登录
    
    入参:
    - identifier: 学号/工号
    - password: 校园系统密码
    
    成功:
    - 更新当前登录用户对应的 `Patient.identifier`
    - 根据校方返回的 `roleName` 映射并更新 `patient_type`
    - 将 `is_verified` 置为 True
    
    返回统一格式 ResponseModel
    """
    try:
        # 1. 先查当前用户对应的患者记录
        patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        patient = patient_res.scalar_one_or_none()
        if not patient:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="患者信息不存在",
                status_code=404
            )

        # 2. 先调用校园登录接口校验密码，成功后再获取身份信息
        login_result = login_to_iclass(data.identifier, data.password)
        if not login_result:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="校园登录失败，请检查账号或密码",
                status_code=400
            )

        verify_result = getInfoById(data.identifier)
        # 期望格式: {'status': '0', 'userId': '...', 'userName': '...', 'roleCode': '...', 'roleName': '学生'}
        status_val = str(verify_result.get("status", "-1"))
        role_name = verify_result.get("roleName", "")
        user_id_from_school = verify_result.get("userId", "")
        user_name_from_school = verify_result.get("userName", "")

        if status_val != "0" or not role_name:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="校园身份验证失败，请检查学号是否正确",
                status_code=400
            )

        # 3. 映射 roleName -> PatientType
        # 学校返回中文: 学生/教师/职工 等
        mapped_type = PatientType.EXTERNAL
        if role_name == PatientType.STUDENT.value:
            mapped_type = PatientType.STUDENT
        elif role_name == PatientType.TEACHER.value:
            mapped_type = PatientType.TEACHER
        elif role_name == PatientType.STAFF.value:
            mapped_type = PatientType.STAFF

        # 3.1 标识唯一性检查：identifier 是否已被其他患者/用户占用
        # 检查 Patient 表
        exist_patient_res = await db.execute(
            select(Patient).where(
                and_(
                    Patient.identifier == data.identifier,
                    Patient.patient_id != patient.patient_id
                )
            )
        )
        exist_patient = exist_patient_res.scalar_one_or_none()
        if exist_patient:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="该学号已被其他患者使用",
                status_code=400
            )

        # 检查 User 表
        exist_user_res = await db.execute(
            select(User).where(
                and_(
                    User.identifier == data.identifier,
                    User.user_id != current_user.user_id
                )
            )
        )
        exist_user = exist_user_res.scalar_one_or_none()
        if exist_user:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="该学号已被其他账号使用",
                status_code=400
            )

        # 4. 更新患者信息
        patient.identifier = data.identifier
        # 若姓名为空或不同，以学校返回为准（仅在提供且非空时覆盖）
        if user_name_from_school:
            patient.name = user_name_from_school
        patient.patient_type = mapped_type
        patient.is_verified = True

        db.add(patient)
        # 同步更新用户表的 user_type 与 identifier
        user_res = await db.execute(select(User).where(User.user_id == current_user.user_id))
        user_obj = user_res.scalar_one_or_none()
        if user_obj:
            user_obj.identifier = data.identifier
            # role 映射到 UserType
            mapped_user_type = UserType.EXTERNAL
            if role_name == PatientType.STUDENT.value:
                mapped_user_type = UserType.STUDENT
            elif role_name == PatientType.TEACHER.value:
                mapped_user_type = UserType.TEACHER
            elif role_name == PatientType.STAFF.value:
                mapped_user_type = UserType.EXTERNAL
            user_obj.user_type = mapped_user_type
            user_obj.is_verified = True
            db.add(user_obj)
        await db.commit()
        await db.refresh(patient)

        return ResponseModel(code=0, message={
            "status": status_val,
            "patient_id": patient.patient_id,
            "identifier": patient.identifier,
            "patient_type": patient.patient_type.value if hasattr(patient.patient_type, 'value') else str(patient.patient_type),
            "userId": user_id_from_school,
            "userName": user_name_from_school,
            "roleCode": verify_result.get("roleCode", ""),
            "roleName": role_name
        })

    except (AuthHTTPException, ResourceHTTPException, BusinessHTTPException):
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"身份校验接口异常: {e}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="身份校验失败",
            status_code=500
        )


@router.get("/patients", response_model=ResponseModel[PatientRelationListResponse])
async def get_my_patients(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取我的就诊人列表 - 需要登录
    
    返回当前用户添加的所有就诊人信息,包括关系、是否默认等
    """
    try:
        # 1. 获取当前用户的 patient_id
        patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        user_patient = patient_res.scalar_one_or_none()
        
        if not user_patient:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="患者信息不存在",
                status_code=404
            )
        
        # 2. 查询所有就诊人关系
        result = await db.execute(
            select(PatientRelation, Patient)
            .join(Patient, Patient.patient_id == PatientRelation.related_patient_id)
            .where(PatientRelation.user_patient_id == user_patient.patient_id)
            .order_by(PatientRelation.is_default.desc(), PatientRelation.create_time.desc())
        )
        
        rows = result.all()
        
        # 2.1 读取 Redis 中的默认就诊人(若存在则覆盖数据库中的 is_default)
        default_related_id: Optional[int] = None
        try:
            redis_key = f"user_default_patient:{user_patient.patient_id}"
            cached = await redis.get(redis_key)
            logger.info(f"[get_my_patients] 查询默认就诊人 - user_patient_id={user_patient.patient_id}, redis_key={redis_key}, cached={cached}")
            if cached:
                try:
                    default_related_id = int(cached.decode() if isinstance(cached, (bytes, bytearray)) else cached)
                    logger.info(f"[get_my_patients] Redis 中的默认就诊人 ID: {default_related_id}")
                except Exception as e:
                    # 内容异常则忽略
                    logger.warning(f"[get_my_patients] Redis 值解析失败: {e}")
                    default_related_id = None
            else:
                logger.info(f"[get_my_patients] Redis 中没有默认就诊人记录")
        except Exception as e:
            # Redis 不可用时忽略, 退回使用数据库字段
            logger.warning(f"[get_my_patients] Redis 查询失败: {e}")
            default_related_id = None
        
        # 3. 构建响应
        patient_list = []
        for relation, patient in rows:
            # 计算年龄
            age = None
            if patient.birth_date:
                today = date_type.today()
                age = today.year - patient.birth_date.year
                if (today.month, today.day) < (patient.birth_date.month, patient.birth_date.day):
                    age -= 1
            
            # 脱敏处理(患者可能未绑定用户账号,无手机号)
            phone_masked = ""
            # 如果患者绑定了用户账号,从 User 表获取手机号
            if patient.user_id:
                # 需要查询 User 表获取手机号
                user_res = await db.execute(
                    select(User).where(User.user_id == patient.user_id)
                )
                user = user_res.scalar_one_or_none()
                if user and user.phonenumber:
                    phone = str(user.phonenumber)
                    if len(phone) >= 11:
                        phone_masked = phone[:3] + "****" + phone[-4:]
                    elif len(phone) >= 7:
                        phone_masked = phone[:3] + "****" + phone[-4:]
                    else:
                        phone_masked = "*" * len(phone)
            # 如果没有绑定用户账号,phone_masked 保持为空字符串
            
            # 身份证脱敏（前6后4）
            idcard_masked = None
            id_card_val = getattr(patient, "id_card", None)
            if id_card_val and len(id_card_val) >= 10:
                idcard_masked = id_card_val[:6] + "********" + id_card_val[-4:]
            elif id_card_val:
                idcard_masked = id_card_val
            
            patient_info = PatientInfo(
                patient_id=patient.patient_id,
                real_name=patient.name,
                identifier=patient.identifier,
                id_card=idcard_masked or "",
                phone_number=phone_masked,
                gender=patient.gender.value if hasattr(patient.gender, 'value') else str(patient.gender) if patient.gender else None,
                birth_date=str(patient.birth_date) if patient.birth_date else None,
                age=age
            )
            
            # 计算是否默认: 优先以 Redis 为准, 否则使用数据库中的 is_default
            computed_is_default = relation.is_default
            if default_related_id is not None:
                computed_is_default = (patient.patient_id == default_related_id)
            
            logger.info(f"[get_my_patients] patient_id={patient.patient_id}, relation_type={relation.relation_type}, db_is_default={relation.is_default}, redis_default_id={default_related_id}, computed_is_default={computed_is_default}")

            patient_list.append(PatientRelationResponse(
                relation_id=relation.relation_id,
                patient=patient_info,
                relation_type=relation.relation_type,
                is_default=computed_is_default,
                remark=relation.remark,
                create_time=relation.create_time
            ))
        
        # 4. 根据是否默认进行排序(默认优先), 保持与旧行为一致
        try:
            patient_list.sort(key=lambda x: (not x.is_default, ), reverse=False)
        except Exception:
            pass

        return ResponseModel(code=0, message=PatientRelationListResponse(
            total=len(patient_list),
            patients=patient_list
        ))
        
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取就诊人列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="获取就诊人列表失败",
            status_code=500
        )


@router.post("/patients", response_model=ResponseModel)
async def add_patient(
    data: PatientRelationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """添加就诊人 - 需要登录
    
    新版业务规则(通过身份证号+姓名添加):
    1. 必须提供身份证号(id_card)和姓名(name)
    2. 根据身份证号查询患者:
       - 如果身份证号存在且姓名一致: 直接建立关系
       - 如果身份证号存在但姓名不一致: 返回错误(身份信息冲突)
       - 如果身份证号不存在: 创建新患者记录并建立关系
    3. 不能添加自己为就诊人
    4. 同一患者不能重复添加
    5. 如果设为默认,自动取消其他默认就诊人
    """
    try:
        # 1. 参数验证
        if not data.id_card or not data.name:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="身份证号和姓名为必填项",
                status_code=400
            )
        
        # 2. 获取当前用户的 patient_id
        patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        user_patient = patient_res.scalar_one_or_none()
        
        if not user_patient:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="患者信息不存在",
                status_code=404
            )
        
        # 3. 根据身份证号查询患者
        related_patient_res = await db.execute(
            select(Patient).where(Patient.id_card == data.id_card)
        )
        related_patient = related_patient_res.scalar_one_or_none()
        
        if related_patient:
            # 3.1 身份证号存在,检查姓名是否一致
            if related_patient.name != data.name:
                raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg=f"请核对身份证号和姓名是否正确",
                    status_code=400
                )
            
            # 3.2 身份证号和姓名都匹配,检查是否为本人
            if related_patient.patient_id == user_patient.patient_id:
                raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="不能添加自己为就诊人",
                    status_code=400
                )
            
            # 3.3 检查是否已存在关系
            existing_res = await db.execute(
                select(PatientRelation).where(
                    and_(
                        PatientRelation.user_patient_id == user_patient.patient_id,
                        PatientRelation.related_patient_id == related_patient.patient_id
                    )
                )
            )
            if existing_res.scalar_one_or_none():
                raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="该就诊人已存在",
                    status_code=400
                )
            
            logger.info(f"通过身份证号匹配到已有患者: patient_id={related_patient.patient_id}, name={related_patient.name}")
        
        else:
            # 3.4 身份证号不存在,创建新患者记录
            # 注意: 新患者没有关联 user_id,也不设置 identifier(学号/工号)
            related_patient = Patient(
                name=data.name,
                id_card=data.id_card,
                identifier=None,  # 就诊人无学号/工号
                user_id=None,  # 就诊人不绑定用户账号
                gender=data.gender if hasattr(data, 'gender') and data.gender else None,
                birth_date=data.birth_date if hasattr(data, 'birth_date') and data.birth_date else None
            )
            db.add(related_patient)
            await db.flush()  # 获取新插入的 patient_id
            
            logger.info(f"创建新患者记录作为就诊人: patient_id={related_patient.patient_id}, name={related_patient.name}, id_card={data.id_card}")
        
        # 4. 创建关系，如果需要设为默认则手动清除其他默认
        new_relation = PatientRelation(
            user_patient_id=user_patient.patient_id,
            related_patient_id=related_patient.patient_id,
            relation_type=data.relation_type,
            is_default=data.is_default,  # 直接使用请求中的值
            remark=data.remark
        )
        
        db.add(new_relation)
        
        # 如果设为默认，需要先取消其他关系的默认标记
        if data.is_default:
            await db.execute(
                update(PatientRelation)
                .where(
                    and_(
                        PatientRelation.user_patient_id == user_patient.patient_id,
                        PatientRelation.related_patient_id != related_patient.patient_id
                    )
                )
                .values(is_default=False)
            )
        
        await db.commit()
        await db.refresh(new_relation)

        # 5. 同步更新 Redis 缓存
        if data.is_default:
            try:
                await redis.set(f"user_default_patient:{user_patient.patient_id}", str(related_patient.patient_id))
                logger.info(f"[add_patient] Redis 缓存已更新 - default_patient_id={related_patient.patient_id}")
            except Exception as redis_err:
                # Redis 写入失败不影响主流程
                logger.warning(f"[add_patient] Redis 更新失败: {redis_err}")
        
        logger.info(f"添加就诊人成功: relation_id={new_relation.relation_id}, user_patient_id={user_patient.patient_id}, related_patient_id={related_patient.patient_id}, is_default={data.is_default}")
        
        return ResponseModel(code=0, message={
            "relation_id": new_relation.relation_id,
            "patient_id": related_patient.patient_id,
            "message": "添加就诊人成功"
        })
        
    except AuthHTTPException:
        await db.rollback()
        raise
    except BusinessHTTPException:
        await db.rollback()
        raise
    except ResourceHTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"添加就诊人时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="添加就诊人失败",
            status_code=500
        )


@router.put("/patients/{patient_id}", response_model=ResponseModel)
async def update_patient_relation(
    patient_id: int,
    data: PatientRelationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """更新就诊人信息 - 需要登录
    
    参数:
    - patient_id: 被添加的患者ID(related_patient_id)
    - data: 更新的关系类型和备注
    """
    try:
        # 1. 获取当前用户的 patient_id
        patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        user_patient = patient_res.scalar_one_or_none()
        
        if not user_patient:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="患者信息不存在",
                status_code=404
            )
        
        # 2. 禁止修改“本人”关系
        if patient_id == user_patient.patient_id:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="不能修改本人关系",
                status_code=400
            )

        # 3. 查询关系记录
        relation_res = await db.execute(
            select(PatientRelation).where(
                and_(
                    PatientRelation.user_patient_id == user_patient.patient_id,
                    PatientRelation.related_patient_id == patient_id
                )
            )
        )
        relation = relation_res.scalar_one_or_none()
        
        if not relation:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="就诊人关系不存在",
                status_code=404
            )
        
        # 4. 更新字段
        if data.relation_type is not None:
            relation.relation_type = data.relation_type
        if data.remark is not None:
            relation.remark = data.remark
        
        db.add(relation)
        await db.commit()
        
        logger.info(f"更新就诊人成功: relation_id={relation.relation_id}")
        
        return ResponseModel(code=0, message={"message": "更新成功"})
        
    except ResourceHTTPException:
        await db.rollback()
        raise
    except BusinessHTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"更新就诊人时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="更新就诊人失败",
            status_code=500
        )


@router.delete("/patients/{patient_id}", response_model=ResponseModel)
async def delete_patient_relation(
    patient_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """删除就诊人 - 需要登录
    
    参数:
    - patient_id: 被添加的患者ID(related_patient_id)
    
    业务规则:
    - 不能删除默认就诊人(需先取消默认)
    """
    try:
        # 1. 获取当前用户的 patient_id
        patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        user_patient = patient_res.scalar_one_or_none()
        
        if not user_patient:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="患者信息不存在",
                status_code=404
            )
        
        # 2. 查询关系记录
        relation_res = await db.execute(
            select(PatientRelation).where(
                and_(
                    PatientRelation.user_patient_id == user_patient.patient_id,
                    PatientRelation.related_patient_id == patient_id
                )
            )
        )
        relation = relation_res.scalar_one_or_none()
        
        if not relation:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="就诊人关系不存在",
                status_code=404
            )
        
        # 3. 禁止删除“本人”关系
        if patient_id == user_patient.patient_id:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="不能删除本人就诊人",
                status_code=400
            )

        # 4. 不能删除默认就诊人(检查缓存)
        try:
            redis_key = f"user_default_patient:{user_patient.patient_id}"
            cached = await redis.get(redis_key)
            if cached is not None:
                try:
                    cached_id = int(cached.decode() if isinstance(cached, (bytes, bytearray)) else cached)
                except Exception:
                    cached_id = None
                if cached_id == patient_id:
                    raise BusinessHTTPException(
                        code=settings.REQ_ERROR_CODE,
                        msg="不能删除默认就诊人,请先取消默认设置",
                        status_code=400
                    )
        except BusinessHTTPException:
            raise
        except Exception as _e:
            logger.warning(
                f"检查默认就诊人缓存失败: user_patient_id={user_patient.patient_id}, err={_e}"
            )

        # 5. 删除关系
        await db.delete(relation)
        await db.commit()
        
        logger.info(f"删除就诊人成功: relation_id={relation.relation_id}")
        # 6. 若被删除的是 Redis 中的默认就诊人, 同步清理默认键
        try:
            redis_key = f"user_default_patient:{user_patient.patient_id}"
            cached = await redis.get(redis_key)
            if cached is not None:
                try:
                    cached_id = int(cached.decode() if isinstance(cached, (bytes, bytearray)) else cached)
                except Exception:
                    cached_id = None
                if cached_id == patient_id:
                    await redis.delete(redis_key)
                    logger.info(
                        f"已清理默认就诊人缓存: user_patient_id={user_patient.patient_id}, related_patient_id={patient_id}"
                    )
        except Exception as _e:
            logger.warning(
                f"删除就诊人后清理默认缓存失败: user_patient_id={user_patient.patient_id}, related_patient_id={patient_id}, err={_e}"
            )

        return ResponseModel(code=0, message={"message": "删除成功"})
        
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        await db.rollback()
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"删除就诊人时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="删除就诊人失败",
            status_code=500
        )


@router.put("/patients/{patient_id}/set-default", response_model=ResponseModel)
async def set_default_patient(
    patient_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """设置默认就诊人 - 需要登录
    
    参数:
    - patient_id: 被设置为默认的患者ID(related_patient_id)
    
    规则:
    - 使用 Redis 记录默认就诊人, 保证全局唯一
    """
    try:
        patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        user_patient = patient_res.scalar_one_or_none()
        
        if not user_patient:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="患者信息不存在",
                status_code=404
            )
        
        # 2. 查询要设为默认的关系记录
        relation_res = await db.execute(
            select(PatientRelation).where(
                and_(
                    PatientRelation.user_patient_id == user_patient.patient_id,
                    PatientRelation.related_patient_id == patient_id
                )
            )
        )
        relation = relation_res.scalar_one_or_none()
        
        if not relation:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="就诊人关系不存在",
                status_code=404
            )
        
        # 3. 双写策略：同时更新 Redis 和数据库
        # 3.1 先更新数据库（事务保证）
        try:
            # 取消当前用户所有就诊人的默认标记
            await db.execute(
                update(PatientRelation)
                .where(PatientRelation.user_patient_id == user_patient.patient_id)
                .values(is_default=False)
            )
            
            # 设置指定就诊人为默认
            await db.execute(
                update(PatientRelation)
                .where(
                    and_(
                        PatientRelation.user_patient_id == user_patient.patient_id,
                        PatientRelation.related_patient_id == patient_id
                    )
                )
                .values(is_default=True)
            )
            
            await db.commit()
            logger.info(f"[set_default_patient] 数据库更新成功 - user_patient_id={user_patient.patient_id}, default_patient_id={patient_id}")
        except Exception as db_err:
            await db.rollback()
            logger.error(f"[set_default_patient] 数据库更新失败: {db_err}")
            raise BusinessHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="设置默认就诊人失败",
                status_code=500
            )
        
        # 3.2 更新 Redis 缓存（异步，失败不影响主流程）
        try:
            redis_key = f"user_default_patient:{user_patient.patient_id}"
            logger.info(f"[set_default_patient] 更新 Redis 缓存 - redis_key={redis_key}, value={patient_id}")
            await redis.set(redis_key, str(patient_id))
            
            # 验证写入
            verify = await redis.get(redis_key)
            logger.info(f"[set_default_patient] Redis 写入验证成功 - value={verify}")
        except Exception as redis_err:
            # Redis 失败不影响主流程，仅记录日志
            logger.warning(f"[set_default_patient] Redis 更新失败（不影响功能）: {redis_err}")

        logger.info(f"设置默认就诊人成功: user_patient_id={user_patient.patient_id}, related_patient_id={patient_id}")

        return ResponseModel(code=0, message={"message": "设置成功"})
        
    except ResourceHTTPException:
        await db.rollback()
        raise
    except BusinessHTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"设置默认就诊人时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="设置默认就诊人失败",
            status_code=500
        )


@router.get("/patients/default", response_model=ResponseModel)
async def get_default_patient(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取当前默认就诊人信息 - 需要登录
    
    返回:
    - 默认就诊人的完整信息，如果没有设置默认就诊人则返回 null
    """
    try:
        # 1. 获取当前用户的患者信息
        patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        user_patient = patient_res.scalar_one_or_none()
        
        if not user_patient:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="患者信息不存在",
                status_code=404
            )
        
        # 2. 先从 Redis 获取默认就诊人 ID
        default_related_id: Optional[int] = None
        try:
            redis_key = f"user_default_patient:{user_patient.patient_id}"
            cached = await redis.get(redis_key)
            logger.info(f"[get_default_patient] 查询默认就诊人 - user_patient_id={user_patient.patient_id}, redis_key={redis_key}, cached={cached}")
            
            if cached:
                try:
                    default_related_id = int(cached.decode() if isinstance(cached, (bytes, bytearray)) else cached)
                    logger.info(f"[get_default_patient] Redis 中的默认就诊人 ID: {default_related_id}")
                except Exception as e:
                    logger.warning(f"[get_default_patient] Redis 值解析失败: {e}")
                    default_related_id = None
            else:
                logger.info(f"[get_default_patient] Redis 中没有默认就诊人记录，查询数据库")
        except Exception as e:
            logger.warning(f"[get_default_patient] Redis 查询失败，回退到数据库: {e}")
            default_related_id = None
        
        # 3. 如果 Redis 中没有，从数据库查询
        if default_related_id is None:
            db_relation_res = await db.execute(
                select(PatientRelation)
                .where(
                    and_(
                        PatientRelation.user_patient_id == user_patient.patient_id,
                        PatientRelation.is_default == True
                    )
                )
            )
            db_relation = db_relation_res.scalar_one_or_none()
            if db_relation:
                default_related_id = db_relation.related_patient_id
                logger.info(f"[get_default_patient] 从数据库获取默认就诊人 ID: {default_related_id}")
        
        # 4. 如果没有默认就诊人
        if default_related_id is None:
            logger.info(f"[get_default_patient] 用户 {user_patient.patient_id} 没有设置默认就诊人")
            return ResponseModel(code=0, message=None)
        
        # 5. 查询默认就诊人的完整信息
        result = await db.execute(
            select(PatientRelation, Patient)
            .join(Patient, Patient.patient_id == PatientRelation.related_patient_id)
            .where(
                and_(
                    PatientRelation.user_patient_id == user_patient.patient_id,
                    PatientRelation.related_patient_id == default_related_id
                )
            )
        )
        row = result.one_or_none()
        
        if not row:
            logger.warning(f"[get_default_patient] 默认就诊人记录不存在: related_patient_id={default_related_id}")
            return ResponseModel(code=0, message=None)
        
        relation, patient = row
        
        # 6. 计算年龄
        age = None
        if patient.birth_date:
            today = date_type.today()
            age = today.year - patient.birth_date.year
            if (today.month, today.day) < (patient.birth_date.month, patient.birth_date.day):
                age -= 1
        
        # 7. 脱敏处理手机号
        phone_masked = ""
        if patient.user_id:
            user_res = await db.execute(
                select(User).where(User.user_id == patient.user_id)
            )
            user = user_res.scalar_one_or_none()
            if user and user.phonenumber:
                phone = str(user.phonenumber)
                if len(phone) >= 11:
                    phone_masked = phone[:3] + "****" + phone[-4:]
                else:
                    phone_masked = phone
        
        # 8. 脱敏身份证号
        id_card_masked = ""
        if patient.id_card:
            id_card = str(patient.id_card)
            if len(id_card) == 18:
                id_card_masked = id_card[:6] + "********" + id_card[-4:]
            elif len(id_card) == 15:
                id_card_masked = id_card[:6] + "*****" + id_card[-4:]
            else:
                id_card_masked = id_card
        
        # 9. 构建响应
        default_patient_info = {
            "relation_id": relation.relation_id,
            "patient": {
                "patient_id": patient.patient_id,
                "name": patient.name,
                "gender": patient.gender,
                "age": age,
                "birth_date": patient.birth_date.isoformat() if patient.birth_date else None,
                "idCard": id_card_masked,
                "phone": phone_masked
            },
            "relation_type": relation.relation_type,
            "is_default": True,
            "remark": relation.remark,
            "create_time": relation.create_time.isoformat() if relation.create_time else None
        }
        
        logger.info(f"[get_default_patient] 获取默认就诊人成功: user_patient_id={user_patient.patient_id}, default_patient_id={default_related_id}")
        
        return ResponseModel(code=0, message=default_patient_info)
        
    except AuthHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取默认就诊人时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="获取默认就诊人失败",
            status_code=500
        )


@router.get("/appointments/{appointmentId}", response_model=ResponseModel)
async def get_appointment_detail(
    appointmentId: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取预约详情 - 需要登录
    
    参数:
    - appointmentId: 预约订单ID
    
    返回:
    - 预约的完整信息,包括医院、科室、医生、患者、时间、状态等
    """
    try:
        # 1. 查询预约订单及关联信息
        result = await db.execute(
            select(RegistrationOrder, Schedule, Doctor, Clinic, MinorDepartment, HospitalArea, Patient)
            .join(Schedule, Schedule.schedule_id == RegistrationOrder.schedule_id)
            .join(Doctor, Doctor.doctor_id == RegistrationOrder.doctor_id)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .join(MinorDepartment, MinorDepartment.minor_dept_id == Clinic.minor_dept_id)
            .join(HospitalArea, HospitalArea.area_id == Clinic.area_id)
            .join(Patient, Patient.patient_id == RegistrationOrder.patient_id)
            .where(RegistrationOrder.order_id == appointmentId)
        )
        
        row = result.first()
        
        if not row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="预约不存在",
                status_code=404
            )
        
        order, schedule, doctor, clinic, dept, area, patient = row
        
        # 2. 验证权限（只能查看自己的预约）
        if order.user_id != current_user.user_id:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权查看该预约",
                status_code=403
            )
        
        # 3. 判断是否可取消
        can_cancel = False
        if order.status in [OrderStatus.PENDING, OrderStatus.CONFIRMED]:
            # 获取配置
            schedule_config = await get_schedule_config(db)
            reg_config = await get_registration_config(
                db,
                scope_type="DOCTOR",
                scope_id=order.doctor_id
            )
            
            if not schedule_config:
                schedule_config = {}
            if not reg_config:
                reg_config = {}
            
            cancel_hours_before = reg_config.get("cancelHoursBefore", 2)
            
            now = datetime.now()
            appointment_datetime = datetime.combine(order.slot_date, datetime.min.time())
            
            # 根据时间段从配置中获取开始时间
            if order.time_section == "上午":
                time_str = schedule_config.get("morningStart", "08:00")
            elif order.time_section == "下午":
                time_str = schedule_config.get("afternoonStart", "13:30")
            else:
                time_str = schedule_config.get("eveningStart", "18:00")
            
            hour, minute = parse_time_to_hour_minute(time_str)
            cancel_deadline = appointment_datetime.replace(hour=hour, minute=minute) - timedelta(hours=cancel_hours_before)
            
            can_cancel = now < cancel_deadline
        
        # 4. 构建响应
        appointment_detail = {
            "id": order.order_id,
            "orderNo": order.order_no if order.order_no else _generate_order_no(),
            "hospitalId": area.area_id,
            "hospitalName": area.name,
            "hospitalAddress": area.destination,
            "departmentId": dept.minor_dept_id,
            "departmentName": dept.name,
            "doctorId": doctor.doctor_id,
            "doctorName": doctor.name,
            "doctorTitle": doctor.title or "",
            "doctorSpecialty": doctor.specialty,
            "scheduleId": schedule.schedule_id,
            "appointmentDate": str(order.slot_date),
            "appointmentTime": f"{order.time_section}",
            "slotType": _slot_type_to_str(schedule.slot_type),
            "patientId": patient.patient_id,
            "patientName": patient.name,
            "patientGender": patient.gender.value if hasattr(patient.gender, 'value') else str(patient.gender) if patient.gender else None,
            "patientPhone": None,  # 患者表中无 phone_number 字段,需要从 User 表获取
            "symptoms": order.symptoms,
            "price": float(order.price) if order.price else 0.0,
            "status": order.status.value,
            "paymentStatus": order.payment_status.value,
            "sourceType": order.source_type if hasattr(order, 'source_type') and order.source_type else "normal",
            "canCancel": can_cancel,
            "createdAt": order.create_time.strftime("%Y-%m-%d %H:%M:%S") if order.create_time else "",
            "paidAt": order.payment_time.strftime("%Y-%m-%d %H:%M:%S") if order.payment_time else None,
            "cancelledAt": order.cancel_time.strftime("%Y-%m-%d %H:%M:%S") if order.cancel_time else None
        }
        
        return ResponseModel(code=0, message=appointment_detail)
        
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取预约详情时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="获取预约详情失败",
            status_code=500
        )


@router.post("/appointments/{appointmentId}/send-reminder", response_model=ResponseModel)
async def send_appointment_reminder(
    appointmentId: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """手动发送就诊提醒 - 需要登录
    
    业务规则:
    1. 只能对未来日期且距离就诊不超过1天的订单发送提醒
    2. 只能对已支付已确认的订单发送
    3. 防止重复发送
    
    限制条件: 临近就诊的前一天才能提醒
    """
    try:
        # 1. 查询订单
        stmt = select(RegistrationOrder).where(RegistrationOrder.order_id == appointmentId)
        result = await db.execute(stmt)
        order = result.scalar_one_or_none()
        
        if not order:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="订单不存在",
                status_code=404
            )
        
        # 2. 权限检查：只能查看自己的订单或是当前用户的就诊人
        patient_res = await db.execute(
            select(Patient).where(Patient.patient_id == order.patient_id)
        )
        patient = patient_res.scalar_one_or_none()
        
        user_patient_res = await db.execute(
            select(Patient).where(Patient.user_id == current_user.user_id)
        )
        user_patient = user_patient_res.scalar_one_or_none()
        
        is_self = user_patient and patient and patient.patient_id == user_patient.patient_id
        is_related = False
        
        if not is_self and user_patient:
            relation_res = await db.execute(
                select(PatientRelation).where(
                    and_(
                        PatientRelation.user_patient_id == user_patient.patient_id,
                        PatientRelation.related_patient_id == order.patient_id
                    )
                )
            )
            is_related = relation_res.scalar_one_or_none() is not None
        
        if not is_self and not is_related:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权操作此订单",
                status_code=403
            )
        
        # 3. 业务规则检查：只能对已支付已确认的订单发送
        if order.payment_status != PaymentStatus.PAID:
            raise BusinessHTTPException(
                code=1001,
                msg="只能对已支付的订单发送提醒",
                status_code=400
            )
        
        if order.status != OrderStatus.CONFIRMED:
            raise BusinessHTTPException(
                code=1002,
                msg="只能对已确认的订单发送提醒",
                status_code=400
            )
        
        # 4. 限制条件：只能在就诊前一天发送（临近就诊）
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        
        # 允许的发送日期范围：从今天到明天都可以
        # 但实际检查应该是：订单日期必须是今天或明天
        if order.slot_date != today and order.slot_date != tomorrow:
            days_until = (order.slot_date - today).days
            raise BusinessHTTPException(
                code=1003,
                msg=f"只能在就诊前一天发送提醒，该订单距离就诊还有{days_until}天",
                status_code=400
            )
        
        # 5. 执行提醒发送
        from app.services.appointment_reminder_service import send_single_reminder
        
        stmt = select(Schedule).where(Schedule.schedule_id == order.schedule_id)
        result = await db.execute(stmt)
        schedule = result.scalar_one_or_none()
        
        if not schedule:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="排班信息不存在",
                status_code=404
            )
        
        doctor = await db.get(Doctor, schedule.doctor_id) if schedule and schedule.doctor_id else None
        clinic = await db.get(Clinic, schedule.clinic_id) if schedule and schedule.clinic_id else None
        
        if not patient or not doctor or not clinic:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="患者、医生或诊室信息不完整",
                status_code=404
            )
        
        # 调用发送提醒函数
        success = await send_single_reminder(db, order, schedule, patient, doctor, clinic)
        
        if success:
            logger.info(f"[手动提醒] 用户{current_user.user_id}手动发送订单{order.order_no}的提醒")
            return ResponseModel(code=0, message="提醒已发送")
        else:
            raise BusinessHTTPException(
                code=1004,
                msg="提醒发送失败，请检查授权情况或稍后重试",
                status_code=400
            )
        
    except AuthHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"手动发送就诊提醒时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="发送提醒失败",
            status_code=500
        )