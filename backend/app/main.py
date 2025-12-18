from fastapi import FastAPI,Depends, Request, Response,status,HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import logging
from contextlib import asynccontextmanager
import asyncio
import os
import sys
from redis.asyncio import Redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.core.datetime_utils import BEIJING_TZ

from app.api import auth,statistics
from app.api import admin,doctor, patient, common
from app.core.exception_handler import register_exception_handlers
from app.core.log_middleware import LogMiddleware
from app.core.config import settings
from app.db.base import engine,Base,redis,AsyncSessionLocal
from app.core.cleantask import create_cleanup_task
from app.services.absence_scheduler_service import start_absence_scheduler, stop_absence_scheduler
from app.services.waitlist_service import WaitlistService
from app.services.payment_timeout_service import PaymentTimeoutService
from app.services.appointment_reminder_service import send_appointment_reminder

# 确保 logs 文件夹存在
os.makedirs("logs", exist_ok=True)

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/app.log", encoding="utf-8"),  # 写入到文件
        logging.StreamHandler()  # 控制台同时输出
    ]
)
logger = logging.getLogger(__name__)

# 全局变量存储清理任务和调度器
cleanup_task = None
scheduler = None


async def persist_waitlist_job():
    """定时任务：每5分钟同步 Redis 候补队列到数据库"""
    try:
        from app.db.base import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            count = await WaitlistService.persist_waitlist_to_db(db)
            logger.info(f"候补队列同步完成: 更新 {count} 条记录")
    except Exception as e:
        logger.error(f"候补队列同步失败: {str(e)}")


async def check_payment_timeout_job():
    """定时任务：每分钟检查一次支付超时的订单"""
    try:
        async with AsyncSessionLocal() as db:
            count = await PaymentTimeoutService.cancel_timeout_pending_orders(db)
            if count > 0:
                logger.info(f"支付超时检查: 处理 {count} 个超时订单")
    except Exception as e:
        logger.error(f"支付超时检查失败: {str(e)}")


async def send_appointment_reminder_job():
    """定时任务：每天晚上20:00发送明天的就诊提醒"""
    try:
        async with AsyncSessionLocal() as db:
            result = await send_appointment_reminder(db)
            logger.info(f"就诊提醒: 总计{result.get('total', 0)}, 成功{result.get('success', 0)}, 失败{result.get('failed', 0)}")
    except Exception as e:
        logger.error(f"就诊提醒失败: {str(e)}")



@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis, cleanup_task, scheduler

    try:
        # 初始化 Redis
        redis = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            decode_responses=True,
            password=settings.REDIS_PASSWORD
        )

        # 测试 Redis 连接
        try:
            await asyncio.wait_for(redis.ping(), timeout=2)
            logger.info(" Redis connected successfully")
        except Exception as e:
            logger.critical(f" Redis connection failed: {e}")
            redis = None  # 防止后续 .aclose() 报错
            sys.exit(1)

        # 初始化数据库表（必要时）
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # 启动缺勤检测定时任务
        start_absence_scheduler()
        logger.info("✓ 缺勤检测定时任务已启动")
        
        # 启动 APScheduler 并注册候补队列同步任务（强制使用北京时间时区）
        scheduler = AsyncIOScheduler(timezone=BEIJING_TZ)
        scheduler.add_job(persist_waitlist_job, "interval", minutes=5, id="persist_waitlist")
        scheduler.add_job(check_payment_timeout_job, "interval", minutes=1, id="check_payment_timeout")
        # 添加就诊提醒任务：每天晚上20:00执行
        scheduler.add_job(send_appointment_reminder_job, "cron", hour=20, minute=0, id="appointment_reminder")
        scheduler.start()
        logger.info("✓ APScheduler 已启动")
        logger.info("  - 候补队列同步任务每 5 分钟执行一次")
        logger.info("  - 支付超时检查任务每 1 分钟执行一次")
        logger.info("  - 就诊提醒任务每天晚上 20:00 执行")

        logger.info(" Application startup complete")
        yield  # 应用正常运行

    except Exception as e:
        logger.critical(f" Application startup failed: {e}")
        sys.exit(1)

    finally:
        # 停止定时任务
        stop_absence_scheduler()
        logger.info("✓ 缺勤检测定时任务已停止")
        
        # 停止 APScheduler
        if scheduler and scheduler.running:
            scheduler.shutdown()
            logger.info("✓ APScheduler 已停止")
        
        # 清理 Redis
        if redis:
            try:
                await asyncio.wait_for(redis.aclose(), timeout=3)
                logger.info(" Redis connection closed")
            except asyncio.TimeoutError:
                logger.warning(" Redis close timed out")
            except Exception as e:
                logger.error(f"Redis close failed: {e}")

        # 关闭数据库引擎（可选）
        try:
            await engine.dispose()
            logger.info("DB engine disposed")
        except Exception as e:
            logger.warning(f"DB engine dispose failed: {e}")

        # 停止清理任务（如果有）
        if cleanup_task:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                logger.info(" Cleanup task cancelled")

        logger.info("Application shutdown complete")

app = FastAPI(title=settings.PROJECT_NAME,lifespan=lifespan)

# 挂载静态文件目录 (用于访问上传的图片)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
        
# 注册全局异常处理器
register_exception_handlers(app)

app.add_middleware(
    LogMiddleware
)

#中间件解决跨域(后续需扩展)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5000", "http://localhost:3000","http://47.116.175.206", "http://119.3.239.72"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


#引用子路由
try:
    app.include_router(router=auth.router, prefix="/auth", tags=["authentication"])
    app.include_router(router=statistics.router, prefix="/statistics", tags=["statistics"])
    app.include_router(router=admin.router, prefix="/admin", tags=["admin"])
    app.include_router(router=doctor.router, prefix="/doctor", tags=["doctor"])
    app.include_router(router=patient.router, prefix="/patient", tags=["patient"])
    app.include_router(router=common.router, prefix="/common", tags=["common"])
    logger.info("All routers registered successfully")
except Exception as e:
    logger.error(f"Failed to register routers: {e}", exc_info=True)
    raise

#默认
@app.get("/")
async def root():
    return {"message": "Welcome to BJTUHospital API"}