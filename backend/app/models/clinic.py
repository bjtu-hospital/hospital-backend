from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base


class Clinic(Base):
    """门诊地点信息表"""
    __tablename__ = "clinic"
    
    clinic_id = Column(Integer, primary_key=True, autoincrement=True, comment="门诊唯一 ID")
    area_id = Column(Integer, ForeignKey("hospital_area.area_id"), nullable=False, comment="外键，关联 hospital_area.area_id")
    name = Column(String(100), nullable=False, comment="门诊名称")
    address = Column(String(255), nullable=True, comment="门诊具体位置描述 (如: 3号楼2层)")
    create_time = Column(DateTime, default=None, comment="创建时间")
    minor_dept_id = Column(Integer, ForeignKey("minor_department.minor_dept_id"), nullable=True)
    clinic_type = Column(Integer, nullable=False, default=0, comment="门诊类型: 0-普通, 1-国疗, 2-特需")
    
    # 关系字段
    hospital_area = relationship("HospitalArea", back_populates="clinics")
    minor_department = relationship("MinorDepartment", back_populates="clinics")
    schedules = relationship("Schedule", back_populates="clinic")
    schedule_audits = relationship("ScheduleAudit", back_populates="clinic")
    
    
