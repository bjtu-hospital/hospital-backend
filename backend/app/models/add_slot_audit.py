import datetime
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON
from app.db.base import Base
from app.core.datetime_utils import get_now_naive


class AddSlotAudit(Base):
    """
    加号申请审核表模型 (add_slot_audit)
    当医生需要为某个患者在指定排班上加号时，创建此记录并等待管理员审核；管理员也可直接执行加号。
    """
    __tablename__ = "add_slot_audit"

    audit_id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment='加号申请ID')
    schedule_id = Column(BigInteger, ForeignKey('schedule.schedule_id'), nullable=False, comment='目标排班ID')
    doctor_id = Column(Integer, ForeignKey('doctor.doctor_id'), nullable=False, comment='申请加号的医生ID')
    patient_id = Column(BigInteger, ForeignKey('patient.patient_id'), nullable=False, comment='获得加号的患者的 patient_id')
    slot_type = Column(String(32), nullable=False, comment='号源类型: 普通/专家/特需')
    reason = Column(Text, nullable=True, comment='申请理由（医生填写）')
    applicant_id = Column(Integer, ForeignKey('user.user_id'), nullable=False, comment='发起申请的用户ID')

    submit_time = Column(DateTime, default=get_now_naive, nullable=False, comment='提交时间')
    status = Column(String(20), default='pending', nullable=False, comment='审核状态: pending, approved, rejected')
    auditor_user_id = Column(Integer, ForeignKey('user.user_id'), comment='审核人User ID(可以是管理员或科室长)')
    audit_time = Column(DateTime, comment='审核时间')
    audit_remark = Column(Text, comment='审核备注/原因')

    # 关系
    doctor = relationship("Doctor", back_populates="add_slot_audits")
    # 与 Schedule 的关系
    schedule = relationship("Schedule", back_populates="add_slot_audits")
    # 申请发起人（User）和患者（User），因均关联到 user.user_id，需要指定 foreign_keys
    applicant = relationship("User", foreign_keys=[applicant_id], back_populates="add_slot_applications")
    # 指向 Patient 而非 User，便于直接访问患者业务 ID
    patient = relationship("Patient", foreign_keys=[patient_id], back_populates="add_slot_received")
    # 审核人（User）
    auditor = relationship("User", foreign_keys=[auditor_user_id], back_populates="audited_add_slot_audits")
