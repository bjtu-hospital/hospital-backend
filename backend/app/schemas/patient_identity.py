from pydantic import BaseModel


class IdentityVerifyRequest(BaseModel):
    """患者校内身份验证请求体"""
    identifier: str
    password: str
