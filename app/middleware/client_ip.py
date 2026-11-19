"""解析可信客户端 IP：仅当直连方是受信代理时才读取转发头。"""
import ipaddress
import os

_DEFAULT_TRUSTED = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
)


def _trusted_networks() -> tuple:
    raw = (os.getenv("TRUSTED_PROXY_IPS") or ",".join(_DEFAULT_TRUSTED)).strip()
    networks = []
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def is_trusted_proxy(host: str | None) -> bool:
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in _trusted_networks())


def trusted_client_ip(request) -> str:
    """返回最可信的客户端地址；伪造的转发头在非受信直连时被忽略。"""
    client_host = request.client.host if request.client else None
    if not is_trusted_proxy(client_host):
        return client_host or "unknown"

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # 反向代理统一以 $remote_addr 覆盖该头，最左侧即真实客户端。
        first = forwarded.split(",", 1)[0].strip()
        try:
            return str(ipaddress.ip_address(first))
        except ValueError:
            pass
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        try:
            return str(ipaddress.ip_address(real_ip))
        except ValueError:
            pass
    return client_host or "unknown"
