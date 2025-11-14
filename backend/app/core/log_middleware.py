import time
from fastapi import Request,Depends
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
import json


from app.core.security import get_user_id_from_request  # 根据Token获取用户信息
from app.db.base import redis,get_db,UserAccessLog  # 你封装的 Redis 客户端
from app.services.risk_detection_service import risk_detection_service
from sqlalchemy.ext.asyncio import AsyncSession

async def save_log_to_db(log_data: dict):
    """将访问日志写入数据库"""

    async for db in get_db():  # 手动获取 AsyncSession
        log_entry = UserAccessLog(**log_data)
        try:
            db.add(log_entry)
            await db.commit()
            await db.refresh(log_entry)
        except Exception as e:
            await db.rollback()
            print(f"日志写入失败: {e}")
        break  # 退出 generator，避免警告


class LogMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        
        #请求前
        start_time = time.time()
        
        #执行业务逻辑拿到对应的响应
        response = await call_next(request)
        
        #请求耗时
        process_time = int((time.time() - start_time) * 1000)

        # 从 token 中解出 user_id
        user_id = await get_user_id_from_request(request)  


        # 读取响应 body（需要处理 Streaming 的 response）
        response_body = b""
        async for chunk in response.body_iterator:
            response_body += chunk
        # 设置异步响应体
        async def reset_body():
            yield response_body

        response.body_iterator = reset_body()

        # 尝试解析 JSON 获取 code 字段
        response_code = None
        try:
            body_data = json.loads(response_body)
            response_code = body_data.get("code")
        except Exception:
            pass  # 忽略非 JSON 响应
        
        log_data = {
            "user_id": user_id,
            "ip": request.client.host,
            "ua": request.headers.get("user-agent"),
            "url": str(request.url),
            "method": request.method,
            "status_code": response.status_code,
            "response_code": response_code,
            "access_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_ms": process_time,
        }

        # 挂号接口风险检测 (仅在成功响应时执行)
        # try:
        #     if response.status_code == 200 and request.method == "POST" and request.url.path.startswith("/registration") and user_id:
        #         async for db in get_db():
        #             try:
        #                 await risk_detection_service.detect_registration_risk(db, user_id)
        #                 break
        #             except Exception:
        #                 break
        # except Exception:
        #     pass

        # Redis 去抖逻辑
        key = f"logdedup:{user_id}:{request.url.path}"
        if not await redis.exists(key):
            await redis.setex(key, 5, 1)
            await save_log_to_db(log_data)
            
        return response
