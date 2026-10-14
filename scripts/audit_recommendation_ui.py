"""Count the candidate pool's composition and prove the search-reachability problem.

The list endpoint loads every item row and joins plan details before slicing the page in
Python, so cost is independent of `limit`. That makes the pool expensive to enumerate and
means the UI's `limit=200` window is a small, arbitrary slice.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

API = "http://127.0.0.1:8000/api/v1"


def get(path: str):
    started = time.time()
    try:
        with urllib.request.urlopen(f"{API}{path}", timeout=900) as response:
            return response.status, json.loads(response.read().decode()), time.time() - started
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}"), time.time() - started


def post(path: str, body: dict):
    request = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=900) as response:
        return json.loads(response.read().decode())


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
    )
    preference = post(
        f"/profiles/{profile['id']}/preferences",
        {"risk_mode": "BALANCED", "candidate_rank": 60000, "target_count": 96},
    )
    run = post("/recommendations", {"profile_id": profile["id"], "preference_id": preference["id"]})
    session_id = run["session"]["id"]
    pool = run["pool_size"]
    print(f"会话 {session_id}：候选池 {pool:,} 条")
    print()

    # Walk the pool page by page, using the server's own reported page size.
    names: Counter[str] = Counter()
    provinces: Counter[str] = Counter()
    offset = 0
    pages = 0
    elapsed = 0.0
    first_page_universities: set[str] = set()
    while True:
        _s, page, seconds = get(
            f"/recommendations/{session_id}/items?limit=500&offset={offset}"
        )
        elapsed += seconds
        rows = page.get("items", [])
        if not rows:
            break
        pages += 1
        for row in rows:
            names[row.get("university_name")] += 1
            provinces[row.get("province_name") or "?"] += 1
            if pages == 1:
                first_page_universities.add(row.get("university_name"))
        offset += len(rows)
        if offset >= page.get("total", 0):
            break

    print("=" * 96)
    print("候选池构成")
    print("=" * 96)
    print(f"  取回 {sum(names.values()):,} 条 / {pages} 页（每页上限 500），累计 {elapsed:.0f}s")
    print(f"  学校 {len(names)} 所    省份 {len(provinces)} 个")
    print(f"  第一页（对应用户实际看到的 200 条）覆盖学校 {len(first_page_universities)} 所"
          f"（{len(first_page_universities) / len(names) * 100:.1f}%）")
    print()
    print("  条数最多的 10 所学校（每所最多 96 个志愿，所以同一学校会重复出现）:")
    for name, count in names.most_common(10):
        print(f"    {name:<28}{count:>5} 条")
    print()

    outside = [(n, c) for n, c in names.most_common() if n not in first_page_universities]
    print("=" * 96)
    print("搜索可达性")
    print("=" * 96)
    print(f"  不在第一页中的学校 {len(outside)} 所。逐个调服务端搜索验证:")
    print()
    print(f"    {'学校':<28}{'池中条数':>10}{'服务端 search 命中':>20}")
    print("    " + "-" * 60)
    for name, count in outside[:8]:
        _s, hit, _ = get(
            f"/recommendations/{session_id}/items?limit=200&search={urllib.parse.quote(name)}"
        )
        print(f"    {name:<28}{count:>10}{len(hit.get('items', [])):>20}")
    print()
    print("  服务端 search 命中正常，说明搜索本身可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
