"""
缺勤检测定时任务调度器

使用 APScheduler 每日自动标记缺勤
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
from app.core.datetime_utils import get_now_naive
import logging

from app.db.base import get_db
from app.services.absence_detection_service import auto_mark_yesterday_absent

logger = logging.getLogger(__name__)

# 全局调度器实例
scheduler: AsyncIOScheduler = None


async def scheduled_absence_check():
    """
    定时任务：检查并标记昨天的缺勤记录
    """
    logger.info(f"[定时任务] 缺勤检测任务开始执行 - {get_now_naive()}")
    
    try:
        async for db in get_db():
            stats = await auto_mark_yesterday_absent(db)
            logger.info(f"[定时任务] 缺勤检测完成: {stats}")
            break  # get_db 是生成器，只需要一个 session
    except Exception as e:
        logger.error(f"[定时任务] 缺勤检测失败: {str(e)}", exc_info=True)


def start_absence_scheduler():
    """
    启动缺勤检测定时任务
    
    默认配置：每天凌晨 2:00 执行
    """
    global scheduler
    
    if scheduler is not None:
        logger.warning("缺勤检测调度器已在运行")
        return
    
    scheduler = AsyncIOScheduler()
    
    # 添加定时任务：每天凌晨 2:00 执行
    scheduler.add_job(
        scheduled_absence_check,
        trigger=CronTrigger(hour=2, minute=0),
        id='daily_absence_check',
        name='每日缺勤检测',
        replace_existing=True
    )
    
    scheduler.start()
    logger.info("缺勤检测定时任务已启动 - 每日 02:00 执行")


def stop_absence_scheduler():
    """
    停止缺勤检测定时任务
    """
    global scheduler
    
    if scheduler is None:
        logger.warning("缺勤检测调度器未运行")
        return
    
    scheduler.shutdown()
    scheduler = None
    logger.info("缺勤检测定时任务已停止")
