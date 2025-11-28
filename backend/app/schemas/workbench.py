from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime


class WorkbenchDoctorInfo(BaseModel):
    id: int
    name: str
    title: Optional[str] = None
    department: Optional[str] = None
    photo_path: Optional[str] = None


class WorkbenchCurrentShift(BaseModel):
    id: int
    name: str
    startTime: str
    endTime: str
    location: Optional[str] = None
    countdown: Optional[str] = None


class WorkbenchShiftStatus(BaseModel):
    status: str  # not_checkin | checked_in | checkout_pending | checked_out
    currentShift: Optional[WorkbenchCurrentShift] = None
    checkinTime: Optional[str] = None
    checkoutTime: Optional[str] = None
    workDuration: Optional[str] = None
    timeToCheckout: Optional[str] = None


class WorkbenchTodayData(BaseModel):
    pendingConsultation: int
    ongoingConsultation: int
    completedConsultation: int
    totalConsultation: int


class WorkbenchReminder(BaseModel):
    id: int
    type: str
    title: str
    icon: Optional[str] = None
    time: str


class WorkbenchRecentRecord(BaseModel):
    id: int
    patientName: str
    consultationTime: str
    diagnosis: Optional[str] = None


class WorkbenchDashboardResponse(BaseModel):
    doctor: WorkbenchDoctorInfo
    shiftStatus: WorkbenchShiftStatus
    todayData: WorkbenchTodayData
    reminders: List[WorkbenchReminder]
    recentRecords: List[WorkbenchRecentRecord]


class CheckinResponse(BaseModel):
    checkinTime: str
    status: str
    message: str
    workDuration: str


class CheckoutResponse(BaseModel):
    checkoutTime: str
    workDuration: str
    status: str
    message: str


class ShiftItem(BaseModel):
    id: int
    name: str
    startTime: str
    endTime: str
    location: Optional[str] = None
    status: str  # not_started | checking_in | checkout_pending | checked_out


class ShiftsResponse(BaseModel):
    shifts: List[ShiftItem]


class ConsultationStatsResponse(BaseModel):
    pending: int
    ongoing: int
    completed: int
    total: int


class RecentConsultationItem(BaseModel):
    id: int
    patientName: str
    consultationTime: str
    diagnosis: Optional[str] = None


class RecentConsultationsResponse(BaseModel):
    records: List[RecentConsultationItem]


class AttendanceRecordItem(BaseModel):
    record_id: int
    schedule_id: int
    checkin_time: Optional[datetime] = None
    checkout_time: Optional[datetime] = None
    work_duration_minutes: Optional[int] = None
    status: str
    created_at: datetime


class AttendanceRecordsResponse(BaseModel):
    records: List[AttendanceRecordItem]
    total: int


class AuthUserDoctorInfo(BaseModel):
    doctor: WorkbenchDoctorInfo
