"""Check whether two accepted-but-questionable inputs actually cause wrong output.

The boundary audit found that the API accepts:
  A. a profile with only TWO 选考科目 (Shandong requires exactly three), and
  B. a rank of 600,000 when the 2025 一段 population is 333,469.

Both returned 201 rather than being rejected. Whether that is a defect depends on what
the pipeline then produces, so this drives both cases end to end and inspects the result
instead of assuming.

Run: python scripts/audit_edge_inputs.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.session import session_scope  # noqa: E402

API = "http://127.0.0.1:8000/api/v1"


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        try:
            return exc.code, json.loads(payload)
        except json.JSONDecodeError:
            return exc.code, {"raw": payload[:200]}


def run_case(label: str, profile_body: dict, preference_body: dict) -> None:
    print("=" * 100)
    print(label)
    print("=" * 100)
    status, profile = call("POST", "/profiles", profile_body)
    print(f"  建档: HTTP {status}")
    if status not in (200, 201):
        print(f"  被拒绝: {json.dumps(profile, ensure_ascii=False)[:220]}")
        print()
        return

    profile_id = profile["id"]
    status, pref = call("POST", f"/profiles/{profile_id}/preferences", preference_body)
    print(f"  偏好: HTTP {status}")
    if status not in (200, 201):
        print(f"  被拒绝: {json.dumps(pref, ensure_ascii=False)[:220]}")
        print()
        return

    status, run = call("POST", "/recommendations", {
        "profile_id": profile_id, "preference_id": pref["id"],
    })
    print(f"  推荐: HTTP {status}")
    if status not in (200, 201):
        print(f"  被拒绝: {json.dumps(run, ensure_ascii=False)[:220]}")
        print()
        return

    stats = run.get("stats", {})
    print(f"    扫描 {stats.get('plans_scanned')}  资格FAIL {stats.get('eligibility_fail')}"
          f"  UNKNOWN {stats.get('eligibility_unknown')}  候选 {stats.get('pooled')}")
    notes = stats.get("notes") or []
    for note in notes:
        print(f"    备注: {note}")

    session_id = run.get("id")
    if session_id:
        status, items = call("GET", f"/recommendations/{session_id}/items?limit=3")
        shown = items.get("items", [])[:3]
        print(f"    条目 {items.get('total')} 条，抽样:")
        for item in shown:
            print(f"      ACS={item.get('acs')}  资格={item.get('eligibility')}"
                  f"  可信度={item.get('confidence')}")
    print()


def main() -> int:
    with session_scope() as session:
        population = session.execute(
            text(
                "select ranked_candidates from admission.exam_year_stats "
                "where exam_year = 2025 and program_level = 'UNDERGRAD'"
            )
        ).scalar_one()
    print(f"2025 年普通类一段达线人数（位次百分位的分母）= {population:,}")
    print()

    run_case(
        "A. 只选 2 门选考科目（山东要求恰好 3 门）",
        {"cycle_id": 5, "exam_year": 2025, "rank": 60000, "score": 590,
         "subjects": ["PHY", "CHM"]},
        {"risk_mode": "BALANCED", "candidate_rank": 60000, "target_count": 96},
    )

    run_case(
        f"B. 位次 {600000:,} 超出全省达线人数 {population:,}",
        {"cycle_id": 5, "exam_year": 2025, "rank": 600000, "score": 441,
         "subjects": ["PHY", "CHM", "BIO"]},
        {"risk_mode": "BALANCED", "candidate_rank": 600000, "target_count": 96},
    )

    print("=" * 100)
    print("判断要点")
    print("=" * 100)
    print("  A. 若 2 科档案仍能产出大量候选且未提示科目不足，则选科校验存在缺口：")
    print("     真实考生的科目数由政策固定为 3，2 科档案的资格判断没有意义。")
    print("  B. 位次超出全省人数时，位次百分位 q_c = rank / population > 1，")
    print("     会让所有历史项都显得「比考生差」，ACS 系统性偏高。")
    print("     需要确认是否有校验或提示。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
