"""
接诊队列服务
提供医生工作台的叫号、过号、队列管理等功能
"""
from datetime import date, datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload

from app.models.registration_order import RegistrationOrder, OrderStatus
from app.models.patient import Patient
from app.models.schedule import Schedule
from app.core.config import settings
from app.core.exception_handler import BusinessHTTPException


async def get_consultation_queue(
    db: AsyncSession,
    schedule_id: int
) -> dict:
    """
    获取某次排班的接诊队列信息
    
    参数：
    - schedule_id: 排班ID（唯一标识某次出诊，如某天上午/下午/晚上）
    
    返回：
    - stats: 统计数据（总号源、候诊、已完成、过号等）
    - currentPatient: 当前正在就诊的患者
    - nextPatient: 下一位候诊患者
    - queue: 正式队列（CONFIRMED）
    - waitlist: 候补队列（WAITLIST）
    """
    # 验证排班是否存在
    schedule_query = await db.execute(
        select(Schedule).where(Schedule.schedule_id == schedule_id)
    )
    schedule = schedule_query.scalar_one_or_none()
    if not schedule:
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg=f"排班 {schedule_id} 不存在",
            status_code=404
        )
    
    # 1. 查询正式队列（CONFIRMED，按过号次数和创建时间排序）
    confirmed_query = await db.execute(
        select(RegistrationOrder)
        .options(selectinload(RegistrationOrder.patient))
        .where(
            and_(
                RegistrationOrder.schedule_id == schedule_id,
                RegistrationOrder.status == OrderStatus.CONFIRMED
            )
        )
        .order_by(
            RegistrationOrder.priority.asc(),
            RegistrationOrder.pass_count.asc(),
            RegistrationOrder.create_time.asc()
        )
    )
    confirmed_list = confirmed_query.scalars().all()
    
    # 2. 查询候补队列（WAITLIST）
    waitlist_query = await db.execute(
        select(RegistrationOrder)
        .options(selectinload(RegistrationOrder.patient))
        .where(
            and_(
                RegistrationOrder.schedule_id == schedule_id,
                RegistrationOrder.status == OrderStatus.WAITLIST
            )
        )
        .order_by(RegistrationOrder.create_time.asc())
    )
    waitlist_list = waitlist_query.scalars().all()
    
    # 3. 查询已完成数量
    completed_count_query = await db.execute(
        select(func.count(RegistrationOrder.order_id))
        .where(
            and_(
                RegistrationOrder.schedule_id == schedule_id,
                RegistrationOrder.status == OrderStatus.COMPLETED
            )
        )
    )
    completed_count = completed_count_query.scalar() or 0
    
    # 4. 动态生成队列号
    for idx, order in enumerate(confirmed_list, start=1):
        order.queue_number_display = f"A{idx:03d}"
    
    # 5. 筛选出当前患者和候诊队列
    current_patient = next((o for o in confirmed_list if o.is_calling), None)
    waiting_queue = [o for o in confirmed_list if not o.is_calling]
    
    # 6. 找到下一位
    next_patient = waiting_queue[0] if waiting_queue else None
    
    # 7. 统计数据
    # totalSlots 修改为实际订单总数：已确认 + 候补 + 已完成
    dynamic_total_slots = len(confirmed_list) + len(waitlist_list) + int(completed_count)
    stats = {
        "totalSlots": dynamic_total_slots,
        "confirmedCount": len(confirmed_list),
        "waitlistCount": len(waitlist_list),
        "completedCount": int(completed_count),
        "waitingCount": len(waiting_queue),
        "passedCount": len([o for o in confirmed_list if o.pass_count > 0])
    }
    
    return {
        "stats": stats,
        "scheduleInfo": {
            "scheduleId": schedule.schedule_id,
            "doctorId": schedule.doctor_id,
            "date": schedule.date.strftime('%Y-%m-%d'),
            "timeSection": schedule.time_section
        },
        "currentPatient": _format_patient_info(current_patient) if current_patient else None,
        "nextPatient": _format_patient_info(next_patient, minimal=True) if next_patient else None,
        "queue": [_format_patient_info(o) for o in waiting_queue],
        "waitlist": [_format_patient_info(o, is_waitlist=True) for o in waitlist_list]
    }


