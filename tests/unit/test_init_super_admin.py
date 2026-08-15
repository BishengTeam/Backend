from __future__ import annotations

import pytest

from scripts import init_super_admin


def test_unexpected_initialization_error_never_echoes_hash_or_traceback(
    monkeypatch,
    capsys,
) -> None:
    pseudo_hash = "a" * 64 + ":" + "top-secret-digest"

    async def _fail() -> None:
        raise RuntimeError(f"database statement failed params={pseudo_hash}")

    monkeypatch.setattr(init_super_admin, "main", _fail)

    with pytest.raises(SystemExit) as exc_info:
        init_super_admin.cli_main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Initialization failed unexpectedly; verify the transaction outcome "
        "before retrying.\n"
    )
    assert pseudo_hash not in captured.err
    assert "Traceback" not in captured.err
