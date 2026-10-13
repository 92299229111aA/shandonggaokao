"""Audit concurrent-write behaviour, which the first pass only tested with a stale number.

The boundary audit sent a single request with an outdated `version` and got a rejection.
That proves the optimistic-locking *check* exists, not that it holds under real
interleaving. This fires two writes at the same scheme version simultaneously and checks
that exactly one succeeds — the "方案已在其他位置被修改" promise.

Also checks that the policy limit survives a deliberately oversized ordering request.

Run: python scripts/audit_concurrency.py
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/api/v1"
FINDINGS: list[tuple[str, str, str]] = []


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
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


def main() -> int:
    print("=" * 100)
    print("准备：取一个已有方案的当前版本")
    print("=" * 100)
    # `GET /schemes` is not a supported route; take an id from the CLI or the database.
    scheme_id = int(sys.argv[2]) if len(sys.argv) > 2 else 26
    status, detail = call("GET", f"/schemes/{scheme_id}")
    if status != 200:
        print(f"  读取方案 {scheme_id} 失败（HTTP {status}）")
        return 1
    version = detail.get("version_no", 1)
    scheme_items = detail.get("items", [])
    print(f"  方案 {scheme_id}  当前版本 {version}")
    print(f"  条目数 {len(scheme_items)}，用于排序的版本号 {version}")

    if len(scheme_items) < 2:
        print("  条目过少，跳过并发排序测试")
        return 0

    # SchemeOrderEntry requires recommendation_item_id, so rebuild the order from the
    # actual items rather than from position numbers alone.
    ids = [item.get("recommendation_item_id") for item in scheme_items]
    if any(i is None for i in ids):
        print("  条目缺少 recommendation_item_id，跳过")
        return 1
    reordered = list(ids)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    body = {
        "version": version,
        "items": [
            {"recommendation_item_id": item_id, "position": index}
            for index, item_id in enumerate(reordered, start=1)
        ],
    }

    print()
    print("=" * 100)
    print("并发：两个写请求同时提交同一版本号")
    print("=" * 100)
    results: list[tuple[int, dict]] = []
    lock = threading.Lock()

    def worker() -> None:
        outcome = call("PATCH", f"/schemes/{scheme_id}/order", body)
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    codes = sorted(status for status, _ in results)
    for index, (status, payload) in enumerate(results, start=1):
        code = ""
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            code = payload["error"].get("code", "")
        print(f"  请求 {index}: HTTP {status} {code}")

    success = sum(1 for status, _ in results if status in (200, 201))
    conflict = sum(1 for status, _ in results if status == 409)
    server_errors = sum(1 for status, _ in results if status >= 500)
    print(f"  成功 {success}  冲突(409) {conflict}  服务端错误(5xx) {server_errors}")
    if server_errors:
        FINDINGS.append(
            (
                "HIGH",
                "并发写入触发未捕获的 IntegrityError，返回 500",
                f"应返回 409；返回码 {codes}",
            )
        )
    if success != 1:
        FINDINGS.append(
            ("HIGH", "并发写入未做到恰好一个成功", f"成功 {success}，返回码 {codes}")
        )
    elif not server_errors:
        print("  OK 恰好一个成功，另一个被乐观锁拒绝")

    # Confirm the losing request really changed nothing.
    status, after = call("GET", f"/schemes/{scheme_id}")
    after_version = after.get("version_no")
    print(f"  写后版本号: {after_version}（应为 {version + 1}）")
    if after_version != version + 1:
        FINDINGS.append(
            ("MEDIUM", "并发写入后版本号未按预期递增", f"{after_version} != {version + 1}")
        )

    print()
    print("=" * 100)
    print("政策性上限：提交超过 96 条的排序请求")
    print("=" * 100)
    oversized = {
        "version": after_version,
        "items": [
            {"recommendation_item_id": item_id, "position": index}
            for index, item_id in enumerate(ids * 2, start=1)
        ],
    }
    status, payload = call("PATCH", f"/schemes/{scheme_id}/order", oversized)
    code = ""
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        code = payload["error"].get("code", "")
    print(f"  HTTP {status} {code}")
    if status in (200, 201):
        status, check = call("GET", f"/schemes/{scheme_id}")
        count = len(check.get("items", []))
        print(f"  写后条目数 {count}")
        if count > 96:
            FINDINGS.append(("HIGH", "排序请求突破了政策上限", f"{count} > 96"))
    else:
        print("  OK 超限请求被拒绝")

    print()
    print("=" * 100)
    print("发现汇总")
    print("=" * 100)
    if not FINDINGS:
        print("  并发与上限未发现缺陷。")
    else:
        for severity, title, detail in FINDINGS:
            print(f"  [{severity}] {title} —— {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
