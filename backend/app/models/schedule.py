from sqlalchemy import Column, BigInteger, Integer, String, Date, Enum, DateTime, ForeignKey, Numeric
from sqlalchemy.orm import relationship
from app.db.base import Base
import enum


# 定义号源类型枚举
class SlotType(enum.Enum):
    NORMAL = "普通"
    SPECIAL = "特需"
    EXPERT = "专家"


class Schedule(Base):
    """医生出诊排班及号源管理表 (含号源类型)"""
    __tablename__ = "schedule"
    
    schedule_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="排班记录唯一 ID")
    doctor_id = Column(Integer, ForeignKey("doctor.doctor_id"), nullable=False, comment="外键，关联 doctor.doctor_id")
    clinic_id = Column(Integer, ForeignKey("clinic.clinic_id"), nullable=False, comment="外键，关联 clinic.clinic_id")
    date = Column(Date, nullable=False, comment="具体出诊日期")
    week_day = Column(Integer, nullable=False, comment="星期几 (1=周一, 7=周日)")
    time_section = Column(String(20), nullable=False, comment="时间段 (上午/下午/晚上)")
    slot_type = Column(Enum(SlotType, values_callable=lambda x: [e.value for e in x]), nullable=False, comment="号源类型：普通、特需、专家")
    total_slots = Column(Integer, nullable=False, default=0, comment="预设总号源数")
    remaining_slots = Column(Integer, nullable=False, default=0, comment="当前剩余号源数")
    status = Column(String(20), nullable=True, default="正常", comment="排班状态 (如：正常、停诊)")
    create_time = Column(DateTime, default=None, comment="创建时间")
    price = Column(Numeric(10, 2), nullable=False, default=0.00, comment="挂号原价 (单位: 元)")
    
    # 关系字段
    doctor = relationship("Doctor", back_populates="schedules")
    clinic = relationship("Clinic", back_populates="schedules")
    add_slot_audits = relationship("AddSlotAudit", back_populates="schedule")
    attendance_records = relationship("AttendanceRecord", back_populates="schedule")


