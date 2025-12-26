"""患者就诊人关系表模型

用于管理患者之间的就诊人关系，允许一个用户为其他患者（如家人）进行预约挂号。

业务场景：
- 用户A可以添加患者B作为就诊人
- 用户A可以为患者B进行预约挂号
- 这是单向关系：A添加B不代表B也添加了A

示例：
- 张三(patient_id=1, user_id=100) 添加 李四(patient_id=2) 为就诊人
- 张三可以为李四预约挂号
- 李四不能为张三预约（除非李四也添加张三为就诊人）
"""

from sqlalchemy import Column, BigInteger, Integer, String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.core.datetime_utils import beijing_now_for_model


class PatientRelation(Base):
    """患者就诊人关系表
    
    记录用户与其就诊人的关系，支持为家人、朋友等代为预约
    """
    __tablename__ = "patient_relation"
    
    # 主键
    relation_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="关系记录ID")
    
    # 关系双方
    user_patient_id = Column(
        BigInteger, 
        ForeignKey("patient.patient_id", ondelete="CASCADE"), 
        nullable=False, 
        comment="当前用户的患者ID（添加关系的人）"
    )
    related_patient_id = Column(
        BigInteger, 
        ForeignKey("patient.patient_id", ondelete="CASCADE"), 
        nullable=False, 
        comment="被添加为就诊人的患者ID"
    )
    
    # 关系信息
    relation_type = Column(
        String(20), 
        nullable=False, 
        default="其他",
        comment="关系类型：本人/父母/配偶/子女/其他"
    )
    
    # 默认就诊人标记
    is_default = Column(
        Boolean, 
        default=False, 
        nullable=False,
        comment="是否为默认就诊人（每个用户只能有一个默认就诊人）"
    )
    
    # 备注信息（可选）
    remark = Column(String(200), nullable=True, comment="备注信息")
    
    # 时间戳
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=beijing_now_for_model, onupdate=beijing_now_for_model, nullable=False, comment="更新时间")
    
    # 关系字段
    user_patient = relationship(
        "Patient", 
        foreign_keys=[user_patient_id],
        backref="managed_relations"
    )
    related_patient = relationship(
        "Patient", 
        foreign_keys=[related_patient_id],
        backref="relation_sources"
    )
    
    # 索引
    __table_args__ = (
        # 联合唯一索引：同一用户不能重复添加同一就诊人
        Index('idx_user_related_unique', 'user_patient_id', 'related_patient_id', unique=True),
        # 查询某用户的所有就诊人
        Index('idx_user_patient', 'user_patient_id'),
        # 查询某患者被哪些用户添加为就诊人
        Index('idx_related_patient', 'related_patient_id'),
        # 快速查找默认就诊人
        Index('idx_user_default', 'user_patient_id', 'is_default'),
    )
    
    def __repr__(self):
        return f"<PatientRelation(id={self.relation_id}, user={self.user_patient_id}, related={self.related_patient_id}, type={self.relation_type})>"
