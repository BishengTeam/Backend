"""Static deployment contract for the non-root shared upload volume."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


BACKEND_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (
    (BACKEND_ROOT / "docker-compose.yml", ("app",)),
    (BACKEND_ROOT.parent / "docker-compose.yml", ("backend", "quiz-worker")),
)


@pytest.mark.parametrize(("compose_path", "dependent_services"), COMPOSE_FILES)
def test_upload_volume_is_initialized_before_non_root_services(
    compose_path: Path,
    dependent_services: tuple[str, ...],
) -> None:
    document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = document["services"]
    initializer = services["uploads-init"]

    assert initializer["user"] == "0:0"
    assert "uploads:/app/uploads" in initializer["volumes"]
    command = initializer["command"][0]
    assert "/app/uploads/private/quiz-imports" in command
    assert "chown -R 10001:10001 /app/uploads" in command
    assert "chmod 777" not in command
    assert "rm " not in command

    for service_name in dependent_services:
        dependency = services[service_name]["depends_on"]["uploads-init"]
        assert dependency["condition"] == "service_completed_successfully"
        assert "uploads:/app/uploads" in services[service_name]["volumes"]