async def complete_current_patient(
    db: AsyncSession,
    patient_id: int,
    schedule_id: int,
    doctor_id: int
) -> dict:
    """
    完成当前患者就诊（患者到场并完成就诊）
    
    流程：
    1. 验证患者是否正在就诊（is_calling=True）
    2. 标记为已完成（status=COMPLETED）
    3. 记录就诊时间（visit_times）
    
    使用事务确保原子性
    """
    async with db.begin_nested():
        # 锁定并验证患者
        patient_query = await db.execute(
            select(RegistrationOrder)
            .options(selectinload(RegistrationOrder.patient))
            .where(
                RegistrationOrder.patient_id == patient_id,
                RegistrationOrder.schedule_id == schedule_id
            )
            .with_for_update()
        )
        patient = patient_query.scalar_one_or_none()
        
        if not patient:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg=f"未找到患者 {patient_id} 在排班 {schedule_id} 下的订单",
                status_code=404
            )
        
        if patient.status != OrderStatus.CONFIRMED:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg=f"只能完成已确认的订单，当前状态: {patient.status.value}",
                status_code=400
            )
        
        if not patient.is_calling:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="只能完成正在就诊的患者（is_calling=True）",
                status_code=400
            )
        
        # 由于已经通过 schedule_id 筛选，并且在 API 层已经验证了医生权限，这里不需要再次验证
        
        # 标记为已完成
        patient.status = OrderStatus.COMPLETED
        patient.is_calling = False
        if not patient.visit_times:
            patient.visit_times = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        await db.flush()
        
        return {
            "completedPatient": _format_patient_info(patient),
            "scheduleId": patient.schedule_id
        }


async def call_next_patient(
    db: AsyncSession,
    schedule_id: int
) -> dict:
    """
    呼叫下一位患者（针对某次排班）
    
    参数：
    - schedule_id: 排班ID
    
    流程：
    1. 从队列中选取下一位（CONFIRMED 且未叫号）
    2. 标记为正在就诊（is_calling=True）
    3. 记录叫号时间（call_time）
    
    使用事务和行锁确保并发安全
    """
    async with db.begin_nested():  # 嵌套事务
        # 选取下一位（正式队列中第一个未叫号的）
        next_query = await db.execute(
            select(RegistrationOrder)
            .options(selectinload(RegistrationOrder.patient))
            .where(
                and_(
                    RegistrationOrder.schedule_id == schedule_id,
                    RegistrationOrder.status == OrderStatus.CONFIRMED,
                    RegistrationOrder.is_calling == False
                )
            )
            .order_by(
                RegistrationOrder.priority.asc(),
                RegistrationOrder.pass_count.asc(),
                RegistrationOrder.create_time.asc()
            )
            .limit(1)
            .with_for_update(skip_locked=True)  # 跳过已锁定的行（并发安全）
        )
        next_patient = next_query.scalar_one_or_none()
        
        # 标记为正在就诊
        if next_patient:
            next_patient.is_calling = True
            next_patient.call_time = datetime.now()
        
        await db.flush()
        
        return {
            "nextPatient": _format_patient_info(next_patient) if next_patient else None,
            "scheduleId": schedule_id
        }


async def get_max_pass_count(db: AsyncSession, doctor_id: int = None) -> int:
    """
    获取过号次数上限配置
    
    优先级：医生配置 > 全局配置 > 默认值(3)
    """
    from app.models.system_config import SystemConfig
    
    # 1. 尝试获取医生级别配置
    if doctor_id:
        result = await db.execute(
            select(SystemConfig)
            .where(
                SystemConfig.scope_type == "DOCTOR",
                SystemConfig.scope_id == doctor_id,
                SystemConfig.config_key == "consultation.max_pass_count",
                SystemConfig.is_active == True
            )
        )
        doctor_config = result.scalar_one_or_none()
        if doctor_config:
            return int(doctor_config.config_value)
    
    # 2. 尝试获取全局配置
    result = await db.execute(
        select(SystemConfig)
        .where(
            SystemConfig.scope_type == "GLOBAL",
            SystemConfig.config_key == "consultation.max_pass_count",
            SystemConfig.is_active == True
        )
    )
    global_config = result.scalar_one_or_none()
    if global_config:
        return int(global_config.config_value)
    
    # 3. 返回默认值
    return 3


