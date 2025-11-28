from fastapi import APIRouter, Depends,UploadFile, File, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, delete
from sqlalchemy.orm.attributes import flag_modified
from typing import Optional, Union, List
import logging
import os
import time
import mimetypes
import aiofiles
from app.core.security import get_hash_pwd
from datetime import datetime, date, timedelta
from app.api.auth import get_current_user
from app.models.user_access_log import UserAccessLog
from app.schemas.admin import MajorDepartmentCreate, MajorDepartmentUpdate, MinorDepartmentCreate, MinorDepartmentUpdate, DoctorCreate, DoctorUpdate, DoctorAccountCreate, DoctorTransferDepartment, ClinicCreate, ClinicUpdate, ClinicListResponse, ScheduleCreate, ScheduleUpdate, ScheduleListResponse
from app.schemas.admin import AddSlotAuditListResponse, AddSlotAuditResponse, HospitalAreaItem, HospitalAreaListResponse
from app.schemas.response import (
    ResponseModel, AuthErrorResponse, MajorDepartmentListResponse, MinorDepartmentListResponse, DoctorListResponse, DoctorAccountCreateResponse, DoctorTransferResponse
)
from app.schemas.config import SystemConfigRequest, SystemConfigResponse, RegistrationConfig, ScheduleConfig
from app.db.base import get_db, redis, User, Administrator, MajorDepartment, MinorDepartment, Doctor, Clinic, Schedule, ScheduleAudit, LeaveAudit, AddSlotAudit
from app.models.hospital_area import HospitalArea
from app.models.patient import Patient
from app.services.crawler_service import import_all_json, crawl_and_import_schedules
from app.models.user_ban import UserBan
from app.models.risk_log import RiskLog
from app.models.user_risk_summary import UserRiskSummary
from app.models.registration_order import RegistrationOrder, OrderStatus
from sqlalchemy import select, and_, delete, func, or_
from app.models.system_config import SystemConfig
from app.schemas.user import user as UserSchema
from app.schemas.audit import (
    ScheduleAuditItem, ScheduleAuditListResponse, ScheduleDoctorInfo,AuditAction, AuditActionResponse,LeaveAuditItem, LeaveAuditListResponse 
)
from app.core.config import settings
from app.core.exception_handler import AuthHTTPException, BusinessHTTPException, ResourceHTTPException
from app.services.admin_helpers import (
    get_hierarchical_price,
    get_entity_prices,
    update_entity_prices,
    _weekday_to_cn,
    _slot_type_to_str,
    _str_to_slot_type,
    get_administrator_id,
    calculate_leave_days,
    bulk_get_doctor_prices,
    bulk_get_clinic_prices,
    bulk_get_minor_dept_prices,
)
from app.services.config_service import (
    get_registration_config,
    get_schedule_config,
    get_config_value,
    get_department_head_config
)
from app.services.absence_detection_service import (
    mark_absent_for_date,
    mark_absent_for_date_range,
    get_absent_statistics
)

from app.schemas.anti_scalper import (
    AntiScalperUserItem,
    AntiScalperUserListResponse,
    AntiScalperUserDetailResponse,
    AntiScalperUserStatsResponse,
    UserBanRequest,
    UserUnbanRequest,
    RiskLogItem,
    BanRecordItem,
)



logger = logging.getLogger(__name__)
router = APIRouter()


# ====== 管理员科室管理接口 ======

# 大科室管理
@router.post("/major-departments", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
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
            raise BusinessHTTPException(
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
                "description": db_dept.description
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"创建大科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ====== 防黄牛(anti-scalper) 管理接口 ======



def _ensure_admin(current_user: UserSchema):
    if not getattr(current_user, "is_admin", False):
        raise AuthHTTPException(
            code=settings.INSUFFICIENT_AUTHORITY_CODE,
            msg="仅管理员可操作",
            status_code=403
        )


@router.get("/anti-scalper/users", response_model=ResponseModel[Union[AntiScalperUserListResponse, AuthErrorResponse]])
async def anti_scalper_users(
    user_type: Optional[str] = "normal",
    page: int = 1,
    page_size: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """SEC-001 获取风险用户列表
    user_type: high | low | normal | banned
    分页返回用户基础信息 + 风险 + 封禁状态
    """
    try:
        _ensure_admin(current_user)
        if page <= 0 or page_size <= 0:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="分页参数必须为正整数")

        # 1. 获取所有活跃封禁记录
        banned_result = await db.execute(select(UserBan).where(UserBan.is_active == True))
        banned_records = banned_result.scalars().all()
        banned_map = {r.user_id: r for r in banned_records}
        
        # 2. 获取所有用户（用于 normal 场景）
        users_result = await db.execute(select(User))
        all_users = users_result.scalars().all()

        # 3. 预取这些用户的风险汇总
        user_ids_all = [u.user_id for u in all_users]
        summary_map = {}
        if user_ids_all:
            sum_result = await db.execute(
                select(UserRiskSummary).where(UserRiskSummary.user_id.in_(user_ids_all))
            )
            summaries = sum_result.scalars().all()
            summary_map = {s.user_id: s for s in summaries}

        if user_type not in {"high", "low", "normal", "banned"}:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="user_type 无效")

        filtered: List[User] = []
        if user_type == "banned":
            filtered = [u for u in all_users if u.user_id in banned_map]
        elif user_type == "high":
            # 高风险用户: 风险等级为 MEDIUM 或 HIGH
            filtered = [
                u for u in all_users
                if summary_map.get(u.user_id) and summary_map[u.user_id].current_level in ('MEDIUM', 'HIGH')
            ]
        elif user_type == "low":
            # 低风险用户: 风险等级为 LOW
            filtered = [
                u for u in all_users
                if summary_map.get(u.user_id) and summary_map[u.user_id].current_level == 'LOW'
            ]
        else:  # normal
            # 正常用户: 未被封禁且（无风险汇总或风险等级为 SAFE）
            filtered = [
                u for u in all_users
                if u.user_id not in banned_map and (
                    not summary_map.get(u.user_id) or summary_map[u.user_id].current_level == 'SAFE'
                )
            ]

        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size
        page_slice = filtered[start:end]

        items: List[AntiScalperUserItem] = []
        for u in page_slice:
            ban_rec = banned_map.get(u.user_id)
            summary = summary_map.get(u.user_id)
            items.append(
                AntiScalperUserItem(
                    user_id=u.user_id,
                    username=u.email or u.phonenumber or u.identifier,
                    risk_level=(summary.current_level if summary else None),
                    risk_score=(summary.current_score if summary else None),
                    banned=ban_rec is not None,
                    ban_type=ban_rec.ban_type if ban_rec else None,
                    ban_until=ban_rec.ban_until if ban_rec else None
                )
            )

        return ResponseModel(code=0, message=AntiScalperUserListResponse(total=total, page=page, page_size=page_size, users=items))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取风险用户列表异常: {e}")
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="内部服务异常", status_code=500)


@router.get("/anti-scalper/users/{user_id}", response_model=ResponseModel[Union[AntiScalperUserDetailResponse, AuthErrorResponse]])
async def anti_scalper_user_detail(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """SEC-002 获取单个用户风险与封禁详情"""
    try:
        _ensure_admin(current_user)
        user_result = await db.execute(select(User).where(User.user_id == user_id))
        db_user = user_result.scalar_one_or_none()
        if not db_user:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="用户不存在", status_code=404)

        # 风险汇总
        sum_res = await db.execute(select(UserRiskSummary).where(UserRiskSummary.user_id == user_id))
        summary = sum_res.scalar_one_or_none()

        # 所有封禁记录（按时间倒序）
        bans_res = await db.execute(select(UserBan).where(UserBan.user_id == user_id).order_by(UserBan.create_time.desc()))
        bans = bans_res.scalars().all()
        active_ban = next((b for b in bans if b.is_active), None)

        # 最近的风险日志（可选限制数量）
        rlogs_res = await db.execute(select(RiskLog).where(RiskLog.user_id == user_id).order_by(RiskLog.alert_time.desc()))
        rlogs = rlogs_res.scalars().all()

        detail = AntiScalperUserDetailResponse(
            user_id=db_user.user_id,
            username=db_user.email or db_user.phonenumber or db_user.identifier,
            is_admin=db_user.is_admin,
            risk_score=(summary.current_score if summary else None),
            risk_level=(summary.current_level if summary else None),
            ban_active=active_ban.is_active if active_ban else False,
            ban_type=active_ban.ban_type if active_ban else None,
            ban_until=active_ban.ban_until if active_ban else None,
            ban_reason=active_ban.reason if active_ban else None,
            unban_time=active_ban.unban_time if active_ban else None,
            risk_logs=[
                RiskLogItem(
                    log_id=rl.risk_log_id,
                    risk_score=rl.risk_score,
                    risk_level=rl.risk_level,
                    behavior_type=rl.behavior_type,
                    description=rl.description,
                    alert_time=rl.alert_time,
                ) for rl in rlogs
            ],
            ban_records=[
                BanRecordItem(
                    ban_id=b.ban_id,
                    ban_type=b.ban_type,
                    ban_until=b.ban_until,
                    is_active=b.is_active,
                    reason=b.reason,
                    banned_at=b.create_time,
                    deactivated_at=b.unban_time
                ) for b in bans
            ]
        )
        return ResponseModel(code=0, message=detail)
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户风险详情异常: {e}")
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="内部服务异常", status_code=500)


@router.get("/anti-scalper/users/{user_id}/stats", response_model=ResponseModel[Union[AntiScalperUserStatsResponse, AuthErrorResponse]])
async def anti_scalper_user_stats(
    user_id: int,
    start_date: date,
    end_date: date,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """SEC-003 用户在时间段内的行为统计 (挂号/取消等)"""
    try:
        _ensure_admin(current_user)
        # 校验用户存在
        user_result = await db.execute(select(User).where(User.user_id == user_id))
        if not user_result.scalar_one_or_none():
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="用户不存在", status_code=404)

        if start_date > end_date:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="开始日期不能大于结束日期")
        
        sd = start_date
        ed = end_date

        # 查询挂号记录
        regs_result = await db.execute(
            select(RegistrationOrder).where(
                and_(
                    RegistrationOrder.user_id == user_id,
                    RegistrationOrder.slot_date >= sd,
                    RegistrationOrder.slot_date <= ed
                )
            )
        )
        orders = regs_result.scalars().all()
        total_registrations = len(orders)
        total_cancellations = sum(1 for o in orders if o.status == OrderStatus.CANCELLED)
        cancellation_rate = round(total_cancellations / total_registrations, 4) if total_registrations else 0.0

        # 细分各状态数量
        total_completed = sum(1 for o in orders if o.status == OrderStatus.COMPLETED)
        total_no_show = sum(1 for o in orders if o.status == OrderStatus.NO_SHOW)
        total_confirmed = sum(1 for o in orders if o.status == OrderStatus.CONFIRMED)
        total_pending = sum(1 for o in orders if o.status == OrderStatus.PENDING)
        total_waitlist = sum(1 for o in orders if o.status == OrderStatus.WAITLIST)

        # 登录次数（统计成功登录请求）
        start_dt = datetime.combine(sd, datetime.min.time())
        end_dt = datetime.combine(ed, datetime.max.time())
        login_count_result = await db.execute(
            select(func.count()).where(
                and_(
                    UserAccessLog.user_id == user_id,
                    UserAccessLog.status_code == 200,
                    UserAccessLog.access_time >= start_dt,
                    UserAccessLog.access_time <= end_dt,
                    or_(
                        UserAccessLog.url.contains("/auth/patient/login"),
                        UserAccessLog.url.contains("/auth/staff/login"),
                        UserAccessLog.url.contains("/auth/swagger-login"),
                    )
                )
            )
        )
        login_count = login_count_result.scalar_one()

        stats = AntiScalperUserStatsResponse(
            user_id=user_id,
            start_date=sd,
            end_date=ed,
            total_registrations=total_registrations,
            total_cancellations=total_cancellations,
            cancellation_rate=cancellation_rate,
            total_completed=total_completed,
            total_no_show=total_no_show,
            total_confirmed=total_confirmed,
            total_pending=total_pending,
            total_waitlist=total_waitlist,
            login_count=login_count
        )
        return ResponseModel(code=0, message=stats)
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户统计异常: {e}")
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="内部服务异常", status_code=500)


