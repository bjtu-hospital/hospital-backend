from sqlalchemy import Column, BigInteger, Integer, String, Date, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.core.datetime_utils import beijing_now_for_model


class VisitHistory(Base):
    """
    患者就诊历史表：记录患者在医院的每次就诊，以及医生给出的诊断意见/处置建议/处方等。
    - patient_id: 关联 Patient.patient_id
    - doctor_id: 关联 Doctor.doctor_id
    - order_id: 可选，关联当次就诊的订单 registration_order.order_id
    - visit_date: 就诊日期
    - diagnosis: 医生的诊断结论（文本）
    - advice: 医生的处理建议或处置（文本）
    - prescription: 医生开具处方或药品清单（文本/JSON 字符串）
    - attachments: 可选的附件路径或资源描述（例如检查图片、检验单）
    - followup_required: 是否需要复诊
    - followup_date: 建议复诊日期（可选）
    """

    __tablename__ = "visit_history"

    visit_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="就诊记录ID")
    patient_id = Column(BigInteger, ForeignKey("patient.patient_id"), nullable=False, comment="关联 patient.patient_id")
    doctor_id = Column(Integer, ForeignKey("doctor.doctor_id"), nullable=True, comment="关联 doctor.doctor_id")
    order_id = Column(BigInteger, ForeignKey("registration_order.order_id"), nullable=True, comment="可选：关联订单")

    visit_date = Column(Date, nullable=False, comment="就诊日期")
    diagnosis = Column(Text, nullable=True, comment="医生诊断结论")
    advice = Column(Text, nullable=True, comment="医生建议/处置")
    prescription = Column(Text, nullable=True, comment="处方/药品清单 (文本或 JSON 字符串)")
    attachments = Column(Text, nullable=True, comment="附件路径或描述(JSON)")

    followup_required = Column(Boolean, default=False, comment="是否需要复诊")
    followup_date = Column(Date, nullable=True, comment="建议复诊日期")

    create_time = Column(DateTime, default=beijing_now_for_model, comment="创建时间")
    update_time = Column(DateTime, default=beijing_now_for_model, comment="最后更新时间")

    # 关系
    patient = relationship("Patient")
    doctor = relationship("Doctor")
    order = relationship("RegistrationOrder")
