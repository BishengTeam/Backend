class AppException(Exception):
    def __init__(self, code: int, message: str, http_status_code: int = 400):
        self.code = code
        self.message = message
        self.http_status_code = http_status_code


class NotFoundException(AppException):
    def __init__(self, resource: str):
        super().__init__(code=40300, message=f"{resource} 不存在", http_status_code=404)


class BusinessException(AppException):
    def __init__(self, message: str):
        super().__init__(code=40200, message=message, http_status_code=422)


class UnauthorizedException(AppException):
    def __init__(self, message: str = "请先登录"):
        super().__init__(code=40100, message=message, http_status_code=401)


class ForbiddenException(AppException):
    def __init__(self, message: str = "无权限"):
        super().__init__(code=40101, message=message, http_status_code=403)


class ConflictException(AppException):
    def __init__(self, message: str):
        super().__init__(code=40201, message=message, http_status_code=409)


class ValidationException(AppException):
    def __init__(self, message: str = "参数校验失败", detail: list[dict] | None = None):
        super().__init__(code=40001, message=message, http_status_code=422)
        self.detail = detail or []
