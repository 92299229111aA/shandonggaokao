"""Build a real 96-item scheme through the API, for the health-check baseline.

`snapshot_run` rolls its session back, so it leaves no scheme to run the health check
against. This drives the product path instead (profile -> preference -> recommendation ->
scheme) and prints the ids, so `ops` scripts and the health-check baseline measure a
scheme a user could actually have.

Run: python scripts/make_real_scheme.py [rank]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

API = "http://127.0.0.1:8000/api/v1"


def call(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        try:
            return exc.code, json.loads(payload)
        except json.JSONDecodeError:
            return exc.code, {"raw": payload[:300]}


def main() -> int:
    rank = int(sys.argv[1]) if len(sys.argv) > 1 else 60_000

    cycles = call("GET", "/meta/admission-cycles")
    cycle_id = 5
    if cycles[0] == 200 and isinstance(cycles[1], list) and cycles[1]:
        newest = max(cycles[1], key=lambda c: c.get("exam_year", 0))
        cycle_id = newest["id"]
    print(f"周期 {cycle_id}")

    status, profile = call(
        "POST",
        "/profiles",
        {
            "cycle_id": cycle_id,
            "exam_year": 2025,
            "rank": rank,
            "score": 590,
            "subjects": ["PHY", "CHM", "BIO"],
            "foreign_language": "ENG",
        },
    )
    if status not in (200, 201):
        print(f"建档失败 HTTP {status}: {profile}")
        return 1
    print(f"档案 {profile['id']}  位次 {rank:,}")

    status, preference = call(
        "POST",
        f"/profiles/{profile['id']}/preferences",
        {"risk_mode": "BALANCED", "candidate_rank": rank, "target_count": 96},
    )
    if status not in (200, 201):
        print(f"建偏好失败 HTTP {status}: {preference}")
        return 1
    print(f"偏好 {preference['id']}")

    status, run = call(
        "POST", "/recommendations", {"profile_id": profile["id"], "preference_id": preference["id"]}
    )
    if status not in (200, 201):
        print(f"推荐失败 HTTP {status}: {run}")
        return 1
    session_id = run["session"]["id"]
    print(f"推荐会话 {session_id}  候选池 {run.get('pool_size')}")

    status, scheme = call("POST", "/schemes", {"recommendation_session_id": session_id})
    if status not in (200, 201):
        print(f"建方案失败 HTTP {status}: {scheme}")
        return 1
    print(f"方案 {scheme['id']}  版本 {scheme.get('version_no')}  条目 {len(scheme.get('items') or [])}")

    status, report = call(
        "POST", f"/schemes/{scheme['id']}/health-check", {"version": scheme.get("version_no", 1)}
    )
    print()
    if status in (200, 201):
        print(f"体检：健康分 {report.get('score')}")
        counts: dict[str, int] = {}
        for issue in report.get("issues", []):
            counts[issue.get("severity", "?")] = counts.get(issue.get("severity", "?"), 0) + 1
        print(f"  严重度分布 {counts}")
    else:
        print(f"体检失败 HTTP {status}: {report}")

    print()
    print(f"SCHEME_ID={scheme['id']}")
    print(f"VERSION_NO={scheme.get('version_no', 1)}")
    print(f"PROFILE_ID={profile['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
