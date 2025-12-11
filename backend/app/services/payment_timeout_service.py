"""
支付超时管理服务
- 定期扫描超时未支付的订单
- 自动取消超时订单
- 释放号源并触发候补转预约
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, update
from datetime import datetime, timedelta
import logging

from app.db.base import redis
from app.models.registration_order import RegistrationOrder, OrderStatus, PaymentStatus
from app.models.schedule import Schedule
from app.services.waitlist_service import WaitlistService
from app.core.config import settings

logger = logging.getLogger(__name__)


class PaymentTimeoutService:
    """支付超时处理服务"""
    
    # 支付超时时间：30分钟
    PAYMENT_TIMEOUT_MINUTES = 30
    
    @classmethod
    async def cancel_timeout_pending_orders(cls, db: AsyncSession) -> int:
        """
        扫描并自动取消超时未支付的订单
        
        逻辑:
        1. 查询所有状态为 PENDING 且创建时间超过 30 分钟的订单
        2. 对每个超时订单：
           a. 标记为 TIMEOUT
           b. 更新支付状态为 FAILED
           c. 释放对应的号源
           d. 触发候补转预约
        3. 返回处理的订单数
        
        返回: 处理的订单数
        """
        try:
            # 计算超时时间点（30分钟前）
            timeout_threshold = datetime.now() - timedelta(minutes=cls.PAYMENT_TIMEOUT_MINUTES)
            
            # 查询所有超时未支付的订单（PENDING 状态，创建时间超过 30 分钟）
            result = await db.execute(
                select(RegistrationOrder, Schedule).
                outerjoin(Schedule, Schedule.schedule_id == RegistrationOrder.schedule_id).
                where(
                    and_(
                        RegistrationOrder.status == OrderStatus.PENDING,
                        RegistrationOrder.payment_status == PaymentStatus.PENDING,
                        RegistrationOrder.is_waitlist == False,  # noqa: E712
                        RegistrationOrder.create_time <= timeout_threshold
                    )
                )
            )
            
            timeout_orders = result.all()
            processed_count = 0
            
            for order, schedule in timeout_orders:
                try:
                    # 1. 标记订单为超时
                    order.status = OrderStatus.TIMEOUT
                    order.payment_status = PaymentStatus.FAILED
                    order.update_time = datetime.now()
                    db.add(order)
                    
                    # 2. 释放号源（如果关联了排班）
                    if order.schedule_id and schedule:
                        schedule.remaining_slots = (schedule.remaining_slots or 0) + 1
                        db.add(schedule)
                        logger.info(f"释放号源: schedule_id={order.schedule_id}, 剩余={schedule.remaining_slots}")
                    
                    await db.flush()
                    
                    # 3. 级联触发候补转预约（如果有多个候补，全部转换）
                    if order.schedule_id:
                        try:
                            converted_count = 0
                            max_attempts = 10  # 防止无限循环
                            
                            for attempt in range(max_attempts):
                                converted_order_id = await WaitlistService.notify_and_convert_first_in_queue(
                                    db,
                                    order.schedule_id
                                )
                                if not converted_order_id:
                                    break
                                converted_count += 1
                            
                            if converted_count > 0:
                                logger.info(f"支付超时订单 {order.order_id} 释放号源，自动转化 {converted_count} 个候补")
                            else:
                                logger.info(f"支付超时订单 {order.order_id} 释放号源，但无候补订单")
                        except Exception as e:
                            logger.warning(f"触发候补转预约失败: {str(e)}")
                            # 继续处理下一个订单
                    
                    processed_count += 1
                    logger.info(f"支付超时订单已处理: order_id={order.order_id}, 新状态=TIMEOUT")
                    
                except Exception as e:
                    await db.rollback()
                    logger.error(f"处理超时订单 {order.order_id} 时失败: {str(e)}")
                    # 继续处理其他订单
            
            # 批量提交所有更改
            if processed_count > 0:
                await db.commit()
                logger.info(f"支付超时扫描完成: 处理 {processed_count} 个订单")
            
            return processed_count
            
        except Exception as e:
            await db.rollback()
            logger.error(f"支付超时扫描失败: {str(e)}")
            return 0
    
    @classmethod
    async def get_timeout_pending_orders_count(cls, db: AsyncSession) -> int:
        """获取当前超时未支付的订单数（用于监控）"""
        try:
            timeout_threshold = datetime.now() - timedelta(minutes=cls.PAYMENT_TIMEOUT_MINUTES)
            
            result = await db.execute(
                select(RegistrationOrder).where(
                    and_(
                        RegistrationOrder.status == OrderStatus.PENDING,
                        RegistrationOrder.payment_status == PaymentStatus.PENDING,
                        RegistrationOrder.is_waitlist == False,  # noqa: E712
                        RegistrationOrder.create_time <= timeout_threshold
                    )
                )
            )
            
            orders = result.all()
            return len(orders)
        except Exception as e:
            logger.error(f"查询超时订单计数失败: {str(e)}")
            return 0
