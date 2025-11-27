from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import date as date_type, timedelta
import logging

from app.core.config import settings
from app.core.exception_handler import BusinessHTTPException, ResourceHTTPException, AuthHTTPException
from app.schemas.response import ResponseModel
from app.db.base import get_db
from app.api.auth import get_current_user
from app.schemas.user import user as UserSchema
from app.models.visit_history import VisitHistory
from app.models.patient import Patient
from app.models.doctor import Doctor
from app.models.administrator import Administrator
from app.services.pdf_service import MedicalRecordPDFGenerator, ensure_pdf_directory

import os
from datetime import datetime
import uuid
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)
router = APIRouter()

# 允许上传的图片格式
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
# 最大文件大小 (5MB)
MAX_FILE_SIZE = 5 * 1024 * 1024


def get_file_extension(filename: str) -> str:
	"""获取文件扩展名"""
	return Path(filename).suffix.lower()


def is_allowed_file(filename: str) -> bool:
	"""检查文件类型是否允许"""
	return get_file_extension(filename) in ALLOWED_EXTENSIONS


def generate_unique_filename(original_filename: str) -> str:
	"""生成唯一文件名: 时间戳_UUID_原始名"""
	ext = get_file_extension(original_filename)
	timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
	unique_id = str(uuid.uuid4())[:8]
	# 保留原始文件名(去除扩展名)
	original_name = Path(original_filename).stem
	# 清理文件名中的特殊字符
	safe_name = "".join(c for c in original_name if c.isalnum() or c in (' ', '-', '_'))[:30]
	return f"{timestamp}_{unique_id}_{safe_name}{ext}"


@router.post("/upload", response_model=ResponseModel)
async def upload_image(file: UploadFile = File(...)):
	"""
	通用图片上传接口
	
	支持格式: jpg, jpeg, png, gif, bmp, webp
	最大文件大小: 5MB
	
	返回格式:
	{
		"code": 0,
		"message": {
			"url": "static/images/audit/20251126143020_a1b2c3d4_诊断证明.jpg",
			"name": "诊断证明.jpg"
		}
	}
	"""
	try:
		# 1. 验证文件类型
		if not file.filename:
			raise BusinessHTTPException(
				code=settings.REQ_ERROR_CODE,
				msg="文件名不能为空",
				status_code=400
			)
		
		if not is_allowed_file(file.filename):
			raise BusinessHTTPException(
				code=settings.REQ_ERROR_CODE,
				msg=f"不支持的文件格式，仅支持: {', '.join(ALLOWED_EXTENSIONS)}",
				status_code=400
			)
		
		# 2. 读取文件内容并验证大小
		contents = await file.read()
		file_size = len(contents)
		
		if file_size > MAX_FILE_SIZE:
			raise BusinessHTTPException(
				code=settings.REQ_ERROR_CODE,
				msg=f"文件过大，最大支持 {MAX_FILE_SIZE / 1024 / 1024:.1f}MB",
				status_code=400
			)
		
		if file_size == 0:
			raise BusinessHTTPException(
				code=settings.REQ_ERROR_CODE,
				msg="文件内容为空",
				status_code=400
			)
		
		# 3. 生成保存路径
		# 按日期分类: static/images/audit/2025/11/26/
		now = datetime.now()
		date_path = now.strftime("%Y/%m/%d")
		upload_dir = Path("app/static/images/audit") / date_path
		
		# 确保目录存在
		upload_dir.mkdir(parents=True, exist_ok=True)
		
		# 4. 生成唯一文件名
		unique_filename = generate_unique_filename(file.filename)
		file_path = upload_dir / unique_filename
		
		# 5. 保存文件
		with open(file_path, "wb") as f:
			f.write(contents)
		
		# 6. 返回相对路径 (供前端访问和存储到数据库)
		relative_path = f"static/images/audit/{date_path}/{unique_filename}"
		
		return ResponseModel(
			code=0,
			message={
				"url": relative_path,
				"name": file.filename  # 保留原始文件名
			}
		)
	
	except BusinessHTTPException:
		raise
	except Exception as e:
		raise BusinessHTTPException(
			code=settings.UNKNOWN_ERROR_CODE,
			msg=f"文件上传失败: {str(e)}",
			status_code=500
		)


