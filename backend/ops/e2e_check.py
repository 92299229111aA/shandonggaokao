"""End-to-end acceptance against a live API server.

Runs the whole six-step flow over real HTTP:
    meta -> profile -> preference -> recommendation -> scheme -> edit -> health check -> export

Usage: python ops/e2e_check.py [base_url]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8021"
API = f"{BASE}/api/v1"

failures: list[str] = []


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(f"{API}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode()
            return response.status, (json.loads(payload) if payload else {})
    except urllib.error.HTTPError as error:
        payload = error.read().decode()
        return error.code, (json.loads(payload) if payload else {})


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


def expect(label: str, response: tuple[int, dict], wanted: int) -> dict:
    """Assert a status code and surface the API's own error message on failure."""
    status, payload = response
    if status != wanted:
        detail = f"HTTP {status}"
        error = payload.get("error") if isinstance(payload, dict) else None
        if error:
            detail += f" {error.get('code')}: {error.get('message')}"
            if error.get("details"):
                detail += f" {json.dumps(error['details'], ensure_ascii=False)[:300]}"
        elif payload:
            detail += f" {json.dumps(payload, ensure_ascii=False)[:300]}"
        check(label, False, detail)
        return {}
    check(label, True, f"HTTP {status}")
    return payload


print("=" * 72)
print("山东高考智能志愿系统 V1 — 端到端验收")
print("=" * 72)

# ---- 1. meta ----
status, health = call("GET", "/meta/health")
check("OpenAPI 服务健康检查", status == 200 and health.get("database") == "up", str(health.get("database")))

status, cycles = call("GET", "/meta/admission-cycles")
check("读取招生周期", status == 200 and len(cycles) >= 1)
cycle = cycles[0]
limit = cycle["application_limit"]
check("志愿数量上限来自 admission_cycles", limit == 96, f"application_limit={limit}")

status, dataset = call("GET", "/meta/dataset")
check("读取数据集版本", status == 200 and dataset.get("snapshot") is not None)
# 数据来源必须对用户可见：要么明确标 DEMO，要么明确声明是第三方整理包。
# 真实但非官方的数据被当成权威数据展示，比缺少数据更危险。
warnings_text = " ".join(dataset.get("warnings") or [])
if dataset.get("is_demo") is True:
    check("数据来源被明确标记", "DEMO" in warnings_text, "DEMO 数据已标注")
else:
    check(
        "数据来源被明确标记",
        "THIRD_PARTY" in warnings_text or "第三方" in warnings_text,
        f"非 DEMO 数据；溯源警告={'有' if warnings_text else '无'}",
    )
check("演示数据计划数 > 0", dataset.get("plan_count", 0) > 0, f"{dataset.get('plan_count')} 条")

# ---- 2. score -> rank ----
status, converted = call("POST", "/catalog/score-to-rank", {"cycle_id": cycle["id"], "score": 590})
check("分数换算位次", status == 200 and converted.get("rank", 0) > 0, f"590分 -> {converted.get('rank')}")

# ---- 3. profile ----
candidate_rank = 60000
profile = expect(
    "创建考生档案",
    call(
        "POST",
        "/profiles",
        {
            "cycle_id": cycle["id"],
            "display_name": "E2E 演示考生",
            "score": 590,
            "rank": candidate_rank,
            "subjects": ["PHY", "CHM", "BIO"],
            "foreign_language": "ENG",
            "sex": "MALE",
            "subject_scores": [
                {"subject_code": "PHY", "score": 88},
                {"subject_code": "CHM", "score": 85},
                {"subject_code": "BIO", "score": 82},
                {"subject_code": "ENG", "score": 120},
            ],
            "attributes": [
                {"attr_key": "COLOR_VISION_NORMAL", "value_type": "BOOL", "value_bool": True}
            ],
        },
    ),
    201,
)
profile_id = profile.get("id")

