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
    """返回 (start_date, end_date)，逻辑说明：
    1. 先解析 date 作为锚点（anchor）。如果未提供，则使用当前 UTC 日期 today。
    2. date == "" 视为错误；格式不符抛出明确异常。
    3. date_range 取值：today / 7days / 30days；均以锚点日期为 end_date，向前回溯。
       - today  => (anchor, anchor)
       - 7days  => (anchor - 6, anchor)
       - 30days => (anchor - 29, anchor)
    4. 未提供 date_range 或传入 today 时返回单日范围。
    5. 传入未知的 date_range 值抛异常，避免静默回退。
    """
    today = datetime.utcnow().date()

    # 解析锚点日期
    if date is None:
        anchor = today
    else:
        if date.strip() == "":
            raise ValueError("参数 date 不能为空，请传入 YYYY-MM-DD 格式的日期或不传该参数")
        try:
            anchor = datetime.strptime(date, "%Y-%m-%d").date()
        except Exception as ex:
            raise ValueError(f"日期格式无效: {date}. 请使用 YYYY-MM-DD") from ex

    # 处理日期范围
    if not date_range or date_range.lower() == "today":
        return anchor, anchor

    dr = date_range.lower()
    mapping = {"7days": 6, "30days": 29}
    if dr in mapping:
        return anchor - timedelta(days=mapping[dr]), anchor

    raise ValueError(f"时间范围无效: {date_range}. 允许值: today,7days,30days")


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
        stats_list = rows.all()
        
        # 批量获取医生信息，避免 N+1 查询
        doctor_ids = [did for did, _, _ in stats_list if did]
        doctors_map = {}
        if doctor_ids:
            docs_result = await db.execute(select(Doctor).where(Doctor.doctor_id.in_(doctor_ids)))
            for doc in docs_result.scalars().all():
                doctors_map[doc.doctor_id] = doc
        
        doctors = []
        for did, cnt, rev in stats_list:
            doc = doctors_map.get(did)
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
        sched_list = rows.all()
        
        # 批量获取门诊信息，避免 N+1 查询
        clinic_ids = [cid for _, cid, _, _, _, _ in sched_list if cid]
        clinics_map = {}
        if clinic_ids:
            clinics_result = await db.execute(select(Clinic).where(Clinic.clinic_id.in_(clinic_ids)))
            for clinic in clinics_result.scalars().all():
                clinics_map[clinic.clinic_id] = clinic
        
        schedules = []
        for sid, cid, tsec, stype, total_slots, regs in sched_list:
            utilization = float(regs) / float(total_slots) if total_slots and total_slots > 0 else 0.0
            clinic = clinics_map.get(cid)
            schedules.append({
                "schedule_id": sid,
                "clinic_name": clinic.name if clinic else None,
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

        # 优化: 先获取 clinic_id -> minor_dept_id 映射,避免重复 JOIN
        clinics_result = await db.execute(select(Clinic.clinic_id, Clinic.minor_dept_id))
        clinic_to_dept = {c_id: d_id for c_id, d_id in clinics_result.all()}
        
        # 聚合查询只 JOIN Schedule 获取价格
        agg_q = select(
            Schedule.clinic_id.label("clinic_id"),
            func.count(RegistrationOrder.order_id).label("registrations"),
            func.coalesce(func.sum(Schedule.price), 0).label("revenue")
        ).select_from(RegistrationOrder).join(
            Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id
        ).where(
            and_(
                RegistrationOrder.slot_date >= start_date,
                RegistrationOrder.slot_date <= end_date,
                RegistrationOrder.status != "cancelled"
            )
        ).group_by(Schedule.clinic_id)
        
        rows = await db.execute(agg_q)
        clinic_stats = rows.all()
        
        # 按科室聚合
        dept_stats = {}
        for clinic_id, regs, rev in clinic_stats:
            dept_id = clinic_to_dept.get(clinic_id)
            if dept_id:
                if dept_id not in dept_stats:
                    dept_stats[dept_id] = {"registrations": 0, "revenue": 0.0}
                dept_stats[dept_id]["registrations"] += int(regs or 0)
                dept_stats[dept_id]["revenue"] += float(rev or 0.0)
        
        # 排序
        sorted_depts = sorted(
            dept_stats.items(),
            key=lambda x: x[1][order_by] if order_by in x[1] else x[1]["registrations"],
            reverse=True
        )[:limit]
        
        # 批量获取科室信息
        dept_ids = [dept_id for dept_id, _ in sorted_depts]
        depts_map = {}
        if dept_ids:
            depts_result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id.in_(dept_ids)))
            for dept in depts_result.scalars().all():
                depts_map[dept.minor_dept_id] = dept
        
        ranking = []
        for dept_id, stats in sorted_depts:
            dept = depts_map.get(dept_id)
            ranking.append({
                "minor_dept_id": dept_id,
                "dept_name": dept.name if dept else None,
                "registrations": stats["registrations"],
                "revenue": stats["revenue"]
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

        # 如果指定科室,先过滤医生 ID
        doctor_filter = []
        if dept_id:
            docs_in_dept = await db.execute(select(Doctor.doctor_id).where(Doctor.dept_id == dept_id))
            doctor_filter = [did for (did,) in docs_in_dept.all()]
            if not doctor_filter:
                # 该科室无医生
                return ResponseModel(code=0, message={"date": str(start_date), "order_by": order_by, "ranking": []})
        
        # 聚合查询(无需 JOIN Doctor 表)
        agg_q = select(
            RegistrationOrder.doctor_id.label("doctor_id"),
            func.count(RegistrationOrder.order_id).label("registrations"),
            func.coalesce(func.sum(Schedule.price), 0).label("revenue")
        ).select_from(RegistrationOrder).join(
            Schedule, RegistrationOrder.schedule_id == Schedule.schedule_id
        ).where(
            and_(
                RegistrationOrder.slot_date >= start_date,
                RegistrationOrder.slot_date <= end_date,
                RegistrationOrder.status != "cancelled"
            )
        )
        
        if doctor_filter:
            agg_q = agg_q.where(RegistrationOrder.doctor_id.in_(doctor_filter))
        
        agg_q = agg_q.group_by(RegistrationOrder.doctor_id)
        
        # 排序
        if order_by == "revenue":
            agg_q = agg_q.order_by(func.coalesce(func.sum(Schedule.price), 0).desc())
        else:
            agg_q = agg_q.order_by(func.count(RegistrationOrder.order_id).desc())
        
        agg_q = agg_q.limit(limit)
        rows = await db.execute(agg_q)
        stats_list = rows.all()
        
        # 批量获取医生和科室信息
        doctor_ids = [did for did, _, _ in stats_list if did]
        doctors_map = {}
        dept_ids_set = set()
        if doctor_ids:
            docs_result = await db.execute(select(Doctor).where(Doctor.doctor_id.in_(doctor_ids)))
            for doc in docs_result.scalars().all():
                doctors_map[doc.doctor_id] = doc
                if doc.dept_id:
                    dept_ids_set.add(doc.dept_id)
        
        depts_map = {}
        if dept_ids_set:
            depts_result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id.in_(list(dept_ids_set))))
            for dept in depts_result.scalars().all():
                depts_map[dept.minor_dept_id] = dept
        
        ranking = []
        for did, regs, rev in stats_list:
            doc = doctors_map.get(did)
            dept = depts_map.get(doc.dept_id) if doc and doc.dept_id else None
            ranking.append({
                "doctor_id": did,
                "doctor_name": doc.name if doc else None,
                "title": doc.title if doc else None,
                "dept_name": dept.name if dept else None,
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
