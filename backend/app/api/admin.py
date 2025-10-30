from fastapi import APIRouter, Depends,UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Optional, Union
import logging

from app.schemas.admin import MajorDepartmentCreate, MajorDepartmentUpdate, MinorDepartmentCreate, MinorDepartmentUpdate, DoctorCreate, DoctorUpdate, DoctorAccountCreate, DoctorTransferDepartment
from app.schemas.response import (
    ResponseModel, AuthErrorResponse, MajorDepartmentListResponse, MinorDepartmentListResponse, DoctorListResponse, DoctorAccountCreateResponse, DoctorTransferResponse
)
from app.db.base import get_db, redis, User, MajorDepartment, MinorDepartment, Doctor
from app.schemas.user import user as UserSchema
from app.core.config import settings
from app.core.exception_handler import AuthHTTPException, BusinessHTTPException, ResourceHTTPException
from app.api.auth import get_current_user
from app.core.security import get_hash_pwd
from datetime import datetime
import os
import aiofiles
import time


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
                "description": db_dept.description,
                "create_time": db_dept.create_time
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
                "description": dept.description,
                "create_time": dept.create_time
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
                "description": db_dept.description,
                "create_time": db_dept.create_time
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
                "description": db_dept.description,
                "create_time": db_dept.create_time
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
            "description": db_dept.description,
            "create_time": db_dept.create_time
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
                "description": dept.description,
                "create_time": dept.create_time
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
            "introduction": db_doctor.introduction,
            "create_time": db_doctor.create_time
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
                "original_photo_url": doctor.original_photo_url,
                "create_time": doctor.create_time
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
                "original_photo_url": db_doctor.original_photo_url,
                "create_time": db_doctor.create_time
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
        save_path = os.path.join("app", "static", "image", new_filename)
        url_path = f"/static/image/{new_filename}"

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
