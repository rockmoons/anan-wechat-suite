#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发布前闸门（机械判据）—— anan-wechat-suite
============================================
两条闸门，都不靠人记得住：

闸门 A · 正文里的图必须已经在微信图床
    正文里每一个 <img src=...> 都必须指向微信自己的图床域名
    （mmbiz.qpic.cn / mmbiz.qlogo.cn / wx.qlogo.cn）。
    只要还剩一个本地路径（images/01.png）或第三方外链，发出去就是碎图 —— 直接拦。
    ★ 只在「API 直发草稿箱」这条路生效：粘贴模式靠浏览器复制把图带上去，
      本地相对路径在粘贴模式里是对的，不拦。

闸门 B · 正文里不许有过程性标题
    「改了什么 / 修改说明 / 改动清单 / 改写说明 …」这类**写给用户看的改动说明**
    不是文章。AI 在同一个回答里先写正文、再写改了什么，很容易一起落进稿子；
    一落进去，排版和发布都会把它当正文带走（2026-10-06 用户实测的第二个 bug）。
    发现就拦，并把行号点出来；--fix 能把这一段挪到单独的 <名字>.改动说明.md。

为什么单独成一个模块：判据只有一份。publish.py（发布前拦）与 typeset.py（排版时提醒）
共用同一份，免得两处各写一套、改一处漏一处（这正是「分模块测过 != 串起来能跑」的教训）。

用法：
    python scripts/guard.py 初稿.md                    # 体检正文：过程性标题 + 图片地址
    python scripts/guard.py 初稿.md --fix              # 把过程性那一段挪到 初稿.改动说明.md
    python scripts/guard.py 初稿.html --html           # 只查 HTML 里的图片地址
    python scripts/guard.py --list-titles              # 看看都认哪些过程性标题

退出码：
    0 = 过关（--fix 时表示已经清理干净）
    1 = 发现问题（详情已打印）
    2 = 参数错误 / 文件读不到
"""

import argparse
import os
import re
import sys
import urllib.parse

VERSION = "1.0.0"
BOM = chr(0xFEFF)
Q = chr(96)              # 反引号：源码里不写字面量，避免被外层模板吃掉

# 微信自己的图床域名。正文图必须落在这些域名下，草稿里才不会碎。
# ★ 只写 **腾讯图床的整个区**（qpic.cn / qlogo.cn），不写具体主机名 ——
#   2026-10-06 实测：api 的 media/uploadimg 传完图返回的是
#     http://mmecoa.qpic.cn/mmecoa_png/...
#   而不是文档示例里的 mmbiz.qpic.cn。按主机名白名单写死会在这一步误拦自己刚传上去的图。
#   qpic.cn / qlogo.cn 都是腾讯的图床域（mmbiz / mmecoa / sz_mmbiz / wx.qlogo 都在其下）。
WECHAT_IMAGE_HOSTS = ("qpic.cn", "qlogo.cn")

# ★ 过程性标题的判据（写死在这里，别散落到各处）。
#   一条判据 = 归一化后的标题/行首里出现下面任一子串。
PROCESS_TITLE_TOKENS = (
    "改了什么", "改动了什么", "改动说明", "改动清单", "改动记录", "改动点", "改动一览",
    "修改说明", "修改清单", "修改记录", "修改点", "修改一览",
    "改写说明", "改写记录", "改写清单",
    "变更说明", "变更清单", "变更记录",
    "本次改动", "本次修改", "本次变更", "本轮改动", "本轮修改",
    "编辑说明", "调整说明", "调整清单",
    "changelog", "whatchanged", "whathaschanged",
)

_RE_HEADING = re.compile(r"^(#{1,6})\s*(\S.*?)\s*$")
_RE_BOLD_ONLY = re.compile(r"^\s*(?:\*\*|__)\s*(\S.*?)\s*(?:\*\*|__)\s*$")
_RE_IMG = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>\"']+))", re.I)
_STRIP = re.compile(r"[\s\u3000#*_（）()【】\[\]「」:：、,，。.·\-—|/" + Q + r"]")


def normalize_title(text):
    """把标题/行首归一化，再拿去做子串匹配（让「## 改了什么（三行）」也能命中）。"""
    return _STRIP.sub("", (text or "")).lower()


def is_process_title(text):
    """这句是不是过程性标题。"""
    norm = normalize_title(text)
    if not norm:
        return False
    return any(tok in norm for tok in PROCESS_TITLE_TOKENS)


