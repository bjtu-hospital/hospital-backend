from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.registration_order import RegistrationOrder, OrderStatus
from app.models.schedule import Schedule
from app.models.patient import Patient
from app.db.base import redis
from app.core.exception_handler import BusinessHTTPException, ResourceHTTPException
from app.core.config import settings
from datetime import datetime


async def execute_add_slot_and_register(
    db: AsyncSession,
    schedule_id: int,
    patient_id: int,
    slot_type: str,
    applicant_user_id: int,
    position: str = "end"
) -> int:
    """在单个事务中执行加号并创建挂号记录。

    Args:
        position: 加号位置，"next" 表示插队到下一个，"end" 表示队尾（默认）

    返回新创建的 registration_order.order_id
    """
    # 1. 获取并校验 schedule
    result = await db.execute(select(Schedule).where(Schedule.schedule_id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="排班不存在", status_code=404)

    # 2. 获取 patient 记录（通过 patient_id）
    result = await db.execute(select(Patient).where(Patient.patient_id == patient_id))
    patient = result.scalar_one_or_none()
    if not patient:
        raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="患者不存在或未注册为患者", status_code=404)

    # 3. 检查是否已有重复挂号（同一 schedule_id 且未取消）
    res = await db.execute(
        select(RegistrationOrder).where(
            RegistrationOrder.schedule_id == schedule_id,
            RegistrationOrder.patient_id == patient.patient_id,
            RegistrationOrder.status != OrderStatus.CANCELLED
        )
    )
    if res.scalar_one_or_none():
        raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="患者在该排班已有有效挂号", status_code=400)

    # 4. 根据 position 设置优先级
    priority = -1 if position == "next" else 0

    # 5. 创建挂号记录，直接设为 CONFIRMED（已支付）
    reg = RegistrationOrder(
        patient_id=patient.patient_id,
        user_id=patient.user_id,
        doctor_id=schedule.doctor_id,
        schedule_id=schedule.schedule_id,
        slot_type=slot_type,
        slot_date=schedule.date,
        time_section=schedule.time_section,
        status=OrderStatus.CONFIRMED,  # 加号直接进入正式队列
        priority=priority,  # 设置优先级
        notes=f"加号申请 (由用户 {applicant_user_id} 发起，位置: {position})",
    )

    # 记录价格信息于 notes
    reg.notes = (reg.notes or "") + f" | price={float(schedule.price)}"

    db.add(reg)

    # 一并提交（调用者一般在外部 commit）
    await db.flush()
    await db.refresh(reg)

    return reg.order_id
