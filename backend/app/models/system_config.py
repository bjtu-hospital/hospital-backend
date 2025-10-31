from sqlalchemy import Column, Integer, String, DateTime, Boolean, JSON, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
from app.db.base import Base


class SystemConfig(Base):
    """
    系统通用配置表 (system_config) 的 SQLAlchemy ORM 模型
    对应 SQL DDL 结构
    """
    
    __tablename__ = 'system_config'

    # 主键
    config_id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment='配置项唯一ID')

    # 核心键名
    config_key = Column(String(128), nullable=False, comment='配置键名 (格式: 模块.子模块.参数)')

    # 配置分层字段
    scope_type = Column(String(32), nullable=False, default='GLOBAL', comment='配置应用范围类型: GLOBAL, MODULE, CLINIC, DOCTOR, MINOR_DEPT')
    scope_id = Column(Integer, nullable=True, comment='应用范围的实体ID (如: clinic_id, doctor_id). GLOBAL时为NULL')

    # 配置值及类型
    # 使用 SQLAlchemy 的 JSON 类型，便于数据库存储和Python字典/列表的交互
    config_value = Column(JSON, nullable=False, comment='配置值 (JSON)')
    data_type = Column(String(32), nullable=False, comment='值的预期数据类型: STRING, INT, FLOAT, BOOLEAN, JSON')

    # 描述与状态
    description = Column(String(255), nullable=True, comment='配置项用途描述')
    is_active = Column(Boolean, nullable=False, default=True, comment='是否启用 (1: 启用, 0: 禁用)')

    # 审计字段 (时间戳)
    create_time = Column(DateTime, nullable=False, default=datetime.utcnow, comment='创建时间')
    # onupdate=datetime.utcnow 实现自动更新时间
    update_time = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    # 确保在同一 Scope 内，键名是唯一的 (实现配置分层的关键)
    __table_args__ = (
        UniqueConstraint('scope_type', 'scope_id', 'config_key', name='uk_scope_key'),
        {'comment': '系统通用配置表'}
    )

    def __repr__(self):
        return f"<SystemConfig(key='{self.config_key}', scope='{self.scope_type}:{self.scope_id}')>"