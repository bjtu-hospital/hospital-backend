"""
就诊提醒定时任务服务

定期检查即将就诊的预约订单，提前发送就诊提醒微信订阅消息
"""
from datetime import timedelta, date as date_type
from app.core.datetime_utils import get_now_naive
from typing import Optional
import logging
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.registration_order import RegistrationOrder, OrderStatus, PaymentStatus
from app.models.schedule import Schedule
from app.models.patient import Patient
from app.models.doctor import Doctor
from app.models.clinic import Clinic
from app.services.wechat_service import WechatService
from app.services.config_service import get_schedule_config
from app.core.config import settings

logger = logging.getLogger(__name__)


async def get_time_section_start(
    db: AsyncSession,
    time_section: str,
    scope_type: str = "GLOBAL",
    scope_id: Optional[int] = None
) -> str:
    """
    根据时间段从系统配置表返回开始时间
    
    参数:
        db: 数据库会话
        time_section: 时间段标识（如"上午", "下午", "晚上"等）
        scope_type: 作用域类型（DOCTOR/CLINIC/GLOBAL）
        scope_id: 作用域ID（doctor_id/clinic_id）
    
    返回:
        时间字符串，格式为 "HH:MM"
    """
    try:
        # 获取排班配置
        config = await get_schedule_config(db, scope_type=scope_type, scope_id=scope_id)
        
        # 根据时间段标识选择对应的配置字段
        section = (time_section or "").strip()
        if section in ["上午", "早上", "morning"]:
            return config["morningStart"]
        elif section in ["下午", "中午", "afternoon"]:
            return config["afternoonStart"]
        else:  # 晚上或其他
            return config["eveningStart"]
    except Exception as e:
        logger.error(f"[就诊提醒] 获取时间段配置失败: {str(e)}，使用默认值")
        # 降级到硬编码默认值
        section = (time_section or "").strip()
        if section in ["上午", "早上", "morning"]:
            return "08:00"
        elif section in ["下午", "中午", "afternoon"]:
            return "13:30"
        else:
            return "18:00"


async def send_single_reminder(
    db: AsyncSession,
    order: RegistrationOrder,
    schedule: Schedule,
    patient: Patient,
    doctor: Doctor,
    clinic: Clinic
) -> bool:
    """
    为单个订单发送就诊提醒
    
    返回:
        True if 成功发送或已发送过, False if 发送失败
    """
    try:
        # 检查是否已经发送过提醒
        from app.models.wechat_message_log import WechatMessageLog
        existing_reminder = await db.execute(
            select(WechatMessageLog).where(
                and_(
                    WechatMessageLog.order_id == order.order_id,
                    WechatMessageLog.scene == "reminder",
                    WechatMessageLog.status == "success"
                )
            )
        )
        if existing_reminder.scalar_one_or_none():
            logger.info(f"[就诊提醒] 订单 {order.order_no} 已发送过提醒，跳过")
            return True
        
        # 获取用户的openid
        target_user_id = patient.user_id if patient and patient.user_id else order.initiator_user_id
        if not target_user_id:
            logger.warning(f"[就诊提醒] 订单 {order.order_no} 无法确定目标用户，跳过")
            return False
        
        wechat = WechatService()
        openid = await wechat.get_user_openid(db, target_user_id)
        if not openid:
            logger.warning(f"[就诊提醒] 用户 {target_user_id} 未绑定微信openid，跳过")
            return False
        
        # 检查用户是否授权
        template_id = settings.WECHAT_TEMPLATE_REMINDER
        if not template_id:
            logger.warning(f"[就诊提醒] 未配置就诊提醒模板ID，跳过")
            return False
        
        authorized = await wechat.check_user_authorized(db, target_user_id, template_id)
        if not authorized:
            logger.info(f"[就诊提醒] 用户 {target_user_id} 未授权模板 {template_id}，跳过")
            return False
        
        # 构建消息数据
        patient_name = patient.name if patient else ""
        # 就诊时间格式：2025年12月19日 上午08:00
        # 从系统配置获取时间段开始时间，使用诊所作用域
        time_str = await get_time_section_start(
            db,
            schedule.time_section,
            scope_type="CLINIC",
            scope_id=clinic.clinic_id if clinic else None
        )
        datetime_str = f"{schedule.date.strftime('%Y年%m月%d日')} {schedule.time_section}{time_str}"
        # 体检地点使用clinic.address，如果为空则使用clinic.name
        location = (clinic.address or clinic.name) if clinic else ""
        # 温馨提示
        tip = f"已预约成功,请注意查看"
        
        data_payload = {
            "thing65": {"value": patient_name or ""},
            "time67": {"value": datetime_str},
            "thing18": {"value": location or ""},
            "thing8": {"value": tip},
        }
        
        # 发送订阅消息
        await wechat.send_subscribe_message(
            db,
            target_user_id,
            openid,
            template_id,
            data_payload,
            scene="reminder",
            order_id=order.order_id,
            page=f"pages/appointment/detail?orderId={order.order_id}",
        )
        
        logger.info(f"[就诊提醒] 订单 {order.order_no} 提醒发送成功")
        return True
        
    except Exception as e:
        logger.error(f"[就诊提醒] 订单 {order.order_no} 提醒发送失败: {str(e)}")
        return False


