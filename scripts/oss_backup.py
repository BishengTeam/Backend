from __future__ import annotations

import argparse
import hashlib
import stat
from pathlib import Path

import oss2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        key, separator, value = raw_line.partition("=")
        if not separator or key in result:
            raise SystemExit(f"invalid runtime configuration line: {raw_line}")
        result[key] = value
    return result


def _private_file(path: Path) -> bytes:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SystemExit(f"secret path is unsafe: {path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SystemExit(f"secret permissions are unsafe: {path}")
    return path.read_bytes().strip()


def _bucket(installation_dir: Path) -> oss2.Bucket:
    raw_installation_dir = Path(installation_dir)
    try:
        info = raw_installation_dir.lstat()
    except OSError:
        raise SystemExit("installation directory is unavailable")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SystemExit("installation directory is unsafe")
    installation_dir = raw_installation_dir.resolve()
    runtime = _read_env(installation_dir / "runtime.env")
    endpoint = runtime.get("RECOVERY_OSS_ENDPOINT", "").strip()
    bucket_name = runtime.get("RECOVERY_OSS_BUCKET", "").strip()
    if not endpoint or not bucket_name:
        raise SystemExit("backup OSS is not configured")
    access_id = _private_file(
        installation_dir / "secrets" / "aliyun_oss_access_key_id"
    ).decode("utf-8")
    access_secret = _private_file(
        installation_dir / "secrets" / "aliyun_oss_access_key_secret"
    ).decode("utf-8")
    return oss2.Bucket(oss2.Auth(access_id, access_secret), endpoint, bucket_name)


def upload(args: argparse.Namespace) -> None:
    path = Path(args.file).resolve()
    if not path.is_file() or path.is_symlink():
        raise SystemExit("backup file is unavailable")
    if not args.object_key.startswith("wemini-backups/postgresql/") \
            or ".." in args.object_key.split("/"):
        raise SystemExit("backup object key is unsafe")
    bucket = _bucket(Path(args.installation_dir))
    if str(bucket.get_bucket_acl().acl).strip().lower() != "private":
        raise SystemExit("backup OSS bucket must be private")
    digest = _sha256(path)
    with path.open("rb") as stream:
        bucket.put_object(
            args.object_key,
            stream,
            headers={
                "Content-Type": args.content_type,
                "x-oss-meta-sha256": digest,
                "x-oss-meta-purpose": "wemini-postgresql-backup",
            },
        )
    metadata = bucket.head_object(args.object_key)
    headers = {str(key).lower(): str(value) for key, value in metadata.headers.items()}
    if headers.get("x-oss-meta-sha256") != digest:
        raise SystemExit("backup OSS checksum metadata mismatch")
    print(digest)


def prune(args: argparse.Namespace) -> None:
    if not args.prefix.startswith("wemini-backups/postgresql/") \
            or ".." in args.prefix.split("/"):
        raise SystemExit("backup object prefix is unsafe")
    bucket = _bucket(Path(args.installation_dir))
    objects = sorted(
        oss2.ObjectIterator(bucket, prefix=args.prefix),
        key=lambda item: (item.last_modified, item.key),
        reverse=True,
    )
    cutoff = args.retain_days * 24 * 60 * 60
    now = oss2.utils.http_to_unixtime(oss2.utils.http_date())
    kept = 0
    for item in objects:
        keep = kept < args.min_objects or now - item.last_modified <= cutoff
        if keep:
            kept += 1
        else:
            bucket.delete_object(item.key)
    print(f"objects_before={len(objects)} objects_after={kept}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Upload and prune WeMiniApp backups")
    subcommands = parser.add_subparsers(dest="command", required=True)

    upload_parser = subcommands.add_parser("upload")
    upload_parser.add_argument("--installation-dir", required=True)
    upload_parser.add_argument("--file", required=True)
    upload_parser.add_argument("--object-key", required=True)
    upload_parser.add_argument("--content-type", default="application/octet-stream")
    upload_parser.set_defaults(action=upload)

    prune_parser = subcommands.add_parser("prune")
    prune_parser.add_argument("--installation-dir", required=True)
    prune_parser.add_argument("--prefix", required=True)
    prune_parser.add_argument("--retain-days", type=int, default=7)
    prune_parser.add_argument("--min-objects", type=int, default=7)
    prune_parser.set_defaults(action=prune)
    return parser


def main() -> None:
    args = _parser().parse_args()
    args.action(args)


if __name__ == "__main__":
    main()
