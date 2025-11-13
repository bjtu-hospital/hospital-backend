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
    applicant_user_id: int
) -> int:
    """在单个事务中执行加号并创建挂号记录。

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

    # 4. 创建挂号记录，使用 schedule.price 作为费用
    reg = RegistrationOrder(
        patient_id=patient.patient_id,
        user_id=patient.user_id,
        doctor_id=schedule.doctor_id,
        schedule_id=schedule.schedule_id,
        slot_type=slot_type,
        slot_date=schedule.date,
        time_section=schedule.time_section,
        status=OrderStatus.PENDING,
        notes=f"加号申请 (由用户 {applicant_user_id} 发起)",
    )

    # 记录价格字段于 notes 或 visit_times 处；RegistrationOrder 模型中没有价格字段，
    # 如需保留价格，建议在 notes 中附加价格信息或拓展模型。这里把价格放在 notes。
    reg.notes = (reg.notes or "") + f" | price={float(schedule.price)}"

    db.add(reg)

    # (不改变)5. 更新排班：total_slots +1, remaining_slots +1
    # schedule.total_slots = (schedule.total_slots or 0) + 1
    # schedule.remaining_slots = (schedule.remaining_slots or 0) + 1
    # db.add(schedule)

    # 一并提交（调用者一般在外部 commit）
    await db.flush()
    await db.refresh(reg)

    return reg.order_id
