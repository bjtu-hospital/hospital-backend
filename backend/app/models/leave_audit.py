import datetime
from sqlalchemy import Column, Integer, String, DateTime, Date, ForeignKey, Text
from sqlalchemy.orm import relationship
# 引入 JSON 类型，用于存储复杂的排班和附件数据
from sqlalchemy.types import JSON
from sqlalchemy.ext.declarative import declarative_base
from app.db.base import Base

# 请假申请表模型
class LeaveAudit(Base):
    """
    医生请假申请审核表模型 (leave_audit)
    用于存储医生提交的请假申请，等待审核。
    """
    __tablename__ = "leave_audit"

    # 基础信息
    audit_id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment='请假申请ID')
    doctor_id = Column(Integer, ForeignKey('doctor.doctor_id'), nullable=False, comment='申请请假的医生ID')
    
    # 请假详情
    leave_start_date = Column(Date, nullable=False, comment='请假起始日期')
    leave_end_date = Column(Date, nullable=False, comment='请假结束日期')
    shift = Column(String(16), default='full', nullable=False, comment='请假时段: morning/afternoon/night/full')
    reason = Column(Text, nullable=False, comment='请假详细原因')
    
    # 附件数据
    # 存储附件列表的 JSON 数据结构，例如：[{"url": "...", "name": "..."}, ...]
    attachment_data_json = Column(JSON, comment='附件信息列表的JSON数据') 
    
    # 提交和审核信息
    submit_time = Column(DateTime, default=datetime.datetime.now, nullable=False, comment='提交申请时间')
    
    status = Column(String(20), default='pending', nullable=False, comment='审核状态: pending, approved, rejected')
    auditor_user_id = Column(Integer, ForeignKey('user.user_id'), comment='审核人User ID(可以是管理员或科室长)')
    audit_time = Column(DateTime, comment='审核时间')
    audit_remark = Column(Text, comment='审核备注/原因')

    # 关系
    doctor = relationship("Doctor", back_populates="leave_audits")
    auditor = relationship("User", foreign_keys=[auditor_user_id], back_populates="audited_leave_audits")
    