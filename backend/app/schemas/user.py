from pydantic import BaseModel,EmailStr,Field
from pydantic_extra_types.phone_numbers import PhoneNumber
from typing import Optional

#USER数据模型

class userBase(BaseModel):
    identifier: str = Field(max_length=50, description="工号（必填）")
    email: Optional[EmailStr] = Field(default=None, description="邮箱（可选）")
    phonenumber: Optional[str] = Field(default=None, max_length=14, description="手机号（可选）")


class userCreate(userBase):
    password: str = Field(max_length=18, description="密码（必填）")

# 患者端登录 - 使用手机号和密码
class PatientLogin(BaseModel):
    phonenumber: str = Field(max_length=25, description="手机号")
    password: str = Field(max_length=18, description="密码")

# 医生/管理端登录 - 使用工号和密码  
class StaffLogin(BaseModel):
    identifier: str = Field(max_length=50, description="工号")
    password: str = Field(max_length=18, description="密码")
    
class user(userBase):
    user_id: int
    is_admin: bool
    is_verified: bool
    last_login_ip: str | None = None
    last_login_time: int | None = None
    user_type: str | None = None
    class Config:
        from_attributes = True
        orm_mode = True

#登入Token
class Token(BaseModel):
    access_token: str
    token_type: str

#邮箱唯一确定
class TokenData(BaseModel):
    email: str | None = None

class PasswordUpdate(BaseModel):
    user_id : int #用户id
    old_password : str #旧密码
    new_password : str #新密码
    confirm_password : str #再次确认密码

class PasswordChangeConfirmInput(BaseModel):
    user_id : int #用户id
    code: str #验证码
class UserUpdate(BaseModel):
    # username 已移除
    email: EmailStr | None = None
    phonenumber: str | None = Field(default=None, max_length=14)
    
class UserRoleUpdate(BaseModel):
    is_admin: bool

# 患者精确查询相关
class PatientSearchItem(BaseModel):
    """患者搜索结果项"""
    patient_id: str  # 患者业务ID，转为字符串格式如"P123"
    name: str
    gender: str
    age: int
    phone: str
    
    class Config:
        from_attributes = True

class SearchPatientResult(BaseModel):
    """患者精确查询响应"""
    patients: list[PatientSearchItem]