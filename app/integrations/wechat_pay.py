import hashlib
import secrets
import time
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from app.core.config import settings
from app.core.exceptions import ThirdPartyException

WECHAT_UNIFIED_ORDER_URL = "https://api.mch.weixin.qq.com/pay/unifiedorder"
WECHAT_SIGN_TYPE = "MD5"


class WechatPayClient:
    """Wechat Pay client: sign requests, call API, and build JSAPI payment params."""

    def __init__(self) -> None:
        self.appid = settings.WECHAT_PAY_APPID or settings.WECHAT_APPID
        self.mch_id = settings.WECHAT_PAY_MCHID
        self.api_key = settings.WECHAT_PAY_API_KEY
        self.notify_url = settings.WECHAT_PAY_NOTIFY_URL

    async def create_jsapi_prepay(
        self,
        *,
        openid: str,
        out_trade_no: str,
        body: str,
        total_fee: int,
    ) -> dict[str, str]:
        if not self._is_configured():
            raise ThirdPartyException("Wechat Pay configuration is incomplete")

        nonce_str = self._nonce()
        params: dict[str, Any] = {
            "appid": self.appid,
            "mch_id": self.mch_id,
            "nonce_str": nonce_str,
            "body": body,
            "out_trade_no": out_trade_no,
            "total_fee": total_fee,
            "spbill_create_ip": "127.0.0.1",
            "notify_url": self.notify_url,
            "trade_type": "JSAPI",
            "openid": openid,
        }
        params["sign"] = self._sign(params)

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    WECHAT_UNIFIED_ORDER_URL,
                    content=self._to_xml(params),
                    headers={"Content-Type": "text/xml"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ThirdPartyException(f"Wechat unified order request failed: {exc}") from exc

        data = self._from_xml(response.text)
        if data.get("return_code") != "SUCCESS":
            raise ThirdPartyException(f"Wechat unified order failed: {data.get('return_msg', 'unknown')}")
        if data.get("result_code") != "SUCCESS":
            raise ThirdPartyException(f"Wechat unified order failed: {data.get('err_code_des', 'unknown')}")
        prepay_id = data.get("prepay_id")
        if not prepay_id:
            raise ThirdPartyException("Wechat unified order did not return prepay_id")
        return self._build_jsapi_params(prepay_id=prepay_id, nonce_str=nonce_str)

    def verify_signature(self, payload: dict[str, Any]) -> bool:
        sign = payload.get("sign")
        if not sign or not self.api_key:
            return False
        return sign == self._sign(payload)

    def _is_configured(self) -> bool:
        return bool(self.appid and self.mch_id and self.api_key and self.notify_url)

    def _build_jsapi_params(self, *, prepay_id: str, nonce_str: str) -> dict[str, str]:
        time_stamp = str(int(time.time()))
        params = {
            "appId": self.appid or settings.WECHAT_APPID,
            "timeStamp": time_stamp,
            "nonceStr": nonce_str,
            "package": f"prepay_id={prepay_id}",
            "signType": WECHAT_SIGN_TYPE,
        }
        params["paySign"] = self._sign(params)
        return {
            "prepay_id": prepay_id,
            "time_stamp": time_stamp,
            "nonce_str": nonce_str,
            "package": params["package"],
            "sign_type": WECHAT_SIGN_TYPE,
            "pay_sign": params["paySign"],
        }

    def _sign(self, params: dict[str, Any]) -> str:
        if not self.api_key:
            raise ThirdPartyException("Wechat Pay API key is not configured")
        filtered = {
            key: value
            for key, value in params.items()
            if key != "sign" and value is not None and value != ""
        }
        query = "&".join(f"{key}={filtered[key]}" for key in sorted(filtered))
        source = f"{query}&key={self.api_key}"
        return hashlib.md5(source.encode("utf-8")).hexdigest().upper()

    @staticmethod
    def _nonce() -> str:
        return secrets.token_hex(16)

    @staticmethod
    def _to_xml(params: dict[str, Any]) -> str:
        root = ET.Element("xml")
        for key, value in params.items():
            child = ET.SubElement(root, key)
            child.text = str(value)
        return ET.tostring(root, encoding="utf-8").decode("utf-8")

    @staticmethod
    def _from_xml(text: str) -> dict[str, str]:
        root = ET.fromstring(text)
        return {child.tag: child.text or "" for child in root}
