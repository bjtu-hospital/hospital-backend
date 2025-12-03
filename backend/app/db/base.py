from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from redis.asyncio import Redis

from app.core.config import settings

#异步引擎连接数据库(echo表输出日志)
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=True,
    pool_pre_ping=True,        # 每次从连接池获取连接时先 ping 测试是否有效
    pool_recycle=3600,          # 连接回收时间（秒），避免使用超时的连接
    pool_size=10,               # 连接池大小
    max_overflow=20,            # 超出 pool_size 后最多再创建的连接数
    pool_timeout=30,            # 获取连接的超时时间（秒）
    connect_args={
        "connect_timeout": 10   # MySQL 连接超时（秒）
    }
)

#事务处理
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

#全局Base
Base = declarative_base()

#Redis数据库连接
redis = Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, decode_responses=True, password=settings.REDIS_PASSWORD)

#引用表类(****十分重要)

from app.models.user import User    # noqa
from app.models.user_access_log import UserAccessLog # noqa
from app.models.administrator import Administrator # noqa
from app.models.hospital_area import HospitalArea # noqa
from app.models.major_department import MajorDepartment # noqa
from app.models.minor_department import MinorDepartment # noqa
from app.models.clinic import Clinic # noqa
from app.models.doctor import Doctor # noqa
from app.models.patient import Patient # noqa
from app.models.patient_relation import PatientRelation # noqa
from app.models.schedule import Schedule # noqa
from app.models.registration_order import RegistrationOrder # noqa
from app.models.visit_history import VisitHistory # noqa
from app.models.schedule_audit import ScheduleAudit # noqa
from app.models.leave_audit import LeaveAudit # noqa
from app.models.add_slot_audit import AddSlotAudit # noqa
from app.models.user_ban import UserBan # noqa
from app.models.risk_log import RiskLog # noqa
from app.models.user_risk_summary import UserRiskSummary # noqa
from app.models.attendance_record import AttendanceRecord # noqa

#异步获取事务函数
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
            
async def get_redis():
    redis = Redis(host="localhost", port=6379, decode_responses=True)
    try:
        yield redis
    finally:
        await redis.close()
 