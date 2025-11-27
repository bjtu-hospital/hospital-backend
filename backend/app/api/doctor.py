from fastapi import APIRouter, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from app.db.base import get_db
from app.api.auth import get_current_user
from app.schemas.user import user as UserSchema
from app.core.exception_handler import AuthHTTPException, BusinessHTTPException, ResourceHTTPException
from app.core.config import settings
from app.schemas.admin import AddSlotAuditCreate
from app.models.add_slot_audit import AddSlotAudit
from app.models.schedule import Schedule
from app.models.doctor import Doctor
from app.models.registration_order import RegistrationOrder, OrderStatus
from app.models.minor_department import MinorDepartment
from app.models.attendance_record import AttendanceRecord, AttendanceStatus
from app.models.clinic import Clinic
from app.models.system_config import SystemConfig
from app.schemas.workbench import (
	WorkbenchDashboardResponse,
	CheckinResponse,
	CheckoutResponse,
	ShiftsResponse,
	ConsultationStatsResponse,
	RecentConsultationsResponse,
	AttendanceRecordsResponse,
	AttendanceRecordItem,
	WorkbenchDoctorInfo,
	WorkbenchShiftStatus,
	WorkbenchCurrentShift,
	WorkbenchTodayData,
	WorkbenchReminder,
	WorkbenchRecentRecord,
	ShiftItem,
	RecentConsultationItem
)
from app.schemas.leave import (
	DayScheduleItem,
	LeaveApplyRequest,
	LeaveHistoryItem,
	ShiftEnum
)
from app.models.leave_audit import LeaveAudit
from app.schemas.audit import (
	LeaveAuditItem,
	LeaveAuditListResponse,
	AuditAction,
	AuditActionResponse,
)
from app.services.admin_helpers import calculate_leave_days
from app.models.administrator import Administrator
from app.models.patient import Patient
from app.models.visit_history import VisitHistory
from app.db.base import redis
from app.services.add_slot_service import execute_add_slot_and_register
from app.services.config_service import get_schedule_config
from app.services.consultation_service import (
	get_consultation_queue,
	call_next_patient,
	pass_patient,
	complete_current_patient
)
from app.schemas.response import ResponseModel
from typing import Optional
from datetime import datetime, date, timezone, timedelta
import json

router = APIRouter()
# ===================== 科室长排班模块 =====================

from typing import List, Dict, Any
import json as _json


@router.get("/schedule/clinics", response_model=ResponseModel)
async def schedule_clinics(
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""获取当前登录用户（科室长）管理的门诊/科室列表。
	返回结构遵循设计文档：Array<{id,name,totalSlots,filledSlots}>。
	"""
	try:
		# 当前用户绑定医生，且必须为科室长
		doctor = await _get_doctor(db, current_user)
		if not getattr(doctor, "is_department_head", False):
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅科室长可访问该接口", status_code=403)

		# 取本科室的门诊列表（以 Clinic 关联 MinorDepartment 或通过科室ID过滤）
		# 由于模型未给出科室-门诊的直接映射，这里返回该科室下所有排班涉及的门诊聚合
		sched_res = await db.execute(
			select(Schedule.clinic_id, Clinic.name)
			.join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
			.where(Schedule.doctor_id == doctor.doctor_id)
			.group_by(Schedule.clinic_id, Clinic.name)
		)
		rows = sched_res.all()

		data = []
		for cid, cname in rows:
			# 统计本周（以今天所在周）总名额与已排名额（简化：用当周已有记录计数）
			# 可根据业务需要扩展统计逻辑
			data.append({
				"id": str(cid),
				"name": cname,
				"totalSlots": None,
				"filledSlots": None,
			})

		return ResponseModel(code=0, message=data)
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取门诊列表失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"获取门诊列表失败: {str(e)}",
			status_code=500
		)


@router.get("/schedule/list", response_model=ResponseModel)
async def schedule_list(
	clinicId: int,
	startDate: str,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""获取指定门诊在特定周的排班详情。
	返回 Array<ScheduleSlot>，含 filled/empty/unavailable。
	"""
	try:
		doctor = await _get_doctor(db, current_user)
		if not getattr(doctor, "is_department_head", False):
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅科室长可访问该接口", status_code=403)

		try:
			week_start = datetime.strptime(startDate, "%Y-%m-%d").date()
		except ValueError:
			raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="startDate 格式错误", status_code=400)

		# 查询一周内该门诊所有排班
		week_dates = [week_start + timedelta(days=i) for i in range(7)]
		res = await db.execute(
			select(Schedule)
			.options(selectinload(Schedule.doctor))
			.where(and_(Schedule.clinic_id == clinicId, Schedule.date.in_(week_dates)))
		)
		schedules = res.scalars().all()

		# 构造日历：每天三班（上午/下午/晚上），默认 empty
		slots: List[Dict[str, Any]] = []
		for d in week_dates:
			for idx, shift in enumerate(["morning", "afternoon", "night"]):
				# 找到对应时间段的记录
				found = next((s for s in schedules if s.date == d and s.time_section in ["上午", "早", "morning"] and idx == 0), None)
				if idx == 1:
					found = next((s for s in schedules if s.date == d and s.time_section in ["下午", "after", "afternoon"]), None)
				if idx == 2:
					found = next((s for s in schedules if s.date == d and s.time_section not in ["上午", "早", "morning", "下午", "after", "afternoon"]), None)

				item: Dict[str, Any] = {
					"date": d.strftime("%Y-%m-%d"),
					"dayOfWeek": d.isoweekday(),
					"shift": shift,
					"status": "empty",
				}
				if found:
					item.update({
						"status": "filled",
						"doctorId": str(found.doctor_id),
						# 优先使用关联的 Doctor 记录名称
						"doctorName": (found.doctor.name if getattr(found, "doctor", None) else None),
						"doctorTitle": (found.doctor.title if getattr(found, "doctor", None) else None),
					})
				slots.append(item)

		return ResponseModel(code=0, message=slots)
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取排班列表失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"获取排班列表失败: {str(e)}",
			status_code=500
		)


@router.get("/schedule/available-doctors", response_model=ResponseModel)
async def schedule_available_doctors(
	clinicId: int,
	date: str,
	shift: str,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""为某一日期与班次返回科室医生的可用状态（available/conflict/leave）。"""
	try:
		doctor = await _get_doctor(db, current_user)
		if not getattr(doctor, "is_department_head", False):
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅科室长可访问该接口", status_code=403)

		try:
			target_date = datetime.strptime(date, "%Y-%m-%d").date()
		except ValueError:
			raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="date 格式错误", status_code=400)

		# 科室下所有医生
		dres = await db.execute(select(Doctor).where(Doctor.dept_id == doctor.dept_id))
		doctors = dres.scalars().all()

		# 查询该日期该门诊的已排班
		sres = await db.execute(select(Schedule).where(and_(Schedule.clinic_id == clinicId, Schedule.date == target_date)))
		day_scheds = sres.scalars().all()

		# 查询该日期的请假申请（已批准或待审核都视为不可用）
		lres = await db.execute(select(LeaveAudit).where(and_(LeaveAudit.leave_start_date <= target_date, LeaveAudit.leave_end_date >= target_date)))
		leaves = lres.scalars().all()
		leave_doctor_ids = {lv.doctor_id for lv in leaves}

		data: List[Dict[str, Any]] = []
		for d in doctors:
			status = "available"
			conflict_reason = None
			# 冲突：该医生在该日该门诊已有任一班次排班
			if any(s.doctor_id == d.doctor_id for s in day_scheds):
				status = "conflict"
				conflict_reason = "当天已有排班"
			# 请假：当日跨越该医生请假
			if d.doctor_id in leave_doctor_ids:
				status = "leave"
			# 为了前端字段一致性，提供 doctorId/doctorName，同时保留 id/name 兼容
			data.append({
				"id": str(d.doctor_id),
				"name": d.name,
				"doctorName": d.name,
				"title": d.title,
				"dept": doctor.dept_id,
				"status": status,
				"conflictReason": conflict_reason,
				"assignedCount": None,
			})

		return ResponseModel(code=0, message=data)
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取可用医生失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"获取可用医生失败: {str(e)}",
			status_code=500
		)


@router.post("/schedule/submit-change", response_model=ResponseModel)
async def schedule_submit_change(
	body: Dict[str, Any],
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""提交排班调整申请，生成 ScheduleAudit 记录，状态 pending，待管理员审核。"""
	try:
		doctor = await _get_doctor(db, current_user)
		if not getattr(doctor, "is_department_head", False):
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅科室长可访问该接口", status_code=403)

		clinic_id = body.get("clinicId")
		changes = body.get("changes") or []
		if not clinic_id or not isinstance(changes, list):
			raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="请求参数错误", status_code=400)

		# 将变更转换为 7x3 的周排班矩阵（简化：按提交的 date+shift 生成矩阵）
		# 确定周一
		try:
			# 找到最小日期作为周起始（或由前端传 startDate 更好），为简化仅聚合到周维度
			dates = [datetime.strptime(c.get("date"), "%Y-%m-%d").date() for c in changes if c.get("date")]
			if not dates:
				raise ValueError
			week_start = min(dates)
			week_start = week_start - timedelta(days=week_start.isoweekday() - 1)  # 调整到周一
		except Exception:
			raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="changes 中日期格式错误", status_code=400)

		matrix: List[List[Any]] = [[None, None, None] for _ in range(7)]
		for c in changes:
			cdate = datetime.strptime(c["date"], "%Y-%m-%d").date()
			day_idx = (cdate - week_start).days
			if day_idx < 0 or day_idx > 6:
				continue
			shift = c.get("shift")
			slot_idx = {"morning": 0, "afternoon": 1, "night": 2}.get(shift, None)
			if slot_idx is None:
				continue
			doctor_id = c.get("doctorId")
			if doctor_id in (None, "", "null"):
				matrix[day_idx][slot_idx] = None
			else:
				matrix[day_idx][slot_idx] = {"doctor_id": int(doctor_id)}

		# 写入 ScheduleAudit
		from app.models.schedule_audit import ScheduleAudit
		audit = ScheduleAudit(
			minor_dept_id=doctor.dept_id,
			clinic_id=clinic_id,
			submitter_doctor_id=doctor.doctor_id,
			submit_time=datetime.now(),
			week_start_date=week_start,
			week_end_date=week_start + timedelta(days=6),
			remark="科室长提交排班调整",
			status="pending",
			schedule_data_json=matrix,
		)
		db.add(audit)
		await db.commit()
		await db.refresh(audit)

		return ResponseModel(code=0, message={"msg": "申请已提交，等待审核", "auditId": audit.audit_id})
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		await db.rollback()
		import logging
		logging.getLogger(__name__).error(f"提交排班调整失败: {e}")
		raise BusinessHTTPException(
			code=settings.REQ_ERROR_CODE,
			msg=f"提交排班调整失败: {str(e)}",
			status_code=500
		)


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
				applicant_user_id=current_user.user_id,
				position=data.position or "end"
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


