from sqlalchemy import Column, Integer, String, DateTime, Numeric
from sqlalchemy.orm import relationship
from app.db.base import Base


class HospitalArea(Base):
    """医院院区信息表"""
    __tablename__ = "hospital_area"
    
    area_id = Column(Integer, primary_key=True, autoincrement=True, comment="院区唯一 ID")
    name = Column(String(100), nullable=False, comment="院区名称")
    destination = Column(String(255), nullable=True, comment="院区物理地址")
    create_time = Column(DateTime, default=None, comment="创建时间")
    
    # 新增字段：经纬度与图片
    latitude = Column(Numeric(10, 7), nullable=True, comment="纬度，WGS84，精度7位小数")
    longitude = Column(Numeric(10, 7), nullable=True, comment="经度，WGS84，精度7位小数")
    image_url = Column(String(500), nullable=True, comment="院区图片 URL")
    
    # 关系字段
    clinics = relationship("Clinic", back_populates="hospital_area")
