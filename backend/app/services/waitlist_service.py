"""
候补队列管理服务
- 使用 Redis 存放实时候补队列
- 定时任务自动压入数据库
- 自动递推通知机制
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, update
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import json
import logging
import re # 正则表达式验证邮箱格式

from app.db.base import redis
from app.models.registration_order import RegistrationOrder, OrderStatus, PaymentStatus
from app.models.patient import Patient
from app.models.user import User
from app.models.doctor import Doctor
from app.models.schedule import Schedule
from app.core.security import send_email
from app.core.config import settings
from app.core.datetime_utils import get_now_naive
from app.core.exception_handler import BusinessHTTPException

logger = logging.getLogger(__name__)


class WaitlistService:
    """候补队列服务"""
    
    # Redis Key 前缀
    WAITLIST_QUEUE_PREFIX = "waitlist:queue"  # waitlist:queue:{schedule_id} -> [user_ids...]
    WAITLIST_POSITION_PREFIX = "waitlist:position"  # waitlist:position:{schedule_id}:{patient_id} -> position
    WAITLIST_TIMEOUT = 1800  # 候补超时时间：30分钟
    
    # 邮箱格式验证正则表达式
    EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    
    @staticmethod
    def _is_valid_email(email: Optional[str]) -> bool:
        """
        验证邮箱格式是否合法
        
        参数:
        - email: 邮箱地址
        
        返回: 是否有效
        """
        if not email or not isinstance(email, str):
            return False
        email = email.strip()
        if not email:
            return False
        return bool(WaitlistService.EMAIL_REGEX.match(email))
    
    @staticmethod
    def _get_queue_key(schedule_id: int) -> str:
        """获取某排班的候补队列 Redis Key"""
        return f"{WaitlistService.WAITLIST_QUEUE_PREFIX}:{schedule_id}"
    
    @staticmethod
    def _get_position_key(schedule_id: int, patient_id: int) -> str:
        """获取某患者在某排班候补队列中的位置 Key"""
        return f"{WaitlistService.WAITLIST_POSITION_PREFIX}:{schedule_id}:{patient_id}"
    
    @classmethod
    async def add_to_queue(
        cls,
        schedule_id: int,
        patient_id: int,
        order_id: int
    ) -> int:
        """
        添加到候补队列，返回排位
        
        参数:
        - schedule_id: 排班ID
        - patient_id: 患者ID
        - order_id: 订单ID
        
        返回: 候补排位
        """
        queue_key = cls._get_queue_key(schedule_id)
        
        # 获取当前队列长度（排位）
        queue = await redis.lrange(queue_key, 0, -1)
        position = len(queue) + 1
        
        # 存入队列：[order_id, patient_id, create_timestamp]
        queue_data = json.dumps({
            "order_id": order_id,
            "patient_id": patient_id,
            "create_time": get_now_naive().isoformat()
        })
        await redis.rpush(queue_key, queue_data)
        
        # 设置队列过期时间：6小时（定期压入DB时清理）
        await redis.expire(queue_key, 6 * 3600)
        
        # 记录位置映射
        position_key = cls._get_position_key(schedule_id, patient_id)
        await redis.set(position_key, str(position), ex=6 * 3600)
        
        logger.info(f"添加到候补队列: schedule_id={schedule_id}, patient_id={patient_id}, position={position}")
        return position
    
    @classmethod
    async def get_queue_position(
        cls,
        schedule_id: int,
        patient_id: int
    ) -> Optional[int]:
        """获取患者在队列中的排位"""
        position_key = cls._get_position_key(schedule_id, patient_id)
        pos = await redis.get(position_key)
        return int(pos) if pos else None
    
    @classmethod
    async def get_first_in_queue(
        cls,
        schedule_id: int
    ) -> Optional[Dict]:
        """
        获取队列首个候补记录
        
        返回: {order_id, patient_id, create_time} 或 None
        """
        queue_key = cls._get_queue_key(schedule_id)
        front = await redis.lindex(queue_key, 0)
        
        if front:
            try:
                data = json.loads(front)
                return data
            except Exception as e:
                logger.error(f"解析队列数据失败: {e}")
                return None
        return None
    
    @classmethod
    async def remove_from_queue(
        cls,
        schedule_id: int,
        patient_id: int
    ) -> bool:
        """
        从队列移除（取消候补或转预约）
        
        返回: 是否成功移除
        """
        queue_key = cls._get_queue_key(schedule_id)
        
        # 获取整个队列
        queue = await redis.lrange(queue_key, 0, -1)
        
        # 过滤掉该患者的记录
        new_queue = []
        removed = False
        for item in queue:
            try:
                data = json.loads(item)
                if data["patient_id"] == patient_id:
                    removed = True
                    continue
                new_queue.append(item)
            except Exception as e:
                logger.error(f"处理队列数据失败: {e}")
        
        # 重新设置队列
        if removed:
            await redis.delete(queue_key)
            if new_queue:
                for item in new_queue:
                    await redis.rpush(queue_key, item)
                await redis.expire(queue_key, 6 * 3600)
            
            # 清除位置映射
            position_key = cls._get_position_key(schedule_id, patient_id)
            await redis.delete(position_key)
            
            logger.info(f"从候补队列移除: schedule_id={schedule_id}, patient_id={patient_id}")
        
        return removed
    
    @classmethod
    async def notify_and_convert_first_in_queue(
        cls,
        db: AsyncSession,
        schedule_id: int
    ) -> Optional[int]:
        """
        获取队列第一个，发送邮件通知并自动转预约
        
        流程:
        1. 验证schedule还有剩余号源
        2. 获取队列首个候补记录（order_id）
        3. 从DB查询订单、患者、发起人信息
        4. 发送邮件通知到发起人邮箱
        5. 更新订单状态：WAITLIST → PENDING（自动转预约）
        6. 更新schedule的remaining_slots
        7. 从队列移除
        8. 返回订单ID
        
        返回: 转预约成功的订单ID，或 None
        """
        # 首先检查schedule是否还有剩余号源
        from app.models.schedule import Schedule
        schedule_res = await db.execute(
            select(Schedule).where(Schedule.schedule_id == schedule_id)
        )
        schedule = schedule_res.scalar_one_or_none()
        
        if not schedule or schedule.remaining_slots <= 0:
            logger.info(f"号源已满或不存在: schedule_id={schedule_id}, remaining={schedule.remaining_slots if schedule else 'N/A'}")
            return None
        
        first = await cls.get_first_in_queue(schedule_id)
        if not first:
            return None
        
        order_id = first["order_id"]
        patient_id = first["patient_id"]
        
        try:
            # 查询订单、患者、发起人、医生、排班信息
            order_res = await db.execute(
                select(RegistrationOrder, Patient, User, Doctor, Schedule).
                join(Patient, Patient.patient_id == RegistrationOrder.patient_id).
                join(User, User.user_id == RegistrationOrder.initiator_user_id).
                join(Doctor, Doctor.doctor_id == RegistrationOrder.doctor_id).
                join(Schedule, Schedule.schedule_id == RegistrationOrder.schedule_id).
                where(RegistrationOrder.order_id == order_id)
            )
            row = order_res.first()
            
            if not row:
                logger.warning(f"订单不存在: order_id={order_id}")
                await cls.remove_from_queue(schedule_id, patient_id)
                return None
            
            order, patient, initiator_user, doctor, schedule = row
            
            # 检查订单状态是否仍为 WAITLIST
            if order.status != OrderStatus.WAITLIST:
                logger.info(f"订单状态已变更，不再转预约: order_id={order_id}, status={order.status.value}")
                await cls.remove_from_queue(schedule_id, patient_id)
                return None
            
            # 发送邮件通知到发起人
            # 需要同时检查：发起人存在、有邮箱、邮箱格式有效
            email_sent = False
            
            if not initiator_user:
                logger.warning(f"发起人不存在，无法发送邮件通知: order_id={order_id}, initiator_user_id={order.initiator_user_id}")
            elif not initiator_user.email:
                logger.warning(f"发起人邮箱为空，无法发送邮件通知: user_id={initiator_user.user_id}, order_id={order_id}")
            elif not cls._is_valid_email(initiator_user.email):
                logger.warning(f"发起人邮箱格式不合法，无法发送邮件通知: email={initiator_user.email}, user_id={initiator_user.user_id}, order_id={order_id}")
            else:
                # 邮箱有效，尝试发送
                try:
                    subject = "候补转预约通知"
                    body = f"""
                    <html>
                    <body style="font-family: Arial, sans-serif;">
                        <h2>您的候补已转为预约</h2>
                        <p>尊敬的用户，</p>
                        <p>恭喜！为 <strong>{patient.name}</strong> 预约的候补号已自动转为预约。</p>
                        <table style="border-collapse: collapse; margin: 20px 0;">
                            <tr>
                                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">医生：</td>
                                <td style="padding: 8px; border: 1px solid #ddd;">{doctor.name}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">日期：</td>
                                <td style="padding: 8px; border: 1px solid #ddd;">{order.slot_date}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">时段：</td>
                                <td style="padding: 8px; border: 1px solid #ddd;">{order.time_section}</td>
                            </tr>
                        </table>
                        <p style="color: red; font-weight: bold;">请在30分钟内完成支付，否则预约将被自动取消。</p>
                        <p>谢谢！</p>
                        <hr>
                        <p style="color: #666; font-size: 12px;">此邮件由系统自动发送，请勿直接回复。</p>
                    </body>
                    </html>
                    """
                    send_email(initiator_user.email, subject, body)
                    logger.info(f"邮件通知已发送: email={initiator_user.email}, order_id={order_id}")
                    email_sent = True
                except Exception as e:
                    logger.error(f"邮件发送失败: {e}, order_id={order_id}")
                    # 不中断流程，继续转预约
            
            # 更新订单状态: WAITLIST → PENDING
            now = get_now_naive()
            order.status = OrderStatus.PENDING
            order.payment_status = PaymentStatus.PENDING
            order.is_waitlist = False
            order.waitlist_position = None
            order.source_type = "waitlist"  # 标记为候补转预约
            order.update_time = now
            
            db.add(order)
            
            # 更新schedule的remaining_slots (减1，因为候补转为预约占用号源)
            schedule.remaining_slots -= 1
            db.add(schedule)
            
            await db.commit()
            
            # 从队列移除
            await cls.remove_from_queue(schedule_id, patient_id)
            
            # 发送微信订阅消息（候补转预约成功）
            try:
                from app.services.wechat_service import WechatService
                from app.models.clinic import Clinic
                
                # 获取clinic信息用于组装通知
                clinic_res = await db.execute(
                    select(Clinic).where(Clinic.clinic_id == schedule.clinic_id)
                )
                clinic = clinic_res.scalar_one_or_none()
                clinic_name = clinic.name if clinic else "诊室"
                
                # 格式化时间
                time_str = WaitlistService._get_time_section_start(order.time_section, {})
                datetime_str = f"{order.slot_date.strftime('%Y年%m月%d日')} {time_str}"
                
                # 构建微信通知（复用候补成功模板）
                wechat_data = {
                    "thing6": {"value": patient.name or "就诊人"},
                    "phrase1": {"value": "候补转预约成功"},
                    "thing4": {"value": clinic_name or ""},
                    "time3": {"value": datetime_str},
                    "thing5": {"value": doctor.name or ""},
                }
                
                wechat = WechatService()
                openid = await wechat.get_user_openid(db, order.initiator_user_id)
                
                if openid and settings.WECHAT_TEMPLATE_WAITLIST_SUCCESS:
                    authorized = await wechat.check_user_authorized(
                        db,
                        order.initiator_user_id,
                        settings.WECHAT_TEMPLATE_WAITLIST_SUCCESS
                    )
                    if authorized:
                        await wechat.send_subscribe_message(
                            db,
                            order.initiator_user_id,
                            openid,
                            settings.WECHAT_TEMPLATE_WAITLIST_SUCCESS,
                            wechat_data,
                            scene="waitlist_to_appointment",
                            order_id=order_id,
                        )
                        logger.info(f"候补转预约微信通知已发送: openid={openid}, order_id={order_id}")
                    else:
                        logger.info(f"用户未授权该模板，跳过微信通知: user_id={order.initiator_user_id}, order_id={order_id}")
                else:
                    logger.info(f"缺少openid或模板ID，跳过微信通知: openid={openid}, order_id={order_id}")
            except Exception as e:
                logger.warning(f"候补转预约微信通知发送失败: {e}")
            
            logger.info(f"候补自动转预约成功: order_id={order_id}, patient_id={patient_id}, remaining_slots={schedule.remaining_slots}")
            return order_id
            
        except Exception as e:
            await db.rollback()
            logger.error(f"候补转预约失败: {e}")
            return None
    
    @staticmethod
    def _get_time_section_start(time_section: str, schedule_config: dict) -> str:
        """根据时间段获取开始时间字符串"""
        section = (time_section or "").strip()
        if section in ["上午", "早上", "morning"]:
            return schedule_config.get("morningStart", "08:00")
        if section in ["下午", "中午", "afternoon"]:
            return schedule_config.get("afternoonStart", "13:30")
        return schedule_config.get("eveningStart", "18:00")
    
    @classmethod
    async def persist_waitlist_to_db(
        cls,
        db: AsyncSession
    ) -> int:
        """
        定时任务：将 Redis 中的候补队列压入数据库
        
        逻辑:
        1. 扫描所有 Redis 队列（waitlist:queue:*）
        2. 对每个队列，更新 DB 中对应记录的 waitlist_position
        3. 清理过期队列
        
        返回: 更新的记录数
        """
        try:
            # 获取所有队列 Key
            keys = await redis.keys(f"{cls.WAITLIST_QUEUE_PREFIX}:*")
            
            updated_count = 0
            for key in keys:
                try:
                    # 提取 schedule_id
                    schedule_id = int(key.split(":")[-1])
                    
                    # 获取队列数据
                    queue = await redis.lrange(key, 0, -1)
                    
                    # 更新 DB 中的 waitlist_position
                    for idx, item in enumerate(queue, start=1):
                        try:
                            data = json.loads(item)
                            order_id = data["order_id"]
                            
                            # 更新数据库
                            await db.execute(
                                update(RegistrationOrder).
                                where(RegistrationOrder.order_id == order_id).
                                values(waitlist_position=idx, update_time=get_now_naive())
                            )
                            updated_count += 1
                        except Exception as e:
                            logger.error(f"更新 waitlist_position 失败: {e}")
                    
                    await db.commit()
                    
                except Exception as e:
                    logger.error(f"处理队列 {key} 失败: {e}")
            
            logger.info(f"候补队列持久化完成: 更新 {updated_count} 条记录")
            return updated_count
            
        except Exception as e:
            await db.rollback()
            logger.error(f"候补队列持久化失败: {e}")
            return 0
