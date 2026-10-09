"""The DEMO dataset.

**This is not official data.** Every row is marked so it cannot be mistaken for
the real thing:

  * `source_documents.quality = 'DEMO'`
  * `dataset_snapshots.code` starts with `DEMO-`
  * `admission_plans.is_demo = true`, `admission_outcomes.is_demo = true`
  * university names literally start with 演示, and their codes start with `DEMO`

Shandong candidates fill in schools from all over the country, so the dataset
covers all 31 provincial-level regions with three selectivity tiers each. That is
what makes the 地域偏好 dimension (province/city filters, hard excludes) meaningful
and testable.

**Replacing this with real data**: the shapes here mirror the official files
(一分一段表, 招生计划, 投档结果, 选科要求). Point the importers in
`app/ingestion/etl.py` at the official files, create a new snapshot, and delete
these DEMO rows — nothing in the pipeline needs to change.

Deliberately **not** done: inventing plan counts or cut-off ranks for *real*
universities. Those are official facts; fabricating them would be worse than
having no data, because the output would look authoritative and be wrong.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

DEMO_SNAPSHOT_CODE = "DEMO-SD-2026"
DEMO_POLICY_VERSION = "sd-2026-demo-v1"
DEMO_PROVINCE_CODE = "37"
DEMO_EXAM_YEAR = 2026
DEMO_APPLICATION_LIMIT = 96

# Historical years used for ACS. Three years is the designed sweet spot for
# Confidence; the demo keeps all three so the UI shows a realistic spread.
DEMO_HISTORY_YEARS = (2023, 2024, 2025)

# Candidate population per year. These are DEMO numbers, not official statistics.
DEMO_EXAM_YEAR_STATS: dict[int, int] = {
    2023: 296_000,
    2024: 302_000,
    2025: 308_000,
    2026: 308_000,
}

# 分数线：2025 年取自山东省人民政府官方发布
# http://www.shandong.gov.cn/art/2025/6/26/art_97564_677390.html
# 普通类：特殊类型招生控制线 521 分，一段线 441 分，二段线 150 分。
# 2026 年官方公告未单独披露分数线，且普通类取消了特殊类型批，
# 因此 2026 行只填一段线与二段线，special_line 留空 —— 不猜测未公布的数字。
# 其余年份没有可靠来源，保持为空。
DEMO_SCORE_LINES: dict[int, tuple[int | None, int | None]] = {
    2025: (521, 441),
    2026: (None, 441),
}

# (score, same_score_count) sampled so that score→rank conversion works end to end.
DEMO_SCORE_SEGMENTS: tuple[tuple[int, int], ...] = (
    (700, 3), (690, 12), (680, 35), (670, 80), (660, 160),
    (650, 300), (640, 520), (630, 850), (620, 1_300), (610, 1_900),
    (600, 2_700), (590, 3_700), (580, 5_000), (570, 6_600), (560, 8_500),
    (550, 10_800), (540, 13_400), (530, 16_300), (520, 19_500), (510, 23_000),
    (500, 26_800), (480, 35_000), (460, 44_000), (445, 51_000),
)

SUBJECT_NAMES: dict[str, str] = {
    "PHY": "物理",
    "CHM": "化学",
    "BIO": "生物",
    "POL": "思想政治",
    "HIS": "历史",
    "GEO": "地理",
}

# 选科要求 variants actually used by Shandong plans, as DNF groups.
SUBJECT_RULES: dict[str, dict] = {
    "UNRESTRICTED": {"mode": "ALL", "groups": [], "raw_text": "不限"},
    "PHY_ONLY": {
        "mode": "ALL",
        "groups": [["PHY"]],
        "raw_text": "物理(1门科目考生必须选考方可报考)",
    },
    "PHY_AND_CHM": {
        "mode": "ALL",
        "groups": [["PHY", "CHM"]],
        "raw_text": "物理和化学(2门科目考生均须选考方可报考)",
    },
    "PHY_OR_HIS": {
        "mode": "ANY",
        "groups": [["PHY"], ["HIS"]],
        "raw_text": "物理或历史(考生选考其中一门即可报考)",
    },
    "CHM_AND_BIO": {
        "mode": "ALL",
        "groups": [["CHM", "BIO"]],
        "raw_text": "化学和生物(2门科目考生均须选考方可报考)",
    },
}


@dataclass(frozen=True, slots=True)
class DemoUniversity:
    ministry_code: str
    name: str
    short_name: str
    province_code: str
    province_name: str
    city_code: str
    city_name: str
    is_985: bool = False
    is_211: bool = False
    is_double_first_class: bool = False
    is_public: bool = True


@dataclass(frozen=True, slots=True)
class DemoMajor:
    code: str
    name: str
    level: str
    discipline_code: str
    discipline_name: str
    category_code: str
    category_name: str
    degree_awarded: str


@dataclass(frozen=True, slots=True)
class DemoPlan:
    """One 招生计划 in DEMO-2026.

    `history_min_ranks` are the 投档最低位次 for DEMO_HISTORY_YEARS, oldest first;
    `history_plans` are the corresponding plan counts.
    """

    university_code: str
    plan_name: str
    major_code: str | None
    subject_rule: str
    plan_count: int
    tuition_fee: int
    history_min_ranks: tuple[int, int, int]
    history_plans: tuple[int, int, int]
    duration_years: int = 4
    campus_name: str | None = None
    cooperative: str = "NONE"
    special_requirements: str | None = None
    requirement_specs: tuple[dict, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------
# 31 provincial-level regions (Shandong plus the other 30), used to build the
# national demo pool. `tier` drives selectivity: 1 = 985, 2 = 211/双一流,
# 3 = 普通本科. Index inside the list makes the more competitive provinces
# (Beijing, Shanghai, Jiangsu …) harder, which is what the real pool looks like.
# --------------------------------------------------------------------------

PROVINCES: tuple[tuple[str, str, str, str], ...] = (
    ("37", "山东省", "370100", "济南市"),
    ("11", "北京市", "110108", "北京市"),
    ("31", "上海市", "310104", "上海市"),
    ("32", "江苏省", "320100", "南京市"),
    ("33", "浙江省", "330100", "杭州市"),
    ("44", "广东省", "440100", "广州市"),
    ("42", "湖北省", "420100", "武汉市"),
    ("51", "四川省", "510100", "成都市"),
    ("61", "陕西省", "610100", "西安市"),
    ("12", "天津市", "120104", "天津市"),
    ("50", "重庆市", "500106", "重庆市"),
    ("13", "河北省", "130100", "石家庄市"),
    ("14", "山西省", "140100", "太原市"),
    ("21", "辽宁省", "210100", "沈阳市"),
    ("22", "吉林省", "220100", "长春市"),
    ("23", "黑龙江省", "230100", "哈尔滨市"),
    ("34", "安徽省", "340100", "合肥市"),
    ("35", "福建省", "350200", "厦门市"),
    ("36", "江西省", "360100", "南昌市"),
    ("41", "河南省", "410100", "郑州市"),
    ("43", "湖南省", "430100", "长沙市"),
    ("45", "广西壮族自治区", "450100", "南宁市"),
    ("46", "海南省", "460100", "海口市"),
    ("52", "贵州省", "520100", "贵阳市"),
    ("53", "云南省", "530100", "昆明市"),
    ("54", "西藏自治区", "540100", "拉萨市"),
    ("62", "甘肃省", "620100", "兰州市"),
    ("63", "青海省", "630100", "西宁市"),
    ("64", "宁夏回族自治区", "640100", "银川市"),
    ("65", "新疆维吾尔自治区", "650100", "乌鲁木齐市"),
    ("15", "内蒙古自治区", "150100", "呼和浩特市"),
)

# (tier, is_985, is_211, is_double_first_class)
TIERS: tuple[tuple[int, bool, bool, bool], ...] = (
    (1, True, True, True),
    (2, False, True, True),
    (3, False, False, False),
)

# Per-tier selectivity expressed as a multiple of the *reference candidate's* rank
# percentile.
#
#   q_y = ref_percentile * ratio
#   d_y = clip(ln(q_y / q_c), -0.30, +0.30) = clip(ln(ratio), -0.30, +0.30)
#
# The brackets below are derived from the REAL formula, not from HistoryACS alone.
# ACS is shrunk toward 50 by confidence, so with confidence ≈ 0.79:
#
#   ACS = 50 + (BaseACS - 50) * 0.79,   BaseACS = 50 + 35 * tanh(d / 0.07)
#
#   d = -0.30 (clip) → ACS 25.6  → 高冲        d = +0.15 → ACS 56.7 → 边界
#   d = -0.15       → ACS 36.9  → 冲          d = +0.30 → ACS 74.6 → 稳
#
# Note that d is clipped at +0.30, so ACS tops out near 75 for this confidence
# level — reaching the 保 bucket (ACS >= 80) therefore needs BOTH a large rank
# cushion AND high confidence. Getting this wrong is why an earlier revision of
# this dataset produced an empty 保 bucket.
TIER_Q_RATIOS: dict[int, tuple[float, float]] = {
    # tier: (base ratio, per-plan step); base+step is the easiest plan in the tier
    1: (0.42, 0.18),  # 985:        ln -0.87..-0.28 → 高冲
    2: (0.82, 0.33),  # 211/双一流: ln -0.20..+0.14 → 冲 / 边界
    3: (1.20, 0.55),  # 普通本科:   ln +0.18..+0.56 → 稳 / 保
}

# The reference candidate this dataset is tuned for: rank 60,000 of 308,000.
DEMO_REFERENCE_RANK = 60_000

TIER_TUITION: dict[int, int] = {1: 5_600, 2: 5_200, 3: 4_800}
TIER_PLAN_COUNT: dict[int, int] = {1: 35, 2: 55, 3: 80}

# Majors offered per tier. Chosen so the demo exercises every 选科 rule shape:
# 物理和化学 (both tier-1 plans), 仅物理, 化学和生物, 不限, 物理或历史.
TIER_MAJORS: dict[int, tuple[tuple[str, str, str], ...]] = {
    # tier: ((major_code, plan_name, subject_rule), ...)
    1: (
        ("0809", "计算机类", "PHY_AND_CHM"),
        ("0701", "数学类", "PHY_ONLY"),
    ),
    2: (
        ("080701", "电子信息工程", "PHY_AND_CHM"),
        ("1202", "工商管理类", "UNRESTRICTED"),
    ),
    3: (
        ("081001", "土木工程", "PHY_ONLY"),
        ("050201", "英语", "UNRESTRICTED"),
    ),
}


def _stable_offset(seed: str, span: int) -> int:
    """Deterministic pseudo-random offset.

    Uses hashlib rather than hash() because Python's string hash is salted per
    process; `seed_demo` must be idempotent across runs.
    """
    if span <= 0:
        return 0
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % span


def build_universities() -> tuple[DemoUniversity, ...]:
    """Three universities per provincial-level region, deterministically named."""
    universities: list[DemoUniversity] = []
    for province_code, province_name, city_code, city_name in PROVINCES:
        for tier, is_985, is_211, is_double in TIERS:
            universities.append(
                DemoUniversity(
                    # Code shape: DEMO + province code + tier digit, e.g. DEMO373
                    ministry_code=f"DEMO{province_code}{tier}",
                    name=f"演示大学（{province_name}·{tier}档）",
                    short_name=f"演示{province_name[:2]}{tier}",
                    province_code=province_code,
                    province_name=province_name,
                    city_code=city_code,
                    city_name=city_name,
                    is_985=is_985,
                    is_211=is_211,
                    is_double_first_class=is_double,
                    is_public=True,
                )
            )
    return tuple(universities)


def build_plans(universities: tuple[DemoUniversity, ...]) -> tuple[DemoPlan, ...]:
    """Two plans per university, with three years of clearly-synthetic history.

    Ranks are derived from the reference candidate's percentile multiplied by a
    per-tier ratio, so the resulting pool provably spans every risk bucket.
    """
    plans: list[DemoPlan] = []
    province_index_by_code = {code: index for index, (code, *_rest) in enumerate(PROVINCES)}
    reference_percentile = DEMO_REFERENCE_RANK / DEMO_EXAM_YEAR_STATS[DEMO_EXAM_YEAR]

    for university in universities:
        tier = int(university.ministry_code[-1])
        province_index = province_index_by_code[university.province_code]
        base_ratio, ratio_step = TIER_Q_RATIOS[tier]
        total_plans = len(TIER_MAJORS[tier])

        for plan_index, (major_code, plan_name, subject_rule) in enumerate(TIER_MAJORS[tier]):
            # More competitive regions (earlier in PROVINCES) are harder: a modest
            # percentage premium that never flips the tier ordering between the
            # first and last plan of the same tier.
            province_premium = 1.0 - min(province_index, 30) * 0.008

            # Deterministic spread inside the tier so the bucket boundaries get
            # populated instead of everything landing in one bucket.
            spread = plan_index / max(1, total_plans - 1) if total_plans > 1 else 0.0
            # Small deterministic jitter (< 4% of the ratio) so plans inside a tier
            # are not perfectly evenly spaced, without escaping the calibrated band.
            jitter = _stable_offset(f"{university.ministry_code}:{major_code}", 40) / 1000.0
            ratio = (base_ratio + spread * ratio_step + jitter) * province_premium

            q_y = reference_percentile * ratio
            current_rank = max(1, int(q_y * DEMO_EXAM_YEAR_STATS[DEMO_EXAM_YEAR]))

            # History trends slightly harder each year (competition intensifying),
            # which gives the ACS trend term something real to detect.
            history_min_ranks: tuple[int, int, int] = (
                int(current_rank * 1.16),
                int(current_rank * 1.08),
                int(current_rank * 1.00),
            )
            plan_count = TIER_PLAN_COUNT[tier] + _stable_offset(
                f"count:{university.ministry_code}:{major_code}", 25
            )
            history_plans: tuple[int, int, int] = (
                plan_count + _stable_offset(f"h1:{university.ministry_code}:{major_code}", 10),
                plan_count + _stable_offset(f"h2:{university.ministry_code}:{major_code}", 10),
                plan_count,
            )

            plans.append(
                DemoPlan(
                    university_code=university.ministry_code,
                    plan_name=plan_name,
                    major_code=major_code,
                    subject_rule=subject_rule,
                    plan_count=plan_count,
                    tuition_fee=TIER_TUITION[tier] + plan_index * 400,
                    history_min_ranks=history_min_ranks,
                    history_plans=history_plans,
                )
            )
    return tuple(plans)


# The in-province (Shandong) sample keeps the hand-written edge cases that the
# Health Check and Rule Engine tests rely on: a plan shrinking 50→5, a medical
# requirement, a single-subject requirement, a cooperative programme with an
# interview, and a 物理或历史 subject rule.
SHANDONG_EDGE_CASES: tuple[DemoPlan, ...] = (
    DemoPlan(
        "DEMO373", "临床医学", "100201", "CHM_AND_BIO", 40, 6800, (62000, 60000, 58000), (38, 40, 42),
        special_requirements="不招色盲、色弱考生",
        requirement_specs=(
            {
                "requirement_type": "MEDICAL",
                "operand": {"must_satisfy": ["COLOR_VISION_NORMAL"]},
                "raw_text": "不招色盲、色弱考生",
            },
        ),
    ),
    # 计划骤减样本：50 -> 5，用于验证 PlanReductionCheck
    DemoPlan(
        "DEMO371", "生物科学类", "0710", "CHM_AND_BIO", 5, 5800, (55000, 53000, 51000), (50, 50, 50),
        campus_name="鱼山校区",
    ),
    # 中外合作办学样本，带面试要求
    DemoPlan(
        "DEMO372", "计算机类", "0809", "PHY_AND_CHM", 30, 26000, (88000, 85000, 82000), (28, 29, 30),
        cooperative="CHINESE_FOREIGN",
        special_requirements="中外合作办学，学费较高；需通过学校组织的英语面试",
    ),
    # 单科要求样本
    DemoPlan(
        "DEMO372", "英语", "050201", "UNRESTRICTED", 45, 4400, (95000, 92000, 89000), (44, 45, 46),
        requirement_specs=(
            {
                "requirement_type": "SINGLE_SUBJECT",
                "operator": "GTE",
                "operand": {"subject_code": "ENG", "min_score": 115},
                "raw_text": "英语单科成绩不低于 115 分",
            },
        ),
    ),
    # 选科 "物理或历史" 样本
    DemoPlan(
        "DEMO373", "法学类", "0301", "PHY_OR_HIS", 40, 5000, (58000, 56000, 54000), (38, 40, 42),
    ),
)


DEMO_UNIVERSITIES: tuple[DemoUniversity, ...] = build_universities()
DEMO_PLANS: tuple[DemoPlan, ...] = (*build_plans(DEMO_UNIVERSITIES), *SHANDONG_EDGE_CASES)

DEMO_MAJORS: tuple[DemoMajor, ...] = (
    DemoMajor("0809", "计算机类", "MAJOR_CLASS", "08", "工学", "0809", "计算机类", "工学学士"),
    DemoMajor("080901", "计算机科学与技术", "MAJOR", "08", "工学", "0809", "计算机类", "工学学士"),
    DemoMajor("080902", "软件工程", "MAJOR", "08", "工学", "0809", "计算机类", "工学学士"),
    DemoMajor("080701", "电子信息工程", "MAJOR", "08", "工学", "0807", "电子信息类", "工学学士"),
    DemoMajor("080202", "机械设计制造及其自动化", "MAJOR", "08", "工学", "0802", "机械类", "工学学士"),
    DemoMajor("081001", "土木工程", "MAJOR", "08", "工学", "0810", "土木类", "工学学士"),
    DemoMajor("081301", "化学工程与工艺", "MAJOR", "08", "工学", "0813", "化工与制药类", "工学学士"),
    DemoMajor("0701", "数学类", "MAJOR_CLASS", "07", "理学", "0701", "数学类", "理学学士"),
    DemoMajor("070101", "数学与应用数学", "MAJOR", "07", "理学", "0701", "数学类", "理学学士"),
    DemoMajor("0710", "生物科学类", "MAJOR_CLASS", "07", "理学", "0710", "生物科学类", "理学学士"),
    DemoMajor("100201", "临床医学", "MAJOR", "10", "医学", "1002", "临床医学类", "医学学士"),
    DemoMajor("1007", "药学类", "MAJOR_CLASS", "10", "医学", "1007", "药学类", "理学学士"),
    DemoMajor("0201", "经济学类", "MAJOR_CLASS", "02", "经济学", "0201", "经济学类", "经济学学士"),
    DemoMajor("1202", "工商管理类", "MAJOR_CLASS", "12", "管理学", "1202", "工商管理类", "管理学学士"),
    DemoMajor("0501", "中国语言文学类", "MAJOR_CLASS", "05", "文学", "0501", "中国语言文学类", "文学学士"),
    DemoMajor("050201", "英语", "MAJOR", "05", "文学", "0502", "外国语言文学类", "文学学士"),
    DemoMajor("0301", "法学类", "MAJOR_CLASS", "03", "法学", "0301", "法学类", "法学学士"),
)

DEMO_SOURCE_DOCUMENTS: tuple[dict, ...] = (
    {
        "title": "DEMO 2026 年全国招生计划演示数据（虚构数据，非官方）",
        "publisher": "DEMO 数据集（非官方）",
        "quality": "DEMO",
        "archive_path": "data/raw/demo/demo_plans_2026.csv",
        "notes": (
            "用于功能开发与回归测试的虚构招生计划，覆盖 31 个省级行政区。"
            "院校名称为占位名称，禁止用于真实填报。"
        ),
    },
    {
        "title": "DEMO 历年投档结果演示数据（虚构数据，非官方）",
        "publisher": "DEMO 数据集（非官方）",
        "quality": "DEMO",
        "archive_path": "data/raw/demo/demo_outcomes.csv",
        "notes": "包含 2023-2025 三个年度的虚构最低位次，用于 ACS 计算演示。",
    },
    {
        "title": "DEMO 一分一段表与考生人数（虚构数据，非官方）",
        "publisher": "DEMO 数据集（非官方）",
        "quality": "DEMO",
        "archive_path": "data/raw/demo/demo_score_rank.csv",
        "notes": "用于演示分数换算位次，数值为演示而构造。",
    },
)

__all__ = [
    "DEMO_APPLICATION_LIMIT",
    "DEMO_EXAM_YEAR",
    "DEMO_EXAM_YEAR_STATS",
    "DEMO_HISTORY_YEARS",
    "DEMO_MAJORS",
    "DEMO_PLANS",
    "DEMO_POLICY_VERSION",
    "DEMO_PROVINCE_CODE",
    "DEMO_SCORE_LINES",
    "DEMO_SCORE_SEGMENTS",
    "DEMO_SNAPSHOT_CODE",
    "DEMO_SOURCE_DOCUMENTS",
    "DEMO_UNIVERSITIES",
    "PROVINCES",
    "SHANDONG_EDGE_CASES",
    "SUBJECT_NAMES",
    "SUBJECT_RULES",
    "TIERS",
    "DemoMajor",
    "DemoPlan",
    "DemoUniversity",
    "build_plans",
    "build_universities",
]
