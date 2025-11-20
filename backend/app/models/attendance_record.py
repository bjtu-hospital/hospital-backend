from sqlalchemy import Column, Integer, BigInteger, String, DateTime, DECIMAL, Enum as SQLEnum, Index, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.base import Base
import enum


class AttendanceStatus(str, enum.Enum):
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    ABSENT = "absent"


class AttendanceRecord(Base):
    __tablename__ = "attendance_record"

    record_id = Column(Integer, primary_key=True, autoincrement=True, comment="考勤记录ID")
    schedule_id = Column(BigInteger, ForeignKey('schedule.schedule_id', ondelete='CASCADE'), nullable=False, comment="排班ID")
    doctor_id = Column(Integer, ForeignKey('doctor.doctor_id', ondelete='CASCADE'), nullable=False, comment="医生ID")
    
    checkin_time = Column(DateTime, nullable=True, comment="签到时间")
    checkin_lat = Column(DECIMAL(10, 7), nullable=True, comment="签到纬度")
    checkin_lng = Column(DECIMAL(10, 7), nullable=True, comment="签到经度")
    
    checkout_time = Column(DateTime, nullable=True, comment="签退时间")
    checkout_lat = Column(DECIMAL(10, 7), nullable=True, comment="签退纬度")
    checkout_lng = Column(DECIMAL(10, 7), nullable=True, comment="签退经度")
    
    work_duration_minutes = Column(Integer, nullable=True, comment="工作时长(分钟)")
    status = Column(SQLEnum(AttendanceStatus), default=AttendanceStatus.CHECKED_IN, nullable=False, comment="考勤状态")
    
    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")

    # 关系
    schedule = relationship("Schedule", back_populates="attendance_records")
    doctor = relationship("Doctor", back_populates="attendance_records")

    __table_args__ = (
        Index('idx_doctor_date', 'doctor_id', 'created_at'),
        Index('idx_schedule', 'schedule_id'),
    )
