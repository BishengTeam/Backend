#!/usr/bin/env python3
"""Export/verify the canonical quiz contract manifest for joint releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def canonical_manifest() -> dict[str, Any]:
    from app.contracts.quiz import (
        DELETED_QUIZ_ENDPOINTS,
        QUIZ_API_CONTRACTS,
        QUIZ_CONTRACT_VERSION,
    )

    operations = [
        {
            "method": entry.method.upper(),
            "path": entry.path,
            "auth": entry.auth,
            "permission": entry.permission,
            "rate_limit_per_minute": entry.rate_limit_per_minute,
        }
        for entry in QUIZ_API_CONTRACTS
    ]
    operations.sort(key=lambda item: (item["path"], item["method"]))
    removed = [
        {"method": method.upper(), "path": path}
        for method, path in DELETED_QUIZ_ENDPOINTS
    ]
    removed.sort(key=lambda item: (item["path"], item["method"]))
    fingerprint_input = json.dumps(
        {"operations": operations, "removed_operations": removed},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema_version": 1,
        "quiz_contract_version": QUIZ_CONTRACT_VERSION,
        "operation_count": len(operations),
        "user_operation_count": sum(item["auth"] != "admin" for item in operations),
        "admin_operation_count": sum(item["auth"] == "admin" for item in operations),
        "removed_operation_count": len(removed),
        "fingerprint_sha256": hashlib.sha256(fingerprint_input).hexdigest(),
        "operations": operations,
        "removed_operations": removed,
    }


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read contract manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"contract manifest must be an object: {path}")
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _assert_equal(expected: dict[str, Any], actual: dict[str, Any], path: Path) -> None:
    if actual != expected:
        raise RuntimeError(
            f"quiz contract manifest mismatch: {path}; regenerate all three "
            "manifests in the same release window"
        )


def _scan_removed_operations(manifest: dict[str, Any], roots: list[str]) -> None:
    removed = manifest.get("removed_operations") or []
    violations: list[str] = []
    extensions = {".ts", ".tsx", ".js", ".jsx"}
    string_literal = re.compile(r"(?P<quote>['\"`])(?P<value>.*?)(?P=quote)")
    for raw_root in roots:
        root = Path(raw_root)
        if not root.is_dir():
            raise RuntimeError(f"client source directory does not exist: {root}")
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in extensions:
                continue
            content = path.read_text(encoding="utf-8")
            literals = [match.group("value") for match in string_literal.finditer(content)]
            for item in removed:
                template = str(item["path"])
                # Dynamic removed paths match both literal `/x/{id}` and the
                # common client interpolation `/x/${id}`.
                pieces = re.split(r"\{[^{}]+\}", template)
                pattern = r"(?:\{[^{}]+\}|\$\{[^{}]+\}|[^/]+)".join(
                    re.escape(piece) for piece in pieces
                )
                if any(re.fullmatch(pattern, literal) for literal in literals):
                    violations.append(f"{path}:{item['method']} {template}")
    if violations:
        raise RuntimeError(
            "removed quiz API references remain:\n" + "\n".join(sorted(violations))
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="append", default=[], metavar="PATH")
    parser.add_argument("--check", action="append", default=[], metavar="PATH")
    parser.add_argument("--print", action="store_true", dest="print_manifest")
    parser.add_argument(
        "--scan-client",
        action="append",
        default=[],
        metavar="SOURCE_DIR",
        help="reject references to any removed quiz API in client source",
    )
    args = parser.parse_args(argv)
    if not args.write and not args.check and not args.print_manifest and not args.scan_client:
        parser.error("provide --write, --check, --scan-client or --print")

    manifest = canonical_manifest()
    try:
        for raw_path in args.write:
            _write(Path(raw_path), manifest)
        for raw_path in args.check:
            path = Path(raw_path)
            _assert_equal(manifest, _read(path), path)
        _scan_removed_operations(manifest, args.scan_client)
        if args.print_manifest:
            print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        "quiz_contract_manifest=ok "
        f"version={manifest['quiz_contract_version']} "
        f"operations={manifest['operation_count']} "
        f"removed={manifest['removed_operation_count']} "
        f"sha256={manifest['fingerprint_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