async def pass_patient(
    db: AsyncSession,
    patient_order_id: int,
    max_pass_count: int = None
) -> dict:
    """
    过号操作（当前被叫号的患者未到场）
    
    流程：
    1. 验证患者是否正在被叫号（is_calling=True）
    2. 增加过号次数（pass_count += 1）
    3. 取消正在就诊标记（is_calling = False），患者回到队列末尾
    4. 检查过号次数，如果达到上限，标记为 NO_SHOW（爽约）
    5. 自动呼叫下一位
    
    Args:
        max_pass_count: 最大过号次数上限，None 时从配置读取（优先级：医生配置 > 全局配置 > 默认3次）
    
    使用事务确保原子性
    """
    async with db.begin_nested():
        # 1. 锁定并验证过号患者
        patient_query = await db.execute(
            select(RegistrationOrder)
            .options(selectinload(RegistrationOrder.patient))
            .where(RegistrationOrder.order_id == patient_order_id)
            .with_for_update()
        )
        patient = patient_query.scalar_one_or_none()
        
        if not patient:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg=f"订单 {patient_order_id} 不存在",
                status_code=404
            )
        
        if patient.status != OrderStatus.CONFIRMED:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg=f"只能对已确认的订单进行过号操作，当前状态: {patient.status.value}",
                status_code=400
            )
        
        # 验证是否正在被叫号
        if not patient.is_calling:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="只能对正在叫号的患者执行过号操作（患者未到场时使用）",
                status_code=400
            )
        
        # 如果没有传入 max_pass_count，从配置读取
        schedule_id = patient.schedule_id
        if max_pass_count is None:
            # 获取排班信息以获取 doctor_id
            schedule_query = await db.execute(
                select(Schedule).where(Schedule.schedule_id == schedule_id)
            )
            schedule = schedule_query.scalar_one_or_none()
            doctor_id = schedule.doctor_id if schedule else None
            max_pass_count = await get_max_pass_count(db, doctor_id)
        
        # 2. 增加过号次数
        patient.pass_count += 1
        patient.is_calling = False
        
        # 3. 检查是否达到过号上限
        is_no_show = False
        if patient.pass_count >= max_pass_count:
            patient.status = OrderStatus.NO_SHOW
            is_no_show = True
        
        # 4. 自动呼叫下一位
        next_result = await call_next_patient(db, schedule_id)
        
        await db.flush()
        
        return {
            "passedPatient": {
                "orderId": patient.order_id,
                "patientName": patient.patient.name if patient.patient else "未知",
                "passCount": patient.pass_count,
                "isNoShow": is_no_show,
                "status": patient.status.value
            },
            "nextPatient": next_result["nextPatient"],
            "scheduleId": schedule_id
        }


def _format_patient_info(order: RegistrationOrder, minimal: bool = False, is_waitlist: bool = False) -> dict:
    """
    格式化患者信息为 API 响应格式
    
    Args:
        order: 挂号订单对象
        minimal: 是否只返回最小信息（用于 nextPatient）
        is_waitlist: 是否为候补队列
    """
    if not order:
        return None
    
    patient = order.patient
    
    base_info = {
        "orderId": order.order_id,
        "patientId": order.patient_id,
        "patientName": patient.name if patient else "未知",
    }
    
    if minimal:
        # 最小信息（下一位患者）
        base_info.update({
            "queueNumber": getattr(order, 'queue_number_display', '--'),
            "status": order.status.value,
            "passCount": order.pass_count
        })
    elif is_waitlist:
        # 候补队列信息
        base_info.update({
            "status": order.status.value,
            "createTime": order.create_time.strftime('%Y-%m-%d %H:%M:%S') if order.create_time else None,
            "waitlistPosition": order.waitlist_position
        })
    else:
        # 完整信息（正式队列）
        base_info.update({
            "gender": patient.gender.value if patient and patient.gender else None,
            "age": _calculate_age(patient.birth_date) if patient and patient.birth_date else None,
            "queueNumber": getattr(order, 'queue_number_display', '--'),
            "status": order.status.value,
            "isCall": order.is_calling,
            "callTime": order.call_time.strftime('%Y-%m-%d %H:%M:%S') if order.call_time else None,
            "visitTime": order.visit_times if order.visit_times else None,
            "passCount": order.pass_count,
            "priority": order.priority
        })
    
    return base_info


def _calculate_age(date_of_birth: date) -> int:
    """计算年龄"""
    if not date_of_birth:
        return None
    today = date.today()
    age = today.year - date_of_birth.year
    if (today.month, today.day) < (date_of_birth.month, date_of_birth.day):
        age -= 1
    return age
