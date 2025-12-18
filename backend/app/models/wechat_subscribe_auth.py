"""微信订阅消息授权记录表模型"""
from sqlalchemy import Column, Integer, String, DateTime, Index
from sqlalchemy.sql import func
from app.db.base import Base


class WechatSubscribeAuth(Base):
    """微信订阅消息授权记录表
    
    记录用户对各个订阅消息模板的授权状态
    用于发送消息前检查用户是否授权
    """
    __tablename__ = "wechat_subscribe_auth"
    
    id = Column(Integer, primary_key=True, index=True, comment="主键ID")
    
    user_id = Column(
        Integer, 
        nullable=False, 
        index=True,
        comment="用户ID，关联 user 表"
    )
    
    template_id = Column(
        String(64),
        nullable=False,
        comment="微信订阅消息模板ID"
    )
    
    auth_status = Column(
        String(20),
        nullable=False,
        comment="授权状态: accept(用户同意), reject(用户拒绝), ban(用户已禁用)"
    )
    
    scene = Column(
        String(50),
        nullable=True,
        comment="业务场景标识: appointment(预约), waitlist(候补), reschedule(改约), cancel(取消)"
    )
    
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        comment="创建时间"
    )
    
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间"
    )
    
    # 创建联合唯一索引：一个用户对一个模板只保留最新的授权记录
    __table_args__ = (
        Index('idx_user_template', 'user_id', 'template_id'),
        {'comment': '微信订阅消息授权记录表'}
    )
