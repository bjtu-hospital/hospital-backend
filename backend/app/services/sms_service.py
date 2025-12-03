import os
import time
import random
from typing import Optional
from alibabacloud_dypnsapi20170525.client import Client as Dypnsapi20170525Client
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_dypnsapi20170525 import models as dypnsapi_20170525_models
from alibabacloud_tea_util import models as util_models
from app.db.base import redis
from app.core.exception_handler import BusinessHTTPException
from app.core.config import settings

class SMSService:
    """短信验证码服务适配器。负责生成、发送、校验验证码与防刷控制。
    Redis Keys:
      - sms:code:{phone} -> {"code": str, "timestamp": float, "attempts": int}
      - sms:rate:{phone} -> 1 (TTL=RATE_LIMIT_SECONDS)
      - sms:verified:{phone} -> 1 (TTL=VERIFIED_WINDOW_SECONDS)
    """

    CODE_TTL_SECONDS = getattr(settings, "SMS_CODE_TTL_SECONDS", 300)  # 验证码有效期，默认5分钟
    RATE_LIMIT_SECONDS = getattr(settings, "SMS_RATE_LIMIT_SECONDS", 60)  # 发送间隔限制，默认60秒
    VERIFIED_WINDOW_SECONDS = getattr(settings, "SMS_VERIFIED_WINDOW_SECONDS", 900)  # 验证通过后的注册窗口，默认15分钟

    @staticmethod
    def _create_client() -> Dypnsapi20170525Client:
        ak = settings.ALI_ACCESS_KEY_ID
        sk = settings.ALI_ACCESS_KEY_SECRET
        if not ak or not sk:
            raise BusinessHTTPException("短信服务未配置，缺少 AK/SK")
        config = open_api_models.Config(access_key_id=ak, access_key_secret=sk)
        return Dypnsapi20170525Client(config)

    @staticmethod
    def _generate_code(length: int = 6) -> str:
        return ''.join(str(random.randint(0, 9)) for _ in range(length))

    @classmethod
    async def send_code(cls, phone: str) -> dict:
        # 基础节流：每手机号 60s
        rate_key = f"sms:rate:{phone}"
        if await redis.get(rate_key):
            raise BusinessHTTPException("发送过于频繁，请稍后再试")
        await redis.set(rate_key, "1", ex=cls.RATE_LIMIT_SECONDS)

        code = cls._generate_code(6)
        data = {"code": code, "timestamp": time.time(), "attempts": 0}
        await redis.set(f"sms:code:{phone}", str(data), ex=cls.CODE_TTL_SECONDS)

        # 发送短信
        client = cls._create_client()
        template_code = settings.SMS_TEMPLATE_CODE
        sign_name = settings.SMS_SIGN_NAME
        if not template_code or not sign_name:
            raise BusinessHTTPException("短信模板或签名未配置")
        req = dypnsapi_20170525_models.SendSmsVerifyCodeRequest(
            template_param=f'{{"code":"{code}","min":"5"}}',
            template_code=template_code,
            sign_name=sign_name,
            phone_number=phone,
        )
        runtime = util_models.RuntimeOptions()
        try:
            _ = client.send_sms_verify_code_with_options(req, runtime)
        except Exception as e:
            # 发送失败，清理验证码
            await redis.delete(f"sms:code:{phone}")
            raise BusinessHTTPException(f"短信发送失败：{str(e)}")
        return {"detail": "验证码已发送"}

    @classmethod
    async def verify_code(cls, phone: str, input_code: str) -> dict:
        raw = await redis.get(f"sms:code:{phone}")
        if not raw:
            raise BusinessHTTPException("验证码错误或已过期")
        # 简单解析（存储为 str 的 dict），避免引入额外依赖
        try:
            # 形如 {'code': '123456', 'timestamp': 173322...', 'attempts': 0}
            data = eval(raw)  # 仅用于内部受控数据；若有顾虑，可改为 json 序列化
        except Exception:
            await redis.delete(f"sms:code:{phone}")
            raise BusinessHTTPException("验证码状态异常，请重试")

        # 尝试次数限制
        attempts = int(data.get("attempts", 0))
        if attempts >= 3:
            await redis.delete(f"sms:code:{phone}")
            raise BusinessHTTPException("尝试次数过多，请重新获取验证码")

        # 时间检查
        if time.time() - float(data.get("timestamp", 0)) > cls.CODE_TTL_SECONDS:
            await redis.delete(f"sms:code:{phone}")
            raise BusinessHTTPException("验证码已过期，请重新获取")

        # 比对验证码
        if str(input_code) != str(data.get("code")):
            data["attempts"] = attempts + 1
            await redis.set(f"sms:code:{phone}", str(data), ex=cls.CODE_TTL_SECONDS)
            left = max(0, 3 - data["attempts"])
            raise BusinessHTTPException(f"验证码错误，还剩{left}次机会")

        # 成功：删除验证码，写入 verified 标记
        await redis.delete(f"sms:code:{phone}")
        await redis.set(f"sms:verified:{phone}", "1", ex=cls.VERIFIED_WINDOW_SECONDS)
        return {"detail": "验证码验证通过"}
