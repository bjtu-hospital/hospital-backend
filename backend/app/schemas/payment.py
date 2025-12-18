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

    class Config:
        json_schema_extra = {
            "example": {
                "method": "alipay",
                "remark": "在线支付"
            }
        }


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
