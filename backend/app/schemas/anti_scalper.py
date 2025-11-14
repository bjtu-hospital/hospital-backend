from typing import Optional, List
from datetime import datetime, date
from pydantic import BaseModel, Field

class AntiScalperUserItem(BaseModel):
    user_id: int
    username: Optional[str] = None
    risk_level: Optional[str] = None
    risk_score: Optional[int] = None
    banned: bool = False
    ban_type: Optional[str] = None
    ban_until: Optional[datetime] = None

class AntiScalperUserListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    users: List[AntiScalperUserItem]

class AntiScalperUserDetailResponse(BaseModel):
    user_id: int
    username: Optional[str]
    is_admin: bool
    risk_score: Optional[int]
    risk_level: Optional[str]
    ban_active: bool
    ban_type: Optional[str]
    ban_until: Optional[datetime]
    ban_reason: Optional[str]
    unban_time: Optional[datetime]

class AntiScalperUserStatsResponse(BaseModel):
    user_id: int
    start_date: date
    end_date: date
    total_registrations: int
    total_cancellations: int
    cancellation_rate: float
    access_logs: Optional[int] = None

class UserBanRequest(BaseModel):
    user_id: int
    ban_type: str = Field(pattern="^(register|login|all)$", description="封禁类型: register/login/all")
    duration_days: int = Field(ge=0, description="封禁天数, 0 表示永久")
    reason: str = Field(min_length=1, max_length=500)

class UserUnbanRequest(BaseModel):
    user_id: int
    reason: str = Field(min_length=1, max_length=500, description="解除封禁备注")
