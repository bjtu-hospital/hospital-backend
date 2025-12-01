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
    # 使用枚举的 value 存储（例如中文值："男"/"女"/"未知"），
    # 避免 SQLAlchemy 默认按枚举成员名（MALE/FEMALE/UNKNOWN）验证导致与 DB 已有字符串值冲突。
    gender = Column(
        Enum(Gender, values_callable=lambda e: [v.value for v in e], name="gender", native_enum=False),
        nullable=False,
        default=Gender.UNKNOWN,
        comment="性别"
    )
    birth_date = Column(Date, nullable=True, comment="出生日期")
    patient_type = Column(
        Enum(PatientType, values_callable=lambda e: [v.value for v in e], name="patienttype", native_enum=False),
        nullable=False,
        comment="患者身份类型"
    )
    identifier = Column(String(50), nullable=True, unique=True, comment="学号/工号/证件号（用于认证）")
    is_verified = Column(Boolean, default=False, comment="身份是否已通过管理员审核 (0=否, 1=是)")
    create_time = Column(Date, default=None, comment="创建时间")
    
    # 关系字段
    user = relationship("User", back_populates="patient")
    # 收到的加号申请
    add_slot_received = relationship("AddSlotAudit", back_populates="patient")