# ---- 4. preference ----
preference = expect(
    "创建偏好档案",
    call(
        "POST",
        f"/profiles/{profile_id}/preferences",
        {
            "risk_mode": "BALANCED",
            "max_tuition": 9000,
            "exclude_cooperative": True,
            "min_safe_count": 3,
            "major_preferences": [
                {"major_name": "英语", "polarity": "EXCLUDE", "is_hard_exclude": True}
            ],
            "location_preferences": [{"province_code": "37", "province_name": "山东省", "weight": 80}],
            "university_preferences": [{"trait": "IS_211", "weight": 60}],
        },
    ),
    201,
)
check("目标数量取自政策上限", preference.get("target_count") == limit, f"{preference.get('target_count')}")
preference_id = preference.get("id")

# ---- 5. recommendation ----
run = expect(
    "生成推荐（锁定版本）",
    call("POST", "/recommendations", {"profile_id": profile_id, "preference_id": preference_id}),
    201,
)
session = run.get("session", {})
check("会话锁定 profile_version", session.get("profile_version") == 1)
check("会话锁定 preference_version", session.get("preference_version") == 1)
check("会话锁定 dataset_snapshot_id", session.get("snapshot_id") is not None)
check("会话锁定 policy_version", bool(session.get("policy_version")), session.get("policy_version", ""))
check("会话锁定 model_version", bool(session.get("model_version")), session.get("model_version", ""))
session_id = session.get("id")
pool_size = run.get("pool_size", 0)
check("候选池非空", pool_size > 0, f"{pool_size} 个")
print(f"       风险分布: {run.get('risk_distribution')}")

status, items = call("GET", f"/recommendations/{session_id}/items?limit=200")
check("读取推荐条目", status == 200 and items.get("total", 0) > 0, f"{items.get('total')} 条")
sample = items["items"][0]
check("卡片包含 ACS", sample.get("acs") is not None, f"ACS={sample.get('acs'):.1f}" if sample.get("acs") is not None else "")
check("卡片包含 Confidence", sample.get("confidence") is not None)
check("卡片包含 Preference", sample.get("preference_score") is not None)
history_years = len(sample.get("history", []))
# 真实数据里专业代码/名称会变动，且每年都有新增专业，因此 3 年是常见值而非保证值。
# 断言「至少有历史数据」，并打印实际年数以便观察。
check(
    "卡片包含历史最低位次",
    history_years >= 1,
    f"{history_years} 年（真实数据中年数因专业而异）",
)
check("卡片包含特殊要求字段", "special_requirements" in sample)
# The spec forbids *claiming* an admission probability. The phrase may appear only
# inside an explicit negation such as "不是录取概率".
payload_text = json.dumps(items, ensure_ascii=False)
raw_hits = payload_text.count("录取概率")
negated_hits = payload_text.count("不是录取概率") + payload_text.count("而非录取概率")
check(
    "无理赔表述（出现的“录取概率”必须都在否定语境中）",
    raw_hits == negated_hits,
    f"出现 {raw_hits} 次，其中否定语境 {negated_hits} 次",
)
excluded_present = [i for i in items["items"] if i["plan_name"] == "英语"]
check("用户硬性排除的专业未进入候选池", not excluded_present, f"{len(excluded_present)} 条泄漏")
fail_present = [i for i in items["items"] if i["eligibility"] == "FAIL"]
check("候选池中没有 FAIL 志愿", not fail_present, f"{len(fail_present)} 条")

# ---- 6. scheme ----
status, scheme = call("POST", "/schemes", {"recommendation_session_id": session_id})
check("生成志愿方案", status == 201, f"scheme={scheme.get('id')} v{scheme.get('version_no')}")
scheme_id = scheme.get("id")
check(
    "方案条目数 = min(候选数, 政策上限)",
    scheme.get("item_count") == min(pool_size, limit),
    f"{scheme.get('item_count')}（候选 {pool_size}，上限 {limit}）",
)
check("方案版本从 1 开始", scheme.get("version_no") == 1)
check("方案数量不超过政策上限", scheme.get("item_count", 0) <= limit)
positions = [item["position"] for item in scheme.get("items", [])]
check("位置连续从 1 开始", positions == list(range(1, len(positions) + 1)))
bands = [item["desire_band"] or 0 for item in scheme.get("items", [])]
check("意愿层单调不增（真实意愿优先）", bands == sorted(bands, reverse=True))
check("方案中没有硬性排除的专业", not [i for i in scheme["items"] if i["plan_name"] == "英语"])

