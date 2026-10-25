"""Generate the draft answer deck (19 slides, 16:9) for the competition.

The deck is a draft: screenshot slots are explicit placeholders, and no school
or personal name appears anywhere in the slides. Rename the final file to the
organiser's format before submitting.

Run: .venv/Scripts/python.exe scripts/build_deck.py
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "submission" / "证据工作台-答辩PPT-草稿.pptx"

INK = RGBColor(0x14, 0x18, 0x1F)
MUTED = RGBColor(0x5B, 0x64, 0x73)
ACCENT = RGBColor(0x1F, 0x5E, 0xFF)
FONT = "Microsoft YaHei"


def _textbox(slide, left, top, width, height):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = box.text_frame
    frame.word_wrap = True
    return frame


def _style(run, size: int, color=INK, bold: bool = False) -> None:
    run.font.size = Pt(size)
    run.font.name = FONT
    run.font.color.rgb = color
    run.font.bold = bold


def _title(slide, text: str, subtitle: str = "") -> None:
    frame = _textbox(slide, 0.7, 0.45, 12.0, 1.1)
    paragraph = frame.paragraphs[0]
    _style(paragraph.add_run(), 30, INK, True)
    paragraph.runs[0].text = text
    if subtitle:
        line = frame.add_paragraph()
        _style(line.add_run(), 14, MUTED)
        line.runs[0].text = subtitle


def _bullets(slide, items, top=1.75, left=0.85, width=11.6, height=5.1, size=18):
    frame = _textbox(slide, left, top, width, height)
    first = True
    for item in items:
        level = 0
        text = item
        if isinstance(item, tuple):
            level, text = item
        paragraph = frame.paragraphs[0] if first else frame.add_paragraph()
        first = False
        paragraph.level = level
        paragraph.space_after = Pt(10)
        run = paragraph.add_run()
        run.text = ("• " if level == 0 else "– ") + text
        _style(run, size - 2 * level, INK if level == 0 else MUTED)


def _table(slide, rows, top=1.9, left=0.85, width=11.6, height=4.4, size=14):
    shape = slide.shapes.add_table(
        len(rows), len(rows[0]), Inches(left), Inches(top), Inches(width), Inches(height)
    )
    table = shape.table
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            cell = table.cell(row_index, column_index)
            cell.text = ""
            paragraph = cell.text_frame.paragraphs[0]
            run = paragraph.add_run()
            run.text = str(value)
            _style(run, size, INK if row_index else MUTED, bold=(row_index == 0))


def _placeholder(slide, text: str) -> None:
    frame = _textbox(slide, 1.2, 2.2, 10.9, 3.6)
    paragraph = frame.paragraphs[0]
    paragraph.alignment = PP_ALIGN.CENTER
    run = paragraph.add_run()
    run.text = f"[{text}]"
    _style(run, 20, MUTED)


def main() -> int:
    deck = Presentation()
    deck.slide_width = Inches(13.333)
    deck.slide_height = Inches(7.5)
    blank = deck.slide_layouts[6]
    slides = []

    # 1 cover
    slide = deck.slides.add_slide(blank)
    frame = _textbox(slide, 0.9, 2.0, 11.5, 2.2)
    run = frame.paragraphs[0].add_run()
    run.text = "证据工作台"
    _style(run, 54, INK, True)
    line = frame.add_paragraph()
    _style(line.add_run(), 24, ACCENT)
    line.runs[0].text = "可审计的多 Agent 资料研读与交付"
    tag = _textbox(slide, 0.95, 4.4, 11.0, 1.0)
    _style(tag.paragraphs[0].add_run(), 18, MUTED)
    tag.paragraphs[0].runs[0].text = "每条结论都能回到原文，冲突不被消解"
    link = _textbox(slide, 0.95, 6.2, 11.0, 0.6)
    _style(link.paragraphs[0].add_run(), 12, MUTED)
    link.paragraphs[0].runs[0].text = (
        "https://workbench-334475-5-1515867239.sh.run.tcloudbase.com"
    )
    slides.append(slide)

    # 2 background
    slide = deck.slides.add_slide(blank)
    _title(slide, "作品背景与说明", "政策研读需要的是有出处的结论")
    _bullets(
        slide,
        [
            "三个反复出现的问题",
            (1, "引用挂在答案末尾，读者看不出哪句话由哪条原文支撑"),
            (1, "两份文件要求不一致时，结论被悄悄折中"),
            (1, "模型说“已完成”，但结果没有通过任何结构化检查"),
            "目标用户：学校管理者、合规与教研人员",
            "方案：Leader 规划 + 两名成员分工取证与成稿，应用侧做引用与完成门禁",
        ],
    )
    slides.append(slide)

    # 3 data sources
    slide = deck.slides.add_slide(blank)
    _title(slide, "数据来源（1/2）", "全部为官方站点公开文本")
    _table(
        slide,
        [
            ["工作区", "文件数", "切片数", "主要来源"],
            ["AI 治理", "6", "92", "网信办、全国人大官网"],
            ["教育政策", "9", "173", "教育部、湖南省教育厅、中国政府网"],
            ["合计", "15", "265", "逐份记录抓取日期与原文 URL"],
        ],
    )
    slides.append(slide)

    # 4 data quality
    slide = deck.slides.add_slide(blank)
    _title(slide, "数据来源（2/2）", "权威性与时效性")
    _bullets(
        slide,
        [
            "权威性：只取官方站点原文，来源清单随仓库提交",
            "时效性：优先现行有效版本；无法核对纯文本的文件不纳入",
            "采集：官方页面 → 正文段落抽取 → 去导航/页脚 → 保留条号",
            "质量检查：逐份记录来源与条数；解析失败显式报错，不静默跳过",
            "边界：不含判例与地方细则全集，避免把“没收录”说成“没有规定”",
        ],
    )
    slides.append(slide)

    # 5 knowledge base
    slide = deck.slides.add_slide(blank)
    _title(slide, "知识库内容", "265 个切片，可定位到章节")
    _bullets(
        slide,
        [
            "中文 FTS5 trigram 索引；每块保留来源、修订、chars:start-end 定位",
            "每个工作区一个独立数据库：授权边界就是“打开哪个文件”",
            "检索 SQL 不跨语料；跨工作区隔离由离线断言钉住（7/7）",
            "降级可见：无向量时标记 keyword_only，不伪造相似度",
        ],
    )
    slides.append(slide)

    # 6 preprocessing
    slide = deck.slides.add_slide(blank)
    _title(slide, "预处理与利用", "抓取 → 解析 → 切片 → 索引 → 检索")
    _bullets(
        slide,
        [
            "抓取：curl 获取官方页面，按页面编码解码",
            "解析：正文段落抽取，去除导航/页脚，保留条号",
            "切片：目标 400 字、80 字重叠；记录 chars:start-end",
            "索引：SQLite FTS5；同一文档新版本先删旧块",
            "利用：成员调用 retrieve 工具取证，证据携带完整 provenance 进入引用校验",
        ],
        top=1.7,
    )
    slides.append(slide)

    # 7 method
    slide = deck.slides.add_slide(blank)
    _title(slide, "分析方法选择", "为什么是“多 Agent + 确定性门禁”")
    _bullets(
        slide,
        [
            "资料研读可拆成两类认知任务：取证与成稿；单 Agent 容易把检索结果直接当结论",
            "Leader 产出结构化计划，应用侧校验：无环、依赖存在、验收声明",
            "原生任务板派发，成员结构化回传；应用不建第二套调度",
            "确定性门禁：引用、完成、执行有效性；模型自评不替代程序校验",
        ],
    )
    slides.append(slide)

    # 8 task graph
    slide = deck.slides.add_slide(blank)
    _title(slide, "问题理解与逻辑论证", "一次真实运行的任务图")
    _placeholder(slide, "截图：任务图（两个节点、状态变化）")
    slides.append(slide)

    # 9 citation funnel
    slide = deck.slides.add_slide(blank)
    _title(slide, "引用与验收漏斗", "从检索块到可支撑的引用")
    _placeholder(slide, "图：检索块 265 → 单任务保留证据 → fact claims → 通过校验的引用")
    slides.append(slide)

    # 10 real numbers
    slide = deck.slides.add_slide(blank)
    _title(slide, "真实运行数据", "不预置答案，全部来自真实回合")
    _table(
        slide,
        [
            ["运行", "终态", "关键结果"],
            ["本地产品级验证", "team.completed", "11 条 fact 全部引用本任务证据"],
            ["公网 · AI 治理", "team_completed", "报告 59 KB"],
            ["公网 · 教育政策", "team_completed", "报告 68 KB"],
            ["离线检查", "39 脚本 / 630 断言", "0 失败"],
        ],
    )
    slides.append(slide)

    # 11 innovation
    slide = deck.slides.add_slide(blank)
    _title(slide, "创新点展示", "三个可验证的差异")
    _bullets(
        slide,
        [
            "结论级引用 + “实际收到”的可判定定义：按任务检索集判定，而不是按语料里有没有",
            "冲突保留：跨来源、同句式、不同数值 → 并列双方原文与定位，系统不裁决",
            "真实委派 + 可审计终态：原生调度器拉起成员；只有 team.completed 且退出码 0 才算成功",
        ],
    )
    slides.append(slide)

    # 12 perspective
    slide = deck.slides.add_slide(blank)
    _title(slide, "独特视角", "教育 × 合规审核")
    _bullets(
        slide,
        [
            "不是“会聊天的法律助手”，而是“能交差的可审计工作底稿”",
            "把 RAG 的评价对象从“答案像不像”换成“结论有没有出处、冲突有没有保留、完成有没有验证”",
            "对 AI 进校园的现实价值：先解决“谁依据哪条、哪里不一致”，再谈教学创新",
        ],
    )
    slides.append(slide)

    # 13 results
    slide = deck.slides.add_slide(blank)
    _title(slide, "结果解读", "把“读文件”变成“可核对的清单”")
    _bullets(
        slide,
        [
            "数分钟内产出带引用的研读报告，可下载为 Markdown",
            "降低合规遗漏与误引：每条事实都能回到来源与章节",
            "不是万能问答：未收录的资料不会凭空出现，证据不足会显式说明",
        ],
    )
    slides.append(slide)

    # 14 problems 1
    slide = deck.slides.add_slide(blank)
    _title(slide, "问题与建议（1/2）", "已知限制与优化方向")
    _bullets(
        slide,
        [
            "公网不开放上传与 shell/文件工具（安全取舍）",
            (1, "建议：赛后增加本地/管理员模式 + 沙箱工具包，公网保持只读"),
            "未挂持久卷，重启后运行历史与索引重建",
            (1, "建议：挂 CFS 或对象存储归档；索引可自动重建，历史需持久化"),
        ],
    )
    slides.append(slide)

    # 15 problems 2
    slide = deck.slides.add_slide(blank)
    _title(slide, "问题与建议（2/2）", "优化优先级")
    _bullets(
        slide,
        [
            "固定三成员名册，依赖型任务的评审流程尚未在真实调度器验证",
            (1, "建议：真实回合验证 reviewer 结算后恢复 research→engineering 依赖"),
            "优先级：持久化 → 通用工具包 → 更多工作区 → 自适应模型路由",
        ],
    )
    slides.append(slide)

    # 16 risks
    slide = deck.slides.add_slide(blank)
    _title(slide, "风险识别与应对", "把风险写在明处")
    _table(
        slide,
        [
            ["风险", "应对"],
            ["数据安全与隐私", "公网只读预置语料、无上传；会话隔离；密钥只在服务端"],
            ["幻觉与误引", "结论级引用 + 结构化验收；未验证结论改标"],
            ["成本与滥用", "并发/每日配额、回合截止时间、单实例锁"],
            ["平台依赖", "单容器镜像；换平台只换部署配置"],
        ],
        size=15,
    )
    slides.append(slide)

    # 17 UI
    slide = deck.slides.add_slide(blank)
    _title(slide, "应用结果呈现", "工作台界面")
    _placeholder(slide, "截图：登录页 / 工作区与目标输入 / 任务图 + 证据抽屉 / 报告与冲突面板")
    slides.append(slide)

    # 18 prompt 1
    slide = deck.slides.add_slide(blank)
    _title(slide, "提示词示例 1", "教育政策工作区")
    frame = _textbox(slide, 0.85, 1.7, 11.6, 1.0)
    _style(frame.paragraphs[0].add_run(), 16, INK)
    frame.paragraphs[0].runs[0].text = (
        "提示词：中小学使用生成式人工智能辅助教学时，学校和教师需要注意哪些要求？请给出依据条文。"
    )
    _placeholder(slide, "截图：报告“结论”与“证据”小节")
    slides.append(slide)

    # 19 prompts 2-3 + link
    slide = deck.slides.add_slide(blank)
    _title(slide, "提示词示例 2–3 与链接", "AI 治理工作区")
    _bullets(
        slide,
        [
            "示例 2：面向公众的生成式 AI 客服上线前，必须完成哪些合规动作？",
            "示例 3：深度合成服务提供者有哪些标识义务？不同文件的要求是否一致？",
        ],
        top=1.6,
        size=17,
    )
    _placeholder(slide, "截图：AI 治理报告 + 冲突候选面板")
    link = _textbox(slide, 0.85, 6.35, 11.6, 0.5)
    _style(link.paragraphs[0].add_run(), 13, ACCENT)
    link.paragraphs[0].runs[0].text = (
        "智能体链接：https://workbench-334475-5-1515867239.sh.run.tcloudbase.com"
    )
    slides.append(slide)

    # page numbers
    for index, slide in enumerate(deck.slides, start=1):
        frame = _textbox(slide, 12.35, 7.0, 0.8, 0.35)
        paragraph = frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.RIGHT
        _style(paragraph.add_run(), 10, MUTED)
        paragraph.runs[0].text = str(index)

    deck.save(OUTPUT)
    print(f"saved {OUTPUT} ({len(deck.slides)} slides)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
