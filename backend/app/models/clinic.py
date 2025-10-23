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
    
    # 关系字段
    hospital_area = relationship("HospitalArea", back_populates="clinics")
    schedules = relationship("Schedule", back_populates="clinic")
