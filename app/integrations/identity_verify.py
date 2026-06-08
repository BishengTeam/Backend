"""实名核验集成 — 对接第三方身份证实名认证 API

支持的 provider:
    none     — 仅格式校验 (默认)
    aliyun   — 阿里云云市场 身份证实名认证
    tencent  — 腾讯云 实名认证

配置项见 app/port/config.py
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import httpx

from app.port.config import settings


class IdentityVerifyError(Exception):
    """核验服务不可用 / 网络异常"""


class IdentityMismatchError(Exception):
    """姓名与身份证号不匹配"""


async def verify_real_name(name: str, id_card: str) -> bool | None:
    """核验姓名与身份证号是否匹配。

    返回:
        True  — 核验通过（姓名与身份证号匹配）
        False — 核验不通过（姓名与身份证号不匹配）
        None  — 未执行核验（provider=none，需人工审核）

    Raises:
        IdentityVerifyError: 服务不可用或网络异常(调用方应降级)
    """
    provider = settings.IDENTITY_VERIFY_PROVIDER
    if provider == "none":
        return None  # 未配置核验服务，需人工审核
    if provider == "aliyun":
        return await _aliyun_verify(name, id_card)
    if provider == "tencent":
        return await _tencent_verify(name, id_card)
    raise IdentityVerifyError(f"未知的实名核验 provider: {provider}")


# ── 阿里云 云市场 ──────────────────────────────────────────────

_ALIYUN_HOST = "https://eid.shumaidata.com"
_ALIYUN_PATH = "/eid/check"


async def _aliyun_verify(name: str, id_card: str) -> bool:
    """阿里云云市场 身份证二要素核验

    文档: https://market.aliyun.com/products/57000002/cmapi00042664.html

    返回:
        True  — 匹配
        False — 不匹配
    """
    app_code = settings.ALIYUN_VERIFY_APP_CODE
    if not app_code:
        raise IdentityVerifyError("ALIYUN_VERIFY_APP_CODE 未配置")

    params = urlencode({"idcard": id_card, "name": name})
    url = f"{_ALIYUN_HOST}{_ALIYUN_PATH}?{params}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"Authorization": f"APPCODE {app_code}"})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        raise IdentityVerifyError(f"阿里云核验请求失败: {e}") from e

    # 返回格式: {"code": 0, "message": "成功", "data": {"result": 1, "desc": "一致"}}
    # result: 0=不一致, 1=一致, 2=无记录
    if data.get("code") != 0:
        if data.get("code") == 1:
            return False  # 不匹配
        raise IdentityVerifyError(f"阿里云核验返回异常: {data.get('message')}")

    result = data.get("data", {}).get("result")
    if result == 1:
        return True
    if result in (0, 2):
        return False
    raise IdentityVerifyError(f"阿里云核验未知结果: {data}")


# ── 腾讯云 ─────────────────────────────────────────────────────

_TENCENT_HOST = "https://faceid.tencentcloudapi.com"
_TENCENT_SERVICE = "faceid"
_TENCENT_ACTION = "IdCardVerification"
_TENCENT_VERSION = "2020-03-03"


def _tencent_sign(secret_key: str, payload: str, timestamp: int) -> str:
    """腾讯云 API v3 签名"""
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    canonical = f"POST\n/\n\ncontent-type:application/json\nhost:{_TENCENT_HOST.removeprefix('https://')}\n\ncontent-type;host\n{hashlib.sha256(payload.encode()).hexdigest()}"
    string_to_sign = f"TC3-HMAC-SHA256\n{timestamp}\n{date}/{_TENCENT_SERVICE}/tc3_request\n{hashlib.sha256(canonical.encode()).hexdigest()}"

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    secret_date = _sign(f"TC3{secret_key}".encode(), date)
    secret_service = _sign(secret_date, _TENCENT_SERVICE)
    secret_signing = _sign(secret_service, "tc3_request")
    return hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()


async def _tencent_verify(name: str, id_card: str) -> bool:
    """腾讯云 身份证实名认证

    文档: https://cloud.tencent.com/document/product/1007/33188
    """
    secret_id = settings.TENCENT_SECRET_ID
    secret_key = settings.TENCENT_SECRET_KEY
    if not secret_id or not secret_key:
        raise IdentityVerifyError("TENCENT_SECRET_ID / TENCENT_SECRET_KEY 未配置")

    payload = json.dumps({"IdCard": id_card, "Name": name})
    timestamp = int(time.time())

    headers = {
        "Content-Type": "application/json",
        "Host": _TENCENT_HOST.removeprefix("https://"),
        "X-TC-Action": _TENCENT_ACTION,
        "X-TC-Version": _TENCENT_VERSION,
        "X-TC-Timestamp": str(timestamp),
        "Authorization": f"TC3-HMAC-SHA256 Credential={secret_id}/{time.strftime('%Y-%m-%d', time.gmtime(timestamp))}/{_TENCENT_SERVICE}/tc3_request, SignedHeaders=content-type;host, Signature={_tencent_sign(secret_key, payload, timestamp)}",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_TENCENT_HOST, content=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        raise IdentityVerifyError(f"腾讯云核验请求失败: {e}") from e

    if "Error" in data.get("Response", {}):
        err = data["Response"]["Error"]
        raise IdentityVerifyError(f"腾讯云核验错误: {err.get('Code')} - {err.get('Message')}")

    result = data.get("Response", {}).get("Result")
    # IdCardVerificationResult: "0"=一致, "-1"=不一致
    return result == "0"
