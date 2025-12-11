"""
系统配置相关的 Pydantic Schema
用于挂号配置和排班配置的请求/响应验证
"""
from pydantic import BaseModel, Field, validator
from typing import Optional
import re


# ============ 挂号配置 Schema ============

class RegistrationConfig(BaseModel):
    """挂号配置"""
    advanceBookingDays: Optional[int] = Field(None, ge=1, le=90, description="提前挂号天数 (1-90)")
    sameDayDeadline: Optional[str] = Field(None, description="当日挂号截止时间，格式: HH:mm")
    noShowLimit: Optional[int] = Field(None, ge=1, le=10, description="爽约次数限制 (1-10)")
    cancelHoursBefore: Optional[int] = Field(None, ge=1, le=72, description="退号提前时间（小时） (1-72)")
    sameClinicInterval: Optional[int] = Field(None, ge=1, le=30, description="同科室挂号间隔（天） (1-30)")

    @validator('sameDayDeadline')
    def validate_time_format(cls, v):
        """验证时间格式 HH:mm"""
        if v is not None:
            if not re.match(r'^([0-1][0-9]|2[0-3]):[0-5][0-9]$', v):
                raise ValueError('时间格式必须为 HH:mm (24小时制)')
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "advanceBookingDays": 14,
                "sameDayDeadline": "08:00",
                "noShowLimit": 3,
                "cancelHoursBefore": 24,
                "sameClinicInterval": 7
            }
        }


# ============ 排班配置 Schema ============

class ScheduleConfig(BaseModel):
    """排班配置"""
    maxFutureDays: Optional[int] = Field(None, ge=7, le=180, description="最多排未来天数 (7-180)")
    morningStart: Optional[str] = Field(None, description="上午班开始时间，格式: HH:mm")
    morningEnd: Optional[str] = Field(None, description="上午班结束时间，格式: HH:mm")
    afternoonStart: Optional[str] = Field(None, description="下午班开始时间，格式: HH:mm")
    afternoonEnd: Optional[str] = Field(None, description="下午班结束时间，格式: HH:mm")
    eveningStart: Optional[str] = Field(None, description="晚班开始时间，格式: HH:mm")
    eveningEnd: Optional[str] = Field(None, description="晚班结束时间，格式: HH:mm")
    consultationDuration: Optional[int] = Field(None, ge=5, le=60, description="单次就诊时长（分钟） (5-60)")
    intervalTime: Optional[int] = Field(None, ge=0, le=30, description="就诊间隔时间（分钟） (0-30)")

    @validator('morningStart', 'morningEnd', 'afternoonStart', 'afternoonEnd', 'eveningStart', 'eveningEnd')
    def validate_time_format(cls, v):
        """验证时间格式 HH:mm"""
        if v is not None:
            if not re.match(r'^([0-1][0-9]|2[0-3]):[0-5][0-9]$', v):
                raise ValueError('时间格式必须为 HH:mm (24小时制)')
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "maxFutureDays": 60,
                "morningStart": "08:00",
                "morningEnd": "12:00",
                "afternoonStart": "14:00",
                "afternoonEnd": "18:00",
                "eveningStart": "18:30",
                "eveningEnd": "21:00",
                "consultationDuration": 15,
                "intervalTime": 5
            }
        }


# ============ 患者身份折扣配置 Schema ============

class PatientIdentityDiscountsConfig(BaseModel):
    """患者身份折扣配置"""
    student: float = Field(0.8, ge=0.0, le=1.0, description="学生折扣率 (0.0-1.0)")
    teacher: float = Field(0.8, ge=0.0, le=1.0, description="教师折扣率 (0.0-1.0)")
    staff: float = Field(0.8, ge=0.0, le=1.0, description="职工折扣率 (0.0-1.0)")
    external: float = Field(1.0, ge=0.0, le=1.0, description="校外折扣率 (0.0-1.0)")

    class Config:
        json_schema_extra = {
            "example": {
                "student": 0.8,
                "teacher": 0.8,
                "staff": 0.8,
                "external": 1.0
            }
        }


class SystemConfigRequest(BaseModel):
    """系统配置更新请求"""
    registration: Optional[RegistrationConfig] = None
    schedule: Optional[ScheduleConfig] = None
    patientIdentityDiscounts: Optional[PatientIdentityDiscountsConfig] = None

    class Config:
        json_schema_extra = {
            "example": {
                "registration": {
                    "advanceBookingDays": 14,
                    "sameDayDeadline": "08:00",
                    "noShowLimit": 3
                },
                "schedule": {
                    "maxFutureDays": 60,
                    "morningStart": "08:00"
                },
                "patientIdentityDiscounts": {
                    "student": 0.8,
                    "teacher": 0.8,
                    "staff": 0.8,
                    "external": 1.0
                }
            }
        }


class SystemConfigResponse(BaseModel):
    """系统配置获取响应"""
    registration: dict
    schedule: dict
    patientIdentityDiscounts: dict

    class Config:
        json_schema_extra = {
            "example": {
                "registration": {
                    "advanceBookingDays": 14,
                    "sameDayDeadline": "08:00",
                    "noShowLimit": 3,
                    "cancelHoursBefore": 24,
                    "sameClinicInterval": 7
                },
                "schedule": {
                    "maxFutureDays": 60,
                    "morningStart": "08:00",
                    "morningEnd": "12:00",
                    "afternoonStart": "14:00",
                    "afternoonEnd": "18:00",
                    "eveningStart": "18:30",
                    "eveningEnd": "21:00",
                    "consultationDuration": 15,
                    "intervalTime": 5
                },
                "patientIdentityDiscounts": {
                    "学生": 0.8,
                    "教师": 0.8,
                    "职工": 0.8,
                    "校外": 1.0
                }
            }
        }
