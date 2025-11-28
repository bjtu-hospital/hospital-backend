"""医生请假相关的 Pydantic Schema"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, datetime
from enum import Enum


class ShiftEnum(str, Enum):
    """时段枚举"""
    MORNING = "morning"
    AFTERNOON = "afternoon"
    NIGHT = "night"
    FULL = "full"


class LeaveStatusEnum(str, Enum):
    """请假状态枚举"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ShiftLeaveStatus(BaseModel):
    """单个时段的请假状态"""
    shift: str = Field(..., description="时段: morning/afternoon/night")
    leaveStatus: Optional[str] = Field(None, description="请假状态: null/pending/approved/rejected")

class DayScheduleItem(BaseModel):
    """单日排班与请假状态"""
    date: str = Field(..., description="日期，格式 YYYY-MM-DD")
    day: int = Field(..., description="日期天数")
    hasShift: bool = Field(..., description="当天是否有排班")
    shiftInfo: Optional[str] = Field(None, description="排班简要描述")
    leaveStatus: Optional[str] = Field(None, description="全天请假状态: null/pending/approved/rejected")
    shiftLeaveStatuses: Optional[List[ShiftLeaveStatus]] = Field(default_factory=list, description="各时段请假状态列表")
    isToday: bool = Field(False, description="是否是今天")


class AttachmentItem(BaseModel):
    """旧格式保留（不再使用）。附件改为字符串路径。"""
    url: str = Field(..., description="附件URL")
    name: Optional[str] = Field(None, description="原始文件名")


class LeaveApplyRequest(BaseModel):
    """提交请假申请请求 (仅格式校验，业务日期合法性由接口处理)"""
    date: str = Field(..., description="请假日期 YYYY-MM-DD")
    shift: ShiftEnum = Field(..., description="请假时段")
    reason: str = Field(..., min_length=1, max_length=500, description="请假原因，最大500字")
    attachments: Optional[List[str]] = Field(default=[], description="附件路径字符串列表")


class LeaveHistoryItem(BaseModel):
    """请假历史记录项"""
    id: str = Field(..., description="申请ID")
    date: str = Field(..., description="请假日期")
    shift: str = Field(..., description="请假时段")
    reason: str = Field(..., description="请假原因")
    status: str = Field(..., description="审核状态")
    createTime: str = Field(..., description="申请提交时间")
    approver: Optional[str] = Field(None, description="审批人姓名")
    rejectReason: Optional[str] = Field(None, description="驳回原因")
    attachments: List[str] = Field(default_factory=list, description="附件路径字符串列表")