# ---------------------------------------------------------------- 闸门 B：过程性标题
def find_process_sections(text):
    """找正文里的过程性标题。

    @param text Markdown 全文
    @returns [(行号, 级别, 原文)]；行号从 1 起。级别是 'h1'..'h6' / 'bold' / 'line'
    """
    found = []
    for i, line in enumerate(text.split("\n"), 1):
        s = line.strip()
        if not s:
            continue
        m = _RE_HEADING.match(s)
        if m:
            if is_process_title(m.group(2)):
                found.append((i, "h%d" % len(m.group(1)), s))
            continue
        m = _RE_BOLD_ONLY.match(s)
        if m:
            if is_process_title(m.group(1)):
                found.append((i, "bold", s))
            continue
        # 行首裸写：「改了什么：把三段并成两段」——不是标题，但同样不是正文
        head = s.split("：", 1)[0].split(":", 1)[0]
        if head != s and is_process_title(head):
            found.append((i, "line", s))
    return found


def section_span(text, line_no, level):
    """算出某个过程性段落占的行区间 [起, 止)，起是 0 基。

    标题：到下一个同级或更高级标题之前；行首/加粗：到下一个任何级别的标题之前。
    """
    lines = text.split("\n")
    start = line_no - 1
    want = int(level[1:]) if level.startswith("h") else None
    for j in range(start + 1, len(lines)):
        m = _RE_HEADING.match(lines[j].strip())
        if not m:
            continue
        if want is None or len(m.group(1)) <= want:
            return start, j
    return start, len(lines)


def strip_process_sections(text):
    """把过程性段落从正文里摘掉。

    @returns (清理后的正文, [(被摘掉的正文, (起始行, 结束行))])
    """
    found = find_process_sections(text)
    if not found:
        return text, []
    lines = text.split("\n")
    spans = sorted(section_span(text, ln, lv) for ln, lv, _ in found)
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    removed = [("\n".join(lines[a:b]).strip("\n"), (a + 1, b)) for a, b in merged]
    keep, idx = [], 0
    for a, b in merged:
        keep.extend(lines[idx:a])
        idx = b
    keep.extend(lines[idx:])
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip("\n") + "\n"
    return out, removed


# ---------------------------------------------------------------- 闸门 A：图片地址
def iter_img_srcs(html):
    """按出现顺序取出 <img src=...>，返回 [(序号(1起), src)]。"""
    out = []
    for m in _RE_IMG.finditer(html or ""):
        out.append((len(out) + 1, m.group(1) or m.group(2) or m.group(3) or ""))
    return out


def is_wechat_image_url(url):
    """这个地址是不是微信图床上的图（草稿里能直接显示）。"""
    u = (url or "").strip()
    if not u or u.startswith("data:"):
        return False
    if u.startswith("//"):
        u = "https:" + u
    try:
        host = (urllib.parse.urlsplit(u).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in WECHAT_IMAGE_HOSTS)


def classify_img_src(src):
    """给一个 img src 归类，用于打印人话。"""
    s = (src or "").strip()
    if not s:
        return "空 src"
    if s.startswith("data:"):
        return "内嵌 data: 图（微信取不到，必须转存）"
    if is_wechat_image_url(s):
        return "微信图床"
    low = s.lower()
    if low.startswith("http://") or low.startswith("https://") or low.startswith("//"):
        try:
            host = urllib.parse.urlsplit(("https:" + s) if s.startswith("//") else s).hostname or "?"
        except ValueError:
            host = "?"
        return "第三方外链（%s）" % host
    return "本地路径"


def check_images(html):
    """闸门 A：正文里还有没有非微信图床的图。

    @param html 正文 HTML
    @returns (问题列表, 统计字典)
    """
    items = iter_img_srcs(html)
    problems = []
    stat = {"total": len(items), "wechat": 0, "bad": 0}
    for idx, src in items:
        if is_wechat_image_url(src):
            stat["wechat"] += 1
            continue
        stat["bad"] += 1
        problems.append("第 %d 张图还没进微信图床：%s（%s）"
                        % (idx, (src[:90] + "…") if len(src) > 90 else src, classify_img_src(src)))
    return problems, stat