# ===== 医生工作台相关辅助函数 =====

async def _get_time_section_config(
	db: AsyncSession,
	time_section: str,
	clinic_id: int | None = None
) -> tuple:
	"""
	分级查询时间段配置
	优先级: CLINIC > GLOBAL
	返回: (start_time, end_time)
	"""
	# 使用配置服务获取排班配置
	schedule_config = await get_schedule_config(
		db,
		scope_type="CLINIC" if clinic_id else "GLOBAL",
		scope_id=clinic_id
	)
	
	# 根据时间段返回对应的开始和结束时间
	if time_section in ["上午", "早", "morning"]:
		return (
			schedule_config.get("morningStart", "08:00"),
			schedule_config.get("morningEnd", "12:00")
		)
	elif time_section in ["下午", "after", "afternoon"]:
		return (
			schedule_config.get("afternoonStart", "13:30"),
			schedule_config.get("afternoonEnd", "17:30")
		)
	else:  # 晚间
		return (
			schedule_config.get("eveningStart", "18:00"),
			schedule_config.get("eveningEnd", "21:00")
		)


def _human_duration(start: datetime, end: datetime) -> str:
	delta = end - start
	minutes = int(delta.total_seconds() // 60)
	hours = minutes // 60
	mins = minutes % 60
	if hours == 0:
		return f"{mins}分钟"
	return f"{hours}小时{mins}分钟"


async def _get_doctor(db: AsyncSession, current_user: UserSchema) -> Doctor:
	res = await db.execute(select(Doctor).where(Doctor.user_id == current_user.user_id))
	doctor = res.scalar_one_or_none()
	if not doctor:
		raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅医生可访问该接口", status_code=403)
	return doctor


async def _get_today_schedules(db: AsyncSession, doctor_id: int) -> list[Schedule]:
	res = await db.execute(select(Schedule).where(and_(Schedule.doctor_id == doctor_id, Schedule.date == date.today())))
	return res.scalars().all()


async def _load_shift_state(schedule_id: int) -> dict:
	"""优先从 Redis 读取，若无则尝试从数据库读取最新考勤记录"""
	raw = await redis.get(f"workshift:{schedule_id}")
	if raw:
		try:
			return json.loads(raw)
		except Exception:
			pass
	# Redis 无数据，尝试从数据库读取
	from app.db.base import AsyncSessionLocal
	async with AsyncSessionLocal() as db:
		try:
			res = await db.execute(
				select(AttendanceRecord)
				.where(AttendanceRecord.schedule_id == schedule_id)
				.order_by(AttendanceRecord.created_at.desc())
			)
			record = res.scalars().first()
			if record:
				state = {}
				if record.checkin_time:
					state["checkin_time"] = record.checkin_time.strftime("%H:%M")
				if record.checkout_time:
					state["checkout_time"] = record.checkout_time.strftime("%H:%M")
				return state
		except Exception:
			pass
	return {}


async def _save_shift_state(schedule_id: int, state: dict, ttl_hours: int = 24):
	await redis.setex(f"workshift:{schedule_id}", ttl_hours * 3600, json.dumps(state, ensure_ascii=False))


# ====== 工作台接口实现 ======

@router.get("/workbench/dashboard", response_model=ResponseModel[WorkbenchDashboardResponse])
async def workbench_dashboard(db: AsyncSession = Depends(get_db), current_user: UserSchema = Depends(get_current_user)):
	try:
		doctor = await _get_doctor(db, current_user)
		# 部门
		dept_res = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == doctor.dept_id))
		dept = dept_res.scalar_one_or_none()

		# 获取医生今天的排班信息
		today = datetime.now(timezone.utc).date()
		stmt = (
			select(Schedule)
			.options(selectinload(Schedule.clinic))
			.where(Schedule.doctor_id == current_user.user_id, Schedule.date == today)
		)
		result = await db.execute(stmt)
		schedules = result.scalars().all()

		schedule_details = []
		now = datetime.now()  # 使用本地时间而非UTC
		current_shift_obj = None
		shift_status_value = "checked_out"
		checkin_time = None
		checkout_time = None
		work_duration = None
		time_to_checkout = None
		countdown = None

		# 选择当前或下一个排班
		sorted_scheds = []
		for s in schedules:
			start_str, end_str = await _get_time_section_config(db, s.time_section, s.clinic_id)
			start_dt = datetime.combine(s.date, datetime.strptime(start_str, "%H:%M").time())
			end_dt = datetime.combine(s.date, datetime.strptime(end_str, "%H:%M").time())
			sorted_scheds.append((s, start_dt, end_dt))
		sorted_scheds.sort(key=lambda x: x[1])

		for s, start_dt, end_dt in sorted_scheds:
			state = await _load_shift_state(s.schedule_id)
			if start_dt <= now <= end_dt:
				# 活跃排班
				current_shift_obj = (s, start_dt, end_dt, state)
				break
			if now < start_dt and not current_shift_obj:
				# 下一个未来排班
				current_shift_obj = (s, start_dt, end_dt, state)
				break

		if current_shift_obj:
			s, start_dt, end_dt, state = current_shift_obj
			start_str, end_str = await _get_time_section_config(db, s.time_section, s.clinic_id)
			earliest_checkin = start_dt - timedelta(minutes=30)
			latest_checkout = end_dt + timedelta(hours=2)
			
			# 优化后的状态判断
			if state.get("checkout_time"):
				shift_status_value = "checked_out"
				checkout_time = state.get("checkout_time")
				if state.get("checkin_time"):
					ct_parsed = datetime.strptime(state["checkin_time"], "%H:%M")
					work_duration = _human_duration(datetime.combine(date.today(), ct_parsed.time()), datetime.combine(date.today(), datetime.strptime(checkout_time, "%H:%M").time()))
			elif state.get("checkin_time"):
				checkin_time = state.get("checkin_time")
				shift_status_value = "checked_in"
				work_duration = _human_duration(datetime.combine(date.today(), datetime.strptime(checkin_time, "%H:%M").time()), now)
				time_to_checkout = _human_duration(now, end_dt) if now <= end_dt else "已超时"
			elif now < earliest_checkin:
				shift_status_value = "not_started"
				countdown = _human_duration(now, start_dt)
			elif earliest_checkin <= now <= end_dt:
				shift_status_value = "ready"
				countdown = f"可签到（班次 {start_str} 开始）"
			elif end_dt < now <= latest_checkout:
				shift_status_value = "expired"
			else:
				shift_status_value = "expired"

			clinic_addr = s.clinic.address if s.clinic and getattr(s.clinic, "address", None) else None
			current_shift = WorkbenchCurrentShift(
				id=s.schedule_id,
				name=f"{s.time_section}门诊",
				startTime=start_str,
				endTime=end_str,
				location=clinic_addr,
				countdown=countdown
			)
		else:
			current_shift = None
			shift_status_value = "checked_out"

		# 接诊统计（今日）
		stats_res = await db.execute(select(RegistrationOrder).where(and_(RegistrationOrder.doctor_id == doctor.doctor_id, RegistrationOrder.slot_date == date.today())))
		orders = stats_res.scalars().all()
		pending_cnt = sum(1 for o in orders if o.status in (OrderStatus.PENDING, OrderStatus.WAITLIST))
		ongoing_cnt = sum(1 for o in orders if o.status in (OrderStatus.CONFIRMED,))
		completed_cnt = sum(1 for o in orders if o.status in (OrderStatus.COMPLETED,))
		total_cnt = len(orders)

		doctor_info = WorkbenchDoctorInfo(
			id=doctor.doctor_id,
			name=doctor.name,
			title=doctor.title,
			department=dept.name if dept else None,
			photo_path=doctor.photo_path
		)
		shift_status = WorkbenchShiftStatus(
			status=shift_status_value,
			currentShift=current_shift,
			checkinTime=checkin_time,
			checkoutTime=checkout_time,
			workDuration=work_duration,
			timeToCheckout=time_to_checkout
		)
		today_data = WorkbenchTodayData(
			pendingConsultation=pending_cnt,
			ongoingConsultation=ongoing_cnt,
			completedConsultation=completed_cnt,
			totalConsultation=total_cnt
		)
		# 简单占位提醒与近期记录（真实实现需业务支撑）
		reminders = [WorkbenchReminder(id=1, type="system", title="请按时签到", icon="bell", time="08:00")]
		recent_records = []
		# 只显示已就诊的记录（有就诊时间）
		for o in orders:
			if o.visit_times and o.status in (OrderStatus.COMPLETED, OrderStatus.CONFIRMED):
				try:
					visit_dt = datetime.strptime(o.visit_times, "%Y-%m-%d %H:%M:%S")
					consultation_time = visit_dt.strftime("%H:%M")
					recent_records.append(WorkbenchRecentRecord(id=o.order_id, patientName=str(o.patient_id), consultationTime=consultation_time, diagnosis=None))
					if len(recent_records) >= 3:
						break
				except Exception:
					pass

			return ResponseModel(code=0, message=WorkbenchDashboardResponse(
				doctor=doctor_info,
				shiftStatus=shift_status,
				todayData=today_data,
				reminders=reminders,
				recentRecords=recent_records
			))
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取工作台数据失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"获取工作台数据失败: {str(e)}",
			status_code=500
		)


# ===================== 科室长请假审核接口 =====================

