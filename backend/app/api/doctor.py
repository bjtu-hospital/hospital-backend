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
from app.db.base import redis
from app.services.add_slot_service import execute_add_slot_and_register
from app.schemas.response import ResponseModel
from typing import Optional
from datetime import datetime, date, timedelta
import json

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


# ===== 医生工作台相关辅助函数 =====

async def _get_time_section_config(
	db: AsyncSession,
	time_section: str,
	clinic_id: int | None = None
) -> tuple[str, str]:
	"""
	分级查询时间段配置
	优先级: CLINIC > GLOBAL
	返回: (start_time, end_time)
	"""
	# 标准化时间段名称映射
	label_to_key = {
		"上午": "MORNING",
		"早": "MORNING",
		"morning": "MORNING",
		"下午": "AFTERNOON",
		"after": "AFTERNOON",
		"afternoon": "AFTERNOON",
		"晚": "EVENING",
		"晚上": "EVENING",
		"晚间": "EVENING",
		"evening": "EVENING",
		"夜": "EVENING"
	}
	
	search_key = label_to_key.get(time_section, None)
	
	# 查询顺序: CLINIC -> GLOBAL
	search_order = []
	if clinic_id:
		search_order.append(("CLINIC", clinic_id))
	search_order.append(("GLOBAL", None))
	
	for scope_type, scope_id in search_order:
		query = select(SystemConfig).where(
			and_(
				SystemConfig.config_key == "scheduling.time_sections",
				SystemConfig.scope_type == scope_type,
				SystemConfig.is_active == True
			)
		)
		
		if scope_type == "GLOBAL":
			query = query.where(SystemConfig.scope_id.is_(None))
		else:
			query = query.where(SystemConfig.scope_id == scope_id)
		
		result = await db.execute(query)
		config = result.scalar_one_or_none()
		
		if config and config.config_value:
			# config_value 是 JSON 数组: [{"key": "MORNING", "label": "上午", "start": "08:00", "end": "12:00"}, ...]
			sections = config.config_value if isinstance(config.config_value, list) else []
			
			# 优先按 key 匹配
			if search_key:
				for section in sections:
					if section.get("key") == search_key:
						return (section.get("start", "08:00"), section.get("end", "17:00"))
			
			# 再按 label 精确匹配
			for section in sections:
				if section.get("label") == time_section:
					return (section.get("start", "08:00"), section.get("end", "17:00"))
	
	# 兜底默认值
	return ("08:00", "17:00")


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
	doctor = await _get_doctor(db, current_user)
	# 部门
	dept_res = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == doctor.dept_id))
	dept = dept_res.scalar_one_or_none()

	schedules = await _get_today_schedules(db, doctor.doctor_id)
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


@router.post("/workbench/checkin", response_model=ResponseModel[CheckinResponse])
async def workbench_checkin(
	shiftId: int = Body(..., embed=True),
	latitude: Optional[float] = Body(None),
	longitude: Optional[float] = Body(None),
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""签到接口 - 仅限当天排班，提前30分钟可签到"""
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


@router.post("/workbench/checkout", response_model=ResponseModel[CheckoutResponse])
async def workbench_checkout(
	shiftId: int = Body(..., embed=True),
	latitude: Optional[float] = Body(None),
	longitude: Optional[float] = Body(None),
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""签退接口 - 仅限当天排班，必须先签到，班次结束后2小时内可签退"""
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


@router.get("/workbench/shifts", response_model=ResponseModel[ShiftsResponse])
async def workbench_shifts(doctorId: Optional[int] = None, date_str: Optional[str] = None, db: AsyncSession = Depends(get_db), current_user: UserSchema = Depends(get_current_user)):
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


@router.get("/workbench/consultation-stats", response_model=ResponseModel[ConsultationStatsResponse])
async def workbench_consultation_stats(doctorId: int, db: AsyncSession = Depends(get_db), current_user: UserSchema = Depends(get_current_user)):
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


@router.get("/workbench/recent-consultations", response_model=ResponseModel[RecentConsultationsResponse])
async def workbench_recent_consultations(doctorId: int, limit: int = 3, db: AsyncSession = Depends(get_db), current_user: UserSchema = Depends(get_current_user)):
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


