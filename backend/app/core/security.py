from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, timedelta
from fastapi import Request
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from app.core.datetime_utils import get_now_naive
    
from app.core.config import settings
from app.db.base import redis
pwd_context = CryptContext(schemes=["bcrypt"], deprecated=["auto"])




#对原始密码进行hash加密
def get_hash_pwd(pwd: str):
    
    return pwd_context.hash(pwd)

#登入时验证密码
def verify_pwd(plain_pwd: str, hashed_pwd: str):

    return pwd_context.verify(plain_pwd, hashed_pwd)

#登入后获取Token
def create_access_token(data: dict, expires_delta: timedelta = None):
    
    to_encode = data.copy()
    expire = get_now_naive() + (expires_delta or timedelta(minutes=settings.TOKEN_EXPIRE_TIME))
    
    to_encode.update({"exp": expire})  
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.TOKEN_ALGORITHM)
    return encoded_jwt

#生成邮箱验证码
def generate_email_verify_token(email: str):
    expire = get_now_naive() + timedelta(minutes=settings.EMAIL_VERIFY_EXPIRE_MINUTES)
    to_encode = {"sub": email, "exp": expire}
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.TOKEN_ALGORITHM)


    
#验证邮箱验证码
def verify_email_token(token: str):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.TOKEN_ALGORITHM])
        email = payload.get("sub")
        return email
    except JWTError:
        return None

#发送邮箱
def send_email(to_email: str, subject: str, body: str):

    msg = MIMEText(body, 'html', 'utf-8')
    msg['From'] = settings.EMAIL_FROM
    msg['To'] = to_email
    msg['Subject'] = Header(subject, 'utf-8')
    try:
        with smtplib.SMTP_SSL(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.EMAIL_FROM, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"邮件发送失败: {e}")
        return False
    
async def get_user_id_from_request(request: Request) -> int | None:
    """
    从请求中提取用户ID(只提取,不抛异常)
    - 如果 token 缺失/无效，返回 None
    """
    token = None

    # 1) Authorization header (case-insensitive, 支持 'Bearer <token>' 或直接提供 token)
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
        else:
            # 兼容某些客户端直接放 token 的情况
            token = auth_header.strip()

    # 2) fallback: cookie 中可能存放 token（如前端以 cookie 保存）
    if not token:
        token = request.cookies.get("token") or request.cookies.get("access_token") or request.cookies.get("Authorization")

    # 3) fallback: query 参数 token
    if not token:
        token = request.query_params.get("token")

    if not token:
        return None

    # Redis 验证：token -> user_id
    try:
        user_id = await redis.get(f"token:{token}")
    except Exception:
        return None
    if not user_id:
        return None

    # JWT 验证（验证 sub 与 redis 中的 user_id 一致）
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.TOKEN_ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            return None
        if str(user_id) != str(sub):
            return None
        return int(sub)
    except JWTError:
        return None