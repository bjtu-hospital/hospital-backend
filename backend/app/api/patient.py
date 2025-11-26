from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, update
from typing import Optional
from datetime import datetime, timedelta, date as date_type
import logging
import base64
import os
import aiofiles

from app.db.base import get_db, User, MajorDepartment, MinorDepartment, Doctor, Clinic, Schedule
from app.models.hospital_area import HospitalArea
from app.models.registration_order import RegistrationOrder, OrderStatus, PaymentStatus
from app.models.patient import Patient
from app.schemas.response import ResponseModel
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentListResponse,
    AppointmentListItem,
    CancelAppointmentResponse
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
    parse_time_to_hour_minute
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ====== 患者端公开查询接口(无需登录) ======


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
                "is_registered": is_registered,
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
        
    except Exception as e:
        logger.error(f"获取医生列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
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
        
    except ResourceHTTPException:
        raise
    except BusinessHTTPException:
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
        
        # 2. 验证患者是否存在且属于当前用户
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
        
        # 验证患者归属
        if patient.user_id != current_user.user_id:
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
        
        new_order = RegistrationOrder(
            order_no=order_no,
            patient_id=data.patientId,
            user_id=current_user.user_id,
            doctor_id=schedule.doctor_id,
            schedule_id=data.scheduleId,
            slot_date=schedule.date,
            time_section=schedule.time_section,
            slot_type=str(schedule.slot_type.value if hasattr(schedule.slot_type, 'value') else schedule.slot_type),
            price=schedule.price,
            symptoms=data.symptoms,
            status=OrderStatus.PENDING,  # 待支付
            payment_status=PaymentStatus.PENDING,  # 待支付
            create_time=datetime.now(),
            update_time=datetime.now()
        )
        
        db.add(new_order)
        
        # 7. 锁定号源 - 减少 remaining_slots
        schedule.remaining_slots -= 1
        db.add(schedule)
        
        await db.commit()
        await db.refresh(new_order)
        
        # 8. 计算队列号码(当天同排班已预约数量)
        queue_res = await db.execute(
            select(func.count()).select_from(RegistrationOrder).where(
                and_(
                    RegistrationOrder.schedule_id == data.scheduleId,
                    RegistrationOrder.status.in_([OrderStatus.PENDING, OrderStatus.CONFIRMED])
                )
            )
        )
        queue_number = queue_res.scalar()
        
        logger.info(f"创建预约成功: order_id={new_order.order_id}, order_no={order_no}, patient_id={data.patientId}")
        
        return ResponseModel(code=0, message=AppointmentResponse(
            id=new_order.order_id,
            orderNo=order_no,
            queueNumber=queue_number,
            needPay=True,
            payAmount=float(schedule.price) if schedule.price else 0.0,
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
            
            appointment_list.append(AppointmentListItem(
                id=order.order_id,
                orderNo=order.order_no or "",
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
                canReschedule=False,
                createdAt=order.create_time.strftime("%Y-%m-%d %H:%M:%S") if order.create_time else ""
            ))
        
        return ResponseModel(code=0, message=AppointmentListResponse(
            total=total,
            page=page,
            pageSize=pageSize,
            list=appointment_list
        ))
        
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
        
        # 2. 验证订单归属
        if order.user_id != current_user.user_id:
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
        
        # 4. 检查取消时间限制(根据配置动态计算)
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
        
        # 5. 取消订单
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


