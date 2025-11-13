from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.base import get_db
from app.api.auth import get_current_user
from app.schemas.user import user as UserSchema
from app.core.exception_handler import AuthHTTPException, BusinessHTTPException, ResourceHTTPException
from app.core.config import settings
from app.schemas.admin import AddSlotAuditCreate
from app.models.add_slot_audit import AddSlotAudit
from app.models.schedule import Schedule
from app.models.doctor import Doctor
from app.services.add_slot_service import execute_add_slot_and_register
from app.schemas.response import ResponseModel
from typing import Optional
from datetime import datetime

router = APIRouter()


@router.post("/schedules/add-slot", response_model=ResponseModel)
async def add_slot_request(
	data: AddSlotAuditCreate,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""医生发起加号申请或管理员直接创建加号记录（管理员跳过审核）。"""
	try:
		# 管理员直接执行加号并创建挂号记录
		if getattr(current_user, "is_admin", False):
			order_id = await execute_add_slot_and_register(
				db=db,
				schedule_id=data.schedule_id,
				patient_id=data.patient_id,
				slot_type=data.slot_type,
				applicant_user_id=current_user.user_id
			)
			return ResponseModel(code=0, message={"detail": "加号记录已创建", "order_id": order_id})

		# 非管理员必须是医生并且与目标排班医生匹配
		# 校验当前用户是否存在 doctor 记录
		res = await db.execute(select(Doctor).where(Doctor.user_id == current_user.user_id))
		db_doctor = res.scalar_one_or_none()
		if not db_doctor:
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅医生可发起加号申请", status_code=403)

		# 验证 schedule 是否存在且归属当前医生
		res = await db.execute(select(Schedule).where(Schedule.schedule_id == data.schedule_id))
		schedule = res.scalar_one_or_none()
		if not schedule:
			raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="排班不存在", status_code=404)
		if schedule.doctor_id != db_doctor.doctor_id:
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="医生只能为自己负责的排班申请加号", status_code=403)

		# 创建加号申请记录
		new_audit = AddSlotAudit(
			schedule_id=data.schedule_id,
			doctor_id=db_doctor.doctor_id,
			patient_id=data.patient_id,
			slot_type=data.slot_type,
			reason=data.reason,
			applicant_id=current_user.user_id,
			status="pending"
		)
		db.add(new_audit)
		await db.commit()
		await db.refresh(new_audit)

		return ResponseModel(code=0, message={"detail": "加号申请已提交，等待审核", "audit_id": new_audit.audit_id})

	except AuthHTTPException:
		raise
	except BusinessHTTPException:
		raise
	except ResourceHTTPException:
		raise
	except Exception as e:
		await db.rollback()
		raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"加号申请失败: {e}", status_code=500)

