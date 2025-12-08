"""
候补挂号相关的 Pydantic schemas
"""
from pydantic import BaseModel, Field
from typing import Optional, List


class WaitlistCreate(BaseModel):
    """加入候补请求 - 仅需必要参数"""
    scheduleId: int = Field(..., description="排班ID")
    patientId: int = Field(..., description="就诊人ID")

    class Config:
        extra = "ignore"  # 忽略未声明字段


class WaitlistCreateResponse(BaseModel):
    """加入候补响应"""
    id: int = Field(..., description="候补记录ID(order_id)")
    queueNumber: int = Field(..., description="当前排队位置 (从 1 开始)")
    estimatedTime: Optional[str] = Field(None, description="预计等待时间 (基于队列位置估算，每个号源平均 10 分钟)")
    createdAt: str = Field(..., description="加入候补时间")


class WaitlistItem(BaseModel):
    """候补列表项"""
    id: int
    scheduleId: int
    hospitalName: Optional[str]
    departmentName: Optional[str]
    doctorName: Optional[str]
    doctorTitle: Optional[str]
    appointmentDate: Optional[str]
    appointmentTime: Optional[str]
    price: Optional[float]
    status: str
    queueNumber: Optional[int]
    patientName: Optional[str]
    createdAt: str
    canConvert: bool = Field(False, description="是否有号源可转预约")


class WaitlistListResponse(BaseModel):
    """候补列表响应"""
    list: List[WaitlistItem]


class WaitlistConvertRequest(BaseModel):
    """候补转预约请求"""
    slotId: Optional[str] = Field(None, description="具体时段ID(可选)")

    class Config:
        extra = "ignore"


class WaitlistConvertResponse(BaseModel):
    """候补转预约响应"""
    id: int
    appointmentDate: Optional[str]
    appointmentTime: Optional[str]
    queueNumber: Optional[int]
    doctorName: Optional[str]
    price: Optional[float]
    status: str
    paymentStatus: str
    createdAt: str
    expiresAt: Optional[str] = None
