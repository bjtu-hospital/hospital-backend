from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.datetime_utils import beijing_now_for_model


from app.db.base import Base


#用户日志数据库表
class UserAccessLog(Base):
    __tablename__ = "user_access_log"

    user_access_log_id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    user_id = Column(Integer, ForeignKey("user.user_id"), nullable=True, comment="用户ID,未登录为NULL")
    ip = Column(String(45), nullable=False, comment="访问IP地址")
    ua = Column(Text, nullable=True, comment="User-Agent请求头")
    url = Column(Text, nullable=False, comment="请求的完整URL地址")
    method = Column(String(10), nullable=False, comment="请求方法")
    status_code = Column(Integer, nullable=False, comment="HTTP响应状态码")
    response_code = Column(Integer, nullable=True, comment="业务返回码")
    access_time = Column(DateTime, default=beijing_now_for_model, comment="访问时间")
    duration_ms = Column(Integer, nullable=False, comment="请求耗时（毫秒）")
    
    #与user表为多对一的关系
    user = relationship("User", back_populates = "user_access_logs")