@router.post("/anti-scalper/ban", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def anti_scalper_ban_user(
    data: UserBanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """SEC-004 执行用户封禁 / 覆盖更新封禁记录"""
    try:
        _ensure_admin(current_user)
        # 校验用户存在
        user_result = await db.execute(select(User).where(User.user_id == data.user_id))
        if not user_result.scalar_one_or_none():
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="用户不存在", status_code=404)

        # 查找现有封禁记录
        ban_result = await db.execute(select(UserBan).where(UserBan.user_id == data.user_id))
        ban_rec = ban_result.scalar_one_or_none()
        if ban_rec:
            # 更新
            ban_rec.ban_type = data.ban_type
            ban_rec.is_active = True
            ban_rec.apply_duration(data.duration_days)
            ban_rec.reason = data.reason
        else:
            ban_rec = UserBan(
                user_id=data.user_id,
                ban_type=data.ban_type,
                is_active=True,
                reason=data.reason
            )
            ban_rec.apply_duration(data.duration_days)
            db.add(ban_rec)

        await db.commit()
        await db.refresh(ban_rec)

        return ResponseModel(code=0, message={
            "detail": "封禁操作成功",
            "user_id": data.user_id,
            "ban_type": ban_rec.ban_type,
            "ban_until": ban_rec.ban_until.isoformat() if ban_rec.ban_until else None,
            "is_active": ban_rec.is_active
        })
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"用户封禁异常: {e}")
        raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="内部服务异常", status_code=500)


@router.post("/anti-scalper/unban", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def anti_scalper_unban_user(
    data: UserUnbanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """SEC-005 解除用户封禁"""
    try:
        _ensure_admin(current_user)
        ban_result = await db.execute(
            select(UserBan).where(and_(UserBan.user_id == data.user_id, UserBan.is_active == True))  # noqa: E712
        )
        ban_rec = ban_result.scalar_one_or_none()
        if not ban_rec:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="该用户当前无有效封禁", status_code=404)

        ban_rec.deactivate(extra_reason=data.reason)
        await db.commit()
        await db.refresh(ban_rec)

        return ResponseModel(code=0, message={
            "detail": "解除封禁成功",
            "user_id": data.user_id,
            "ban_type": ban_rec.ban_type,
            "unban_time": ban_rec.unban_time.isoformat() if ban_rec.unban_time else None
        })
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"解除封禁异常: {e}")
        raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="内部服务异常", status_code=500)


@router.get("/major-departments", response_model=ResponseModel[Union[MajorDepartmentListResponse, AuthErrorResponse]])
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
                "description": dept.description
            })
        
        return ResponseModel(
            code=0,
            message=MajorDepartmentListResponse(departments=dept_list)
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取大科室列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/major-departments/{dept_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
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
            raise ResourceHTTPException(
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
                raise BusinessHTTPException(
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
                "description": db_dept.description
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新大科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.delete("/major-departments/{dept_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
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
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="科室不存在",
                status_code=404
            )

        # 检查是否存在小科室依赖
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.major_dept_id == dept_id))
        if result.scalar_one_or_none():
            raise BusinessHTTPException(
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
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除大科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# 小科室管理
@router.post("/minor-departments", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
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
            raise ResourceHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="大科室不存在",
                status_code=400
            )
        
        # 检查小科室名称是否已存在
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.name == dept_data.name))
        if result.scalar_one_or_none():
            raise BusinessHTTPException(
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
        
        # 如果提供了价格配置，则创建价格记录
        if (dept_data.default_price_normal is not None or 
            dept_data.default_price_expert is not None or 
            dept_data.default_price_special is not None):
            await update_entity_prices(
                db=db,
                scope_type="MINOR_DEPT",
                scope_id=db_dept.minor_dept_id,
                default_price_normal=dept_data.default_price_normal,
                default_price_expert=dept_data.default_price_expert,
                default_price_special=dept_data.default_price_special
            )
        
        logger.info(f"创建小科室成功: {dept_data.name}")

        # 获取价格配置
        prices = await get_entity_prices(
            db=db,
            scope_type="MINOR_DEPT",
            scope_id=db_dept.minor_dept_id
        )
        
        return ResponseModel(
            code=0,
            message={
                "minor_dept_id": db_dept.minor_dept_id,
                "major_dept_id": db_dept.major_dept_id,
                "name": db_dept.name,
                "description": db_dept.description,
                "default_price_normal": prices["default_price_normal"],
                "default_price_expert": prices["default_price_expert"],
                "default_price_special": prices["default_price_special"]
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"创建小科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/minor-departments/{minor_dept_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_minor_department(
    minor_dept_id: int,
    dept_data: MinorDepartmentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    更新小科室信息 - 仅管理员可操作
    - 必选参数: name, description
    - 可选参数: major_dept_id (用于将小科室转移到其他大科室)
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 获取小科室
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == minor_dept_id))
        db_dept = result.scalar_one_or_none()
        if not db_dept:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="科室不存在",
                status_code=404
            )
        
        # 检查新名称是否与其他科室冲突
        if dept_data.name and dept_data.name != db_dept.name:
            result = await db.execute(select(MinorDepartment).where(
                and_(MinorDepartment.name == dept_data.name, MinorDepartment.minor_dept_id != minor_dept_id)
            ))
            if result.scalar_one_or_none():
                raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="科室名称已存在",
                    status_code=400
                )
        
        # 如果需要转移大科室
        if dept_data.major_dept_id is not None and dept_data.major_dept_id != db_dept.major_dept_id:
            # 检查目标大科室是否存在
            result = await db.execute(select(MajorDepartment).where(MajorDepartment.major_dept_id == dept_data.major_dept_id))
            if not result.scalar_one_or_none():
                raise ResourceHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="目标大科室不存在",
                    status_code=400
                )
            
            # 记录转移信息
            old_major_dept_id = db_dept.major_dept_id
            db_dept.major_dept_id = dept_data.major_dept_id
            logger.info(f"小科室 {db_dept.name} 从大科室 {old_major_dept_id} 转移至大科室 {dept_data.major_dept_id}")
        
        # 更新科室基本信息
        if dept_data.name:
            db_dept.name = dept_data.name
        if dept_data.description is not None:
            db_dept.description = dept_data.description
        
        db.add(db_dept)
        await db.commit()
        await db.refresh(db_dept)
        
        # 更新价格配置（如果提供了价格字段）
        if (dept_data.default_price_normal is not None or 
            dept_data.default_price_expert is not None or 
            dept_data.default_price_special is not None):
            await update_entity_prices(
                db=db,
                scope_type="MINOR_DEPT",
                scope_id=minor_dept_id,
                default_price_normal=dept_data.default_price_normal,
                default_price_expert=dept_data.default_price_expert,
                default_price_special=dept_data.default_price_special
            )
        
        logger.info(f"更新小科室成功: {db_dept.name}")

        # 获取价格配置
        prices = await get_entity_prices(
            db=db,
            scope_type="MINOR_DEPT",
            scope_id=minor_dept_id
        )
        
        response_message = {
            "minor_dept_id": db_dept.minor_dept_id,
            "major_dept_id": db_dept.major_dept_id,
            "name": db_dept.name,
            "description": db_dept.description,
            "default_price_normal": prices["default_price_normal"],
            "default_price_expert": prices["default_price_expert"],
            "default_price_special": prices["default_price_special"]
        }
        
        # 如果发生了科室转移，添加转移相关信息
        if 'old_major_dept_id' in locals():
            response_message["transfer_info"] = {
                "old_major_dept_id": old_major_dept_id,
                "new_major_dept_id": db_dept.major_dept_id
            }
        
        return ResponseModel(code=0, message=response_message)
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新小科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.delete("/minor-departments/{minor_dept_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
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
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="科室不存在",
                status_code=404
            )

        # 检查是否有医生关联
        result = await db.execute(select(Doctor).where(Doctor.dept_id == minor_dept_id))
        if result.scalar_one_or_none():
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="存在关联医生，无法删除",
                status_code=400
            )

        # 删除关联的价格配置（如果存在）
        try:
            result = await db.execute(
                select(SystemConfig).where(
                    and_(
                        SystemConfig.scope_type == "MINOR_DEPT",
                        SystemConfig.scope_id == minor_dept_id,
                        SystemConfig.config_key == "registration.price"
                    )
                )
            )
            price_config = result.scalar_one_or_none()
            if price_config:
                await db.delete(price_config)
                logger.info(f"删除小科室 {db_dept.name} 的价格配置")
        except Exception as e:
            logger.warning(f"删除小科室价格配置时发生异常: {str(e)}")
        
        # 删除小科室
        await db.delete(db_dept)
        await db.commit()

        logger.info(f"删除小科室成功: {db_dept.name}")
        return ResponseModel(code=0, message={"detail": f"成功删除小科室 {db_dept.name}"})
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除小科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/minor-departments", response_model=ResponseModel[Union[MinorDepartmentListResponse, AuthErrorResponse]])
async def get_minor_departments(
    major_dept_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取小科室列表 - 仅管理员可操作，可按大科室过滤，支持分页"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        # 构建查询条件并获取小科室列表
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

        # 批量获取所有小科室的价格配置，避免 N+1 查询
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
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取小科室列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ====== 管理员医生管理接口 ======

# 医生管理
@router.post("/doctors", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
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
        # 基本校验：小科室必须存在
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == doctor_data.dept_id))
        if not result.scalar_one_or_none():
                raise ResourceHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="小科室不存在",
                status_code=400
            )

        # 工号与密码为可选，但必须同时提供或都不提供
        identifier = getattr(doctor_data, "identifier", None)
        password = getattr(doctor_data, "password", None)
        if (identifier and not password) or (password and not identifier):
                raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="工号和密码必须同时提供或都不提供",
                status_code=400
            )

        # 如果提供了工号（同时也会有密码），仅检查该工号在 User 表是否已存在，
        # 但不在此接口创建用户账号（账号创建通过 /doctors/{id}/create-account 进行）
        if identifier:
            result = await db.execute(select(User).where(User.identifier == identifier))
            if result.scalar_one_or_none():
                 raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="工号已被使用",
                    status_code=400
                )

        # 创建医生信息（先创建医生档案）
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

        # 如果提供了价格配置，则创建价格记录
        if (doctor_data.default_price_normal is not None or 
            doctor_data.default_price_expert is not None or 
            doctor_data.default_price_special is not None):
            await update_entity_prices(
                db=db,
                scope_type="DOCTOR",
                scope_id=db_doctor.doctor_id,
                default_price_normal=doctor_data.default_price_normal,
                default_price_expert=doctor_data.default_price_expert,
                default_price_special=doctor_data.default_price_special
            )

        logger.info(f"创建医生信息成功: {doctor_data.name}")

        # 获取价格配置
        prices = await get_entity_prices(
            db=db,
            scope_type="DOCTOR",
            scope_id=db_doctor.doctor_id
        )
        
        response_payload = {
            "doctor_id": db_doctor.doctor_id,
            "dept_id": db_doctor.dept_id,
            "name": db_doctor.name,
            "title": db_doctor.title,
            "specialty": db_doctor.specialty,
            "introduction": db_doctor.introduction,
            "default_price_normal": prices["default_price_normal"],
            "default_price_expert": prices["default_price_expert"],
            "default_price_special": prices["default_price_special"]
        }

        # 如果请求体中包含工号和密码，则在此一并创建用户账号并关联
        if identifier:
            try:
                # 可选的额外账号字段
                email = getattr(doctor_data, "email", None)
                phonenumber = getattr(doctor_data, "phonenumber", None)

                # 检查邮箱/手机号是否被占用（若有提供）
                if email:
                    r = await db.execute(select(User).where(User.email == email))
                    if r.scalar_one_or_none():
                            raise BusinessHTTPException(
                            code=settings.REQ_ERROR_CODE,
                            msg="邮箱已被使用",
                            status_code=400
                        )
                if phonenumber:
                    r = await db.execute(select(User).where(User.phonenumber == phonenumber))
                    if r.scalar_one_or_none():
                            raise BusinessHTTPException(
                            code=settings.REQ_ERROR_CODE,
                            msg="手机号已被使用",
                            status_code=400
                        )

                # 创建用户账号并关联
                hashed_password = get_hash_pwd(password)
                db_user = User(
                    identifier=identifier,
                    email=email,
                    phonenumber=phonenumber,
                    hashed_password=hashed_password,
                    user_type="doctor",
                    is_admin=False,
                    is_verified=True
                )
                db.add(db_user)
                await db.commit()
                await db.refresh(db_user)

                # 关联医生记录
                db_doctor.user_id = db_user.user_id
                db.add(db_doctor)
                await db.commit()
                await db.refresh(db_doctor)

                response_payload["account_provided"] = True
                response_payload["user_id"] = db_user.user_id
                response_payload["account_note"] = "已为医生创建并关联登录账号。"
            except AuthHTTPException:
                # 已经抛出的业务异常，回滚并抛出
                await db.rollback()
                raise
            except Exception as ex:
                await db.rollback()
                logger.error(f"为医生创建账号时发生异常: {str(ex)}")
                raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="创建医生账号失败",
                    status_code=500
                )
        else:
            response_payload["account_provided"] = False

        return ResponseModel(code=0, message=response_payload)
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"创建医生信息时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/doctors", response_model=ResponseModel[Union[DoctorListResponse, AuthErrorResponse]])
async def get_doctors(
    dept_id: Optional[int] = None,
    name: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取医生列表 - 仅管理员可操作，可按科室过滤和姓名模糊搜索，支持分页"""
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
        
        # 预取所有关联的 user（避免循环中多次查询）
        user_ids = [d.user_id for d in doctors if d.user_id]
        users_map = {}
        if user_ids:
            res_users = await db.execute(select(User).where(User.user_id.in_(user_ids)))
            users = res_users.scalars().all()
            users_map = {u.user_id: u for u in users}

        # 批量获取价格，避免循环内 await 造成 N+1 查询
        prices_map = await bulk_get_doctor_prices(db, doctors)

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
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取医生列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/doctors/{doctor_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
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
              raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )
        
        # 如果更新科室，检查新科室是否存在
        if doctor_data.dept_id and doctor_data.dept_id != db_doctor.dept_id:
            result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == doctor_data.dept_id))
            if not result.scalar_one_or_none():
                raise ResourceHTTPException(
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

        # 更新价格配置（如果提供了价格字段）
        if (doctor_data.default_price_normal is not None or 
            doctor_data.default_price_expert is not None or 
            doctor_data.default_price_special is not None):
            await update_entity_prices(
                db=db,
                scope_type="DOCTOR",
                scope_id=doctor_id,
                default_price_normal=doctor_data.default_price_normal,
                default_price_expert=doctor_data.default_price_expert,
                default_price_special=doctor_data.default_price_special
            )

        logger.info(f"更新医生信息成功: {db_doctor.name}")

        # 获取价格配置
        prices = await get_entity_prices(
            db=db,
            scope_type="DOCTOR",
            scope_id=doctor_id
        )
        
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
                "default_price_normal": prices["default_price_normal"],
                "default_price_expert": prices["default_price_expert"],
                "default_price_special": prices["default_price_special"]
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新医生信息时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.delete("/doctors/{doctor_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
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
              raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        # 如果医生有关联的用户账号，进行懒删除（软删除）并移除关联
        if db_doctor.user_id:
            # 查找用户并标记为已删除，同时置为不可用
            result = await db.execute(select(User).where(User.user_id == db_doctor.user_id))
            db_user = result.scalar_one_or_none()
            if db_user:
                try:
                    db_user.is_deleted = True
                    db_user.is_active = False
                    # 可选：清除登录相关信息
                    db_user.last_login_ip = None
                    db_user.last_login_time = None
                    db.add(db_user)
                    await db.commit()
                except Exception as ex:
                    await db.rollback()
                    logger.error(f"软删除用户时发生异常: {ex}")
                    raise BusinessHTTPException(
                        code=settings.REQ_ERROR_CODE,
                        msg="删除医生关联账号失败",
                        status_code=500
                    )

                # 清除 Redis 中的 token 映射，防止已删除用户继续使用旧 token
                try:
                    token = await redis.get(f"user_token:{db_user.user_id}")
                    if token:
                        await redis.delete(f"token:{token}")
                        await redis.delete(f"user_token:{db_user.user_id}")
                except Exception as rex:
                    logger.warning(f"删除用户 token 时 Redis 操作失败: {rex}")

            # 解除医生记录中的关联
            db_doctor.user_id = None

        # 删除关联的价格配置（如果存在）
        try:
            result = await db.execute(
                select(SystemConfig).where(
                    and_(
                        SystemConfig.scope_type == "DOCTOR",
                        SystemConfig.scope_id == doctor_id,
                        SystemConfig.config_key == "registration.price"
                    )
                )
            )
            price_config = result.scalar_one_or_none()
            if price_config:
                await db.delete(price_config)
                logger.info(f"删除医生 {db_doctor.name} 的价格配置")
        except Exception as e:
            logger.warning(f"删除医生价格配置时发生异常: {str(e)}")
        
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
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除医生信息时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# 医生调科室
@router.put("/doctors/{doctor_id}/transfer", response_model=ResponseModel[Union[DoctorTransferResponse, AuthErrorResponse]])
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
                raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        # 检查目标科室是否存在
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == transfer_data.new_dept_id))
        if not result.scalar_one_or_none():
                raise ResourceHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="目标科室不存在",
                status_code=400
            )

        # 记录原科室ID
        old_dept_id = db_doctor.dept_id

        # 更新医生科室
        db_doctor.dept_id = transfer_data.new_dept_id
        # 若该医生当前为科室长，调科室时自动取消其科室长身份（is_department_head 为 Integer: 1=是）
        if getattr(db_doctor, "is_department_head", None) == 1:
            db_doctor.is_department_head = 0
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
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"医生调科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )




@router.post("/departments/{dept_id}/heads/select", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def select_department_head(
    dept_id: int,
    doctor_id: int = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """选择某医生为该科室的科室长（仅管理员）。

    规则：
    - 医生必须属于该科室。
    - 医生职称必须为主任/副主任（title 字段包含“主任”）。
    - 科室长数量不得超过分级配置的最大值（config_key: departmentHeadMaxCount，MINOR_DEPT 优先，回退 GLOBAL）。
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="无权限，仅管理员可操作", status_code=403)

        # 校验科室存在
        dept = await db.get(MinorDepartment, dept_id)
        if not dept:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="科室不存在", status_code=404)

        # 校验医生存在与归属
        doctor = await db.get(Doctor, doctor_id)
        if not doctor:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="医生不存在", status_code=404)
        if doctor.dept_id != dept_id:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="医生不属于该科室", status_code=400)

        # 职称校验（主任/副主任）
        title = (doctor.title or "").strip()
        if "主任" not in title:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="仅主任/副主任可设为科室长", status_code=400)

        # 读取最大科室长数量配置（分级：MINOR_DEPT -> GLOBAL）
        dept_head_config = await get_department_head_config(
            db,
            scope_type="MINOR_DEPT",
            scope_id=dept_id
        )
        max_count = dept_head_config["maxCount"]

        # 当前科室长数量
        res = await db.execute(select(Doctor).where(and_(Doctor.dept_id == dept_id, Doctor.is_department_head == 1)))
        current_heads = res.scalars().all()
        logger.info(f"[科室长选择] 科室={dept_id}, 当前数量={len(current_heads)}, 最大值={max_count}, 医生={doctor_id}, is_head={getattr(doctor, 'is_department_head', None)}")
        # 检查该医生是否已是科室长（is_department_head 为 Integer: 1=是, 0/None=否）
        is_already_head = getattr(doctor, "is_department_head", None) == 1
        if len(current_heads) >= max_count and not is_already_head:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"科室长数量已达上限({max_count})", status_code=400)

        # 更新医生为科室长
        doctor.is_department_head = 1
        db.add(doctor)
        await db.commit()
        await db.refresh(doctor)

        return ResponseModel(code=0, message={
            "dept_id": dept_id,
            "doctor_id": doctor_id,
            "is_department_head": True,
            "max_heads": max_count
        })
    except AuthHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"设置科室长时发生异常: {e}")
        raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="内部服务异常", status_code=500)