async def _get_original_schedule_info(db: AsyncSession, doctor_id: int, start_date: date, end_date: date, shift: str) -> list[str]:
	"""
	获取医生在请假期间的原排班信息
	
	Args:
		db: 数据库会话
		doctor_id: 医生ID
		start_date: 请假开始日期
		end_date: 请假结束日期  
		shift: 请假时段 (morning/afternoon/night/full)
	
	Returns:
		原排班信息列表，格式如 ["2025-11-28 上午: 专家门诊(内科门诊)", "2025-11-29 下午: 普通门诊(外科门诊)"]
	"""
	try:
		# 查询该医生在请假期间的所有排班
		schedule_res = await db.execute(
			select(Schedule, Clinic)
			.join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
			.where(
				and_(
					Schedule.doctor_id == doctor_id,
					Schedule.date >= start_date,
					Schedule.date <= end_date
				)
			)
			.order_by(Schedule.date, Schedule.time_section)
		)
		schedules = schedule_res.all()
		
		original_schedule = []
		
		# 统一的时段标准化映射（支持数据库中可能存在的各种表示方式）
		def normalize_time_section(ts: str) -> str:
			"""将数据库中的时段字段标准化为英文 morning/afternoon/night"""
			ts_lower = str(ts).lower().strip()
			if ts_lower in ("上午", "早", "morning", "am"):
				return "morning"
			elif ts_lower in ("下午", "after", "afternoon", "pm"):
				return "afternoon"
			elif ts_lower in ("晚上", "夜", "night", "evening", "晚"):
				return "night"
			else:
				# 默认按原值返回（可能需要后续扩展）
				return ts_lower
		
		# 中文时段显示名称映射
		time_section_display = {
			"morning": "上午",
			"afternoon": "下午",
			"night": "晚上"
		}
		
		for sched, clinic in schedules:
			# 标准化数据库中的时段
			normalized_shift = normalize_time_section(sched.time_section)
			
			# 如果请假时段不是 full，需要过滤匹配的时段
			if shift != "full":
				if normalized_shift != shift:
					continue
			
			# 构造排班信息字符串（包含日期、时段、类型、门诊）
			date_str = sched.date.strftime("%Y-%m-%d") if sched.date else "未知日期"
			time_display = time_section_display.get(normalized_shift, sched.time_section)
			clinic_name = clinic.name if clinic else "未知门诊"
			
			# 处理 slot_type：如果是枚举，取其 value（中文值），否则直接使用
			if hasattr(sched.slot_type, 'value'):
				slot_type = sched.slot_type.value
			else:
				slot_type = str(sched.slot_type) if sched.slot_type else "排班"
			
			schedule_info = f"{date_str} {time_display}: {slot_type}({clinic_name})"
			original_schedule.append(schedule_info)
		
		return original_schedule
		
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取原排班信息失败: {e}")
		# 如果查询失败，返回空列表，不影响主流程
		return []


@router.get("/approval/stats", response_model=ResponseModel)
async def get_approval_stats(
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""
	获取审批统计数据
	
	用于在页面顶部展示待审批、本月已通过、本月已拒绝的数量统计。
	
	返回格式：
	{
		"code": 0,
		"message": {
			"pending": 5,          // 当前待审批总数
			"approvedMonth": 12,   // 本月已审批通过数量
			"rejectedMonth": 2     // 本月已拒绝数量
		}
	}
	"""
	try:
		# 验证当前用户是科室长
		head_doctor = await _get_doctor(db, current_user)
		if getattr(head_doctor, "is_department_head", 0) != 1:
			raise AuthHTTPException(
				code=settings.INSUFFICIENT_AUTHORITY_CODE, 
				msg="仅科室长可访问", 
				status_code=403
			)
		
		# 计算本月的起始和结束日期
		now = datetime.now()
		month_start = datetime(now.year, now.month, 1)
		if now.month == 12:
			month_end = datetime(now.year + 1, 1, 1)
		else:
			month_end = datetime(now.year, now.month + 1, 1)
		
		# 查询本科室所有医生的请假申请
		# 1. 待审批数量（status='pending'）
		pending_res = await db.execute(
			select(LeaveAudit)
			.join(Doctor, Doctor.doctor_id == LeaveAudit.doctor_id)
			.where(
				and_(
					Doctor.dept_id == head_doctor.dept_id,
					LeaveAudit.status == 'pending'
				)
			)
		)
		pending_count = len(pending_res.all())
		
		# 2. 本月已通过数量（status='approved' AND audit_time在本月）
		approved_res = await db.execute(
			select(LeaveAudit)
			.join(Doctor, Doctor.doctor_id == LeaveAudit.doctor_id)
			.where(
				and_(
					Doctor.dept_id == head_doctor.dept_id,
					LeaveAudit.status == 'approved',
					LeaveAudit.audit_time >= month_start,
					LeaveAudit.audit_time < month_end
				)
			)
		)
		approved_count = len(approved_res.all())
		
		# 3. 本月已拒绝数量（status='rejected' AND audit_time在本月）
		rejected_res = await db.execute(
			select(LeaveAudit)
			.join(Doctor, Doctor.doctor_id == LeaveAudit.doctor_id)
			.where(
				and_(
					Doctor.dept_id == head_doctor.dept_id,
					LeaveAudit.status == 'rejected',
					LeaveAudit.audit_time >= month_start,
					LeaveAudit.audit_time < month_end
				)
			)
		)
		rejected_count = len(rejected_res.all())
		
		# 返回统计数据
		return ResponseModel(
			code=0,
			message={
				"pending": pending_count,
				"approvedMonth": approved_count,
				"rejectedMonth": rejected_count
			}
		)
		
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE, 
			msg=f"获取审批统计数据失败: {e}", 
			status_code=500
		)


@router.get("/leave/audit", response_model=ResponseModel[LeaveAuditListResponse])
async def department_head_leave_audits(
    status: str = "pending",
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
	"""科室长查看本科室医生请假申请列表 (pending/approved/rejected/all)。"""
	try:
		if page <= 0 or page_size <= 0:
			raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="分页参数必须为正整数", status_code=400)

		head_doctor = await _get_doctor(db, current_user)
		if getattr(head_doctor, "is_department_head", 0) != 1:
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅科室长可访问", status_code=403)

		valid_status = {"pending", "approved", "rejected", "all"}
		if status not in valid_status:
			raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="status 参数无效", status_code=400)

		offset = (page - 1) * page_size
		stmt = (
			select(LeaveAudit, Doctor, MinorDepartment)
			.join(Doctor, Doctor.doctor_id == LeaveAudit.doctor_id)
			.join(MinorDepartment, Doctor.dept_id == MinorDepartment.minor_dept_id)
			.where(Doctor.dept_id == head_doctor.dept_id)
			.order_by(LeaveAudit.submit_time.desc())
			.offset(offset)
			.limit(page_size)
		)
		if status != "all":
			stmt = stmt.where(LeaveAudit.status == status)
		res = await db.execute(stmt)
		rows = res.fetchall()

		items: list[LeaveAuditItem] = []
		for leave, d_obj, dept_obj in rows:
			attachments = []
			if leave.attachment_data_json:
				for att in leave.attachment_data_json:
					if isinstance(att, str):
						attachments.append(att)
					elif isinstance(att, dict) and att.get("url"):
						attachments.append(att.get("url"))
			reason_preview = leave.reason[:30] + ("..." if len(leave.reason) > 30 else "")
			
			# 获取原排班信息
			original_schedule = await _get_original_schedule_info(
				db, 
				leave.doctor_id, 
				leave.leave_start_date, 
				leave.leave_end_date,
				leave.shift or "full"
			)
			
			items.append(
				LeaveAuditItem(
					id=leave.audit_id,
					doctor_id=leave.doctor_id,
					doctor_name=d_obj.name,
					doctor_title=d_obj.title,
					department_name=dept_obj.name if dept_obj else None,
					leave_start_date=leave.leave_start_date,
					leave_end_date=leave.leave_end_date,
					leave_days=calculate_leave_days(leave.leave_start_date, leave.leave_end_date),
					shift=leave.shift or "full",
					reason=leave.reason,
					reason_preview=reason_preview,
					attachments=attachments,
					original_schedule=original_schedule,
					submit_time=leave.submit_time,
					status=leave.status,
					auditor_id=leave.auditor_user_id,
					audit_time=leave.audit_time,
					audit_remark=leave.audit_remark
				)
			)
		return ResponseModel(code=0, message=LeaveAuditListResponse(audits=items))
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg=f"获取请假审核列表失败: {e}", status_code=500)


@router.get("/leave/audit/{audit_id}", response_model=ResponseModel[LeaveAuditItem])
async def department_head_leave_audit_detail(
	audit_id: int,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""科室长查看单个请假申请详情。"""
	try:
		head_doctor = await _get_doctor(db, current_user)
		if getattr(head_doctor, "is_department_head", 0) != 1:
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅科室长可访问", status_code=403)

		res = await db.execute(select(LeaveAudit).where(LeaveAudit.audit_id == audit_id))
		leave = res.scalar_one_or_none()
		if not leave:
			raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="申请不存在", status_code=404)
		d_res = await db.execute(select(Doctor, MinorDepartment).join(MinorDepartment, Doctor.dept_id == MinorDepartment.minor_dept_id).where(Doctor.doctor_id == leave.doctor_id))
		d_row = d_res.fetchone()
		if not d_row:
			raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="医生或科室信息缺失", status_code=404)
		d_obj, dept_obj = d_row
		if d_obj.dept_id != head_doctor.dept_id:
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="不可查看其他科室申请", status_code=403)

		attachments = []
		if leave.attachment_data_json:
			for att in leave.attachment_data_json:
				if isinstance(att, str):
					attachments.append(att)
				elif isinstance(att, dict) and att.get("url"):
					attachments.append(att.get("url"))

		# 获取原排班信息
		original_schedule = await _get_original_schedule_info(
			db, 
			leave.doctor_id, 
			leave.leave_start_date, 
			leave.leave_end_date,
			leave.shift or "full"
		)

		item = LeaveAuditItem(
			id=leave.audit_id,
			doctor_id=leave.doctor_id,
			doctor_name=d_obj.name,
			doctor_title=d_obj.title,
			department_name=dept_obj.name if dept_obj else None,
			leave_start_date=leave.leave_start_date,
			leave_end_date=leave.leave_end_date,
			leave_days=calculate_leave_days(leave.leave_start_date, leave.leave_end_date),
			shift=leave.shift or "full",
			reason=leave.reason,
			reason_preview=leave.reason[:30] + ("..." if len(leave.reason) > 30 else ""),
			attachments=attachments,
			original_schedule=original_schedule,
			submit_time=leave.submit_time,
			status=leave.status,
			auditor_id=leave.auditor_user_id,
			audit_time=leave.audit_time,
			audit_remark=leave.audit_remark
		)
		return ResponseModel(code=0, message=item)
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg=f"获取请假审核详情失败: {e}", status_code=500)


