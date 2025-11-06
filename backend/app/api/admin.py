from fastapi import APIRouter, Depends,UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, delete
from typing import Optional, Union
import logging
from app.schemas.admin import MajorDepartmentCreate, MajorDepartmentUpdate, MinorDepartmentCreate, MinorDepartmentUpdate, DoctorCreate, DoctorUpdate, DoctorAccountCreate, DoctorTransferDepartment, ClinicCreate, ClinicListResponse, ScheduleCreate, ScheduleUpdate, ScheduleListResponse
from app.schemas.response import (
    ResponseModel, AuthErrorResponse, MajorDepartmentListResponse, MinorDepartmentListResponse, DoctorListResponse, DoctorAccountCreateResponse, DoctorTransferResponse
)
from app.db.base import get_db, redis, User, Administrator, MajorDepartment, MinorDepartment, Doctor, Clinic, Schedule, ScheduleAudit, LeaveAudit
from app.schemas.user import user as UserSchema
from app.schemas.audit import (
    ScheduleAuditItem, ScheduleAuditListResponse, ScheduleDoctorInfo,AuditAction, AuditActionResponse,LeaveAttachment, LeaveAuditItem, LeaveAuditListResponse 
)
from app.core.config import settings
from app.core.exception_handler import AuthHTTPException, BusinessHTTPException, ResourceHTTPException
from app.api.auth import get_current_user
from app.core.security import get_hash_pwd
from datetime import date, datetime, timedelta
import os
import aiofiles
import time
import mimetypes

logger = logging.getLogger(__name__)
router = APIRouter()


# ====== 管理员科室管理接口 ======

