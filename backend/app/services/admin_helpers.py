from sqlalchemy import select, and_
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.system_config import SystemConfig
from app.db.base import Administrator
from app.core.exception_handler import AuthHTTPException, BusinessHTTPException, ResourceHTTPException
from app.core.config import settings
from datetime import datetime, date, timedelta


async def get_hierarchical_price(
    db: AsyncSession,
    slot_type: str,
    doctor_id: int | None = None,
    clinic_id: int | None = None,
    minor_dept_id: int | None = None
) -> float | None:
    """
    分级查询挂号价格配置
    优先级: DOCTOR > CLINIC > MINOR_DEPT > GLOBAL
    """
    price_field_map = {
        "普通": "default_price_normal",
        "专家": "default_price_expert",
        "特需": "default_price_special"
    }

    price_field = price_field_map.get(slot_type)
    if not price_field:
        return None

    search_order = []
    if doctor_id:
        search_order.append(("DOCTOR", doctor_id))
    if clinic_id:
        search_order.append(("CLINIC", clinic_id))
    if minor_dept_id:
        search_order.append(("MINOR_DEPT", minor_dept_id))
    search_order.append(("GLOBAL", None))

    for scope_type, scope_id in search_order:
        query = select(SystemConfig).where(
            and_(
                SystemConfig.config_key == "registration.price",
                SystemConfig.scope_type == scope_type,
                SystemConfig.is_active == True
            )
        )

        if scope_type == "GLOBAL":
            query = query.where(SystemConfig.scope_id.is_(None))
        else:
            query = query.where(SystemConfig.scope_id == scope_id)

        result = await db.execute(query)
        config = result.scalar_one_or_none()

        if config and config.config_value:
            price_value = config.config_value.get(price_field)
            if price_value is not None:
                return float(price_value)

    return None


async def get_entity_prices(
    db: AsyncSession,
    scope_type: str,
    scope_id: int | None
) -> dict:
    query = select(SystemConfig).where(
        and_(
            SystemConfig.config_key == "registration.price",
            SystemConfig.scope_type == scope_type,
            SystemConfig.is_active == True
        )
    )

    if scope_type == "GLOBAL":
        query = query.where(SystemConfig.scope_id.is_(None))
    else:
        query = query.where(SystemConfig.scope_id == scope_id)

    result = await db.execute(query)
    config = result.scalar_one_or_none()

    if config and config.config_value:
        return {
            "default_price_normal": float(config.config_value["default_price_normal"]) if config.config_value.get("default_price_normal") is not None else None,
            "default_price_expert": float(config.config_value["default_price_expert"]) if config.config_value.get("default_price_expert") is not None else None,
            "default_price_special": float(config.config_value["default_price_special"]) if config.config_value.get("default_price_special") is not None else None
        }

    return {
        "default_price_normal": None,
        "default_price_expert": None,
        "default_price_special": None
    }


async def update_entity_prices(
    db: AsyncSession,
    scope_type: str,
    scope_id: int | None,
    default_price_normal: float | None = None,
    default_price_expert: float | None = None,
    default_price_special: float | None = None
) -> None:
    query = select(SystemConfig).where(
        and_(
            SystemConfig.config_key == "registration.price",
            SystemConfig.scope_type == scope_type
        )
    )

    if scope_type == "GLOBAL":
        query = query.where(SystemConfig.scope_id.is_(None))
    else:
        query = query.where(SystemConfig.scope_id == scope_id)

    result = await db.execute(query)
    config = result.scalar_one_or_none()

    new_config_value = {}
    if config and config.config_value:
        new_config_value = dict(config.config_value)

    if default_price_normal is not None:
        new_config_value["default_price_normal"] = default_price_normal
    if default_price_expert is not None:
        new_config_value["default_price_expert"] = default_price_expert
    if default_price_special is not None:
        new_config_value["default_price_special"] = default_price_special

    if config:
        config.config_value = new_config_value
        config.update_time = datetime.now()
        flag_modified(config, "config_value")
        # caller should add/commit
    else:
        entity_desc_map = {
            "GLOBAL": "全局",
            "MINOR_DEPT": f"小科室{scope_id}",
            "CLINIC": f"诊室{scope_id}",
            "DOCTOR": f"医生{scope_id}"
        }

        new_config = SystemConfig(
            config_key="registration.price",
            scope_type=scope_type,
            scope_id=scope_id,
            config_value=new_config_value,
            data_type="JSON",
            description=f"{entity_desc_map.get(scope_type, '')}挂号费用配置",
            is_active=True
        )
        db.add(new_config)

    await db.commit()


def _weekday_to_cn(week_day: int) -> str:
    mapping = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}
    return mapping.get(week_day, "")


def _slot_type_to_str(slot_type_enum) -> str:
    return slot_type_enum.value if hasattr(slot_type_enum, "value") else str(slot_type_enum)


def _str_to_slot_type(value: str):
    from app.models.schedule import SlotType
    for member in SlotType:
        if member.value == value:
            return member
    raise BusinessHTTPException(
        code=settings.REQ_ERROR_CODE,
        msg="无效的号源类型，应为 普通/专家/特需",
        status_code=400
    )


async def get_administrator_id(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        select(Administrator.admin_id).where(Administrator.user_id == user_id)
    )
    admin_id = result.scalar_one_or_none()
    if not admin_id:
        raise AuthHTTPException(
            code=settings.INSUFFICIENT_AUTHORITY_CODE,
            msg="管理员身份异常，未找到对应的管理员档案。",
            status_code=403
        )
    return admin_id


def calculate_leave_days(start_date: date, end_date: date) -> int:
    return (end_date - start_date).days + 1
