# exception_handlers.py
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import traceback
import logging

from app.schemas.response import ResponseModel, UnknownErrorResponse, HTTPErrorResponse, RequestValidationErrorResponse, AuthErrorResponse, TrafficErrorResponse, StatisticsErrorResponse
from app.core.config import settings


logger = logging.getLogger(__name__)

class AuthHTTPException(Exception):
    """专为认证相关接口设计的异常,detail 必须为 dict,包含 code 和 msg 字段"""
    def __init__(self, code: int, msg: str, status_code: int = 400):
        self.status_code = status_code
        self.detail = {"code": code, "msg": msg}
        super().__init__(msg)

class TrafficHTTPException(Exception):
    """专为交通数据相关接口设计的异常,detail 必须为 dict,包含 code 和 msg 字段"""
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
        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                code=settings.UNKNOWN_ERROR_CODE,
                message=UnknownErrorResponse(error="未知错误", detail=str(exc))
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

        # 如果包含 json 解析错误，给出更友好的提示
        for e in errors:
            if e.get("type") == "json_invalid":
                # 用更易懂的提示替换原始错误信息
                ctx = e.get("ctx") or {}
                json_err = ctx.get("error") if isinstance(ctx, dict) else str(ctx)
                friendly = {
                    "type": "json_invalid",
                    "loc": e.get("loc"),
                    "msg": "请求体不是有效的 JSON，请检查是否使用了正确的双引号(\")并确保 JSON 格式正确",
                    "input": e.get("input"),
                    "ctx": {"error": json_err}
                }
                # 将该条错误替换为更友好的版本
                errors = [friendly if x is e else x for x in errors]
                break

        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                code=settings.REQ_ERROR_CODE,
                message=RequestValidationErrorResponse(error="请求参数验证失败", detail=errors)
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

    #交通请求异常
    @app.exception_handler(TrafficHTTPException)
    async def traffic_http_exception_handler(request: Request, exc: TrafficHTTPException):
        logger.warning(f"TrafficHTTPException: {exc.detail}")
        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                code=exc.detail["code"],
                message=TrafficErrorResponse(error="交通数据操作异常", msg=exc.detail["msg"])
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
        
    