def check_markdown(text):
    """闸门 B：正文里有没有过程性标题。返回问题列表。"""
    return ["第 %d 行是过程性标题（%s）：%s" % (n, lv, raw[:90])
            for n, lv, raw in find_process_sections(text)]


# ---------------------------------------------------------------- 对外一步到位
def check_article(md_path, html=None):
    """读 Markdown（可选再给一份 HTML），跑两条闸门。

    @returns (问题列表, 统计字典)；问题为空即过关
    """
    with open(md_path, "rb") as f:
        text = f.read().decode("utf-8-sig", errors="replace").replace(BOM, "")
    problems = check_markdown(text)
    stat = {"process_headings": len(problems)}
    if html is not None:
        img_problems, img_stat = check_images(html)
        problems += img_problems
        stat.update(img_stat)
    return problems, stat


def fix_markdown(md_path, note_path=None):
    """把过程性段落从正文里摘出来，挪到 <名字>.改动说明.md。

    @param md_path 要清理的 Markdown
    @param note_path 改动说明落到哪儿；默认与 md 同目录同名的 .改动说明.md
    @returns (是否改动, 说明文件路径, 摘掉的行数)
    """
    with open(md_path, "rb") as f:
        text = f.read().decode("utf-8-sig", errors="replace").replace(BOM, "")
    cleaned, removed = strip_process_sections(text)
    if not removed:
        return False, None, 0
    if note_path is None:
        note_path = os.path.splitext(md_path)[0] + ".改动说明.md"
    note = ("# 改动说明（从正文里摘出来的，不是文章的一部分）\n\n"
            "> 这些内容原来混在 %s 里，排版 / 发布都会把它当正文带走。\n"
            "> 现在单独放在这里，正文保持干净。\n\n%s\n"
            % (os.path.basename(md_path),
               "\n\n---\n\n".join(b for b, _ in removed)))
    with open(note_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(note)
    with open(md_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(cleaned)
    return True, note_path, sum(b - a for _, (a, b) in removed)


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="发布前闸门：正文图是否都在微信图床 / 正文里有没有过程性标题",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0=过关  1=有问题  2=参数错误")
    ap.add_argument("article", nargs="?", help="要体检的 Markdown 或 HTML")
    ap.add_argument("--html", action="store_true", help="把参数当 HTML 读，只查图片地址")
    ap.add_argument("--fix", action="store_true",
                    help="把过程性段落挪到 <名字>.改动说明.md（改文件，会打印改了什么）")
    ap.add_argument("--note", help="配合 --fix：改动说明写到这个路径")
    ap.add_argument("--list-titles", action="store_true", help="列出认哪些过程性标题")
    args = ap.parse_args(argv)

    if args.list_titles:
        print("过程性标题判据（归一化后包含任一子串即命中）：")
        for t in PROCESS_TITLE_TOKENS:
            print("  - %s" % t)
        return 0
    if not args.article:
        ap.print_help()
        return 2
    if not os.path.isfile(args.article):
        print("错误：找不到文件：%s" % args.article)
        return 2

    with open(args.article, "rb") as f:
        raw = f.read().decode("utf-8-sig", errors="replace").replace(BOM, "")

    if args.html:
        problems = check_images(raw)[0]
    else:
        problems = check_markdown(raw) + check_images(raw)[0]

    if not problems:
        print("过关：%s" % args.article)
        print("  · 没有过程性标题")
        print("  · 正文里没有非微信图床的图（如果有图的话）")
        return 0

    print("拦下：%s" % args.article)
    for p in problems:
        print("  ✗ %s" % p)
    if args.fix and not args.html:
        changed, note, n = fix_markdown(args.article, args.note)
        if changed:
            print("")
            print("已清理：把 %d 行过程性说明挪到 %s" % (n, note))
            left = check_markdown(open(args.article, "rb").read()
                                  .decode("utf-8-sig", errors="replace"))
            if left:
                print("但仍剩 %d 处，需要人工看一眼：" % len(left))
                for p in left:
                    print("  ✗ %s" % p)
                return 1
            print("正文已干净：%s" % args.article)
            return 0
    if not args.html:
        print("")
        print("怎么修：")
        print("  1) 正文里只留文章本身；「改了什么 / 修改说明」这类过程性内容另存一个文件")
        print("  2) 或直接跑：python scripts/guard.py \"%s\" --fix" % args.article)
    return 1


if __name__ == "__main__":
    sys.exit(main())
