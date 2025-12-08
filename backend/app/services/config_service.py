"""
配置服务 - 支持分级配置读取 (GLOBAL > CLINIC > DOCTOR)
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Optional, Dict, Any, Union
import logging
from decimal import Decimal, ROUND_HALF_UP

from app.models.system_config import SystemConfig

logger = logging.getLogger(__name__)


def calculate_final_price(
    base_price: Union[float, Decimal],
    discount_rate: Union[float, Decimal] = 1.0
) -> Decimal:
    """
    计算最终价格（应用折扣），并精确到小数点后2位
    
    参数:
    - base_price: 基础价格 (float 或 Decimal)
    - discount_rate: 折扣率 (0.0-1.0)，默认 1.0（无折扣）
    
    返回:
    - Decimal: 精确到小数点后2位的最终价格
    """
    try:
        # 转换为 Decimal 以避免浮点精度问题
        if not isinstance(base_price, Decimal):
            base_price = Decimal(str(base_price))
        
        if not isinstance(discount_rate, Decimal):
            discount_rate = Decimal(str(discount_rate))
        
        # 计算最终价格
        final_price = base_price * discount_rate
        
        # 精确到小数点后2位（四舍五入）
        final_price = final_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        
        return final_price
    except Exception as e:
        logger.error(f"计算价格失败: base_price={base_price}, discount_rate={discount_rate}, error={e}")
        # 如果转换失败，返回基础价格
        if isinstance(base_price, Decimal):
            return base_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return Decimal(str(base_price)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)



async def get_config_value(
    db: AsyncSession,
    config_key: str,
    scope_type: str = "GLOBAL",
    scope_id: Optional[int] = None,
    fallback_to_global: bool = True
) -> Optional[Any]:
    """
    获取配置值,支持分级查询
    
    参数:
    - config_key: 配置键名
    - scope_type: 配置范围类型 (GLOBAL/CLINIC/DOCTOR/MINOR_DEPT)
    - scope_id: 范围ID
    - fallback_to_global: 如果指定范围未找到配置,是否回退到全局配置
    
    返回:
    - 配置值(JSON格式)或None
    """
    try:
        # 首先尝试查询指定范围的配置
        if scope_type != "GLOBAL" and scope_id is not None:
            result = await db.execute(
                select(SystemConfig).where(
                    and_(
                        SystemConfig.config_key == config_key,
                        SystemConfig.scope_type == scope_type,
                        SystemConfig.scope_id == scope_id,
                        SystemConfig.is_active == True
                    )
                )
            )
            config = result.scalar_one_or_none()
            if config:
                logger.debug(f"找到 {scope_type}:{scope_id} 级别的配置: {config_key}")
                return config.config_value
        
        # 如果需要回退到全局配置
        if fallback_to_global:
            result = await db.execute(
                select(SystemConfig).where(
                    and_(
                        SystemConfig.config_key == config_key,
                        SystemConfig.scope_type == "GLOBAL",
                        SystemConfig.is_active == True
                    )
                )
            )
            config = result.scalar_one_or_none()
            if config:
                logger.debug(f"使用全局配置: {config_key}")
                return config.config_value
        
        logger.warning(f"未找到配置: {config_key} (scope={scope_type}:{scope_id})")
        return None
        
    except Exception as e:
        logger.error(f"获取配置失败: {config_key}, 错误: {str(e)}")
        return None


async def get_registration_config(
    db: AsyncSession,
    scope_type: str = "GLOBAL",
    scope_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    获取挂号配置
    
    返回默认值或数据库配置
    """
    config = await get_config_value(
        db, 
        config_key="registration",
        scope_type=scope_type,
        scope_id=scope_id,
        fallback_to_global=True
    )
    
    # 默认配置
    default_config = {
        "advanceBookingDays": 14,  # 提前14天
        "sameDayDeadline": "08:00",  # 当日挂号截止时间
        "noShowLimit": 3,  # 爽约次数限制
        "cancelHoursBefore": 2,  # 取消提前小时数
        "sameClinicInterval": 7,  # 同科室挂号间隔天数
        "maxAppointmentsPerPeriod": 10,  # 时间段内最大预约数
        "appointmentPeriodDays": 8  # 预约限制时间段(天)
    }
    
    if config:
        # 合并配置,数据库配置覆盖默认配置
        return {**default_config, **config}
    
    return default_config


async def get_schedule_config(
    db: AsyncSession,
    scope_type: str = "GLOBAL",
    scope_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    获取排班配置
    
    返回默认值或数据库配置
    """
    config = await get_config_value(
        db,
        config_key="schedule",
        scope_type=scope_type,
        scope_id=scope_id,
        fallback_to_global=True
    )
    
    # 默认配置
    default_config = {
        "maxFutureDays": 60,
        "morningStart": "08:00",
        "morningEnd": "12:00",
        "afternoonStart": "13:30",
        "afternoonEnd": "17:30",
        "eveningStart": "18:00",
        "eveningEnd": "21:00",
        "consultationDuration": 15,
        "intervalTime": 5
    }
    
    if config:
        return {**default_config, **config}
    
    return default_config


async def get_department_head_config(
    db: AsyncSession,
    scope_type: str = "GLOBAL",
    scope_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    获取科室长配置
    
    返回默认值或数据库配置
    """
    config = await get_config_value(
        db,
        config_key="departmentHeadMaxCount",
        scope_type=scope_type,
        scope_id=scope_id,
        fallback_to_global=True
    )
    
    # 默认配置
    default_max_count = 2
    
    if config is not None:
        try:
            return {"maxCount": int(config)}
        except (ValueError, TypeError):
            logger.warning(f"科室长配置值无效: {config}, 使用默认值")
            return {"maxCount": default_max_count}
    
    return {"maxCount": default_max_count}


async def get_patient_identity_discounts(
    db: AsyncSession,
    scope_type: str = "GLOBAL",
    scope_id: Optional[int] = None
) -> Dict[str, float]:
    """
    获取患者身份折扣配置
    
    返回格式:
    {
        "学生": 0.8,
        "教师": 0.8,
        "职工": 0.8,
        "校外": 1.0
    }
    """
    config = await get_config_value(
        db,
        config_key="patientIdentityDiscounts",
        scope_type=scope_type,
        scope_id=scope_id,
        fallback_to_global=True
    )
    
    # 默认折扣配置
    default_discounts = {
        "学生": 0.8,
        "教师": 0.8,
        "职工": 0.8,
        "校外": 1.0
    }
    
    if config and isinstance(config, dict):
        # 验证折扣值合法性
        try:
            validated_config = {}
            for key, value in config.items():
                discount = float(value)
                if 0 < discount <= 1.0:
                    validated_config[key] = discount
                else:
                    logger.warning(f"折扣值无效: {key}={value}, 使用默认值")
                    validated_config[key] = default_discounts.get(key, 1.0)
            return validated_config
        except (ValueError, TypeError) as e:
            logger.warning(f"解析折扣配置失败: {e}, 使用默认值")
            return default_discounts
    
    return default_discounts


def parse_time_to_hour_minute(time_str: str) -> tuple:
    """
    解析时间字符串为小时和分钟
    
    参数:
    - time_str: 格式如 "08:00"
    
    返回:
    - (int, int): (小时, 分钟)
    """
    try:
        parts = time_str.split(":")
        return int(parts[0]), int(parts[1])
    except Exception as e:
        logger.warning(f"解析时间失败: {time_str}, 错误: {e}")
        return 0, 0
