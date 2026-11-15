from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_backend_runtime_uses_an_explicit_utc_clock():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.deploy.yml").read_text(encoding="utf-8")

    assert "TZ=UTC" in dockerfile
    assert 'TZ: "UTC"' in compose
