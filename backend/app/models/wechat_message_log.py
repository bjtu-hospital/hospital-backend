"""微信订阅消息发送日志表模型"""
from sqlalchemy import Column, Integer, String, DateTime, Text, Index
from sqlalchemy.sql import func
from app.db.base import Base


class WechatMessageLog(Base):
    """微信订阅消息发送日志表
    
    记录所有订阅消息的发送情况，包括成功和失败记录
    用于追踪消息发送状态、排查问题、统计分析
    """
    __tablename__ = "wechat_message_log"
    
    id = Column(Integer, primary_key=True, index=True, comment="主键ID")
    
    user_id = Column(
        Integer,
        nullable=False,
        index=True,
        comment="用户ID，关联 user 表"
    )
    
    openid = Column(
        String(128),
        nullable=False,
        index=True,
        comment="微信 openid（脱敏显示前4后4）"
    )
    
    template_id = Column(
        String(64),
        nullable=False,
        comment="微信订阅消息模板ID"
    )
    
    scene = Column(
        String(50),
        nullable=True,
        comment="业务场景标识: appointment(预约), waitlist(候补), reschedule(改约), cancel(取消), reminder(提醒)"
    )
    
    order_id = Column(
        Integer,
        nullable=True,
        index=True,
        comment="关联的订单ID（RegistrationOrder 表）"
    )
    
    status = Column(
        String(20),
        nullable=False,
        default='pending',
        comment="发送状态: pending(待发送), success(成功), failed(失败)"
    )
    
    error_code = Column(
        Integer,
        nullable=True,
        comment="微信返回的错误码"
    )
    
    error_message = Column(
        Text,
        nullable=True,
        comment="错误信息详情"
    )
    
    request_data = Column(
        Text,
        nullable=True,
        comment="发送请求的数据（JSON格式，用于排查问题）"
    )
    
    response_data = Column(
        Text,
        nullable=True,
        comment="微信返回的响应数据（JSON格式）"
    )
    
    sent_at = Column(
        DateTime,
        nullable=True,
        comment="实际发送时间"
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
    
    # 创建索引以优化查询
    __table_args__ = (
        Index('idx_user_status', 'user_id', 'status'),
        Index('idx_order_scene', 'order_id', 'scene'),
        Index('idx_created_at', 'created_at'),
        {'comment': '微信订阅消息发送日志表'}
    )