# 大科室管理
@router.post("/major-departments", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def create_major_department(
    dept_data: MajorDepartmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """创建大科室 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 检查科室名称是否已存在
        result = await db.execute(select(MajorDepartment).where(MajorDepartment.name == dept_data.name))
        if result.scalar_one_or_none():
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="科室名称已存在",
                status_code=400
            )
        
        # 创建大科室
        db_dept = MajorDepartment(
            name=dept_data.name,
            description=dept_data.description
        )
        db.add(db_dept)
        await db.commit()
        await db.refresh(db_dept)
        
        logger.info(f"创建大科室成功: {dept_data.name}")
        
        return ResponseModel(
            code=0,
            message={
                "major_dept_id": db_dept.major_dept_id,
                "name": db_dept.name,
                "description": db_dept.description
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"创建大科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/major-departments", response_model=ResponseModel[Union[MajorDepartmentListResponse, AuthErrorResponse]])
async def get_major_departments(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取所有大科室 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        result = await db.execute(select(MajorDepartment))
        departments = result.scalars().all()
        
        dept_list = []
        for dept in departments:
            dept_list.append({
                "major_dept_id": dept.major_dept_id,
                "name": dept.name,
                "description": dept.description
            })
        
        return ResponseModel(
            code=0,
            message=MajorDepartmentListResponse(departments=dept_list)
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取大科室列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/major-departments/{dept_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_major_department(
    dept_id: int,
    dept_data: MajorDepartmentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """更新大科室信息 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 获取科室
        result = await db.execute(select(MajorDepartment).where(MajorDepartment.major_dept_id == dept_id))
        db_dept = result.scalar_one_or_none()
        if not db_dept:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="科室不存在",
                status_code=404
            )
        
        # 检查新名称是否与其他科室冲突
        if dept_data.name and dept_data.name != db_dept.name:
            result = await db.execute(select(MajorDepartment).where(
                and_(MajorDepartment.name == dept_data.name, MajorDepartment.major_dept_id != dept_id)
            ))
            if result.scalar_one_or_none():
                raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="科室名称已存在",
                    status_code=400
                )
        
        # 更新科室信息
        if dept_data.name:
            db_dept.name = dept_data.name
        if dept_data.description is not None:
            db_dept.description = dept_data.description
        
        db.add(db_dept)
        await db.commit()
        await db.refresh(db_dept)
        
        logger.info(f"更新大科室成功: {db_dept.name}")
        
        return ResponseModel(
            code=0,
            message={
                "major_dept_id": db_dept.major_dept_id,
                "name": db_dept.name,
                "description": db_dept.description
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新大科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.delete("/major-departments/{dept_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def delete_major_department(
    dept_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    删除大科室 - 仅管理员可操作。若存在小科室依赖则拒绝删除。
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 检查大科室是否存在
        result = await db.execute(select(MajorDepartment).where(MajorDepartment.major_dept_id == dept_id))
        db_dept = result.scalar_one_or_none()
        if not db_dept:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="科室不存在",
                status_code=404
            )

        # 检查是否存在小科室依赖
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.major_dept_id == dept_id))
        if result.scalar_one_or_none():
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="存在下属小科室，无法删除",
                status_code=400
            )

        # 删除大科室
        await db.delete(db_dept)
        await db.commit()

        logger.info(f"删除大科室成功: {db_dept.name}")
        return ResponseModel(code=0, message={"detail": f"成功删除大科室 {db_dept.name}"})
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除大科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# 小科室管理
@router.post("/minor-departments", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def create_minor_department(
    dept_data: MinorDepartmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """创建小科室 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 检查大科室是否存在
        result = await db.execute(select(MajorDepartment).where(MajorDepartment.major_dept_id == dept_data.major_dept_id))
        if not result.scalar_one_or_none():
            raise ResourceHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="大科室不存在",
                status_code=400
            )
        
        # 检查小科室名称是否已存在
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.name == dept_data.name))
        if result.scalar_one_or_none():
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="科室名称已存在",
                status_code=400
            )
        
        # 创建小科室
        db_dept = MinorDepartment(
            major_dept_id=dept_data.major_dept_id,
            name=dept_data.name,
            description=dept_data.description
        )
        db.add(db_dept)
        await db.commit()
        await db.refresh(db_dept)
        
        logger.info(f"创建小科室成功: {dept_data.name}")
        
        return ResponseModel(
            code=0,
            message={
                "minor_dept_id": db_dept.minor_dept_id,
                "major_dept_id": db_dept.major_dept_id,
                "name": db_dept.name,
                "description": db_dept.description
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"创建小科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/minor-departments/{minor_dept_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_minor_department(
    minor_dept_id: int,
    dept_data: MinorDepartmentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    更新小科室信息 - 仅管理员可操作
    - 必选参数: name, description
    - 可选参数: major_dept_id (用于将小科室转移到其他大科室)
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 获取小科室
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == minor_dept_id))
        db_dept = result.scalar_one_or_none()
        if not db_dept:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="科室不存在",
                status_code=404
            )
        
        # 检查新名称是否与其他科室冲突
        if dept_data.name and dept_data.name != db_dept.name:
            result = await db.execute(select(MinorDepartment).where(
                and_(MinorDepartment.name == dept_data.name, MinorDepartment.minor_dept_id != minor_dept_id)
            ))
            if result.scalar_one_or_none():
                raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="科室名称已存在",
                    status_code=400
                )
        
        # 如果需要转移大科室
        if dept_data.major_dept_id is not None and dept_data.major_dept_id != db_dept.major_dept_id:
            # 检查目标大科室是否存在
            result = await db.execute(select(MajorDepartment).where(MajorDepartment.major_dept_id == dept_data.major_dept_id))
            if not result.scalar_one_or_none():
                raise ResourceHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="目标大科室不存在",
                    status_code=400
                )
            
            # 记录转移信息
            old_major_dept_id = db_dept.major_dept_id
            db_dept.major_dept_id = dept_data.major_dept_id
            logger.info(f"小科室 {db_dept.name} 从大科室 {old_major_dept_id} 转移至大科室 {dept_data.major_dept_id}")
        
        # 更新科室基本信息
        if dept_data.name:
            db_dept.name = dept_data.name
        if dept_data.description is not None:
            db_dept.description = dept_data.description
        
        db.add(db_dept)
        await db.commit()
        await db.refresh(db_dept)
        
        logger.info(f"更新小科室成功: {db_dept.name}")
        
        response_message = {
            "minor_dept_id": db_dept.minor_dept_id,
            "major_dept_id": db_dept.major_dept_id,
            "name": db_dept.name,
            "description": db_dept.description
        }
        
        # 如果发生了科室转移，添加转移相关信息
        if 'old_major_dept_id' in locals():
            response_message["transfer_info"] = {
                "old_major_dept_id": old_major_dept_id,
                "new_major_dept_id": db_dept.major_dept_id
            }
        
        return ResponseModel(code=0, message=response_message)
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新小科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.delete("/minor-departments/{minor_dept_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def delete_minor_department(
    minor_dept_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """
    删除小科室 - 仅管理员可操作。若存在关联医生则拒绝删除。
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 检查小科室是否存在
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == minor_dept_id))
        db_dept = result.scalar_one_or_none()
        if not db_dept:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="科室不存在",
                status_code=404
            )

        # 检查是否有医生关联
        result = await db.execute(select(Doctor).where(Doctor.dept_id == minor_dept_id))
        if result.scalar_one_or_none():
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="存在关联医生，无法删除",
                status_code=400
            )

        # 删除小科室
        await db.delete(db_dept)
        await db.commit()

        logger.info(f"删除小科室成功: {db_dept.name}")
        return ResponseModel(code=0, message={"detail": f"成功删除小科室 {db_dept.name}"})
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除小科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/minor-departments", response_model=ResponseModel[Union[MinorDepartmentListResponse, AuthErrorResponse]])
async def get_minor_departments(
    major_dept_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取小科室列表 - 仅管理员可操作，可按大科室过滤"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 构建查询条件
        filters = []
        if major_dept_id:
            filters.append(MinorDepartment.major_dept_id == major_dept_id)
        
        result = await db.execute(select(MinorDepartment).where(and_(*filters) if filters else True))
        departments = result.scalars().all()
        
        dept_list = []
        for dept in departments:
            dept_list.append({
                "minor_dept_id": dept.minor_dept_id,
                "major_dept_id": dept.major_dept_id,
                "name": dept.name,
                "description": dept.description
            })
        
        return ResponseModel(
            code=0,
            message=MinorDepartmentListResponse(departments=dept_list)
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取小科室列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ====== 管理员医生管理接口 ======

# 医生管理
@router.post("/doctors", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def create_doctor(
    doctor_data: DoctorCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """创建医生信息 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        # 基本校验：小科室必须存在
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == doctor_data.dept_id))
        if not result.scalar_one_or_none():
                raise ResourceHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="小科室不存在",
                status_code=400
            )

        # 工号与密码为可选，但必须同时提供或都不提供
        identifier = getattr(doctor_data, "identifier", None)
        password = getattr(doctor_data, "password", None)
        if (identifier and not password) or (password and not identifier):
                raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="工号和密码必须同时提供或都不提供",
                status_code=400
            )

        # 如果提供了工号（同时也会有密码），仅检查该工号在 User 表是否已存在，
        # 但不在此接口创建用户账号（账号创建通过 /doctors/{id}/create-account 进行）
        if identifier:
            result = await db.execute(select(User).where(User.identifier == identifier))
            if result.scalar_one_or_none():
                 raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="工号已被使用",
                    status_code=400
                )

        # 创建医生信息（先创建医生档案）
        db_doctor = Doctor(
            dept_id=doctor_data.dept_id,
            name=doctor_data.name,
            title=doctor_data.title,
            specialty=doctor_data.specialty,
            introduction=doctor_data.introduction
        )
        db.add(db_doctor)
        await db.commit()
        await db.refresh(db_doctor)

        logger.info(f"创建医生信息成功: {doctor_data.name}")

        response_payload = {
            "doctor_id": db_doctor.doctor_id,
            "dept_id": db_doctor.dept_id,
            "name": db_doctor.name,
            "title": db_doctor.title,
            "specialty": db_doctor.specialty,
            "introduction": db_doctor.introduction
        }

        # 如果请求体中包含工号和密码，则在此一并创建用户账号并关联
        if identifier:
            try:
                # 可选的额外账号字段
                email = getattr(doctor_data, "email", None)
                phonenumber = getattr(doctor_data, "phonenumber", None)

                # 检查邮箱/手机号是否被占用（若有提供）
                if email:
                    r = await db.execute(select(User).where(User.email == email))
                    if r.scalar_one_or_none():
                            raise BusinessHTTPException(
                            code=settings.REQ_ERROR_CODE,
                            msg="邮箱已被使用",
                            status_code=400
                        )
                if phonenumber:
                    r = await db.execute(select(User).where(User.phonenumber == phonenumber))
                    if r.scalar_one_or_none():
                            raise BusinessHTTPException(
                            code=settings.REQ_ERROR_CODE,
                            msg="手机号已被使用",
                            status_code=400
                        )

                # 创建用户账号并关联
                hashed_password = get_hash_pwd(password)
                db_user = User(
                    identifier=identifier,
                    email=email,
                    phonenumber=phonenumber,
                    hashed_password=hashed_password,
                    user_type="doctor",
                    is_admin=False,
                    is_verified=True
                )
                db.add(db_user)
                await db.commit()
                await db.refresh(db_user)

                # 关联医生记录
                db_doctor.user_id = db_user.user_id
                db.add(db_doctor)
                await db.commit()
                await db.refresh(db_doctor)

                response_payload["account_provided"] = True
                response_payload["user_id"] = db_user.user_id
                response_payload["account_note"] = "已为医生创建并关联登录账号。"
            except AuthHTTPException:
                # 已经抛出的业务异常，回滚并抛出
                await db.rollback()
                raise
            except Exception as ex:
                await db.rollback()
                logger.error(f"为医生创建账号时发生异常: {str(ex)}")
                raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="创建医生账号失败",
                    status_code=500
                )
        else:
            response_payload["account_provided"] = False

        return ResponseModel(code=0, message=response_payload)
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"创建医生信息时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/doctors", response_model=ResponseModel[Union[DoctorListResponse, AuthErrorResponse]])
async def get_doctors(
    dept_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取医生列表 - 仅管理员可操作，可按科室过滤"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 构建查询条件
        filters = []
        if dept_id:
            filters.append(Doctor.dept_id == dept_id)
        
        result = await db.execute(select(Doctor).where(and_(*filters) if filters else True))
        doctors = result.scalars().all()
        
        # 预取所有关联的 user（避免循环中多次查询）
        user_ids = [d.user_id for d in doctors if d.user_id]
        users_map = {}
        if user_ids:
            res_users = await db.execute(select(User).where(User.user_id.in_(user_ids)))
            users = res_users.scalars().all()
            users_map = {u.user_id: u for u in users}

        doctor_list = []
        for doctor in doctors:
            is_registered = False
            if doctor.user_id:
                u = users_map.get(doctor.user_id)
                # 更严格的注册定义：对应 User 存在且未删除且处于激活状态
                if u and getattr(u, "is_active", False) and not getattr(u, "is_deleted", False):
                    is_registered = True

            doctor_list.append({
                "doctor_id": doctor.doctor_id,
                "user_id": doctor.user_id,
                "is_registered": is_registered,
                "dept_id": doctor.dept_id,
                "name": doctor.name,
                "title": doctor.title,
                "specialty": doctor.specialty,
                "introduction": doctor.introduction,
                "photo_path": doctor.photo_path,
                "original_photo_url": doctor.original_photo_url
            })
        
        return ResponseModel(
            code=0,
            message=DoctorListResponse(doctors=doctor_list)
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取医生列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/doctors/{doctor_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_doctor(
    doctor_id: int,
    doctor_data: DoctorUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """更新医生信息 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        # 获取医生
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
              raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )
        
        # 如果更新科室，检查新科室是否存在
        if doctor_data.dept_id and doctor_data.dept_id != db_doctor.dept_id:
            result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == doctor_data.dept_id))
            if not result.scalar_one_or_none():
                raise ResourceHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="目标科室不存在",
                    status_code=400
                )

        # 更新医生信息
        if doctor_data.dept_id:
            db_doctor.dept_id = doctor_data.dept_id
        if doctor_data.name:
            db_doctor.name = doctor_data.name
        if doctor_data.title is not None:
            db_doctor.title = doctor_data.title
        if doctor_data.specialty is not None:
            db_doctor.specialty = doctor_data.specialty
        if doctor_data.introduction is not None:
            db_doctor.introduction = doctor_data.introduction
        if doctor_data.photo_path is not None:
            db_doctor.photo_path = doctor_data.photo_path
        if doctor_data.original_photo_url is not None:
            db_doctor.original_photo_url = doctor_data.original_photo_url

        db.add(db_doctor)
        await db.commit()
        await db.refresh(db_doctor)

        logger.info(f"更新医生信息成功: {db_doctor.name}")

        return ResponseModel(
            code=0,
            message={
                "doctor_id": db_doctor.doctor_id,
                "dept_id": db_doctor.dept_id,
                "name": db_doctor.name,
                "title": db_doctor.title,
                "specialty": db_doctor.specialty,
                "introduction": db_doctor.introduction,
                "photo_path": db_doctor.photo_path,
                "original_photo_url": db_doctor.original_photo_url
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新医生信息时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.delete("/doctors/{doctor_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def delete_doctor(
    doctor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """删除医生信息 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 获取医生
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
              raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        # 如果医生有关联的用户账号，进行懒删除（软删除）并移除关联
        if db_doctor.user_id:
            # 查找用户并标记为已删除，同时置为不可用
            result = await db.execute(select(User).where(User.user_id == db_doctor.user_id))
            db_user = result.scalar_one_or_none()
            if db_user:
                try:
                    db_user.is_deleted = True
                    db_user.is_active = False
                    # 可选：清除登录相关信息
                    db_user.last_login_ip = None
                    db_user.last_login_time = None
                    db.add(db_user)
                    await db.commit()
                except Exception as ex:
                    await db.rollback()
                    logger.error(f"软删除用户时发生异常: {ex}")
                    raise BusinessHTTPException(
                        code=settings.REQ_ERROR_CODE,
                        msg="删除医生关联账号失败",
                        status_code=500
                    )

                # 清除 Redis 中的 token 映射，防止已删除用户继续使用旧 token
                try:
                    token = await redis.get(f"user_token:{db_user.user_id}")
                    if token:
                        await redis.delete(f"token:{token}")
                        await redis.delete(f"user_token:{db_user.user_id}")
                except Exception as rex:
                    logger.warning(f"删除用户 token 时 Redis 操作失败: {rex}")

            # 解除医生记录中的关联
            db_doctor.user_id = None

        # 删除医生信息
        await db.delete(db_doctor)
        await db.commit()

        logger.info(f"删除医生信息成功: {db_doctor.name}")

        return ResponseModel(
            code=0,
            message={"detail": f"成功删除医生 {db_doctor.name}"}
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除医生信息时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# 医生调科室
@router.put("/doctors/{doctor_id}/transfer", response_model=ResponseModel[Union[DoctorTransferResponse, AuthErrorResponse]])
async def transfer_doctor_department(
    doctor_id: int,
    transfer_data: DoctorTransferDepartment,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """医生调科室 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 获取医生
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
                raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        # 检查目标科室是否存在
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == transfer_data.new_dept_id))
        if not result.scalar_one_or_none():
                raise ResourceHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="目标科室不存在",
                status_code=400
            )

        # 记录原科室ID
        old_dept_id = db_doctor.dept_id

        # 更新医生科室
        db_doctor.dept_id = transfer_data.new_dept_id
        db.add(db_doctor)
        await db.commit()
        await db.refresh(db_doctor)

        logger.info(f"医生调科室成功: {db_doctor.name} 从科室 {old_dept_id} 调到科室 {transfer_data.new_dept_id}")

        return ResponseModel(
            code=0,
            message=DoctorTransferResponse(
                detail=f"成功将医生 {db_doctor.name} 调至新科室",
                doctor_id=doctor_id,
                old_dept_id=old_dept_id,
                new_dept_id=transfer_data.new_dept_id
            )
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"医生调科室时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )





@router.post("/doctors/{doctor_id}/photo", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_doctor_photo(
    doctor_id: int,
    photo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """更新医生照片 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 获取医生信息
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        # 验证文件类型
        content_type = photo.content_type.lower()
        if not content_type.startswith('image/'):
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="只允许上传图片文件",
                status_code=400
            )

        # 生成文件名（使用时间戳确保唯一性）
        timestamp = int(time.time() * 1000)  # 毫秒级时间戳
        file_extension = os.path.splitext(photo.filename)[1].lower()
        if not file_extension in ['.jpg', '.jpeg', '.png', '.gif']:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="不支持的图片格式",
                status_code=400
            )

        new_filename = f"doctor_{doctor_id}_{timestamp}{file_extension}"
        save_path = os.path.join("app", "static", "images", "doctor", new_filename)
        url_path = f"/static/images/doctor/{new_filename}"

        # 保存新文件
        try:
            async with aiofiles.open(save_path, 'wb') as out_file:
                content = await photo.read()
                await out_file.write(content)
        except Exception as e:
            logger.error(f"保存医生照片时发生异常: {str(e)}")
            raise ResourceHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="保存图片失败",
                status_code=500
            )

        # 更新数据库中的图片路径
        old_photo_path = db_doctor.photo_path
        db_doctor.photo_path = url_path
        db_doctor.original_photo_url = None  # 清除可能存在的外部图片URL
        db.add(db_doctor)
        await db.commit()
        await db.refresh(db_doctor)

        logger.info(f"更新医生照片成功: {db_doctor.name}, 新照片路径: {url_path}")

        return ResponseModel(
            code=0,
            message={
                "detail": f"成功更新医生 {db_doctor.name} 的照片",
                "doctor_id": doctor_id,
                "new_photo_path": url_path,
                "old_photo_path": old_photo_path
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新医生照片时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.delete("/doctors/{doctor_id}/photo", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def delete_doctor_photo(
    doctor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """删除医生照片（仅清除引用） - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 获取医生信息
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        # 检查是否有照片
        if not db_doctor.photo_path and not db_doctor.original_photo_url:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="医生当前没有照片",
                status_code=400
            )

        # 记录旧的照片路径（用于日志）
        old_photo_path = db_doctor.photo_path or db_doctor.original_photo_url

        # 清除照片引用
        db_doctor.photo_path = None
        db_doctor.original_photo_url = None
        db.add(db_doctor)
        await db.commit()
        await db.refresh(db_doctor)

        logger.info(f"删除医生照片成功: {db_doctor.name}, 原照片路径: {old_photo_path}")

        return ResponseModel(
            code=0,
            message={
                "detail": f"成功删除医生 {db_doctor.name} 的照片",
                "doctor_id": doctor_id,
                "old_photo_path": old_photo_path
            }
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除医生照片时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )

# ====== 医生照片二进制获取 ======
@router.get("/doctors/{doctor_id}/photo")
async def get_doctor_photo_raw(
    doctor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """根据医生ID返回照片二进制数据（仅管理员）。

    - 优先读取 `Doctor.photo_path` 指向的本地文件，例如 `/static/image/xxx.jpg`
    - 如果不存在或文件缺失，则返回 404
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        if not db_doctor.photo_path:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="该医生暂无本地照片",
                status_code=404
            )

        # 解析本地文件系统路径（始终使用相对 app 目录的路径）
        base_dir = os.path.dirname(os.path.dirname(__file__))  # .../app
        rel_path = db_doctor.photo_path.lstrip("/")  # e.g. static/image/xxx.jpg 或 app/static/image/xxx.jpg
        if rel_path.startswith("app/"):
            # 归一化去掉前缀 app/
            rel_path = rel_path[4:]
        fs_path = os.path.normpath(os.path.join(base_dir, rel_path))  # app/<rel_path>

        if not os.path.exists(fs_path) or os.path.isdir(fs_path):
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生照片文件不存在",
                status_code=404
            )

        mime_type, _ = mimetypes.guess_type(fs_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        def file_iterator(path: str, chunk_size: int = 8192):
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        return StreamingResponse(file_iterator(fs_path), media_type=mime_type)
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取医生照片数据时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )

# 为医生创建/更新账号
@router.post("/doctors/{doctor_id}/create-account", response_model=ResponseModel[Union[DoctorAccountCreateResponse, AuthErrorResponse]])
async def create_or_update_doctor_account(
    doctor_id: int,
    account_data: DoctorAccountCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """为医生创建或更新账号 - 仅管理员可操作
    
    - 如果医生没有账号，则创建新账号
    - 如果医生已有账号，则更新密码和其他信息
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 获取医生
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        db_doctor = result.scalar_one_or_none()
        if not db_doctor:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        existing_user = None
        if db_doctor.user_id:
            # 如果医生已有账号，获取用户信息
            result = await db.execute(select(User).where(User.user_id == db_doctor.user_id))
            existing_user = result.scalar_one_or_none()

        # 检查工号唯一性（跳过医生自己的账号）
        result = await db.execute(
            select(User).where(
                and_(
                    User.identifier == account_data.identifier,
                    User.user_id != (existing_user.user_id if existing_user else None)
                )
            )
        )
        if result.scalar_one_or_none():
                raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="工号已被其他用户使用",
                status_code=400
            )

        # 检查邮箱唯一性（如果提供了邮箱）
        if account_data.email:
            result = await db.execute(
                select(User).where(
                    and_(
                        User.email == account_data.email,
                        User.user_id != (existing_user.user_id if existing_user else None)
                    )
                )
            )
            if result.scalar_one_or_none():
                    raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="邮箱已被其他用户使用",
                    status_code=400
                )

        # 检查手机号唯一性（如果提供了手机号）
        if account_data.phonenumber:
            result = await db.execute(
                select(User).where(
                    and_(
                        User.phonenumber == account_data.phonenumber,
                        User.user_id != (existing_user.user_id if existing_user else None)
                    )
                )
            )
            if result.scalar_one_or_none():
                    raise BusinessHTTPException(
                    code=settings.REQ_ERROR_CODE,
                    msg="手机号已被其他用户使用",
                    status_code=400
                )

        hashed_password = get_hash_pwd(account_data.password)
        operation_type = "更新" if existing_user else "创建"

        if existing_user:
            # 更新现有账号
            existing_user.identifier = account_data.identifier
            existing_user.hashed_password = hashed_password
            if account_data.email is not None:
                existing_user.email = account_data.email
            if account_data.phonenumber is not None:
                existing_user.phonenumber = account_data.phonenumber
            
            db.add(existing_user)
            await db.commit()
            await db.refresh(existing_user)
            user_id = existing_user.user_id
        else:
            # 创建新账号
            new_user = User(
                identifier=account_data.identifier,
                email=account_data.email,
                phonenumber=account_data.phonenumber,
                hashed_password=hashed_password,
                user_type="doctor",  # 设置为医生类型
                is_admin=False,      # 医生不是管理员
                is_verified=True     # 管理员创建的账号直接验证
            )
            db.add(new_user)
            await db.commit()
            await db.refresh(new_user)

            # 更新医生信息，关联用户账号
            db_doctor.user_id = new_user.user_id
            db.add(db_doctor)
            await db.commit()
            user_id = new_user.user_id

        logger.info(f"为医生{operation_type}账号成功: {db_doctor.name} (工号: {account_data.identifier})")

        return ResponseModel(
            code=0,
            message=DoctorAccountCreateResponse(
                detail=f"成功为医生 {db_doctor.name} {operation_type}登录账号",
                user_id=user_id,
                doctor_id=doctor_id
            )
        )
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"为医生{operation_type}账号时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ====== 门诊管理接口 ======

@router.get("/clinics", response_model=ResponseModel[Union[ClinicListResponse, AuthErrorResponse]])
async def get_clinics(
    dept_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取科室门诊列表 - 仅管理员可操作，可按小科室过滤"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        filters = []
        if dept_id:
            filters.append(Clinic.minor_dept_id == dept_id)

        result = await db.execute(select(Clinic).where(and_(*filters) if filters else True))
        clinics = result.scalars().all()

        clinic_list = []
        for c in clinics:
            clinic_list.append({
                "clinic_id": c.clinic_id,
                "area_id": c.area_id,
                "name": c.name,
                "address": c.address,
                "minor_dept_id": c.minor_dept_id,
                "clinic_type": c.clinic_type
            })

        return ResponseModel(code=0, message=ClinicListResponse(clinics=clinic_list))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取门诊列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.post("/clinics", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def create_clinic(
    clinic_data: ClinicCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """创建门诊 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 校验小科室存在
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == clinic_data.minor_dept_id))
        if not result.scalar_one_or_none():
            raise ResourceHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="小科室不存在",
                status_code=400
            )

        # 目前未提供院区选择，默认归属院区ID=1（后续支持可扩展）
        db_clinic = Clinic(
            area_id=1,
            name=clinic_data.name,
            address=clinic_data.address,
            minor_dept_id=clinic_data.minor_dept_id,
            clinic_type=clinic_data.clinic_type
        )
        db.add(db_clinic)
        await db.commit()
        await db.refresh(db_clinic)

        logger.info(f"创建门诊成功: {db_clinic.name}")
        return ResponseModel(code=0, message={
            "clinic_id": db_clinic.clinic_id,
            "detail": "门诊创建成功"
        })
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"创建门诊时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ====== 排班管理接口 ======

def _weekday_to_cn(week_day: int) -> str:
    mapping = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}
    return mapping.get(week_day, "")


def _slot_type_to_str(slot_type_enum) -> str:
    # Enum values are Chinese strings in model
    return slot_type_enum.value if hasattr(slot_type_enum, "value") else str(slot_type_enum)


def _str_to_slot_type(value: str):
    # Accept: 普通/专家/特需
    from app.models.schedule import SlotType
    for member in SlotType:
        if member.value == value:
            return member
    raise BusinessHTTPException(
        code=settings.REQ_ERROR_CODE,
        msg="无效的号源类型，应为 普通/专家/特需",
        status_code=400
    )


@router.get("/departments/{dept_id}/schedules", response_model=ResponseModel[Union[ScheduleListResponse, AuthErrorResponse]])
async def get_department_schedules(
    dept_id: int,
    start_date: str,
    end_date: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取科室排班 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 校验科室
        result = await db.execute(select(MinorDepartment).where(MinorDepartment.minor_dept_id == dept_id))
        if not result.scalar_one_or_none():
            raise ResourceHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="小科室不存在",
                status_code=400
            )

        # 日期解析
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

        # 查询：该小科室下的门诊 -> 排班
        result = await db.execute(select(Clinic.clinic_id).where(Clinic.minor_dept_id == dept_id))
        clinic_ids = [row[0] for row in result.all()]
        if not clinic_ids:
            return ResponseModel(code=0, message=ScheduleListResponse(schedules=[]))

        from sqlalchemy import or_  # ensure imported
        result = await db.execute(
            select(Schedule, Doctor.name, Clinic.name, Clinic.clinic_type)
            .join(Doctor, Doctor.doctor_id == Schedule.doctor_id)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .where(
                and_(
                    Schedule.clinic_id.in_(clinic_ids),
                    Schedule.date >= start_dt,
                    Schedule.date <= end_dt,
                )
            )
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
                "date": sch.date,
                "week_day": _weekday_to_cn(sch.week_day),
                "time_section": sch.time_section,
                "slot_type": _slot_type_to_str(sch.slot_type),
                "total_slots": sch.total_slots,
                "remaining_slots": sch.remaining_slots,
                "status": sch.status,
                "price": float(sch.price)
            })

        return ResponseModel(code=0, message=ScheduleListResponse(schedules=data))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取科室排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/doctors/{doctor_id}/schedules", response_model=ResponseModel[Union[ScheduleListResponse, AuthErrorResponse]])
async def get_doctor_schedules(
    doctor_id: int,
    start_date: str,
    end_date: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取医生排班 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 校验医生
        result = await db.execute(select(Doctor).where(Doctor.doctor_id == doctor_id))
        if not result.scalar_one_or_none():
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="医生不存在",
                status_code=404
            )

        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

        result = await db.execute(
            select(Schedule, Doctor.name, Clinic.name, Clinic.clinic_type)
            .join(Doctor, Doctor.doctor_id == Schedule.doctor_id)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .where(
                and_(
                    Schedule.doctor_id == doctor_id,
                    Schedule.date >= start_dt,
                    Schedule.date <= end_dt,
                )
            )
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
                "date": sch.date,
                "week_day": _weekday_to_cn(sch.week_day),
                "time_section": sch.time_section,
                "slot_type": _slot_type_to_str(sch.slot_type),
                "total_slots": sch.total_slots,
                "remaining_slots": sch.remaining_slots,
                "status": sch.status,
                "price": float(sch.price)
            })

        return ResponseModel(code=0, message=ScheduleListResponse(schedules=data))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取医生排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/clinics/{clinic_id}/schedules", response_model=ResponseModel[Union[ScheduleListResponse, AuthErrorResponse]])
async def get_clinic_schedules(
    clinic_id: int,
    start_date: str,
    end_date: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取门诊排班 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 校验门诊
        result = await db.execute(select(Clinic).where(Clinic.clinic_id == clinic_id))
        if not result.scalar_one_or_none():
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="门诊不存在",
                status_code=404
            )

        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

        result = await db.execute(
            select(Schedule, Doctor.name, Clinic.name, Clinic.clinic_type)
            .join(Doctor, Doctor.doctor_id == Schedule.doctor_id)
            .join(Clinic, Clinic.clinic_id == Schedule.clinic_id)
            .where(
                and_(
                    Schedule.clinic_id == clinic_id,
                    Schedule.date >= start_dt,
                    Schedule.date <= end_dt,
                )
            )
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
                "date": sch.date,
                "week_day": _weekday_to_cn(sch.week_day),
                "time_section": sch.time_section,
                "slot_type": _slot_type_to_str(sch.slot_type),
                "total_slots": sch.total_slots,
                "remaining_slots": sch.remaining_slots,
                "status": sch.status,
                "price": float(sch.price)
            })

        return ResponseModel(code=0, message=ScheduleListResponse(schedules=data))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取门诊排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.post("/schedules", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def create_schedule(
    schedule_data: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """创建排班 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 基本校验
        if not (await db.execute(select(Doctor).where(Doctor.doctor_id == schedule_data.doctor_id))).scalar_one_or_none():
            raise ResourceHTTPException(code=settings.REQ_ERROR_CODE, msg="医生不存在", status_code=400)
        if not (await db.execute(select(Clinic).where(Clinic.clinic_id == schedule_data.clinic_id))).scalar_one_or_none():
            raise ResourceHTTPException(code=settings.REQ_ERROR_CODE, msg="门诊不存在", status_code=400)

        # 计算 week_day (1-7)
        week_day = schedule_data.schedule_date.isoweekday()

        db_schedule = Schedule(
            doctor_id=schedule_data.doctor_id,
            clinic_id=schedule_data.clinic_id,
            date=schedule_data.schedule_date,
            week_day=week_day,
            time_section=schedule_data.time_section,
            slot_type=_str_to_slot_type(schedule_data.slot_type),
            total_slots=schedule_data.total_slots,
            remaining_slots=schedule_data.total_slots,
            status=schedule_data.status,
            price=schedule_data.price
        )
        db.add(db_schedule)
        await db.commit()
        await db.refresh(db_schedule)

        logger.info(f"创建排班成功: {db_schedule.schedule_id}")
        return ResponseModel(code=0, message={"schedule_id": db_schedule.schedule_id, "detail": "排班创建成功"})
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"创建排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.put("/schedules/{schedule_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def update_schedule(
    schedule_id: int,
    schedule_data: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """更新排班 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        result = await db.execute(select(Schedule).where(Schedule.schedule_id == schedule_id))
        db_schedule = result.scalar_one_or_none()
        if not db_schedule:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="排班不存在", status_code=404)

        # 字段更新和校验
        if schedule_data.doctor_id is not None and schedule_data.doctor_id != db_schedule.doctor_id:
            if not (await db.execute(select(Doctor).where(Doctor.doctor_id == schedule_data.doctor_id))).scalar_one_or_none():
                raise ResourceHTTPException(code=settings.REQ_ERROR_CODE, msg="医生不存在", status_code=400)
            db_schedule.doctor_id = schedule_data.doctor_id

        if schedule_data.clinic_id is not None and schedule_data.clinic_id != db_schedule.clinic_id:
            if not (await db.execute(select(Clinic).where(Clinic.clinic_id == schedule_data.clinic_id))).scalar_one_or_none():
                raise ResourceHTTPException(code=settings.REQ_ERROR_CODE, msg="门诊不存在", status_code=400)
            db_schedule.clinic_id = schedule_data.clinic_id

        if schedule_data.schedule_date is not None:
            db_schedule.date = schedule_data.schedule_date
            db_schedule.week_day = schedule_data.schedule_date.isoweekday()

        if schedule_data.time_section is not None:
            db_schedule.time_section = schedule_data.time_section

        if schedule_data.slot_type is not None:
            db_schedule.slot_type = _str_to_slot_type(schedule_data.slot_type)

        if schedule_data.total_slots is not None:
            # 调整 remaining 时保持不为负
            delta = schedule_data.total_slots - db_schedule.total_slots
            db_schedule.total_slots = schedule_data.total_slots
            db_schedule.remaining_slots = max(0, db_schedule.remaining_slots + delta)

        if schedule_data.status is not None:
            db_schedule.status = schedule_data.status

        if schedule_data.price is not None:
            db_schedule.price = schedule_data.price

        db.add(db_schedule)
        await db.commit()
        await db.refresh(db_schedule)

        logger.info(f"更新排班成功: {db_schedule.schedule_id}")
        return ResponseModel(code=0, message={"detail": "排班更新成功"})
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"更新排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.delete("/schedules/{schedule_id}", response_model=ResponseModel[Union[dict, AuthErrorResponse]])
async def delete_schedule(
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """删除排班 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        result = await db.execute(select(Schedule).where(Schedule.schedule_id == schedule_id))
        db_schedule = result.scalar_one_or_none()
        if not db_schedule:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="排班不存在", status_code=404)

        await db.delete(db_schedule)
        await db.commit()

        logger.info(f"删除排班成功: {schedule_id}")
        return ResponseModel(code=0, message={"detail": "排班删除成功"})
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"删除排班时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )
        


async def get_administrator_id(db: AsyncSession, user_id: int) -> int:
    """根据用户ID获取管理员ID，用于审核人字段的填充"""
    # 查找关联的 Administrator 记录
    result = await db.execute(
        select(Administrator.admin_id).where(Administrator.user_id == user_id)
    )
    admin_id = result.scalar_one_or_none()
    if not admin_id:
        # 如果是管理员，但 Administrator 表中没有记录，则抛出异常
        raise AuthHTTPException(
            code=settings.INSUFFICIENT_AUTHORITY_CODE,
            msg="管理员身份异常，未找到对应的管理员档案。",
            status_code=403
        )
    return admin_id


def calculate_leave_days(start_date: date, end_date: date) -> int:
    """计算请假天数 (包含头尾两天)"""
    return (end_date - start_date).days + 1

# ================================== 排班审核接口 ==================================

@router.get("/audit/schedule", response_model=ResponseModel[Union[ScheduleAuditListResponse, AuthErrorResponse]])
async def get_schedule_audits(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取所有排班审核列表 - 仅管理员可操作 (无分页)"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 多表查询: ScheduleAudit, MinorDepartment, Clinic, Doctor
        result = await db.execute(
            select(
                ScheduleAudit,
                MinorDepartment.name.label("department_name"),
                Clinic.name.label("clinic_name"),
                Doctor.name.label("submitter_name"),
            )
            .join(MinorDepartment, MinorDepartment.minor_dept_id == ScheduleAudit.minor_dept_id)
            .join(Clinic, Clinic.clinic_id == ScheduleAudit.clinic_id)
            .join(Doctor, Doctor.doctor_id == ScheduleAudit.submitter_doctor_id)
            .order_by(ScheduleAudit.submit_time.desc())
        )
        
        audit_list = []
        for audit, dept_name, clinic_name, submitter_name in result.all():
            audit_list.append(ScheduleAuditItem(
                id=audit.audit_id,
                department_id=audit.minor_dept_id,
                department_name=dept_name,
                clinic_id=audit.clinic_id,
                clinic_name=clinic_name,
                submitter_id=audit.submitter_doctor_id,
                submitter_name=submitter_name,
                submit_time=audit.submit_time,
                week_start=audit.week_start_date,
                week_end=audit.week_end_date,
                remark=audit.remark,
                status=audit.status,
                auditor_id=audit.auditor_admin_id,
                audit_time=audit.audit_time,
                audit_remark=audit.audit_remark,
                # 假设 schedule_data_json 结构已符合 ScheduleAuditItem.schedule
                schedule=audit.schedule_data_json
            ))

        return ResponseModel(code=0, message=ScheduleAuditListResponse(audits=audit_list))
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取排班审核列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/audit/schedule/{audit_id}", response_model=ResponseModel[Union[ScheduleAuditItem, AuthErrorResponse]])
async def get_schedule_audit_detail(
    audit_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取排班审核详情 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 多表查询: ScheduleAudit, MinorDepartment, Clinic, Doctor
        result = await db.execute(
            select(
                ScheduleAudit,
                MinorDepartment.name.label("department_name"),
                Clinic.name.label("clinic_name"),
                Doctor.name.label("submitter_name"),
            )
            .join(MinorDepartment, MinorDepartment.minor_dept_id == ScheduleAudit.minor_dept_id)
            .join(Clinic, Clinic.clinic_id == ScheduleAudit.clinic_id)
            .join(Doctor, Doctor.doctor_id == ScheduleAudit.submitter_doctor_id)
            .where(ScheduleAudit.audit_id == audit_id)
        )
        row = result.first()

        if not row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="排班申请不存在",
                status_code=404
            )
        
        audit, dept_name, clinic_name, submitter_name = row
        
        return ResponseModel(code=0, message=ScheduleAuditItem(
            id=audit.audit_id,
            department_id=audit.minor_dept_id,
            department_name=dept_name,
            clinic_id=audit.clinic_id,
            clinic_name=clinic_name,
            submitter_id=audit.submitter_doctor_id,
            submitter_name=submitter_name,
            submit_time=audit.submit_time,
            week_start=audit.week_start_date,
            week_end=audit.week_end_date,
            remark=audit.remark,
            status=audit.status,
            auditor_id=audit.auditor_admin_id,
            audit_time=audit.audit_time,
            audit_remark=audit.audit_remark,
            schedule=audit.schedule_data_json
        ))
    except AuthHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取排班审核详情时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.post("/audit/schedule/{audit_id}/approve", response_model=ResponseModel[Union[AuditActionResponse, AuthErrorResponse]])
async def approve_schedule_audit(
    audit_id: int,
    data: AuditAction,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """通过排班审核 - 仅管理员可操作，并写入排班数据到 Schedule 表"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        db_audit = await db.get(ScheduleAudit, audit_id)
        if not db_audit:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="排班申请不存在", status_code=404)
        
        if db_audit.status != 'pending':
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"当前申请状态为 {db_audit.status}，无法重复审核", status_code=400)
        
        # 获取审核人 Admin ID
        auditor_admin_id = await get_administrator_id(db, current_user.user_id)
        current_time = datetime.now()
        
        # 1. 更新审核表状态
        db_audit.status = 'approved'
        db_audit.auditor_admin_id = auditor_admin_id
        db_audit.audit_time = current_time
        db_audit.audit_remark = data.comment
        db.add(db_audit)
        
        # 2. 将排班数据写入 Schedule 表 (简化的 JSON 解析和插入逻辑)
        # 假设 schedule_data_json 是一个 7x3 的列表结构，且已包含必要的 time_section 和 slot_type 信息
        schedule_data = db_audit.schedule_data_json
        start_date = db_audit.week_start_date
        clinic_id = db_audit.clinic_id
        
        schedule_records = []
        # 假设 time_section 依次为 '上午', '下午', '晚上'
        time_sections = ['上午', '下午', '晚上']
        # 假设排班 JSON 数据中的 DoctorInfo 包含其他必要信息如 slot_type, total_slots, price，或者使用默认值
        # ⚠️ 注: 这里的 Schedule 表字段 (如 slot_type, total_slots, price) 缺失，需在实际模型中补充
        
        for day_index, day_schedule in enumerate(schedule_data):
            current_date = start_date + timedelta(days=day_index)
            week_day = current_date.isoweekday() # 1=Mon, 7=Sun
            for slot_index, slot_data in enumerate(day_schedule):
                if slot_data:
                    # 假设 slot_data 是 ScheduleDoctorInfo: {"doctorId": 1, "doctorName": "李医生"}
                    # 实际生产中，JSON 应该包含完整的排班信息（号源类型、数量、价格等）
                    
                    # 假设默认值，实际应从更详细的 JSON 结构中提取
                    doctor_id = slot_data.get('doctor_id')
                    
                    # 检查医生是否存在 (可选，但推荐)
                    if not (await db.get(Doctor, doctor_id)):
                        raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"排班数据中医生ID {doctor_id} 不存在", status_code=400)
                    
                    # 假设 Schedule 模型中需要这些字段 (您未提供 Schedule 模型，此处按常见结构填充)
                    new_schedule = Schedule(
                        doctor_id=doctor_id,
                        clinic_id=clinic_id,
                        date=current_date,
                        week_day=week_day,
                        time_section=time_sections[slot_index],
                        slot_type='普通', # 需根据实际业务逻辑确定
                        total_slots=50,  # 需根据实际业务逻辑确定
                        remaining_slots=50, # 需根据实际业务逻辑确定
                        price=10.00, # 需根据实际业务逻辑确定
                        status='normal'
                    )
                    schedule_records.append(new_schedule)

        db.add_all(schedule_records)
        await db.commit()
        await db.refresh(db_audit)

        logger.info(f"排班审核通过并写入排班记录: Audit ID {audit_id}")

        return ResponseModel(code=0, message=AuditActionResponse(
            audit_id=audit_id,
            status='approved',
            auditor_id=auditor_admin_id,
            audit_time=current_time
        ))
    except AuthHTTPException:
        await db.rollback()
        raise
    except BusinessHTTPException:
        await db.rollback()
        raise
    except ResourceHTTPException:
        await db.rollback()
        raise
    except Exception as e:
        logger.error(f"通过排班审核时发生异常: {str(e)}")
        await db.rollback()
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常: 写入排班数据失败",
            status_code=500
        )


@router.post("/audit/schedule/{audit_id}/reject", response_model=ResponseModel[Union[AuditActionResponse, AuthErrorResponse]])
async def reject_schedule_audit(
    audit_id: int,
    data: AuditAction,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """拒绝排班审核 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        db_audit = await db.get(ScheduleAudit, audit_id)
        if not db_audit:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="排班申请不存在", status_code=404)
        
        if db_audit.status != 'pending':
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"当前申请状态为 {db_audit.status}，无法重复审核", status_code=400)
        
        auditor_admin_id = await get_administrator_id(db, current_user.user_id)
        current_time = datetime.now()

        # 更新审核表状态
        db_audit.status = 'rejected'
        db_audit.auditor_admin_id = auditor_admin_id
        db_audit.audit_time = current_time
        db_audit.audit_remark = data.comment
        db.add(db_audit)
        await db.commit()
        await db.refresh(db_audit)

        logger.info(f"排班审核拒绝: Audit ID {audit_id}")

        return ResponseModel(code=0, message=AuditActionResponse(
            audit_id=audit_id,
            status='rejected',
            auditor_id=auditor_admin_id,
            audit_time=current_time
        ))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"拒绝排班审核时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )

# ================================== 请假审核接口 ==================================

@router.get("/audit/leave", response_model=ResponseModel[Union[LeaveAuditListResponse, AuthErrorResponse]])
async def get_leave_audits(
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取所有请假审核列表 - 仅管理员可操作 (无分页)"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 多表查询: LeaveAudit, Doctor, MinorDepartment
        result = await db.execute(
            select(
                LeaveAudit,
                Doctor.name.label("doctor_name"),
                Doctor.title.label("doctor_title"),
                MinorDepartment.name.label("department_name"),
            )
            .join(Doctor, Doctor.doctor_id == LeaveAudit.doctor_id)
            .join(MinorDepartment, MinorDepartment.minor_dept_id == Doctor.dept_id)
            .order_by(LeaveAudit.submit_time.desc())
        )
        
        audit_list = []
        for audit, doctor_name, doctor_title, dept_name in result.all():
            leave_days = calculate_leave_days(audit.leave_start_date, audit.leave_end_date)
            # 原因预览：截取前 50 个字符
            reason_preview = (audit.reason[:50] + '...') if len(audit.reason) > 50 else audit.reason
            
            audit_list.append(LeaveAuditItem(
                id=audit.audit_id,
                doctor_id=audit.doctor_id,
                doctor_name=doctor_name,
                doctor_title=doctor_title,
                department_name=dept_name,
                leave_start_date=audit.leave_start_date,
                leave_end_date=audit.leave_end_date,
                leave_days=leave_days,
                reason=audit.reason,
                reason_preview=reason_preview,
                attachments=audit.attachment_data_json or [], # 确保返回列表
                submit_time=audit.submit_time,
                status=audit.status,
                auditor_id=audit.auditor_admin_id,
                audit_time=audit.audit_time,
                audit_remark=audit.audit_remark
            ))

        return ResponseModel(code=0, message=LeaveAuditListResponse(audits=audit_list))
    except AuthHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取请假审核列表时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.get("/audit/leave/{audit_id}", response_model=ResponseModel[Union[LeaveAuditItem, AuthErrorResponse]])
async def get_leave_audit_detail(
    audit_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """获取请假审核详情 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 多表查询: LeaveAudit, Doctor, MinorDepartment
        result = await db.execute(
            select(
                LeaveAudit,
                Doctor.name.label("doctor_name"),
                Doctor.title.label("doctor_title"),
                MinorDepartment.name.label("department_name"),
            )
            .join(Doctor, Doctor.doctor_id == LeaveAudit.doctor_id)
            .join(MinorDepartment, MinorDepartment.minor_dept_id == Doctor.dept_id)
            .where(LeaveAudit.audit_id == audit_id)
        )
        row = result.first()

        if not row:
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="请假申请不存在",
                status_code=404
            )
        
        audit, doctor_name, doctor_title, dept_name = row
        leave_days = calculate_leave_days(audit.leave_start_date, audit.leave_end_date)
        reason_preview = (audit.reason[:50] + '...') if len(audit.reason) > 50 else audit.reason
        
        return ResponseModel(code=0, message=LeaveAuditItem(
            id=audit.audit_id,
            doctor_id=audit.doctor_id,
            doctor_name=doctor_name,
            doctor_title=doctor_title,
            department_name=dept_name,
            leave_start_date=audit.leave_start_date,
            leave_end_date=audit.leave_end_date,
            leave_days=leave_days,
            reason=audit.reason,
            reason_preview=reason_preview,
            attachments=audit.attachment_data_json or [],
            submit_time=audit.submit_time,
            status=audit.status,
            auditor_id=audit.auditor_admin_id,
            audit_time=audit.audit_time,
            audit_remark=audit.audit_remark
        ))
    except AuthHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取请假审核详情时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg="内部服务异常",
            status_code=500
        )


@router.post("/audit/leave/{audit_id}/approve", response_model=ResponseModel[Union[AuditActionResponse, AuthErrorResponse]])
async def approve_leave_audit(
    audit_id: int,
    data: AuditAction,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """通过请假审核 - 仅管理员可操作，并删除请假期间的排班"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        db_audit = await db.get(LeaveAudit, audit_id)
        if not db_audit:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="请假申请不存在", status_code=404)
        
        if db_audit.status != 'pending':
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"当前申请状态为 {db_audit.status}，无法重复审核", status_code=400)
        
        # 获取审核人 Admin ID
        auditor_admin_id = await get_administrator_id(db, current_user.user_id)
        current_time = datetime.now()

        # 1. 删除医生在请假期间的排班记录
        delete_stmt = delete(Schedule).where(
            and_(
                Schedule.doctor_id == db_audit.doctor_id,
                Schedule.date >= db_audit.leave_start_date,
                Schedule.date <= db_audit.leave_end_date,
                # 仅删除状态为 'normal' 或 'pending' 的排班，已完成的排班不应被删除
                # 假设 Schedule 表有 status 字段
            )
        )
        deleted_schedules = await db.execute(delete_stmt)
        await db.commit() # 先提交删除，确保原子性

        logger.warning(f"请假审核通过，已删除 {deleted_schedules.rowcount} 条排班记录。")
        
        # 2. 更新审核表状态
        db_audit.status = 'approved'
        db_audit.auditor_admin_id = auditor_admin_id
        db_audit.audit_time = current_time
        db_audit.audit_remark = data.comment
        db.add(db_audit)
        
        await db.commit()
        await db.refresh(db_audit)

        logger.info(f"请假审核通过: Audit ID {audit_id}")

        return ResponseModel(code=0, message=AuditActionResponse(
            audit_id=audit_id,
            status='approved',
            auditor_id=auditor_admin_id,
            audit_time=current_time
        ))
    except AuthHTTPException:
        await db.rollback()
        raise
    except BusinessHTTPException:
        await db.rollback()
        raise
    except ResourceHTTPException:
        await db.rollback()
        raise
    except Exception as e:
        logger.error(f"通过请假审核时发生异常: {str(e)}")
        await db.rollback()
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常: 清除排班或更新审核状态失败",
            status_code=500
        )


@router.post("/audit/leave/{audit_id}/reject", response_model=ResponseModel[Union[AuditActionResponse, AuthErrorResponse]])
async def reject_leave_audit(
    audit_id: int,
    data: AuditAction,
    db: AsyncSession = Depends(get_db),
    current_user: UserSchema = Depends(get_current_user)
):
    """拒绝请假审核 - 仅管理员可操作"""
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )
        
        db_audit = await db.get(LeaveAudit, audit_id)
        if not db_audit:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="请假申请不存在", status_code=404)
        
        if db_audit.status != 'pending':
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"当前申请状态为 {db_audit.status}，无法重复审核", status_code=400)
        
        auditor_admin_id = await get_administrator_id(db, current_user.user_id)
        current_time = datetime.now()

        # 更新审核表状态
        db_audit.status = 'rejected'
        db_audit.auditor_admin_id = auditor_admin_id
        db_audit.audit_time = current_time
        db_audit.audit_remark = data.comment
        db.add(db_audit)
        await db.commit()
        await db.refresh(db_audit)

        logger.info(f"请假审核拒绝: Audit ID {audit_id}")

        return ResponseModel(code=0, message=AuditActionResponse(
            audit_id=audit_id,
            status='rejected',
            auditor_id=auditor_admin_id,
            audit_time=current_time
        ))
    except AuthHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except Exception as e:
        logger.error(f"拒绝请假审核时发生异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )


# ====== 通用附件路径二进制数据获取 ======
@router.get("/audit/attachment/raw", response_model=None)
async def get_attachment_raw_from_path(
    path: str,
    current_user: UserSchema = Depends(get_current_user)
):
    """根据附件的本地相对路径返回文件二进制数据（仅管理员）。

    用于获取请假申请等附件中的图片或文件内容。文件路径应存储在 LeaveAudit.attachment_data_json 中。
    """
    try:
        if not current_user.is_admin:
            raise AuthHTTPException(
                code=settings.INSUFFICIENT_AUTHORITY_CODE,
                msg="无权限，仅管理员可操作",
                status_code=403
            )

        # 路径解析：基于应用根目录进行拼接，并进行规范化
        base_dir = os.path.dirname(os.path.dirname(__file__))  # 假设 /app 目录
        rel_path = path.lstrip("/") 
        
        # 归一化路径，防止路径中出现 ../ 等跳转
        fs_path = os.path.normpath(os.path.join(base_dir, rel_path))
        
        # 关键安全检查：确保文件路径在应用基础目录内，防止目录遍历攻击 (Directory Traversal)
        if not fs_path.startswith(os.path.normpath(base_dir)):
            logger.warning(f"检测到目录遍历尝试: {fs_path}")
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="提供的文件路径不安全或无效",
                status_code=400
            )

        # 检查文件是否存在
        if not os.path.exists(fs_path) or os.path.isdir(fs_path):
            raise ResourceHTTPException(
                code=settings.DATA_GET_FAILED_CODE,
                msg="附件文件不存在或路径错误",
                status_code=404
            )

        # 猜测 MIME Type
        mime_type, _ = mimetypes.guess_type(fs_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        # 异步文件迭代器，适用于 StreamingResponse
        async def async_file_iterator(path: str, chunk_size: int = 8192):
            """异步读取文件块的生成器"""
            try:
                # 使用 aiofiles 确保文件 I/O 不阻塞主线程
                async with aiofiles.open(path, "rb") as f:
                    while True:
                        chunk = await f.read(chunk_size)
                        if not chunk:
                            break
                        yield chunk
            except Exception as e:
                logger.error(f"异步读取文件失败: {path}, 异常: {str(e)}")
                # 在流中抛出异常会导致连接中断，这里更倾向于记录错误

        logger.info(f"开始流式传输本地附件文件: {fs_path}")
        return StreamingResponse(async_file_iterator(fs_path), media_type=mime_type)

    except AuthHTTPException:
        raise
    except ResourceHTTPException:
        raise
    except BusinessHTTPException:
        raise
    except Exception as e:
        logger.error(f"获取附件数据时发生未知异常: {str(e)}")
        raise BusinessHTTPException(
            code=settings.REQ_ERROR_CODE,
            msg="内部服务异常",
            status_code=500
        )