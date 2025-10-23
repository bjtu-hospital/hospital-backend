from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.orm import relationship
from app.db.base import Base


class MajorDepartment(Base):
    """大科室表"""
    __tablename__ = "major_department"
    
    major_dept_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    create_time = Column(DateTime, default=None)
    
    # 关系字段
    minor_departments = relationship("MinorDepartment", back_populates="major_department")
