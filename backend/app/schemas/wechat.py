"""微信小程序相关的 Pydantic Schemas"""
from pydantic import BaseModel, Field, RootModel
from typing import Optional
from datetime import datetime


# ========== 微信登录相关 Schemas ==========

class WechatLoginRequest(BaseModel):
    """微信小程序登录请求"""
    code: str = Field(..., description="wx.login() 获取的临时 code，5分钟有效")
    
    class Config:
        json_schema_extra = {
            "example": {
                "code": "071AbcDefG1w3qxyzTuv123456"
            }
        }


class WechatLoginResponse(BaseModel):
    """微信小程序登录响应"""
    token: Optional[str] = Field(None, description="登录 token（已绑定时返回）")
    openid: Optional[str] = Field(None, description="微信 openid（未绑定时返回）")
    needBind: bool = Field(..., description="是否需要绑定账号")
    
    class Config:
        json_schema_extra = {
            "example": {
                "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "needBind": False
            }
        }


class WechatBindRequest(BaseModel):
    """微信绑定账号请求"""
    openid: str = Field(..., description="微信 openid")
    phonenumber: str = Field(..., description="手机号")
    password: str = Field(..., description="密码")
    session_key: Optional[str] = Field(None, description="微信 session_key（可选）")
    
    class Config:
        json_schema_extra = {
            "example": {
                "openid": "oABC123456XYZ",
                "phonenumber": "13800138000",
                "password": "password123"
            }
        }


class WechatCodeToOpenIdResponse(BaseModel):
    """微信 code 换取 openid 响应"""
    openid: str = Field(..., description="微信 openid")
    session_key: str = Field(..., description="微信 session_key")
    unionid: Optional[str] = Field(None, description="微信 unionid（如有）")
    errcode: Optional[int] = Field(None, description="错误码")
    errmsg: Optional[str] = Field(None, description="错误信息")


# ========== 订阅消息相关 Schemas ==========

class SubscribeAuthResult(RootModel[dict[str, str]]):
    """订阅授权结果：key 为模板ID，value 为授权状态(accept/reject/ban)。"""

    root: dict[str, str]

    class Config:
        json_schema_extra = {
            "example": {
                "template_id_1": "accept",
                "template_id_2": "reject",
                "template_id_3": "ban"
            }
        }


class SubscribeMessageData(BaseModel):
    """订阅消息数据字段"""
    value: str = Field(..., description="字段值")
    
    class Config:
        json_schema_extra = {
            "example": {
                "value": "张三"
            }
        }


class SubscribeMessageRequest(BaseModel):
    """发送订阅消息请求"""
    touser: str = Field(..., description="接收者 openid")
    template_id: str = Field(..., description="订阅消息模板ID")
    page: Optional[str] = Field(None, description="点击消息后跳转的小程序页面")
    data: dict[str, SubscribeMessageData] = Field(..., description="消息数据")
    miniprogram_state: Optional[str] = Field("formal", description="跳转小程序类型：developer/trial/formal")
    lang: Optional[str] = Field("zh_CN", description="语言")
    
    class Config:
        json_schema_extra = {
            "example": {
                "touser": "oABC123456XYZ",
                "template_id": "template_id_1",
                "page": "pages/appointment/detail?id=123",
                "data": {
                    "thing1": {"value": "张三"},
                    "date2": {"value": "2025年12月18日 09:00"},
                    "thing3": {"value": "心内科"}
                }
            }
        }


class SubscribeMessageResponse(BaseModel):
    """发送订阅消息响应"""
    errcode: int = Field(..., description="错误码，0表示成功")
    errmsg: str = Field(..., description="错误信息")
    msgid: Optional[int] = Field(None, description="消息ID")


# ========== 授权记录相关 Schemas ==========

class WechatSubscribeAuthCreate(BaseModel):
    """创建订阅授权记录"""
    user_id: int = Field(..., description="用户ID")
    template_id: str = Field(..., description="模板ID")
    auth_status: str = Field(..., description="授权状态：accept/reject/ban")
    scene: Optional[str] = Field(None, description="业务场景")


class WechatSubscribeAuthResponse(BaseModel):
    """订阅授权记录响应"""
    id: int
    user_id: int
    template_id: str
    auth_status: str
    scene: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# ========== 消息日志相关 Schemas ==========

class WechatMessageLogCreate(BaseModel):
    """创建消息日志"""
    user_id: int
    openid: str
    template_id: str
    scene: Optional[str] = None
    order_id: Optional[int] = None
    status: str = "pending"
    request_data: Optional[str] = None


class WechatMessageLogResponse(BaseModel):
    """消息日志响应"""
    id: int
    user_id: int
    openid: str
    template_id: str
    scene: Optional[str]
    order_id: Optional[int]
    status: str
    error_code: Optional[int]
    error_message: Optional[str]
    sent_at: Optional[datetime]
    created_at: datetime
    
    class Config:
        from_attributes = True


# ========== 扩展现有 Schemas 的可选字段 ==========

class WechatOptionalFields(BaseModel):
    """微信相关可选字段（用于扩展现有接口）"""
    wxCode: Optional[str] = Field(None, description="wx.login() 获取的临时 code")
    subscribeAuthResult: Optional[dict[str, str]] = Field(
        None, 
        description="订阅授权结果，key为模板ID，value为授权状态(accept/reject/ban)"
    )
    subscribeScene: Optional[str] = Field(
        None, 
        description="业务场景标识: appointment/waitlist/reschedule/cancel"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "wxCode": "071AbcDefG1w3qxyzTuv123456",
                "subscribeAuthResult": {
                    "template_id_1": "accept",
                    "template_id_2": "accept"
                },
                "subscribeScene": "appointment"
            }
        }
