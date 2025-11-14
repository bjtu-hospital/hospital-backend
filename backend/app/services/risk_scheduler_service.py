from datetime import datetime
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_risk_summary import UserRiskSummary
from app.models.user_ban import UserBan
from app.services.risk_score_service import risk_score_service

class RiskSchedulerService:
    """风险管理定时任务服务 (骨架)"""

    async def daily_decay_task(self, db: AsyncSession):
        result = await db.execute(select(UserRiskSummary).where(UserRiskSummary.current_score > 0))
        summaries = result.scalars().all()
        for summary in summaries:
            await risk_score_service.apply_decay(db, summary)

    async def check_stable_users_task(self, db: AsyncSession):
        # 连续30天无 incident 的用户奖励
        cutoff = datetime.utcnow()
        result = await db.execute(select(UserRiskSummary).where(and_(UserRiskSummary.last_incident_time != None)))  # noqa: E711
        summaries = result.scalars().all()
        for summary in summaries:
            if summary.last_incident_time and (cutoff - summary.last_incident_time).days >= 30:
                await risk_score_service.update_risk_score(db, summary.user_id, -15, "monthly_stable", "连续30天无异常")

    async def check_expired_bans_task(self, db: AsyncSession):
        now = datetime.utcnow()
        result = await db.execute(select(UserBan).where(and_(UserBan.is_active == True, UserBan.ban_until != None, UserBan.ban_until < now)))  # noqa: E712,E711
        bans = result.scalars().all()
        for ban in bans:
            ban.is_active = False
            ban.unban_time = now
            ban.update_time = now

risk_scheduler_service = RiskSchedulerService()
