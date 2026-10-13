"""Verify the P1–P5 fixes end to end against the live API.

Each check maps to one confirmed fix:
  P1  special_requirements no longer poisons eligibility -> PASS appears, 保底 recovers
  P4  a subject set that is not exactly three is refused (422)
  P5  a rank beyond the 一段 population is accepted but warned about
  P3  two concurrent reorders -> exactly one 200 and one 409, never a 500

Creates throwaway profiles; clean up with
`python -m app.ingestion.cleanup_audit_data --write`.

Run: python scripts/verify_fixes.py
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/api/v1"
FAILURES: list[str] = []


def call(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        try:
            return exc.code, json.loads(payload)
        except json.JSONDecodeError:
            return exc.code, {"raw": payload[:200]}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -> {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def make_profile(subjects, rank, score=590):
    status, profile = call(
        "POST",
        "/profiles",
        {
            "cycle_id": 5,
            "exam_year": 2025,
            "rank": rank,
            "score": score,
            "subjects": subjects,
        },
    )
    return status, profile


def main() -> int:
    print("=" * 100)
    print("P4 — 选考科目必须恰好 3 门")
    print("=" * 100)
    for label, subjects, want in (
        ("2 门", ["PHY", "CHM"], 422),
        ("4 门", ["PHY", "CHM", "BIO", "GEO"], 422),
        ("1 门", ["PHY"], 422),
        ("3 门（正常）", ["PHY", "CHM", "BIO"], 201),
    ):
        status, _ = make_profile(subjects, 60000)
        check(f"{label} -> {status}", status == want, f"期望 {want}")

    print()
    print("=" * 100)
    print("P1 — 普通考生应得到 PASS 与保底，而不是全场 UNKNOWN")
    print("=" * 100)
    status, profile = make_profile(["PHY", "CHM", "BIO"], 60000)
    if status not in (200, 201):
        check("建立对照档案", False, f"HTTP {status}")
        return 1
    profile_id = profile["id"]
    status, pref = call(
        "POST",
        f"/profiles/{profile_id}/preferences",
        {"risk_mode": "BALANCED", "candidate_rank": 60000, "target_count": 96},
    )
    status, run = call("POST", "/recommendations", {"profile_id": profile_id, "preference_id": pref["id"]})
    session_id = (run.get("session") or {}).get("id")
    print(f"  会话 {session_id}   候选池 {run.get('pool_size')}")

    status, items = call("GET", f"/recommendations/{session_id}/items?limit=1")
    total = items.get("total", 0)
    print(f"  条目总数 {total}")

    from collections import Counter

    counts: Counter[str] = Counter()
    safe_eligible = 0
    offset = 0
    while offset < total:
        status, page = call("GET", f"/recommendations/{session_id}/items?limit=500&offset={offset}")
        rows = page.get("items", [])
        if not rows:
            break
        for item in rows:
            counts[item.get("eligibility") or "?"] += 1
            if item.get("eligibility") == "PASS" and (item.get("acs") or 0) >= 60:
                safe_eligible += 1
        offset += len(rows)
    print(f"  资格分布 {dict(counts)}")
    check("出现 PASS（此前为 0）", counts.get("PASS", 0) > 0, f"PASS {counts.get('PASS', 0)}")
    check(
        "保底计数恢复（PASS 且 ACS>=60）",
        safe_eligible > 0,
        f"{safe_eligible} 条此前为 0",
    )
    check(
        "UNKNOWN 不再占满全部",
        counts.get("UNKNOWN", 0) < total,
        f"UNKNOWN {counts.get('UNKNOWN', 0)} / {total}",
    )

    print()
    print("=" * 100)
    print("P5 — 位次超出达线人数应被接受但明确提示")
    print("=" * 100)
    status, big = make_profile(["PHY", "CHM", "BIO"], 600000, 441)
    check("超出位次仍可建档", status in (200, 201), f"HTTP {status}")
    if status in (200, 201):
        status, pref2 = call(
            "POST",
            f"/profiles/{big['id']}/preferences",
            {"risk_mode": "BALANCED", "candidate_rank": 600000, "target_count": 96},
        )
        status, run2 = call(
            "POST", "/recommendations", {"profile_id": big["id"], "preference_id": pref2["id"]}
        )
        warnings = run2.get("input_warnings") or []
        check("返回了位次警示", len(warnings) > 0, f"{len(warnings)} 条")
        for warning in warnings:
            print(f"      {warning}")

    print()
    print("=" * 100)
    print("P3 — 并发改动应恰好一个成功、一个 409，且绝不 500")
    print("=" * 100)
    # A rebuilt database has no schemes, so make one from the session created above.
    status, created = call("POST", "/schemes", {"recommendation_session_id": session_id})
    scheme_id = created.get("id") if status in (200, 201) else None
    check("建立测试方案", scheme_id is not None, f"HTTP {status}")
    if scheme_id is None:
        pass
    else:
        status, detail = call("GET", f"/schemes/{scheme_id}")
        version = detail.get("version_no", 1)
        ids = [i.get("recommendation_item_id") for i in detail.get("items", [])]
        swapped = list(ids)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        body = {
            "version": version,
            "items": [
                {"recommendation_item_id": i, "position": p}
                for p, i in enumerate(swapped, start=1)
            ],
        }
        results: list[int] = []
        lock = threading.Lock()

        def worker() -> None:
            outcome, _ = call("PATCH", f"/schemes/{scheme_id}/order", body)
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        print(f"  返回码 {sorted(results)}")
        check("没有 5xx", all(code < 500 for code in results), f"{sorted(results)}")
        check("恰好一个成功", results.count(200) == 1, f"200 × {results.count(200)}")
        check("另一个是 409", results.count(409) == 1, f"409 × {results.count(409)}")

    print()
    print("=" * 100)
    print(f"结论：{'全部通过' if not FAILURES else '存在失败项'}")
    print("=" * 100)
    for failure in FAILURES:
        print(f"  !! {failure}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