@router.post("/leave/audit/{audit_id}/approve", response_model=ResponseModel[AuditActionResponse])
async def department_head_leave_approve(
	audit_id: int,
	action: AuditAction,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""科室长批准请假申请 (仅 pending)。"""
	try:
		head_doctor = await _get_doctor(db, current_user)
		if getattr(head_doctor, "is_department_head", 0) != 1:
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅科室长可操作", status_code=403)
		res = await db.execute(select(LeaveAudit).where(LeaveAudit.audit_id == audit_id))
		leave = res.scalar_one_or_none()
		if not leave:
			raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="申请不存在", status_code=404)
		d_res = await db.execute(select(Doctor).where(Doctor.doctor_id == leave.doctor_id))
		doc = d_res.scalar_one_or_none()
		if not doc:
			raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="医生不存在", status_code=404)
		if doc.dept_id != head_doctor.dept_id:
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="不可审批其他科室", status_code=403)
		if leave.status != "pending":
			raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="仅可审批待审核申请", status_code=400)
		leave.status = "approved"
		leave.auditor_user_id = current_user.user_id
		leave.audit_time = datetime.now()
		leave.audit_remark = action.comment
		await db.commit()
		return ResponseModel(code=0, message=AuditActionResponse(audit_id=leave.audit_id, status=leave.status, auditor_id=current_user.user_id, audit_time=leave.audit_time))
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		await db.rollback()
		raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"批准失败: {e}", status_code=500)


@router.post("/leave/audit/{audit_id}/reject", response_model=ResponseModel[AuditActionResponse])
async def department_head_leave_reject(
	audit_id: int,
	action: AuditAction,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""科室长驳回请假申请 (需提供 comment)。"""
	try:
		head_doctor = await _get_doctor(db, current_user)
		if getattr(head_doctor, "is_department_head", 0) != 1:
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅科室长可操作", status_code=403)
		if not action.comment or not action.comment.strip():
			raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="驳回必须提供 comment", status_code=400)
		res = await db.execute(select(LeaveAudit).where(LeaveAudit.audit_id == audit_id))
		leave = res.scalar_one_or_none()
		if not leave:
			raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="申请不存在", status_code=404)
		d_res = await db.execute(select(Doctor).where(Doctor.doctor_id == leave.doctor_id))
		doc = d_res.scalar_one_or_none()
		if not doc:
			raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="医生不存在", status_code=404)
		if doc.dept_id != head_doctor.dept_id:
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="不可审批其他科室", status_code=403)
		if leave.status != "pending":
			raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="仅可驳回待审核申请", status_code=400)
		leave.status = "rejected"
		leave.auditor_user_id = current_user.user_id
		leave.audit_time = datetime.now()
		leave.audit_remark = action.comment
		await db.commit()
		return ResponseModel(code=0, message=AuditActionResponse(audit_id=leave.audit_id, status=leave.status, auditor_id=current_user.user_id, audit_time=leave.audit_time))
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		await db.rollback()
		raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"驳回失败: {e}", status_code=500)


@router.post("/workbench/checkin", response_model=ResponseModel[CheckinResponse])
async def workbench_checkin(
	shiftId: int = Body(..., embed=True),
	latitude: Optional[float] = Body(None),
	longitude: Optional[float] = Body(None),
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""签到接口 - 仅限当天排班，提前30分钟可签到"""
	try:
		doctor = await _get_doctor(db, current_user)
		res = await db.execute(select(Schedule).where(Schedule.schedule_id == shiftId))
		schedule = res.scalar_one_or_none()
		if not schedule or schedule.doctor_id != doctor.doctor_id:
			raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="排班不存在或不属于当前医生", status_code=404)
		
		# 仅允许当天排班签到
		today = date.today()
		if schedule.date != today:
			raise BusinessHTTPException(
				code=settings.REQ_ERROR_CODE,
				msg=f"仅可对当天排班签到，该排班日期为 {schedule.date}",
				status_code=400
			)
		
		# 检查是否已签到
		state = await _load_shift_state(schedule.schedule_id)
		if state.get("checkin_time"):
			raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="已签到，请勿重复操作", status_code=400)
		
		# 时间窗口检查:提前30分钟可签到，班次结束前必须签到
		start_str, end_str = await _get_time_section_config(db, schedule.time_section, schedule.clinic_id)
		now = datetime.now()  # 使用本地时间而非UTC
		start_dt = datetime.combine(today, datetime.strptime(start_str, "%H:%M").time())
		end_dt = datetime.combine(today, datetime.strptime(end_str, "%H:%M").time())
		
		# 提前30分钟开放签到
		earliest_checkin = start_dt - timedelta(minutes=30)
		
		# 严格时间窗口验证
		if now < earliest_checkin:
			raise BusinessHTTPException(
				code=settings.REQ_ERROR_CODE,
				msg=f"签到时间过早，最早可于 {earliest_checkin.strftime('%H:%M')} 签到（班次 {start_str}-{end_str}）",
				status_code=400
			)
		if now > end_dt:
			raise BusinessHTTPException(
				code=settings.REQ_ERROR_CODE,
				msg=f"班次已结束（{end_str}），无法签到",
				status_code=400
			)
		
		checkin_time_str = now.strftime("%H:%M")
		state["checkin_time"] = checkin_time_str
		await _save_shift_state(schedule.schedule_id, state)
		
		# 持久化到数据库
		attendance = AttendanceRecord(
			schedule_id=schedule.schedule_id,
			doctor_id=doctor.doctor_id,
			checkin_time=now,
			checkin_lat=latitude,
			checkin_lng=longitude,
			status=AttendanceStatus.CHECKED_IN
		)
		db.add(attendance)
		await db.commit()
		
		return ResponseModel(code=0, message=CheckinResponse(checkinTime=checkin_time_str, status="checked_in", message="签到成功", workDuration="0分钟"))
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		await db.rollback()
		import logging
		logging.getLogger(__name__).error(f"签到失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"签到失败: {str(e)}",
			status_code=500
		)


@router.post("/workbench/checkout", response_model=ResponseModel[CheckoutResponse])
async def workbench_checkout(
	shiftId: int = Body(..., embed=True),
	latitude: Optional[float] = Body(None),
	longitude: Optional[float] = Body(None),
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""签退接口 - 仅限当天排班，必须先签到，班次结束后2小时内可签退"""
	try:
		doctor = await _get_doctor(db, current_user)
		res = await db.execute(select(Schedule).where(Schedule.schedule_id == shiftId))
		schedule = res.scalar_one_or_none()
		if not schedule or schedule.doctor_id != doctor.doctor_id:
			raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="排班不存在或不属于当前医生", status_code=404)
		
		# 仅允许当天排班签退
		today = date.today()
		if schedule.date != today:
			raise BusinessHTTPException(
				code=settings.REQ_ERROR_CODE,
				msg=f"仅可对当天排班签退，该排班日期为 {schedule.date}",
				status_code=400
			)
		
		# 必须先签到
		state = await _load_shift_state(schedule.schedule_id)
		if not state.get("checkin_time"):
			raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="尚未签到，无法签退", status_code=400)
		if state.get("checkout_time"):
			raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="已签退，请勿重复操作", status_code=400)
		
		# 时间窗口检查：班次结束后2小时内可签退
		start_str, end_str = await _get_time_section_config(db, schedule.time_section, schedule.clinic_id)
		now = datetime.now()  # 使用本地时间而非UTC
		end_dt = datetime.combine(today, datetime.strptime(end_str, "%H:%M").time())
		latest_checkout = end_dt + timedelta(hours=2)
		
		if now > latest_checkout:
			raise BusinessHTTPException(
				code=settings.REQ_ERROR_CODE,
				msg=f"签退超时，最晚可于 {latest_checkout.strftime('%H:%M')} 签退",
				status_code=400
			)
		
		checkout_time_str = now.strftime("%H:%M")
		state["checkout_time"] = checkout_time_str
		# 计算工时
		try:
			start_dt = datetime.strptime(state["checkin_time"], "%H:%M")
			work_duration = _human_duration(datetime.combine(today, start_dt.time()), now)
		except Exception:
			work_duration = "--"
		await _save_shift_state(schedule.schedule_id, state)
		
		# 更新数据库考勤记录
		att_res = await db.execute(
			select(AttendanceRecord).where(
				and_(
					AttendanceRecord.schedule_id == schedule.schedule_id,
					AttendanceRecord.doctor_id == doctor.doctor_id,
					AttendanceRecord.status == AttendanceStatus.CHECKED_IN
				)
			).order_by(AttendanceRecord.created_at.desc())
		)
		attendance = att_res.scalars().first()
		if attendance:
			attendance.checkout_time = now
			attendance.checkout_lat = latitude
			attendance.checkout_lng = longitude
			attendance.status = AttendanceStatus.CHECKED_OUT
			if attendance.checkin_time:
				delta = now - attendance.checkin_time
				attendance.work_duration_minutes = int(delta.total_seconds() / 60)
			await db.commit()
		
		return ResponseModel(code=0, message=CheckoutResponse(checkoutTime=checkout_time_str, workDuration=work_duration, status="checked_out", message="签退成功"))
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		await db.rollback()
		import logging
		logging.getLogger(__name__).error(f"签退失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"签退失败: {str(e)}",
			status_code=500
		)


@router.get("/workbench/shifts", response_model=ResponseModel[ShiftsResponse])
async def workbench_shifts(doctorId: Optional[int] = None, date_str: Optional[str] = None, db: AsyncSession = Depends(get_db), current_user: UserSchema = Depends(get_current_user)):
	try:
		doctor = await _get_doctor(db, current_user)
		if doctorId and doctorId != doctor.doctor_id:
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="不能查询其他医生的排班", status_code=403)
		target_date = date.today()
		if date_str:
			try:
				target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
			except Exception:
				pass
		res = await db.execute(
			select(Schedule)
			.options(selectinload(Schedule.clinic))
			.where(and_(Schedule.doctor_id == doctor.doctor_id, Schedule.date == target_date))
		)
		schedules = res.scalars().all()
		now = datetime.now()  # 使用本地时间而非UTC
		items = []
		for s in schedules:
			start_str, end_str = await _get_time_section_config(db, s.time_section, s.clinic_id)
			start_dt = datetime.combine(s.date, datetime.strptime(start_str, "%H:%M").time())
			end_dt = datetime.combine(s.date, datetime.strptime(end_str, "%H:%M").time())
			earliest_checkin = start_dt - timedelta(minutes=30)
			latest_checkout = end_dt + timedelta(hours=2)
			
			state = await _load_shift_state(s.schedule_id)
			
			# 新的状态机逻辑
			if state.get("checkout_time"):
				status = "checked_out"  # 已签退
			elif state.get("checkin_time"):
				status = "checked_in"  # 已签到未签退
			elif now < earliest_checkin:
				status = "not_started"  # 排班未开始（签到窗口未开放）
			elif earliest_checkin <= now <= end_dt:
				status = "ready"  # 可签到（签到窗口已开放）
			elif end_dt < now <= latest_checkout:
				status = "expired"  # 已过期但仍在签退窗口内
			else:
				status = "expired"  # 完全过期
			
			clinic_addr = s.clinic.address if s.clinic and getattr(s.clinic, "address", None) else None
			items.append(ShiftItem(id=s.schedule_id, name=f"{s.time_section}门诊", startTime=start_str, endTime=end_str, location=clinic_addr, status=status))
		return ResponseModel(code=0, message=ShiftsResponse(shifts=items))
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取工作台排班失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"获取工作台排班失败: {str(e)}",
			status_code=500
		)


