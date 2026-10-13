"""Data-integrity audit over the loaded Shandong dataset.

Checks the invariants the tests do not cover: dangling references, orphan rows, impossible
values, duplicate natural keys, and fields silently left empty by a loader. These are the
failures that produce plausible-looking but wrong recommendations rather than errors.

Read-only. Run: python scripts/audit_data_integrity.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.session import session_scope  # noqa: E402

FINDINGS: list[tuple[str, str, str]] = []


def check(severity: str, title: str, detail: str) -> None:
    FINDINGS.append((severity, title, detail))


def scalar(session: Session, sql: str) -> int:
    return int(session.execute(text(sql)).scalar_one())


def main() -> int:
    with session_scope() as session:
        print("=" * 100)
        print("1) 引用完整性（悬空引用会让推荐引用到不存在的对象）")
        print("=" * 100)
        checks = [
            ("专业序列指向不存在的院校", """
                select count(*) from admission.program_series s
                left join reference.universities u on u.id = s.university_id
                where u.id is null"""),
            ("专业序列指向不存在的专业", """
                select count(*) from admission.program_series s
                left join reference.majors m on m.id = s.major_id
                where s.major_id is not null and m.id is null"""),
            ("招生计划指向不存在的序列", """
                select count(*) from admission.admission_plans p
                left join admission.program_series s on s.id = p.series_id
                where s.id is null"""),
            ("招生计划指向不存在的周期", """
                select count(*) from admission.admission_plans p
                left join admission.admission_cycles c on c.id = p.cycle_id
                where c.id is null"""),
            ("招生计划指向不存在的快照", """
                select count(*) from admission.admission_plans p
                left join admission.dataset_snapshots d on d.id = p.snapshot_id
                where d.id is null"""),
            ("历史录取指向不存在的序列", """
                select count(*) from admission.admission_outcomes o
                left join admission.program_series s on s.id = o.series_id
                where s.id is null"""),
            ("选科组指向不存在的规则集", """
                select count(*) from admission.subject_rule_groups g
                left join admission.subject_rule_sets s on s.id = g.rule_set_id
                where s.id is null"""),
            ("选科项指向不存在的组", """
                select count(*) from admission.subject_rule_items i
                left join admission.subject_rule_groups g on g.id = i.group_id
                where g.id is null"""),
            ("选科项科目代码不在 6 选 3 范围内", """
                select count(*) from admission.subject_rule_items
                where subject_code not in ('PHY','CHM','BIO','POL','HIS','GEO')"""),
            ("计划指向不存在的选科规则集", """
                select count(*) from admission.admission_plans p
                left join admission.subject_rule_sets s on s.id = p.subject_rule_set_id
                where p.subject_rule_set_id is not null and s.id is null"""),
        ]
        for title, sql in checks:
            count = scalar(session, sql)
            mark = "OK " if count == 0 else "!! "
            print(f"  {mark}{title:<34} {count:>8,}")
            if count:
                check("HIGH", title, f"{count:,} 行")

        print()
        print("=" * 100)
        print("2) 孤儿与死数据")
        print("=" * 100)
        orphans = [
            ("没有任何计划的专业序列", """
                select count(*) from admission.program_series s
                where not exists (select 1 from admission.admission_plans p where p.series_id = s.id)"""),
            ("没有任何历史数据的专业序列", """
                select count(*) from admission.program_series s
                where not exists (select 1 from admission.admission_outcomes o where o.series_id = s.id)"""),
            ("既无计划又无历史数据的序列", """
                select count(*) from admission.program_series s
                where not exists (select 1 from admission.admission_plans p where p.series_id = s.id)
                  and not exists (select 1 from admission.admission_outcomes o where o.series_id = s.id)"""),
            ("没有任何计划的院校", """
                select count(*) from reference.universities u
                where not exists (
                  select 1 from admission.program_series s
                  join admission.admission_plans p on p.series_id = s.id
                  where s.university_id = u.id)"""),
        ]
        for title, sql in orphans:
            count = scalar(session, sql)
            mark = "OK " if count == 0 else "-- "
            print(f"  {mark}{title:<34} {count:>8,}")
            if count:
                check("MEDIUM", title, f"{count:,} 行")

        print()
        print("=" * 100)
        print("3) 数值合理性")
        print("=" * 100)
        values = [
            ("计划数为 0 或负的计划", "select count(*) from admission.admission_plans where plan_count <= 0"),
            ("学费为负的计划", "select count(*) from admission.admission_plans where tuition_fee < 0"),
            ("最低位次 <= 0 的历史录取", "select count(*) from admission.admission_outcomes where min_rank <= 0"),
            ("最低分越界（不在 0~750）", "select count(*) from admission.admission_outcomes where min_score is not null and (min_score < 0 or min_score > 750)"),
            ("录取人数大于计划数 2 倍（可疑）", """
                select count(*) from admission.admission_outcomes
                where plan_count is not null and admitted_count is not null
                  and plan_count > 0 and admitted_count > plan_count * 2"""),
            ("一分一段累计人数非递增", """
                select count(*) from (
                  select exam_year, score, cumulative_rank,
                         lag(cumulative_rank) over (partition by exam_year order by score desc) as prev
                  from admission.score_rank_segments) t
                where prev is not null and cumulative_rank < prev"""),
            ("score 越界的一分一段", "select count(*) from admission.score_rank_segments where score < 0 or score > 750"),
            ("门类代码越界的专业（01~14）", """
                select count(*) from reference.majors
                where discipline_code is not null
                  and discipline_code !~ '^(0[1-9]|1[0-4])$'"""),
        ]
        for title, sql in values:
            count = scalar(session, sql)
            mark = "OK " if count == 0 else "!! "
            print(f"  {mark}{title:<34} {count:>8,}")
            if count:
                check("HIGH", title, f"{count:,} 行")

        print()
        print("=" * 100)
        print("4) 自然键唯一性（重复会产生同一志愿的多条候选）")
        print("=" * 100)
        keys = [
            ("重复的 series_key", """
                select count(*) from (
                  select series_key from admission.program_series
                  group by series_key having count(*) > 1) t"""),
            ("同一周期同一序列有多条同选科计划", """
                select count(*) from (
                  select cycle_id, series_id, subject_rule_set_id
                  from admission.admission_plans
                  group by 1,2,3 having count(*) > 1) t"""),
            ("同一序列同一年有多条录取记录", """
                select count(*) from (
                  select series_id, exam_year from admission.admission_outcomes
                  group by 1,2 having count(*) > 1) t"""),
            ("同学年同分数有多条一分一段", """
                select count(*) from (
                  select exam_year, score from admission.score_rank_segments
                  group by 1,2 having count(*) > 1) t"""),
            ("同一院校代码有多条院校记录", """
                select count(*) from (
                  select ministry_code from reference.universities
                  group by 1 having count(*) > 1) t"""),
        ]
        for title, sql in keys:
            count = scalar(session, sql)
            mark = "OK " if count == 0 else "!! "
            print(f"  {mark}{title:<34} {count:>8,}")
            if count:
                check("HIGH", title, f"{count:,} 组重复")

        print()
        print("=" * 100)
        print("5) 关键字段覆盖率（静默为空会让功能无声失效）")
        print("=" * 100)
        coverage = [
            ("院校 city_name", "select count(*) from reference.universities where city_name is not null",
             "select count(*) from reference.universities"),
            ("专业 discipline_code", "select count(*) from reference.majors where discipline_code is not null",
             "select count(*) from reference.majors"),
            ("序列 major_id", "select count(*) from admission.program_series where major_id is not null",
             "select count(*) from admission.program_series"),
            ("计划 subject_rule_set_id", "select count(*) from admission.admission_plans where subject_rule_set_id is not null",
             "select count(*) from admission.admission_plans"),
            ("计划 special_requirements", "select count(*) from admission.admission_plans where special_requirements is not null",
             "select count(*) from admission.admission_plans"),
            ("录取 min_score", "select count(*) from admission.admission_outcomes where min_score is not null",
             "select count(*) from admission.admission_outcomes"),
            ("录取 plan_count", "select count(*) from admission.admission_outcomes where plan_count is not null",
             "select count(*) from admission.admission_outcomes"),
        ]
        for title, filled_sql, total_sql in coverage:
            filled = scalar(session, filled_sql)
            total = scalar(session, total_sql)
            pct = filled / total * 100 if total else 0
            mark = "OK " if pct >= 95 else ("-- " if pct >= 50 else "!! ")
            print(f"  {mark}{title:<30} {filled:>7,} / {total:>7,}  ({pct:5.1f}%)")
            if pct < 95:
                check("MEDIUM" if pct >= 50 else "HIGH", f"{title} 覆盖率偏低", f"{pct:.1f}%")

        print()
        print("=" * 100)
        print("6) 快照与周期的一致性")
        print("=" * 100)
        snap = [
            ("已发布快照数量（应为 1）", "select count(*) from admission.dataset_snapshots where status = 'PUBLISHED'"),
            ("DEMO 快照残留", "select count(*) from admission.dataset_snapshots where is_demo"),
            ("DEMO 计划残留", "select count(*) from admission.admission_plans where is_demo"),
            ("计划引用的快照不是已发布快照", """
                select count(*) from admission.admission_plans p
                join admission.dataset_snapshots d on d.id = p.snapshot_id
                where d.status <> 'PUBLISHED'"""),
            ("激活的招生周期数量", "select count(*) from admission.admission_cycles where is_active"),
            ("缺少当年考生人数的周期", """
                select count(*) from admission.admission_cycles c
                where not exists (
                  select 1 from admission.exam_year_stats e
                  where e.exam_year = c.exam_year and e.program_level = 'UNDERGRAD')"""),
        ]
        for title, sql in snap:
            count = scalar(session, sql)
            print(f"  -- {title:<44} {count:>6,}")

    print()
    print("=" * 100)
    print("发现汇总")
    print("=" * 100)
    if not FINDINGS:
        print("  未发现数据完整性问题。")
    else:
        for severity, title, detail in FINDINGS:
            print(f"  [{severity:<6}] {title} —— {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
