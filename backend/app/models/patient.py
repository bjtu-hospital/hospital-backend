from sqlalchemy import Column,Integer, BigInteger, String, Boolean, Date, Enum, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base
import enum


# 定义患者类型枚举
class PatientType(enum.Enum):
    STUDENT = "学生"
    TEACHER = "教师"
    STAFF = "职工"


# 定义性别枚举
class Gender(enum.Enum):
    MALE = "男"
    FEMALE = "女"
    UNKNOWN = "未知"


class Patient(Base):
    """患者详细信息表 (校内师生职工)"""
    __tablename__ = "patient"
    
    patient_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="患者业务 ID")
    user_id = Column(Integer, ForeignKey("user.user_id"), nullable=False, unique=True, comment="外键，关联 user.user_id")
    name = Column(String(50), nullable=False, comment="真实姓名")
    gender = Column(Enum(Gender), nullable=False, default=Gender.UNKNOWN, comment="性别")
    birth_date = Column(Date, nullable=True, comment="出生日期")
    patient_type = Column(Enum(PatientType), nullable=False, comment="患者身份类型")
    student_id = Column(String(50), nullable=True, unique=True, comment="学号/工号 (用于认证)")
    is_verified = Column(Boolean, default=False, comment="身份是否已通过管理员审核 (0=否, 1=是)")
    create_time = Column(Date, default=None, comment="创建时间")
    
    # 关系字段
    user = relationship("User", back_populates="patient")

