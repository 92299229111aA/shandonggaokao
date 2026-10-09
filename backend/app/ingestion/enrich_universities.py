"""Enrich the university reference table from the MOE registry.

`reference.universities` was populated from the plan workbooks, which carry the
enrolment code (A001 …) and province but leave city, 办学性质 and the MOE identifier
empty. The MOE registry 「全国普通高等学校名单（截至2024年6月20日）」 supplies exactly
those, and it is a genuine government document rather than the third-party compilation
the plan data came from — so it is recorded as quality='OFFICIAL'.

Matching is by exact school name after stripping a trailing parenthetical, because the
plan files append qualifiers such as 「北京大学医学部」 or 「国防科技大学(湖南公办)」.
Unmatched rows are reported, never guessed.

Run:
    python -m app.ingestion.enrich_universities                 # 演练
    python -m app.ingestion.enrich_universities --write
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

from app.admission.models import SourceDocument
from app.db.session import session_scope
from app.ingestion.data_root import SourceLayout, resolve_root
from app.reference.models import University

REGISTRY_NAME = "全国普通高等学校名单"

# The registry groups schools under headings like 「北京市（92所）」; those rows carry a
# province in column 0 and nothing else.
PROVINCE_CODE_BY_NAME: dict[str, str] = {
    "北京市": "11", "天津市": "12", "河北省": "13", "山西省": "14", "内蒙古自治区": "15",
    "辽宁省": "21", "吉林省": "22", "黑龙江省": "23", "上海市": "31", "江苏省": "32",
    "浙江省": "33", "安徽省": "34", "福建省": "35", "江西省": "36", "山东省": "37",
    "河南省": "41", "湖北省": "42", "湖南省": "43", "广东省": "44", "广西壮族自治区": "45",
    "海南省": "46", "重庆市": "50", "四川省": "51", "贵州省": "52", "云南省": "53",
    "西藏自治区": "54", "陕西省": "61", "甘肃省": "62", "青海省": "63",
    "宁夏回族自治区": "64", "新疆维吾尔自治区": "65",
}

# 办学层次 → is_public. The registry states 本科 / 专科 and, for private schools,
# 民办 in the 备注 column.
PRIVATE_HINTS = ("民办", "中外合作办学", "内地与港澳台", "境外高校")


def base_name(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("（", "(").replace("）", ")")
    return text.split("(")[0].strip()


@dataclass
class RegistryRow:
    name: str
    moe_code: str | None
    authority: str | None
    city_name: str | None
    level: str | None
    remark: str | None
    province_name: str | None = None


@dataclass
class EnrichReport:
    registry_rows: int = 0
    matched: int = 0
    city_filled: int = 0
    public_filled: int = 0
    remark_filled: int = 0
    province_filled: int = 0
    unmatched_sample: list[str] = field(default_factory=list)
    public_conflicts: list[str] = field(default_factory=list)
    database_total: int = 0

    def lines(self) -> list[str]:
        rate = self.matched / self.database_total * 100 if self.database_total else 0
        return [
            f"官方名单条目 {self.registry_rows:,}",
            f"匹配到本校院校库 {self.matched:,} / {self.database_total:,}（{rate:.1f}%）",
            f"补充所在省份 {self.province_filled:,}",
            f"补充所在城市 {self.city_filled:,}",
            f"补充办学性质 {self.public_filled:,}",
            f"补充备注（教育部标识码/主管部门/层次）{self.remark_filled:,}",
        ]


def find_registry(layout: SourceLayout) -> Path:
    """Locate the MOE registry workbook in either supplied material set."""
    candidates: list[Path] = []
    for root in (layout.root, layout.root.parent / "志愿高报资料"):
        if root.exists():
            candidates += [
                p for p in root.rglob("*.xls*") if REGISTRY_NAME in p.name
            ]
    if not candidates:
        raise FileNotFoundError(
            f"找不到「{REGISTRY_NAME}」工作簿。请用 --data-root 指定包含新资料的目录。"
        )
    return sorted(candidates, key=lambda p: len(p.parts))[0]


def read_registry(path: Path) -> list[RegistryRow]:
    """Parse the MOE registry.

    Column layout: 序号 | 学校名称 | 学校标识码 | 主管部门 | 所在地 | 办学层次 | 备注.
    `所在地` is a CITY (北京市 / 保定市 / 邯郸市), not a province — the province comes
    from the section heading that precedes each group. `备注` carries 民办 for private
    schools, which is the only place 办学性质 is stated.
    """
    frame = pd.read_excel(path, sheet_name=0, header=None, dtype=str, engine="xlrd")
    rows: list[RegistryRow] = []
    current_province: str | None = None
    for index in range(len(frame)):
        first = str(frame.iat[index, 0]).strip()
        if not first or first.lower() == "nan":
            continue
        if "（" in first or "(" in first:
            heading = first.split("（")[0].split("(")[0].strip()
            if heading in PROVINCE_CODE_BY_NAME:
                current_province = heading
                continue
        try:
            int(float(first))
        except ValueError:
            continue
        name = str(frame.iat[index, 1]).strip()
        if not name or name.lower() == "nan":
            continue
        rows.append(
            RegistryRow(
                name=name,
                moe_code=_clean(frame.iat[index, 2]),
                authority=_clean(frame.iat[index, 3]),
                city_name=_clean(frame.iat[index, 4]),
                level=_clean(frame.iat[index, 5]),
                remark=_clean(frame.iat[index, 6]),
                province_name=current_province,
            )
        )
    return rows


def _clean(value: object) -> str | None:
    text = "" if value is None else str(value).strip()
    return None if not text or text.lower() == "nan" else text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="真正写库（默认演练）")
    parser.add_argument("--data-root", default=None, help="资料根目录")
    args = parser.parse_args(argv)

    layout = resolve_root(args.data_root)
    try:
        path = find_registry(layout)
    except FileNotFoundError as exc:
        print(exc)
        return 2

    print("=" * 100)
    print("院校库补充：教育部《全国普通高等学校名单》")
    print("=" * 100)
    print(f"名单文件 : {path}")
    registry = read_registry(path)
    print(f"解析条目 : {len(registry):,}")

    # Index by exact name and by parenthetical-stripped name.
    by_name: dict[str, RegistryRow] = {}
    for row in registry:
        by_name.setdefault(row.name, row)
        by_name.setdefault(base_name(row.name), row)
    print(f"唯一校名 : {len(by_name):,}")
    print()

    report = EnrichReport(registry_rows=len(registry))

    with session_scope() as session:
        universities = list(session.execute(select(University)).scalars())
        report.database_total = len(universities)
        print(f"本校院校库: {len(universities):,} 所")

        for university in universities:
            match = by_name.get(university.name) or by_name.get(base_name(university.name))
            if match is None:
                if len(report.unmatched_sample) < 20:
                    report.unmatched_sample.append(university.name)
                continue
            report.matched += 1

            # The enrolment data only ever gives a province; the registry adds the city.
            if match.province_name:
                code = PROVINCE_CODE_BY_NAME.get(match.province_name)
                if code and university.province_code in {"", "37"} and university.province_name in {"", "未知"}:
                    university.province_code = code
                    university.province_name = match.province_name
                    report.province_filled += 1

            if not university.city_name and match.city_name:
                university.city_name = match.city_name
                report.city_filled += 1

            # 办学性质 is already known from the enrolment compilation; the registry is a
            # government source, so disagreements are reported rather than silently applied.
            registry_public = "民办" not in (match.remark or "")
            if university.is_public is None and match.level:
                university.is_public = registry_public
                report.public_filled += 1
            elif university.is_public is not None and university.is_public != registry_public:
                if len(report.public_conflicts) < 10:
                    report.public_conflicts.append(
                        f"{university.name}: 纳入数据={'公办' if university.is_public else '民办'} "
                        f"教育部名单={'公办' if registry_public else '民办'}"
                    )

            if not university.remark and match.moe_code:
                pieces = [f"教育部标识码 {match.moe_code}"]
                if match.authority:
                    pieces.append(f"主管部门 {match.authority}")
                if match.level:
                    pieces.append(f"办学层次 {match.level}")
                if match.remark:
                    pieces.append(match.remark)
                university.remark = "；".join(pieces)
                report.remark_filled += 1

        if args.write:
            document = session.execute(
                select(SourceDocument).where(SourceDocument.checksum_sha256 == _checksum(path))
            ).scalar_one_or_none()
            if document is None:
                session.add(
                    SourceDocument(
                        title=f"{path.name}（教育部全国普通高等学校名单）",
                        publisher="中华人民共和国教育部",
                        archive_path=str(path),
                        checksum_sha256=_checksum(path),
                        quality="OFFICIAL",
                        is_demo=False,
                        retrieved_at=datetime.now(),
                        notes="教育部官方发布的全国高等学校名单，用于补充院校所在地与办学性质。",
                    )
                )
            print("\n已写入数据库。")
        else:
            session.rollback()
            print("\n演练结束，未写库。加 --write 正式写入。")

    print()
    for line in report.lines():
        print(f"  - {line}")
    if report.unmatched_sample:
        print(f"  ! 未匹配样例: {report.unmatched_sample}")
    if report.public_conflicts:
        print(f"  ! 公办/民办标记与教育部名单不一致（{len(report.public_conflicts)} 所示例）:")
        for conflict in report.public_conflicts:
            print(f"      {conflict}")
    else:
        print("  - 公办/民办标记与教育部名单无冲突")
    print()
    print("数据库当前院校数: ", end="")
    with session_scope() as session:
        print(session.execute(select(func.count()).select_from(University)).scalar_one())
    return 0


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    sys.exit(main())
