"""Fetch the public education-policy documents used by the second workspace.

The corpus is committed: this script exists so the text can be re-fetched and
compared, not because the image downloads it at build time. Sources are
official Chinese government pages; the laws themselves are public documents.

Fetching goes through ``curl`` because this host's Python TLS stack rejects
some of these servers (``SSL: BAD_ECPOINT``). Encoding is detected per page
(UTF-8 first, then GB18030) and only paragraph text is kept.

Run: .venv/Scripts/python.exe scripts/fetch_education_corpus.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "corpus" / "education-policy"

SOURCES: tuple[tuple[str, str, str], ...] = (
    ("教育法", "https://jyt.hunan.gov.cn/jyt/sjyt/xxgk/zcfg/flfg/202205/t20220527_1070804.html", "中华人民共和国教育法.md"),
    ("学位法", "https://jyt.hunan.gov.cn/jyt/sjyt/xxgk/zcfg/flfg/202405/t20240513_1122444.html", "中华人民共和国学位法.md"),
    ("学前教育法", "http://www.moe.gov.cn/jyb_sjzl/sjzl_zcfg/zcfg_jyfl/202411/t20241108_1161363.html", "中华人民共和国学前教育法.md"),
    ("义务教育法", "http://www.moe.gov.cn/jyb_sjzl/sjzl_zcfg/zcfg_jyfl/202110/t20211029_575949.html", "中华人民共和国义务教育法.md"),
    ("职业教育条例", "https://jyt.hunan.gov.cn/jyt/sjyt/xxgk/zcfg/flfg/202412/t20241209_33521114.html", "湖南省职业教育条例.md"),
    ("未成年人网络保护条例", "https://www.gov.cn/zhengce/zhengceku/202310/content_6911289.htm", "未成年人网络保护条例.md"),
    ("未成年人保护法实施办法", "http://www.hunan.gov.cn/hnszf/szf/hnzb_18/c102939/202411/02222gqgsdsddtcfvurptpusubvvggrfbfffbrhukgepdkfqnfkqserucnbsqdb/202406/t20240614_33327758.html", "湖南省实施未成年人保护法办法.md"),
    ("预防中小学生溺水规定", "http://www.hunan.gov.cn/hnszf/szf/hnzb_18/c102939/202411/02222gqgsdsddtcfvurptpusubvvggrfbfffbrhukgepdkfqnfkqserucnbsqdb/202406/t20240614_33327763.html", "湖南省预防中小学生溺水若干规定.md"),
    ("普通高校招生工作通知", "https://jyt.hunan.gov.cn/jyt/sjyt/xxgk/zcfg/flfg/202606/t20260601_1179223.html", "2026年湖南省普通高校招生工作通知.md"),
)

_NOISE = (
    "ICP备",
    "网站标识码",
    "主办单位",
    "技术支持",
    "办公时间",
    "政府网",
    "打印本页",
    "关闭窗口",
    "扫一扫",
    "当前位置",
    "首页>",
)


def _fetch(url: str) -> str:
    result = subprocess.run(
        ["curl", "-sS", "-L", "-m", "40", "-A", "Mozilla/5.0", url],
        check=True,
        capture_output=True,
    )
    raw = result.stdout
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _paragraphs(html: str) -> list[str]:
    html = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S | re.I)
    paragraphs = [
        re.sub(r"<[^>]+>", "", item).replace("&nbsp;", " ").replace("　", " ").strip()
        for item in re.findall(r"<p[^>]*>(.*?)</p>", html, flags=re.S | re.I)
    ]
    kept: list[str] = []
    for text in paragraphs:
        text = re.sub(r"\s+", " ", text)
        if len(text) < 20:
            continue
        if any(noise in text for noise in _NOISE):
            continue
        if "。" not in text and not text.startswith("第"):
            continue
        kept.append(text)
    return kept


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    failures = 0
    for title, url, name in SOURCES:
        try:
            html = _fetch(url)
        except subprocess.CalledProcessError as exc:
            print(f"FAIL {title}: curl exit {exc.returncode}")
            failures += 1
            continue
        paragraphs = _paragraphs(html)
        body = "\n\n".join(paragraphs)
        if len(body) < 800:
            print(f"THIN {title}: {len(body)} chars from {url}")
            failures += 1
            continue
        header = f"# {title}\n\n来源：{url}\n获取日期：2026-10-25\n\n"
        (TARGET / name).write_text(header + body + "\n", encoding="utf-8")
        print(f"OK   {title}: {len(body)} chars -> {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
