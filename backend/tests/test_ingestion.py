"""Ingestion contract tests: the guards that keep DEMO out of real decisions."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.ingestion.etl import RawArchive, ValidationReport, file_checksum


def test_file_checksum_matches_hashlib(tmp_path: Path) -> None:
    target = tmp_path / "plans.csv"
    payload = "专业,学校\n计算机类,演示大学\n".encode()
    target.write_bytes(payload)
    assert file_checksum(target) == hashlib.sha256(payload).hexdigest()


def test_raw_archive_is_demo_by_default_only_when_asked(tmp_path: Path) -> None:
    target = tmp_path / "x.csv"
    target.write_text("a,b\n", encoding="utf-8")
    real = RawArchive(path=target, title="official", publisher="考试院", url=None, quality="OFFICIAL")
    demo = RawArchive(
        path=target, title="demo", publisher=None, url=None, quality="DEMO", is_demo=True
    )
    assert real.is_demo is False
    assert demo.is_demo is True
    assert real.checksum == demo.checksum, "同一文件的校验和必须一致，避免重复入库"


def test_validation_report_error_blocks_publish() -> None:
    report = ValidationReport(rows_parsed=10)
    report.warn("MISSING_TUITION", "缺少学费，按 0 处理", row=3)
    assert report.ok is True, "警告不应阻止发布"

    report.error("DUPLICATE_SERIES", "同一计划重复出现", row=4)
    assert report.ok is False
    assert report.rows_rejected == 1


def test_validation_report_tracks_unknowns() -> None:
    report = ValidationReport(rows_parsed=5)
    report.unknown("MEDICAL_TEXT", "体检要求无法解析，需人工核对", row=2)
    assert report.unknowns == 1
    assert report.ok is True, "无法解析应标 UNKNOWN 而不是直接失败"
    assert any(issue.code == "UNKNOWN_MEDICAL_TEXT" for issue in report.issues)
