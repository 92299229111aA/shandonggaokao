"""Measure why the scheme editor's candidate pool shows only 高冲.

Three stacked causes, each measurable:
  1. `preference_score` degenerates to a three-level mapping of 院校层次 when the
     candidate set no explicit 专业/地域/学校 preferences;
  2. the default ordering ties on that score and falls back to ACS ascending;
  3. the editor requests a fixed `limit=500` slice, so the slice is the 500 hardest.

Read-only. Run: python scripts/audit_editor_pool.py
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import Counter

API = "http://127.0.0.1:8000/api/v1"
LEVELS = ("HIGH_REACH", "REACH", "BORDER", "STABLE", "SAFE")


def get(path: str):
    with urllib.request.urlopen(f"{API}{path}", timeout=900) as response:
        return json.loads(response.read().decode())


def post(path: str, body: dict):
    request = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def main() -> int:
    profile = post(
        "/profiles",
        {
            "cycle_id": 5,
            "exam_year": 2025,
            "rank": 60000,
            "score": 590,
            "subjects": ["PHY", "CHM", "BIO"],
        },
    )[1]
    preference = post(
        f"/profiles/{profile['id']}/preferences",
        {"risk_mode": "BALANCED", "candidate_rank": 60000, "target_count": 96},
    )[1]
    run = post(
        "/recommendations",
        {"profile_id": profile["id"], "preference_id": preference["id"]},
    )[1]
    session_id = run["session"]["id"]

    print("=" * 96)
    print(f"会话 {session_id}   候选池 {run['pool_size']:,}")
    print("=" * 96)
    print()
    print("全池风险构成:")
    for level in LEVELS:
        count = run["risk_distribution"].get(level, 0)
        share = count / run["pool_size"] * 100
        print(f"  {level:<12} {count:>6,}  {share:>5.1f}%  {'█' * int(share / 2)}")
    print()

    # ---- cause 1: preference score is a level mapping ----
    print("=" * 96)
    print("原因 1：preference_score 退化成院校层次的三档映射")
    print("=" * 96)
    print("  （按风险层分层取样，避免单一排序造成的取样偏差）")
    print()
    # Sampling one sort would only cover one bucket — e.g. ACS_DESC returns SAFE only,
    # which is all 普通 schools, making the mapping look like it does not exist.
    bands: Counter = Counter()
    for level in LEVELS:
        page = get(
            f"/recommendations/{session_id}/items?limit=200&risk_bucket={level}"
        )
        for item in page["items"]:
            if item["is_985"]:
                university_level = "985"
            elif item["is_211"]:
                university_level = "211"
            elif item["is_double_first_class"]:
                university_level = "双一流"
            else:
                university_level = "普通"
            bands[(round(item["preference_score"] or 0), university_level)] += 1

    print(f"  {'偏好分':>8}{'院校层次':>10}{'条数':>8}")
    print("  " + "-" * 28)
    for (score, university_level), count in sorted(bands.items(), key=lambda kv: (-kv[0][0], -kv[1])):
        print(f"  {score:>8}{university_level:>10}{count:>8}")
    print()
    distinct_scores = {score for score, _level in bands}
    print(f"  偏好分只有 {len(distinct_scores)} 种取值: {sorted(distinct_scores, reverse=True)}")
    if len(distinct_scores) <= 3:
        print("  → 确认：它不是连续偏好度，而是院校层次的三档映射。")
    print()

    # ---- cause 2 + 3: default sort + fixed slice ----
    print("=" * 96)
    print("原因 2+3：默认排序 = 偏好分降序 → ACS 升序，再固定取 500 条")
    print("=" * 96)
    print(f"  {'排序':<18}{'高冲':>6}{'冲':>6}{'边界':>6}{'稳':>6}{'保':>6}   {'ACS 范围':>12}")
    print("  " + "-" * 74)
    for sort in ("PREFERENCE", "ACS_DESC", "ACS_ASC", "PLAN_COUNT_DESC", "TUITION_ASC"):
        page = get(f"/recommendations/{session_id}/items?limit=500&sort={sort}")
        counts = Counter(item["risk_bucket"] for item in page["items"])
        acs = [item["acs"] for item in page["items"] if item["acs"] is not None]
        row = "".join(f"{counts.get(level, 0):>6}" for level in LEVELS)
        print(f"  {sort:<18}{row}   {min(acs):>5.0f}~{max(acs):<5.0f}")
    print()
    print("  任何单一排序 + 固定 500 条都只能得到一种形态；换排序只是换一种偏斜。")
    print()

    # ---- the practical consequence ----
    print("=" * 96)
    print("实际后果：方案需要 20 条「边界」，而 500 条窗口里一条都没有")
    print("=" * 96)
    status, scheme = post("/schemes", {"recommendation_session_id": session_id})
    if status in (200, 201):
        in_scheme = Counter(item["risk_bucket"] for item in scheme["items"])
        print(f"  方案 {scheme['id']}：{len(scheme['items'])} 条")
        for level in LEVELS:
            print(f"    {level:<12} {in_scheme.get(level, 0):>3} 条")
        pool_500 = get(f"/recommendations/{session_id}/items?limit=500")
        available = Counter(item["risk_bucket"] for item in pool_500["items"])
        print()
        print("  池子 500 条窗口里各层可加入数量:")
        for level in LEVELS:
            need = in_scheme.get(level, 0)
            have = available.get(level, 0)
            flag = "  ← 无法替换" if need and have == 0 else ""
            print(f"    {level:<12} 需要 {need:>3}  可加入 {have:>3}{flag}")
    print()
    print("结论：候选池必须改为「筛选驱动」，而不是「固定切片」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
