# exception_handlers.py
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import traceback
import logging
import json
from app.schemas.response import ResponseModel, UnknownErrorResponse, HTTPErrorResponse, RequestValidationErrorResponse, AuthErrorResponse, StatisticsErrorResponse
from app.core.config import settings


logger = logging.getLogger(__name__)

class BusinessHTTPException(Exception):
    """业务逻辑相关异常, 例如数据验证、业务规则校验等"""
    def __init__(self, code: int, msg: str, status_code: int = 400):
        self.status_code = status_code
        self.detail = {"code": code, "msg": msg}
        super().__init__(msg)

class ResourceHTTPException(Exception):
    """资源相关异常, 例如资源不存在、资源已存在、资源状态错误等"""
    def __init__(self, code: int, msg: str, status_code: int = 400):
        self.status_code = status_code
        self.detail = {"code": code, "msg": msg}
        super().__init__(msg)

class AuthHTTPException(Exception):
    """专为认证相关接口设计的异常,例如权限不足、登录失败等"""
    def __init__(self, code: int, msg: str, status_code: int = 400):
        self.status_code = status_code
        self.detail = {"code": code, "msg": msg}
        super().__init__(msg)
        
class StatisticsHTTPException(Exception):
    """专为统计信息相关接口设计的异常, detail 必须为 dict, 包含 code 和 msg 字段"""
    def __init__(self, code: int, msg: str, status_code: int = 400):
        self.status_code = status_code
        self.detail = {"code": code, "msg": msg}
        super().__init__(msg)


class BusinessHTTPException(Exception):
    """通用业务逻辑异常，用于处理业务规则校验失败等情况"""
    def __init__(self, code: int, msg: str, status_code: int = 400):
        self.status_code = status_code
        self.detail = {"code": code, "msg": msg}
        super().__init__(msg)


def register_exception_handlers(app):
    """全局异常处理器

    Args:
        app (_type_): _description_

    Returns:
        _type_: _description_
    """
    
    #无法处理异常
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(f"Unhandled Exception: {traceback.format_exc()}")
        
        # 确保异常信息完全转换为字符串,避免 JSON 序列化错误
        error_detail = str(exc)
        try:
            # 尝试提取更多异常信息
            if hasattr(exc, 'detail'):
                error_detail = str(exc.detail)
        except Exception:
            pass
        
        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                code=settings.UNKNOWN_ERROR_CODE,
                message=UnknownErrorResponse(error="未知错误", detail=error_detail)
            ).dict(),
        )

    #HTTP异常
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        logger.warning(f"HTTPException: {exc.detail}")
        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                code=settings.HTTP_ERROR_CODE,
                message=HTTPErrorResponse(error="HTTP异常", detail=exc.detail)
            ).dict(),
        )

    #验证异常
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        logger.info(f"Validation Error: {errors}")

        # 确保所有错误信息都可以被 JSON 序列化
        serializable_errors = []
        for e in errors:
            try:
                # 深度复制错误对象并确保所有值都是可序列化的
                error_dict = {
                    "type": str(e.get("type", "unknown")),
                    "loc": list(e.get("loc", [])),
                    "msg": str(e.get("msg", "")),
                    "input": str(e.get("input", ""))[:100] if e.get("input") is not None else None,  # 限制长度
                }
                
                # 处理 ctx 字段(可能包含异常对象)
                ctx = e.get("ctx")
                if ctx:
                    if isinstance(ctx, dict):
                        error_dict["ctx"] = {k: str(v) for k, v in ctx.items()}
                    else:
                        error_dict["ctx"] = str(ctx)
                
                # 如果包含 json 解析错误，给出更友好的提示
                if e.get("type") == "json_invalid":
                    error_dict["msg"] = "请求体不是有效的 JSON，请检查是否使用了正确的双引号(\")并确保 JSON 格式正确"
                
                serializable_errors.append(error_dict)
            except Exception as serialize_err:
                # 如果序列化失败，至少返回基本信息
                logger.error(f"Error serializing validation error: {serialize_err}")
                serializable_errors.append({
                    "type": "serialization_error",
                    "msg": "验证错误信息序列化失败",
                    "detail": str(e)
                })

        # 将整个错误数组序列化为单个 JSON 字符串，作为 msg 返回，符合旧的错误格式
        try:
            msg_str = json.dumps(serializable_errors, ensure_ascii=False)
        except Exception:
            # 回退方案：尽量将错误信息变为可读字符串
            try:
                msg_str = str(serializable_errors)
            except Exception:
                msg_str = "验证错误"

        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                code=settings.REQ_ERROR_CODE,
                message={
                    "error": "请求参数验证失败",
                    "msg": msg_str
                }
            ).dict(),
        )

    #认证异常
    @app.exception_handler(AuthHTTPException)
    async def auth_http_exception_handler(request: Request, exc: AuthHTTPException):
        logger.warning(f"AuthHTTPException: {exc.detail}")
        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                code=exc.detail["code"],
                message=AuthErrorResponse(error="认证时出现异常", msg=exc.detail["msg"])
            ).dict(),
        )

    #统计信息异常
    @app.exception_handler(StatisticsHTTPException)
    async def statistics_http_exception_handler(request: Request, exc: StatisticsHTTPException):
        logger.warning(f"StatisticsHTTPException: {exc.detail}")
        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                code=exc.detail["code"],
                message=StatisticsErrorResponse(msg=exc.detail["msg"])
            ).dict(),
        )

    @app.exception_handler(BusinessHTTPException)
    async def business_http_exception_handler(request: Request, exc: BusinessHTTPException):
        logger.warning(f"BusinessHTTPException: {exc.detail}")
        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                code=exc.detail["code"],
                message={"error": "业务规则校验失败", "msg": exc.detail["msg"]}
            ).dict(),
        )

    @app.exception_handler(ResourceHTTPException)
    async def resource_http_exception_handler(request: Request, exc: ResourceHTTPException):
        logger.warning(f"ResourceHTTPException: {exc.detail}")
        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                code=exc.detail["code"],
                message={"error": "资源操作失败", "msg": exc.detail["msg"]}
            ).dict(),
        )
    