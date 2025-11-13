from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base


class Doctor(Base):
    """医院医生基本信息表"""
    __tablename__ = "doctor"
    
    doctor_id = Column(Integer, primary_key=True, autoincrement=True, comment="医生唯一 ID")
    user_id = Column(Integer, ForeignKey("user.user_id"), nullable=True, unique=True, comment="外键，关联 user.user_id")
    dept_id = Column(Integer, ForeignKey("minor_department.minor_dept_id"), nullable=False, comment="外键，关联 minor_department.minor_dept_id")
    name = Column(String(50), nullable=False, comment="医生姓名")
    title = Column(String(100), nullable=True, comment="职称 (如: 主任医师, 教授)")
    specialty = Column(Text, nullable=True, comment="擅长领域 (从简介中提取，便于搜索和展示)")
    introduction = Column(Text, nullable=True, comment="个人简介/描述 (一字不差的完整信息)")
    photo_path = Column(String(255), nullable=True, comment="自建服务器上的图片访问路径")
    original_photo_url = Column(String(255), nullable=True, comment="原医院的完整图片下载URL (用于备份)")
    create_time = Column(DateTime, default=None, comment="记录创建时间")
    
    # 关系字段
    user = relationship("User", back_populates="doctor")
    minor_department = relationship("MinorDepartment", back_populates="doctors")
    schedules = relationship("Schedule", back_populates="doctor")
    schedule_audits = relationship("ScheduleAudit", back_populates="doctor")
    leave_audits = relationship("LeaveAudit", back_populates="doctor")
    add_slot_audits = relationship("AddSlotAudit", back_populates="doctor")
