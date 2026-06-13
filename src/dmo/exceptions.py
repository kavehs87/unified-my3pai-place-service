import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import StatementError

logger = structlog.get_logger()


class AppError(Exception):
    def __init__(self, message: str, code: str, status_code: int = 400):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        logger.error(
            "request.error",
            status_code=exc.status_code,
            detail=exc.detail,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": _error_type(exc.status_code),
                "message": str(exc.detail),
                "code": exc.status_code,
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": _error_type(exc.status_code),
                "message": exc.message,
                "code": exc.code,
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    @app.exception_handler(StatementError)
    async def statement_timeout_handler(request: Request, exc: StatementError):
        orig_code = getattr(getattr(exc, "orig", None), "code", "")
        if orig_code == "57014":
            logger.warning(
                "request.query_timeout",
                path=request.url.path,
                request_id=getattr(request.state, "request_id", ""),
            )
            return JSONResponse(
                status_code=504,
                content={
                    "error": "GatewayTimeout",
                    "message": "Query exceeded timeout limit",
                    "code": 504,
                    "request_id": getattr(request.state, "request_id", ""),
                },
            )
        raise exc

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error(
            "request.unhandled_error",
            error=str(exc),
            path=request.url.path,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal Server Error",
                "message": "An unexpected error occurred",
                "code": 500,
                "request_id": getattr(request.state, "request_id", ""),
            },
        )


def _error_type(status_code: int) -> str:
    types = {
        400: "BadRequest",
        401: "Unauthorized",
        403: "Forbidden",
        404: "NotFound",
        409: "Conflict",
        422: "UnprocessableEntity",
        429: "TooManyRequests",
        500: "InternalServerError",
    }
    return types.get(status_code, "Error")
