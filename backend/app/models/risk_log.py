from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db.base import Base

class RiskLog(Base):
    """用户风险评分日志
    - user_id: 关联 User.user_id
    - risk_score: 风险分值 (0-100)
    - risk_level: 风险等级: 高危/中危/正常 等
    - alert_time: 记录时间
    """
    __tablename__ = "risk_log"

    risk_log_id = Column(Integer, primary_key=True, autoincrement=True, comment="风险日志ID")
    user_id = Column(Integer, ForeignKey("user.user_id"), nullable=False, index=True, comment="关联用户ID")
    risk_score = Column(Integer, nullable=False, comment="风险分值")
    risk_level = Column(String(20), nullable=False, comment="风险等级")
    alert_time = Column(DateTime, default=datetime.utcnow, nullable=False, comment="记录时间")

    user = relationship("User")
