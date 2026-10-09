"""Generate the product introduction document (Outbound V1).

Content is drawn only from what the repository actually implements; no
performance or admission claims are invented.

Run: python scripts/make_product_doc.py
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT = Path(__file__).resolve().parents[1] / "docs" / "PRODUCT_INTRODUCTION.docx"

CN_FONT = "微软雅黑"
EN_FONT = "Segoe UI"
ACCENT = RGBColor(0x1D, 0x4E, 0xD8)
MUTED = RGBColor(0x55, 0x5F, 0x70)

RISK_COLORS = ["#DC2626", "#F97316", "#FBBF24", "#0EA5E9", "#059669"]


# ---------------------------------------------------------------- low level


def set_cjk(run, font: str = CN_FONT) -> None:
    run.font.name = font
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    fonts.set(qn("w:ascii"), EN_FONT)
    fonts.set(qn("w:hAnsi"), EN_FONT)
    fonts.set(qn("w:eastAsia"), font)


def shade(cell, hex_color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def cell_text(cell, text: str, *, bold: bool = False, size: int = 9, color=None, align=None) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    if align is not None:
        paragraph.alignment = align
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = color
    set_cjk(run)
    paragraph.paragraph_format.space_before = Pt(2)
    paragraph.paragraph_format.space_after = Pt(2)


def add_table(document, headers: list[str], rows: list[list[str]], widths: list[float] | None = None):
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    # Repeat the header row when a table spans pages; otherwise the continuation rows
    # appear without column labels and the reader cannot tell which column is which.
    header_row = table.rows[0]
    tr_pr = header_row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell_text(cell, header, bold=True, size=9)
        shade(cell, "EEF2FF")
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cell_text(cells[index], value, size=9)
    if widths:
        for row in table.rows:
            for index, width in enumerate(widths):
                row.cells[index].width = Cm(width)
    document.add_paragraph()
    return table


def para(document, text: str, *, size: int = 10.5, color=None, bold: bool = False, space_after: int = 6):
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(space_after)
    paragraph.paragraph_format.line_spacing = 1.35
    run = paragraph.add_run(text)
    run.font.size = Pt(size)
    run.bold = bold
    if color is not None:
        run.font.color.rgb = color
    set_cjk(run)
    return paragraph


def bullet(document, text: str, *, size: int = 10.5) -> None:
    paragraph = document.add_paragraph(style="List Bullet")
    paragraph.paragraph_format.space_after = Pt(3)
    run = paragraph.add_run(text)
    run.font.size = Pt(size)
    set_cjk(run)


def callout(document, title: str, body: str, *, fill: str = "EFF6FF") -> None:
    table = document.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    cell = table.rows[0].cells[0]
    shade(cell, fill)
    cell.text = ""
    head = cell.paragraphs[0]
    head.paragraph_format.space_after = Pt(2)
    run = head.add_run(title)
    run.bold = True
    run.font.size = Pt(10)
    run.font.color.rgb = ACCENT
    set_cjk(run)
    body_paragraph = cell.add_paragraph()
    body_paragraph.paragraph_format.space_after = Pt(2)
    body_run = body_paragraph.add_run(body)
    body_run.font.size = Pt(9.5)
    set_cjk(body_run)
    document.add_paragraph()


def heading(document, text: str, level: int):
    h = document.add_heading(text, level=level)
    for run in h.runs:
        set_cjk(run, CN_FONT)
        run.font.color.rgb = ACCENT if level <= 2 else RGBColor(0x1F, 0x2A, 0x37)
    return h


# ---------------------------------------------------------------- document


def build() -> Document:
    document = Document()

    core = document.core_properties
    core.title = "山东高考智能志愿系统 V1 · 产品介绍"
    core.author = "产品团队"
    core.subject = "面向山东夏季高考普通类考生的志愿决策系统"
    core.comments = "本文档为对外产品介绍。ACS 是录取安全指数，不是录取概率。"

    section = document.sections[0]
    section.top_margin = Cm(2.4)
    section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.6)
    section.right_margin = Cm(2.6)

    normal = document.styles["Normal"]
    normal.font.size = Pt(10.5)
    normal.font.name = EN_FONT
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)

    # ---------------- cover ----------------
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(90)
    title_run = title.add_run("山东高考智能志愿系统")
    title_run.bold = True
    title_run.font.size = Pt(30)
    title_run.font.color.rgb = ACCENT
    set_cjk(title_run)

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle_run = subtitle.add_run("V1 · 产品介绍")
    subtitle_run.font.size = Pt(16)
    subtitle_run.font.color.rgb = MUTED
    set_cjk(subtitle_run)

    tagline = document.add_paragraph()
    tagline.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tagline.paragraph_format.space_before = Pt(28)
    tagline_run = tagline.add_run(
        "把“能不能报、够不够稳、想不想去”分开算清楚\n为山东普通类考生生成可解释、可编辑、可体检的最多 96 个志愿方案"
    )
    tagline_run.font.size = Pt(12)
    set_cjk(tagline_run)

    meta = document.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.paragraph_format.space_before = Pt(60)
    meta_run = meta.add_run(
        "适用范围：山东省 · 夏季高考 · 普通类 · 常规批第 1 次志愿 · 本科计划\n"
        "本文档为产品介绍，不构成录取承诺"
    )
    meta_run.font.size = Pt(9.5)
    meta_run.font.color.rgb = MUTED
    set_cjk(meta_run)

    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---------------- 1. 一页速览 ----------------
    heading(document, "一、一页速览", 1)

    callout(
        document,
        "一句话定位",
        "在官方招生规则与历年录取数据约束下，为考生把 96 个志愿从“凭感觉填”变成"
        "“每一条都有理由、每一步都能改、改完还能体检”的决策方案。",
    )

    para(
        document,
        "系统输出的是决策辅助方案，不是录取保证。这是我们最重要的产品前提："
        "我们不预测录取结果，而是把做出决策所需的全部依据摆到考生面前。",
        color=MUTED,
        size=10,
    )

    heading(document, "核心能力一览", 2)
    add_table(
        document,
        ["能力", "解决什么问题", "产品呈现"],
        [
            ["报考资格筛查", "选科、单科、语种、体检等条件不符，填了也白填", "每条志愿标注「符合 / 待确认 / 不符合」"],
            ["录取安全指数 ACS", "看不出某个专业到底“够不够稳”", "0~100 指数 + 五档风险层 + 逐年计算依据"],
            ["偏好建模", "学校、专业、城市、学费、未来方向混在一起说不清", "七个维度分别打分，权重可调"],
            ["志愿组合优化", "96 个位置怎么分配冲稳保", "按风险模式生成组合，不为凑数塞志愿"],
            ["志愿顺序编排", "顺序错了等于浪费机会", "越想去越靠前；同一意愿层内更难的先试"],
            ["志愿体检", "填完不放心，不知道哪里会出问题", "11 项检查逐条给结论和修改建议"],
            ["版本管理", "改来改去，改乱了没法回头", "每次修改留版本，历史方案随时可查"],
        ],
        widths=[3.2, 6.6, 6.0],
    )

    heading(document, "三个必须说清楚的边界", 2)
    add_table(
        document,
        ["边界", "我们的说法"],
        [
            ["ACS 是什么", "ACS 是「录取安全指数」，反映历史数据下的安全程度。它不是录取概率，我们没有做概率校准"],
            ["系统保证什么", "保证资格判断有规则依据、数据可追溯、方案可回溯、修改可撤销。不保证录取结果"],
            ["数据来源", "已接入山东 2022–2025 普通类常规批真实招生与录取数据；数据来源为第三方整理包，系统会明确标注并提示以官方指南核对"],
        ],
        widths=[3.6, 12.2],
    )

    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---------------- 2. 需求 ----------------
    heading(document, "二、为什么需要它", 1)

    heading(document, "山东普通类的填报现实", 2)
    para(
        document,
        "山东夏季高考普通类常规批实行“专业（专业类）+学校”的平行志愿，第 1 次志愿可以填报"
        "最多 96 个志愿单位。这个设计的初衷是降低落榜风险，但同时也带来三个非常具体的困难。",
    )

    add_table(
        document,
        ["困难", "具体表现", "后果"],
        [
            [
                "信息量远超人工处理能力",
                "可选志愿单位以万计，每个都涉及选科要求、单科要求、体检要求、招生人数、学费、校区",
                "多数家庭只能凭学校名气或往年分数粗略缩小范围",
            ],
            [
                "96 个位置需要结构化分配",
                "全部填“冲”会落榜，全部填“保”会浪费分数，冲稳保各放多少没有明确依据",
                "要么过于保守浪费位次，要么过于激进导致滑档",
            ],
            [
                "规则与偏好被混为一谈",
                "“这个专业我能不能报”和“这个专业我想不想去”是两件事，但常常一起拍脑袋决定",
                "把精力花在不可能录取的方向上，或错过真正合适的选项",
            ],
        ],
        widths=[3.4, 7.4, 5.0],
    )

    heading(document, "现有工具的不足", 2)
    bullet(document, "查分工具只回答“往年多少分能上”，不回答“我能不能报”——选科和体检要求经常被忽略。")
    bullet(document, "推荐工具给出一个排名或分数，但说不出这个结论是怎么来的，考生无法判断该不该信。")
    bullet(document, "一些工具用“录取概率 78%”这类表述，但并未做概率校准，容易让考生产生错误的安全感。")
    bullet(document, "几乎所有工具都止步于“推荐一个列表”，而志愿填报真正的难点在于 96 个位置怎么排序、怎么取舍。")

    callout(
        document,
        "我们选择的方向",
        "不做一个更准的预测器，而是做一个更清楚的决策台：把规则判断、历史依据、个人偏好拆开算，"
        "让每一个结论都能被追问“为什么”。",
        fill="F0FDF4",
    )

    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---------------- 3. 产品概述 ----------------
    heading(document, "三、产品概述", 1)

    heading(document, "适用范围（V1）", 2)
    add_table(
        document,
        ["维度", "V1 支持范围"],
        [
            ["省份", "山东省"],
            ["考试类型", "夏季高考"],
            ["类别", "普通类"],
            ["批次", "常规批"],
            ["轮次", "第 1 次志愿"],
            ["教育层次", "本科计划"],
            ["志愿单位", "专业（专业类）+ 学校"],
            ["志愿数量", "读取当年政策上限，当前为 96 个"],
        ],
        widths=[3.6, 12.2],
    )
    para(
        document,
        "V1 不包含提前批、艺术类、体育类、春季高考、常规批第 2/3 次志愿与专科推荐。"
        "这些范围在数据层就被拒绝，不会出现“能查到但结果不可靠”的情况。",
        color=MUTED,
        size=9.5,
    )

    heading(document, "六步使用流程", 2)
    add_table(
        document,
        ["步骤", "页面", "考生要做的事", "系统给出的结果"],
        [
            ["1", "成绩与选科", "填写分数或位次、三门选考科目、单科成绩与特殊条件", "由分数换算位次，建立考生档案"],
            ["2", "偏好画像", "选择专业方向、地域、学校层次、学费上限、风险偏好", "生成偏好档案，可生成多个版本"],
            ["3", "推荐结果", "浏览候选志愿，按风险层筛选，查看每条的完整依据", "分层候选池，含 ACS、可信度、偏好匹配、近年位次"],
            ["4", "志愿编辑器", "拖拽排序、增删、锁定、重新优化", "正式志愿方案，每次修改生成新版本"],
            ["5", "志愿体检", "运行体检，逐条处理问题", "11 项检查结论与修改建议"],
            ["6", "导出", "导出最终顺序用于填报参考", "CSV 文件，含顺序与关键指标"],
        ],
        widths=[1.4, 2.8, 6.2, 5.4],
    )

    heading(document, "系统输出什么、不输出什么", 2)
    add_table(
        document,
        ["系统会输出", "系统不会输出"],
        [
            ["每条志愿的报考资格结论与具体原因", "录取可能性百分比"],
            ["0~100 的录取安全指数与计算明细", "对录取结果的任何承诺"],
            ["七个维度的偏好匹配说明", "替考生做最终决定"],
            ["最多 96 个志愿的完整方案与顺序依据", "超过政策上限的志愿数量"],
            ["11 项体检结论与改进建议", "自动代替考生提交志愿"],
        ],
        widths=[7.9, 7.9],
    )

    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---------------- 4. 核心能力 ----------------
    heading(document, "四、核心能力", 1)

    heading(document, "4.1 报考资格筛查：先回答“能不能报”", 2)
    para(
        document,
        "很多填报失误的根源不是分数不够，而是条件不符。系统首先对每一条志愿做资格判断，"
        "结论只有三种，含义完全明确：",
    )
    add_table(
        document,
        ["结论", "含义", "系统如何处理"],
        [
            ["符合", "所有已知条件都明确满足", "正常进入推荐范围"],
            ["待确认", "存在无法自动核实的要求（如需面试、体检结论未填）", "可以保留，但会被明确标记，且不计入保底"],
            ["不符合", "存在明确不满足的硬性条件", "排除在正式推荐之外"],
        ],
        widths=[2.6, 8.4, 4.8],
    )
    para(
        document,
        "关键设计：无法确认的信息一律标为「待确认」，绝不猜测成「符合」。"
        "宁可让考生自己核对章程，也不给一个可能错误的安心结论。",
        color=MUTED,
        size=9.5,
    )

    heading(document, "4.2 录取安全指数 ACS：回答“够不够稳”", 2)
    para(
        document,
        "ACS 是 0~100 的指数，数值越高表示从历史数据看录取越安全。它由三部分构成："
        "历史录取位置对比、招生计划变化、近年竞争趋势，再根据数据可信度做收缩处理。",
    )

    heading(document, "五档风险层", 3)
    table = document.add_table(rows=2, cols=5)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    labels = [("高冲", "0 ~ 29"), ("冲", "30 ~ 44"), ("边界", "45 ~ 59"), ("稳", "60 ~ 79"), ("保", "80 ~ 100")]
    for index, (name, span) in enumerate(labels):
        cell = table.rows[0].cells[index]
        cell_text(cell, name, bold=True, size=11, color=RGBColor(0xFF, 0xFF, 0xFF), align=WD_ALIGN_PARAGRAPH.CENTER)
        shade(cell, RISK_COLORS[index].lstrip("#"))
        cell_text(table.rows[1].cells[index], span, size=9, align=WD_ALIGN_PARAGRAPH.CENTER)
    document.add_paragraph()

    heading(document, "为什么不能叫“录取概率”", 3)
    para(
        document,
        "要给出一个可信的概率，需要用历史数据做预测、再用实际结果回测校准。V1 没有做这件事，"
        "所以整个产品——包括界面、接口、导出文件——都不使用“录取概率”这一表述。"
        "我们把不确定性如实呈现为“安全指数 + 数据可信度”，而不是把它包装成一个看起来精确的百分比。",
    )

    para(document, "以当前数据中的两条真实志愿为例。考生位次 60,000（全省普通类一段约 33.3 万人，位次百分位 18%）：", size=10)
    add_table(
        document,
        ["展示项", "中南大学 · 材料类", "佳木斯大学 · 临床医学"],
        [
            ["ACS", "15.4", "84.4"],
            ["基础指数", "9.6", "90.6"],
            ["数据可信度", "0.86", "0.85"],
            ["风险层", "高冲", "保"],
            ["近年最低位次", "2022:14,373 → 2025:13,590", "2022:99,719 → 2025:128,920"],
            ["历史覆盖", "4 年齐备", "4 年齐备"],
            ["结论", "位次远低于历史最低位次，属冲刺型", "位次显著高于历史最低位次，属保底型"],
        ],
        widths=[3.0, 6.4, 6.4],
    )
    para(
        document,
        "两条志愿给出的都不是一个孤立的数字，而是一套可以自己复核的依据："
        "历史逐年位次、计划变化、数据可信度分项，考生可以顺着任一项去核对原始数据。",
        color=MUTED,
        size=9.5,
    )

    heading(document, "4.3 偏好建模：回答“有多想去”", 2)
    para(
        document,
        "偏好和资格是两件事，因此分开计算。系统把考生的意愿拆成七个维度，每个维度独立打分后加权汇总：",
    )
    add_table(
        document,
        ["维度", "默认权重", "考生怎么表达"],
        [
            ["专业匹配", "35%", "按学科门类选，或直接排除绝不接受的专业"],
            ["学校匹配", "15%", "指定学校，或勾选 985 / 211 / 双一流等特征"],
            ["地域匹配", "15%", "优先考虑的省份，或绝不考虑的省份"],
            ["升学 / 学校特征", "10%", "更看重读研升学率还是就业"],
            ["职业方向", "10%", "倾向的行业与岗位方向"],
            ["学费", "10%", "设置学费上限与期望区间"],
            ["个性化偏好", "5%", "其他个性化要求"],
        ],
        widths=[3.6, 2.4, 9.8],
    )
    callout(
        document,
        "一个容易被忽略的细节",
        "如果某个维度考生没有填，系统会把它从计算分母中剔除，而不是按 0 分处理。"
        "否则“信息填得少”会变成“所有志愿匹配度都很低”，这对考生不公平。",
    )

    heading(document, "4.4 志愿组合优化：96 个位置怎么分配", 2)
    para(
        document,
        "有了资格、安全和偏好三个结果，接下来的问题是：在 96 个位置里，冲、稳、保各放多少？"
        "系统提供三种预设风险模式，考生也可以自定义。",
    )
    add_table(
        document,
        ["风险模式", "高冲", "冲", "边界", "稳", "保", "适合谁"],
        [
            ["稳健", "5", "10", "15", "36", "30", "宁可稳一点，不愿冒落榜风险"],
            ["均衡", "8", "17", "20", "31", "20", "希望兼顾机会与安全（默认）"],
            ["激进", "15", "25", "20", "24", "12", "位次有优势，愿意争取更好的学校"],
        ],
        widths=[2.6, 1.7, 1.5, 1.8, 1.6, 1.6, 5.0],
    )
    para(document, "优化过程遵守四条规则：", size=10)
    bullet(document, "风险层目标是可以浮动的建议值，不是死规定；系统会尽量接近，但不会为达标而降低质量。")
    bullet(document, "考生明确排除的专业、地域、学校、学费上限，在任何情况下都不会被推荐。")
    bullet(document, "标记为「待确认」的志愿不会被算作保底，避免把不确定性当作安全垫。")
    bullet(document, "如果真正符合条件的志愿只有 78 个，系统就返回 78 个，并明确告诉你放宽哪些条件可以增加候选。")

    heading(document, "4.5 志愿顺序编排：把最想去的排前面", 2)
    para(
        document,
        "组合优化决定“选哪些”，排序决定“怎么填”，这是两个独立步骤。平行志愿的投档规则是"
        "“分数优先、遵循志愿”，因此顺序本身就会影响结果。系统的排序逻辑是：",
    )
    add_table(
        document,
        ["优先级", "排序依据", "设计理由"],
        [
            ["1", "考生手动固定的位置", "考生的明确意愿优先于任何算法"],
            ["2", "意愿层级（越想去越靠前）", "顺序首先代表真实意愿"],
            ["3", "同一意愿层内，ACS 更低者靠前", "同等想去程度下，先尝试更难的"],
            ["4", "偏好匹配度更高者靠前", "细化同一层级内的取舍"],
            ["5", "数据可信度更高者靠前", "同等条件下优先选依据更充分的"],
        ],
        widths=[1.8, 6.0, 8.0],
    )

    heading(document, "4.6 志愿体检：填完之后的最后一道检查", 2)
    para(document, "方案生成后，考生可以随时运行体检。系统会执行 11 项检查，逐条给出结论与建议：")
    add_table(
        document,
        ["检查项", "检查什么"],
        [
            ["报考资格", "方案中是否存在条件不符的志愿"],
            ["重复志愿", "同一个志愿单位是否被填了两次"],
            ["志愿数量上限", "是否超过政策规定的数量上限"],
            ["待确认要求", "是否存在需要人工核对的要求，以及是否被误当作保底"],
            ["风险分布", "冲稳保比例是否合理，保底是否过薄"],
            ["志愿顺序", "是否存在“更想去的排在后面”的顺序问题"],
            ["数据可信度", "是否存在历史依据薄弱的志愿"],
            ["招生人数", "是否存在招生人数过少的志愿"],
            ["计划缩减", "是否存在今年大幅缩招、竞争预计加剧的志愿"],
            ["数据时效", "方案所依据的数据是否已经过期"],
            ["硬性偏好", "是否出现考生明确排除的专业、地域或超预算情况"],
        ],
        widths=[3.6, 12.2],
    )
    callout(
        document,
        "体检只诊断，不修改",
        "体检报告会指出问题并给出建议，但不会自动改动考生的方案。"
        "志愿是考生的决定，系统只负责把问题说清楚。",
        fill="FEF3C7",
    )

    heading(document, "4.7 版本管理：改错了能回头", 2)
    para(
        document,
        "志愿填报期往往持续数天，方案会被反复修改。系统为每一次修改保留一个不可覆盖的历史版本："
        "调整顺序、增删志愿、锁定、重新优化，都会生成新版本，旧版本随时可以打开查看。",
    )
    bullet(document, "历史版本完整可读，包含当时的顺序、风险层与各项指标。")
    bullet(document, "多人协作或跨设备编辑时，如果版本已过期，系统会提示“方案已在其他位置被修改”，而不是静默覆盖。")
    bullet(document, "每个方案都记录了所依据的考生档案版本、偏好版本、数据版本与算法版本，可追溯。")

    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---------------- 5. 差异化 ----------------
    heading(document, "五、核心差异化", 1)

    heading(document, "1. 三层严格分离，结论不互相污染", 2)
    para(
        document,
        "资格、安全、偏好是三个独立问题，系统在实现上把它们彻底分开："
        "偏好再高也不会让一个不能报的志愿进入方案；数据再乐观也不会改变考生说过的“绝不接受”。"
        "这种分离是产品可信度的基础。",
    )

    heading(document, "2. 每个结论都能被追问", 2)
    para(
        document,
        "任意一条志愿，考生都可以展开看到：ACS 的三个组成部分、数据可信度的六个来源、"
        "偏好七个维度各自的得分与权重、近三年的最低位次与招生计划。"
        "我们不要求考生信任一个黑盒结论。",
    )

    heading(document, "3. 不做未经校准的概率宣传", 2)
    para(
        document,
        "这是产品价值观层面的选择。用“录取概率 78%”能显著提升转化率，但 V1 没有做概率校准，"
        "给出这样的数字是不负责任的。我们用安全指数与可信度两个维度如实表达确定性程度。",
    )

    heading(document, "4. 考生的硬性要求不可被算法突破", 2)
    para(
        document,
        "考生标记为“绝不接受”的专业、地域、学校，以及学费上限，在推荐、优化、排序、"
        "体检四个环节都会被独立校验。这条规则不参与任何权衡，也不会因为“凑满 96 个”而被放松。",
    )

    heading(document, "5. 数据来源可追溯", 2)
    para(
        document,
        "每一条招生计划与历史录取数据都关联到具体的数据来源与数据版本，可追溯到文件级校验值；"
        "数据是否来自官方发布会被明确标注，系统在使用非官方来源的数据时给出显著提示。",
    )

    heading(document, "6. 完整的流程闭环", 2)
    para(
        document,
        "从录入成绩到导出方案，六个环节在同一个系统内完成，不需要在多个工具之间搬运数据。"
        "每一步的产出都是下一步的输入，减少了信息丢失和人工转录错误。",
    )

    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---------------- 6. 可信与安全 ----------------
    heading(document, "六、可信与安全", 1)

    heading(document, "我们如何保证结论可靠", 2)
    add_table(
        document,
        ["机制", "作用"],
        [
            ["规则判断不依赖模型", "是否符合报考条件由确定性规则得出，不使用大模型推测政策"],
            ["无法确认即标注", "信息不足时输出「待确认」并提示人工核对，不猜测为符合"],
            ["数据可信度参与计算", "历史数据不足或波动较大时，指数会向中位收缩，避免过度自信"],
            ["政策参数读取自数据库", "志愿数量上限等政策数字不写死在界面或代码中，政策调整无需改版"],
            ["全程留痕", "方案修改、版本变更、体检结果均保留记录"],
        ],
        widths=[4.4, 11.4],
    )

    heading(document, "考生信息保护", 2)
    bullet(document, "只采集规则判断必需的信息，不做与填报无关的收集。")
    bullet(document, "体检结论等敏感信息仅用于资格判断，并在界面上明确说明用途。")
    bullet(document, "填报进度保存在浏览器本地，不上传第三方。")

    heading(document, "系统质量状况", 2)
    para(
        document,
        "截至当前版本，核心逻辑（资格规则、安全指数、偏好计算、组合优化、排序、体检）"
        "共有 337 项自动化测试（335 项通过、2 项因数据条件跳过），"
        "并有一次覆盖完整业务流程的端到端验收（52 项断言）。"
        "这些测试每次改动后都会重新运行。",
        size=10,
    )
    para(
        document,
        "需要如实说明的是：当前数据覆盖山东 2022–2025 年普通类常规批第 1 次志愿的本科招生计划，"
        "共 1,153 所院校、7.3 万条招生计划、7.3 万条历史录取记录，覆盖 32 个省级行政区。"
        "数据来源为第三方整理包而非省考试院官方发布，系统已明确标注；"
        "正式对外提供服务前建议接入官方《填报志愿指南》数据并重新生成方案。",
        color=MUTED,
        size=9.5,
    )

    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---------------- 7. 用户与场景 ----------------
    heading(document, "七、目标用户与使用场景", 1)

    add_table(
        document,
        ["用户", "典型场景", "系统提供的价值"],
        [
            [
                "考生本人",
                "出分后有几天时间决定 96 个志愿，希望尽快理清方向",
                "用一条完整流程把方向定下来，并知道每条志愿的理由",
            ],
            [
                "考生家长",
                "不熟悉选科与体检等细节，担心漏掉硬性条件",
                "资格筛查把“填了也白填”的选项提前剔除",
            ],
            [
                "高中班主任 / 升学指导老师",
                "需要同时跟进多名学生，难以逐个核对规则",
                "标准化的档案、方案与体检报告，便于批量辅导",
            ],
            [
                "志愿填报咨询机构",
                "需要在有限时间内给出有依据、可交付的方案",
                "完整的依据链与版本记录，可直接用于客户沟通",
            ],
        ],
        widths=[3.4, 6.4, 6.0],
    )

    heading(document, "一个典型使用过程", 2)
    para(
        document,
        "以位次 60000 名左右的普通类考生为例：录入成绩与选科后，系统先筛掉选科不符的志愿，"
        "再对剩余志愿逐个计算安全指数，并按偏好打分。考生在推荐页看到按风险层分组的候选志愿，"
        "可以选择均衡模式生成初始方案，然后在编辑器里把最想去的几所调到前面、锁定两三个最满意的、"
        "再对剩下的位置重新优化。最后运行体检，系统提示“某专业今年计划从 50 人缩减到 5 人，"
        "ACS 可能偏乐观，不建议作为保底”，考生据此补充两个更稳的志愿。",
    )

    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---------------- 8. 路线图 ----------------
    heading(document, "八、后续规划", 1)
    para(document, "以下为方向性规划，不代表承诺的交付时间：", color=MUTED, size=9.5)
    add_table(
        document,
        ["方向", "内容"],
        [
            ["数据接入", "对接官方招生计划、投档结果与一分一段数据，建立可持续更新的数据流程"],
            ["概率校准", "在积累足够历史数据后，尝试建立经回测校准的录取可能性模型"],
            ["范围扩展", "在普通类常规批稳定运行后，评估扩展至其他批次与类别的可行性"],
            ["结果跟踪", "记录考生实际录取结果，用于评估建议质量并持续改进模型"],
            ["协作能力", "支持老师与家长共同查看与讨论同一份方案"],
        ],
        widths=[3.0, 12.8],
    )

    heading(document, "九、术语说明", 1)
    add_table(
        document,
        ["术语", "含义"],
        [
            ["志愿单位", "一个「专业（专业类）+学校」组合，是填报的最小单位"],
            ["位次", "考生在全省同类考生中的排名"],
            ["ACS / 录取安全指数", "0~100 的指数，反映历史数据下录取的安全程度，不是录取概率"],
            ["数据可信度", "对 ACS 所依据数据充分程度的评估，历史数据越少、波动越大则越低"],
            ["风险层", "按 ACS 划分的五个档位：高冲、冲、边界、稳、保"],
            ["偏好匹配度", "考生个人意愿与某条志愿的匹配程度，0~100"],
            ["候选池", "通过资格筛查与偏好过滤后，进入优化环节的志愿集合"],
            ["方案版本", "一次修改后的方案快照，旧版本保留可查"],
            ["志愿体检", "对已完成方案进行的规则、风险、顺序与时效诊断"],
        ],
        widths=[4.4, 11.4],
    )

    document.add_paragraph()
    closing = document.add_paragraph()
    closing.alignment = WD_ALIGN_PARAGRAPH.CENTER
    closing.paragraph_format.space_before = Pt(20)
    closing_run = closing.add_run(
        "本系统输出的是决策辅助方案，不是录取保证。\n"
        "ACS（录取安全指数）反映历史数据下的录取安全程度，不是录取概率。\n"
        "最终请以山东省教育招生考试院官方系统为准。"
    )
    closing_run.font.size = Pt(9.5)
    closing_run.font.color.rgb = MUTED
    set_cjk(closing_run)

    return document


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build().save(str(OUT))
    print(f"wrote {OUT}")
