"""微信小程序相关服务：access_token 缓存、code 换 openid、订阅消息发送、授权记录管理。

该服务的设计目标：
1) 失败不影响主业务流程（异常捕获、记录日志）。
2) 尽量使用 Redis 缓存 access_token，减少微信 API 调用次数。
3) 支持可选的 session_key 加密存储（使用 Fernet，未安装则降级为明文并记录告警）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.datetime_utils import get_now_naive
from app.core.exception_handler import BusinessHTTPException
from app.db.base import redis
from app.models.user import User
from app.models.wechat_message_log import WechatMessageLog
from app.models.wechat_subscribe_auth import WechatSubscribeAuth

logger = logging.getLogger(__name__)


class WechatService:
    """封装微信相关 API 与数据操作"""

    def __init__(self) -> None:
        self.app_id = settings.WECHAT_APP_ID
        self.app_secret = settings.WECHAT_APP_SECRET
        # 防守性设计：提前 10 分钟让缓存过期（而不是依赖微信的精确过期时间）
        # 微信返回的 expires_in 通常是 7200 秒，我们设为 6600 秒（110 分钟）来确保缓存在微信端过期前失效
        self.access_token_ttl = 6600  # 110 分钟，比微信默认的 7200 秒短 10 分钟
        # 干跑模式：不触达微信，仅记录日志与落库
        self.dry_run = bool(getattr(settings, "WECHAT_DRY_RUN", False))

    # ========== 基础能力 ========== #
    async def get_access_token(self) -> Optional[str]:
        """获取并缓存全局 access_token。失败时返回 None，不抛出异常以保护业务链路。
        
        设计说明：
        - 缓存 TTL 设为 6600 秒（110 分钟），防止使用微信端已失效的 token
        - 如果 Redis 获取失败，则自动向微信请求新 token
        - 网络错误或微信返回错误时，返回 None 并记录日志
        """
        if self.dry_run:
            return "mock_access_token"
        
        cache_key = f"wx:access_token:{self.app_id}"
        
        # 第一步：尝试从 Redis 读取缓存
        try:
            cached = await redis.get(cache_key)
            if cached:
                logger.debug("从 Redis 读取 access_token（缓存仍有效）")
                return cached
        except Exception as exc:
            logger.warning("从 Redis 读取 access_token 失败，将向微信重新请求: %s", exc)
        
        # 第二步：缓存不存在或已失效，向微信请求新 token
        url = "https://api.weixin.qq.com/cgi-bin/token"
        params = {
            "grant_type": "client_credential",
            "appid": self.app_id,
            "secret": self.app_secret,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params)
            data = resp.json()
        except Exception as exc:  # 网络/解析错误
            logger.error("获取微信 access_token 失败（网络错误）: %s", exc)
            return None

        # 检查微信返回是否成功
        errcode = data.get("errcode")
        if errcode not in (None, 0):
            logger.error(
                "获取微信 access_token 失败: errcode=%s, errmsg=%s",
                errcode,
                data.get("errmsg"),
            )
            return None

        access_token = data.get("access_token")
        if not access_token:
            logger.error("微信返回了 token，但 access_token 字段为空")
            return None
        
        # 使用微信返回的 expires_in（通常 7200），但缓存 TTL 设为 6600（提前 10 分钟失效）
        expires_in = int(data.get("expires_in", 7200))
        ttl = self.access_token_ttl  # 固定 110 分钟，或可用 min(expires_in - 600, self.access_token_ttl)
        
        # 第三步：缓存新 token
        try:
            await redis.set(cache_key, access_token, ex=ttl)
            logger.info(
                "成功获取新 access_token（缓存 TTL=%s 秒，微信 expires_in=%s 秒）",
                ttl,
                expires_in,
            )
        except Exception as exc:
            logger.warning("缓存 access_token 到 Redis 失败，但 token 有效可用: %s", exc)
        
        return access_token

    async def code_to_openid(self, code: str) -> Optional[Dict[str, Any]]:
        """使用 wx.login() 的 code 换取 openid/session_key。

        返回字典包含 openid、session_key、unionid（如有）。失败时返回 None。
        """
        if self.dry_run:
            tail = (code or "openid")[-16:]
            return {"openid": f"mock_{tail}", "session_key": "mock_session_key"}
        url = "https://api.weixin.qq.com/sns/jscode2session"
        params = {
            "appid": self.app_id,
            "secret": self.app_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params)
            data = resp.json()
        except Exception as exc:
            logger.error("code 换 openid 失败: %s", exc)
            return None

        if data.get("errcode") not in (None, 0):
            logger.warning("code 换 openid 失败: errcode=%s, errmsg=%s", data.get("errcode"), data.get("errmsg"))
            return None

        return data

    # ========== 数据写入 ========== #
    async def save_user_openid(
        self,
        db: AsyncSession,
        user_id: int,
        openid: str,
        session_key: Optional[str] = None,
        unionid: Optional[str] = None,
    ) -> None:
        """绑定 openid/session_key 到用户。失败抛 BusinessHTTPException。"""
        result = await db.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise BusinessHTTPException(code=settings.USER_GET_FAILED_CODE, msg="用户不存在")

        user.wechat_openid = openid
        user.wechat_bind_time = get_now_naive()
        if session_key:
            user.wechat_session_key = self._encrypt_session_key(session_key)
        if unionid:
            user.wechat_unionid = unionid

        db.add(user)
        await db.commit()

    async def save_subscribe_auth(
        self,
        db: AsyncSession,
        user_id: int,
        auth_result: Dict[str, str],
        scene: Optional[str] = None,
    ) -> None:
        """保存订阅授权结果。key 为模板ID，value 为授权状态。"""
        if not auth_result:
            return

        for template_id, status in auth_result.items():
            stmt = select(WechatSubscribeAuth).where(
                WechatSubscribeAuth.user_id == user_id,
                WechatSubscribeAuth.template_id == template_id,
            )
            result = await db.execute(stmt)
            record = result.scalar_one_or_none()
            if record:
                await db.execute(
                    update(WechatSubscribeAuth)
                    .where(WechatSubscribeAuth.id == record.id)
                    .values(
                        auth_status=status,
                        scene=scene,
                        updated_at=get_now_naive(),
                    )
                )
            else:
                db.add(
                    WechatSubscribeAuth(
                        user_id=user_id,
                        template_id=template_id,
                        auth_status=status,
                        scene=scene,
                    )
                )
        await db.commit()

    async def log_message(
        self,
        db: AsyncSession,
        user_id: int,
        openid: str,
        template_id: str,
        scene: Optional[str],
        order_id: Optional[int],
        status: str,
        request_data: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
        error_code: Optional[int] = None,
        error_message: Optional[str] = None,
        sent_at: Optional[datetime] = None,
    ) -> None:
        """写入消息发送日志。"""
        log = WechatMessageLog(
            user_id=user_id,
            openid=openid,
            template_id=template_id,
            scene=scene,
            order_id=order_id,
            status=status,
            error_code=error_code,
            error_message=error_message,
            request_data=json.dumps(request_data, ensure_ascii=False) if request_data else None,
            response_data=json.dumps(response_data, ensure_ascii=False) if response_data else None,
            sent_at=sent_at,
        )
        db.add(log)
        await db.commit()

    # ========== 查询辅助 ========== #
    async def get_user_openid(self, db: AsyncSession, user_id: int) -> Optional[str]:
        result = await db.execute(select(User.wechat_openid).where(User.user_id == user_id))
        return result.scalar_one_or_none()

    async def check_user_authorized(self, db: AsyncSession, user_id: int, template_id: str) -> bool:
        stmt = (
            select(WechatSubscribeAuth)
            .where(
                WechatSubscribeAuth.user_id == user_id,
                WechatSubscribeAuth.template_id == template_id,
                WechatSubscribeAuth.auth_status == "accept",
            )
            .order_by(WechatSubscribeAuth.updated_at.desc())
        )
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        return record is not None

    # ========== 订阅消息发送 ========== #
    async def send_subscribe_message(
        self,
        db: AsyncSession,
        user_id: int,
        openid: str,
        template_id: str,
        data: Dict[str, Any],
        scene: Optional[str] = None,
        order_id: Optional[int] = None,
        page: Optional[str] = None,
    ) -> None:
        """发送订阅消息。

        失败时仅记录日志，不抛异常，以免中断业务流程。
        """
        # 验证消息数据的有效性
        for field_name, field_data in data.items():
            if isinstance(field_data, dict) and "value" in field_data:
                value = field_data.get("value")
                # 检查字段值是否为空或仅包含空格
                if not value or (isinstance(value, str) and len(value.strip()) == 0):
                    logger.error(f"微信消息数据验证失败: {field_name}.value 为空。data={data}")
                    return
        
        # 干跑模式：跳过真实请求，直接落成功日志
        if self.dry_run:
            payload = {
                "touser": openid,
                "template_id": template_id,
                "data": data,
            }
            if page:
                payload["page"] = page
            await self.log_message(
                db,
                user_id,
                openid,
                template_id,
                scene,
                order_id,
                status="success",
                request_data=payload,
                response_data={"dry_run": True, "note": "skipped real request"},
                sent_at=get_now_naive(),
            )
            return
        
        # 第一次尝试：使用缓存的 token
        access_token = await self.get_access_token()
        if not access_token:
            logger.error("发送订阅消息失败：无法获取 access_token")
            await self.log_message(
                db,
                user_id,
                openid,
                template_id,
                scene,
                order_id,
                status="failed",
                request_data=data,
                response_data={"err": "no_access_token"},
            )
            return

        payload = {
            "touser": openid,
            "template_id": template_id,
            "data": data,
        }
        if page:
            payload["page"] = page

        # 尝试发送消息（最多2次：第1次用缓存token，若40001则刷新后第2次）
        for attempt in range(2):
            url = f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={access_token}"
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(url, json=payload)
                resp_data = resp.json()
            except Exception as exc:
                logger.error("订阅消息发送失败（请求错误）: %s", exc)
                await self.log_message(
                    db,
                    user_id,
                    openid,
                    template_id,
                    scene,
                    order_id,
                    status="failed",
                    request_data=payload,
                    response_data={"exception": str(exc)},
                )
                return

            errcode = resp_data.get("errcode", -1)
            errmsg = resp_data.get("errmsg")
            
            # 成功
            if errcode == 0:
                await self.log_message(
                    db,
                    user_id,
                    openid,
                    template_id,
                    scene,
                    order_id,
                    status="success",
                    request_data=payload,
                    response_data=resp_data,
                    error_code=None,
                    error_message=None,
                    sent_at=get_now_naive(),
                )
                return
            
            # 40001: Token 无效或过期，需要刷新
            if errcode == 40001 and attempt == 0:
                logger.warning("Access Token 失效（errcode=40001），准备清除缓存并重新获取")
                # 清除缓存，强制重新获取
                cache_key = f"wx:access_token:{self.app_id}"
                await redis.delete(cache_key)
                access_token = await self.get_access_token()
                if not access_token:
                    logger.error("重新获取 access_token 失败，放弃重试")
                    await self.log_message(
                        db,
                        user_id,
                        openid,
                        template_id,
                        scene,
                        order_id,
                        status="failed",
                        request_data=payload,
                        response_data={"err": "refresh_token_failed", "original_errcode": 40001},
                        error_code=40001,
                        error_message="Token 无效且无法刷新",
                        sent_at=get_now_naive(),
                    )
                    return
                # 继续循环，用新 token 重试
                continue
            
            # 其他错误，直接记录并返回
            logger.error(f"订阅消息发送失败: errcode={errcode}, errmsg={errmsg}")
            await self.log_message(
                db,
                user_id,
                openid,
                template_id,
                scene,
                order_id,
                status="failed",
                request_data=payload,
                response_data=resp_data,
                error_code=errcode,
                error_message=errmsg,
                sent_at=get_now_naive(),
            )
            return

    # ========== 工具方法 ========== #
    def _encrypt_session_key(self, session_key: str) -> str:
        """可选的 session_key 加密。
        若未配置密钥或未安装 cryptography，则返回原文并记录一次警告。
        """
        if not session_key:
            return session_key

        cipher_key = getattr(settings, "WECHAT_SESSION_KEY_CIPHER", None)
        if not cipher_key:
            return session_key

        try:
            from cryptography.fernet import Fernet
        except Exception:
            logger.warning("cryptography 未安装，session_key 将以明文存储。建议安装 cryptography 并配置 WECHAT_SESSION_KEY_CIPHER。")
            return session_key

        try:
            cipher = Fernet(cipher_key.encode())
            return cipher.encrypt(session_key.encode()).decode()
        except Exception as exc:
            logger.warning("session_key 加密失败，使用明文存储: %s", exc)
            return session_key

    @staticmethod
    def mask_openid(openid: Optional[str]) -> str:
        if not openid:
            return ""
        if len(openid) <= 8:
            return openid
        return f"{openid[:4]}****{openid[-4:]}"