@router.delete("/departments/{dept_id}/heads/{doctor_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def remove_department_head(
    dept_id: int,
    doctor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """取消某医生的科室长身份（仅管理员）。

    要求：医生属于该科室；若本就不是科室长则返回提示但不报错。
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="无权限，仅管理员可操作", status_code=403)

        # 校验科室存在
        dept = await db.get(MinorDepartment, dept_id)
        if not dept:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="科室不存在", status_code=404)

        # 校验医生存在与归属
        doctor = await db.get(Doctor, doctor_id)
        if not doctor:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="医生不存在", status_code=404)
        if doctor.dept_id != dept_id:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="医生不属于该科室", status_code=400)

        # is_department_head 为 Integer: 1=是, 0/None=否
        already = getattr(doctor, "is_department_head", None) == 1
        doctor.is_department_head = 0
        db.add(doctor)
        await db.commit()
        await db.refresh(doctor)

        return ResponseModel(code=0, message={
            "dept_id": dept_id,
            "doctor_id": doctor_id,
            "was_department_head": already,
            "is_department_head": False
        })
    except AuthHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"取消科室长时发生异常: {e}")
        raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="内部服务异常", status_code=500)


@router.post("/doctors/{doctor_id}/photo", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_doctor_photo(
    doctor_id: int,
    photo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """更新医生照片 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 获取医生信息
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        # 验证文件类型
        content_type = photo.content_type.lower()
        if not content_type.startswith('image/'):
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="只允许上传图片文件",
                status_code=400
            )

        # 生成文件名（使用时间戳确保唯一性）
        timestamp = int(time.time() * 1000)  # 毫秒级时间戳
        file_extension = os.path.splitext(photo.filename)[1].lower()
        if not file_extension in ['.jpg', '.jpeg', '.png', '.gif']:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="不支持的图片格式",
                status_code=400
            )

        new_filename = f"doctor_{doctor_id}_{timestamp}{file_extension}"
        save_path = os.path.join("app", "static", "images", "doctor", new_filename)
        url_path = f"/static/images/doctor/{new_filename}"

        # 保存新文件
        try:
            async with aiofiles.open(save_path, 'wb') as out_file:
                content = await photo.read()
                await out_file.write(content)
        except Exception as e:
            logger.error(f"保存医生照片时发生异常: {str(e)}")
            raise ResourceHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="保存图片失败",
                status_code=500
            )

        # 更新数据库中的图片路径
        old_photo_path = db_doctor.photo_path
        db_doctor.photo_path = url_path
        db_doctor.original_photo_url = None  # 清除可能存在的外部图片URL
        db.add(db_doctor)
        await db.commit()
        await db.refresh(db_doctor)

        logger.info(f"更新医生照片成功: {db_doctor.name}, 新照片路径: {url_path}")

        return ResponseModel(
            code=0,
            message={
                "detail": f"成功更新医生 {db_doctor.name} 的照片",
                "doctor_id": doctor_id,
                "new_photo_path": url_path,
                "old_photo_path": old_photo_path
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新医生照片时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.delete("/doctors/{doctor_id}/photo", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def delete_doctor_photo(
    doctor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """删除医生照片（仅清除引用） - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 获取医生信息
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        # 检查是否有照片
        if not db_doctor.photo_path and not db_doctor.original_photo_url:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="医生当前没有照片",
                status_code=400
            )

        # 记录旧的照片路径（用于日志）
        old_photo_path = db_doctor.photo_path or db_doctor.original_photo_url

        # 清除照片引用
        db_doctor.photo_path = None
        db_doctor.original_photo_url = None
        db.add(db_doctor)
        await db.commit()
        await db.refresh(db_doctor)

        logger.info(f"删除医生照片成功: {db_doctor.name}, 原照片路径: {old_photo_path}")

        return ResponseModel(
            code=0,
            message={
                "detail": f"成功删除医生 {db_doctor.name} 的照片",
                "doctor_id": doctor_id,
                "old_photo_path": old_photo_path
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除医生照片时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )

# ====== 医生照片二进制获取 ======
@router.get("/doctors/{doctor_id}/photo")
async def get_doctor_photo_raw(
    doctor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """根据医生ID返回照片二进制数据（仅管理员）。

    - 优先读取 `Doctor.photo_path` 指向的本地文件，例如 `/static/image/xxx.jpg`
    - 如果不存在或文件缺失，则返回 404
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        if not db_doctor.photo_path:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="该医生暂无本地照片",
                status_code=404
            )

        # 解析本地文件系统路径（始终使用相对 app 目录的路径）
        base_dir = os.path.dirname(os.path.dirname(__file__))  # .../app
        rel_path = db_doctor.photo_path.lstrip("/")  # e.g. static/image/xxx.jpg 或 app/static/image/xxx.jpg
        if rel_path.startswith("app/"):
            # 归一化去掉前缀 app/
            rel_path = rel_path[4:]
        fs_path = os.path.normpath(os.path.join(base_dir, rel_path))  # app/<rel_path>

        if not os.path.exists(fs_path) or os.path.isdir(fs_path):
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生照片文件不存在",
                status_code=404
            )

        mime_type, _ = mimetypes.guess_type(fs_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        def file_iterator(path: str, chunk_size: int = 8192):
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        return StreamingResponse(file_iterator(fs_path), media_type=mime_type)
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取医生照片数据时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )

# 为医生创建/更新账号
@router.post("/doctors/{doctor_id}/create-account", response_model=ResponseModel[Union[DoctorAccountCreateResponse, AuthErrorResponse]])
async def create_or_update_doctor_account(
    doctor_id: int,
    account_data: DoctorAccountCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """为医生创建或更新账号 - 仅管理员可操作
    
    - 如果医生没有账号，则创建新账号
    - 如果医生已有账号，则更新密码和其他信息
    """
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
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        existing_user = None
        if db_doctor.user_id:
            # 如果医生已有账号，获取用户信息
            result = await db.execute(select(User).where(User.user_id == db_doctor.user_id))
            existing_user = result.scalar_one_or_none()

        # 检查工号唯一性（跳过医生自己的账号）
        result = await db.execute(
            select(User).where(
                and_(
                    User.identifier == account_data.identifier,
                    User.user_id != (existing_user.user_id if existing_user else None)
                )
            )
        )
        if result.scalar_one_or_none():
                raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="工号已被其他用户使用",
                status_code=400
            )

        # 检查邮箱唯一性（如果提供了邮箱）
        if account_data.email:
            result = await db.execute(
                select(User).where(
                    and_(
                        User.email == account_data.email,
                        User.user_id != (existing_user.user_id if existing_user else None)
                    )
                )
            )
            if result.scalar_one_or_none():
                    raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="邮箱已被其他用户使用",
                    status_code=400
                )

        # 检查手机号唯一性（如果提供了手机号）
        if account_data.phonenumber:
            result = await db.execute(
                select(User).where(
                    and_(
                        User.phonenumber == account_data.phonenumber,
                        User.user_id != (existing_user.user_id if existing_user else None)
                    )
                )
            )
            if result.scalar_one_or_none():
                    raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="手机号已被其他用户使用",
                    status_code=400
                )

        hashed_password = get_hash_pwd(account_data.password)
        operation_type = "更新" if existing_user else "创建"

        if existing_user:
            # 更新现有账号
            existing_user.identifier = account_data.identifier
            existing_user.hashed_password = hashed_password
            if account_data.email is not None:
                existing_user.email = account_data.email
            if account_data.phonenumber is not None:
                existing_user.phonenumber = account_data.phonenumber
            
            db.add(existing_user)
            await db.commit()
            await db.refresh(existing_user)
            user_id = existing_user.user_id
        else:
            # 创建新账号
            new_user = User(
                identifier=account_data.identifier,
                email=account_data.email,
                phonenumber=account_data.phonenumber,
                hashed_password=hashed_password,
                user_type="doctor",  # 设置为医生类型
                is_admin=False,      # 医生不是管理员
                is_verified=True     # 管理员创建的账号直接验证
            )
            db.add(new_user)
            await db.commit()
            await db.refresh(new_user)

            # 更新医生信息，关联用户账号
            db_doctor.user_id = new_user.user_id
            db.add(db_doctor)
            await db.commit()
            user_id = new_user.user_id

        logger.info(f"为医生{operation_type}账号成功: {db_doctor.name} (工号: {account_data.identifier})")

        return ResponseModel(
            code=0,
            message=DoctorAccountCreateResponse(
                detail=f"成功为医生 {db_doctor.name} {operation_type}登录账号",
                user_id=user_id,
                doctor_id=doctor_id
            )
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"为医生{operation_type}账号时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ====== 患者查询接口 ======

@router.get("/patients", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def get_patients(
    name: Optional[str] = None,
    phone: Optional[str] = None,
    patient_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """患者查询接口 - 仅管理员可操作
    
    支持按姓名（模糊搜索）、手机号（模糊搜索）、患者ID（精确搜索）过滤
    """
    try:
        if not getattr(current_user, "is_admin", False):
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="仅管理员可访问"
            )
        
        # 导入Patient模型
        from app.models.patient import Patient
        
        # 构建查询，关联User表获取手机号
        stmt = select(Patient, User).join(
            User, Patient.user_id == User.user_id
        )
        
        # 添加过滤条件
        if patient_id is not None:
            stmt = stmt.where(Patient.patient_id == patient_id)
        if name:
            stmt = stmt.where(Patient.name.like(f"%{name}%"))
        if phone:
            stmt = stmt.where(User.phonenumber.like(f"%{phone}%"))
        
        result = await db.execute(stmt)
        rows = result.all()
        
        patients = []
        for patient, user in rows:
            # 计算年龄（如果有出生日期）
            age = None
            if patient.birth_date:
                from datetime import date as date_type
                today = date_type.today()
                age = today.year - patient.birth_date.year
                if (today.month, today.day) < (patient.birth_date.month, patient.birth_date.day):
                    age -= 1
            
            patients.append({
                "patient_id": patient.patient_id,
                "name": patient.name,
                "phone": user.phonenumber,
                "gender": patient.gender.value if patient.gender else "未知",
                "age": age,
                "id_card": patient.student_id  # 使用student_id作为身份证号/学号工号
            })
        
        return ResponseModel(code=0, message={"patients": patients})
        
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"查询患者失败: {e}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg=f"查询患者失败: {str(e)}"
        )


# ====== 院区管理接口 ======

@router.get("/hospital-areas", response_model=ResponseModel[Union[HospitalAreaListResponse, AuthErrorResponse]])
async def get_hospital_areas(
    area_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取院区列表 - 仅管理员可操作
    
    参数:
    - area_id: 可选，指定院区ID则返回该院区信息，不传则返回全部院区
    """
    try:
        if not getattr(current_user, "is_admin", False):
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="仅管理员可访问"
            )
        
        # 构建查询
        stmt = select(HospitalArea)
        if area_id is not None:
            stmt = stmt.where(HospitalArea.area_id == area_id)
        
        result = await db.execute(stmt)
        areas = result.scalars().all()
        
        # 构建响应
        area_items = [
            HospitalAreaItem(
                area_id=area.area_id,
                name=area.name,
                destination=area.destination,
                create_time=area.create_time
            )
            for area in areas
        ]
        
        return ResponseModel(
            code=0,
            message=HospitalAreaListResponse(areas=area_items)
        )
        
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"查询院区失败: {e}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg=f"查询院区失败: {str(e)}"
        )


# ====== 门诊管理接口 ======

# ====== 排班爬虫导入接口 ======

@router.post("/crawler/schedules/run", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def run_full_crawler_pipeline(
    skip_crawl: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """完整爬虫流程：爬取医院排班 -> 合并数据 -> 导入数据库（一键执行）

    参数：
    - skip_crawl: 是否跳过爬虫步骤，直接使用已有的 all.json（默认 False）
    
    流程：
    1. 从 final/crawler_data.json 读取医生列表
    2. 并发爬取所有医生的排班数据（存储到 schedule/年份i周/ 目录）
    3. 合并所有 JSON 文件为 all.json
    4. 解析并导入到数据库（创建院区/门诊，匹配医生，插入/更新排班）
    
    响应：包含爬取统计、合并计数、导入统计
    """
    try:
        if not getattr(current_user, "is_admin", False):
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="仅管理员可访问"
            )
        
        result = await crawl_and_import_schedules(db, skip_crawl=skip_crawl)
        return ResponseModel(code=0, message=result)
        
    except AuthHTTPException:
        raise
    except BusinessHTTPException as be:
        raise be
    except Exception as e:
        logger.error(f"完整爬虫流程失败: {e}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg=f"完整爬虫流程失败: {str(e)}"
        )


@router.get("/clinics", response_model=ResponseModel[Union[ClinicListResponse, AuthErrorResponse]])
async def get_clinics(
    dept_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取科室门诊列表 - 仅管理员可操作，可按小科室过滤，支持分页"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        filters = []
        if dept_id:
            filters.append(Clinic.minor_dept_id == dept_id)

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

        # 批量获取所有门诊的价格配置，避免 N+1 查询
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

        return ResponseModel(code=0, message=ClinicListResponse(clinics=clinic_list))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取门诊列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.post("/clinics", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def create_clinic(
    clinic_data: ClinicCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """创建门诊 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 校验小科室存在
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == clinic_data.minor_dept_id))
        if not result.scalar_one_or_none():
            raise ResourceHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="小科室不存在",
                status_code=400
            )

        # 目前未提供院区选择，默认归属院区ID=1（后续支持可扩展）
        db_clinic = Clinic(
            area_id=1,
            name=clinic_data.name,
            address=clinic_data.address,
            minor_dept_id=clinic_data.minor_dept_id,
            clinic_type=clinic_data.clinic_type
        )
        db.add(db_clinic)
        await db.commit()
        await db.refresh(db_clinic)

        # 如果提供了价格配置，则创建价格记录
        if (clinic_data.default_price_normal is not None or 
            clinic_data.default_price_expert is not None or 
            clinic_data.default_price_special is not None):
            await update_entity_prices(
                db=db,
                scope_type="CLINIC",
                scope_id=db_clinic.clinic_id,
                default_price_normal=clinic_data.default_price_normal,
                default_price_expert=clinic_data.default_price_expert,
                default_price_special=clinic_data.default_price_special
            )

        logger.info(f"创建门诊成功: {db_clinic.name}")
        # 获取价格配置
        prices = await get_entity_prices(
            db=db,
            scope_type="CLINIC",
            scope_id=db_clinic.clinic_id
        )
        return ResponseModel(code=0, message={
            "clinic_id": db_clinic.clinic_id,
            "name": db_clinic.name,
            "address": db_clinic.address,
            "minor_dept_id": db_clinic.minor_dept_id,
            "clinic_type": db_clinic.clinic_type,
            "default_price_normal": prices["default_price_normal"],
            "default_price_expert": prices["default_price_expert"],
            "default_price_special": prices["default_price_special"],
            "detail": "门诊创建成功"
        })
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"创建门诊时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/clinics/{clinic_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_clinic(
    clinic_id: int,
    clinic_data: ClinicUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """更新门诊信息 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 获取门诊
        result = await db.execute(select(Clinic).where(Clinic.clinic_id == clinic_id))
        db_clinic = result.scalar_one_or_none()
        if not db_clinic:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="门诊不存在",
                status_code=404
            )

        # 更新门诊基本信息
        if clinic_data.name is not None:
            db_clinic.name = clinic_data.name
        if clinic_data.address is not None:
            db_clinic.address = clinic_data.address

        db.add(db_clinic)
        await db.commit()
        await db.refresh(db_clinic)

        # 更新价格配置（如果提供了价格字段）
        if (clinic_data.default_price_normal is not None or 
            clinic_data.default_price_expert is not None or 
            clinic_data.default_price_special is not None):
            await update_entity_prices(
                db=db,
                scope_type="CLINIC",
                scope_id=clinic_id,
                default_price_normal=clinic_data.default_price_normal,
                default_price_expert=clinic_data.default_price_expert,
                default_price_special=clinic_data.default_price_special
            )

        logger.info(f"更新门诊成功: {db_clinic.name}")
        # 获取价格配置
        prices = await get_entity_prices(
            db=db,
            scope_type="CLINIC",
            scope_id=clinic_id
        )
        return ResponseModel(code=0, message={
            "clinic_id": db_clinic.clinic_id,
            "name": db_clinic.name,
            "address": db_clinic.address,
            "minor_dept_id": db_clinic.minor_dept_id,
            "clinic_type": db_clinic.clinic_type,
            "default_price_normal": prices["default_price_normal"],
            "default_price_expert": prices["default_price_expert"],
            "default_price_special": prices["default_price_special"],
            "detail": "门诊信息更新成功"
        })
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新门诊时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ====== 排班管理接口 ======

@router.get("/departments/{dept_id}/schedules", response_model=ResponseModel[Union[ScheduleListResponse, AuthErrorResponse]])
async def get_department_schedules(
    dept_id: int,
    start_date: str,
    end_date: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取科室排班 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

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

        # 查询：该小科室下的门诊 -> 排班
        result = await db.execute(select(Clinic.clinic_id).where(Clinic.minor_dept_id == dept_id))
        clinic_ids = [row[0] for row in result.all()]
        if not clinic_ids:
            return ResponseModel(code=0, message=ScheduleListResponse(schedules=[]))

        from sqlalchemy import or_  # ensure imported
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
                "date": sch.date,
                "week_day": _weekday_to_cn(sch.week_day),
                "time_section": sch.time_section,
                "slot_type": _slot_type_to_str(sch.slot_type),
                "total_slots": sch.total_slots,
                "remaining_slots": sch.remaining_slots,
                "status": sch.status,
                "price": float(sch.price)
            })

        return ResponseModel(code=0, message=ScheduleListResponse(schedules=data))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取科室排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )

@router.get("/doctors/{doctor_id}/schedules", response_model=ResponseModel[Union[ScheduleListResponse, AuthErrorResponse]])
async def get_doctor_schedules(
    doctor_id: int,
    start_date: str,
    end_date: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取医生排班 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

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
                "date": sch.date,
                "week_day": _weekday_to_cn(sch.week_day),
                "time_section": sch.time_section,
                "slot_type": _slot_type_to_str(sch.slot_type),
                "total_slots": sch.total_slots,
                "remaining_slots": sch.remaining_slots,
                "status": sch.status,
                "price": float(sch.price)
            })

        return ResponseModel(code=0, message=ScheduleListResponse(schedules=data))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取医生排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/clinics/{clinic_id}/schedules", response_model=ResponseModel[Union[ScheduleListResponse, AuthErrorResponse]])
async def get_clinic_schedules(
    clinic_id: int,
    start_date: str,
    end_date: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取门诊排班 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

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
                "date": sch.date,
                "week_day": _weekday_to_cn(sch.week_day),
                "time_section": sch.time_section,
                "slot_type": _slot_type_to_str(sch.slot_type),
                "total_slots": sch.total_slots,
                "remaining_slots": sch.remaining_slots,
                "status": sch.status,
                "price": float(sch.price)
            })

        return ResponseModel(code=0, message=ScheduleListResponse(schedules=data))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取门诊排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/doctors/{doctor_id}/schedules/today", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def get_doctor_schedules_today(
    doctor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """查询医生当日排班 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限,仅管理员可操作",
                status_code=403
            )

        # 查询医生信息
        doctor_result = await db.execute(
            select(Doctor).where(Doctor.doctor_id == doctor_id)
        )
        doctor = doctor_result.scalar_one_or_none()
        if not doctor:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg=f"医生ID {doctor_id} 不存在"
            )

        # 获取当天日期
        today = datetime.now().date()

        # 查询当天排班
        stmt = select(Schedule, Clinic, MinorDepartment).join(
            Clinic, Schedule.clinic_id == Clinic.clinic_id
        ).join(
            MinorDepartment, Clinic.minor_dept_id == MinorDepartment.minor_dept_id
        ).where(
            and_(
                Schedule.doctor_id == doctor_id,
                Schedule.date == today
            )
        ).order_by(Schedule.time_section)

        result = await db.execute(stmt)
        rows = result.all()

        schedules = []
        for schedule, clinic, dept in rows:
            # 根据门诊类型确定可用号源类型
            # clinic_type: 0-普通门诊, 1-专家门诊(国疗), 2-特需门诊
            if clinic.clinic_type == 0:
                available_types = ["普通"]
            elif clinic.clinic_type == 1:
                available_types = ["普通", "专家"]
            else:  # clinic_type == 2
                available_types = ["普通", "专家", "特需"]

            schedules.append({
                "schedule_id": schedule.schedule_id,
                "doctor_id": doctor.doctor_id,
                "doctor_name": doctor.name,
                "department_id": dept.minor_dept_id,
                "department_name": dept.name,
                "clinic_type": "普通门诊" if clinic.clinic_type == 0 else ("专家门诊" if clinic.clinic_type == 1 else "特需门诊"),
                "date": str(schedule.date),
                "time_slot": schedule.time_section,
                "total_slots": schedule.total_slots,
                "remaining_slots": schedule.remaining_slots,
                "available_slot_types": available_types
            })

        logger.info(f"获取医生当日排班成功: doctor_id={doctor_id}, 共 {len(schedules)} 条")
        return ResponseModel(code=0, message={"schedules": schedules})

    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取医生当日排班失败: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.post("/schedules", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def create_schedule(
    schedule_data: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """创建排班 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 基本校验
        doctor_result = await db.execute(select(Doctor).where(Doctor.doctor_id == schedule_data.doctor_id))
        doctor = doctor_result.scalar_one_or_none()
        if not doctor:
            raise ResourceHTTPException(code=settings.REQ_ERROR_CODE, msg="医生不存在", status_code=400)
        
        clinic_result = await db.execute(select(Clinic).where(Clinic.clinic_id == schedule_data.clinic_id))
        clinic = clinic_result.scalar_one_or_none()
        if not clinic:
            raise ResourceHTTPException(code=settings.REQ_ERROR_CODE, msg="门诊不存在", status_code=400)

        # 检查该医生在同一日期和时间段是否已有排班
        conflict_result = await db.execute(
            select(Schedule).where(
                and_(
                    Schedule.doctor_id == schedule_data.doctor_id,
                    Schedule.date == schedule_data.schedule_date,
                    Schedule.time_section == schedule_data.time_section
                )
            )
        )
        existing_schedule = conflict_result.scalar_one_or_none()
        if existing_schedule:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg=f"该医生在 {schedule_data.schedule_date} {schedule_data.time_section} 已有排班(ID: {existing_schedule.schedule_id})",
                status_code=400
            )

        # 处理价格：如果 price <= 0，则使用分级价格查询
        final_price = schedule_data.price
        if schedule_data.price <= 0:
            # 分级查询：DOCTOR -> CLINIC -> MINOR_DEPT -> GLOBAL
            hierarchical_price = await get_hierarchical_price(
                db=db,
                slot_type=schedule_data.slot_type,
                doctor_id=schedule_data.doctor_id,
                clinic_id=schedule_data.clinic_id,
                minor_dept_id=doctor.dept_id  # 医生所属小科室
            )
            
            if hierarchical_price is not None:
                final_price = hierarchical_price
            else:
                # 如果分级查询未找到，使用默认价格
                default_prices = {"普通": 50.0, "专家": 100.0, "特需": 500.0}
                final_price = default_prices.get(schedule_data.slot_type, 50.0)
        
        # 计算 week_day (1-7)
        week_day = schedule_data.schedule_date.isoweekday()

        db_schedule = Schedule(
            doctor_id=schedule_data.doctor_id,
            clinic_id=schedule_data.clinic_id,
            date=schedule_data.schedule_date,
            week_day=week_day,
            time_section=schedule_data.time_section,
            slot_type=_str_to_slot_type(schedule_data.slot_type),
            total_slots=schedule_data.total_slots,
            remaining_slots=schedule_data.total_slots,
            status=schedule_data.status,
            price=final_price
        )
        db.add(db_schedule)
        await db.commit()
        await db.refresh(db_schedule)

        logger.info(f"创建排班成功: {db_schedule.schedule_id}")
        return ResponseModel(code=0, message={"schedule_id": db_schedule.schedule_id, "detail": "排班创建成功"})
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"创建排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/schedules/{schedule_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_schedule(
    schedule_id: int,
    schedule_data: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """更新排班 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        result = await db.execute(select(Schedule).where(Schedule.schedule_id == schedule_id))
        db_schedule = result.scalar_one_or_none()
        if not db_schedule:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="排班不存在", status_code=404)

        # 字段更新和校验
        if schedule_data.doctor_id is not None and schedule_data.doctor_id != db_schedule.doctor_id:
            if not (await db.execute(select(Doctor).where(Doctor.doctor_id == schedule_data.doctor_id))).scalar_one_or_none():
                raise ResourceHTTPException(code=settings.REQ_ERROR_CODE, msg="医生不存在", status_code=400)
            db_schedule.doctor_id = schedule_data.doctor_id

        if schedule_data.clinic_id is not None and schedule_data.clinic_id != db_schedule.clinic_id:
            if not (await db.execute(select(Clinic).where(Clinic.clinic_id == schedule_data.clinic_id))).scalar_one_or_none():
                raise ResourceHTTPException(code=settings.REQ_ERROR_CODE, msg="门诊不存在", status_code=400)
            db_schedule.clinic_id = schedule_data.clinic_id

        if schedule_data.schedule_date is not None:
            db_schedule.date = schedule_data.schedule_date
            db_schedule.week_day = schedule_data.schedule_date.isoweekday()

        if schedule_data.time_section is not None:
            db_schedule.time_section = schedule_data.time_section

        if schedule_data.slot_type is not None:
            db_schedule.slot_type = _str_to_slot_type(schedule_data.slot_type)

        if schedule_data.total_slots is not None:
            # 调整 remaining 时保持不为负
            delta = schedule_data.total_slots - db_schedule.total_slots
            db_schedule.total_slots = schedule_data.total_slots
            db_schedule.remaining_slots = max(0, db_schedule.remaining_slots + delta)

        if schedule_data.status is not None:
            db_schedule.status = schedule_data.status

        if schedule_data.price is not None:
            # 如果价格 <= 0，使用分级查询
            if schedule_data.price <= 0:
                # 获取当前排班的医生和诊室信息
                doctor_result = await db.execute(select(Doctor).where(Doctor.doctor_id == db_schedule.doctor_id))
                doctor = doctor_result.scalar_one_or_none()
                
                # 确定号源类型（如果更新了 slot_type 则使用新值，否则使用当前值）
                current_slot_type = _slot_type_to_str(db_schedule.slot_type)
                
                if doctor:
                    hierarchical_price = await get_hierarchical_price(
                        db=db,
                        slot_type=current_slot_type,
                        doctor_id=db_schedule.doctor_id,
                        clinic_id=db_schedule.clinic_id,
                        minor_dept_id=doctor.dept_id
                    )
                    
                    if hierarchical_price is not None:
                        db_schedule.price = hierarchical_price
                    else:
                        # 使用默认价格
                        default_prices = {"普通": 50.0, "专家": 100.0, "特需": 500.0}
                        db_schedule.price = default_prices.get(current_slot_type, 50.0)
            else:
                db_schedule.price = schedule_data.price

        db.add(db_schedule)
        await db.commit()
        await db.refresh(db_schedule)

        logger.info(f"更新排班成功: {db_schedule.schedule_id}")
        return ResponseModel(code=0, message={"detail": "排班更新成功"})
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.delete("/schedules/{schedule_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def delete_schedule(
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """删除排班 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        result = await db.execute(select(Schedule).where(Schedule.schedule_id == schedule_id))
        db_schedule = result.scalar_one_or_none()
        if not db_schedule:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="排班不存在", status_code=404)

        await db.delete(db_schedule)
        await db.commit()

        logger.info(f"删除排班成功: {schedule_id}")
        return ResponseModel(code=0, message={"detail": "排班删除成功"})
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )
        


# get_administrator_id and calculate_leave_days moved to `app.services.admin_helpers`

# ================================== 排班审核接口 ==================================

@router.get("/audit/schedule", response_model=ResponseModel[Union[ScheduleAuditListResponse, AuthErrorResponse]])
async def get_schedule_audits(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取所有排班审核列表 - 仅管理员可操作 (无分页)"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 多表查询: ScheduleAudit, MinorDepartment, Clinic, Doctor
        result = await db.execute(
            select(
                ScheduleAudit,
                MinorDepartment.name.label("department_name"),
                Clinic.name.label("clinic_name"),
                Doctor.name.label("submitter_name"),
            )
            .join(MinorDepartment, MinorDepartment.minor_dept_id == ScheduleAudit.minor_dept_id)
            .join(Clinic, Clinic.clinic_id == ScheduleAudit.clinic_id)
            .join(Doctor, Doctor.doctor_id == ScheduleAudit.submitter_doctor_id)
            .order_by(ScheduleAudit.submit_time.desc())
        )
        
        audit_list = []
        for audit, dept_name, clinic_name, submitter_name in result.all():
            audit_list.append(ScheduleAuditItem(
                id=audit.audit_id,
                department_id=audit.minor_dept_id,
                department_name=dept_name,
                clinic_id=audit.clinic_id,
                clinic_name=clinic_name,
                submitter_id=audit.submitter_doctor_id,
                submitter_name=submitter_name,
                submit_time=audit.submit_time,
                week_start=audit.week_start_date,
                week_end=audit.week_end_date,
                remark=audit.remark,
                status=audit.status,
                auditor_id=audit.auditor_user_id,
                audit_time=audit.audit_time,
                audit_remark=audit.audit_remark,
                # 假设 schedule_data_json 结构已符合 ScheduleAuditItem.schedule
                schedule=audit.schedule_data_json
            ))

        return ResponseModel(code=0, message=ScheduleAuditListResponse(audits=audit_list))
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取排班审核列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/audit/add-slot", response_model=ResponseModel[Union[AddSlotAuditListResponse, AuthErrorResponse]])
async def get_add_slot_audits(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取所有加号申请（无分页） - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅管理员可查看加号申请", status_code=403)

        # 关联查询医生和患者姓名
        result = await db.execute(
            select(AddSlotAudit, Doctor.name, Patient.name)
            .join(Doctor, Doctor.doctor_id == AddSlotAudit.doctor_id)
            .join(Patient, Patient.patient_id == AddSlotAudit.patient_id)
            .order_by(AddSlotAudit.submit_time.desc())
        )
        rows = result.all()

        audit_list = []
        for a, doctor_name, patient_name in rows:
            audit_list.append({
                "audit_id": a.audit_id,
                "schedule_id": a.schedule_id,
                "doctor_id": a.doctor_id,
                "doctor_name": doctor_name,
                "patient_id": a.patient_id,
                "patient_name": patient_name,
                "slot_type": a.slot_type,
                "reason": a.reason,
                "applicant_id": a.applicant_id,
                "submit_time": a.submit_time,
                "status": a.status,
                "auditor_user_id": a.auditor_user_id,
                "audit_time": a.audit_time,
                "audit_remark": a.audit_remark,
            })

        return ResponseModel(code=0, message=AddSlotAuditListResponse(audits=audit_list))
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取加号申请列表时发生异常: {str(e)}")
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="内部服务异常", status_code=500)


@router.get("/audit/schedule/{audit_id}", response_model=ResponseModel[Union[ScheduleAuditItem, AuthErrorResponse]])
async def get_schedule_audit_detail(
    audit_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取排班审核详情 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 多表查询: ScheduleAudit, MinorDepartment, Clinic, Doctor
        result = await db.execute(
            select(
                ScheduleAudit,
                MinorDepartment.name.label("department_name"),
                Clinic.name.label("clinic_name"),
                Doctor.name.label("submitter_name"),
            )
            .join(MinorDepartment, MinorDepartment.minor_dept_id == ScheduleAudit.minor_dept_id)
            .join(Clinic, Clinic.clinic_id == ScheduleAudit.clinic_id)
            .join(Doctor, Doctor.doctor_id == ScheduleAudit.submitter_doctor_id)
            .where(ScheduleAudit.audit_id == audit_id)
        )
        row = result.first()

        if not row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="排班申请不存在",
                status_code=404
            )
        
        audit, dept_name, clinic_name, submitter_name = row
        
        return ResponseModel(code=0, message=ScheduleAuditItem(
            id=audit.audit_id,
            department_id=audit.minor_dept_id,
            department_name=dept_name,
            clinic_id=audit.clinic_id,
            clinic_name=clinic_name,
            submitter_id=audit.submitter_doctor_id,
            submitter_name=submitter_name,
            submit_time=audit.submit_time,
            week_start=audit.week_start_date,
            week_end=audit.week_end_date,
            remark=audit.remark,
            status=audit.status,
            auditor_id=audit.auditor_user_id,
            audit_time=audit.audit_time,
            audit_remark=audit.audit_remark,
            schedule=audit.schedule_data_json
        ))
    except AuthHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取排班审核详情时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.post("/audit/schedule/{audit_id}/approve", response_model=ResponseModel[Union[AuditActionResponse, AuthErrorResponse]])
async def approve_schedule_audit(
    audit_id: int,
    data: AuditAction,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """通过排班审核 - 仅管理员可操作，并写入排班数据到 Schedule 表"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        db_audit = await db.get(ScheduleAudit, audit_id)
        if not db_audit:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="排班申请不存在", status_code=404)
        
        if db_audit.status != 'pending':
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"当前申请状态为 {db_audit.status}，无法重复审核", status_code=400)
        
        # 使用当前用户 User ID 作为审核人
        current_time = datetime.now()
        
        # 1. 更新审核表状态
        db_audit.status = 'approved'
        db_audit.auditor_user_id = current_user.user_id
        db_audit.audit_time = current_time
        db_audit.audit_remark = data.comment
        db.add(db_audit)
        
        # 2. 将排班数据写入 Schedule 表 (简化的 JSON 解析和插入逻辑)
        # 假设 schedule_data_json 是一个 7x3 的列表结构，且已包含必要的 time_section 和 slot_type 信息
        schedule_data = db_audit.schedule_data_json
        start_date = db_audit.week_start_date
        clinic_id = db_audit.clinic_id
        
        schedule_records = []
        # 假设 time_section 依次为 '上午', '下午', '晚上'
        time_sections = ['上午', '下午', '晚上']
        # 假设排班 JSON 数据中的 DoctorInfo 包含其他必要信息如 slot_type, total_slots, price，或者使用默认值
        # ⚠️ 注: 这里的 Schedule 表字段 (如 slot_type, total_slots, price) 缺失，需在实际模型中补充
        
        for day_index, day_schedule in enumerate(schedule_data):
            current_date = start_date + timedelta(days=day_index)
            week_day = current_date.isoweekday() # 1=Mon, 7=Sun
            for slot_index, slot_data in enumerate(day_schedule):
                if slot_data:
                    # 假设 slot_data 是 ScheduleDoctorInfo: {"doctorId": 1, "doctorName": "李医生"}
                    # 实际生产中，JSON 应该包含完整的排班信息（号源类型、数量、价格等）
                    
                    # 假设默认值，实际应从更详细的 JSON 结构中提取
                    doctor_id = slot_data.get('doctor_id')
                    
                    # 检查医生是否存在 (可选，但推荐)
                    if not (await db.get(Doctor, doctor_id)):
                        raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"排班数据中医生ID {doctor_id} 不存在", status_code=400)
                    
                    # 假设 Schedule 模型中需要这些字段 (您未提供 Schedule 模型，此处按常见结构填充)
                    new_schedule = Schedule(
                        doctor_id=doctor_id,
                        clinic_id=clinic_id,
                        date=current_date,
                        week_day=week_day,
                        time_section=time_sections[slot_index],
                        slot_type='普通', # 需根据实际业务逻辑确定
                        total_slots=50,  # 需根据实际业务逻辑确定
                        remaining_slots=50, # 需根据实际业务逻辑确定
                        price=10.00, # 需根据实际业务逻辑确定
                        status='normal'
                    )
                    schedule_records.append(new_schedule)

        db.add_all(schedule_records)
        await db.commit()
        await db.refresh(db_audit)

        logger.info(f"排班审核通过并写入排班记录: Audit ID {audit_id}")

        return ResponseModel(code=0, message=AuditActionResponse(
            audit_id=audit_id,
            status='approved',
            auditor_id=current_user.user_id,
            audit_time=current_time
        ))
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
        logger.error(f"通过排班审核时发生异常: {str(e)}")
        await db.rollback()
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常: 写入排班数据失败",
            status_code=500
        )


