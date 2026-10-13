"""Break down which programmes cannot be mapped to a 门类, and what it costs.

79.3% of series names match the catalogue. This shows what the missing 20.7% actually is
(vocational 专科 majors, elite 试验班, new majors) and how many plans each group covers,
so the coverage number can be judged rather than just reported.

Run: python scripts/probe_major_unmatched.py
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.admission.models import AdmissionPlan, ProgramSeries  # noqa: E402

NEW_ROOT = Path(r"D:\BaiduNetdiskDownload\志愿高报资料")
CATALOGUE_FRAGMENT = "绝密报考-普通高等学校本科专业目录（2023年完整版）"

COL_DISCIPLINE, COL_CLASS, COL_CODE, COL_NAME = 2, 3, 5, 6

DISCIPLINES = {
    "哲学", "经济学", "法学", "教育学", "文学", "历史学", "理学",
    "工学", "农学", "医学", "军事学", "管理学", "艺术学", "交叉学科",
}


def load_catalogue_names() -> set[str]:
    paths = sorted(p for p in NEW_ROOT.rglob("*.xlsx") if CATALOGUE_FRAGMENT in p.name)
    frame = pd.read_excel(paths[0], sheet_name=0, header=None, dtype=str)
    names: set[str] = set()
    for index in range(len(frame)):
        for column in (COL_NAME, COL_CLASS):
            raw = frame.iat[index, column]
            if raw is None or str(raw).lower() == "nan":
                continue
            text = str(raw).replace("\n", "").strip()
            if text and text not in {"专业名称", "专业", "专业类", "概况"}:
                names.add(text)
    return names


def classify(name: str) -> str:
    if name.endswith("试验班") or name.endswith("实验班"):
        return "试验班（跨学科精英班，无单一门类）"
    if re.search(r"(技术|工程技术|应用技术|管理与应用)$", name):
        return "疑似专科/职业本科专业"
    if name.endswith("类"):
        return "专业类名称未收录"
    return "其他（新专业或校自设方向）"


def main() -> int:
    catalogue = load_catalogue_names()
    print(f"目录收录名称 {len(catalogue):,} 条")
    print()

    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        series = session.execute(
            select(
                ProgramSeries.id,
                ProgramSeries.canonical_name,
                func.count(AdmissionPlan.id),
            )
            .join(AdmissionPlan, AdmissionPlan.series_id == ProgramSeries.id)
            .where(AdmissionPlan.is_demo.is_(False))
            .group_by(ProgramSeries.id, ProgramSeries.canonical_name)
        ).all()

    total_series = len(series)
    total_plans = sum(count for _sid, _name, count in series)
    matched = [(sid, name, count) for sid, name, count in series if name in catalogue]
    unmatched = [(sid, name, count) for sid, name, count in series if name not in catalogue]

    print(f"专业序列 {total_series:,}（对应计划 {total_plans:,} 条）")
    print(f"  可映射到门类 {len(matched):,}  ({sum(c for _s, _n, c in matched):,} 条计划)")
    print(f"  不可映射     {len(unmatched):,}  ({sum(c for _s, _n, c in unmatched):,} 条计划)")
    print()

    groups = Counter(classify(name) for _sid, name, _count in unmatched)
    print("=" * 100)
    print("未匹配的构成")
    print("=" * 100)
    for label, count in groups.most_common():
        plans = sum(c for _s, n, c in unmatched if classify(n) == label)
        print(f"  {label:<32} {count:>4} 个序列 / {plans:>6,} 条计划")
    print()

    print("各类样例:")
    for label in groups:
        samples = [n for _s, n, _c in unmatched if classify(n) == label][:12]
        print(f"  {label}: {samples}")
    print()

    # Would a name-prefix fallback for 试验班 be defensible?
    trials = [(n, c) for _s, n, c in unmatched if classify(n).startswith("试验班")]
    print(f"试验班类共 {len(trials)} 个序列 / {sum(c for _n, c in trials):,} 条计划")
    print("  这些是各校自设的跨学科精英班，官方目录中确实没有对应门类，")
    print("  强行归入某一门类属于猜测。")
    print()

    # How much of the *undergraduate* remainder is genuinely unmappable?
    still = [n for _s, n, _c in unmatched if not classify(n).startswith(("试验班", "疑似"))]
    print(f"扣除试验班与疑似专科后，剩余 {len(still)} 个序列: {still[:20]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