@router.get("/workbench/consultation-stats", response_model=ResponseModel[ConsultationStatsResponse])
async def workbench_consultation_stats(doctorId: int, db: AsyncSession = Depends(get_db), current_user: UserSchema = Depends(get_current_user)):
	try:
		doctor = await _get_doctor(db, current_user)
		if doctorId != doctor.doctor_id:
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="不能查询其他医生的数据", status_code=403)
		res = await db.execute(select(RegistrationOrder).where(and_(RegistrationOrder.doctor_id == doctor.doctor_id, RegistrationOrder.slot_date == date.today())))
		orders = res.scalars().all()
		pending_cnt = sum(1 for o in orders if o.status in (OrderStatus.PENDING, OrderStatus.WAITLIST))
		ongoing_cnt = sum(1 for o in orders if o.status in (OrderStatus.CONFIRMED,))
		completed_cnt = sum(1 for o in orders if o.status in (OrderStatus.COMPLETED,))
		total_cnt = len(orders)
		return ResponseModel(code=0, message=ConsultationStatsResponse(pending=pending_cnt, ongoing=ongoing_cnt, completed=completed_cnt, total=total_cnt))
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取接诊统计失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"获取接诊统计失败: {str(e)}",
			status_code=500
		)


@router.get("/workbench/recent-consultations", response_model=ResponseModel[RecentConsultationsResponse])
async def workbench_recent_consultations(doctorId: int, limit: int = 3, db: AsyncSession = Depends(get_db), current_user: UserSchema = Depends(get_current_user)):
	try:
		doctor = await _get_doctor(db, current_user)
		if doctorId != doctor.doctor_id:
			raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="不能查询其他医生的数据", status_code=403)
		
		# 只查询已就诊的订单（已完成或进行中且有就诊时间）
		res = await db.execute(
			select(RegistrationOrder)
			.where(
				and_(
					RegistrationOrder.doctor_id == doctor.doctor_id,
					RegistrationOrder.slot_date == date.today(),
					RegistrationOrder.status.in_([OrderStatus.COMPLETED, OrderStatus.CONFIRMED]),
					RegistrationOrder.visit_times.isnot(None)  # 必须有就诊时间
				)
			)
			.order_by(RegistrationOrder.create_time.desc())
			.limit(limit)
		)
		orders = res.scalars().all()
		
		records = []
		for o in orders:
			try:
				# visit_times 存储格式: "2025-11-20 10:23:00"
				visit_dt = datetime.strptime(o.visit_times, "%Y-%m-%d %H:%M:%S")
				consultation_time = visit_dt.strftime("%H:%M")
				records.append(RecentConsultationItem(id=o.order_id, patientName=str(o.patient_id), consultationTime=consultation_time, diagnosis=None))
			except Exception:
				# 如果时间解析失败，跳过该记录
				pass
		
		return ResponseModel(code=0, message=RecentConsultationsResponse(records=records))
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取近期就诊失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"获取近期就诊失败: {str(e)}",
			status_code=500
		)


@router.get("/workbench/attendance-records", response_model=ResponseModel[AttendanceRecordsResponse])
async def workbench_attendance_records(
	doctorId: Optional[int] = None,
	start_date: Optional[str] = None,
	end_date: Optional[str] = None,
	page: int = 1,
	page_size: int = 20,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""查询医生考勤历史记录"""
	try:
		doctor = await _get_doctor(db, current_user)
		if doctorId and doctorId != doctor.doctor_id:
			if not current_user.is_admin:
				raise AuthHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只能查询本人考勤记录", status_code=403)
		
		target_doctor_id = doctorId if doctorId else doctor.doctor_id
		
		# 构建查询条件
		conditions = [AttendanceRecord.doctor_id == target_doctor_id]
		if start_date:
			try:
				from datetime import datetime as dt
				start_dt = dt.strptime(start_date, "%Y-%m-%d")
				conditions.append(AttendanceRecord.created_at >= start_dt)
			except Exception:
				pass
		if end_date:
			try:
				from datetime import datetime as dt
				end_dt = dt.strptime(end_date, "%Y-%m-%d")
				end_dt = end_dt.replace(hour=23, minute=59, second=59)
				conditions.append(AttendanceRecord.created_at <= end_dt)
			except Exception:
				pass
		
		# 查询总数
		count_res = await db.execute(
			select(AttendanceRecord).where(and_(*conditions))
		)
		total = len(count_res.scalars().all())
		
		# 分页查询
		offset = (page - 1) * page_size
		res = await db.execute(
			select(AttendanceRecord)
			.where(and_(*conditions))
			.order_by(AttendanceRecord.created_at.desc())
			.limit(page_size)
			.offset(offset)
		)
		records_db = res.scalars().all()
		
		records = [
			AttendanceRecordItem(
				record_id=r.record_id,
				schedule_id=r.schedule_id,
				checkin_time=r.checkin_time,
				checkout_time=r.checkout_time,
				work_duration_minutes=r.work_duration_minutes,
				status=r.status.value,
				created_at=r.created_at
			)
			for r in records_db
		]
		
		return ResponseModel(code=0, message=AttendanceRecordsResponse(records=records, total=total))
	except (AuthHTTPException, BusinessHTTPException, ResourceHTTPException):
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取考勤记录失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"获取考勤记录失败: {str(e)}",
			status_code=500
		)


@router.get("/schedules", response_model=ResponseModel)
async def get_doctor_schedules(
	doctor_id: Optional[int] = None,
	start_date: str = None,
	end_date: str = None,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""获取医生排班 - 医生只能查自己的,管理员可以查所有人"""
	try:
		# 权限检查
		target_doctor_id = doctor_id
		if not current_user.is_admin:
			# 非管理员必须是医生
			res = await db.execute(select(Doctor).where(Doctor.user_id == current_user.user_id))
			doctor = res.scalar_one_or_none()
			if not doctor:
				raise AuthHTTPException(
					code=settings.INSUFFICIENT_AUTHORITY_CODE,
					msg="仅医生可访问",
					status_code=403
				)
			# 医生只能查自己的
			if doctor_id and doctor_id != doctor.doctor_id:
				raise AuthHTTPException(
					code=settings.INSUFFICIENT_AUTHORITY_CODE,
					msg="医生只能查询本人排班",
					status_code=403
				)
			target_doctor_id = doctor.doctor_id
		else:
			# 管理员如果没指定doctor_id则报错
			if not target_doctor_id:
				raise BusinessHTTPException(
					code=settings.REQ_ERROR_CODE,
					msg="管理员需指定doctor_id参数",
					status_code=400
				)

		# 校验目标医生存在
		result = await db.execute(select(Doctor).where(Doctor.doctor_id == target_doctor_id))
		if not result.scalar_one_or_none():
			raise ResourceHTTPException(
				code=settings.DATA_GET_FAILED_CODE,
				msg="医生不存在",
				status_code=404
			)

		# 日期范围处理
		if not start_date or not end_date:
			raise BusinessHTTPException(
				code=settings.REQ_ERROR_CODE,
				msg="需要提供start_date和end_date参数(YYYY-MM-DD)",
				status_code=400
			)

		start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
		end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

		# 查询排班
		result = await db.execute(
			select(Schedule, Doctor.name, Clinic.name, Clinic.clinic_type)
			.join(Doctor, Doctor.doctor_id == Schedule.doctor_id)
			.join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
			.where(
				and_(
					Schedule.doctor_id == target_doctor_id,
					Schedule.date >= start_dt,
					Schedule.date <= end_dt,
				)
			)
			.order_by(Schedule.date, Schedule.time_section)
		)

		rows = result.all()
		data = []
		for sch, doctor_name, clinic_name, clinic_type in rows:
			data.append({
				"schedule_id": sch.schedule_id,
				"doctor_id": sch.doctor_id,
				"doctor_name": doctor_name,
				"clinic_id": sch.clinic_id,
				"clinic_name": clinic_name,
				"clinic_type": clinic_type,
				"date": str(sch.date),
				"week_day": sch.week_day,
				"time_section": sch.time_section,
				"slot_type": sch.slot_type.value if hasattr(sch.slot_type, 'value') else str(sch.slot_type),
				"total_slots": sch.total_slots,
				"remaining_slots": sch.remaining_slots,
				"status": sch.status,
				"price": float(sch.price)
			})

		return ResponseModel(code=0, message={"schedules": data})
	except AuthHTTPException:
		raise
	except BusinessHTTPException:
		raise
	except ResourceHTTPException:
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取医生排班时发生异常: {str(e)}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg="内部服务异常",
			status_code=500
		)