async def send_appointment_reminder(db: AsyncSession, target_date: date_type = None):
    """
    发送就诊提醒
    
    业务规则：
    1. 查找指定日期（默认明天）需要就诊的已支付已确认订单
    2. 检查是否已经发送过提醒（通过wechat_message_log表scene='reminder'来判断）
    3. 发送就诊提醒微信订阅消息
    
    参数:
        db: 数据库session
        target_date: 目标日期，默认为None表示明天
    
    调用时机：
        - 定时任务：每天晚上20:00执行，提前一天提醒患者明天的就诊安排
        - 手动API：客户端请求时，限制为前一天才能提醒
    """
    if target_date is None:
        target_date = (get_now_naive() + timedelta(days=1)).date()
    logger.info(f"[就诊提醒] 开始执行 - {get_now_naive()}, 目标日期: {target_date}")
    
    try:
        # 查询指定日期需要就诊的已支付已确认订单
        stmt = select(RegistrationOrder, Schedule, Patient, Doctor, Clinic).join(
            Schedule, Schedule.schedule_id == RegistrationOrder.schedule_id
        ).join(
            Patient, Patient.patient_id == RegistrationOrder.patient_id
        ).join(
            Doctor, Doctor.doctor_id == RegistrationOrder.doctor_id
        ).join(
            Clinic, Clinic.clinic_id == Schedule.clinic_id
        ).where(
            and_(
                RegistrationOrder.slot_date == target_date,
                RegistrationOrder.status == OrderStatus.CONFIRMED,  # 只提醒已确认订单
                RegistrationOrder.payment_status == PaymentStatus.PAID,
                RegistrationOrder.is_waitlist == False,
            )
        )
        
        result = await db.execute(stmt)
        orders_data = result.all()
        
        if not orders_data:
            logger.info(f"[就诊提醒] {target_date}无需要提醒的就诊订单")
            return {"total": 0, "success": 0, "failed": 0}
        
        logger.info(f"[就诊提醒] 找到 {len(orders_data)} 个订单需要提醒")
        
        success_count = 0
        failed_count = 0
        
        for order, schedule, patient, doctor, clinic in orders_data:
            if await send_single_reminder(db, order, schedule, patient, doctor, clinic):
                success_count += 1
            else:
                failed_count += 1
        
        logger.info(f"[就诊提醒] 完成 - 总计:{len(orders_data)}, 成功:{success_count}, 失败:{failed_count}")
        return {"total": len(orders_data), "success": success_count, "failed": failed_count}
        
    except Exception as e:
        logger.error(f"[就诊提醒] 执行失败: {str(e)}", exc_info=True)
        return {"total": 0, "success": 0, "failed": 0, "error": str(e)}