@router.post("/audit/schedule/{audit_id}/reject", response_model=ResponseModel[Union[AuditActionResponse, AuthErrorResponse]])
async def reject_schedule_audit(
    audit_id: int,
    data: AuditAction,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """拒绝排班审核 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        db_audit = await db.get(ScheduleAudit, audit_id)
        if not db_audit:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="排班申请不存在", status_code=404)
        
        if db_audit.status != 'pending':
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"当前申请状态为 {db_audit.status}，无法重复审核", status_code=400)
        
        auditor_admin_id = await get_administrator_id(db, current_user.user_id)
        current_time = datetime.now()

        # 更新审核表状态
        db_audit.status = 'rejected'
        db_audit.auditor_admin_id = auditor_admin_id
        db_audit.audit_time = current_time
        db_audit.audit_remark = data.comment
        db.add(db_audit)
        await db.commit()
        await db.refresh(db_audit)

        logger.info(f"排班审核拒绝: Audit ID {audit_id}")

        return ResponseModel(code=0, message=AuditActionResponse(
            audit_id=audit_id,
            status='rejected',
            auditor_id=auditor_admin_id,
            audit_time=current_time
        ))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"拒绝排班审核时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )

# ================================== 请假审核接口 ==================================

@router.get("/audit/leave", response_model=ResponseModel[Union[LeaveAuditListResponse, AuthErrorResponse]])
async def get_leave_audits(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取所有请假审核列表 - 仅管理员可操作 (无分页)"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 多表查询: LeaveAudit, Doctor, MinorDepartment
        result = await db.execute(
            select(
                LeaveAudit,
                Doctor.name.label("doctor_name"),
                Doctor.title.label("doctor_title"),
                MinorDepartment.name.label("department_name"),
            )
            .join(Doctor, Doctor.doctor_id == LeaveAudit.doctor_id)
            .join(MinorDepartment, MinorDepartment.minor_dept_id == Doctor.dept_id)
            .order_by(LeaveAudit.submit_time.desc())
        )
        
        audit_list = []
        for audit, doctor_name, doctor_title, dept_name in result.all():
            leave_days = calculate_leave_days(audit.leave_start_date, audit.leave_end_date)
            # 原因预览:截取前 50 个字符
            reason_preview = (audit.reason[:50] + '...') if len(audit.reason) > 50 else audit.reason
            
            # 统一附件为字符串路径列表
            attachments_list = []
            if audit.attachment_data_json and isinstance(audit.attachment_data_json, list):
                for item in audit.attachment_data_json:
                    if isinstance(item, str):
                        attachments_list.append(item)
                    elif isinstance(item, dict) and 'url' in item:
                        attachments_list.append(item['url'])
            
            audit_list.append(LeaveAuditItem(
                id=audit.audit_id,
                doctor_id=audit.doctor_id,
                doctor_name=doctor_name,
                doctor_title=doctor_title,
                department_name=dept_name,
                leave_start_date=audit.leave_start_date,
                leave_end_date=audit.leave_end_date,
                leave_days=leave_days,
                reason=audit.reason,
                reason_preview=reason_preview,
                attachments=attachments_list,
                submit_time=audit.submit_time,
                status=audit.status,
                auditor_id=audit.auditor_user_id,
                audit_time=audit.audit_time,
                audit_remark=audit.audit_remark
            ))

        return ResponseModel(code=0, message=LeaveAuditListResponse(audits=audit_list))
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取请假审核列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/audit/leave/{audit_id}", response_model=ResponseModel[Union[LeaveAuditItem, AuthErrorResponse]])
async def get_leave_audit_detail(
    audit_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取请假审核详情 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 多表查询: LeaveAudit, Doctor, MinorDepartment
        result = await db.execute(
            select(
                LeaveAudit,
                Doctor.name.label("doctor_name"),
                Doctor.title.label("doctor_title"),
                MinorDepartment.name.label("department_name"),
            )
            .join(Doctor, Doctor.doctor_id == LeaveAudit.doctor_id)
            .join(MinorDepartment, MinorDepartment.minor_dept_id == Doctor.dept_id)
            .where(LeaveAudit.audit_id == audit_id)
        )
        row = result.first()

        if not row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="请假申请不存在",
                status_code=404
            )
        
        audit, doctor_name, doctor_title, dept_name = row
        leave_days = calculate_leave_days(audit.leave_start_date, audit.leave_end_date)
        reason_preview = (audit.reason[:50] + '...') if len(audit.reason) > 50 else audit.reason
        
        return ResponseModel(code=0, message=LeaveAuditItem(
            id=audit.audit_id,
            doctor_id=audit.doctor_id,
            doctor_name=doctor_name,
            doctor_title=doctor_title,
            department_name=dept_name,
            leave_start_date=audit.leave_start_date,
            leave_end_date=audit.leave_end_date,
            leave_days=leave_days,
            reason=audit.reason,
            reason_preview=reason_preview,
            # 统一附件为字符串路径列表
            attachments=(
                [att if isinstance(att, str) else att.get('url') for att in (audit.attachment_data_json or [])
                 if isinstance(att, str) or (isinstance(att, dict) and att.get('url'))]
            ),
            submit_time=audit.submit_time,
            status=audit.status,
            auditor_id=audit.auditor_user_id,
            audit_time=audit.audit_time,
            audit_remark=audit.audit_remark
        ))
    except AuthHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取请假审核详情时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.post("/audit/leave/{audit_id}/approve", response_model=ResponseModel[Union[AuditActionResponse, AuthErrorResponse]])
async def approve_leave_audit(
    audit_id: int,
    data: AuditAction,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """通过请假审核 - 仅管理员可操作，将请假期间的排班标记为'停诊'状态"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        db_audit = await db.get(LeaveAudit, audit_id)
        if not db_audit:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="请假申请不存在", status_code=404)
        
        if db_audit.status != 'pending':
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"当前申请状态为 {db_audit.status}，无法重复审核", status_code=400)
        
        # 使用当前用户 User ID 作为审核人
        current_time = datetime.now()

        # 1. 将医生在请假期间的排班状态标记为"请假"（保留历史记录，便于追溯）
        from sqlalchemy import update
        update_stmt = (
            update(Schedule)
            .where(
                and_(
                    Schedule.doctor_id == db_audit.doctor_id,
                    Schedule.date >= db_audit.leave_start_date,
                    Schedule.date <= db_audit.leave_end_date,
                    Schedule.status.in_(["正常", "待审核"])  # 仅更新正常或待审核的排班
                )
            )
            .values(status="停诊")
        )
        result = await db.execute(update_stmt)
        affected_schedules = result.rowcount

        logger.info(f"请假审核通过，已将 {affected_schedules} 条排班标记为'停诊'状态。")
        
        # 2. 更新审核表状态
        db_audit.status = 'approved'
        db_audit.auditor_user_id = current_user.user_id
        db_audit.audit_time = current_time
        db_audit.audit_remark = data.comment
        db.add(db_audit)
        
        # 统一提交（排班更新 + 审核状态更新）
        await db.commit()
        await db.refresh(db_audit)

        logger.info(f"请假审核通过: Audit ID {audit_id}")

        return ResponseModel(code=0, message=AuditActionResponse(
            audit_id=audit_id,
            status='approved',
            auditor_id=current_user.user_id,
            audit_time=current_time
        ))
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
        logger.error(f"通过请假审核时发生异常: {str(e)}")
        await db.rollback()
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常: 更新排班状态或审核状态失败",
            status_code=500
        )


