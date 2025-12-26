from sqlalchemy import Column, Integer, String, DateTime
from app.core.datetime_utils import beijing_now_for_model

from app.db.base import Base

class UserRiskSummary(Base):
    """用户风险分数汇总表
    - current_score: 当前累计风险分数 (>=0)
    - current_level: 当前风险等级 SAFE/LOW/MEDIUM/HIGH
    - last_incident_time: 最近一次产生风险分数(正向)的时间
    - last_decay_time: 最近一次执行自然衰减的时间
    - total_positive_count: 累计正向行为次数 (消分)
    - total_negative_count: 累计负向行为次数 (加分)
    """
    __tablename__ = "user_risk_summary"

    user_id = Column(Integer, primary_key=True, comment="用户ID")
    current_score = Column(Integer, default=0, nullable=False, comment="当前风险分数")
    current_level = Column(String(20), default="SAFE", nullable=False, comment="当前风险等级")
    last_incident_time = Column(DateTime, nullable=True, comment="最近一次异常行为时间")
    last_decay_time = Column(DateTime, nullable=True, comment="最近一次衰减时间")
    total_positive_count = Column(Integer, default=0, nullable=False, comment="累计正向行为次数")
    total_negative_count = Column(Integer, default=0, nullable=False, comment="累计负向行为次数")
    updated_at = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="最近更新时间")

    def touch(self):
        self.updated_at = beijing_now_for_model()
