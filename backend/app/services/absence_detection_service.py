"""
自动检测并标记缺勤的服务

功能：
1. 检查已过期的排班（日期 < 今天）
2. 若排班无考勤记录，自动创建 ABSENT 状态的记录
3. 支持手动触发和定时任务调用
"""
from datetime import date, datetime, timedelta
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict
from app.core.datetime_utils import get_now_naive, get_today
import logging

from app.models.schedule import Schedule
from app.models.attendance_record import AttendanceRecord, AttendanceStatus
from app.models.doctor import Doctor

logger = logging.getLogger(__name__)


async def mark_absent_for_date(
    db: AsyncSession,
    target_date: date
) -> Dict[str, int]:
    """
    标记指定日期的缺勤记录
    
    Args:
        db: 数据库会话
        target_date: 目标日期（通常是昨天或更早）
        
    Returns:
        统计信息: {"total_schedules": 总排班数, "absent_marked": 标记缺勤数, "already_recorded": 已有记录数}
    """
    logger.info(f"开始检查 {target_date} 的缺勤情况")
    
    # 查询指定日期的所有排班（排除停诊）
    result = await db.execute(
        select(Schedule).where(
            and_(
                Schedule.date == target_date,
                Schedule.status != "停诊"
            )
        )
    )
    schedules = result.scalars().all()
    
    if not schedules:
        logger.info(f"{target_date} 无有效排班")
        return {
            "total_schedules": 0,
            "absent_marked": 0,
            "already_recorded": 0
        }
    
    total_count = len(schedules)
    absent_marked = 0
    already_recorded = 0
    
    for schedule in schedules:
        # 检查是否已有考勤记录
        existing_result = await db.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.schedule_id == schedule.schedule_id
            )
        )
        existing_record = existing_result.scalar_one_or_none()
        
        if existing_record:
            already_recorded += 1
            logger.debug(f"排班 {schedule.schedule_id} 已有考勤记录，跳过")
            continue
        
        # 创建缺勤记录
        absent_record = AttendanceRecord(
            schedule_id=schedule.schedule_id,
            doctor_id=schedule.doctor_id,
            checkin_time=None,
            checkin_lat=None,
            checkin_lng=None,
            checkout_time=None,
            checkout_lat=None,
            checkout_lng=None,
            work_duration_minutes=None,
            status=AttendanceStatus.ABSENT,
            created_at=get_now_naive(),
            updated_at=get_now_naive()
        )
        db.add(absent_record)
        absent_marked += 1
        logger.info(f"标记缺勤: 排班 {schedule.schedule_id}, 医生 {schedule.doctor_id}, 日期 {target_date}")
    
    await db.commit()
    
    stats = {
        "total_schedules": total_count,
        "absent_marked": absent_marked,
        "already_recorded": already_recorded
    }
    
    logger.info(f"{target_date} 缺勤检查完成: {stats}")
    return stats


async def mark_absent_for_date_range(
    db: AsyncSession,
    start_date: date,
    end_date: date
) -> List[Dict]:
    """
    批量标记日期范围内的缺勤记录
    
    Args:
        db: 数据库会话
        start_date: 开始日期
        end_date: 结束日期
        
    Returns:
        每日统计列表
    """
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    
    results = []
    current_date = start_date
    
    while current_date <= end_date:
        stats = await mark_absent_for_date(db, current_date)
        results.append({
            "date": str(current_date),
            **stats
        })
        current_date += timedelta(days=1)
    
    return results


async def auto_mark_yesterday_absent(db: AsyncSession) -> Dict:
    """
    自动标记昨天的缺勤记录（用于定时任务）
    
    Args:
        db: 数据库会话
        
    Returns:
        统计信息
    """
    yesterday = get_today() - timedelta(days=1)
    logger.info(f"定时任务：开始标记 {yesterday} 的缺勤记录")
    
    stats = await mark_absent_for_date(db, yesterday)
    
    logger.info(f"定时任务完成：{yesterday} 缺勤标记统计 - {stats}")
    return {
        "date": str(yesterday),
        **stats
    }


async def get_absent_statistics(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    doctor_id: int = None
) -> Dict:
    """
    获取缺勤统计
    
    Args:
        db: 数据库会话
        start_date: 开始日期
        end_date: 结束日期
        doctor_id: 医生ID（可选，指定则只统计该医生）
        
    Returns:
        缺勤统计信息
    """
    conditions = [
        AttendanceRecord.status == AttendanceStatus.ABSENT,
        AttendanceRecord.created_at >= datetime.combine(start_date, datetime.min.time()),
        AttendanceRecord.created_at <= datetime.combine(end_date, datetime.max.time())
    ]
    
    if doctor_id:
        conditions.append(AttendanceRecord.doctor_id == doctor_id)
    
    # 查询缺勤记录
    result = await db.execute(
        select(AttendanceRecord, Schedule, Doctor)
        .join(Schedule, AttendanceRecord.schedule_id == Schedule.schedule_id)
        .join(Doctor, AttendanceRecord.doctor_id == Doctor.doctor_id)
        .where(and_(*conditions))
        .order_by(Schedule.date.desc())
    )
    
    rows = result.all()
    
    # 按医生汇总
    doctor_stats = {}
    records = []
    
    for record, schedule, doctor in rows:
        # 详细记录
        records.append({
            "record_id": record.record_id,
            "schedule_id": schedule.schedule_id,
            "doctor_id": doctor.doctor_id,
            "doctor_name": doctor.name,
            "date": str(schedule.date),
            "time_section": schedule.time_section,
            "clinic_id": schedule.clinic_id,
            "created_at": record.created_at.isoformat()
        })
        
        # 按医生统计
        if doctor.doctor_id not in doctor_stats:
            doctor_stats[doctor.doctor_id] = {
                "doctor_id": doctor.doctor_id,
                "doctor_name": doctor.name,
                "absent_count": 0
            }
        doctor_stats[doctor.doctor_id]["absent_count"] += 1
    
    return {
        "total_absent": len(records),
        "date_range": {
            "start": str(start_date),
            "end": str(end_date)
        },
        "doctor_statistics": list(doctor_stats.values()),
        "records": records
    }