# ---- 7. edit + optimistic locking ----
entry = [{"recommendation_item_id": scheme["items"][-1]["recommendation_item_id"], "position": 1}]
status, updated = call(
    "PATCH", f"/schemes/{scheme_id}/order", {"version": scheme["lock_version"], "items": entry}
)
check("调整顺序生成新版本", status == 200 and updated.get("current_version_no") == 2,
      f"v{updated.get('current_version_no')}")
status, conflict = call(
    "PATCH", f"/schemes/{scheme_id}/order", {"version": scheme["lock_version"], "items": entry}
)
check("陈旧版本返回 409", status == 409, f"HTTP {status}")
check("409 使用统一错误格式", set(conflict.get("error", {})) == {"code", "message", "details"},
      conflict.get("error", {}).get("code", ""))

status, versions = call("GET", f"/schemes/{scheme_id}/versions")
check("历史版本可读", status == 200 and len(versions) == 2, f"{len(versions)} 个版本")
status, old = call("GET", f"/schemes/{scheme_id}?version_no=1")
check("v1 仍可读取且被标记为历史版本", status == 200 and old.get("is_historical") is True)

status, locked = call(
    "PUT",
    f"/schemes/{scheme_id}/items/{updated['items'][-1]['position']}/lock",
    {"version": updated["lock_version"], "locked": True},
)
check("锁定志愿", status == 200 and any(i["locked"] for i in locked.get("items", [])))
locked_item_id = next(i["recommendation_item_id"] for i in locked["items"] if i["locked"])

status, optimized = call(
    "POST",
    f"/schemes/{scheme_id}/optimize",
    {"version": locked["lock_version"], "target_count": 8, "risk_mode": "STEADY", "min_safe_count": 2},
)
check("重新优化", status == 200, f"{optimized.get('item_count')} 个志愿")
check("锁定志愿被保留", locked_item_id in [i["recommendation_item_id"] for i in optimized.get("items", [])])
check("优化记录统计信息", optimized.get("optimizer_stats") is not None)

# ---- 8. health check ----
status, report = call("POST", f"/schemes/{scheme_id}/health-check", {})
check("运行志愿体检", status == 201, f"健康分 {report.get('score')}")
check("体检包含 11 项检查", len(report.get("checks", [])) == 11, f"{len(report.get('checks', []))} 项")
check("体检带免责声明", "不是录取概率" in report.get("disclaimer", ""))
check("体检报告有 summary", bool(report.get("summary")))

before = call("GET", f"/schemes/{scheme_id}")[1]
call("POST", f"/schemes/{scheme_id}/health-check", {})
after = call("GET", f"/schemes/{scheme_id}")[1]
check("体检不修改方案（版本号不变）", before["current_version_no"] == after["current_version_no"])
check("体检不修改方案（lock_version 不变）", before["lock_version"] == after["lock_version"])
check("体检不修改方案（顺序不变）",
      [i["recommendation_item_id"] for i in before["items"]] == [i["recommendation_item_id"] for i in after["items"]])

# ---- 9. export ----
status, exported = call("GET", f"/schemes/{scheme_id}/export")
check("导出方案", status == 200 and len(exported.get("rows", [])) == optimized.get("item_count"))
check("导出带政策上限", exported.get("application_limit") == limit)
check("导出声明不是录取概率", "而非录取概率" in exported.get("disclaimer", ""))

print("=" * 72)
if failures:
    print(f"验收结果：{len(failures)} 项失败")
    for item in failures:
        print(f"  - {item}")
    sys.exit(1)
print("验收结果：全部通过")
sys.exit(0)
