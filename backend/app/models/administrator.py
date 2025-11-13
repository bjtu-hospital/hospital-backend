from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base


class Administrator(Base):
    """管理员详细信息表"""
    __tablename__ = "administrator"
    
    admin_id = Column(Integer, primary_key=True, autoincrement=True, comment="管理员业务 ID")
    user_id = Column(Integer, ForeignKey("user.user_id"), nullable=False, unique=True, comment="外键，关联 user.user_id")
    name = Column(String(50), nullable=False, comment="真实姓名")
    job_title = Column(String(50), nullable=True, comment="职务")
    create_time = Column(DateTime, default=None, comment="创建时间")
    
    # 关系字段
    user = relationship("User", back_populates="administrator")
    schedule_audits = relationship("ScheduleAudit", back_populates="administrator")
    leave_audits = relationship("LeaveAudit", back_populates="administrator")
    add_slot_audits = relationship("AddSlotAudit", back_populates="auditor")
