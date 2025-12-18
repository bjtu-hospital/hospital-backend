from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.registration_order import RegistrationOrder, OrderStatus, PaymentStatus
from app.models.schedule import Schedule
from app.models.patient import Patient
from app.db.base import redis
from app.core.exception_handler import BusinessHTTPException, ResourceHTTPException
from app.core.config import settings
from datetime import datetime
from app.services.config_service import get_patient_identity_discounts, calculate_final_price
from app.models.patient import PatientType


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

    # 4. 获取身份折扣配置并计算最终价格
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
    
    # 5. 创建挂号记录，设为 PENDING（待支付）让患者支付
    reg = RegistrationOrder(
        patient_id=patient.patient_id,
        user_id=patient.user_id,
        initiator_user_id=applicant_user_id,  # 设置发起者 user_id
        doctor_id=schedule.doctor_id,
        schedule_id=schedule.schedule_id,
        slot_type=slot_type,
        slot_date=schedule.date,
        time_section=schedule.time_section,
        price=final_price,  # 应用折扣后的价格
        payment_status=PaymentStatus.PENDING,  # 加号后患者需要支付
        status=OrderStatus.PENDING,  # 待支付状态
        priority=-1,  # 加号患者优先级更高，排在队列前面（priority越小越优先）
        source_type="normal",  # 预约来源
        pass_count=0,  # 初始过号次数
        is_calling=False,  # 未就诊
        notes=f"加号申请 (由用户 {applicant_user_id} 发起)",
    )

    # 记录价格信息于 notes
    reg.notes = (reg.notes or "") + f" | price={float(final_price)}"

    db.add(reg)

    # 一并提交（调用者一般在外部 commit）
    await db.flush()
    await db.refresh(reg)

    return reg.order_id
