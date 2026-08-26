#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牛客网「面经」合集 同步脚本
============================

功能：抓取牛客网指定专栏里的全部面经文章（标题 + uuid），按「公司 -> 岗位」归类，
     各岗位内按面经编号从小到大排序，重新生成 README.md 的链接索引。

用法：
    python3 sync_mianjing.py                 # 只检测并打印，不修改文件（dry-run，默认）
    python3 sync_mianjing.py --apply         # 检测 + 重写 README.md
    python3 sync_mianjing.py --columns 04ypb2,XXXX   # 指定要监控的专栏

依赖：仅 Python3 标准库，无第三方依赖。
参考文档：见同目录《同步指南.md》。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request

# ======================================================================
# 配置区
# ======================================================================

BASE_URL = "https://www.nowcoder.com"

# 默认监控的专栏 ID（牛客网「专栏」，见 columnDetail URL 最后一段）。
# 04ypb2 = 字节跳动面经合集；后续有新公司专栏时在这里追加。
DEFAULT_COLUMNS = ["04ypb2"]

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
README_PATH = os.path.join(PROJECT_DIR, "README.md")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# ----------------------------------------------------------------------
# 公司分类关键词表。顺序：越靠前越优先匹配；对标题做忽略大小写子串匹配。
# 后续新增公司（如阿里、腾讯面经）时在这里追加一条即可。
# ----------------------------------------------------------------------
COMPANY_KEYWORDS = [
    ("字节跳动", ["字节"]),
    # ("阿里", ["阿里", "淘天", "饿了么", "盒马", "菜鸟", "高德", "钉钉", "大文娱"]),
    # ("腾讯", ["腾讯"]),
]

# ----------------------------------------------------------------------
# 岗位分类关键词表。顺序：越靠前越优先匹配（注意「大模型算法岗」要先于「算法岗」，
# 否则会匹配错）。关键词对标题做忽略大小写子串匹配。
# ----------------------------------------------------------------------
POSITION_KEYWORDS = [
    ("测试开发岗", ["测试开发"]),
    ("大模型算法岗", ["大模型算法"]),
    ("算法岗", ["算法岗"]),
    ("产品经理岗", ["产品经理"]),
]

# 无法归到上面任何岗位时的兜底标签
POSITION_OTHER = "其他"


# ======================================================================
# 网络请求
# ======================================================================

def fetch_json(url, retries=3):
    """GET 一个 URL 并解析为 JSON，带简单重试。"""
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"请求失败 {url}: {last_err}")


def get_catalog(column_id):
    """获取某专栏的文章目录：返回 [{title, uuid}, ...]（保持原文顺序）。"""
    url = f"{BASE_URL}/content/zhuanlan/index/catalog/{column_id}"
    data = fetch_json(url)
    if data.get("code") != 0:
        raise RuntimeError(f"目录接口返回异常: {data}")
    catalog = data.get("data", {}).get("catalog") or []
    return [{"title": c["title"], "uuid": c["uuid"]} for c in catalog]


# ======================================================================
# 分类 / 排序
# ======================================================================

def match_first(title, table):
    """在关键词表里找第一个命中标题的类别名；找不到返回 None。"""
    t = title.lower()
    for name, keywords in table:
        for kw in keywords:
            if kw.lower() in t:
                return name
    return None


def classify(title):
    """返回 (company, position)。company / position 未知时为 None。"""
    company = match_first(title, COMPANY_KEYWORDS)
    position = match_first(title, POSITION_KEYWORDS) or POSITION_OTHER
    return company, position


def parse_seq(title):
    """从标题里解析面经编号（如「面经-07」-> 7），用于岗位内排序；解析不到返回 9999。"""
    m = re.search(r"[面经篇][-—_]?\s*(\d{1,3})", title)
    if m:
        return int(m.group(1))
    return 9999


def make_link(column_id, title, uuid):
    """生成一行 markdown：`[标题](链接)`。"""
    return (
        f"[{title.strip()}]"
        f"({BASE_URL}/issue/tutorial?zhuanlanId={column_id}&uuid={uuid})"
    )


def sort_key(item):
    """排序键：公司顺序 -> 岗位顺序 -> 面经编号。"""
    company, position, seq, _title, _uuid, _cid = item
    ci = next((i for i, (c, _) in enumerate(COMPANY_KEYWORDS) if c == company), 999)
    pi = next((i for i, (p, _) in enumerate(POSITION_KEYWORDS) if p == position), 999)
    return (ci, pi, seq)


