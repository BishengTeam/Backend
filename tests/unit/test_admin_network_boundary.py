from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_COMPOSE = ROOT / "docker-compose.deploy.yml"


def _service_block(source: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\s*$\n(.*?)(?=^  [a-zA-Z0-9_-]+:\s*$|\Z)",
        source,
    )
    assert match is not None, f"missing production service: {service}"
    return match.group(1)


def _published_ports(service_block: str) -> list[str]:
    match = re.search(
        r"(?m)^    ports:\s*$\n((?:^      - [^\n]*$\n?)+)", service_block
    )
    assert match is not None, "service must publish an explicit loopback port"
    return [
        line.removeprefix("      - ").strip().strip('"').strip("'")
        for line in match.group(1).splitlines()
    ]


def test_production_admin_and_backend_publish_only_on_ipv4_loopback() -> None:
    source = PRODUCTION_COMPOSE.read_text("utf-8")
    expected = {
        "app": "127.0.0.1:${BACKEND_PORT:-8000}:8000",
        "admin": "127.0.0.1:${ADMIN_PORT:-8080}:80",
    }

    for service, binding in expected.items():
        service_block = _service_block(source, service)
        assert service_block.count("\n    ports:") == 1
        assert re.search(r"(?m)^    network_mode:", service_block) is None
        ports = _published_ports(service_block)
        assert ports == [binding]
        assert all(port.startswith("127.0.0.1:") for port in ports)
        assert all("0.0.0.0:" not in port and "[::]:" not in port for port in ports)
