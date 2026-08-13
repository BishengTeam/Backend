#!/usr/bin/env python3
"""Generate and verify deterministic QF-55 quiz acceptance fixtures.

The generated files contain no credentials and never connect to PostgreSQL,
Redis, OSS, or the application.  They are designed for a disposable test/UAT
database and are byte-for-byte reproducible for a given ``FIXTURE_VERSION``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_VERSION = "2026-08-12-qf55-v1"
FIXTURE_PREFIX = "QF55-V1"
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_QUESTIONS = 5_000

CATEGORY_BLUEPRINT: tuple[dict[str, Any], ...] = (
    {
        "ref": "root",
        "name": "QF55 黄金题库",
        "parent_ref": None,
        "sort_order": 5500,
        "final_status": "active",
    },
    {
        "ref": "import_group",
        "name": "内容导入",
        "parent_ref": "root",
        "sort_order": 10,
        "final_status": "active",
    },
    {
        "ref": "import_leaf",
        "name": "五千题导入",
        "parent_ref": "import_group",
        "sort_order": 10,
        "final_status": "active",
    },
    {
        "ref": "workflow_group",
        "name": "用户流程",
        "parent_ref": "root",
        "sort_order": 20,
        "final_status": "active",
    },
    {
        "ref": "practice_leaf",
        "name": "练习清错",
        "parent_ref": "workflow_group",
        "sort_order": 10,
        "final_status": "active",
    },
    {
        "ref": "exam_leaf",
        "name": "考试四状态",
        "parent_ref": "workflow_group",
        "sort_order": 20,
        "final_status": "active",
    },
    {
        "ref": "disabled_leaf",
        "name": "停用分类",
        "parent_ref": "root",
        "sort_order": 30,
        "final_status": "disabled",
    },
)

IMPORT_PATH = ["QF55 黄金题库", "内容导入", "五千题导入"]
PRACTICE_PATH = ["QF55 黄金题库", "用户流程", "练习清错"]
EXAM_PATH = ["QF55 黄金题库", "用户流程", "考试四状态"]
DISABLED_PATH = ["QF55 黄金题库", "停用分类"]


class FixtureError(RuntimeError):
    """Raised when generated acceptance data is missing or has drifted."""


def _json_bytes(value: object, *, pretty: bool = False) -> bytes:
    if pretty:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    return (rendered + "\n").encode("utf-8")


def _question(
    *,
    category_path: list[str],
    question_type: str,
    question_text: str,
    options: dict[str, str],
    correct_answer: str | list[str],
    explanation: str,
) -> dict[str, object]:
    return {
        "category_path": category_path,
        "question_type": question_type,
        "question_text": question_text,
        "options": options,
        "correct_answer": correct_answer,
        "explanation": explanation,
    }


def _numbered_question(index: int, *, source: str) -> dict[str, object]:
    marker = f"{FIXTURE_PREFIX}-{source}-{index:04d}"
    variant = (index - 1) % 3
    if variant == 0:
        return _question(
            category_path=list(IMPORT_PATH),
            question_type="single_choice",
            question_text=f"{marker} 单选题的固定答案是哪一项？",
            options={"A": "固定答案", "B": "干扰项一", "C": "干扰项二"},
            correct_answer="A",
            explanation=f"{marker} 使用 A 作为固定单选答案。",
        )
    if variant == 1:
        all_correct = index % 30 == 2
        return _question(
            category_path=list(IMPORT_PATH),
            question_type="multiple_choice",
            question_text=f"{marker} 多选题应选择哪些固定选项？",
            options={"A": "选项 A", "B": "选项 B", "C": "选项 C", "D": "选项 D"},
            correct_answer=(
                ["A", "B", "C", "D"] if all_correct else ["A", "C"]
            ),
            explanation=(
                f"{marker} 覆盖全部选项均正确边界。"
                if all_correct
                else f"{marker} 固定选择 A、C。"
            ),
        )
    answer = "A" if index % 2 else "B"
    return _question(
        category_path=list(IMPORT_PATH),
        question_type="judge",
        question_text=f"{marker} 判断题固定命题。",
        options={"A": "正确", "B": "错误"},
        correct_answer=answer,
        explanation=f"{marker} 固定判断答案为 {answer}。",
    )


def _success_questions(*, source: str) -> list[dict[str, object]]:
    return [
        _numbered_question(index, source=source)
        for index in range(1, MAX_IMPORT_QUESTIONS + 1)
    ]


def _workflow_questions() -> list[dict[str, object]]:
    questions: list[dict[str, object]] = []
    for index in range(1, 11):
        marker = f"{FIXTURE_PREFIX}-PRACTICE-{index:02d}"
        questions.append(
            _question(
                category_path=list(PRACTICE_PATH),
                question_type="single_choice",
                question_text=f"{marker} 练习清错固定题。",
                options={"A": "正确项", "B": "错误项", "C": "其他项"},
                correct_answer="A",
                explanation=f"{marker} 固定答案为 A。",
            )
        )
    for index in range(1, 11):
        marker = f"{FIXTURE_PREFIX}-EXAM-{index:02d}"
        variant = (index - 1) % 3
        if variant == 0:
            questions.append(
                _question(
                    category_path=list(EXAM_PATH),
                    question_type="single_choice",
                    question_text=f"{marker} 考试单选固定题。",
                    options={"A": "正确项", "B": "错误项", "C": "其他项"},
                    correct_answer="A",
                    explanation=f"{marker} 固定答案为 A。",
                )
            )
        elif variant == 1:
            questions.append(
                _question(
                    category_path=list(EXAM_PATH),
                    question_type="multiple_choice",
                    question_text=f"{marker} 考试多选固定题。",
                    options={"A": "正确一", "B": "错误一", "C": "正确二", "D": "错误二"},
                    correct_answer=["A", "C"],
                    explanation=f"{marker} 固定答案为 A、C。",
                )
            )
        else:
            questions.append(
                _question(
                    category_path=list(EXAM_PATH),
                    question_type="judge",
                    question_text=f"{marker} 考试判断固定题。",
                    options={"A": "正确", "B": "错误"},
                    correct_answer="B",
                    explanation=f"{marker} 固定答案为 B。",
                )
            )
    return questions


def _invalid_json_questions() -> list[dict[str, object]]:
    duplicate = _question(
        category_path=list(IMPORT_PATH),
        question_type="single_choice",
        question_text=f"{FIXTURE_PREFIX}-INVALID-JSON-DUPLICATE",
        options={"A": "一", "B": "二", "C": "三"},
        correct_answer="A",
        explanation="同批次重复题干，用于验证原子回滚。",
    )
    return [
        _question(
            category_path=["QF55 黄金题库", "内容导入", "不存在分类"],
            question_type="single_choice",
            question_text=f"{FIXTURE_PREFIX}-INVALID-JSON-MISSING-CATEGORY",
            options={"A": "一", "B": "二", "C": "三"},
            correct_answer="A",
            explanation="分类不存在错误。",
        ),
        _question(
            category_path=list(DISABLED_PATH),
            question_type="judge",
            question_text=f"{FIXTURE_PREFIX}-INVALID-JSON-DISABLED-CATEGORY",
            options={"A": "正确", "B": "错误"},
            correct_answer="B",
            explanation="分类停用错误。",
        ),
        duplicate,
        dict(duplicate),
    ]


def _csv_bytes(questions: list[dict[str, object]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        (
            "category_path",
            "question_type",
            "question_text",
            "options",
            "correct_answer",
            "explanation",
        )
    )
    for item in questions:
        answer = item["correct_answer"]
        writer.writerow(
            (
                json.dumps(item["category_path"], ensure_ascii=False, separators=(",", ":")),
                item["question_type"],
                item["question_text"],
                json.dumps(item["options"], ensure_ascii=False, separators=(",", ":")),
                (
                    json.dumps(answer, ensure_ascii=False, separators=(",", ":"))
                    if isinstance(answer, list)
                    else answer
                ),
                item["explanation"],
            )
        )
    return stream.getvalue().encode("utf-8")


def _invalid_csv_bytes() -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        (
            "category_path",
            "question_type",
            "question_text",
            "options",
            "correct_answer",
            "explanation",
        )
    )
    valid_path = json.dumps(IMPORT_PATH, ensure_ascii=False, separators=(",", ":"))
    missing_path = json.dumps(
        ["QF55 黄金题库", "内容导入", "不存在分类"],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    valid_options = json.dumps(
        {"A": "一", "B": "二", "C": "三"},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    writer.writerow(
        (
            valid_path,
            "single_choice",
            f"{FIXTURE_PREFIX}-INVALID-CSV-BROKEN-OPTIONS",
            "{broken-json",
            "A",
            "选项 JSON 解析错误。",
        )
    )
    writer.writerow(
        (
            valid_path,
            "unsupported_type",
            f"{FIXTURE_PREFIX}-INVALID-CSV-TYPE",
            valid_options,
            "A",
            "题型错误。",
        )
    )
    writer.writerow(
        (
            missing_path,
            "single_choice",
            f"{FIXTURE_PREFIX}-INVALID-CSV-MISSING-CATEGORY",
            valid_options,
            "A",
            "分类不存在错误。",
        )
    )
    for _ in range(2):
        writer.writerow(
            (
                valid_path,
                "single_choice",
                f"{FIXTURE_PREFIX}-INVALID-CSV-DUPLICATE",
                valid_options,
                "A",
                "同批次重复题干。",
            )
        )
    return stream.getvalue().encode("utf-8")


def _acceptance_plan() -> dict[str, object]:
    return {
        "version": FIXTURE_VERSION,
        "required_personas": [
            {
                "ref": "admin",
                "kind": "admin",
                "required_permissions": ["quiz:list", "quiz:write", "quiz:import"],
                "credentials_from": ["QF55_ADMIN_USERNAME", "QF55_ADMIN_PASSWORD"],
            },
            {
                "ref": "super_admin",
                "kind": "admin",
                "required_permissions": ["*"],
                "optional": True,
            },
            {
                "ref": "disabled_admin",
                "kind": "admin",
                "is_active": False,
                "optional": True,
            },
            {
                "ref": "user_primary",
                "kind": "wechat_user",
                "token_from": "QF55_USER_TOKEN",
            },
            {
                "ref": "user_secondary",
                "kind": "wechat_user",
                "token_from": "QF55_OTHER_USER_TOKEN",
            },
            {"ref": "anonymous", "kind": "anonymous"},
        ],
        "scenarios": [
            {
                "id": "QF55-CATEGORY-THREE-LEVELS",
                "requires": ["postgresql", "admin"],
                "input": "categories.json",
                "assertions": [
                    "all seven categories are created in parent-before-child order",
                    "import/practice/exam leaves have depth=3",
                    "disabled_leaf is disabled only after all categories exist",
                    "a fourth level, self-reference, and cycle are rejected",
                ],
            },
            {
                "id": "QF55-IMPORT-JSON-5000",
                "requires": ["postgresql", "redis", "oss", "quiz-worker", "admin"],
                "input": "import-success-5000.json",
                "operation": "POST /admin/quiz/imports/json",
                "assertions": [
                    "terminal status=succeeded",
                    "total_rows=validated_rows=created_count=5000",
                    "all created questions are draft",
                    "source object is private and expires after 7 days",
                ],
            },
            {
                "id": "QF55-IMPORT-CSV-5000",
                "requires": ["postgresql", "redis", "oss", "quiz-worker", "admin"],
                "input": "import-success-5000.csv",
                "operation": "POST /admin/quiz/imports/csv",
                "assertions": [
                    "terminal status=succeeded",
                    "total_rows=validated_rows=created_count=5000",
                    "all created questions are draft",
                ],
            },
            {
                "id": "QF55-IMPORT-ATOMIC-ERRORS",
                "requires": ["postgresql", "redis", "oss", "quiz-worker", "admin"],
                "inputs": ["import-validation-errors.json", "import-validation-errors.csv"],
                "assertions": [
                    "each job terminates as validation_failed",
                    "each job created_count=0 and creates no question",
                    "the report contains every row-level error",
                    "validation_failed cannot be retried",
                ],
            },
            {
                "id": "QF55-PERMISSIONS",
                "requires": ["admin", "super_admin", "disabled_admin", "anonymous"],
                "assertions": [
                    "normal admin can exercise quiz:list, quiz:write, and quiz:import",
                    "super_admin wildcard is accepted without a super-admin-only rule",
                    "anonymous is 401 and missing permission is 403/40101 with an audit row",
                    "disabled admin is rejected",
                ],
            },
            {
                "id": "QF55-PRACTICE-WRONG-CLEAR",
                "requires": ["postgresql", "user_primary"],
                "input": "workflow-questions.json",
                "category_ref": "practice_leaf",
                "assertions": [
                    "exactly ten published questions make selection deterministic",
                    "first wrong answer activates the wrong item",
                    "a correct re-answer in the same session does not clear it",
                    "first correct answer in a later session clears it",
                    "the first daily practice answer creates the Asia/Shanghai check-in",
                ],
            },
            {
                "id": "QF55-EXAM-FOUR-STATES-AND-DISCONNECT",
                "requires": ["postgresql", "redis", "quiz-worker", "user_primary", "user_secondary"],
                "input": "workflow-questions.json",
                "category_ref": "exam_leaf",
                "assertions": [
                    "in_progress and abandoned responses never expose answers or explanations",
                    "completed is scored once and duplicate submit is idempotent",
                    "timed_out is settled and included in exam statistics",
                    "abandoned retains saved answers but has no score/stat contribution",
                    "a fresh client resumes the same fixed question order and last saved versions",
                ],
            },
            {
                "id": "QF55-OSS-SEVEN-DAY-LIFECYCLE",
                "requires": ["real-private-aliyun-oss", "quiz-worker", "admin", "anonymous"],
                "assertions": [
                    "object keys do not contain administrator, category, or question data",
                    "anonymous access fails",
                    "signed URL lifetime is at most 300 seconds and expiry is enforced",
                    "source/report access writes a redacted audit row",
                    "objects are deleted after seven days and cleanup failure is retried",
                ],
            },
            {
                "id": "QF55-WORKER-RESTART-AND-CONTENTION",
                "requires": ["postgresql", "redis", "two-quiz-workers"],
                "assertions": [
                    "only one worker claims an import or expired exam",
                    "restart resumes stale work without duplicate questions or settlement",
                    "shared heartbeat and metrics stay visible to Web",
                    "stale or absent heartbeat makes /ready fail",
                ],
            },
        ],
        "cleanup": {
            "allowed_database_suffixes": ["_test", "_uat", "_acceptance"],
            "preferred": "restore the disposable database snapshot created before the run",
            "fallback": "delete only rows owned by the QF55-V1 question/category prefix, children before parents",
            "never": [
                "run fixture cleanup against development, staging, or production",
                "delete shared administrator accounts",
                "infer a cleanup target from an empty environment variable",
            ],
        },
    }


def _artifact_payloads() -> dict[str, bytes]:
    json_success = _success_questions(source="JSON")
    csv_success = _success_questions(source="CSV")
    return {
        "categories.json": _json_bytes(
            {"version": FIXTURE_VERSION, "categories": list(CATEGORY_BLUEPRINT)},
            pretty=True,
        ),
        "workflow-questions.json": _json_bytes(
            {"questions": _workflow_questions()}, pretty=False
        ),
        "import-success-5000.json": _json_bytes(
            {"questions": json_success}, pretty=False
        ),
        "import-success-5000.csv": _csv_bytes(csv_success),
        "import-validation-errors.json": _json_bytes(
            {"questions": _invalid_json_questions()}, pretty=False
        ),
        "import-validation-errors.csv": _invalid_csv_bytes(),
        "acceptance-plan.json": _json_bytes(_acceptance_plan(), pretty=True),
    }


def build_fixture_set() -> tuple[dict[str, bytes], dict[str, object]]:
    artifacts = _artifact_payloads()
    metadata: dict[str, dict[str, object]] = {}
    row_counts = {
        "categories.json": len(CATEGORY_BLUEPRINT),
        "workflow-questions.json": 20,
        "import-success-5000.json": MAX_IMPORT_QUESTIONS,
        "import-success-5000.csv": MAX_IMPORT_QUESTIONS,
        "import-validation-errors.json": 4,
        "import-validation-errors.csv": 5,
        "acceptance-plan.json": len(_acceptance_plan()["scenarios"]),
    }
    for name in sorted(artifacts):
        content = artifacts[name]
        metadata[name] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "row_or_case_count": row_counts[name],
        }
    manifest_without_digest: dict[str, object] = {
        "fixture_version": FIXTURE_VERSION,
        "fixture_prefix": FIXTURE_PREFIX,
        "limits": {
            "max_import_bytes": MAX_IMPORT_BYTES,
            "max_import_questions": MAX_IMPORT_QUESTIONS,
        },
        "artifacts": metadata,
        "generation": {
            "deterministic": True,
            "contains_credentials": False,
            "connects_to_external_services": False,
        },
    }
    definition_digest = hashlib.sha256(
        _json_bytes(manifest_without_digest, pretty=False)
    ).hexdigest()
    manifest = dict(manifest_without_digest)
    manifest["definition_sha256"] = definition_digest
    return artifacts, manifest


def _safe_output(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    forbidden = {Path("/").resolve(), Path.home().resolve(), ROOT.resolve(), ROOT.parent.resolve()}
    if resolved in forbidden:
        raise FixtureError(f"refusing broad fixture output directory: {resolved}")
    if resolved.exists() and not resolved.is_dir():
        raise FixtureError(f"fixture output is not a directory: {resolved}")
    return resolved


def generate(output: Path, *, force: bool = False) -> dict[str, object]:
    destination = _safe_output(output)
    artifacts, manifest = build_fixture_set()
    owned_names = [*artifacts, "manifest.json"]
    existing = [name for name in owned_names if (destination / name).exists()]
    if existing and not force:
        raise FixtureError(
            "fixture files already exist; pass --force to replace only owned files: "
            + ", ".join(sorted(existing))
        )
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in artifacts.items():
        (destination / name).write_bytes(content)
    (destination / "manifest.json").write_bytes(_json_bytes(manifest, pretty=True))
    return manifest


def check(output: Path) -> dict[str, object]:
    destination = _safe_output(output)
    artifacts, expected_manifest = build_fixture_set()
    manifest_path = destination / "manifest.json"
    if not manifest_path.is_file():
        raise FixtureError(f"missing fixture manifest: {manifest_path}")
    try:
        actual_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureError(f"invalid fixture manifest: {exc}") from exc
    if actual_manifest != expected_manifest:
        raise FixtureError("fixture manifest does not match the frozen definition")
    for name, expected_content in artifacts.items():
        path = destination / name
        if not path.is_file():
            raise FixtureError(f"missing fixture artifact: {name}")
        actual_content = path.read_bytes()
        if actual_content != expected_content:
            raise FixtureError(f"fixture artifact drifted: {name}")
        if name.startswith("import-success") and len(actual_content) > MAX_IMPORT_BYTES:
            raise FixtureError(f"fixture exceeds 10 MiB: {name}")
    return expected_manifest


def check_manifest(path: Path) -> dict[str, object]:
    manifest_path = path.expanduser().resolve()
    if not manifest_path.is_file():
        raise FixtureError(f"missing canonical fixture manifest: {manifest_path}")
    try:
        actual = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureError(f"invalid canonical fixture manifest: {exc}") from exc
    _artifacts, expected = build_fixture_set()
    if actual != expected:
        raise FixtureError(
            "canonical fixture manifest drifted; review the generated artifacts "
            "and deliberately replace the manifest"
        )
    return expected


def self_check() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="qf55-quiz-fixtures-") as raw:
        output = Path(raw) / "generated"
        expected = generate(output)
        checked = check(output)
        if checked != expected:
            raise FixtureError("fixture self-check produced inconsistent manifests")
        # Verify that a copied fixture directory remains independently valid.
        copied = Path(raw) / "copied"
        shutil.copytree(output, copied)
        return check(copied)


def _print_summary(manifest: dict[str, object], *, output: Path | None = None) -> None:
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, dict)
    prefix = f" output={output.expanduser().resolve()}" if output is not None else ""
    print(
        "quiz_acceptance_fixtures=ok "
        f"version={manifest['fixture_version']} artifacts={len(artifacts)} "
        f"sha256={manifest['definition_sha256']}{prefix}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="生成固定验收文件")
    generate_parser.add_argument("--output", type=Path, required=True)
    generate_parser.add_argument(
        "--force",
        action="store_true",
        help="仅覆盖目标目录内由本工具拥有的固定文件",
    )

    check_parser = subparsers.add_parser("check", help="校验已有验收文件")
    check_parser.add_argument("--output", type=Path, required=True)
    manifest_parser = subparsers.add_parser(
        "check-manifest", help="校验仓库内冻结的 fixture manifest"
    )
    manifest_parser.add_argument("--manifest", type=Path, required=True)
    subparsers.add_parser("self-check", help="在临时目录生成并校验，不保留文件")
    subparsers.add_parser("print-manifest", help="输出冻结 manifest，不写文件")

    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            manifest = generate(args.output, force=args.force)
            _print_summary(manifest, output=args.output)
        elif args.command == "check":
            manifest = check(args.output)
            _print_summary(manifest, output=args.output)
        elif args.command == "check-manifest":
            manifest = check_manifest(args.manifest)
            _print_summary(manifest)
        elif args.command == "self-check":
            _print_summary(self_check())
        else:
            _artifacts, manifest = build_fixture_set()
            sys.stdout.buffer.write(_json_bytes(manifest, pretty=True))
    except FixtureError as exc:
        print(f"quiz acceptance fixture error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