# ======================================================================
# 生成 README
# ======================================================================

README_HEADER = """# 牛客网面经合集

> 按「公司 / 岗位」收录牛客网专栏中的面经（面试经验问答），当前覆盖：字节跳动（测试开发岗、大模型算法岗、算法岗、产品经理岗）。各岗位内按面经编号从小到大排列，标题与原文链接见对应小节，持续更新中。
>
> 数据来源：牛客网「专栏」（作者：林小白zii）。本文档仅收录文章标题与原文链接，全部文章版权归原作者与平台所有。
"""


def build_readme(articles):
    """根据 articles 重新生成 README.md 全文。articles: [(company, position, seq, title, uuid, column_id)]"""
    companies = [c for c, _ in COMPANY_KEYWORDS]

    lines = [README_HEADER.rstrip(), ""]
    for company in companies:
        company_articles = [a for a in articles if a[0] == company]
        if not company_articles:
            continue
        lines.append(f"### {company}")
        lines.append("")
        # 按岗位分组（保持 POSITION_KEYWORDS 顺序）
        positions = [p for p, _ in POSITION_KEYWORDS] + [POSITION_OTHER]
        for position in positions:
            pos_articles = [a for a in company_articles if a[1] == position]
            if not pos_articles:
                continue
            lines.append(f"#### {position}")
            lines.append("")
            for _c, _p, _s, title, uuid, cid in sorted(pos_articles, key=sort_key):
                lines.append(make_link(cid, title, uuid))
                lines.append("")
        lines.append("")

    # 未归类到任何公司的文章（提示人工补充关键词）
    unknown = [a for a in articles if a[0] is None]
    if unknown:
        lines.append("### 未归类")
        lines.append("")
        for _c, _p, _s, title, uuid, cid in unknown:
            lines.append(make_link(cid, title, uuid))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ======================================================================
# 主流程
# ======================================================================

def main():
    parser = argparse.ArgumentParser(description="牛客网面经合集同步脚本")
    parser.add_argument("--apply", action="store_true",
                        help="实际重写 README.md（默认只打印，不修改文件）")
    parser.add_argument("--columns", type=str, default=",".join(DEFAULT_COLUMNS),
                        help="逗号分隔的专栏 ID，默认 %s" % ",".join(DEFAULT_COLUMNS))
    args = parser.parse_args()

    columns = [c.strip() for c in args.columns.split(",") if c.strip()]
    mode = "写入" if args.apply else "dry-run（仅检测，不写入）"
    print("=" * 60)
    print(f"牛客网面经同步  ·  专栏 = {columns}  ·  模式 = {mode}")
    print("=" * 60)

    # 1) 抓取目录
    print("\n[1] 抓取专栏目录 ...")
    articles = []  # (company, position, seq, title, uuid, column_id)
    for cid in columns:
        try:
            cat = get_catalog(cid)
            print(f"    {cid}: {len(cat)} 篇")
            for a in cat:
                company, position = classify(a["title"])
                seq = parse_seq(a["title"])
                articles.append((company, position, seq, a["title"], a["uuid"], cid))
        except Exception as e:  # noqa: BLE001
            print(f"    ❌ 抓取 {cid} 失败：{e}")

    # 2) 汇总
    print("\n[2] 归类汇总 ...")
    from collections import Counter, defaultdict
    stats = Counter()
    for company, position, _s, _t, _u, _c in articles:
        stats[f"{company or '未归类'} / {position}"] += 1
    for k in sorted(stats):
        print(f"    {k}: {stats[k]} 篇")

    unknown = [a for a in articles if a[0] is None]
    if unknown:
        print(f"\n⚠️  {len(unknown)} 篇无法归类公司，需在 COMPANY_KEYWORDS 补充关键词：")
        for a in unknown:
            print(f"    - {a[3]}")

    # 3) 生成 README
    readme = build_readme(articles)
    print("\n[3] README 生成结果：")
    if args.apply:
        with open(README_PATH, "w", encoding="utf-8") as f:
            f.write(readme)
        print(f"    已重写 {README_PATH}（共 {len(readme.splitlines())} 行）")
    else:
        print("    （dry-run）预览前 40 行：")
        for line in readme.splitlines()[:40]:
            print("    " + line)

    print("\n完成。")


if __name__ == "__main__":
    main()
