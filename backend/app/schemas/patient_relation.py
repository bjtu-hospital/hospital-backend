"""
患者就诊人关系相关的 Pydantic 模型
用于就诊人管理（添加家人为就诊人，代为预约等功能）
"""
from pydantic import BaseModel, Field, field_validator, ValidationError
from typing import Optional
from datetime import datetime, date


class PatientRelationBase(BaseModel):
    """就诊人关系基础模型"""
    relation_type: str = Field(default="其他", max_length=20, description="关系类型（可自定义，如：本人/父母/配偶/子女/朋友/其他）")
    is_default: bool = Field(default=False, description="是否为默认就诊人")
    remark: Optional[str] = Field(default=None, max_length=200, description="备注信息")


class PatientRelationCreate(PatientRelationBase):
    """创建就诊人关系请求模型（通过身份证号+姓名添加）"""
    name: str = Field(..., min_length=1, max_length=50, description="就诊人姓名（必填）")
    id_card: str = Field(..., min_length=15, max_length=18, description="就诊人身份证号（必填，18位）")
    gender: Optional[str] = Field(default=None, description="性别（可选）：男/女/未知")
    birth_date: Optional[date] = Field(default=None, description="出生日期（可选）")
    
    @field_validator('id_card', mode='before')
    @classmethod
    def validate_id_card(cls, v: str) -> str:
        """验证身份证号格式"""
        if not v:
            raise ValueError('身份证号不能为空')
        v = str(v).strip()
        if len(v) not in [15, 18]:
            raise ValueError('身份证号必须为15位或18位')
        return v
    
    @field_validator('gender', mode='before')
    @classmethod
    def validate_gender(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v != '':
            allowed = ['男', '女', '未知']
            if v not in allowed:
                raise ValueError(f'性别必须是以下之一: {", ".join(allowed)}')
        return v


class PatientRelationUpdate(BaseModel):
    """更新就诊人关系请求模型"""
    relation_type: Optional[str] = Field(default=None, max_length=20, description="关系类型（可自定义）")
    remark: Optional[str] = Field(default=None, max_length=200, description="备注信息")


class PatientInfo(BaseModel):
    """就诊人信息（用于列表展示）"""
    patient_id: int = Field(..., description="患者ID")
    real_name: str = Field(..., description="真实姓名")
    identifier: Optional[str] = Field(default=None, description="学号/工号")
    id_card: str = Field(..., description="身份证号（脱敏，保留前6位后4位）")
    phone_number: str = Field(..., description="手机号（脱敏）")
    gender: Optional[str] = Field(default=None, description="性别")
    birth_date: Optional[str] = Field(default=None, description="出生日期")
    age: Optional[int] = Field(default=None, description="年龄")


class PatientRelationResponse(BaseModel):
    """就诊人关系响应模型"""
    relation_id: int = Field(..., description="关系记录ID")
    patient: PatientInfo = Field(..., description="就诊人信息")
    relation_type: str = Field(..., description="关系类型")
    is_default: bool = Field(..., description="是否为默认就诊人")
    remark: Optional[str] = Field(default=None, description="备注")
    create_time: datetime = Field(..., description="创建时间")

    class Config:
        from_attributes = True


class PatientRelationListResponse(BaseModel):
    """就诊人列表响应模型"""
    total: int = Field(..., description="总数")
    patients: list[PatientRelationResponse] = Field(..., description="就诊人列表")