@router.post("/audit/leave/{audit_id}/reject", response_model=ResponseModel[Union[AuditActionResponse, AuthErrorResponse]])
async def reject_leave_audit(
    audit_id: int,
    data: AuditAction,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """拒绝请假审核 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        db_audit = await db.get(LeaveAudit, audit_id)
        if not db_audit:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="请假申请不存在", status_code=404)
        
        if db_audit.status != 'pending':
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"当前申请状态为 {db_audit.status}，无法重复审核", status_code=400)
        
        current_time = datetime.now()

        # 更新审核表状态
        db_audit.status = 'rejected'
        db_audit.auditor_user_id = current_user.user_id
        db_audit.audit_time = current_time
        db_audit.audit_remark = data.comment
        db.add(db_audit)
        await db.commit()
        await db.refresh(db_audit)

        logger.info(f"请假审核拒绝: Audit ID {audit_id}")

        return ResponseModel(code=0, message=AuditActionResponse(
            audit_id=audit_id,
            status='rejected',
            auditor_id=current_user.user_id,
            audit_time=current_time
        ))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"拒绝请假审核时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.post("/audit/add-slot/{audit_id}/approve", response_model=ResponseModel[Union[AuditActionResponse, AuthErrorResponse]])
async def approve_add_slot_audit(
    audit_id: int,
    data: AuditAction,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """管理员通过加号申请，将加号转换为挂号记录（在事务内）。"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 获取加号申请
        result = await db.execute(select(AddSlotAudit).where(AddSlotAudit.audit_id == audit_id))
        audit = result.scalar_one_or_none()
        if not audit:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="加号申请不存在", status_code=404)

        if audit.status != 'pending':
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"当前申请状态为 {audit.status}，无法重复审核", status_code=400)

        # 执行加号处理逻辑（会在内部创建挂号并更新排班）
        from app.services.add_slot_service import execute_add_slot_and_register

        order_id = await execute_add_slot_and_register(
            db=db,
            schedule_id=audit.schedule_id,
            patient_id=audit.patient_id,
            slot_type=audit.slot_type,
            applicant_user_id=audit.applicant_id
        )

        # 更新审核记录
        auditor_admin_id = await get_administrator_id(db, current_user.user_id)
        audit.status = 'approved'
        audit.auditor_admin_id = auditor_admin_id
        audit.audit_time = datetime.now()
        audit.audit_remark = data.comment
        db.add(audit)
        await db.commit()
        await db.refresh(audit)

        return ResponseModel(code=0, message=AuditActionResponse(
            audit_id=audit_id,
            status='approved',
            auditor_id=auditor_admin_id,
            audit_time=audit.audit_time
        ))
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
        logger.error(f"通过加号申请时发生异常: {e}")
        await db.rollback()
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常: 加号处理或更新审核状态失败",
            status_code=500
        )


