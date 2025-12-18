"""
支付相关的 Pydantic Schema
"""
from pydantic import BaseModel, Field
from typing import Optional
import enum


class PaymentMethodEnum(str, enum.Enum):
    """支付方式枚举"""
    BANK = "bank"           # 银行卡
    ALIPAY = "alipay"       # 支付宝
    WECHAT = "wechat"       # 微信


class PaymentRequest(BaseModel):
    """支付请求"""
    method: PaymentMethodEnum = Field(..., description="支付方式: bank/alipay/wechat")
    remark: Optional[str] = Field(None, description="支付备注（可选）")
    wxCode: Optional[str] = Field(None, description="wx.login() 获取的临时 code，用于刷新/绑定 openid")
    subscribeAuthResult: Optional[dict] = Field(
        None, 
        description=(
            "订阅授权结果，key 为模板ID，value 为授权状态(accept/reject/ban)。"
            "支付时建议同时授权：预约成功通知(RFZQNIC-vGQC_mkDcqAneHMamQUhmWIn82L2FwsiC5A) 和 "
            "就诊提醒(RFZQNIC-vGQC_mkDcqAneFF3OluydoAJXHEjh1pY64k)，前者立即发送，后者由定时任务在就诊前24小时发送"
        )
    )
    subscribeScene: Optional[str] = Field(
        None, description="业务场景标识，默认 appointment_paid，用于落库授权记录"
    )



class PaymentResponse(BaseModel):
    """支付响应"""
    success: bool = Field(..., description="支付是否成功")
    orderId: int = Field(..., description="订单ID")
    orderNo: Optional[str] = Field(None, description="订单号")
    paymentStatus: str = Field(..., description="支付状态")
    paymentTime: str = Field(..., description="支付时间")
    method: str = Field(..., description="支付方式")
    amount: float = Field(..., description="支付金额")

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "orderId": 123,
                "orderNo": "20251207000001",
                "paymentStatus": "paid",
                "paymentTime": "2025-12-07 10:30:45",
                "method": "alipay",
                "amount": 80.00
            }
        }


class CancelPaymentRequest(BaseModel):
    """取消支付请求"""
    reason: Optional[str] = Field(None, description="取消原因")

    class Config:
        json_schema_extra = {
            "example": {
                "reason": "不需要了"
            }
        }


class CancelPaymentResponse(BaseModel):
    """取消支付响应"""
    success: bool = Field(..., description="取消是否成功")
    orderId: int = Field(..., description="订单ID")
    status: str = Field(..., description="订单状态")
    cancelTime: str = Field(..., description="取消时间")
    reason: Optional[str] = Field(None, description="取消原因/失败原因")

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "orderId": 123,
                "status": "timeout",
                "cancelTime": "2025-12-07 10:35:00",
                "reason": "支付超时"
            }
        }
