"""健康档案相关的Pydantic模型定义"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class BasicInfo(BaseModel):
    """患者基本信息"""
    name: str = Field(..., description="患者姓名")
    gender: str = Field(..., description="性别")
    age: Optional[int] = Field(None, description="年龄")
    height: Optional[int] = Field(None, description="身高(cm)")
    phone: str = Field(..., description="联系电话(已脱敏)")
    idCard: Optional[str] = Field(None, description="身份证号(已脱敏)")
    address: Optional[str] = Field(None, description="居住地址")


class MedicalHistory(BaseModel):
    """病史信息"""
    pastHistory: List[str] = Field(default_factory=list, description="既往病史列表")
    allergyHistory: List[str] = Field(default_factory=list, description="过敏史列表")
    familyHistory: List[str] = Field(default_factory=list, description="家族病史列表")


class ConsultationRecord(BaseModel):
    """就诊记录摘要"""
    id: str = Field(..., description="就诊记录ID")
    outpatientNo: Optional[str] = Field(None, description="门诊号")
    visitDate: str = Field(..., description="就诊时间")
    department: str = Field(..., description="科室")
    doctorName: str = Field(..., description="医生")
    chiefComplaint: Optional[str] = Field(None, description="主诉")
    presentIllness: Optional[str] = Field(None, description="现病史")
    auxiliaryExam: Optional[str] = Field(None, description="辅助检查")
    diagnosis: Optional[str] = Field(None, description="诊断")
    prescription: Optional[str] = Field(None, description="处方")
    status: str = Field(..., description="状态(completed/ongoing/cancelled)")


class HealthRecordResponse(BaseModel):
    """健康档案完整响应"""
    patientId: str = Field(..., description="患者ID")
    basicInfo: BasicInfo = Field(..., description="基本信息")
    medicalHistory: MedicalHistory = Field(..., description="病史信息")
    consultationRecords: List[ConsultationRecord] = Field(default_factory=list, description="就诊记录列表")


class VisitRecordDetail(BaseModel):
    """就诊记录详情 - 基本信息"""
    patientName: str = Field(..., description="患者姓名")
    gender: str = Field(..., description="性别")
    age: Optional[int] = Field(None, description="年龄")
    outpatientNo: Optional[str] = Field(None, description="门诊号")
    visitDate: str = Field(..., description="就诊时间")
    department: str = Field(..., description="科室")
    doctorName: str = Field(..., description="医生")


class RecordData(BaseModel):
    """就诊记录详情 - 记录数据"""
    chiefComplaint: Optional[str] = Field(None, description="主诉")
    presentIllness: Optional[str] = Field(None, description="现病史")
    auxiliaryExam: Optional[str] = Field(None, description="辅助检查")
    diagnosis: Optional[str] = Field(None, description="诊断")
    prescription: Optional[str] = Field(None, description="处方")


class VisitRecordDetailResponse(BaseModel):
    """就诊记录详情完整响应"""
    basicInfo: VisitRecordDetail = Field(..., description="基本信息")
    recordData: RecordData = Field(..., description="记录数据")