@router.post("/audit/add-slot/{audit_id}/reject", response_model=ResponseModel[Union[AuditActionResponse, AuthErrorResponse]])
async def reject_add_slot_audit(
    audit_id: int,
    data: AuditAction,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """管理员拒绝加号申请"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        result = await db.execute(select(AddSlotAudit).where(AddSlotAudit.audit_id == audit_id))
        audit = result.scalar_one_or_none()
        if not audit:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="加号申请不存在", status_code=404)

        if audit.status != 'pending':
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"当前申请状态为 {audit.status}，无法重复审核", status_code=400)

        auditor_admin_id = await get_administrator_id(db, current_user.user_id)
        audit.status = 'rejected'
        audit.auditor_admin_id = auditor_admin_id
        audit.audit_time = datetime.now()
        audit.audit_remark = data.comment
        db.add(audit)
        await db.commit()
        await db.refresh(audit)

        return ResponseModel(code=0, message=AuditActionResponse(
            audit_id=audit_id,
            status='rejected',
            auditor_id=auditor_admin_id,
            audit_time=audit.audit_time
        ))
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
        logger.error(f"拒绝加号申请时发生异常: {e}")
        await db.rollback()
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常: 更新审核状态失败",
            status_code=500
        )


# ====== 通用附件路径二进制数据获取 ======
@router.get("/audit/attachment/raw", response_model=None)
async def get_attachment_raw_from_path(
    path: str,
    current_user: UserSchema = Depends(get_current_user)
):
    """根据附件的本地相对路径返回文件二进制数据（仅管理员）。

    用于获取请假申请等附件中的图片或文件内容。文件路径应存储在 LeaveAudit.attachment_data_json 中。
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 路径解析：基于应用根目录进行拼接，并进行规范化
        base_dir = os.path.dirname(os.path.dirname(__file__))  # 假设 /app 目录
        rel_path = path.lstrip("/") 
        
        # 归一化路径，防止路径中出现 ../ 等跳转
        fs_path = os.path.normpath(os.path.join(base_dir, rel_path))
        
        # 关键安全检查：确保文件路径在应用基础目录内，防止目录遍历攻击 (Directory Traversal)
        if not fs_path.startswith(os.path.normpath(base_dir)):
            logger.warning(f"检测到目录遍历尝试: {fs_path}")
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="提供的文件路径不安全或无效",
                status_code=400
            )

        # 检查文件是否存在
        if not os.path.exists(fs_path) or os.path.isdir(fs_path):
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="附件文件不存在或路径错误",
                status_code=404
            )

        # 猜测 MIME Type
        mime_type, _ = mimetypes.guess_type(fs_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        # 异步文件迭代器，适用于 StreamingResponse
        async def async_file_iterator(path: str, chunk_size: int = 8192):
            """异步读取文件块的生成器"""
            try:
                # 使用 aiofiles 确保文件 I/O 不阻塞主线程
                async with aiofiles.open(path, "rb") as f:
                    while True:
                        chunk = await f.read(chunk_size)
                        if not chunk:
                            break
                        yield chunk
            except Exception as e:
                logger.error(f"异步读取文件失败: {path}, 异常: {str(e)}")
                # 在流中抛出异常会导致连接中断，这里更倾向于记录错误

        logger.info(f"开始流式传输本地附件文件: {fs_path}")
        return StreamingResponse(async_file_iterator(fs_path), media_type=mime_type)

    except AuthHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取附件数据时发生未知异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ================================== 系统配置接口(全局) ==================================

@router.get("/config", response_model=ResponseModel[Union[SystemConfigResponse, AuthErrorResponse]])
async def get_system_config(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取系统配置 - 仅管理员可操作
    
    返回挂号配置(registration)和排班配置(schedule)的完整信息
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 使用配置服务获取配置
        registration_data = await get_registration_config(db)
        schedule_data = await get_schedule_config(db)

        logger.info(f"获取系统配置成功")
        
        return ResponseModel(
            code=0,
            message=SystemConfigResponse(
                registration=registration_data,
                schedule=schedule_data
            )
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取系统配置时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/config", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_system_config(
    config_data: SystemConfigRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """更新系统配置 - 仅管理员可操作
    
    可选择性更新挂号配置(registration)和/或排班配置(schedule)
    只需传递需要更新的字段即可
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 处理挂号配置更新
        if config_data.registration is not None:
            # 验证时间格式逻辑
            reg_dict = config_data.registration.dict(exclude_none=True)
            
            # 查询现有配置
            registration_result = await db.execute(
                select(SystemConfig).where(
                    and_(
                        SystemConfig.config_key == 'registration',
                        SystemConfig.scope_type == 'GLOBAL'
                    )
                )
            )
            registration_config = registration_result.scalar_one_or_none()


            if registration_config:
                logger.info(f"现有挂号配置: {registration_config.config_value}")
                # 更新现有配置（合并字段）
                current_value = registration_config.config_value or {}
                current_value.update(reg_dict)
                registration_config.config_value = current_value
                registration_config.update_time = datetime.now()
                # 关键：标记 JSON 字段已修改，确保 SQLAlchemy 追踪到变更
                flag_modified(registration_config, "config_value")
                db.add(registration_config)
            else:
                logger.info("无现有挂号配置，创建新配置")
                # 创建新配置
                new_config = SystemConfig(
                    config_key='registration',
                    scope_type='GLOBAL',
                    scope_id=None,
                    config_value=reg_dict,
                    data_type='JSON',
                    description='挂号配置：包含提前挂号天数、当日挂号截止时间、爽约次数限制、退号提前时间、同科室挂号间隔',
                    is_active=True
                )
                db.add(new_config)

            logger.info(f"更新挂号配置: {reg_dict}")

        # 处理排班配置更新
        if config_data.schedule is not None:
            logger.info(f"准备更新排班配置: {config_data.schedule}")
            # 验证时间段逻辑
            sch_dict = config_data.schedule.dict(exclude_none=True)
            
            # 逻辑验证：各时间段开始时间应小于结束时间
            if 'morningStart' in sch_dict and 'morningEnd' in sch_dict:
                if sch_dict['morningStart'] >= sch_dict['morningEnd']:
                    raise BusinessHTTPException(
                        code=settings.REQ_ERROR_CODE,
                        msg="上午班开始时间必须小于结束时间",
                        status_code=400
                    )
            
            if 'afternoonStart' in sch_dict and 'afternoonEnd' in sch_dict:
                if sch_dict['afternoonStart'] >= sch_dict['afternoonEnd']:
                    raise BusinessHTTPException(
                        code=settings.REQ_ERROR_CODE,
                        msg="下午班开始时间必须小于结束时间",
                        status_code=400
                    )
            
            if 'eveningStart' in sch_dict and 'eveningEnd' in sch_dict:
                if sch_dict['eveningStart'] >= sch_dict['eveningEnd']:
                    raise BusinessHTTPException(
                        code=settings.REQ_ERROR_CODE,
                        msg="晚班开始时间必须小于结束时间",
                        status_code=400
                    )

            # 查询现有配置
            schedule_result = await db.execute(
                select(SystemConfig).where(
                    and_(
                        SystemConfig.config_key == 'schedule',
                        SystemConfig.scope_type == 'GLOBAL'
                    )
                )
            )
            schedule_config = schedule_result.scalar_one_or_none()

            if schedule_config:
                # 更新现有配置（合并字段）
                current_value = schedule_config.config_value or {}
                current_value.update(sch_dict)
                schedule_config.config_value = current_value
                schedule_config.update_time = datetime.now()
                # 关键：标记 JSON 字段已修改，确保 SQLAlchemy 追踪到变更
                flag_modified(schedule_config, "config_value")
                db.add(schedule_config)
            else:
                # 创建新配置
                new_config = SystemConfig(
                    config_key='schedule',
                    scope_type='GLOBAL',
                    scope_id=None,
                    config_value=sch_dict,
                    data_type='JSON',
                    description='排班配置：包含最多排未来天数、上午/下午/晚班时间段、单次就诊时长、就诊间隔时间',
                    is_active=True
                )
                db.add(new_config)

            logger.info(f"更新排班配置: {sch_dict}")

        await db.commit()
        
        return ResponseModel(
            code=0,
            message={"detail": "配置更新成功"}
        )
    except AuthHTTPException:
        await db.rollback()
        raise
    except BusinessHTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"更新系统配置时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )

# ====== 全局价格配置接口 ======

@router.get("/global-prices", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def get_global_prices(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取全局挂号价格配置 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        prices = await get_entity_prices(db, "GLOBAL", None)
        
        return ResponseModel(
            code=0,
            message={
                "default_price_normal": prices["default_price_normal"],
                "default_price_expert": prices["default_price_expert"],
                "default_price_special": prices["default_price_special"]
            }
        )
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取全局价格配置时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/global-prices", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_global_prices(
    default_price_normal: Optional[float] = None,
    default_price_expert: Optional[float] = None,
    default_price_special: Optional[float] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """更新全局挂号价格配置 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 至少需要提供一个价格参数
        if all(p is None for p in [default_price_normal, default_price_expert, default_price_special]):
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="至少需要提供一个价格参数",
                status_code=400
            )

        # 更新全局价格配置
        await update_entity_prices(
            db=db,
            scope_type="GLOBAL",
            scope_id=None,
            default_price_normal=default_price_normal,
            default_price_expert=default_price_expert,
            default_price_special=default_price_special
        )

        logger.info("更新全局价格配置成功")
        return ResponseModel(code=0, message={"detail": "全局价格配置更新成功"})
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新全局价格配置时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ==================== 缺勤管理 API ====================

@router.post("/attendance/mark-absent/single", summary="手动标记单日缺勤")
async def mark_single_date_absent(
    target_date: date,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    手动触发单日缺勤标记（管理员）
    
    - **target_date**: 目标日期（格式 YYYY-MM-DD）
    - 自动检测该日所有无考勤记录的排班并标记为 ABSENT
    - 返回标记统计信息
    """
    try:
        # 管理员权限检查
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.AUTH_ERROR_CODE,
                msg="仅管理员可执行此操作",
                status_code=403
            )
        
        # 不能标记今天及未来的日期
        if target_date >= date.today():
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="只能标记历史日期的缺勤记录",
                status_code=400
            )
        
        stats = await mark_absent_for_date(db, target_date)
        
        logger.info(f"管理员 {current_user.user_id} 手动标记 {target_date} 缺勤: {stats}")
        return ResponseModel(
            code=0,
            message={
                "detail": "缺勤标记完成",
                "date": str(target_date),
                **stats
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"标记单日缺勤时发生异常: {str(e)}", exc_info=True)
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.post("/attendance/mark-absent/range", summary="批量标记日期范围缺勤")
async def mark_range_absent(
    start_date: date,
    end_date: date,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    手动触发批量缺勤标记（管理员）
    
    - **start_date**: 开始日期
    - **end_date**: 结束日期
    - 批量检测并标记日期范围内的缺勤记录
    - 返回每日统计明细
    """
    try:
        # 管理员权限检查
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.AUTH_ERROR_CODE,
                msg="仅管理员可执行此操作",
                status_code=403
            )
        
        # 不能标记今天及未来的日期
        if end_date >= date.today():
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="结束日期不能包含今天或未来日期",
                status_code=400
            )
        
        if start_date > end_date:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="开始日期不能晚于结束日期",
                status_code=400
            )
        
        # 防止批量操作过大
        date_diff = (end_date - start_date).days
        if date_diff > 90:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="单次批量操作不能超过 90 天",
                status_code=400
            )
        
        results = await mark_absent_for_date_range(db, start_date, end_date)
        
        total_marked = sum(r["absent_marked"] for r in results)
        logger.info(f"管理员 {current_user.user_id} 批量标记缺勤 {start_date} 至 {end_date}: 共标记 {total_marked} 条")
        
        return ResponseModel(
            code=0,
            message={
                "detail": "批量缺勤标记完成",
                "date_range": {
                    "start": str(start_date),
                    "end": str(end_date)
                },
                "total_marked": total_marked,
                "daily_statistics": results
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"批量标记缺勤时发生异常: {str(e)}", exc_info=True)
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/attendance/absent-statistics", summary="查询缺勤统计")
async def get_absence_statistics(
    start_date: date,
    end_date: date,
    doctor_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    查询缺勤统计（管理员）
    
    - **start_date**: 开始日期
    - **end_date**: 结束日期
    - **doctor_id**: 可选，指定医生ID（不指定则查询所有医生）
    - 返回缺勤记录列表、按医生汇总统计
    """
    try:
        # 管理员权限检查
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.AUTH_ERROR_CODE,
                msg="仅管理员可查看缺勤统计",
                status_code=403
            )
        
        if start_date > end_date:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="开始日期不能晚于结束日期",
                status_code=400
            )
        
        stats = await get_absent_statistics(db, start_date, end_date, doctor_id)
        
        logger.info(f"管理员 {current_user.user_id} 查询缺勤统计: {start_date} 至 {end_date}, doctor_id={doctor_id}")
        return ResponseModel(code=0, message=stats)
        
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"查询缺勤统计时发生异常: {str(e)}", exc_info=True)
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ==================== 11. 接诊配置管理 API ====================

@router.get("/consultation/config", summary="获取接诊配置")
async def get_consultation_config(
    scope_type: str = "GLOBAL",
    scope_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取接诊配置（管理员）
    
    - **scope_type**: 配置范围类型（GLOBAL=全局, DOCTOR=医生级别）
    - **scope_id**: 范围ID（GLOBAL时不需要，DOCTOR时传 doctor_id）
    - 返回过号次数上限等配置
    """
    try:
        # 管理员权限检查
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.AUTH_ERROR_CODE,
                msg="仅管理员可查看接诊配置",
                status_code=403
            )
        
        # 验证参数
        if scope_type not in ["GLOBAL", "DOCTOR"]:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="scope_type 必须是 GLOBAL 或 DOCTOR",
                status_code=400
            )
        
        if scope_type == "DOCTOR" and not scope_id:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="DOCTOR 类型必须提供 scope_id (doctor_id)",
                status_code=400
            )
        
        # 查询配置
        query = select(SystemConfig).where(
            SystemConfig.config_key == "consultation.max_pass_count",
            SystemConfig.scope_type == scope_type,
            SystemConfig.is_active == True
        )
        
        if scope_type == "GLOBAL":
            query = query.where(SystemConfig.scope_id.is_(None))
        else:
            query = query.where(SystemConfig.scope_id == scope_id)
        
        result = await db.execute(query)
        config = result.scalar_one_or_none()
        
        if not config:
            # 返回默认值
            return ResponseModel(
                code=0,
                message={
                    "maxPassCount": 3,
                    "source": "default",
                    "description": "默认值（未配置）"
                }
            )
        
        logger.info(f"管理员 {current_user.user_id} 查询接诊配置: {scope_type}:{scope_id}")
        return ResponseModel(
            code=0,
            message={
                "maxPassCount": int(config.config_value),
                "source": scope_type.lower(),
                "description": config.description,
                "updateTime": config.update_time.isoformat() if config.update_time else None
            }
        )
        
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"查询接诊配置时发生异常: {str(e)}", exc_info=True)
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/consultation/config", summary="更新接诊配置")
async def update_consultation_config(
    max_pass_count: int,
    scope_type: str = "GLOBAL",
    scope_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    更新接诊配置（管理员）
    
    - **max_pass_count**: 过号次数上限（1-10）
    - **scope_type**: 配置范围类型（GLOBAL=全局, DOCTOR=医生级别）
    - **scope_id**: 范围ID（GLOBAL时不需要，DOCTOR时传 doctor_id）
    """
    try:
        # 管理员权限检查
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.AUTH_ERROR_CODE,
                msg="仅管理员可修改接诊配置",
                status_code=403
            )
        
        # 验证参数
        if scope_type not in ["GLOBAL", "DOCTOR"]:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="scope_type 必须是 GLOBAL 或 DOCTOR",
                status_code=400
            )
        
        if scope_type == "DOCTOR" and not scope_id:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="DOCTOR 类型必须提供 scope_id (doctor_id)",
                status_code=400
            )
        
        if not 1 <= max_pass_count <= 10:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="过号次数上限必须在 1-10 之间",
                status_code=400
            )
        
        # 查询现有配置
        query = select(SystemConfig).where(
            SystemConfig.config_key == "consultation.max_pass_count",
            SystemConfig.scope_type == scope_type
        )
        
        if scope_type == "GLOBAL":
            query = query.where(SystemConfig.scope_id.is_(None))
        else:
            query = query.where(SystemConfig.scope_id == scope_id)
        
        result = await db.execute(query)
        config = result.scalar_one_or_none()
        
        if config:
            # 更新现有配置
            config.config_value = str(max_pass_count)
            config.update_time = datetime.utcnow()
        else:
            # 创建新配置
            config = SystemConfig(
                config_key="consultation.max_pass_count",
                scope_type=scope_type,
                scope_id=scope_id if scope_type == "DOCTOR" else None,
                config_value=str(max_pass_count),
                data_type="INT",
                description=f"接诊过号次数上限（{scope_type}）",
                is_active=True
            )
            db.add(config)
        
        await db.commit()
        
        logger.info(f"管理员 {current_user.user_id} 更新接诊配置: {scope_type}:{scope_id} = {max_pass_count}")
        return ResponseModel(
            code=0,
            message={
                "detail": "配置更新成功",
                "maxPassCount": max_pass_count,
                "scope": f"{scope_type}:{scope_id}" if scope_id else scope_type
            }
        )
        
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新接诊配置时发生异常: {str(e)}", exc_info=True)
        await db.rollback()
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )
