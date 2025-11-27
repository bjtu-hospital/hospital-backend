from pydantic import BaseModel, Field
from typing import List, Optional, Any
from datetime import datetime, date

# ================================== 审核通用模型 ==================================

class AuditAction(BaseModel):
    """审核动作的请求体：通过或拒绝时的备注"""
    comment: Optional[str] = Field(None, description="审核备注/原因")


class AuditActionResponse(BaseModel):
    """审核动作的响应体"""
    audit_id: int = Field(..., description="审核记录ID")
    status: str = Field(..., description="新的审核状态: approved 或 rejected")
    auditor_id: Optional[int] = Field(None, description="审核人管理员ID")
    audit_time: Optional[datetime] = Field(None, description="审核时间")


# ================================== 排班审核模型 ==================================

class ScheduleDoctorInfo(BaseModel):
    """排班详情中单个时段的医生信息"""
    doctor_id: int
    doctor_name: str

class ScheduleAuditItem(BaseModel):
    """排班审核列表或详情中的单个项目"""
    id: int = Field(..., description="排班申请ID")
    department_id: int = Field(..., description="申请科室ID")
    department_name: str = Field(..., description="申请科室名称")
    clinic_id: int = Field(..., description="关联诊室ID")
    clinic_name: str = Field(..., description="关联诊室名称")
    submitter_id: int = Field(..., description="排班提交人ID (医生)")
    submitter_name: str = Field(..., description="排班提交人姓名")
    submit_time: datetime = Field(..., description="提交申请时间")
    week_start: date = Field(..., description="排班周的起始日期")
    week_end: date = Field(..., description="排班周的结束日期")
    remark: Optional[str] = Field(None, description="提交备注")
    status: str = Field(..., description="审核状态: pending, approved, rejected")
    auditor_id: Optional[int] = Field(None, description="审核人管理员ID")
    audit_time: Optional[datetime] = Field(None, description="审核时间")
    audit_remark: Optional[str] = Field(None, description="审核备注/原因")
    # schedule 为 7x3 的医生排班列表
    schedule: List[List[Optional[ScheduleDoctorInfo]]] = Field(..., description="排班计划详情的JSON数据 (7天x3时段)")


class ScheduleAuditListResponse(BaseModel):
    """获取排班审核列表的响应体"""
    audits: List[ScheduleAuditItem]


# ================================== 请假审核模型 ==================================

class LeaveAuditItem(BaseModel):
    """请假审核列表或详情中的单个项目"""
    id: int = Field(..., description="请假申请ID")
    doctor_id: int = Field(..., description="申请请假的医生ID")
    doctor_name: str = Field(..., description="医生姓名")
    doctor_title: str = Field(..., description="医生职称")
    department_name: str = Field(..., description="所属科室名称")
    leave_start_date: date = Field(..., description="请假起始日期")
    leave_end_date: date = Field(..., description="请假结束日期")
    leave_days: int = Field(..., description="请假总天数") # 需要在后端计算
    reason: str = Field(..., description="请假详细原因")
    reason_preview: str = Field(..., description="请假原因预览 (截取)")
    attachments: List[str] = Field(default_factory=list, description="附件路径列表（字符串）")
    submit_time: datetime = Field(..., description="提交申请时间")
    status: str = Field(..., description="审核状态: pending, approved, rejected")
    auditor_id: Optional[int] = Field(None, description="审核人管理员ID")
    audit_time: Optional[datetime] = Field(None, description="审核时间")
    audit_remark: Optional[str] = Field(None, description="审核备注/原因")


class LeaveAuditListResponse(BaseModel):
    """获取请假审核列表的响应体"""
    audits: List[LeaveAuditItem]