"""桌面版 SQLite 模式的端到端冒烟：走一遍前端真正会用的接口。

跑在 SQLite 上而不是 PostgreSQL，目的就是证明"换库不换行为"：
参考数据能查、推荐能算、方案能优化、体检能出报告。
"""

import json
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8001/api/v1"
FAILED: list[str] = []


def call(method: str, path: str, body: dict | None = None, timeout: int = 1200):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        print(f"  !! {method} {path} -> {exc.code}: {exc.read().decode()[:200]}")
        raise


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' -> {detail}' if detail else ''}")
    if not ok:
        FAILED.append(label)


def main() -> int:
    # 1. 参考数据（SQLite 里必须能读到）
    cycles = call("GET", "/meta/admission-cycles")
    check("读取招生年份", len(cycles) > 0, f"{len(cycles)} 个")
    active = next((c for c in cycles if c.get("is_active")), cycles[0])
    check("有在用年份与志愿上限", active.get("application_limit") is not None, str(active.get("application_limit")))

    universities = call("GET", "/catalog/universities?limit=5")
    check("读取院校目录", universities["total"] > 1000, f"{universities['total']:,} 所")

    options = call("GET", "/catalog/preference-options")
    check("读取偏好选项", len(options.get("provinces", [])) > 30, f"{len(options.get('provinces', []))} 个")

    # 2. 档案与偏好（写入用户表）
    profile = call(
        "POST",
        "/profiles",
        {
            "cycle_id": active["id"],
            "exam_year": active["exam_year"],
            "rank": 60000,
            "score": 590,
            "subjects": ["PHY", "CHM", "BIO"],
        },
    )
    check("创建档案", profile.get("id") is not None, f"#{profile['id']}")

    pref = call("POST", f"/profiles/{profile['id']}/preferences", {"risk_mode": "BALANCED"})
    check("创建偏好", pref.get("id") is not None, f"#{pref['id']}")

    # 3. 推荐（要把 19k 候选池算一遍）
    started = time.time()
    run = call(
        "POST",
        "/recommendations",
        {"profile_id": profile["id"], "preference_id": pref["id"]},
    )
    elapsed = time.time() - started
    pool = run["pool_size"]
    check("生成推荐", pool > 0, f"候选池 {pool:,}，用时 {elapsed:.1f}s")

    items = call("GET", f"/recommendations/{run['session']['id']}/items?limit=5")
    check("读取推荐明细", len(items["items"]) > 0, f"{len(items['items'])} 条")
    check("分层计数齐全", sum(items.get("bucket_counts", {}).values()) > 0, str(items.get("bucket_counts")))

    # 4. 方案（要跑 OR-Tools 优化器）
    started = time.time()
    scheme = call("POST", "/schemes", {"recommendation_session_id": run["session"]["id"]})
    elapsed = time.time() - started
    check(
        "生成方案",
        scheme["item_count"] == scheme["target_count"],
        f"{scheme['item_count']}/{scheme['target_count']}，用时 {elapsed:.1f}s",
    )
    check("方案条目含 ACS", scheme["items"][0].get("acs") is not None, str(scheme["items"][0].get("acs")))

    # 5. 顺序调整 + 锁定（写入路径）
    order = [
        {"recommendation_item_id": it["recommendation_item_id"], "position": i + 1, "locked": i == 0}
        for i, it in enumerate(scheme["items"])
    ]
    moved = call(
        "PATCH",
        f"/schemes/{scheme['id']}/order",
        {"version": scheme["lock_version"], "change_summary": "冒烟测试", "items": order},
    )
    check("调整顺序并锁定", moved["items"][0]["locked"] is True, f"v{moved['current_version_no']}")

    # 6. 体检
    report = call("POST", f"/schemes/{scheme['id']}/health-check", {})
    check("生成体检报告", report.get("id") is not None, f"score={report['score']}")
    check("逐项检查已落库", len(report["checks"]) == 11, f"{len(report['checks'])} 项")
    check(
        "问题带稳定 plan_id",
        all("plan_id" in (i.get("detail") or {}) for i in report["issues"] if i["code"] != "LIMIT_UNUSED"),
        f"{len(report['issues'])} 条",
    )

    # 7. 导出
    export = call("GET", f"/schemes/{scheme['id']}/export")
    check("导出方案行", len(export["rows"]) == scheme["item_count"], f"{len(export['rows'])} 行")
    check("导出带版本号", export.get("version_no") is not None, str(export.get("version_no")))

    print()
    total = 16
    print(f"  合计 {total - len(FAILED)}/{total} 通过")
    if FAILED:
        print("  失败项:", " | ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
