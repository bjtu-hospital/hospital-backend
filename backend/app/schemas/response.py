
from typing import Generic, TypeVar, Optional, List
from pydantic import BaseModel
from pydantic.generics import GenericModel
from app.schemas.user import user, Token

T = TypeVar("T")


# 通用响应模型
class ResponseModel(GenericModel, Generic[T]):
    code: int
    message: Optional[T]
    
# ====== 全局异常相关返回类型 ======
class UnknownErrorResponse(BaseModel):
    error: str
    detail: str

class HTTPErrorResponse(BaseModel):
    error: str
    detail: str

class RequestValidationErrorResponse(BaseModel):
    error: str
    detail: list

class AuthErrorResponse(BaseModel):
    error: str
    msg: str



# ====== AUTH认证模块相关返回类型 ======


# 登录成功返回的数据模型
class LoginResponse(BaseModel):
    userid: int
    access_token: str
    token_type: str
    user_type: str  # 用户类型：student, teacher, doctor, admin, external

# 获取所有用户返回的数据模型
class UsersListResponse(BaseModel):
    users: List[user]

# 获取单个用户返回的数据模型
class SingleUserResponse(BaseModel):
    user: user

# 删除成功返回的数据模型
class DeleteResponse(BaseModel):
    detail: str

# 注册成功返回的数据模型
class RegisterResponse(BaseModel):
    detail: str

# Token失效返回的数据模型
class TokenErrorResponse(BaseModel):
    error: str

# 更新用户信息返回的数据模型
class UpdateUserResponse(BaseModel):
    user: user

# 获取当前用户角色返回的数据模型
class UserRoleResponse(BaseModel):
    role: str

# 更新用户角色返回的数据模型
class UpdateUserRoleResponse(BaseModel):
    detail: str


    
    

        

# ====== User Access Log相关返回类型 ======
class UserAccessLogItem(BaseModel):
    user_access_log_id: int
    user_id: int | None = None
    ip: str
    ua: str | None = None
    url: str
    method: str
    status_code: int
    response_code: int | None = None
    access_time: str  # ISO格式字符串
    duration_ms: int

    class Config:
        orm_mode = True

class UserAccessLogPageResponse(BaseModel):
    logs: list[UserAccessLogItem]
    total: int
    total_pages: int
    page: int
    page_size: int

        

# ====== 密码修改相关返回类型 ======
        

class PasswordChangeRequestResponse(BaseModel):
    detail: str

class PasswordChangeConfirmResponse(BaseModel):
    detail: str
        

        
# 静态异常响应模型
class StatisticsErrorResponse(BaseModel):
    msg: str = "统计数据获取失败"

# 用户统计响应
class UserStatisticsResponse(BaseModel):
    total_users: int

class VisitStatisticsResponse(BaseModel):
    """
    网站访问量统计响应结构
    """
    total_visits: int
    growth_percent: float
    compare_days: int
    
    
class LoginCountByDayItem(BaseModel):
    day: str
    total_requests: int

class LoginCountByDayResponse(BaseModel):
    days: list[LoginCountByDayItem]


# ====== 管理员管理模块相关返回类型 ======

class AdminRegisterResponse(BaseModel):
    detail: str

class MajorDepartmentListResponse(BaseModel):
    departments: List[dict]

class MinorDepartmentListResponse(BaseModel):
    departments: List[dict]

class DoctorItem(BaseModel):
    doctor_id: int
    user_id: Optional[int] = None
    is_registered: bool
    dept_id: int
    name: str
    title: Optional[str] = None
    specialty: Optional[str] = None
    introduction: Optional[str] = None
    photo_path: Optional[str] = None
    original_photo_url: Optional[str] = None
    create_time: Optional[str] = None


class DoctorListResponse(BaseModel):
    doctors: List[DoctorItem]

class DoctorAccountCreateResponse(BaseModel):
    detail: str
    user_id: int
    doctor_id: int

class DoctorTransferResponse(BaseModel):
    detail: str
    doctor_id: int
    old_dept_id: int
    new_dept_id: int