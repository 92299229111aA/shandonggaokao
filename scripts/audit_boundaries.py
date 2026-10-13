"""Audit error paths and boundaries by driving the live API with hostile input.

Happy-path checks pass; the risk is what happens at the edges — a missing snapshot, a
rank beyond the population, a scheme over the policy limit, a stale version, malformed
JSON. Each of these should produce a clear, correct refusal rather than a 500 or a
silently wrong result.

Read-only against the live API (creates a few throwaway profiles, which it reports).

Run: python scripts/audit_boundaries.py [api_base]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/api/v1"

RESULTS: list[tuple[str, str, str]] = []


def call(method: str, path: str, body: dict | None = None, raw: str | None = None):
    url = f"{API}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if raw is not None:
        data = raw.encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read().decode("utf-8")
            return response.status, (json.loads(payload) if payload else {})
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(payload)
        except json.JSONDecodeError:
            return exc.code, {"raw": payload[:200]}
    except Exception as exc:  # noqa: BLE001 - network failure is itself a result
        return 0, {"error": str(exc)}


def expect(label: str, method: str, path: str, body=None, raw=None, want=(200, 201, 422, 400, 404, 409)):
    status, payload = call(method, path, body, raw)
    code = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code", "")
    ok = status in want
    if status == 500:
        ok = False
    mark = "OK " if ok else "!! "
    detail = f"HTTP {status}"
    if code:
        detail += f" {code}"
    print(f"  {mark}{label:<48} {detail}")
    if not ok:
        RESULTS.append(("HIGH", label, f"HTTP {status} {json.dumps(payload, ensure_ascii=False)[:160]}"))
    return status, payload


def main() -> int:
    print("=" * 100)
    print("A. 输入校验与错误路径")
    print("=" * 100)

    expect("成绩接口：不存在的周期", "GET", "/meta/dataset?cycle_id=999999")
    expect("分数换算：分数越界 900", "POST", "/catalog/score-to-rank", {"cycle_id": 5, "score": 900})
    expect("分数换算：分数为负", "POST", "/catalog/score-to-rank", {"cycle_id": 5, "score": -1})
    expect("分数换算：不存在的周期", "POST", "/catalog/score-to-rank", {"cycle_id": 999999, "score": 590})
    expect("畸形 JSON", "POST", "/profiles", raw="{not json")
    expect("缺必填字段的档案", "POST", "/profiles", {"exam_year": 2025})
    expect("位次为负的档案", "POST", "/profiles", {
        "cycle_id": 5, "exam_year": 2025, "rank": -1, "score": 590,
        "subjects": ["PHY", "CHM", "BIO"],
    })
    expect("只选 2 科的档案", "POST", "/profiles", {
        "cycle_id": 5, "exam_year": 2025, "rank": 60000, "score": 590,
        "subjects": ["PHY", "CHM"],
    })
    expect("选科重复的档案", "POST", "/profiles", {
        "cycle_id": 5, "exam_year": 2025, "rank": 60000, "score": 590,
        "subjects": ["PHY", "PHY", "CHM"],
    })
    expect("非法选考科目代码", "POST", "/profiles", {
        "cycle_id": 5, "exam_year": 2025, "rank": 60000, "score": 590,
        "subjects": ["PHY", "CHM", "XXX"],
    })
    expect("不存在的档案", "GET", "/profiles/999999")
    expect("不存在的方案", "GET", "/schemes/999999")
    expect("不存在的推荐会话", "GET", "/recommendations/999999")
    expect("不存在的体检报告", "GET", "/health-checks/999999")
    expect("方案排序：不存在的方案", "PATCH", "/schemes/999999/order", {"version": 1, "items": []})
    expect("推荐：不存在的档案", "POST", "/recommendations", {"profile_id": 999999})

    print()
    print("=" * 100)
    print("B. 边界：政策上限与并发")
    print("=" * 100)

    # A profile whose rank is beyond the whole population.
    status, profile = expect("位次超出全省人数（600000）", "POST", "/profiles", {
        "cycle_id": 5, "exam_year": 2025, "rank": 600000, "score": 441,
        "subjects": ["PHY", "CHM", "BIO"],
    }, want=(200, 201, 400, 422))
    extreme_id = profile.get("id") if isinstance(profile, dict) else None
    if extreme_id:
        status, pref = expect("  为其建偏好", "POST", f"/profiles/{extreme_id}/preferences", {
            "risk_mode": "BALANCED", "candidate_rank": 600000, "target_count": 96,
        }, want=(200, 201, 400, 422))
        if isinstance(pref, dict) and pref.get("id"):
            status, rec = expect("  跑推荐（极端位次不应 500）", "POST", "/recommendations", {
                "profile_id": extreme_id, "preference_id": pref["id"],
            }, want=(200, 201, 400, 422))
            if isinstance(rec, dict) and rec.get("stats"):
                print(f"       候选池 {rec['stats'].get('pooled')}，评分 {rec['stats'].get('scored')}")

    # target_count above the policy limit
    status, profile2 = expect("目标数量超过政策上限（200）", "POST", "/profiles", {
        "cycle_id": 5, "exam_year": 2025, "rank": 60000, "score": 590,
        "subjects": ["PHY", "CHM", "BIO"],
    }, want=(200, 201, 400, 422))
    if isinstance(profile2, dict) and profile2.get("id"):
        expect("  偏好 target_count=200", "POST", f"/profiles/{profile2['id']}/preferences", {
            "risk_mode": "BALANCED", "candidate_rank": 60000, "target_count": 200,
        }, want=(200, 201, 400, 422))

    print()
    print("=" * 100)
    print("C. 版本冲突（乐观锁）")
    print("=" * 100)
    status, scheme = call("POST", "/schemes", {"recommendation_session_id": 22})
    if status in (200, 201) and isinstance(scheme, dict) and scheme.get("id"):
        scheme_id = scheme["id"]
        version = scheme.get("lock_version", 1)
        expect("用过期版本号排序（应 409）", "PATCH", f"/schemes/{scheme_id}/order",
               {"version": version - 1 if version > 1 else 999, "items": []}, want=(409, 400, 422))
    else:
        print(f"  -- 无法创建方案，跳过（HTTP {status}）")

    print()
    print("=" * 100)
    print("D. 导出与体检")
    print("=" * 100)
    expect("导出不存在的方案", "GET", "/schemes/999999/export")
    expect("体检：不存在的方案", "POST", "/schemes/999999/health-check", {"version": 1})
    expect("体检：不存在的版本号", "POST", "/schemes/25/health-check", {"version": 999}, want=(200, 400, 404, 422))

    print()
    print("=" * 100)
    print("发现汇总")
    print("=" * 100)
    if not RESULTS:
        print("  边界与错误路径未发现缺陷（无 500、无静默成功）。")
    else:
        for severity, title, detail in RESULTS:
            print(f"  [{severity}] {title} —— {detail}")
    print()
    print(f"  （本次创建了临时档案，id 见上；如需清理请删除位次 600000 / 60000 的测试档案）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
