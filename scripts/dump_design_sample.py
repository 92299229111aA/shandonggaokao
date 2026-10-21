"""Pull a real recommendation result out of the running system for the design mockup.

The design draft must contain plausible Shandong data, not invented rows: the whole point of
the review is to judge whether the layout holds up under real university names, real major
labels and real ACS spreads. This dumps one honest session to JSON so the mockup can quote
it verbatim.

Output: docs/design/_sample-data.json   (gitignored, regenerated on demand)
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8000/api/v1"
OUT = Path(__file__).resolve().parents[1] / "docs" / "design" / "_sample-data.json"


def call(path: str, payload: dict | None = None, method: str = "GET") -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(f"{API}{path}", data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=1800) as response:
        return json.loads(response.read().decode())


def main() -> int:
    profile = call(
        "/profiles",
        {
            "cycle_id": 5,
            "exam_year": 2025,
            "rank": 60000,
            "score": 590,
            "subjects": ["PHY", "CHM", "BIO"],
        },
        "POST",
    )
    preference = call(
        f"/profiles/{profile['id']}/preferences",
        {"risk_mode": "BALANCED", "candidate_rank": 60000, "target_count": 96},
        "POST",
    )
    run = call(
        "/recommendations",
        {"profile_id": profile["id"], "preference_id": preference["id"]},
        "POST",
    )
    session_id = run["session"]["id"]
    scheme = call(
        "/schemes", {"recommendation_session_id": session_id}, "POST"
    )

    # Pool page: what the editor's left column actually shows.
    pool = call(
        f"/recommendations/{session_id}/items"
        f"?limit=12&sort=RISK_MIX&exclude_scheme_id={scheme['id']}"
    )
    # Grouped view: one school per row.
    groups = call(
        f"/recommendations/{session_id}/university-groups"
        f"?limit=8&exclude_scheme_id={scheme['id']}"
    )
    # A school expanded into its majors.
    first_university = groups["items"][0]["university_id"]
    expanded = call(
        f"/recommendations/{session_id}/items"
        f"?limit=6&university_id={first_university}"
        f"&exclude_scheme_id={scheme['id']}&sort=RISK_MIX"
    )
    health = call(f"/schemes/{scheme['id']}/health-check", {}, "POST")

    payload = {
        "session_id": session_id,
        "scheme_id": scheme["id"],
        "profile": {
            "rank": 60000,
            "score": 590,
            "subjects": ["物理", "化学", "生物"],
        },
        "pool_size": run["pool_size"],
        "risk_distribution": run["risk_distribution"],
        "bucket_counts": pool.get("bucket_counts"),
        "pool_items": pool["items"],
        "scheme": {
            "target_count": scheme["target_count"],
            "item_count": len(scheme["items"]),
            "items": scheme["items"][:14],
            "bucket_totals": _bucket_totals(scheme["items"]),
        },
        "groups": groups["items"],
        "expanded": expanded["items"],
        "group_total": groups["total"],
        "health": {
            "score": health.get("score"),
            "errors": sum(1 for i in health.get("issues", []) if i["severity"] == "ERROR"),
            "warnings": sum(1 for i in health.get("issues", []) if i["severity"] == "WARNING"),
            "infos": sum(1 for i in health.get("issues", []) if i["severity"] == "INFO"),
            "issues": health.get("issues", [])[:8],
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已写出 {OUT}")
    print(f"  候选池 {payload['pool_size']:,} 条，院校 {payload['group_total']:,} 所")
    print(f"  方案 {payload['scheme']['item_count']} 条，健康分 {payload['health']['score']}")
    print(f"  风险分布 {json.dumps(payload['risk_distribution'], ensure_ascii=False)}")
    return 0


def _bucket_totals(items: list[dict]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for item in items:
        bucket = item.get("risk_bucket") or ""
        if bucket:
            totals[bucket] = totals.get(bucket, 0) + 1
    return totals


if __name__ == "__main__":
    raise SystemExit(main())
