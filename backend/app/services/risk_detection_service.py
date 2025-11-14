from datetime import datetime, timedelta
from typing import Dict
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.registration_order import RegistrationOrder
from app.models.user_access_log import UserAccessLog
from app.services.risk_score_service import risk_score_service

SHORT_TIME_BATCH_THRESHOLD = 3
CROSS_DEPT_THRESHOLD = 3
HIGH_FREQ_LOGIN_THRESHOLD = 10

class RiskDetectionService:
    """风险行为检测与评分服务(精简实现)"""

    async def detect_registration_risk(self, db: AsyncSession, user_id: int) -> int:
        """检测挂号行为风险: 短时间批量 & 跨科室批量"""
        now = datetime.utcnow()
        score_added = 0

        # 10 分钟内挂号次数
        ten_minutes_ago = now - timedelta(minutes=10)
        result = await db.execute(select(func.count(RegistrationOrder.order_id)).where(and_(RegistrationOrder.user_id == user_id, RegistrationOrder.create_time >= ten_minutes_ago)))
        recent_count = result.scalar() or 0
        if recent_count >= SHORT_TIME_BATCH_THRESHOLD:
            await risk_score_service.update_risk_score(db, user_id, 15, "short_time_batch_register", f"10分钟内挂号 {recent_count} 次")
            score_added += 15

        # 一小时内不同科室数
        one_hour_ago = now - timedelta(hours=1)
        dept_result = await db.execute(select(func.count(func.distinct(RegistrationOrder.minor_department_id))).where(and_(RegistrationOrder.user_id == user_id, RegistrationOrder.create_time >= one_hour_ago)))
        dept_count = dept_result.scalar() or 0
        if dept_count >= CROSS_DEPT_THRESHOLD:
            await risk_score_service.update_risk_score(db, user_id, 20, "cross_department_batch", f"1小时内挂号 {dept_count} 个科室")
            score_added += 20

        return score_added

    async def detect_login_risk(self, db: AsyncSession, user_id: int, ip: str) -> int:
        """检测登录风险: 高频登录 (其他复杂如IP地理位置暂未实现)"""
        now = datetime.utcnow()
        one_hour_ago = now - timedelta(hours=1)
        result = await db.execute(select(func.count(UserAccessLog.log_id)).where(and_(UserAccessLog.user_id == user_id, UserAccessLog.access_time >= one_hour_ago)))
        login_count = result.scalar() or 0
        score_added = 0
        if login_count >= HIGH_FREQ_LOGIN_THRESHOLD:
            await risk_score_service.update_risk_score(db, user_id, 10, "high_frequency_login", f"1小时内登录 {login_count} 次")
            score_added += 10
        return score_added

    async def apply_positive_behavior(self, db: AsyncSession, user_id: int, behavior_type: str):
        """应用正向行为消分 (完成就诊/稳定使用/实名认证)"""
        mapping = {
            "complete_visit": -10,
            "monthly_stable": -15,
            "identity_verify": -10,
        }
        delta = mapping.get(behavior_type, 0)
        if delta == 0:
            return 0
        # 高风险减半逻辑
        summary = await risk_score_service.get_or_create_summary(db, user_id)
        current_score = summary.current_score
        if current_score >= 75:
            delta = int(delta * 0.5)
        await risk_score_service.update_risk_score(db, user_id, delta, behavior_type, f"正向行为 {behavior_type}")
        return abs(delta)

risk_detection_service = RiskDetectionService()
