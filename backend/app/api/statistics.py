from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from datetime import datetime, timedelta
from typing import Union

from app.db.base import get_db, User, UserAccessLog
from app.models.registration_order import RegistrationOrder
from app.models.schedule import Schedule
from app.models.clinic import Clinic
from app.models.doctor import Doctor
from app.models.minor_department import MinorDepartment
from app.models.hospital_area import HospitalArea
from app.models.major_department import MajorDepartment
from app.schemas.user import user as UserSchema
from app.schemas.response import StatisticsErrorResponse, UserStatisticsResponse,  ResponseModel, VisitStatisticsResponse,LoginCountByDayItem,LoginCountByDayResponse
from app.api.auth import get_current_user
from app.core.config import settings
from app.core.exception_handler import StatisticsHTTPException
from pydantic import BaseModel

# 默认日期字符串（用于 OpenAPI 默认值），在模块导入时计算一次
_today_str = datetime.utcnow().date().strftime("%Y-%m-%d")

    
router = APIRouter()


def _parse_date_range(date: str | None, date_range: str | None):
    """返回 (start_date, end_date) 的 date 对象（包含端点）。"""
    today = datetime.utcnow().date()
    if date_range:
        dr = date_range.lower()
        if dr == "today":
            return today, today
        if dr == "7days":
            return today - timedelta(days=6), today
        if dr == "30days":
            return today - timedelta(days=29), today
    # If no date provided, default to today
    if date is None:
        return today, today

    # If user provided an empty string, treat as invalid (don't silently fall back to today)
    if isinstance(date, str) and date.strip() == "":
        raise ValueError("参数 date 不能为空，请传入 YYYY-MM-DD 格式的日期或不传该参数")

    # Parse provided date and raise clear error on invalid format
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
        return d, d
    except Exception as ex:
        raise ValueError(f"日期格式无效: {date}. 请使用 YYYY-MM-DD") from ex


