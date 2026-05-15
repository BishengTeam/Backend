class AppException(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message


class NotFoundException(AppException):
    def __init__(self, resource: str):
        super().__init__(code=404, message=f"{resource} 不存在")


class BusinessException(AppException):
    def __init__(self, message: str):
        super().__init__(code=422, message=message)


class UnauthorizedException(AppException):
    def __init__(self, message: str = "请先登录"):
        super().__init__(code=401, message=message)


class ForbiddenException(AppException):
    def __init__(self, message: str = "无权限"):
        super().__init__(code=403, message=message)


class ConflictException(AppException):
    def __init__(self, message: str):
        super().__init__(code=409, message=message)
