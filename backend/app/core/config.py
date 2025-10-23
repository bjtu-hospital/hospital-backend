from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "BJTUHospital"    
    
    # 数据库配置
    DATABASE_URL: str
    
    #Token过期时间
    TOKEN_EXPIRE_TIME: int = 60*24
    #密钥(Token)
    SECRET_KEY: str = "HAJIMI"
    #加密方式(Token) HS256对称加密,RS256非对称加密
    TOKEN_ALGORITHM: str = "HS256"
    
    # 图像验证码配置
    CAPTCHA_EXPIRE_SECONDSl: int =300      # 验证码有效期
    CAPTCHA_LENGTH: int =4                # 验证码字符长度

    #对比天数
    COMPARE_DAYS: int = 3 
    
    # 邮箱专用URL
    YUN_URL: str
    EMAIL_VERIFY_EXPIRE_MINUTES: int = 30  # 邮箱验证链接有效期（分钟）
    LOGIN_EXPIRE_DAYS: int = 30  # 登录超时时间（天）
    
    # 邮箱配置
    EMAIL_FROM: str
    SMTP_SERVER: str
    SMTP_PORT: int
    SMTP_USER: str
    SMTP_PASSWORD: str  # QQ邮箱授权码
    
    # Redis配置
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_PASSWORD: str

    class Config:
        env_file = ".env"
    
    #正确返回码
    SUCCESS_CODE: int = 0 #正确返回码
    #错误码
    
    #主
    UNKNOWN_ERROR_CODE: int = 97 #未知错误
    HTTP_ERROR_CODE: int = 98 #HTTP错误
    REQ_ERROR_CODE: int = 99 #请求参数错误
    
    #auth
    REGISTER_FAILED_CODE: int = 100 #注册失败
    LOGIN_FAILED_CODE: int = 101 #登入失败
    INSUFFICIENT_AUTHORITY_CODE: int = 102 #权限不足
    USER_GET_FAILED_CODE: int = 103 #用户获取失败
    UPDATEPROFILE_FAILED_CODE: int = 104 #用户个人信息更新失败
    TOKEN_INVALID_CODE: int = 105 #Token失效
    CAPTCHA_GEN_FAILED_CODE: int = 106    # 验证码生成失败
    CAPTCHA_INVALID_CODE: int = 107       # 验证码ID无效
    CAPTCHA_MISMATCH_CODE: int = 108      # 验证码不匹配
    
    CAPTCHA_REQ_NEEDED_CODE: int = 109 #验证码请求
    
    EMAIL_VERIFIED_NEEDED_CODE: int = 110 #需要邮箱验证
    
    #traffic
    DATA_GET_FAILED_CODE: int = 301 #数据获取失败


settings = Settings()

