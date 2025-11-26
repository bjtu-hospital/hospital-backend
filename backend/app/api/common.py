from fastapi import APIRouter, UploadFile, File, HTTPException
from app.core.config import settings
from app.core.exception_handler import BusinessHTTPException
from app.schemas.response import ResponseModel
import os
from datetime import datetime
import uuid
from pathlib import Path

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
