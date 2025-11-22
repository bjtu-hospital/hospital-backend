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


async def bulk_get_doctor_prices(
    db: AsyncSession,
    doctors: list
) -> dict[int, dict]:
    """批量获取医生的挂号价格，避免 N+1 查询
    优先级: DOCTOR > MINOR_DEPT > GLOBAL

    返回: { doctor_id: {default_price_normal, default_price_expert, default_price_special} }
    未配置的字段填 None。
    """
    if not doctors:
        return {}

    from sqlalchemy import or_  # local import to keep top clean

    doctor_ids = [d.doctor_id for d in doctors]
    dept_ids = list({d.dept_id for d in doctors})

    # 查询所有相关的配置 (一次往返)
    query = select(SystemConfig).where(
        and_(
            SystemConfig.config_key == "registration.price",
            SystemConfig.is_active == True,  # noqa: E712
            or_(
                and_(SystemConfig.scope_type == "DOCTOR", SystemConfig.scope_id.in_(doctor_ids)),
                and_(SystemConfig.scope_type == "MINOR_DEPT", SystemConfig.scope_id.in_(dept_ids)),
                and_(SystemConfig.scope_type == "GLOBAL", SystemConfig.scope_id.is_(None))
            )
        )
    )
    result = await db.execute(query)
    configs = result.scalars().all()

    doctor_level = {}
    dept_level = {}
    global_level = None
    for cfg in configs:
        if cfg.scope_type == "DOCTOR":
            doctor_level[cfg.scope_id] = cfg.config_value or {}
        elif cfg.scope_type == "MINOR_DEPT":
            dept_level[cfg.scope_id] = cfg.config_value or {}
        elif cfg.scope_type == "GLOBAL":
            global_level = cfg.config_value or {}

    def extract(cfg_dict: dict | None) -> dict:
        if not cfg_dict:
            return {
                "default_price_normal": None,
                "default_price_expert": None,
                "default_price_special": None
            }
        return {
            "default_price_normal": float(cfg_dict["default_price_normal"]) if cfg_dict.get("default_price_normal") is not None else None,
            "default_price_expert": float(cfg_dict["default_price_expert"]) if cfg_dict.get("default_price_expert") is not None else None,
            "default_price_special": float(cfg_dict["default_price_special"]) if cfg_dict.get("default_price_special") is not None else None,
        }

    global_prices = extract(global_level)

    price_map: dict[int, dict] = {}
    for d in doctors:
        # 层级覆盖: 先全局，再科室，再医生
        merged = dict(global_prices)
        dept_cfg = extract(dept_level.get(d.dept_id))
        for k, v in dept_cfg.items():
            if v is not None:
                merged[k] = v
        doc_cfg = extract(doctor_level.get(d.doctor_id))
        for k, v in doc_cfg.items():
            if v is not None:
                merged[k] = v
        price_map[d.doctor_id] = merged

    return price_map


