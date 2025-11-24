from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import Optional
from datetime import datetime, timedelta
import logging

from app.db.base import get_db, User, MajorDepartment, MinorDepartment, Doctor, Clinic, Schedule
from app.models.hospital_area import HospitalArea
from app.schemas.response import ResponseModel
from app.core.config import settings
from app.core.exception_handler import BusinessHTTPException, ResourceHTTPException
from app.services.admin_helpers import (
    bulk_get_doctor_prices,
    bulk_get_clinic_prices,
    bulk_get_minor_dept_prices,
    _weekday_to_cn,
    _slot_type_to_str,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ====== 患者端公开查询接口(无需登录) ======


@router.get("/hospitals", response_model=ResponseModel)
async def get_hospitals(
    area_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """获取院区列表 - 公开接口,无需登录
    
    参数:
    - area_id: 可选,指定院区ID则返回该院区信息,不传则返回全部院区
    
    返回:
    - areas: 院区列表,包含 area_id, name, destination, latitude, longitude, image_url
    """
    try:
        # 构建查询
        stmt = select(HospitalArea)
        if area_id is not None:
            stmt = stmt.where(HospitalArea.area_id == area_id)
        
        result = await db.execute(stmt)
        areas = result.scalars().all()
        
        # 构建响应
        area_list = [
            {
                "area_id": area.area_id,
                "name": area.name,
                "destination": area.destination,
                "latitude": float(area.latitude) if area.latitude else None,
                "longitude": float(area.longitude) if area.longitude else None,
                "image_url": area.image_url,
                "create_time": area.create_time.isoformat() if area.create_time else None
            }
            for area in areas
        ]
        
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



