"""
预约挂号相关的 Pydantic schemas
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, date


class AppointmentCreate(BaseModel):
    """创建预约请求"""
    scheduleId: int = Field(..., description="排班ID")
    hospitalId: int = Field(..., description="医院院区ID") 
    departmentId: int = Field(..., description="科室ID")
    patientId: int = Field(..., description="就诊人ID")
    symptoms: Optional[str] = Field(None, description="症状描述")


class AppointmentResponse(BaseModel):
    """创建预约响应"""
    id: int = Field(..., description="预约ID(order_id)")
    orderNo: str = Field(..., description="订单号")
    queueNumber: Optional[int] = Field(None, description="排队号码")
    needPay: bool = Field(True, description="是否需要支付")
    payAmount: float = Field(..., description="支付金额")
    appointmentDate: str = Field(..., description="预约日期")
    appointmentTime: str = Field(..., description="预约时间段")
    status: str = Field(..., description="预约状态")
    paymentStatus: str = Field(..., description="支付状态")


class AppointmentListItem(BaseModel):
    """预约列表项"""
    id: int
    orderNo: str
    hospitalId: int
    hospitalName: str
    departmentId: int
    departmentName: str
    doctorName: str
    doctorTitle: str
    scheduleId: int
    appointmentDate: str
    appointmentTime: str
    patientName: str
    patientId: int
    queueNumber: Optional[int] = None
    price: float
    status: str
    paymentStatus: str
    canCancel: bool
    canReschedule: bool = False  # 暂不支持改约
    createdAt: str


class AppointmentListResponse(BaseModel):
    """预约列表响应"""
    total: int
    page: int
    pageSize: int
    list: list[AppointmentListItem]


class CancelAppointmentResponse(BaseModel):
    """取消预约响应"""
    success: bool
    refundAmount: Optional[float] = None
