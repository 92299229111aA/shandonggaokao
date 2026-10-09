"""Analyse docs/real-run.json: is the real-data output actually sensible?"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "docs" / "real-run.json"


def main() -> int:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    items = payload["items"]
    candidate = payload["candidate"]
    print("=" * 96)
    print("真实数据产出分析")
    print("=" * 96)
    print(f"考生: 位次 {candidate['rank']:,} / {candidate['population']:,}  "
          f"= {candidate['rank'] / candidate['population'] * 100:.1f}% 百分位   选科 {candidate['subjects']}")
    print(f"数据: {payload['dataset']}")
    print(f"统计: {payload['stats']}")
    print(f"候选池: {len(items):,} 条")
    print()

    acs = [i["acs"] for i in items if i["acs"] is not None]
    conf = [i["confidence"] for i in items if i["confidence"] is not None]
    print(f"ACS        范围 {min(acs):.1f} ~ {max(acs):.1f}   中位 {statistics.median(acs):.1f}   均值 {statistics.mean(acs):.1f}")
    print(f"Confidence 范围 {min(conf):.3f} ~ {max(conf):.3f}  中位 {statistics.median(conf):.3f}")
    print()

    print("风险分桶：")
    total = len(items)
    for bucket in ("HIGH_REACH", "REACH", "BORDER", "STABLE", "SAFE"):
        count = payload["buckets"].get(bucket, 0)
        pct = count / total * 100
        print(f"    {bucket:<12} {count:>7,}  {pct:>5.1f}%  {'█' * int(pct / 2)}")
    print()

    hist = Counter(i["history_points"] for i in items)
    print(f"历史年数分布: {dict(sorted(hist.items()))}")
    years = Counter(len(i["years"]) for i in items)
    print(f"参与计算年数: {dict(sorted(years.items()))}")
    print()

    print("候选池省份分布（前 12）：")
    provinces = Counter(i["province"] for i in items)
    for code, count in provinces.most_common(12):
        print(f"    {code}  {count:>7,}")
    print(f"    共 {len(provinces)} 个省份")
    print()

    print("ACS 最高 10 条（最保底）：")
    for item in sorted(items, key=lambda i: -(i["acs"] or 0))[:10]:
        print(f"    {item['acs']:>5.1f} {item['bucket']:<11} conf={item['confidence']:.2f} "
              f"n={item['history_points']} {item['province']} {item['uni'][:18]:<20} {item['plan'][:24]}")
    print()

    print("ACS 最低 10 条（最冲）：")
    for item in sorted(items, key=lambda i: (i["acs"] or 0))[:10]:
        print(f"    {item['acs']:>5.1f} {item['bucket']:<11} conf={item['confidence']:.2f} "
              f"n={item['history_points']} {item['province']} {item['uni'][:18]:<20} {item['plan'][:24]}")
    print()

    # Sanity: a strong candidate should see mostly safety, a weak one mostly reach.
    print("合理性检查：")
    top = [i for i in items if i["acs"] and i["acs"] >= 80]
    print(f"    ACS>=80（保）条数 {len(top):,}"
          f"   样例: {[i['uni'][:14] for i in top[:3]]}")
    low = [i for i in items if i["acs"] and i["acs"] < 30]
    print(f"    ACS<30（高冲）条数 {len(low):,}"
          f"   样例: {[i['uni'][:14] for i in low[:3]]}")
    no_hist = [i for i in items if i["history_points"] == 0]
    print(f"    无历史数据条数 {len(no_hist):,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
