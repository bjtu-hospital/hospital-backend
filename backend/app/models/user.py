from sqlalchemy import Column, Integer, String, Boolean, BigInteger, Text, Enum, DateTime
from sqlalchemy.orm import relationship
from app.db.base import Base
import enum

# 定义用户类型枚举
class UserType(enum.Enum):
    STUDENT = "student"         # 学生
    TEACHER = "teacher"         # 教师
    DOCTOR = "doctor"           # 医生
    ADMIN = "admin"             # 管理员
    EXTERNAL = "external"       # 外部用户/普通用户 (预留)

# USER数据库表类-模型
class User(Base):
    __tablename__ = "user"
    
    # id
    user_id = Column(Integer, primary_key=True, index=True) # 内部主键，升级为BigInteger
    
    # 认证和身份字段
    email = Column(String(255), unique=False, index=True, nullable=True) # 允许不强制使用邮箱
    
    # 重点修改：phonenumber 设为唯一且非空
    phonenumber = Column(String(25), unique=True, index=True, nullable=True, comment="手机号,患者端唯一登入凭证") 
    
    
    # 身份认证字段
    identifier = Column(String(50), unique=True, index=True, nullable=True, comment="学号或工号，用于医生/管理端登入 以及学生/教师认证信息")
    # 将 Enum 存储为枚举的 value（小写字符串），并显式指定 name 以便数据库迁移可识别
    user_type = Column(
        Enum(
            UserType,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            name="usertype",
            native_enum=False,
        ),
        default=UserType.EXTERNAL,
        comment="用户身份类型：学生/教师/管理员等",
    )
    
    # 安全字段
    hashed_password = Column(String(255), nullable=False)
    
    # 状态和权限字段
    is_active = Column(Boolean, default=True, comment="用户是否有效(可被封禁)")
    is_deleted = Column(Boolean, default=False, comment="用户是否被删除")
    
    is_admin = Column(Boolean, default=False, comment="用户是否是超级管理员") 
    
    is_verified = Column(Boolean, default=False, comment="邮箱/手机号/身份信息是否通过验证")
    
    # 登录信息字段
    last_login_ip = Column(String(64), nullable=True) # 最近登录IP
     
    last_login_time = Column(BigInteger, nullable=True) # 最近登录时间（时间戳）
    
    # 创建时间字段
    create_time = Column(DateTime, default=None, comment="创建时间")
    
    # 关系字段
    
    # 与user_access_log表为一对多的关系
    user_access_logs = relationship("UserAccessLog", back_populates = "user")
    
    # 与administrator表为一对一的关系
    administrator = relationship("Administrator", back_populates="user", uselist=False)
    
    # 与doctor表为一对一的关系
    doctor = relationship("Doctor", back_populates="user", uselist=False)
    
    # 与patient表为一对一的关系
    patient = relationship("Patient", back_populates="user", uselist=False)

    # 与加号申请的关系（发起者/患者接收）
    add_slot_applications = relationship("AddSlotAudit", back_populates="applicant", foreign_keys='AddSlotAudit.applicant_id')


# 运行时兼容性帮助：将传入的字符串（如来自 API 的 user_type）映射到 UserType
def parse_user_type(value: str) -> UserType:
    """把可能的字符串值（大小写不确定）解析为 UserType 成员。

    使用示例：
        user_type = parse_user_type(request.json().get('user_type'))
    如果无法解析，将返回 UserType.EXTERNAL 作为默认值。
    """
    if not value:
        return UserType.EXTERNAL
    # 先尝试匹配 value 本身（通常是小写存储值）
    for member in UserType:
        if value == member.value:
            return member
    # 再尝试按 name 大小写匹配（如 'ADMIN'）
    try:
        return UserType[value.upper()]
    except Exception:
        return UserType.EXTERNAL