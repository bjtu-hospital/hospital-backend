from sqlalchemy import Column, Integer, String, DateTime, Date, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import UniqueConstraint # 导入 UniqueConstraint
from app.db.base import Base
from app.core.datetime_utils import beijing_now_for_model

# 排班申请表模型 (ScheduleAudit)
class ScheduleAudit(Base):
    """
    诊室排班申请审核表模型 (schedule_audit)
    """
    __tablename__ = "schedule_audit"

    # 基础信息
    audit_id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment='排班申请ID')
    minor_dept_id = Column(Integer, ForeignKey('minor_department.minor_dept_id'), nullable=False, comment='申请科室ID')
    clinic_id = Column(Integer, ForeignKey('clinic.clinic_id'), nullable=False, comment='排班申请关联的诊室ID') # 新增
    submitter_doctor_id = Column(Integer, ForeignKey('doctor.doctor_id'), nullable=False, comment='排班提交人ID')
    
    # 排班周期
    week_start_date = Column(Date, nullable=False, comment='排班周的起始日期')
    week_end_date = Column(Date, nullable=False, comment='排班周的结束日期')
    
    # 排班数据 (JSON 结构)
    schedule_data_json = Column(JSON, nullable=False, comment='排班计划详情的JSON数据') 
    
    # 提交和备注
    submit_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment='提交申请时间')
    remark = Column(String(255), comment='提交备注')
    
    # 审核信息
    status = Column(String(20), default='pending', nullable=False, comment='审核状态: pending, approved, rejected')
    auditor_user_id = Column(Integer, ForeignKey('user.user_id'), comment='审核人User ID(可以是管理员或科室长)')
    audit_time = Column(DateTime, comment='审核时间')
    audit_remark = Column(Text, comment='审核备注/原因')

    # 唯一约束：确保一个诊室在同一周内没有重复的排班申请
    __table_args__ = (
        UniqueConstraint('clinic_id', 'week_start_date', name='uk_clinic_week'),
    )
    
    clinic = relationship("Clinic", back_populates="schedule_audits")
    minor_department = relationship("MinorDepartment", back_populates="schedule_audits")
    doctor = relationship("Doctor", back_populates="schedule_audits")
    auditor = relationship("User", foreign_keys=[auditor_user_id], back_populates="audited_schedule_audits")