@router.get("/schedules/today", response_model=ResponseModel)
async def get_doctor_schedules_today(
	doctor_id: Optional[int] = None,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""查询当日排班 - 医生只能查自己的,管理员可以查所有人"""
	try:
		# 权限检查
		target_doctor_id = doctor_id
		if not current_user.is_admin:
			# 非管理员必须是医生
			res = await db.execute(select(Doctor).where(Doctor.user_id == current_user.user_id))
			doctor = res.scalar_one_or_none()
			if not doctor:
				raise AuthHTTPException(
					code=settings.INSUFFICIENT_AUTHORITY_CODE,
					msg="仅医生可访问",
					status_code=403
				)
			# 医生只能查自己的
			if doctor_id and doctor_id != doctor.doctor_id:
				raise AuthHTTPException(
					code=settings.INSUFFICIENT_AUTHORITY_CODE,
					msg="医生只能查询本人排班",
					status_code=403
				)
			target_doctor_id = doctor.doctor_id
		else:
			# 管理员如果没指定doctor_id则报错
			if not target_doctor_id:
				raise BusinessHTTPException(
					code=settings.REQ_ERROR_CODE,
					msg="管理员需指定doctor_id参数",
					status_code=400
				)

		# 查询医生信息
		doctor_result = await db.execute(
			select(Doctor).where(Doctor.doctor_id == target_doctor_id)
		)
		doctor = doctor_result.scalar_one_or_none()
		if not doctor:
			raise ResourceHTTPException(
				code=settings.DATA_GET_FAILED_CODE,
				msg=f"医生ID {target_doctor_id} 不存在"
			)

		# 获取当天日期
		today = datetime.utcnow().date()

		# 查询当天排班
		stmt = select(Schedule, Clinic, MinorDepartment).join(
			Clinic, Schedule.clinic_id == Clinic.clinic_id
		).join(
			MinorDepartment, Clinic.minor_dept_id == MinorDepartment.minor_dept_id
		).where(
			and_(
				Schedule.doctor_id == target_doctor_id,
				Schedule.date == today
			)
		).order_by(Schedule.time_section)

		result = await db.execute(stmt)
		rows = result.all()

		schedules = []
		for schedule, clinic, dept in rows:
			# 根据门诊类型确定可用号源类型
			# clinic_type: 0-普通门诊, 1-专家门诊(国疗), 2-特需门诊
			if clinic.clinic_type == 0:
				available_types = ["普通"]
			elif clinic.clinic_type == 1:
				available_types = ["普通", "专家"]
			else:  # clinic_type == 2
				available_types = ["普通", "专家", "特需"]

			schedules.append({
				"schedule_id": schedule.schedule_id,
				"doctor_id": doctor.doctor_id,
				"doctor_name": doctor.name,
				"department_id": dept.minor_dept_id,
				"department_name": dept.name,
				"clinic_type": "普通门诊" if clinic.clinic_type == 0 else ("专家门诊" if clinic.clinic_type == 1 else "特需门诊"),
				"date": str(schedule.date),
				"time_slot": schedule.time_section,
				"total_slots": schedule.total_slots,
				"remaining_slots": schedule.remaining_slots,
				"available_slot_types": available_types
			})

		return ResponseModel(code=0, message={"schedules": schedules})

	except AuthHTTPException:
		raise
	except BusinessHTTPException:
		raise
	except ResourceHTTPException:
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取医生当日排班失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"获取医生当日排班失败: {str(e)}"
		)


@router.get("/schedules/{schedule_id}", response_model=ResponseModel)
async def get_schedule_detail(
	schedule_id: int,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""根据排班ID获取排班详情 - 医生只能查自己的,管理员可以查所有人"""
	try:
		# 查询排班信息
		result = await db.execute(
			select(Schedule, Doctor, Clinic, MinorDepartment)
			.join(Doctor, Doctor.doctor_id == Schedule.doctor_id)
			.join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
			.join(MinorDepartment, Clinic.minor_dept_id == MinorDepartment.minor_dept_id)
			.where(Schedule.schedule_id == schedule_id)
		)
		row = result.first()
		
		if not row:
			raise ResourceHTTPException(
				code=settings.DATA_GET_FAILED_CODE,
				msg="排班不存在",
				status_code=404
			)
		
		schedule, doctor, clinic, dept = row
		
		# 权限检查
		if not current_user.is_admin:
			# 非管理员必须是医生
			res = await db.execute(select(Doctor).where(Doctor.user_id == current_user.user_id))
			current_doctor = res.scalar_one_or_none()
			if not current_doctor:
				raise AuthHTTPException(
					code=settings.INSUFFICIENT_AUTHORITY_CODE,
					msg="仅医生可访问",
					status_code=403
				)
			# 医生只能查自己的排班
			if schedule.doctor_id != current_doctor.doctor_id:
				raise AuthHTTPException(
					code=settings.INSUFFICIENT_AUTHORITY_CODE,
					msg="医生只能查询本人排班",
					status_code=403
				)
		
		# 根据门诊类型确定可用号源类型
		if clinic.clinic_type == 0:
			available_types = ["普通"]
		elif clinic.clinic_type == 1:
			available_types = ["普通", "专家"]
		else:  # clinic_type == 2
			available_types = ["普通", "专家", "特需"]
		
		schedule_detail = {
			"schedule_id": schedule.schedule_id,
			"doctor_id": doctor.doctor_id,
			"doctor_name": doctor.name,
			"doctor_title": doctor.title,
			"department_id": dept.minor_dept_id,
			"department_name": dept.name,
			"clinic_id": clinic.clinic_id,
			"clinic_name": clinic.name,
			"clinic_type": "普通门诊" if clinic.clinic_type == 0 else ("专家门诊" if clinic.clinic_type == 1 else "特需门诊"),
			"date": str(schedule.date),
			"week_day": schedule.week_day,
			"time_section": schedule.time_section,
			"slot_type": schedule.slot_type.value if hasattr(schedule.slot_type, 'value') else str(schedule.slot_type),
			"total_slots": schedule.total_slots,
			"remaining_slots": schedule.remaining_slots,
			"status": schedule.status,
			"price": float(schedule.price),
			"available_slot_types": available_types
		}
		
		return ResponseModel(code=0, message=schedule_detail)
		
	except AuthHTTPException:
		raise
	except BusinessHTTPException:
		raise
	except ResourceHTTPException:
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取排班详情失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"获取排班详情失败: {str(e)}"
		)


# ==================== 接诊队列管理 API ====================

@router.get("/consultation/queue", response_model=ResponseModel)
async def get_queue(
	schedule_id: int,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""
	获取某次排班的接诊队列
	
	- **schedule_id**: 排班ID（必填，唯一标识某次出诊，如某天上午/下午/晚间）
	
	返回：
	- scheduleInfo: 排班信息（排班ID、医生ID、日期、时段）
	- stats: 统计数据（总号源、候诊人数、已完成、过号等）
	- currentPatient: 当前正在就诊的患者（如果有）
	- nextPatient: 下一位候诊患者（如果有）
	- queue: 正式队列列表（CONFIRMED状态，按优先级、过号次数、挂号时间排序）
	- waitlist: 候补队列列表（WAITLIST状态）
	"""
	try:
		# 验证排班是否存在
		schedule_res = await db.execute(
			select(Schedule).where(Schedule.schedule_id == schedule_id)
		)
		schedule = schedule_res.scalar_one_or_none()
		
		if not schedule:
			raise BusinessHTTPException(
				code=settings.REQ_ERROR_CODE,
				msg=f"排班 {schedule_id} 不存在",
				status_code=404
			)
		
		# 权限检查
		if not current_user.is_admin:
			# 非管理员必须是医生且是自己的排班
			res = await db.execute(select(Doctor).where(Doctor.user_id == current_user.user_id))
			current_doctor = res.scalar_one_or_none()
			if not current_doctor:
				raise AuthHTTPException(
					code=settings.INSUFFICIENT_AUTHORITY_CODE,
					msg="仅医生可查看接诊队列",
					status_code=403
				)
			
			if schedule.doctor_id != current_doctor.doctor_id:
				raise AuthHTTPException(
					code=settings.INSUFFICIENT_AUTHORITY_CODE,
					msg="只能查看本人的排班队列",
					status_code=403
				)
		
		# 调用服务层获取队列
		queue_data = await get_consultation_queue(db, schedule_id)
		
		return ResponseModel(code=0, message=queue_data)
		
	except AuthHTTPException:
		raise
	except BusinessHTTPException:
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取接诊队列失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"获取接诊队列失败: {str(e)}"
		)


@router.post("/consultation/complete", response_model=ResponseModel)
async def complete_consultation(
	patient_id: int = Body(..., embed=True),
	schedule_id: int = Body(..., embed=True),
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""
	完成当前患者就诊（患者正常就诊完毕）
	
	- **patient_id**: 患者ID
	- **schedule_id**: 排班ID
	
	流程：
	1. 验证患者是否正在就诊（is_calling=True）
	2. 标记为已完成（status=COMPLETED）
	3. 记录就诊时间（visit_times）
	
	返回：
	- completedPatient: 完成就诊的患者信息
	"""
	try:
		# 权限检查：必须是医生
		res = await db.execute(select(Doctor).where(Doctor.user_id == current_user.user_id))
		current_doctor = res.scalar_one_or_none()
		if not current_doctor and not current_user.is_admin:
			raise AuthHTTPException(
				code=settings.INSUFFICIENT_AUTHORITY_CODE,
				msg="仅医生可执行完成就诊操作",
				status_code=403
			)
		
		doctor_id = current_doctor.doctor_id if current_doctor else None
		
		# 验证排班是否属于当前医生
		if doctor_id:
			res = await db.execute(
				select(Schedule).where(Schedule.schedule_id == schedule_id)
			)
			schedule = res.scalar_one_or_none()
			if not schedule or schedule.doctor_id != doctor_id:
				raise AuthHTTPException(
					code=settings.INSUFFICIENT_AUTHORITY_CODE,
					msg="只能完成本人排班下的患者就诊",
					status_code=403
				)
		
		# 调用服务层
		result = await complete_current_patient(db=db, patient_id=patient_id, schedule_id=schedule_id, doctor_id=doctor_id)
		
		await db.commit()
		
		return ResponseModel(
			code=0,
			message={
				"detail": "就诊完成",
				**result
			}
		)
		
	except AuthHTTPException:
		raise
	except BusinessHTTPException:
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"完成就诊操作失败: {e}")
		await db.rollback()
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"完成就诊操作失败: {str(e)}"
		)


