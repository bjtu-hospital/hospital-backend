from pydantic import BaseModel, Field, EmailStr
from typing import Optional
from enum import Enum

class FeedbackType(str, Enum):
    bug = "bug"
    suggestion = "suggestion"
    complaint = "complaint"
    praise = "praise"

class FeedbackStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    replied = "replied"
    closed = "closed"

class FeedbackCreate(BaseModel):
    type: FeedbackType = Field(..., description="反馈类型")
    content: str = Field(..., min_length=1, max_length=500, description="反馈内容")
    contactPhone: Optional[str] = Field(None, max_length=20, description="联系电话")
    contactEmail: Optional[EmailStr] = Field(None, description="联系邮箱")

class FeedbackSimpleOut(BaseModel):
    feedback_id: int
    type: FeedbackType
    content: str
    submitDate: str
    status: FeedbackStatus

class FeedbackDetailOut(BaseModel):
    feedback_id: int
    type: FeedbackType
    typeText: str
    content: str
    contactPhone: Optional[str] = None
    contactEmail: Optional[str] = None
    status: FeedbackStatus
    submitDate: str
    createdAt: str

class FeedbackSubmitOut(BaseModel):
    feedback_id: int
    type: FeedbackType
    typeText: str
    content: str
    status: FeedbackStatus
    submitDate: str
    createdAt: str
