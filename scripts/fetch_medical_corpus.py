"""Fetch the public medical/health documents for the third workspace.

Sources are official NPC / State Council pages. The corpus is committed so the
container does not need network access; this script exists for provenance and
re-fetching.

Run: .venv/Scripts/python.exe scripts/fetch_medical_corpus.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "corpus" / "medical-ai-compliance"
DATE = "2026-10-26"

SOURCES: tuple[tuple[str, str, str], ...] = (
    ("基本医疗卫生与健康促进法", "http://www.npc.gov.cn/c2/c30834/201912/t20191231_304414.html", "基本医疗卫生与健康促进法.md"),
    ("医师法", "http://www.npc.gov.cn/c2/c30834/202108/t20210820_313104.html", "中华人民共和国医师法.md"),
    ("生物安全法", "http://www.npc.gov.cn/npc/c2/c30834/202010/t20201017_308282.html", "中华人民共和国生物安全法.md"),
    ("传染病防治法", "http://www.npc.gov.cn/c2/c30834/202504/t20250430_445085.html", "中华人民共和国传染病防治法.md"),
    ("精神卫生法", "http://www.npc.gov.cn/npc/c2/c30834/201905/t20190521_281477.html", "中华人民共和国精神卫生法.md"),
    ("疫苗管理法", "http://www.npc.gov.cn/c2/c30834/201907/t20190702_299244.html", "中华人民共和国疫苗管理法.md"),
    ("人类遗传资源管理条例", "https://www.gov.cn/zhengce/content/2019-06/10/content_5398829.htm", "人类遗传资源管理条例.md"),
    ("医疗器械监督管理条例", "https://www.gov.cn/zhengce/content/2021-03/18/content_5593739.htm", "医疗器械监督管理条例.md"),
)

_NOISE = (
    "ICP备", "网站标识码", "主办单位", "技术支持", "办公时间", "政府网",
    "打印本页", "关闭窗口", "扫一扫", "当前位置", "首页", "版权", "京公网安备",
)


def fetch(url: str) -> str:
    response = requests.get(url, timeout=40, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return response.text


def extract(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [re.sub(r"\s+", " ", line).replace(" ", " ").strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    start = 0
    for i, line in enumerate(lines):
        if re.match(r"^第[一二三四五六七八九十百]+章", line) or re.match(r"^第一条", line):
            start = i
            break
    kept: list[str] = []
    for line in lines[start:]:
        if any(noise in line for noise in _NOISE):
            continue
        if len(line) < 12 and not re.match(r"^第[一二三四五六七八九十百零〇]+条$", line):
            continue
        if not re.search(r"[一-鿿]", line):
            continue
        kept.append(line)
    merged: list[str] = []
    i = 0
    while i < len(kept):
        line = kept[i]
        if re.match(r"^第[一二三四五六七八九十百零〇]+条$", line) and i + 1 < len(kept):
            merged.append(f"{line} {kept[i + 1]}")
            i += 2
            continue
        merged.append(line)
        i += 1
    return "\n\n".join(merged)


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    failures = 0
    for title, url, name in SOURCES:
        try:
            body = extract(fetch(url))
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"FAIL {title}: {type(exc).__name__}: {exc}")
            failures += 1
            continue
        if len(body) < 800:
            print(f"THIN {title}: {len(body)} chars from {url}")
            failures += 1
            continue
        header = f"# {title}\n\n来源：{url}\n获取日期：{DATE}\n\n"
        (TARGET / name).write_text(header + body + "\n", encoding="utf-8")
        print(f"OK   {title}: {len(body)} chars -> {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
