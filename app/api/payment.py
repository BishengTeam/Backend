import logging

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse

from app.domain.user.src.index import User
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import limiter, payment_user_key
from app.port.config import settings
from app.port.exceptions import AppException
from app.schemas.common import APIResponse, success
from app.schemas.payment import (
    PaymentPrepayRequest,
    PaymentPrepayResponse,
    PaymentSyncResponse,
    WechatPayNotificationAck,
)
from app.services.payment import PaymentService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payment", tags=["订单"])


@router.post(
    "/prepay",
    response_model=APIResponse[PaymentPrepayResponse],
    summary="微信支付 V3 JSAPI 预下单",
    description=(
        "为当前用户已有的业务订单创建或重试同一商户订单号的 V3 JSAPI "
        "预下单，返回小程序 requestPayment 所需 RSA 参数。"
    ),
)
async def prepay(
    body: PaymentPrepayRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaymentPrepayResponse]:
    result = await PaymentService().create_prepay(current_user.id, body)
    return success(data=result)


@router.post(
    "/orders/{order_id}/sync",
    response_model=APIResponse[PaymentSyncResponse],
    summary="主动同步本人订单支付状态",
    description=(
        "向微信支付 V3 查单并通过与支付通知相同的事务函数入账。"
        "仅订单本人可调用，按用户限流。"
    ),
)
@limiter.limit(
    f"{settings.WECHAT_PAY_SYNC_RATE_PER_MINUTE}/minute",
    key_func=payment_user_key,
    error_message="支付查单请求过于频繁",
)
async def sync_order(
    request: Request,
    order_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaymentSyncResponse]:
    result = await PaymentService().sync_order(current_user.id, order_id)
    return success(data=result)


@router.post(
    "/callback",
    response_model=WechatPayNotificationAck,
    responses={
        400: {"model": WechatPayNotificationAck, "description": "通知验签、解密或业务校验失败"},
        500: {"model": WechatPayNotificationAck, "description": "临时服务故障，微信应重试通知"},
    },
    summary="微信支付 V3 支付结果通知",
    description=(
        "验证微信支付公钥 ID、时间戳和 RSA 签名，随后使用 API V3 Key "
        "AES-GCM 解密 resource。响应遵循微信支付 V3 通知协议，不套业务响应壳。"
    ),
)
async def payment_callback(request: Request) -> JSONResponse:
    raw_body = await request.body()
    try:
        result = await PaymentService().handle_callback_raw(
            raw_body=raw_body,
            headers=dict(request.headers),
        )
    except AppException as exc:
        logger.warning(
            "wechat payment notification rejected: exception_type=%s",
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=400,
            content={"code": "FAIL", "message": "失败"},
        )
    except Exception as exc:
        logger.error(
            "wechat payment notification failed: exception_type=%s",
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={"code": "FAIL", "message": "失败"},
        )
    logger.info(
        "wechat payment notification acknowledged: order_id=%s processed=%s",
        result.order_id,
        result.processed,
    )
    return JSONResponse(
        status_code=200,
        content={"code": "SUCCESS", "message": "成功"},
    )


@router.post(
    "/refund-callback",
    response_model=WechatPayNotificationAck,
    responses={
        400: {
            "model": WechatPayNotificationAck,
            "description": "退款通知验签、解密或业务校验失败",
        },
        500: {
            "model": WechatPayNotificationAck,
            "description": "临时服务故障，微信应重试通知",
        },
    },
    summary="微信支付 V3 退款结果通知",
    description=(
        "使用微信支付公钥验证签名并使用 API V3 Key 解密退款 resource；"
        "与退款主动查询和对账 Worker 共用同一行锁事务。"
    ),
)
async def refund_callback(request: Request) -> JSONResponse:
    from app.services.renshe_refund import RensheRefundService
    from app.services.h3c_refund import H3cRefundService

    raw_body = await request.body()
    try:
        headers = dict(request.headers)
        try:
            result = await H3cRefundService().handle_callback_raw(
                raw_body=raw_body,
                headers=headers,
            )
            logger.info("wechat H3C refund notification acknowledged: refund_id=%s", result.id)
            return JSONResponse(status_code=200, content={"code": "SUCCESS", "message": "成功"})
        except AppException:
            result = await RensheRefundService().handle_callback_raw(
                raw_body=raw_body,
                headers=headers,
            )
    except AppException as exc:
        logger.warning(
            "wechat refund notification rejected: exception_type=%s",
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=400,
            content={"code": "FAIL", "message": "失败"},
        )
    except Exception as exc:
        logger.error(
            "wechat refund notification failed: exception_type=%s",
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={"code": "FAIL", "message": "失败"},
        )
    logger.info(
        "wechat refund notification acknowledged: refund_id=%s processed=%s",
        result.refund_id,
        result.processed,
    )
    return JSONResponse(
        status_code=200,
        content={"code": "SUCCESS", "message": "成功"},
    )
