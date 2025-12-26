from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.datetime_utils import beijing_now_for_model

from app.db.base import Base

class RiskLog(Base):
    """用户风险评分日志
    - user_id: 关联 User.user_id
    - risk_score: 本次分数变化 (正数=增加, 负数=减少)
    - risk_level: 操作后的风险等级 (SAFE/LOW/MEDIUM/HIGH)
    - behavior_type: 行为类型标识 (short_time_batch_register / high_frequency_login 等)
    - description: 行为说明
    - alert_time: 记录时间
    """
    __tablename__ = "risk_log"

    risk_log_id = Column(Integer, primary_key=True, autoincrement=True, comment="风险日志ID")
    user_id = Column(Integer, ForeignKey("user.user_id"), nullable=False, index=True, comment="关联用户ID")
    risk_score = Column(Integer, nullable=False, comment="本次分数变化")
    risk_level = Column(String(20), nullable=False, comment="操作后的风险等级")
    behavior_type = Column(String(50), nullable=True, comment="行为类型标识")
    description = Column(String(500), nullable=True, comment="行为描述")
    alert_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="记录时间")

    user = relationship("User")
