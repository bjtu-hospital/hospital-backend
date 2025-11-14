from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta

from app.db.base import Base

class UserBan(Base):
    """用户封禁记录
    - user_id: 关联 User.user_id
    - ban_type: 封禁类型 register/login/all
    - ban_until: 封禁截止时间 (为 None 表示永久)
    - is_active: 当前封禁是否生效
    - reason: 封禁原因及追踪备注（可在解除时追加）
    - create_time: 创建时间
    - update_time: 最近更新时间
    - unban_time: 解除封禁时间（若已解除）
    """
    __tablename__ = "user_ban"

    ban_id = Column(Integer, primary_key=True, autoincrement=True, comment="封禁记录ID")
    user_id = Column(Integer, ForeignKey("user.user_id"), nullable=False, index=True, comment="关联用户ID")
    ban_type = Column(String(20), nullable=False, comment="封禁类型: register/login/all")
    ban_until = Column(DateTime, nullable=True, comment="封禁截止时间, NULL 表示永久")
    is_active = Column(Boolean, default=True, nullable=False, comment="是否仍在封禁中")
    reason = Column(String(500), nullable=True, comment="封禁原因和备注")
    create_time = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=datetime.utcnow, nullable=False, comment="最近更新时间")
    unban_time = Column(DateTime, nullable=True, comment="解除封禁时间")

    user = relationship("User")

    def apply_duration(self, duration_days: int):
        """根据天数设置 ban_until (0 表示永久)"""
        if duration_days <= 0:
            self.ban_until = None
        else:
            self.ban_until = datetime.utcnow() + timedelta(days=duration_days)
        self.update_time = datetime.utcnow()

    def deactivate(self, extra_reason: str = ""):
        self.is_active = False
        self.unban_time = datetime.utcnow()
        self.update_time = datetime.utcnow()
        if extra_reason:
            # 追加备注
            if self.reason:
                self.reason = f"{self.reason} | 解除: {extra_reason}"
            else:
                self.reason = f"解除: {extra_reason}"