@router.get("/visit-record/{visit_id}", response_model=ResponseModel)
async def get_visit_record_detail(
	visit_id: int,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""
	获取就诊记录详情（供前端渲染病历单）
	
	权限控制：
	- 管理员：可以查看所有病历
	- 医生：只能查看自己接诊的病历
	- 患者：只能查看自己的病历
	
	返回数据包含：
	- basicInfo: 患者基本信息（姓名、性别、年龄）
	- recordData: 病历详细数据（门诊号、就诊日期、科室、医生、主诉、现病史、诊断、处方等）
	"""
	try:
		# 查询就诊记录
		visit_res = await db.execute(
			select(VisitHistory)
			.options(
				selectinload(VisitHistory.doctor).selectinload(Doctor.minor_department),
				selectinload(VisitHistory.patient).selectinload(Patient.user)
			)
			.where(VisitHistory.visit_id == visit_id)
		)
		visit = visit_res.scalar_one_or_none()
		
		if not visit:
			raise ResourceHTTPException(
				code=404,
				msg="就诊记录不存在",
				status_code=404
			)
		
		# ========== 权限验证 ==========
		has_permission = False
		
		# 1. 检查是否是管理员
		admin_res = await db.execute(
			select(Administrator).where(Administrator.user_id == current_user.user_id)
		)
		admin = admin_res.scalar_one_or_none()
		if admin:
			has_permission = True
			logger.info(f"管理员 {current_user.user_id} 访问病历 {visit_id}")
		
		# 2. 检查是否是接诊医生
		if not has_permission:
			doctor_res = await db.execute(
				select(Doctor).where(Doctor.user_id == current_user.user_id)
			)
			doctor = doctor_res.scalar_one_or_none()
			if doctor and visit.doctor_id == doctor.doctor_id:
				has_permission = True
				logger.info(f"医生 {doctor.name} 访问自己的病历 {visit_id}")
		
		# 3. 检查是否是患者本人
		if not has_permission:
			if visit.patient and visit.patient.user_id == current_user.user_id:
				has_permission = True
				logger.info(f"患者 {visit.patient.name} 访问自己的病历 {visit_id}")
		
		# 4. 无权限则拒绝访问
		if not has_permission:
			raise ResourceHTTPException(
				code=403,
				msg="无权查看该病历",
				status_code=403
			)
		
		# ========== 构造返回数据 ==========
		
		# 计算年龄
		age = None
		if visit.patient and visit.patient.birth_date:
			today = date_type.today()
			age = today.year - visit.patient.birth_date.year
			if today.month < visit.patient.birth_date.month or (
				today.month == visit.patient.birth_date.month and today.day < visit.patient.birth_date.day
			):
				age -= 1
		
		# 构造返回数据（与前端页面数据结构一致）
		return ResponseModel(code=0, message={
			"basicInfo": {
				"name": visit.patient.name if visit.patient else "未知",
				"gender": visit.patient.gender.value if visit.patient and visit.patient.gender else "未知",
				"age": age if age else 0
			},
			"recordData": {
				"id": str(visit.visit_id),
				"outpatientNo": f"{visit.visit_id:06d}",
				"visitDate": visit.visit_date.strftime("%Y-%m-%d %H:%M") if visit.visit_date else 
							 (visit.create_time.strftime("%Y-%m-%d %H:%M") if visit.create_time else ""),
				"department": visit.doctor.minor_department.name if visit.doctor and visit.doctor.minor_department else "未知科室",
				"doctorName": visit.doctor.name if visit.doctor else "未知医生",
				# 使用模型实际字段名
				"chiefComplaint": visit.diagnosis or "无",  # 主诉暂用诊断代替
				"presentIllness": visit.advice or "无",      # 现病史暂用建议代替
				"auxiliaryExam": visit.attachments or "",    # 辅助检查暂用附件代替
				"diagnosis": visit.diagnosis or "无",
				"prescription": visit.prescription or ""
			}
		})
		
	except AuthHTTPException:
		raise
	except ResourceHTTPException:
		raise
	except Exception as e:
		logger.error(f"获取病历详情失败: {e}", exc_info=True)
		raise BusinessHTTPException(
			code=500,
			msg=f"获取病历详情失败: {str(e)}",
			status_code=500
		)


@router.post("/medical-record/{visit_id}/pdf", response_model=ResponseModel)
async def generate_medical_record_pdf(
	visit_id: int,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""
	生成病历单PDF
	
	权限控制：
	- 管理员：可以生成所有病历的PDF
	- 医生：只能生成自己接诊的病历PDF
	- 患者：可以生成自己的病历PDF
	
	返回：PDF下载URL、文件名和过期时间
	"""
	try:
		# 查询就诊记录
		visit_res = await db.execute(
			select(VisitHistory)
			.options(
				selectinload(VisitHistory.doctor).selectinload(Doctor.minor_department),
				selectinload(VisitHistory.patient).selectinload(Patient.user)
			)
			.where(VisitHistory.visit_id == visit_id)
		)
		visit = visit_res.scalar_one_or_none()
		
		if not visit:
			raise ResourceHTTPException(
				code=404,
				msg="就诊记录不存在",
				status_code=404
			)
		
		# ========== 权限验证 ==========
		has_permission = False
		
		# 1. 检查是否是管理员
		admin_res = await db.execute(
			select(Administrator).where(Administrator.user_id == current_user.user_id)
		)
		if admin_res.scalar_one_or_none():
			has_permission = True
		
		# 2. 检查是否是接诊医生
		if not has_permission:
			doctor_res = await db.execute(
				select(Doctor).where(Doctor.user_id == current_user.user_id)
			)
			doctor = doctor_res.scalar_one_or_none()
			if doctor and visit.doctor_id == doctor.doctor_id:
				has_permission = True
		
		# 3. 检查是否是患者本人
		if not has_permission:
			if visit.patient and visit.patient.user_id == current_user.user_id:
				has_permission = True
		
		if not has_permission:
			raise ResourceHTTPException(
				code=403,
				msg="无权生成该病历PDF",
				status_code=403
			)
		
		# ========== 准备PDF数据 ==========
		
		# 计算年龄
		patient = visit.patient
		age = None
		if patient and patient.birth_date:
			today = date_type.today()
			age = today.year - patient.birth_date.year
			if today.month < patient.birth_date.month or (
				today.month == patient.birth_date.month and today.day < patient.birth_date.day
			):
				age -= 1
		
		patient_data = {
			"name": patient.name if patient else "未知",
			"gender": patient.gender.value if patient and patient.gender else "未知",
			"age": age if age else 0,
			"outpatientNo": f"{visit_id:06d}",
			"visitDate": visit.visit_date.strftime("%Y-%m-%d") if visit.visit_date else datetime.now().strftime("%Y-%m-%d")
		}
		
		visit_datetime = visit.visit_date.strftime("%Y-%m-%d") if visit.visit_date else ""
		if visit.create_time:
			visit_datetime = visit.create_time.strftime("%Y-%m-%d %H:%M")
		
		visit_data = {
			"department": visit.doctor.minor_department.name if visit.doctor and visit.doctor.minor_department else "未知科室",
			"doctorName": visit.doctor.name if visit.doctor else "未知医生",
			"chiefComplaint": visit.diagnosis or "无",
			"presentIllness": visit.advice or "无",
			"auxiliaryExam": visit.attachments or "",
			"diagnosis": visit.diagnosis or "无",
			"prescription": visit.prescription or "",
			"visitDate": visit_datetime
		}
		
		# 确保PDF目录存在
		pdf_dir = ensure_pdf_directory()
		
		# 生成PDF文件名
		filename = f"medical_record_{visit_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
		pdf_path = pdf_dir / filename
		
		# 生成PDF
		generator = MedicalRecordPDFGenerator()
		generator.generate_medical_record(visit_data, patient_data, str(pdf_path))
		
		# 生成下载接口URL（改为静态文件路径）
		# 相对于 static 目录的路径
		relative_path = f"pdf/medical_records/{filename}"
		pdf_url = f"/static/{relative_path}"
		
		# 计算过期时间（7天后 - 需配合定时清理任务）
		expire_time = datetime.now() + timedelta(days=7)
		
		# 返回结果
		return ResponseModel(code=0, message={
			"url": pdf_url,
			"fileName": f"病历单_{patient.name if patient else '未知'}_{visit_datetime.split()[0]}.pdf",
			"expireTime": expire_time.isoformat() + "Z"
		})
		
	except AuthHTTPException:
		raise
	except ResourceHTTPException:
		raise
	except Exception as e:
		logger.error(f"生成病历PDF失败: {e}", exc_info=True)
		raise BusinessHTTPException(
			code=500,
			msg=f"生成病历PDF失败: {str(e)}",
			status_code=500
		)


@router.get("/medical-record/{visit_id}/download")
async def download_medical_record_pdf(
	visit_id: int,
	db: AsyncSession = Depends(get_db),
	current_user: UserSchema = Depends(get_current_user)
):
	"""
	下载病历单PDF（带权限验证）
	
	权限控制：
	- 管理员：可以下载所有病历PDF
	- 医生：只能下载自己接诊的病历PDF
	- 患者：可以下载自己的病历PDF
	"""
	try:
		# 查询就诊记录
		visit_res = await db.execute(
			select(VisitHistory)
			.options(
				selectinload(VisitHistory.doctor),
				selectinload(VisitHistory.patient).selectinload(Patient.user)
			)
			.where(VisitHistory.visit_id == visit_id)
		)
		visit = visit_res.scalar_one_or_none()
		
		if not visit:
			raise ResourceHTTPException(
				code=404,
				msg="就诊记录不存在",
				status_code=404
			)
		
		# ========== 权限验证 ==========
		has_permission = False
		
		# 1. 检查是否是管理员
		admin_res = await db.execute(
			select(Administrator).where(Administrator.user_id == current_user.user_id)
		)
		if admin_res.scalar_one_or_none():
			has_permission = True
		
		# 2. 检查是否是接诊医生
		if not has_permission:
			doctor_res = await db.execute(
				select(Doctor).where(Doctor.user_id == current_user.user_id)
			)
			doctor = doctor_res.scalar_one_or_none()
			if doctor and visit.doctor_id == doctor.doctor_id:
				has_permission = True
		
		# 3. 检查是否是患者本人
		if not has_permission:
			if visit.patient and visit.patient.user_id == current_user.user_id:
				has_permission = True
		
		if not has_permission:
			raise ResourceHTTPException(
				code=403,
				msg="无权下载该病历",
				status_code=403
			)
		
		# ========== 查找PDF文件 ==========
		pdf_dir = ensure_pdf_directory()
		
		# 查找该visit_id的所有PDF文件
		pdf_files = list(pdf_dir.glob(f"medical_record_{visit_id}_*.pdf"))
		
		if not pdf_files:
			raise ResourceHTTPException(
				code=404,
				msg="病历PDF文件不存在，请先生成病历",
				status_code=404
			)
		
		# 获取最新的PDF文件（按修改时间排序）
		latest_pdf = max(pdf_files, key=lambda p: p.stat().st_mtime)
		
		# 检查文件是否过期（7天）
		file_age = datetime.now().timestamp() - latest_pdf.stat().st_mtime
		if file_age > 7 * 24 * 3600:
			raise ResourceHTTPException(
				code=410,
				msg="病历PDF已过期，请重新生成",
				status_code=410
			)
		
		# 生成友好的文件名
		patient = visit.patient
		visit_date = visit.visit_date.strftime("%Y-%m-%d") if visit.visit_date else datetime.now().strftime("%Y-%m-%d")
		filename = f"病历单_{patient.name if patient else '未知'}_{visit_date}.pdf"
		
		# URL编码中文文件名（避免编码问题）
		encoded_filename = quote(filename)
		
		# 返回文件
		return FileResponse(
			path=str(latest_pdf),
			media_type="application/pdf",
			filename=filename,
			headers={
				"Content-Disposition": f'attachment; filename*=UTF-8\'\'{encoded_filename}'
			}
		)
		
	except AuthHTTPException:
		raise
	except ResourceHTTPException:
		raise
	except Exception as e:
		logger.error(f"下载病历PDF失败: {e}", exc_info=True)
		raise BusinessHTTPException(
			code=500,
			msg=f"下载病历PDF失败: {str(e)}",
			status_code=500
		)

