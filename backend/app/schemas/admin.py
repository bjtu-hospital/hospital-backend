from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime, date


# 管理员注册
class AdminRegister(BaseModel):
    identifier: str = Field(max_length=50, description="工号（必填）")
    password: str = Field(max_length=18, description="密码（必填）")
    email: Optional[EmailStr] = Field(None, description="邮箱（可选）")
    phonenumber: Optional[str] = Field(None, max_length=25, description="手机号（可选）")
    name: Optional[str] = Field(None, max_length=50, description="真实姓名（可选）")
    job_title: Optional[str] = Field(None, max_length=50, description="职务（可选）")


# 大科室管理

class MajorDepartmentCreate(BaseModel):
    name: str = Field(max_length=100, description="大科室名称")
    description: Optional[str] = Field(None, description="描述")


class MajorDepartmentUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100, description="大科室名称")
    description: Optional[str] = Field(None, description="描述")


class MajorDepartmentResponse(BaseModel):
    major_dept_id: int
    name: str
    description: Optional[str]
    create_time: Optional[datetime]
    
    class Config:
        from_attributes = True


# 小科室管理
class MinorDepartmentCreate(BaseModel):
    major_dept_id: int = Field(description="大科室ID")
    name: str = Field(max_length=100, description="小科室名称")
    description: Optional[str] = Field(None, description="描述")


class MinorDepartmentUpdate(BaseModel):
    major_dept_id: Optional[int] = Field(None, description="大科室ID")
    name: Optional[str] = Field(None, max_length=100, description="小科室名称")
    description: Optional[str] = Field(None, description="描述")


class MinorDepartmentResponse(BaseModel):
    minor_dept_id: int
    major_dept_id: int
    name: str
    description: Optional[str]
    create_time: Optional[datetime]
    major_department: Optional[MajorDepartmentResponse]
    
    class Config:
        from_attributes = True


# 医生管理
class DoctorCreate(BaseModel):
    dept_id: int = Field(description="小科室ID")
    name: str = Field(max_length=50, description="医生姓名")
    identifier: Optional[str] = Field(None, max_length=50, description="工号")
    password: Optional[str] = Field(None, max_length=18, description="密码")
    title: Optional[str] = Field(None, max_length=100, description="职称")
    specialty: Optional[str] = Field(None, description="擅长领域")
    introduction: Optional[str] = Field(None, description="个人简介")
    email: Optional[EmailStr] = Field(None, description="邮箱")
    phonenumber: Optional[str] = Field(None, max_length=25, description="手机号")


class DoctorUpdate(BaseModel):
    dept_id: Optional[int] = Field(None, description="小科室ID")
    name: Optional[str] = Field(None, max_length=50, description="医生姓名")
    title: Optional[str] = Field(None, max_length=100, description="职称")
    specialty: Optional[str] = Field(None, description="擅长领域")
    introduction: Optional[str] = Field(None, description="个人简介")
    photo_path: Optional[str] = Field(None, max_length=255, description="照片路径")
    original_photo_url: Optional[str] = Field(None, max_length=255, description="原始照片URL")


class DoctorResponse(BaseModel):
    doctor_id: int
    user_id: Optional[int] = None
    dept_id: int
    name: str
    title: Optional[str] = None
    specialty: Optional[str] = None
    introduction: Optional[str] = None
    photo_path: Optional[str] = None
    original_photo_url: Optional[str] = None
    create_time: Optional[datetime] = None
    minor_department: Optional[MinorDepartmentResponse] = None
    user: Optional[dict] = None  # 用户基本信息
    
    class Config:
        from_attributes = True


# 医生账号创建
class DoctorAccountCreate(BaseModel):
    # doctor_id: int = Field(description="医生ID")
    identifier: str = Field(max_length=50, description="工号")
    password: str = Field(max_length=18, description="密码")
    email: Optional[EmailStr] = Field(None, description="邮箱")
    phonenumber: Optional[str] = Field(None, max_length=25, description="手机号")


# 医生调科室
class DoctorTransferDepartment(BaseModel):
    # doctor_id: int = Field(description="医生ID")
    new_dept_id: int = Field(description="新科室ID")


# ====== 门诊与排班（新增） ======

# 门诊
class ClinicCreate(BaseModel):
    minor_dept_id: int = Field(description="小科室ID")
    name: str = Field(max_length=100, description="门诊名称")
    clinic_type: int = Field(0, description="门诊类型: 0-普通, 1-国疗, 2-特需")
    address: Optional[str] = Field(None, max_length=255, description="门诊地址描述")


class ClinicResponse(BaseModel):
    clinic_id: int
    area_id: int
    name: str
    address: Optional[str]
    minor_dept_id: Optional[int]
    clinic_type: int
    create_time: Optional[datetime]

    class Config:
        from_attributes = True


class ClinicListResponse(BaseModel):
    clinics: List[ClinicResponse]


# 排班
class ScheduleCreate(BaseModel):
    doctor_id: int = Field(description="医生ID")
    clinic_id: int = Field(description="门诊ID")
    schedule_date: date = Field(description="出诊日期，YYYY-MM-DD")
    time_section: str = Field(description="时间段: 上午/下午/晚上")
    slot_type: str = Field(description="号源类型: 普通/专家/特需")
    status: str = Field("正常", description="排班状态")
    price: float = Field(ge=0, description="挂号原价")
    total_slots: int = Field(ge=0, description="总号源数")


class ScheduleUpdate(BaseModel):
    doctor_id: Optional[int] = Field(None, description="医生ID")
    clinic_id: Optional[int] = Field(None, description="门诊ID")
    schedule_date: Optional[date] = Field(None, description="出诊日期，YYYY-MM-DD")
    time_section: Optional[str] = Field(None, description="时间段: 上午/下午/晚上")
    slot_type: Optional[str] = Field(None, description="号源类型: 普通/专家/特需")
    status: Optional[str] = Field(None, description="排班状态")
    price: Optional[float] = Field(None, ge=0, description="挂号原价")
    total_slots: Optional[int] = Field(None, ge=0, description="总号源数")


class ScheduleItemResponse(BaseModel):
    schedule_id: int
    doctor_id: int
    doctor_name: str
    clinic_id: int
    clinic_name: str
    clinic_type: int
    date: date
    week_day: str
    time_section: str
    slot_type: str
    total_slots: int
    remaining_slots: int
    status: Optional[str]
    price: float
    create_time: Optional[datetime]


class ScheduleListResponse(BaseModel):
    schedules: List[ScheduleItemResponse]
