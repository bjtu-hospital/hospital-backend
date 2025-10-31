from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base


class MinorDepartment(Base):
    """小科室表"""
    __tablename__ = "minor_department"
    
    minor_dept_id = Column(Integer, primary_key=True, autoincrement=True)
    major_dept_id = Column(Integer, ForeignKey("major_department.major_dept_id"), nullable=False)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    create_time = Column(DateTime, default=None)
    
    # 关系字段
    major_department = relationship("MajorDepartment", back_populates="minor_departments")
    doctors = relationship("Doctor", back_populates="minor_department")
    clinics = relationship("Clinic", back_populates="minor_department")
