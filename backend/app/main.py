from fastapi import FastAPI,Depends, Request, Response,status,HTTPException
 
from fastapi.middleware.cors import CORSMiddleware
import logging
from contextlib import asynccontextmanager
import asyncio
import os
import sys
from redis.asyncio import Redis

from app.api import auth,statistics
from app.api import admin,doctor
from app.core.exception_handler import register_exception_handlers
from app.core.log_middleware import LogMiddleware
from app.core.config import settings
from app.db.base import engine,Base,redis
from app.core.cleantask import create_cleanup_task

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

# 全局变量存储清理任务
cleanup_task = None



@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis, cleanup_task

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

        logger.info(" Application startup complete")
        yield  # 应用正常运行

    except Exception as e:
        logger.critical(f" Application startup failed: {e}")
        sys.exit(1)

    finally:
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
    logger.info("All routers registered successfully")
except Exception as e:
    logger.error(f"Failed to register routers: {e}", exc_info=True)
    raise

#默认
@app.get("/")
async def root():
    return {"message": "Welcome to BJTUHospital API"}