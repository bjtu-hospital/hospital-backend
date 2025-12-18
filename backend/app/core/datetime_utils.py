"""北京时间（UTC+8）处理工具

该模块提供统一的时间处理函数，确保所有时间戳都使用北京时间（UTC+8）
而不是 UTC 时间，以保证前后端显示一致。
"""
from datetime import datetime, timezone, timedelta
from typing import Optional


# 北京时区（UTC+8）
BEIJING_TZ = timezone(timedelta(hours=8))


def get_now() -> datetime:
    """获取当前北京时间（带时区信息）。
    
    返回值：
        datetime: 北京时间的 datetime 对象（带时区信息）
    
    示例：
        >>> now = get_now()
        >>> print(now)  # 2025-12-18 14:30:45.123456+08:00
    """
    return datetime.now(BEIJING_TZ)


def get_now_naive() -> datetime:
    """获取当前北京时间（不带时区信息）。
    
    此函数用于与不支持时区的 ORM 或数据库字段兼容。
    返回的 datetime 对象表示的是北京时间，但不包含时区信息。
    
    返回值：
        datetime: 北京时间的 datetime 对象（不带时区信息）
    
    示例：
        >>> now = get_now_naive()
        >>> print(now)  # 2025-12-18 14:30:45.123456
    """
    return get_now().replace(tzinfo=None)


def convert_to_beijing_time(dt: Optional[datetime]) -> Optional[datetime]:
    """将 UTC 时间转换为北京时间。
    
    参数：
        dt: 待转换的 datetime 对象（可能含有时区信息）
    
    返回值：
        datetime: 北京时间的 datetime 对象（不带时区信息），如果输入为 None 则返回 None
    
    示例：
        >>> utc_time = datetime.now(timezone.utc)
        >>> bj_time = convert_to_beijing_time(utc_time)
    """
    if not dt:
        return None
    
    # 如果已经是北京时间，直接返回（去掉时区信息）
    if dt.tzinfo == BEIJING_TZ:
        return dt.replace(tzinfo=None)
    
    # 如果没有时区信息，假设为 UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    
    # 转换到北京时区
    beijing_dt = dt.astimezone(BEIJING_TZ)
    
    # 返回无时区信息的版本
    return beijing_dt.replace(tzinfo=None)


def utc_to_beijing(utc_dt: Optional[datetime]) -> Optional[datetime]:
    """将 UTC 时间转换为北京时间（无时区信息）。
    
    这是 convert_to_beijing_time 的别名，提供简洁的 API。
    
    参数：
        utc_dt: UTC datetime 对象
    
    返回值：
        datetime: 北京时间的 datetime 对象（不带时区信息）
    """
    return convert_to_beijing_time(utc_dt)
