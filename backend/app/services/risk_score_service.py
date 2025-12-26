from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.datetime_utils import get_now_naive

from app.models.user_risk_summary import UserRiskSummary
from app.models.user_ban import UserBan
from app.models.risk_log import RiskLog

RISK_HIGH = 75
RISK_MEDIUM = 50
RISK_LOW = 30

class RiskScoreService:
    """风险分数计算与管理服务"""

    async def get_or_create_summary(self, db: AsyncSession, user_id: int) -> UserRiskSummary:
        result = await db.execute(select(UserRiskSummary).where(UserRiskSummary.user_id == user_id))
        summary = result.scalar_one_or_none()
        if not summary:
            summary = UserRiskSummary(user_id=user_id)
            db.add(summary)
            await db.flush()
        return summary

    def calculate_level(self, score: int) -> str:
        if score >= RISK_HIGH:
            return "HIGH"
        if score >= RISK_MEDIUM:
            return "MEDIUM"
        if score >= RISK_LOW:
            return "LOW"
        return "SAFE"

    async def apply_decay(self, db: AsyncSession, summary: UserRiskSummary) -> Optional[int]:
        """根据距离 last_incident_time 时间执行自然衰减; 每7天 10%"""
        if not summary.last_incident_time:
            return None
        days_passed = (get_now_naive() - summary.last_incident_time).days
        if days_passed < 7:
            return None
        # 计算衰减次数(每7天一次)
        periods = days_passed // 7
        if periods <= 0:
            return None
        decay_factor = 0.9 ** periods
        decayed_score = int(summary.current_score * decay_factor)
        decay_amount = summary.current_score - decayed_score
        if decay_amount <= 0:
            return None
        summary.current_score = decayed_score
        summary.current_level = self.calculate_level(summary.current_score)
        summary.last_decay_time = get_now_naive()
        summary.touch()
        # 写入风险日志(负分)
        db.add(RiskLog(user_id=summary.user_id, risk_score=-decay_amount, risk_level=summary.current_level, behavior_type="natural_decay", description=f"自然衰减 {decay_amount}"))
        return decay_amount

    async def update_risk_score(self, db: AsyncSession, user_id: int, delta: int, behavior_type: str, description: str):
        """更新用户风险分数并写日志; 触发自动封禁"""
        summary = await self.get_or_create_summary(db, user_id)
        if delta > 0:
            summary.total_negative_count += 1
            summary.last_incident_time = get_now_naive()
        else:
            summary.total_positive_count += 1
        summary.current_score = max(0, summary.current_score + delta)
        summary.current_level = self.calculate_level(summary.current_score)
        summary.touch()
        # 日志
        db.add(RiskLog(user_id=user_id, risk_score=delta, risk_level=summary.current_level, behavior_type=behavior_type, description=description))
        # 自动封禁逻辑
        await self._maybe_auto_ban(db, summary)
        return summary.current_score, summary.current_level

    async def _maybe_auto_ban(self, db: AsyncSession, summary: UserRiskSummary):
        if summary.current_score < RISK_LOW:
            return
        # 已有活跃封禁则跳过
        result = await db.execute(select(UserBan).where(UserBan.user_id == summary.user_id, UserBan.is_active == True))  # noqa: E712
        active = result.scalar_one_or_none()
        if active:
            return
        if summary.current_score >= 90:
            ban_type = "all"
            duration = 30
        elif summary.current_score >= RISK_HIGH:
            ban_type = "login"
            duration = 7
        else:
            return
        ban_until = get_now_naive() + timedelta(days=duration)
        ban = UserBan(user_id=summary.user_id, ban_type=ban_type, ban_until=ban_until, reason=f"风险分数达到 {summary.current_score}")
        db.add(ban)

risk_score_service = RiskScoreService()