@router.get("/hospital/registrations", response_model=ResponseModel, tags=["Statistics"], summary="医院总体挂号统计")
async def hospital_registrations(
    date: str = Query(_today_str, description="统计日期，格式 YYYY-MM-DD"),
    date_range: str | None = Query(None, description="时间范围：today/7days/30days"),
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """返回医院总体挂号统计（按号源类型拆分与总收入）。仅管理员可用。"""
    try:
        if not getattr(current_user, "is_admin", False):
            raise Exception("仅管理员可访问")

        start_date, end_date = _parse_date_range(date, date_range)

        # 总挂号数（排除已取消）
        total_q = select(func.count()).select_from(RegistrationOrder).where(
            and_(RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        )
        total_registrations = await db.scalar(total_q)

        # 按号源类型统计
        by_type_q = select(RegistrationOrder.slot_type, func.count().label("cnt")).select_from(RegistrationOrder).where(
            and_(RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        ).group_by(RegistrationOrder.slot_type)
        res = await db.execute(by_type_q)
        by_slot_type = {r[0] or "未知": r[1] for r in res.all()}

        # 总收入：通过 join 到 schedule 使用 schedule.price
        rev_q = select(func.coalesce(func.sum(Schedule.price), 0)).select_from(RegistrationOrder).join(Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id).where(
            and_(RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        )
        total_revenue = float(await db.scalar(rev_q) or 0.0)

        # 已完成就诊数量
        completed_q = select(func.count()).select_from(RegistrationOrder).where(
            and_(RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status == "completed")
        )
        completed_consultations = await db.scalar(completed_q)

        return ResponseModel(code=0, message={
            "date": str(start_date) if start_date == end_date else None,
            "date_range": date_range or ("today" if start_date == end_date else None),
            "start_date": str(start_date),
            "end_date": str(end_date),
            "total_registrations": int(total_registrations or 0),
            "by_slot_type": by_slot_type,
            "total_revenue": total_revenue,
            "completed_consultations": int(completed_consultations or 0)
        })
    except Exception as e:
        raise StatisticsHTTPException(code=settings.DATA_GET_FAILED_CODE, msg=f"获取医院统计失败: {e}")


@router.get("/areas/{area_id}/registrations", response_model=ResponseModel, tags=["Statistics"], summary="分院区挂号统计")
async def area_registrations(
    area_id: int,
    date: str = Query(_today_str, description="统计日期，格式 YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """按院区统计挂号（当天或指定日期）。仅管理员。"""
    try:
        if not getattr(current_user, "is_admin", False):
            raise Exception("仅管理员可访问")
        start_date, end_date = _parse_date_range(date, None)

        # 总数与收入，按关联 clinic.area_id
        total_q = select(func.count()).select_from(RegistrationOrder).join(Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id).join(Clinic, Schedule.clinic_id == Clinic.clinic_id).where(
            and_(Clinic.area_id == area_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        )
        total_registrations = await db.scalar(total_q)

        by_type_q = select(Schedule.slot_type, func.count().label("cnt")).select_from(RegistrationOrder).join(Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id).join(Clinic, Schedule.clinic_id == Clinic.clinic_id).where(
            and_(Clinic.area_id == area_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        ).group_by(Schedule.slot_type)
        res = await db.execute(by_type_q)
        by_slot_type = {r[0] or "未知": r[1] for r in res.all()}

        rev_q = select(func.coalesce(func.sum(Schedule.price), 0)).select_from(RegistrationOrder).join(Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id).join(Clinic, Schedule.clinic_id == Clinic.clinic_id).where(
            and_(Clinic.area_id == area_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        )
        total_revenue = float(await db.scalar(rev_q) or 0.0)

        # 分科室统计（聚合到 minor_department via clinic.minor_dept_id）
        dept_q = select(Clinic.minor_dept_id, func.count().label("cnt"), func.coalesce(func.sum(Schedule.price), 0).label("rev")).select_from(RegistrationOrder).join(Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id).join(Clinic, Schedule.clinic_id == Clinic.clinic_id).where(
            and_(Clinic.area_id == area_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        ).group_by(Clinic.minor_dept_id)
        rows = await db.execute(dept_q)
        departments = []
        for rid, cnt, rev in rows.all():
            departments.append({
                "minor_dept_id": rid,
                "registrations": int(cnt or 0),
                "revenue": float(rev or 0.0)
            })

        return ResponseModel(code=0, message={
            "area_id": area_id,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "total_registrations": int(total_registrations or 0),
            "by_slot_type": by_slot_type,
            "total_revenue": total_revenue,
            "departments": departments
        })
    except Exception as e:
        raise StatisticsHTTPException(code=settings.DATA_GET_FAILED_CODE, msg=f"获取院区统计失败: {e}")


@router.get("/departments/{minor_dept_id}/registrations", response_model=ResponseModel, tags=["Statistics"], summary="科室挂号统计")
async def department_registrations(
    minor_dept_id: int,
    date: str = Query(_today_str, description="统计日期，格式 YYYY-MM-DD"),
    date_range: str | None = Query(None, description="时间范围：today/7days/30days"),
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """返回某小科室在指定日期/范围内的挂号统计，包含按医生分解。仅管理员。"""
    try:
        if not getattr(current_user, "is_admin", False):
            raise Exception("仅管理员可访问")
        start_date, end_date = _parse_date_range(date, date_range)

        total_q = select(func.count()).select_from(RegistrationOrder).join(Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id).join(Clinic, Schedule.clinic_id == Clinic.clinic_id).where(
            and_(Clinic.minor_dept_id == minor_dept_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        )
        total_registrations = await db.scalar(total_q)

        by_type_q = select(Schedule.slot_type, func.count().label("cnt")).select_from(RegistrationOrder).join(Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id).join(Clinic, Schedule.clinic_id == Clinic.clinic_id).where(
            and_(Clinic.minor_dept_id == minor_dept_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        ).group_by(Schedule.slot_type)
        res = await db.execute(by_type_q)
        by_slot_type = {r[0] or "未知": r[1] for r in res.all()}

        rev_q = select(func.coalesce(func.sum(Schedule.price), 0)).select_from(RegistrationOrder).join(Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id).join(Clinic, Schedule.clinic_id == Clinic.clinic_id).where(
            and_(Clinic.minor_dept_id == minor_dept_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        )
        total_revenue = float(await db.scalar(rev_q) or 0.0)

        completed_q = select(func.count()).select_from(RegistrationOrder).join(Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id).join(Clinic, Schedule.clinic_id == Clinic.clinic_id).where(
            and_(Clinic.minor_dept_id == minor_dept_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status == "completed")
        )
        completed_consultations = await db.scalar(completed_q)

        # 医生维度拆分
        doctors_q = select(RegistrationOrder.doctor_id, func.count().label("cnt"), func.coalesce(func.sum(Schedule.price), 0).label("rev")).select_from(RegistrationOrder).join(Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id).join(Clinic, Schedule.clinic_id == Clinic.clinic_id).where(
            and_(Clinic.minor_dept_id == minor_dept_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        ).group_by(RegistrationOrder.doctor_id)
        rows = await db.execute(doctors_q)
        doctors = []
        for did, cnt, rev in rows.all():
            # fetch doctor basic info
            doc = None
            if did:
                dres = await db.execute(select(Doctor).where(Doctor.doctor_id == did))
                doc = dres.scalar_one_or_none()
            doctors.append({
                "doctor_id": did,
                "doctor_name": doc.name if doc else None,
                "title": doc.title if doc else None,
                "registrations": int(cnt or 0),
                "revenue": float(rev or 0.0)
            })

        return ResponseModel(code=0, message={
            "minor_dept_id": minor_dept_id,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "total_registrations": int(total_registrations or 0),
            "by_slot_type": by_slot_type,
            "total_revenue": total_revenue,
            "completed_consultations": int(completed_consultations or 0),
            "doctors": doctors
        })
    except Exception as e:
        raise StatisticsHTTPException(code=settings.DATA_GET_FAILED_CODE, msg=f"获取科室统计失败: {e}")


@router.get("/doctors/{doctor_id}/registrations", response_model=ResponseModel, tags=["Statistics"], summary="医生挂号统计")
async def doctor_registrations(
    doctor_id: int,
    date: str = Query(_today_str, description="统计日期，格式 YYYY-MM-DD"),
    date_range: str | None = Query(None, description="时间范围：today/7days/30days"),
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """返回某医生在指定日期/范围内的挂号与排班利用率统计。仅管理员。"""
    try:
        if not getattr(current_user, "is_admin", False):
            raise Exception("仅管理员可访问")
        start_date, end_date = _parse_date_range(date, date_range)

        total_q = select(func.count()).select_from(RegistrationOrder).where(
            and_(RegistrationOrder.doctor_id == doctor_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        )
        total_registrations = await db.scalar(total_q)

        by_type_q = select(RegistrationOrder.slot_type, func.count().label("cnt")).where(
            and_(RegistrationOrder.doctor_id == doctor_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        ).group_by(RegistrationOrder.slot_type)
        res = await db.execute(by_type_q)
        by_slot_type = {r[0] or "未知": r[1] for r in res.all()}

        rev_q = select(func.coalesce(func.sum(Schedule.price), 0)).select_from(RegistrationOrder).join(Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id).where(
            and_(RegistrationOrder.doctor_id == doctor_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        )
        total_revenue = float(await db.scalar(rev_q) or 0.0)

        completed_q = select(func.count()).select_from(RegistrationOrder).where(
            and_(RegistrationOrder.doctor_id == doctor_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status == "completed")
        )
        completed_consultations = await db.scalar(completed_q)

        # 按时段统计
        by_time_q = select(RegistrationOrder.time_section, func.count().label("cnt")).where(
            and_(RegistrationOrder.doctor_id == doctor_id, RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date,
                 RegistrationOrder.status != "cancelled")
        ).group_by(RegistrationOrder.time_section)
        tres = await db.execute(by_time_q)
        by_time_section = {r[0] or "未知": r[1] for r in tres.all()}

        # 排班明细（利用率）
        sched_q = select(Schedule.schedule_id, Schedule.clinic_id, Schedule.time_section, Schedule.slot_type, Schedule.total_slots, func.coalesce(func.count(RegistrationOrder.order_id), 0).label("regs")).select_from(Schedule).outerjoin(RegistrationOrder, and_(RegistrationOrder.schedule_id == Schedule.schedule_id, RegistrationOrder.status != "cancelled", RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date)).where(
            and_(Schedule.doctor_id == doctor_id, Schedule.date >= start_date, Schedule.date <= end_date)
        ).group_by(Schedule.schedule_id)
        rows = await db.execute(sched_q)
        schedules = []
        for sid, cid, tsec, stype, total_slots, regs in rows.all():
            utilization = float(regs) / float(total_slots) if total_slots and total_slots > 0 else 0.0
            # clinic name
            clinic_name = None
            if cid:
                cres = await db.execute(select(Clinic).where(Clinic.clinic_id == cid))
                cobj = cres.scalar_one_or_none()
                clinic_name = cobj.name if cobj else None
            schedules.append({
                "schedule_id": sid,
                "clinic_name": clinic_name,
                "time_section": tsec,
                "slot_type": stype,
                "registrations": int(regs or 0),
                "total_slots": int(total_slots or 0),
                "utilization_rate": round(utilization, 2)
            })

        # doctor basic
        dres = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        doc = dres.scalar_one_or_none()

        return ResponseModel(code=0, message={
            "doctor_id": doctor_id,
            "doctor_name": doc.name if doc else None,
            "title": doc.title if doc else None,
            "dept_name": None,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "total_registrations": int(total_registrations or 0),
            "by_slot_type": by_slot_type,
            "total_revenue": total_revenue,
            "completed_consultations": int(completed_consultations or 0),
            "by_time_section": by_time_section,
            "schedules": schedules
        })
    except Exception as e:
        raise StatisticsHTTPException(code=settings.DATA_GET_FAILED_CODE, msg=f"获取医生统计失败: {e}")


@router.get("/departments/ranking", response_model=ResponseModel, tags=["Statistics"], summary="科室排行榜")
async def departments_ranking(
    date: str = Query(_today_str, description="统计日期，格式 YYYY-MM-DD"),
    order_by: str = Query("registrations", description="排序依据: registrations 或 revenue"),
    limit: int = Query(10, description="返回数量"),
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    try:
        if not getattr(current_user, "is_admin", False):
            raise Exception("仅管理员可访问")
        start_date, end_date = _parse_date_range(date, None)

        # 聚合到 minor_dept via clinic
        agg_q = select(Clinic.minor_dept_id.label("minor_dept_id"), func.count(RegistrationOrder.order_id).label("registrations"), func.coalesce(func.sum(Schedule.price), 0).label("revenue")).select_from(RegistrationOrder).join(Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id).join(Clinic, Schedule.clinic_id == Clinic.clinic_id).where(
            and_(RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date, RegistrationOrder.status != "cancelled")
        ).group_by(Clinic.minor_dept_id)
        if order_by == "revenue":
            agg_q = agg_q.order_by(func.coalesce(func.sum(Schedule.price), 0).desc())
        else:
            agg_q = agg_q.order_by(func.count(RegistrationOrder.order_id).desc())
        agg_q = agg_q.limit(limit)
        rows = await db.execute(agg_q)
        ranking = []
        for mid, regs, rev in rows.all():
            # fetch dept name
            dname = None
            if mid:
                dres = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == mid))
                dobj = dres.scalar_one_or_none()
                dname = dobj.name if dobj else None
            ranking.append({
                "minor_dept_id": mid,
                "dept_name": dname,
                "registrations": int(regs or 0),
                "revenue": float(rev or 0.0)
            })
        return ResponseModel(code=0, message={"date": str(start_date), "order_by": order_by, "ranking": ranking})
    except Exception as e:
        raise StatisticsHTTPException(code=settings.DATA_GET_FAILED_CODE, msg=f"获取科室排行榜失败: {e}")


@router.get("/doctors/ranking", response_model=ResponseModel, tags=["Statistics"], summary="医生排行榜")
async def doctors_ranking(
    dept_id: int | None = Query(None, description="限定科室ID"),
    date: str = Query(_today_str, description="统计日期，格式 YYYY-MM-DD"),
    order_by: str = Query("registrations", description="排序依据: registrations 或 revenue"),
    limit: int = Query(10, description="返回数量"),
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    try:
        if not getattr(current_user, "is_admin", False):
            raise Exception("仅管理员可访问")
        start_date, end_date = _parse_date_range(date, None)

        agg_q = select(RegistrationOrder.doctor_id.label("doctor_id"), func.count(RegistrationOrder.order_id).label("registrations"), func.coalesce(func.sum(Schedule.price), 0).label("revenue")).select_from(RegistrationOrder).join(Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id).where(
            and_(RegistrationOrder.slot_date >= start_date, RegistrationOrder.slot_date <= end_date, RegistrationOrder.status != "cancelled")
        ).group_by(RegistrationOrder.doctor_id)
        if dept_id:
            agg_q = agg_q.join(Doctor, RegistrationOrder.doctor_id == Doctor.doctor_id).where(Doctor.dept_id == dept_id)
        if order_by == "revenue":
            agg_q = agg_q.order_by(func.coalesce(func.sum(Schedule.price), 0).desc())
        else:
            agg_q = agg_q.order_by(func.count(RegistrationOrder.order_id).desc())
        agg_q = agg_q.limit(limit)
        rows = await db.execute(agg_q)
        ranking = []
        for did, regs, rev in rows.all():
            dname = None
            title = None
            dept_name = None
            if did:
                dres = await db.execute(select(Doctor).where(Doctor.doctor_id == did))
                dobj = dres.scalar_one_or_none()
                if dobj:
                    dname = dobj.name
                    title = dobj.title
                    if dobj.dept_id:
                        mdres = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == dobj.dept_id))
                        mdobj = mdres.scalar_one_or_none()
                        dept_name = mdobj.name if mdobj else None
            ranking.append({
                "doctor_id": did,
                "doctor_name": dname,
                "title": title,
                "dept_name": dept_name,
                "registrations": int(regs or 0),
                "revenue": float(rev or 0.0)
            })
        return ResponseModel(code=0, message={"date": str(start_date), "order_by": order_by, "ranking": ranking})
    except Exception as e:
        raise StatisticsHTTPException(code=settings.DATA_GET_FAILED_CODE, msg=f"获取医生排行榜失败: {e}")

@router.get("/users", response_model=ResponseModel[Union[UserStatisticsResponse, StatisticsErrorResponse]], tags=["Statistics"], summary="统计用户数", description="返回总用户数和较几天前的增长比例。需要Token认证。")
async def get_user_statistics(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    统计用户数
    - 返回总用户数
    """
    try:
        now = datetime.utcnow()
        total_users = await db.scalar(select(func.count()).select_from(User).where(User.is_deleted == 0))
        return ResponseModel(code=0, message=UserStatisticsResponse(total_users=total_users))
    except Exception as e:
        raise StatisticsHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg=f"获取用户统计数据失败: {str(e)}"
        )


@router.get("/visits", response_model=ResponseModel[Union[VisitStatisticsResponse, StatisticsErrorResponse]], tags=["Statistics"], summary="统计网站访问量", description="返回网站总访问量和较几天前的增长比例。需要Token认证。")
async def get_visit_statistics(
    compare_days: int = Query(settings.COMPARE_DAYS, description="对比天数，默认3天前"),
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    统计网站访问量
    - 返回访问日志总数
    - 返回较 compare_days 天前的增长比例
    """
    try:
        now = datetime.utcnow()
        compare_time = now - timedelta(days=compare_days)
        total_visits = await db.scalar(select(func.count()).select_from(UserAccessLog))
        old_visits = await db.scalar(select(func.count()).select_from(UserAccessLog).where(UserAccessLog.access_time < compare_time))
        growth_percent = 0.0
        if old_visits and total_visits > old_visits:
            growth_percent = (total_visits - old_visits) / old_visits * 100
        return ResponseModel(code=0, message=VisitStatisticsResponse(total_visits=total_visits, growth_percent=round(growth_percent, 2), compare_days=compare_days))
    except Exception as e:
        raise StatisticsHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg=f"获取访问量统计数据失败: {str(e)}"
        )
