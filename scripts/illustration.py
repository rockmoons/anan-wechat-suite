#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 配图 prompt 生成 —— anan-wechat-suite 模块 6
==================================================
**本技能不出图。** 它读一篇文章，产出**可以直接交给豆包工作出图的配图提示词**，
外加一张「哪张图放在哪个位置」的施工单。

出图这一步由豆包工作完成（本机没有、也不该有图像 API 的密钥）。
所以本模块的交付物是 **prompt 清单 + 施工单**，不是图片本身——
这一点在 SKILL.md 里也是这么写的，不会假装能自己出图。

用法：
    python scripts/illustration.py --input article.md -o 配图方案.md
    python scripts/illustration.py --input article.md --type tutorial --slots 4
    python scripts/illustration.py --input article.md --style tech-blue
    python scripts/illustration.py --list-types
"""

import argparse
import os
import re
import sys

VERSION = "1.0.0"
Q = chr(96)              # 反引号：源码里不写字面量
FENCE = Q * 3
BOM = chr(0xFEFF)


# ---------------------------------------------------------------- 视觉风格预设
STYLES = {
    "tech-blue": {
        "label": "科技蓝",
        "style": "现代扁平插画风格（flat vector illustration），几何色块，线条干净，轻微等距立体感",
        "palette": "主色 #1A6DD4 深蓝、辅助 #4DA3FF 亮蓝、点缀 #FFB020 橙、底色 #F5F8FD 浅灰蓝",
        "lighting": "柔和均匀光照，无强烈投影",
        "avoid": "照片写实人脸、复杂纹理背景、密集小字、品牌 logo、水印",
    },
    "paper-ink": {
        "label": "纸感墨色",
        "style": "极简线描插画，单色墨线 + 大面积留白，带纸张颗粒感",
        "palette": "主色 #1A1A1A 墨黑、辅助 #8A8A8A 灰、底色 #FAF8F4 米白、点缀 #C8641E 赭石",
        "lighting": "无渐变，纯平面",
        "avoid": "写实照片、高饱和撞色、3D 渲染感、水印",
    },
    "warm-life": {
        "label": "暖调生活",
        "style": "柔和手绘插画，圆润造型，轻微噪点质感，亲和力强",
        "palette": "主色 #E08A3C 暖橙、辅助 #F6D9B8 米杏、点缀 #2E7D5B 墨绿、底色 #FDF6EF 奶油白",
        "lighting": "暖色侧光，柔和阴影",
        "avoid": "冷色调、赛博朋克、恐怖元素、水印",
    },
    "data-clean": {
        "label": "数据清爽",
        "style": "信息图（infographic）风格，图表化元素，网格排版，理性克制",
        "palette": "主色 #2E7D5B 墨绿、辅助 #7FC8A9 浅绿、中性 #E6E9EF、点缀 #C8641E",
        "lighting": "无光影，纯平面",
        "avoid": "无关装饰、卡通人物、水印、难以辨认的假文字",
    },
}

# 文章类型 -> 配图建议（本技能自己整理的对照表）
TYPES = {
    "tutorial": {
        "label": "工具教程 / 操作教学",
        "hint": ["界面示意与步骤图", "流程箭头图", "前后对比图", "编号步骤卡片"],
        "cover_motif": ["一只手在操作界面的俯视构图", "由 4 个编号方块串成的流程图", "半开的工具箱与发光屏幕"],
    },
    "opinion": {
        "label": "行业观点 / 深度分析",
        "hint": ["抽象隐喻图", "结构关系图", "趋势折线示意", "对比双栏图"],
        "cover_motif": ["一个站在分岔路口的抽象人形剪影", "天平两端放着不同的方块", "从云层中穿出的光束照亮一条路"],
    },
    "story": {
        "label": "个人故事 / 经验分享",
        "hint": ["场景插画", "时间线", "情绪化小物特写", "书桌一角"],
        "cover_motif": ["夜里的书桌与一盏台灯", "摊开的笔记本与一杯咖啡", "窗边的椅子沐浴在午后阳光里"],
    },
    "product": {
        "label": "产品测评 / 对比推荐",
        "hint": ["实拍感对比图", "评分卡片", "参数对照表", "使用场景照"],
        "cover_motif": ["两个产品并排放在桌面上", "打开的设备特写与手部动作", "产品悬浮在柔和渐变背景前"],
    },
    "news": {
        "label": "资讯速递 / 热点解读",
        "hint": ["新闻感构图", "关键数字大字", "时间轴", "地图标记"],
        "cover_motif": ["抽象的城市天际线与上升的折线", "报纸版式与放大镜", "闪烁的通知图标群"],
    },
    "data": {
        "label": "数据复盘 / 报告",
        "hint": ["柱状/折线示意", "漏斗或金字塔", "占比环形图", "增长阶梯"],
        "cover_motif": ["一组高低错落的柱状体", "阶梯上放置的不同大小的球体", "由数据点连成的上升曲线"],
    },
}

TYPE_KEYWORDS = {
    "tutorial": ["教程", "步骤", "怎么做", "如何", "实操", "上手", "指南", "保姆级", "操作"],
    "opinion": ["观点", "洞察", "思考", "为什么", "本质", "趋势", "判断", "复盘"],
    "story": ["故事", "经历", "踩坑", "记录", "这一年"],
    "product": ["测评", "对比", "推荐", "横评", "值不值", "开箱"],
    "news": ["发布", "上线", "宣布", "最新", "突发", "快讯"],
    "data": ["数据", "报告", "统计", "增长", "转化率", "万元"],
}


def log(m):
    print(m, flush=True)


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("utf-8-sig", errors="replace").replace(BOM, "")


def guess_type(title, body):
    """按关键词命中数猜文章类型，命中最多的胜出；都不中默认 opinion。"""
    text = title + "\n" + body
    best, best_hits = "opinion", 0
    for t, kws in TYPE_KEYWORDS.items():
        hits = sum(text.count(k) for k in kws)
        if hits > best_hits:
            best, best_hits = t, hits
    return best, best_hits


def extract_outline(md):
    """取标题与各级小标题，外加正文里的图片占位。"""
    title = ""
    sections = []
    images = []
    for line in md.split("\n"):
        s = line.strip()
        if s.startswith("# ") and not title:
            title = s[2:].strip()
        elif s.startswith("## "):
            sections.append(s[3:].strip())
        elif s.startswith("### "):
            sections.append("  " + s[4:].strip())
        m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", s)
        if m:
            images.append((m.group(1), m.group(2)))
    return title, sections, images


def build_prompt(subject, style, ratio, extra=""):
    st = STYLES[style]
    parts = [
        "【画面主体】%s" % subject,
        "【风格】%s" % st["style"],
        "【配色】%s" % st["palette"],
        "【光照】%s" % st["lighting"],
        "【画幅】%s" % ratio,
        "【质量】高清，主体清晰，构图留白适中，适合作为公众号配图",
        "【避免】%s" % st["avoid"],
    ]
    if extra:
        parts.append("【补充】%s" % extra)
    return "；".join(parts) + "。"


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="生成 AI 配图提示词与施工单（本技能不出图，出图由豆包工作完成）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python scripts/illustration.py --input article.md -o 配图方案.md\n"
               "  python scripts/illustration.py --input article.md --type tutorial --slots 4\n"
               "  python scripts/illustration.py --list-types\n")
    ap.add_argument("--input", help="文章 Markdown")
    ap.add_argument("--type", dest="atype", help="文章类型（默认自动判断，见 --list-types）")
    ap.add_argument("--style", default="tech-blue", help="视觉风格预设（默认 tech-blue）")
    ap.add_argument("--slots", type=int, default=4, help="正文配图张数（默认 4，不含封面）")
    ap.add_argument("--topic", default="", help="文章主题（默认取标题）")
    ap.add_argument("--list-types", action="store_true", help="列出文章类型与视觉风格")
    ap.add_argument("-o", "--output", help="输出文件（默认打印到屏幕）")
    args = ap.parse_args(argv)

    if args.list_types:
        log("文章类型（--type）：")
        for k, v in TYPES.items():
            log("  %-10s %s" % (k, v["label"]))
            log("      配图建议：%s" % "、".join(v["hint"]))
        log("")
        log("视觉风格预设（--style）：")
        for k, v in STYLES.items():
            log("  %-12s %s" % (k, v["label"]))
            log("      %s" % v["style"])
            log("      配色：%s" % v["palette"])
        return 0

    if not args.input:
        ap.print_help()
        print("\n错误：请用 --input 指定文章 Markdown。")
        return 2
    if not os.path.isfile(args.input):
        print("错误：找不到文件：%s" % args.input)
        return 1
    if args.style not in STYLES:
        print("错误：没有这个风格预设：%s（可用：%s）" % (args.style, "、".join(STYLES)))
        return 2

    md = read_text(args.input)
    title, sections, images = extract_outline(md)
    topic = args.topic or title or "（未取到标题，请手动指定 --topic）"

    if args.atype:
        if args.atype not in TYPES:
            print("错误：没有这个文章类型：%s（可用：%s）" % (args.atype, "、".join(TYPES)))
            return 2
        atype = args.atype
        type_note = "手动指定"
    else:
        atype, hits = guess_type(title, md)
        type_note = "自动判断（关键词命中 %d 次）" % hits

    spec = TYPES[atype]
    st = STYLES[args.style]
    top_sections = [s for s in sections if not s.startswith("  ")] or ["正文"]
    motif = spec["cover_motif"][0]

    cover_prompt = build_prompt(
        "%s。主题是「%s」" % (motif, topic), args.style,
        "2.35:1 横版，主体居中（同一构图需能裁成 1:1 而不丢主体）",
        extra="画面中不要出现任何文字，标题由公众号后台另配")

    def sec_prompt(i):
        sec = top_sections[(i - 1) % len(top_sections)]
        m = spec["cover_motif"][(i - 1) % len(spec["cover_motif"])]
        hint = spec["hint"][(i - 1) % len(spec["hint"])]
        return sec, build_prompt(
            "%s；表现形式：%s；内容指向「%s」" % (m, hint, sec), args.style,
            "16:9 横版", extra="画面中不要出现任何文字")

    out = []
    add = out.append
    add("# 配图方案 · %s" % topic)
    add("")
    add("> 由 anan-wechat-suite 生成　|　文章类型：%s（%s）　|　视觉风格：%s（%s）"
        % (spec["label"], type_note, st["label"], args.style))
    add("")
    add("**本技能产出配图提示词，不产出图片。** 出图这一步在豆包工作里完成："
        "把下面每一条提示词逐条发给豆包工作，保存图片，再按下文「怎么把图接回文章」贴回去。")
    add("")
    add("---")
    add("")
    add("## 一、封面图（1 张，必做）")
    add("")
    add("公众号封面会被裁成两种比例，**主体务必居中**，两侧留出安全边距：")
    add("")
    add("- 大图（首图）：2.35 : 1")
    add("- 小图（分享卡片）：1 : 1")
    add("")
    add("**封面提示词**")
    add("")
    add("> " + cover_prompt)
    add("")
    add("备选主视觉（想要不同感觉就换这一句）：")
    for m in spec["cover_motif"][1:]:
        add("- " + m)
    add("")
    add("## 二、正文配图（%d 张）" % args.slots)
    add("")
    add("每张图对应一个小标题的位置。**图要放在小标题下面第一段之后**，不要放在标题正上方。")
    add("")
    add("| # | 放在哪一节 | 建议文件名 | 画幅 | 提示词 |")
    add("| --- | --- | --- | --- | --- |")
    for i in range(1, args.slots + 1):
        sec, p = sec_prompt(i)
        add("| %d | %s | images/sec%02d.png | 16:9 | %s |" % (i, sec, i, p))
    add("")
    add("## 三、逐条提示词（直接复制给豆包工作）")
    add("")
    add("### 封面")
    add("")
    add(FENCE + "text")
    add(cover_prompt)
    add(FENCE)
    add("")
    for i in range(1, args.slots + 1):
        sec, p = sec_prompt(i)
        add("### 正文图 %d（%s）" % (i, sec))
        add("")
        add(FENCE + "text")
        add(p)
        add(FENCE)
        add("")

    add("## 四、怎么把图接回文章")
    add("")
    add("1. **出图**：在豆包工作里逐条粘贴上面的提示词，保存图片。")
    add("   建议存成上面表格里的文件名（images/cover.png、images/sec01.png …），")
    add("   把文件放进文章目录的 images/ 里，和抓下来的原图放在一起。")
    add("")
    add("2. **写进 Markdown**：在对应小标题下面加一行：")
    add("")
    add("   " + FENCE + "markdown")
    add("   ![" + "这一节讲什么" + "](images/sec01.png)")
    add("   " + FENCE)
    add("")
    if images:
        add("   这篇文章目前已有 %d 处图片占位，形如 ![%s](%s)；"
            % (len(images), images[0][0], images[0][1]))
        add("   要换成新图就直接改括号里的路径，不用动别的地方。")
        add("")
    add("3. **排版**：跑排版脚本，图片会带着内联样式一起出现在 HTML 里：")
    add("")
    add("   " + FENCE + "bash")
    add("   python scripts/typeset.py article.md -o article.html")
    add("   " + FENCE)
    add("")
    add("4. **贴进公众号**（关键，很多人卡在这一步）：")
    add("   - **本地图片路径直接粘进公众号编辑器是无效的**——公众号只认它自己服务器上的图。")
    add("   - 正确做法：用浏览器打开排版脚本 --full-html 生成的那份 HTML，"
        "在页面上全选复制，再粘进公众号编辑器。")
    add("     浏览器复制时会带上图片数据，公众号会**自动把图上传**到自己的素材库。")
    add("   - 粘完检查一遍图有没有漏，特别是 GIF 和透明 PNG。")
    add("   - 若走服务号 API 发布，封面图需要单独上传成永久素材，见发布模块。")
    add("")
    add("## 五、一致性要求（多张图别长得像两套）")
    add("")
    add("- 所有图用**同一个风格预设**，不要一张扁平一张写实。")
    add("- 主色只用 %s，点缀色最多出现两处。" % st["palette"].split("、")[0])
    add("- 不要在图里放文字：中文小字在手机缩略图上必然糊，标题交给公众号后台。")
    add("- 一次多出几张，挑构图差异最大的用，避免每张都是「居中一个物体」。")
    add("")
    add("---")
    add("")
    add("作者：阿南 rockmoons（微信：rockmoons）")
    add("")

    text = "\n".join(out)
    if args.output:
        parent = os.path.dirname(os.path.abspath(args.output))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.output, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        log("配图方案已生成：%s" % os.path.abspath(args.output))
        log("  文章类型：%s（%s）" % (spec["label"], type_note))
        log("  视觉风格：%s（%s）" % (st["label"], args.style))
        log("  提示词数量：%d 张（1 封面 + %d 正文）" % (1 + args.slots, args.slots))
        log("  小标题参考：%s" % "、".join(top_sections[:5]))
        log("")
        log("提醒：本技能不产出图片本身，请把提示词交给豆包工作出图。")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