@router.post("/consultation/next", response_model=ResponseModel)
async def call_next(
	schedule_id: int = Body(..., embed=True),
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""
	呼叫下一位患者（针对某次排班）
	
	- **schedule_id**: 排班ID（必填）
	
	流程：
	1. 从队列中选取下一位（CONFIRMED 且未叫号）
	2. 标记为正在就诊（is_calling=True）
	3. 记录叫号时间（call_time）
	
	返回：
	- nextPatient: 新呼叫的患者信息
	- scheduleId: 排班ID
	
	注意：如果需要先完成当前患者，请先调用 /consultation/complete
	"""
	try:
		# 验证排班并检查权限
		schedule_res = await db.execute(
			select(Schedule).where(Schedule.schedule_id == schedule_id)
		)
		schedule = schedule_res.scalar_one_or_none()
		
		if not schedule:
			raise BusinessHTTPException(
				code=settings.REQ_ERROR_CODE,
				msg=f"排班 {schedule_id} 不存在",
				status_code=404
			)
		
		# 权限检查：必须是医生且是自己的排班
		res = await db.execute(select(Doctor).where(Doctor.user_id == current_user.user_id))
		current_doctor = res.scalar_one_or_none()
		if not current_doctor and not current_user.is_admin:
			raise AuthHTTPException(
				code=settings.INSUFFICIENT_AUTHORITY_CODE,
				msg="仅医生可执行叫号操作",
				status_code=403
			)
		
		if current_doctor and schedule.doctor_id != current_doctor.doctor_id:
			raise AuthHTTPException(
				code=settings.INSUFFICIENT_AUTHORITY_CODE,
				msg="只能叫本人排班的号",
				status_code=403
			)
		
		# 调用服务层
		result = await call_next_patient(db=db, schedule_id=schedule_id)
		
		await db.commit()
		
		detail = "已呼叫下一位" if result["nextPatient"] else "队列已空"
		
		return ResponseModel(
			code=0,
			message={
				"detail": detail,
				**result
			}
		)
		
	except AuthHTTPException:
		raise
	except BusinessHTTPException:
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"呼叫下一位失败: {e}")
		await db.rollback()
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"呼叫下一位失败: {str(e)}"
		)


@router.post("/consultation/pass", response_model=ResponseModel)
async def pass_current_patient(
	patient_id: int = Body(..., embed=True),
	max_pass_count: Optional[int] = Body(None, embed=True),
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""
	过号操作（当前被叫号的患者未到场）
	
	- **patient_id**: 需要过号的患者订单ID（必须是正在叫号的患者）
	- **max_pass_count**: 可选，覆盖系统配置的过号次数上限
	  - 不传：从配置读取（优先级：医生配置 > 全局配置 > 默认3次）
	  - 传入：使用指定值（临时覆盖，不影响配置）
	
	流程：
	1. 验证患者是否正在被叫号（is_calling=True）
	2. 增加过号次数（pass_count += 1），取消叫号标记
	3. 患者回到队列，因 pass_count 增加自动排到后面
	4. 检查过号次数，达到上限则标记为 NO_SHOW（爽约）
	5. 自动呼叫下一位
	
	返回：
	- passedPatient: 过号患者信息（包含过号次数、是否爽约）
	- nextPatient: 自动呼叫的下一位患者
	"""
	try:
		# 权限检查：必须是医生
		res = await db.execute(select(Doctor).where(Doctor.user_id == current_user.user_id))
		current_doctor = res.scalar_one_or_none()
		if not current_doctor and not current_user.is_admin:
			raise AuthHTTPException(
				code=settings.INSUFFICIENT_AUTHORITY_CODE,
				msg="仅医生可执行过号操作",
				status_code=403
			)
		
		doctor_id = current_doctor.doctor_id if current_doctor else None
		
		# 验证订单是否属于当前医生
		if doctor_id:
			res = await db.execute(
				select(RegistrationOrder).where(RegistrationOrder.order_id == patient_id)
			)
			patient_order = res.scalar_one_or_none()
			if patient_order and patient_order.doctor_id != doctor_id:
				raise AuthHTTPException(
					code=settings.INSUFFICIENT_AUTHORITY_CODE,
					msg="只能对本人患者执行过号操作",
					status_code=403
				)
		
		# 调用服务层
		result = await pass_patient(
			db=db,
			patient_order_id=patient_id,
			doctor_id=doctor_id,
			slot_date=date.today(),
			max_pass_count=max_pass_count
		)
		
		await db.commit()
		
		detail = "过号成功，患者已标记为爽约" if result["passedPatient"]["isNoShow"] else "过号成功"
		
		return ResponseModel(
			code=0,
			message={
				"detail": detail,
				**result
			}
		)
		
	except AuthHTTPException:
		raise
	except BusinessHTTPException:
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"过号操作失败: {e}")
		await db.rollback()
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"过号操作失败: {str(e)}"
		)



