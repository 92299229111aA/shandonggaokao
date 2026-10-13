"""Map every stored major to its 门类/专业类 using the structured MOE catalogue.

Why this exists: the Shandong plan files carry a *per-school* major sequence number
(`'E1'`, `'17'`) rather than an MOE major code, and no 门类 column at all. Consequently
`reference.majors.discipline_code` was populated for only 17 of 1,119 rows and
`program_series.major_id` was NULL everywhere — which silently disabled the 学科 filter in
the 35%-weighted 专业匹配 preference dimension.

Names are the only available join key. The structured catalogue
「绝密报考-普通高等学校本科专业目录（2023年完整版）.xlsx」 supplies
专业名称 → 门类 / 专业类 / 专业代码 for 792 programmes, and it is a real spreadsheet
rather than a PDF, so no text-extraction guessing is involved.

What this does NOT do: invent a 门类 for programmes the catalogue does not contain.
试验班 (cross-disciplinary honours classes), 专科/职业本科 names and brand-new majors stay
unmapped and are reported — a wrong 门类 would corrupt the preference score, which is
worse than a missing one.

Provenance: the catalogue workbook is a third-party compilation of an MOE document, so
its source document is recorded as THIRD_PARTY, not OFFICIAL.

Run:
    python -m app.ingestion.enrich_majors                 # 演练
    python -m app.ingestion.enrich_majors --write
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select

from app.admission.models import AdmissionPlan, ProgramSeries, SourceDocument
from app.db.session import session_scope
from app.ingestion.data_root import SourceLayout, resolve_root
from app.reference.models import Major

# The full catalogue only. The similarly named 「…以及一级学科顶尖院校清单」 variant has a
# different column layout and lacks 军事学, so it must not be picked up.
CATALOGUE_FRAGMENT = "普通高等学校本科专业目录（2023年完整版）"

# Column positions in the catalogue sheet; row 1 holds the header.
COL_DISCIPLINE = 2  # 门类
COL_CLASS = 3  # 专业类
COL_CODE = 5  # 专业代码
COL_NAME = 6  # 专业名称
COL_DEGREE = 7  # 学位授予门类

DISCIPLINE_CODE_BY_NAME: dict[str, str] = {
    "哲学": "01", "经济学": "02", "法学": "03", "教育学": "04", "文学": "05",
    "历史学": "06", "理学": "07", "工学": "08", "农学": "09", "医学": "10",
    "军事学": "11", "管理学": "12", "艺术学": "13", "交叉学科": "14",
}


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    discipline_code: str
    discipline_name: str
    category_code: str
    category_name: str
    major_code: str
    degree_awarded: str | None


@dataclass
class EnrichReport:
    catalogue_entries: int = 0
    majors_total: int = 0
    majors_filled: int = 0
    majors_unmatched: int = 0
    series_total: int = 0
    series_linked: int = 0
    series_unmatched: int = 0
    plans_total: int = 0
    plans_with_discipline: int = 0
    unmatched_samples: list[str] = field(default_factory=list)

    @property
    def plan_coverage(self) -> float:
        return (
            self.plans_with_discipline / self.plans_total * 100 if self.plans_total else 0.0
        )

    def lines(self) -> list[str]:
        return [
            f"目录条目 {self.catalogue_entries:,}",
            f"专业补充门类 {self.majors_filled:,} / {self.majors_total:,}"
            f"（未匹配 {self.majors_unmatched:,}）",
            f"专业序列建立关联 {self.series_linked:,} / {self.series_total:,}"
            f"（未匹配 {self.series_unmatched:,}）",
            f"招生计划可获得门类 {self.plans_with_discipline:,} / {self.plans_total:,}"
            f"（{self.plan_coverage:.1f}%）",
        ]


def find_catalogue(layout: SourceLayout) -> Path:
    """Locate the structured catalogue workbook in either supplied material set."""
    roots = [layout.root]
    sibling = layout.root.parent / "志愿高报资料"
    if sibling.exists():
        roots.append(sibling)
    candidates: list[Path] = []
    for root in roots:
        if root.exists():
            candidates += [
                p for p in root.rglob("*.xlsx") if CATALOGUE_FRAGMENT in p.name
            ]
    if not candidates:
        raise FileNotFoundError(
            f"找不到「{CATALOGUE_FRAGMENT}…」工作簿。"
            "该文件为结构化（Excel）版专业目录，见 docs/DATA_IMPORT.md。"
        )
    # Prefer the shallowest copy: the same workbook is duplicated into every province folder.
    return sorted(candidates, key=lambda p: (len(p.parts), p.name))[0]


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\n", "").replace("\u3000", "").strip()
    return None if not text or text.lower() == "nan" else text


def parse_catalogue(path: Path) -> dict[str, CatalogueEntry]:
    """Build 专业名称 → entry, and additionally 专业类名称 → entry.

    The sheet repeats 门类 only on the first row of each group, so it is carried forward.
    Rows whose 门类 is a number are sub-header rows and are skipped.

    A 专业类 such as 计算机类 never appears as its own row — it is a *value* in the
    专业类 column, shared by all of its programmes. The plan files, however, do list
    「计算机类」 as a 志愿单位, so class names must be resolvable too. Their entry is
    built from the first programme that carries the class, with 专业代码 truncated to the
    4-digit class prefix.
    """
    frame = pd.read_excel(path, sheet_name=0, header=None, dtype=str)
    by_name: dict[str, CatalogueEntry] = {}
    current_discipline: str | None = None

    for index in range(len(frame)):
        discipline = _text(frame.iat[index, COL_DISCIPLINE])
        if discipline in DISCIPLINE_CODE_BY_NAME:
            current_discipline = discipline
        name = _text(frame.iat[index, COL_NAME])
        if not name or name in {"专业名称", "专业"} or current_discipline is None:
            continue

        code = _text(frame.iat[index, COL_CODE]) or ""
        class_name = _text(frame.iat[index, COL_CLASS]) or ""
        entry = CatalogueEntry(
            discipline_code=DISCIPLINE_CODE_BY_NAME[current_discipline],
            discipline_name=current_discipline,
            category_code=code[:4] if len(code) >= 4 else "",
            category_name=class_name,
            major_code=code,
            degree_awarded=_text(frame.iat[index, COL_DEGREE]),
        )
        by_name.setdefault(name, entry)

        if class_name.endswith("类"):
            by_name.setdefault(
                class_name,
                CatalogueEntry(
                    discipline_code=entry.discipline_code,
                    discipline_name=entry.discipline_name,
                    category_code=entry.category_code,
                    category_name=class_name,
                    major_code="",  # a class has no single programme code
                    degree_awarded=None,
                ),
            )

    return by_name


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="真正写库（默认演练）")
    parser.add_argument("--data-root", default=None, help="资料根目录")
    args = parser.parse_args(argv)

    layout = resolve_root(args.data_root)
    try:
        path = find_catalogue(layout)
    except FileNotFoundError as exc:
        print(exc)
        return 2

    print("=" * 100)
    print("专业门类映射：教育部本科专业目录（结构化 Excel）")
    print("=" * 100)
    print(f"目录文件 : {path}")
    catalogue = parse_catalogue(path)
    print(f"解析条目 : {len(catalogue):,}")
    covered = sorted({e.discipline_name for e in catalogue.values()})
    print(f"覆盖门类 : {len(covered)} 个 -> {covered}")
    print()

    report = EnrichReport(catalogue_entries=len(catalogue))

    with session_scope() as session:
        # ---- 1. fill 门类/专业类 on the majors we store ----
        majors = list(session.execute(select(Major)).scalars())
        report.majors_total = len(majors)
        for major in majors:
            entry = catalogue.get(major.name)
            if entry is None:
                report.majors_unmatched += 1
                if len(report.unmatched_samples) < 20:
                    report.unmatched_samples.append(major.name)
                continue
            # Only fill blanks: never overwrite a value that is already there.
            if not major.discipline_code:
                major.discipline_code = entry.discipline_code
                major.discipline_name = entry.discipline_name
                report.majors_filled += 1
            if not major.category_code and entry.category_code:
                major.category_code = entry.category_code
                major.category_name = entry.category_name
            if not major.degree_awarded and entry.degree_awarded:
                major.degree_awarded = entry.degree_awarded
        session.flush()

        # ---- 2. link every series → a major carrying a discipline ----
        #
        # `reference.majors` holds one row per (school, major) pair, so a shared name such
        # as 计算机类 appears 15 times. Every occurrence was filled in step 1, so a plain
        # name → row map (first wins) is enough, and no series is left behind because its
        # particular duplicate happened to be skipped.
        name_to_major: dict[str, Major] = {}
        for major in majors:
            if major.discipline_code and major.name not in name_to_major:
                name_to_major[major.name] = major

        series_rows = list(session.execute(select(ProgramSeries)).scalars())
        report.series_total = len(series_rows)
        for series in series_rows:
            if series.major_id is not None:
                continue
            matched = name_to_major.get(series.canonical_name)
            if matched is None:
                report.series_unmatched += 1
                continue
            series.major_id = matched.id
            report.series_linked += 1
        session.flush()

        # ---- 3. how many plans now carry a 门类 ----
        report.plans_total = int(
            session.execute(
                select(func.count()).select_from(AdmissionPlan).where(AdmissionPlan.is_demo.is_(False))
            ).scalar_one()
        )
        report.plans_with_discipline = int(
            session.execute(
                select(func.count())
                .select_from(AdmissionPlan)
                .join(ProgramSeries, ProgramSeries.id == AdmissionPlan.series_id)
                .join(Major, Major.id == ProgramSeries.major_id)
                .where(AdmissionPlan.is_demo.is_(False), Major.discipline_code.is_not(None))
            ).scalar_one()
        )

        if args.write:
            checksum = _checksum(path)
            document = session.execute(
                select(SourceDocument).where(SourceDocument.checksum_sha256 == checksum)
            ).scalar_one_or_none()
            if document is None:
                session.add(
                    SourceDocument(
                        title=f"{path.name}（教育部本科专业目录，结构化整理版）",
                        publisher="教育部（经第三方整理为 Excel）",
                        archive_path=str(path),
                        checksum_sha256=checksum,
                        quality="THIRD_PARTY",
                        is_demo=False,
                        retrieved_at=datetime.now(),
                        notes=(
                            "教育部《普通高等学校本科专业目录》的第三方结构化整理版，"
                            "用于按专业名称映射门类与专业类。"
                            "整理过程可能存在偏差，如需权威口径请以教育部原文核对。"
                        ),
                    )
                )
            print("已写入数据库。")
        else:
            session.rollback()
            print("演练结束，未写库。加 --write 正式写入。")

    print()
    for line in report.lines():
        print(f"  - {line}")
    if report.unmatched_samples:
        print()
        print(f"  未匹配样例（保留为空，不猜测门类）: {report.unmatched_samples}")
    print()
    print("  未匹配的主要是：试验班（跨学科精英班，官方目录无单一门类）、")
    print("  专科/职业本科专业名、以及目录发布后新增的专业。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