async def bulk_get_clinic_prices(db: AsyncSession, clinics: list) -> dict[int, dict]:
    """
    批量获取多个门诊的价格配置 (避免 N+1 查询)
    返回 {clinic_id: {"default_price_normal": float|None, ...}}
    优先级: CLINIC > MINOR_DEPT > GLOBAL
    """
    from sqlalchemy import or_

    if not clinics:
        return {}

    clinic_ids = [c.clinic_id for c in clinics]
    dept_ids = list({c.minor_dept_id for c in clinics if c.minor_dept_id})

    # 一次查询所有相关配置
    query = select(SystemConfig).where(
        and_(
            SystemConfig.config_key == "registration.price",
            SystemConfig.is_active == True,  # noqa: E712
            or_(
                and_(SystemConfig.scope_type == "CLINIC", SystemConfig.scope_id.in_(clinic_ids)),
                and_(SystemConfig.scope_type == "MINOR_DEPT", SystemConfig.scope_id.in_(dept_ids)) if dept_ids else False,
                and_(SystemConfig.scope_type == "GLOBAL", SystemConfig.scope_id.is_(None))
            )
        )
    )
    result = await db.execute(query)
    configs = result.scalars().all()

    clinic_level = {}
    dept_level = {}
    global_level = None
    for cfg in configs:
        if cfg.scope_type == "CLINIC":
            clinic_level[cfg.scope_id] = cfg.config_value or {}
        elif cfg.scope_type == "MINOR_DEPT":
            dept_level[cfg.scope_id] = cfg.config_value or {}
        elif cfg.scope_type == "GLOBAL":
            global_level = cfg.config_value or {}

    def extract(cfg_dict: dict | None) -> dict:
        if not cfg_dict:
            return {
                "default_price_normal": None,
                "default_price_expert": None,
                "default_price_special": None
            }
        return {
            "default_price_normal": float(cfg_dict["default_price_normal"]) if cfg_dict.get("default_price_normal") is not None else None,
            "default_price_expert": float(cfg_dict["default_price_expert"]) if cfg_dict.get("default_price_expert") is not None else None,
            "default_price_special": float(cfg_dict["default_price_special"]) if cfg_dict.get("default_price_special") is not None else None,
        }

    global_prices = extract(global_level)

    price_map: dict[int, dict] = {}
    for c in clinics:
        # 层级覆盖: GLOBAL -> MINOR_DEPT -> CLINIC
        merged = dict(global_prices)
        if c.minor_dept_id:
            dept_cfg = extract(dept_level.get(c.minor_dept_id))
            for k, v in dept_cfg.items():
                if v is not None:
                    merged[k] = v
        clinic_cfg = extract(clinic_level.get(c.clinic_id))
        for k, v in clinic_cfg.items():
            if v is not None:
                merged[k] = v
        price_map[c.clinic_id] = merged

    return price_map


async def bulk_get_minor_dept_prices(db: AsyncSession, departments: list) -> dict[int, dict]:
    """
    批量获取多个小科室的价格配置 (避免 N+1 查询)
    返回 {minor_dept_id: {"default_price_normal": float|None, ...}}
    优先级: MINOR_DEPT > GLOBAL
    """
    from sqlalchemy import or_

    if not departments:
        return {}

    dept_ids = [d.minor_dept_id for d in departments]

    # 一次查询所有相关配置
    query = select(SystemConfig).where(
        and_(
            SystemConfig.config_key == "registration.price",
            SystemConfig.is_active == True,  # noqa: E712
            or_(
                and_(SystemConfig.scope_type == "MINOR_DEPT", SystemConfig.scope_id.in_(dept_ids)),
                and_(SystemConfig.scope_type == "GLOBAL", SystemConfig.scope_id.is_(None))
            )
        )
    )
    result = await db.execute(query)
    configs = result.scalars().all()

    dept_level = {}
    global_level = None
    for cfg in configs:
        if cfg.scope_type == "MINOR_DEPT":
            dept_level[cfg.scope_id] = cfg.config_value or {}
        elif cfg.scope_type == "GLOBAL":
            global_level = cfg.config_value or {}

    def extract(cfg_dict: dict | None) -> dict:
        if not cfg_dict:
            return {
                "default_price_normal": None,
                "default_price_expert": None,
                "default_price_special": None
            }
        return {
            "default_price_normal": float(cfg_dict["default_price_normal"]) if cfg_dict.get("default_price_normal") is not None else None,
            "default_price_expert": float(cfg_dict["default_price_expert"]) if cfg_dict.get("default_price_expert") is not None else None,
            "default_price_special": float(cfg_dict["default_price_special"]) if cfg_dict.get("default_price_special") is not None else None,
        }

    global_prices = extract(global_level)

    price_map: dict[int, dict] = {}
    for d in departments:
        # 层级覆盖: GLOBAL -> MINOR_DEPT
        merged = dict(global_prices)
        dept_cfg = extract(dept_level.get(d.minor_dept_id))
        for k, v in dept_cfg.items():
            if v is not None:
                merged[k] = v
        price_map[d.minor_dept_id] = merged

    return price_map


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
