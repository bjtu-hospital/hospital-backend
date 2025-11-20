from sqlalchemy import Column, BigInteger, Integer, String, Date, DateTime, Boolean, Text, Enum, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base
import enum
from datetime import datetime


class OrderStatus(enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    WAITLIST = "waitlist"


class RegistrationOrder(Base):
    """
    患者挂号订单表：
    - patient_id: 关联 Patient.patient_id
    - user_id: 关联 User.user_id（冗余，便于查询）
    - doctor_id: 关联 Doctor.doctor_id
    - schedule_id: 可选，关联具体的 Schedule.schedule_id（如果用户选定了具体排班）
    - slot_date / time_section: 存储就诊的日期与时段（与 schedule 冗余，便于查询与历史记录）
    - visit_times: 文本(JSON 数组)，用于存储一系列就诊时间（为将来扩展复诊或多次挂号场景）
    - is_waitlist, waitlist_position: 预留候补挂号支持字段
    - status: 订单状态（枚举）
    """
    __tablename__ = "registration_order"

    order_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="挂号订单ID")
    patient_id = Column(BigInteger, ForeignKey("patient.patient_id"), nullable=False, comment="关联 patient.patient_id")
    user_id = Column(Integer, ForeignKey("user.user_id"), nullable=True, comment="关联 user.user_id，冗余字段")
    doctor_id = Column(Integer, ForeignKey("doctor.doctor_id"), nullable=False, comment="关联 doctor.doctor_id")
    schedule_id = Column(BigInteger, ForeignKey("schedule.schedule_id"), nullable=True, comment="可选：关联具体排班")

    slot_date = Column(Date, nullable=False, comment="预约日期")
    time_section = Column(String(20), nullable=True, comment="预约时段，如: 上午/下午/晚间")

    # 号别/挂号类型（如：普通/专家/特需/加号/候补等）
    slot_type = Column(String(50), nullable=True, comment="挂号号别/类型，例如: 普通/专家/特需/加号/候补")

    # 允许存储多个就诊时间的扩展字段（JSON 数组的字符串表现形式）
    visit_times = Column(Text, nullable=True, comment="JSON 字符串：用于存储一系列就诊时间")

    # 候补相关字段（为后续扩展保留）
    is_waitlist = Column(Boolean, default=False, comment="是否为候补挂号")
    waitlist_position = Column(Integer, nullable=True, comment="候补队列中的位置（1 表示队首）")

    # 接诊队列相关字段
    pass_count = Column(Integer, default=0, nullable=False, comment="过号次数，用于队列排序")
    call_time = Column(DateTime, nullable=True, comment="最近一次叫号时间")
    is_calling = Column(Boolean, default=False, nullable=False, comment="是否正在就诊中（已叫号未完成）")
    priority = Column(Integer, default=0, nullable=False, comment="优先级（加号插队用，负数更优先）")

    status = Column(
        Enum(OrderStatus, values_callable=lambda e: [v.value for v in e], name="orderstatus", native_enum=False),
        default=OrderStatus.PENDING,
        nullable=False,
        comment="订单状态"
    )

    notes = Column(Text, nullable=True, comment="订单备注/特殊说明")

    create_time = Column(DateTime, default=datetime.utcnow, comment="创建时间")
    update_time = Column(DateTime, default=datetime.utcnow, comment="最后更新时间")

    # 关系（便于 ORM 查询）
    patient = relationship("Patient")
    user = relationship("User")
    doctor = relationship("Doctor")
    schedule = relationship("Schedule")
