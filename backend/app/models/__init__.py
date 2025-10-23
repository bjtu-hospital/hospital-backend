# 导入所有模型，确保SQLAlchemy能够正确识别它们
from .user import User, UserType
from .user_access_log import UserAccessLog
from .administrator import Administrator
from .hospital_area import HospitalArea
from .major_department import MajorDepartment
from .minor_department import MinorDepartment
from .clinic import Clinic
from .doctor import Doctor
from .patient import Patient, PatientType, Gender
from .schedule import Schedule, SlotType

__all__ = [
    "User",
    "UserType", 
    "UserAccessLog",
    "Administrator",
    "HospitalArea",
    "MajorDepartment",
    "MinorDepartment",
    "Clinic",
    "Doctor",
    "Patient",
    "PatientType",
    "Gender",
    "Schedule",
    "SlotType"
]


