from pathlib import Path


def test_publish_release_pipeline_runs_independent_workstreams_in_parallel():
    source = Path(__file__).parents[2].joinpath(
        ".github/workflows/publish-github-release.yml"
    ).read_text(encoding="utf-8")

    for job in (
        "backend-test",
        "admin-test",
        "build-backend",
        "build-admin",
    ):
        assert f"\n  {job}:\n" in source
        block = source.split(f"\n  {job}:\n", 1)[1].split("\n  ", 1)[0]
        assert "needs: resolve" in block
        assert "needs: backend-test" not in block
        assert "needs: admin-test" not in block

    package_block = source.split("\n  package-release:\n", 1)[1]
    for dependency in (
        "resolve",
        "backend-test",
        "admin-test",
        "build-backend",
        "build-admin",
    ):
        assert f"- {dependency}" in package_block


def test_publish_release_workflow_keeps_failure_logs_and_image_caches():
    source = Path(__file__).parents[2].joinpath(
        ".github/workflows/publish-github-release.yml"
    ).read_text(encoding="utf-8")

    assert "name: Upload backend test logs" in source
    assert "name: backend-test-logs" in source
    assert "actions: write" in source
    assert source.count("Cancel remaining jobs on") == 4
    assert "cache-from: type=gha,scope=backend" in source
    assert "cache-to: type=gha,mode=max,scope=backend" in source
    assert "cache-from: type=gha,scope=admin" in source
    assert "cache-to: type=gha,mode=max,scope=admin" in source
