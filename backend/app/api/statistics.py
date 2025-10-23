from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from datetime import datetime, timedelta
from typing import Union

from app.db.base import get_db, User, UserAccessLog
from app.schemas.user import user as UserSchema
from app.schemas.response import StatisticsErrorResponse, UserStatisticsResponse, LocationStatisticsResponse, ResponseModel, VisitStatisticsResponse,LoginCountByDayItem,LoginCountByDayResponse
from app.api.auth import get_current_user
from app.core.config import settings
from app.core.exception_handler import StatisticsHTTPException
from pydantic import BaseModel


    
router = APIRouter()

@router.get("/users", response_model=ResponseModel[Union[UserStatisticsResponse, StatisticsErrorResponse]], tags=["Statistics"], summary="统计用户数", description="返回总用户数和较几天前的增长比例。需要Token认证。")
async def get_user_statistics(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    统计用户数
    - 返回总用户数
    """
    try:
        now = datetime.utcnow()
        total_users = await db.scalar(select(func.count()).select_from(User).where(User.is_deleted == 0))
        return ResponseModel(code=0, message=UserStatisticsResponse(total_users=total_users))
    except Exception as e:
        raise StatisticsHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg=f"获取用户统计数据失败: {str(e)}"
        )


@router.get("/visits", response_model=ResponseModel[Union[VisitStatisticsResponse, StatisticsErrorResponse]], tags=["Statistics"], summary="统计网站访问量", description="返回网站总访问量和较几天前的增长比例。需要Token认证。")
async def get_visit_statistics(
    compare_days: int = Query(settings.COMPARE_DAYS, description="对比天数，默认3天前"),
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    统计网站访问量
    - 返回访问日志总数
    - 返回较 compare_days 天前的增长比例
    """
    try:
        now = datetime.utcnow()
        compare_time = now - timedelta(days=compare_days)
        total_visits = await db.scalar(select(func.count()).select_from(UserAccessLog))
        old_visits = await db.scalar(select(func.count()).select_from(UserAccessLog).where(UserAccessLog.access_time < compare_time))
        growth_percent = 0.0
        if old_visits and total_visits > old_visits:
            growth_percent = (total_visits - old_visits) / old_visits * 100
        return ResponseModel(code=0, message=VisitStatisticsResponse(total_visits=total_visits, growth_percent=round(growth_percent, 2), compare_days=compare_days))
    except Exception as e:
        raise StatisticsHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg=f"获取访问量统计数据失败: {str(e)}"
        )
