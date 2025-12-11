from sqlalchemy import Column, Integer, String, DateTime, Text, Enum, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base import Base
import enum

# 反馈类型
class FeedbackType(enum.Enum):
    BUG = "bug"
    SUGGESTION = "suggestion"
    COMPLAINT = "complaint"
    PRAISE = "praise"

# 反馈状态
class FeedbackStatus(enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    REPLIED = "replied"
    CLOSED = "closed"

class Feedback(Base):
    __tablename__ = "feedback"
    feedback_id = Column(Integer, primary_key=True, autoincrement=True, comment="反馈ID")
    user_id = Column(Integer, ForeignKey("user.user_id"), nullable=False, index=True, comment="反馈人user_id")
    type = Column(Enum(FeedbackType, values_callable=lambda e: [v.value for v in e], name="feedbacktype", native_enum=False), nullable=False, comment="反馈类型")
    content = Column(Text, nullable=False, comment="反馈内容")
    contact_phone = Column(String(20), nullable=True, comment="联系电话")
    contact_email = Column(String(100), nullable=True, comment="联系邮箱")
    status = Column(Enum(FeedbackStatus, values_callable=lambda e: [v.value for v in e], name="feedbackstatus", native_enum=False), default=FeedbackStatus.PENDING, nullable=False, comment="处理状态")
    submit_date = Column(String(10), nullable=False, comment="提交日期YYYY-MM-DD")
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")

    user = relationship("User", back_populates="feedbacks")