# ==================== 医生请假 API ====================		
@router.get("/leave/schedule", response_model=ResponseModel)
async def get_leave_schedule(
	year: int,
	month: int,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""
	获取月度排班与请假状态
	用于在日历上展示某个月份每一天的排班情况以及医生的请假状态
	"""
	try:
		# 验证当前用户是否为医生
		res = await db.execute(select(Doctor).where(Doctor.user_id == current_user.user_id))
		db_doctor = res.scalar_one_or_none()
		if not db_doctor:
			raise AuthHTTPException(
				code=settings.INSUFFICIENT_AUTHORITY_CODE,
				msg="仅医生可查询请假排班",
				status_code=403
			)

		# 验证月份参数
		if not (1 <= month <= 12):
			raise BusinessHTTPException(
				code=settings.REQ_ERROR_CODE,
				msg="月份参数错误，应为 1-12",
				status_code=400
			)

		# 计算该月的起始和结束日期
		from calendar import monthrange
		_, last_day = monthrange(year, month)
		start_date = date(year, month, 1)
		end_date = date(year, month, last_day)

		# 查询该医生在该月的所有排班
		schedules_result = await db.execute(
			select(Schedule)
			.where(
				and_(
					Schedule.doctor_id == db_doctor.doctor_id,
					Schedule.date >= start_date,
					Schedule.date <= end_date
				)
			)
		)
		schedules = schedules_result.scalars().all()

		# 构建日期->排班映射
		schedule_map = {}
		for sch in schedules:
			date_str = sch.date.strftime("%Y-%m-%d")
			if date_str not in schedule_map:
				schedule_map[date_str] = []
			schedule_map[date_str].append(sch)

		# 查询该医生在该月的所有请假申请
		leave_result = await db.execute(
			select(LeaveAudit)
			.where(
				and_(
					LeaveAudit.doctor_id == db_doctor.doctor_id,
					LeaveAudit.leave_start_date <= end_date,
					LeaveAudit.leave_end_date >= start_date
				)
			)
		)
		leaves = leave_result.scalars().all()

		# 构建日期->请假状态映射 (区分全天与分时段)
		# leave_map[date_str] = {"full": status, "morning": status, "afternoon": status, "night": status}
		leave_map = {}
		for leave in leaves:
			current_date = leave.leave_start_date
			shift_val = leave.shift or "full"
			while current_date <= leave.leave_end_date:
				date_str = current_date.strftime("%Y-%m-%d")
				if start_date <= current_date <= end_date:
					if date_str not in leave_map:
						leave_map[date_str] = {}
					
					# 优先级处理（pending > approved > rejected）
					def merge_status(old_status, new_status):
						if old_status is None:
							return new_status
						if new_status == "pending" or old_status == "pending":
							return "pending"
						if new_status == "approved":
							return "approved"
						return old_status
					
					if shift_val == "full":
						# 全天请假覆盖所有时段
						for s in ["full", "morning", "afternoon", "night"]:
							leave_map[date_str][s] = merge_status(leave_map[date_str].get(s), leave.status)
					else:
						# 单时段请假
						leave_map[date_str][shift_val] = merge_status(leave_map[date_str].get(shift_val), leave.status)
				current_date += timedelta(days=1)

		# 构建响应数据
		today = date.today()
		result = []
		from app.schemas.leave import ShiftLeaveStatus
		for day in range(1, last_day + 1):
			current_date = date(year, month, day)
			date_str = current_date.strftime("%Y-%m-%d")
	
			# 判断是否有排班
			has_shift = date_str in schedule_map
			shift_info = None
			if has_shift:
				# 构建排班简要描述
				time_sections = [sch.time_section for sch in schedule_map[date_str]]
				shift_info = "、".join(sorted(set(time_sections)))

			# 获取请假状态
			day_leaves = leave_map.get(date_str, {})
			leave_status = day_leaves.get("full")  # 全天请假状态
			
			# 分时段请假状态
			shift_leave_statuses = []
			for shift_key in ["morning", "afternoon", "night"]:
				if shift_key in day_leaves and day_leaves[shift_key]:
					shift_leave_statuses.append(ShiftLeaveStatus(
						shift=shift_key,
						leaveStatus=day_leaves[shift_key]
					))

			result.append(
				DayScheduleItem(
					date=date_str,
					day=day,
					hasShift=has_shift,
					shiftInfo=shift_info,
					leaveStatus=leave_status,
					shiftLeaveStatuses=shift_leave_statuses,
					isToday=(current_date == today)
				)
			)

		return ResponseModel(code=0, message={"days": [item.dict() for item in result]})

	except AuthHTTPException:
		raise
	except BusinessHTTPException:
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取月度排班失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"获取月度排班失败: {str(e)}"
		)


@router.post("/leave/apply", response_model=ResponseModel)
async def apply_leave(
	data: LeaveApplyRequest,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""
	提交请假申请
	医生选择具体日期和时段后，提交请假申请
	"""
	try:
		# 验证当前用户是否为医生
		res = await db.execute(select(Doctor).where(Doctor.user_id == current_user.user_id))
		db_doctor = res.scalar_one_or_none()
		if not db_doctor:
			raise AuthHTTPException(
				code=settings.INSUFFICIENT_AUTHORITY_CODE,
				msg="仅医生可提交请假申请",
				status_code=403
			)

		# 业务日期校验（不同请假类型的提交时限）
		leave_date = datetime.strptime(data.date, "%Y-%m-%d").date()
		now = datetime.now()
		today = date.today()
		if data.shift == ShiftEnum.FULL:
			# 全天需至少提前一天
			if leave_date <= today:
				raise BusinessHTTPException(
					code=settings.REQ_ERROR_CODE,
					msg="全天请假需至少提前一天提交",
					status_code=400
				)
		else:
			# 单时段请假：允许今天申请，但必须在该时段开始前
			if leave_date < today:
				raise BusinessHTTPException(
					code=settings.REQ_ERROR_CODE,
					msg="请假日期不能早于今天",
					status_code=400
				)
			if leave_date == today:
				# 获取排班配置时段起始时间
				schedule_cfg = await get_schedule_config(db)
				shift_start_map = {
					ShiftEnum.MORNING: schedule_cfg.get("morningStart", "08:00"),
					ShiftEnum.AFTERNOON: schedule_cfg.get("afternoonStart", "13:30"),
					ShiftEnum.NIGHT: schedule_cfg.get("eveningStart", "18:00"),
				}
				start_str = shift_start_map.get(data.shift)
				try:
					hour, minute = [int(x) for x in start_str.split(":")]
				except Exception:
					hour, minute = 0, 0
				shift_start_dt = datetime(today.year, today.month, today.day, hour, minute)
				if now >= shift_start_dt:
					raise BusinessHTTPException(
						code=settings.REQ_ERROR_CODE,
						msg="该时段已开始，无法申请当天请假",
						status_code=400
					)

		# 根据时段确定请假的起止日期
		leave_start_date = leave_date
		leave_end_date = leave_date

		# 检查是否已有同日期同时段的待审核或已通过的请假申请
		existing = await db.execute(
			select(LeaveAudit)
			.where(
				and_(
					LeaveAudit.doctor_id == db_doctor.doctor_id,
					LeaveAudit.leave_start_date <= leave_end_date,
					LeaveAudit.leave_end_date >= leave_start_date,
					LeaveAudit.status.in_(["pending", "approved"])
				)
			)
		)
		existing_leaves = existing.scalars().all()
		
		# 检查时段冲突
		for exist in existing_leaves:
			exist_shift = exist.shift or "full"
			# 如果已有全天请假，或当前申请全天，或时段相同，则冲突
			if exist_shift == "full" or data.shift.value == "full" or exist_shift == data.shift.value:
				raise BusinessHTTPException(
					code=settings.REQ_ERROR_CODE,
					msg=f"该日期该时段已有待审核或已通过的请假申请",
					status_code=400
				)

		# 构建附件数据（统一为字符串路径列表）
		attachments_data = []
		if data.attachments:
			for att in data.attachments:
				if isinstance(att, str):
					attachments_data.append(att)

		# 创建请假申请
		new_leave = LeaveAudit(
			doctor_id=db_doctor.doctor_id,
			leave_start_date=leave_start_date,
			leave_end_date=leave_end_date,
			shift=data.shift.value,
			reason=data.reason,
			attachment_data_json=attachments_data if attachments_data else None,
			status="pending",
			submit_time=datetime.now()
		)
		db.add(new_leave)
		await db.commit()
		await db.refresh(new_leave)

		return ResponseModel(code=0, message={"applicationId": str(new_leave.audit_id)})

	except AuthHTTPException:
		await db.rollback()
		raise
	except BusinessHTTPException:
		await db.rollback()
		raise
	except Exception as e:
		await db.rollback()
		import logging
		logging.getLogger(__name__).error(f"提交请假申请失败: {e}")
		raise BusinessHTTPException(
			code=settings.REQ_ERROR_CODE,
			msg=f"提交请假申请失败: {str(e)}"
		)


@router.get("/leave/history", response_model=ResponseModel)
async def get_leave_history(
	page: int = 1,
	pageSize: int = 20,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""
	获取请假历史记录
	获取当前医生的所有请假申请记录，按时间倒序排列
	"""
	try:
		# 验证当前用户是否为医生
		res = await db.execute(select(Doctor).where(Doctor.user_id == current_user.user_id))
		db_doctor = res.scalar_one_or_none()
		if not db_doctor:
			raise AuthHTTPException(
				code=settings.INSUFFICIENT_AUTHORITY_CODE,
				msg="仅医生可查询请假历史",
				status_code=403
			)

		# 查询总数
		from sqlalchemy import func
		count_result = await db.execute(
			select(func.count())
			.select_from(LeaveAudit)
			.where(LeaveAudit.doctor_id == db_doctor.doctor_id)
		)
		total = count_result.scalar()

		# 分页查询 - join User 或 Doctor 获取审核人姓名(可能是管理员或科室长)
		offset = (page - 1) * pageSize
		# 先尝试从 User 获取姓名,如果审核人是管理员则从Administrator表,如果是医生则从Doctor表
		result = await db.execute(
			select(LeaveAudit, Administrator.name, Doctor.name)
			.outerjoin(Administrator, Administrator.user_id == LeaveAudit.auditor_user_id)
			.outerjoin(Doctor, Doctor.user_id == LeaveAudit.auditor_user_id)
			.where(LeaveAudit.doctor_id == db_doctor.doctor_id)
			.order_by(LeaveAudit.submit_time.desc())
			.offset(offset)
			.limit(pageSize)
		)
		rows = result.all()
		
		# 时段映射
		shift_map = {
			"morning": "上午",
			"afternoon": "下午", 
			"night": "晚间",
			"full": "全天"
		}

		history_list = []
		for leave, admin_name, doctor_name in rows:
			# 审核人姓名:优先用admin_name,否则用doctor_name
			approver_name = admin_name or doctor_name
			# 从数据库读取实际 shift 值
			shift = leave.shift or "full"
			date_str = leave.leave_start_date.strftime("%Y-%m-%d")
		
			# 获取附件列表（统一为字符串路径列表）
			attachments: list[str] = []
			if leave.attachment_data_json and isinstance(leave.attachment_data_json, list):
				for item in leave.attachment_data_json:
					if isinstance(item, dict):
						url = item.get("url") or item.get("path") or ""
						if url:
							attachments.append(url)
					elif isinstance(item, str):
						attachments.append(item)

			history_list.append(
				LeaveHistoryItem(
					id=str(leave.audit_id),
					date=date_str,
					shift=shift,
					reason=leave.reason,
					status=leave.status,
					createTime=leave.submit_time.strftime("%Y-%m-%d %H:%M:%S"),
					approver=approver_name,
					rejectReason=leave.audit_remark if leave.status == "rejected" else None,
					attachments=attachments
				)
			)

		return ResponseModel(code=0, message={
			"total": total,
			"list": [item.dict() for item in history_list]
		})

	except AuthHTTPException:
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取请假历史失败: {e}")
		raise BusinessHTTPException(
			code=settings.DATA_GET_FAILED_CODE,
			msg=f"获取请假历史失败: {str(e)}"
		)


@router.get("/patient/{patient_id}", response_model=ResponseModel)
async def get_patient_detail(
	patient_id: int,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""
	医生查看患者详情接口
	- 获取患者基本信息
	- 获取患者病史信息（既往病史、过敏史、家族病史）
	- 获取患者就诊记录
	"""
	try:
		# 权限检查：仅医生可访问
		doctor = await _get_doctor(db, current_user)
		
		# 查询患者基本信息
		patient_res = await db.execute(
			select(Patient)
			.options(selectinload(Patient.user))
			.where(Patient.patient_id == patient_id)
		)
		patient = patient_res.scalar_one_or_none()
		
		if not patient:
			raise ResourceHTTPException(
				code=404,
				msg="患者不存在",
				status_code=404
			)

		# 授权校验：仅允许查看自己接诊过的患者
		visit_check_res = await db.execute(
			select(VisitHistory.visit_id)
			.where(
				VisitHistory.patient_id == patient_id,
				VisitHistory.doctor_id == doctor.doctor_id
			)
			.limit(1)
		)
		if visit_check_res.scalar_one_or_none() is None:
			raise ResourceHTTPException(
				code=403,
				msg="无权查看该患者信息",
				status_code=403
			)
		
		# 计算年龄
		age = None
		if patient.birth_date:
			today = date.today()
			age = today.year - patient.birth_date.year
			if today.month < patient.birth_date.month or (
				today.month == patient.birth_date.month and today.day < patient.birth_date.day
			):
				age -= 1
		
		# 手机号脱敏（保留前3位和后4位）
		phone_masked = None
		if patient.user and patient.user.phonenumber:
			phone = str(patient.user.phonenumber)
			if len(phone) >= 11:  # 标准手机号11位
				phone_masked = phone[:3] + "****" + phone[-4:]
			elif len(phone) >= 7:  # 至少7位才脱敏
				phone_masked = phone[:3] + "****" + phone[-4:]
			else:
				# 太短的号码用星号代替
				phone_masked = "*" * len(phone)
		
		# 身份证号脱敏（保留前6位和后4位） - 使用 student_id 作为身份证号
		idcard_masked = None
		if patient.student_id and len(patient.student_id) >= 10:
			idcard = patient.student_id
			idcard_masked = idcard[:6] + "********" + idcard[-4:]
		elif patient.student_id:
			idcard_masked = patient.student_id
		
		# 构建基本信息
		basic_info = {
			"name": patient.name,
			"gender": patient.gender.value if patient.gender else "未知",
			"age": age,
			"height": None,  # 数据库暂无身高字段，返回 null
			"phone": phone_masked,
			"idCard": idcard_masked,
			"address": "北京市海淀区学院路37号北京交通大学"  # 默认地址
		}
		
		# 病史信息（目前数据库没有专门的病史表，返回空数组）
		# 可以根据实际业务需求从其他表或字段读取
		medical_history = {
			"pastHistory": [],
			"allergyHistory": [],
			"familyHistory": []
		}
		
		# 查询就诊记录
		visit_res = await db.execute(
			select(VisitHistory)
			.options(
				selectinload(VisitHistory.doctor).selectinload(Doctor.minor_department)
			)
			.where(
				VisitHistory.patient_id == patient_id,
				VisitHistory.doctor_id == doctor.doctor_id
			)
			.order_by(VisitHistory.visit_date.desc())
		)
		visit_records = visit_res.scalars().all()
		
		# 构建就诊记录列表
		consultation_records = []
		for visit in visit_records:
			# 获取科室名称
			department_name = "未知科室"
			if visit.doctor and visit.doctor.minor_department:
				department_name = visit.doctor.minor_department.name
			
			# 获取医生姓名
			doctor_name = "未知医生"
			if visit.doctor:
				doctor_name = visit.doctor.name
			
			# 处理就诊日期时间
			visit_datetime = visit.visit_date.strftime("%Y-%m-%d") if visit.visit_date else ""
			if visit.create_time:
				visit_datetime = visit.create_time.strftime("%Y-%m-%d %H:%M")
			
			# 状态处理
			status = "completed"
			if visit.followup_required:
				status = "ongoing"
			
			record = {
				"id": str(visit.visit_id),
				"outpatientNo": f"{visit.visit_id:06d}",  # 使用就诊记录ID生成门诊号
				"visitDate": visit_datetime,
				"department": department_name,
				"doctorName": doctor_name,
				"chiefComplaint": visit.diagnosis or "",  # 主诉（使用诊断字段代替）
				"presentIllness": visit.advice or "",  # 现病史（使用建议字段代替）
				"auxiliaryExam": visit.attachments or "",  # 辅助检查
				"diagnosis": visit.diagnosis or "",
				"prescription": visit.prescription or "",
				"status": status
			}
			
			consultation_records.append(record)
		
		# 返回完整数据
		return ResponseModel(code=0, message={
			"patientId": str(patient_id),
			"basicInfo": basic_info,
			"medicalHistory": medical_history,
			"consultationRecords": consultation_records
		})
		
	except AuthHTTPException:
		raise
	except ResourceHTTPException:
		raise
	except Exception as e:
		import logging
		logging.getLogger(__name__).error(f"获取患者详情失败: {e}")
		raise BusinessHTTPException(
			code=500,
			msg=f"获取患者详情失败: {str(e)}",
			status_code=500
		)
