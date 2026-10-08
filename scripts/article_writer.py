#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
写初稿（四种口吻） / 改写（三种模式）—— anan-wechat-suite 模块 4 & 5
======================================================================
本模块**不联网、不调模型**：它做两件事 —— ① 把「风格规则 + 你的素材」渲染成一份
完整的写作提示词；② 用同一套规则反过来验收初稿。

★ **默认路径是「AI 直接写」**：豆包工作（或任何会写字的 AI）读完这份规则**自己写初稿**，
写完用 --check 验收。那份提示词是**副产品** —— 可以留档、可以拿去别的工具用，
但**不该再绕「先出提示词、再交给别人写」这一圈**：豆包工作本身就是 AI，
技能的价值是那套风格规则，不是那份提示词。

★ **交付契约（模块接缝，踩过坑才写下的）**：写初稿 / 改写产出的是**正文文件**，
里面**只有文章本身**。「改了什么 / 为什么这么改」这类过程说明必须**另存一个文件**。
    为什么：2026-10-06 用户实测 —— 改写提示词原来写的是「先输出正文，然后在最后用三行写清
    改了什么」，AI 照做，正文和说明落进同一个文件；排版、发布都把它当正文，最后**发到了草稿箱**里。
    本文件的 --check / --check-rewrite 现在会把「正文里出现过程性标题」直接判为不合格
    （判据在 guard.py，与发布前的闸门是同一份）。

为什么要有脚本：规则写在 references/styles.json 里（唯一权威），
这个脚本只是规则的渲染器与校验器 —— 规则改了，提示词和检查项一起变，
不会出现「文档写了一套、实际跑的是另一套」。

用法：
    python scripts/article_writer.py --list
    python scripts/article_writer.py --style business-insight --topic "AI 编程工具" -o prompt.md
    python scripts/article_writer.py --style personal-voice --topic "..." --material 素材.md
    python scripts/article_writer.py --rewrite restructure --input draft.md -o prompt.md
    python scripts/article_writer.py --check draft.md --style personal-voice

退出码：
    0 = 正常（--check 时表示没有发现违规）
    1 = --check 发现违规 / 文件读取失败
    2 = 参数错误
"""

import argparse
import json
import os
import re
import sys

VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STYLES_PATH = os.path.join(ROOT, "references", "styles.json")

CJK = "[一-鿿]"          # 汉字区间 U+4E00-U+9FFF，写字面量避免转义层级问题
BOM = chr(0xFEFF)
Q = chr(96)              # 反引号：源码里不写字面量，避免被外层模板吃掉
FENCE = Q * 3


def log(msg):
    print(msg, flush=True)


def load_styles(path=STYLES_PATH):
    if not os.path.isfile(path):
        raise RuntimeError(
            "找不到风格配置文件：%s\n"
            "它是本模块的规则来源，缺了它脚本没法工作。请确认技能包完整解压。" % path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "styles" not in data or "rewrite_modes" not in data:
        raise RuntimeError("风格配置文件结构不对（缺 styles 或 rewrite_modes）：%s" % path)
    return data


def count_words(text):
    """中文字数统计：汉字按字算，英文按词算，数字串按一个词算。"""
    han = len(re.findall(CJK, text))
    latin = len(re.findall(r"[A-Za-z]+", text))
    nums = len(re.findall(r"\d+(?:\.\d+)?", text))
    return han + latin + nums


def read_text(path):
    """读文本：显式剥 BOM（Windows 记事本写出的文件必带）。"""
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("utf-8-sig", errors="replace").replace(BOM, "")


# ---------------------------------------------------------------- 提示词组装
def bullet(items, indent="  "):
    return "\n".join(indent + "- " + x for x in items)


def build_draft_prompt(cfg, style_key, topic, material="", words=""):
    st = cfg["styles"][style_key]
    word_rule = ""
    if words:
        word_rule = "\n7. 本次篇幅按 **%s 字** 来写（与上面的通用字数要求冲突时，以本条为准）。" % words
    if material.strip():
        mat = ("\n\n【我提供的素材】\n"
               "下面是我自己整理、或已经获得授权的材料。请**只用这里的材料**，"
               "不要自己去编造案例、数字或引语；材料不够支撑某个论点时，"
               "就把那个论点删掉，而不是补一个看起来合理的例子。\n\n"
               "------ 素材开始 ------\n%s\n------ 素材结束 ------\n" % material.strip())
    else:
        mat = ("\n\n【素材说明】\n我没有额外提供素材。请只使用公开、可核实的信息；"
               "凡是你不确定的数字或案例，**明确标注「此处需要我补充真实数据」**，不要编。\n")

    return (
        "你是一位中文公众号作者，正在用「{name}」这种口吻写一篇初稿。\n"
        "\n"
        "## 这种口吻是什么\n"
        "{one_liner}\n"
        "\n"
        "特征：\n{traits}\n"
        "\n"
        "## 选题\n"
        "{topic}\n"
        "\n"
        "## 结构骨架（严格按这个顺序写）\n"
        "{structure}\n"
        "\n"
        "## 硬规则（每一条都要能对照检查）\n"
        "{rules}\n"
        "\n"
        "## 禁用表达清单（写完必须自查：出现任何一项就算不合格）\n"
        "{banned}\n"
        "\n"
        "## 交付格式\n"
        "输出 Markdown：第一行是 {q}# 标题{q}，之后是正文；小标题用 {q}##{q}；"
        "不要输出任何解释性的话，也不要在正文外写「以上」「希望对你有帮助」。\n"
        "{mat}"
        "\n\n## 最后再说一遍\n"
        "宁可写得朴素但每句话都站得住，也不要写得漂亮但掺了假。"
        "凡是你不能确认的事实、数字、引语，一律标注出来交给我补，不要替我编。\n"
    ).format(
        name=st["name"],
        one_liner=st["one_liner"],
        traits=bullet(st["voice_traits"]),
        topic=topic.strip() or "（未填写，请先告诉我写什么）",
        structure=bullet(["第 %d 步：%s" % (i, s) for i, s in enumerate(st["structure"], 1)]),
        rules=bullet(["规则 %d：%s" % (i, r) for i, r in enumerate(st["hard_rules"], 1)]) + word_rule,
        banned=bullet(st["banned"]),
        mat=mat,
        q=Q,
    )


def build_rewrite_prompt(cfg, mode_key, source):
    md = cfg["rewrite_modes"][mode_key]
    return (
        "你是一位中文编辑，正在做**{name}**。\n"
        "\n"
        "## 这个模式做什么\n"
        "{one_liner}\n"
        "\n"
        "## 具体动作\n"
        "{actions}\n"
        "\n"
        "## 硬规则（违反任何一条就算失败）\n"
        "{rules}\n"
        "\n"
        "## 合规红线\n"
        "{comp}\n"
        "\n"
        "## 待处理的原文\n"
        "{fence}markdown\n"
        "{src}\n"
        "{fence}\n"
        "\n"
        "## 交付格式（★ 两个文件，不要合成一个）\n"
        "① **正文文件**（例：{q}改写稿.md{q}）：**只有文章本身** —— 第一行 {q}# 标题{q}，然后是正文。\n"
        "   正文里**一个字的过程说明都不许有**：不写「改了什么 / 修改说明 / 改动清单」这类小节，\n"
        "   也不写「以上」「希望对你有帮助」。写完正文就结束。\n"
        "② **改动说明文件**（例：{q}改动说明.md{q}）：**另存一个文件**，写清改了什么、\n"
        "   为什么这么改、有没有动到事实。\n"
        "   ★ 它**不是文章的一部分**：正文文件里绝不能带上它 ——\n"
        "     排版和发布会把正文文件里的每一行都当文章发出去，改动说明会一起发到公众号上。\n"
        "不要输出其他解释。\n"
    ).format(
        name=md["name"],
        one_liner=md["one_liner"],
        actions=bullet(md["actions"]),
        rules=bullet(md["hard_rules"]),
        comp=bullet(cfg["compliance"]),
        src=source.strip(),
        fence=FENCE,
        q=Q,
    )


# ---------------------------------------------------------------- 校验
def process_heading_problems(text):
    """正文里混进过程性标题就判不合格。

    判据在 guard.py（与发布前闸门**同一份**，不另写一套）：
    「改了什么 / 修改说明 / 改动清单 / 改写说明」这类是写给用户看的说明，不是文章。
    2026-10-06 的实测 bug 就是它跟着正文一路进了草稿箱 —— 所以在这里先拦一道。
    """
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import guard
    return ["正文里有过程性标题（第 %d 行，%s）：%s —— 这类内容是给你看的改动说明，"
            "不属于文章；请把它挪到单独的文件（例：改动说明.md），正文里一个字都不要留"
            % (n, lv, raw[:60]) for n, lv, raw in guard.find_process_sections(text)]


def check_draft(cfg, style_key, text):
    """拿同一套风格规则反过来验稿。返回 (问题列表, 提示列表)。"""
    st = cfg["styles"][style_key]
    problems, notes = [], []
    body = text
    problems += process_heading_problems(body)
    words = count_words(body)
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]

    # 禁用表达
    for bad in st["banned"]:
        for token in re.split(r"[／/（）()]", bad):
            token = token.strip()
            if len(token) < 2:
                continue
            if token in body:
                problems.append("命中禁用表达：「%s」" % token)

    # 字数
    m = re.search(r"(\d+)\s*-\s*(\d+)\s*字", " ".join(st["hard_rules"]))
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if words < lo * 0.85:
            problems.append("字数偏少：约 %d 字，规则要求 %d-%d 字" % (words, lo, hi))
        elif words > hi * 1.25:
            problems.append("字数偏多：约 %d 字，规则要求 %d-%d 字" % (words, lo, hi))
        else:
            notes.append("字数 %d，落在要求区间内" % words)

    # 标题
    if not re.match(r"^\s*#\s+\S", body):
        problems.append("第一行不是「# 标题」格式")

    # 口吻专项
    if style_key == "personal-voice":
        first = len(re.findall("我", body))
        if first < 8:
            problems.append("个人口吻要求第一人称，全文「我」只出现 %d 次（要求 >= 8）" % first)
        else:
            notes.append("第一人称「我」出现 %d 次" % first)
        if not re.search(r"如果.{0,40}(就|那)", body, re.S):
            problems.append("个人口吻要求至少 1 组 if/then（「如果你…那就…」），没找到")
        if not re.search(r"(不确定|说不准|我也不敢说|可能会错|存疑)", body):
            problems.append("个人口吻要求主动承认一次不确定，没找到")
        heads = len(re.findall(r"^##\s+", body, re.M))
        if paras and heads and len(paras) / max(heads, 1) > 8:
            problems.append("小标题偏少：%d 段只有 %d 个小标题（约每 4-6 段应有一个）"
                            % (len(paras), heads))
    elif style_key == "business-insight":
        if not re.search(r"(边界|不成立|失效|什么情况下)", body):
            problems.append("商业洞察要求有「划边界」段（说明判断何时不成立），没找到")
        if len(re.findall(r"\d", body)) < 5:
            problems.append("商业洞察要求有具体数字支撑，全文数字过少")
    elif style_key == "finance-depth":
        if not re.search(r"(我认为|我的判断|我倾向于|在我看来)", body):
            problems.append("财经深度要求把「事实」与「我的判断」分开，没找到判断句标记")
        if not re.search(r"(什么情况下我错|如果.{0,20}错了|风险|不成立)", body):
            problems.append("财经深度要求写「什么情况下我错了」与风险提示，没找到")
        for bad in ["必涨", "稳赚", "暴富", "翻倍"]:
            if bad in body:
                problems.append("财经深度红线词：「%s」" % bad)
    elif style_key == "viral-structure":
        long_paras = [p for p in paras if len(p) > 160]
        if long_paras:
            problems.append("爆款结构要求每段不超过 4 行，有 %d 段明显偏长" % len(long_paras))

    # 通用 AI 味粗筛
    ai_smell = ["值得注意的是", "综上所述", "在当今时代", "随着社会的不断发展",
                "不难发现", "毋庸置疑"]
    hits = [w for w in ai_smell if w in body]
    if hits:
        notes.append("AI 高频词提示（不算硬违规，建议处理）：" + "、".join(hits))

    return problems, notes


def check_rewrite(cfg, mode_key, source, result):
    """改写校验：与原文对比，验「不新增事实」「字数不膨胀」「正文没混进改动说明」。"""
    problems, notes = [], []
    problems += process_heading_problems(result)
    sw, rw = count_words(source), count_words(result)
    if mode_key == "rewrite":
        if rw > sw * 1.05:
            problems.append("语言改写要求字数不膨胀：原文 %d 字 -> 改写后 %d 字" % (sw, rw))
        else:
            notes.append("字数 %d -> %d，未膨胀" % (sw, rw))
    s_nums = set(re.findall(r"\d+(?:\.\d+)?%?", source))
    r_nums = set(re.findall(r"\d+(?:\.\d+)?%?", result))
    added = r_nums - s_nums
    if added:
        notes.append("改写后新增了原文没有的数字，请人工确认是否属实："
                     + "、".join(sorted(added)[:10]))
    for bad in ["必涨", "稳赚", "暴富", "内幕消息"]:
        if bad in result and bad not in source:
            problems.append("改写引入了原文没有的红线词：「%s」" % bad)
    return problems, notes


# ---------------------------------------------------------------- CLI
def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="写初稿（四种口吻）/ 改写（三种模式）—— 纯提示词工程，不联网",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python scripts/article_writer.py --list\n"
               "  python scripts/article_writer.py --style business-insight --topic \"AI 编程\" -o prompt.md\n"
               "  python scripts/article_writer.py --check draft.md --style personal-voice\n")
    ap.add_argument("--list", action="store_true", help="列出四种口吻与三种改写模式")
    ap.add_argument("--style", help="口吻：business-insight / viral-structure / personal-voice / finance-depth")
    ap.add_argument("--topic", default="", help="选题（写初稿时用）")
    ap.add_argument("--material", help="素材文件（写初稿时用；只用这里的材料，不编造）")
    ap.add_argument("--words", default="", help="本次字数（覆盖风格默认字数）")
    ap.add_argument("--rewrite", metavar="MODE", help="改写模式：restructure / rewrite / humanize")
    ap.add_argument("--input", help="输入文件（改写时的原文）")
    ap.add_argument("--check", metavar="DRAFT", help="校验一份初稿是否符合指定 --style 的规则")
    ap.add_argument("--check-rewrite", metavar="RESULT",
                    help="校验改写稿：配合 --rewrite 模式与 --input 原文")
    ap.add_argument("-o", "--output", help="把生成的提示词写到文件（默认打印到屏幕）")
    args = ap.parse_args(argv)

    try:
        cfg = load_styles()
    except RuntimeError as e:
        print("错误：%s" % e)
        return 1

    styles, modes = cfg["styles"], cfg["rewrite_modes"]

    if args.list:
        log("四种口吻（--style）：")
        for k, v in styles.items():
            log("  %-18s %-10s %s" % (k, v["name"], v["one_liner"]))
            log("      %d 步结构 / %d 条硬规则 / %d 条禁用表达"
                % (len(v["structure"]), len(v["hard_rules"]), len(v["banned"])))
        log("")
        log("三种改写模式（--rewrite）：")
        for k, v in modes.items():
            log("  %-14s %-8s %s" % (k, v["name"], v["one_liner"]))
        log("")
        log("合规红线：")
        for c in cfg["compliance"]:
            log("  - " + c)
        return 0

    if args.check:
        if not args.style:
            print("错误：--check 必须同时给出 --style。")
            return 2
        if args.style not in styles:
            print("错误：没有这个口吻：%s（可用：%s）" % (args.style, "、".join(styles)))
            return 2
        if not os.path.isfile(args.check):
            print("错误：找不到文件：%s" % args.check)
            return 1
        problems, notes = check_draft(cfg, args.style, read_text(args.check))
        log("校验：%s" % args.check)
        log("口吻：%s（%s）" % (args.style, styles[args.style]["name"]))
        log("")
        for n in notes:
            log("  · %s" % n)
        if notes:
            log("")
        if problems:
            log("发现 %d 个问题：" % len(problems))
            for p in problems:
                log("  ✗ %s" % p)
            log("")
            log("结论：不合格。请按上面逐条修改后重跑本命令。")
            return 1
        log("结论：通过 —— 没有命中该口吻的禁用表达与硬规则。")
        return 0

    if args.check_rewrite:
        if not args.rewrite or not args.input:
            print("错误：--check-rewrite 需要同时给出 --rewrite 模式与 --input 原文。")
            return 2
        if args.rewrite not in modes:
            print("错误：没有这个改写模式：%s（可用：%s）" % (args.rewrite, "、".join(modes)))
            return 2
        for p in (args.input, args.check_rewrite):
            if not os.path.isfile(p):
                print("错误：找不到文件：%s" % p)
                return 1
        problems, notes = check_rewrite(cfg, args.rewrite,
                                        read_text(args.input), read_text(args.check_rewrite))
        log("改写校验：%s（模式 %s）" % (args.check_rewrite, modes[args.rewrite]["name"]))
        log("")
        for n in notes:
            log("  · %s" % n)
        if notes:
            log("")
        if problems:
            log("发现 %d 个问题：" % len(problems))
            for p in problems:
                log("  ✗ %s" % p)
            return 1
        log("结论：通过。")
        return 0

    if args.rewrite:
        if args.rewrite not in modes:
            print("错误：没有这个改写模式：%s（可用：%s）" % (args.rewrite, "、".join(modes)))
            return 2
        if not args.input or not os.path.isfile(args.input):
            print("错误：改写需要 --input 指向原文文件。")
            return 2
        prompt = build_rewrite_prompt(cfg, args.rewrite, read_text(args.input))
        head = "改写模式：%s（%s）" % (args.rewrite, modes[args.rewrite]["name"])
    elif args.style:
        if args.style not in styles:
            print("错误：没有这个口吻：%s（可用：%s）" % (args.style, "、".join(styles)))
            return 2
        material = ""
        if args.material:
            if not os.path.isfile(args.material):
                print("错误：找不到素材文件：%s" % args.material)
                return 1
            material = read_text(args.material)
        prompt = build_draft_prompt(cfg, args.style, args.topic, material, args.words)
        head = "写初稿：%s（%s）" % (args.style, styles[args.style]["name"])
    else:
        ap.print_help()
        print("\n错误：请用 --style 写初稿、--rewrite 做改写、--check 校验，或 --list 看清单。")
        return 2

    if args.output:
        parent = os.path.dirname(os.path.abspath(args.output))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.output, "w", encoding="utf-8", newline="\n") as f:
            f.write(prompt)
        log("%s" % head)
        log("提示词已写入：%s（%s 字符）"
            % (os.path.abspath(args.output), format(len(prompt), ",")))
        log("接下来：这份提示词就是本口吻的完整规则 —— **你自己按它写出初稿**，")
        log("别再把提示词转手给别人写（豆包工作本身就是 AI）。")
        log("写完后用 --check 拿同一套规则验收，不合格就改，别把没过检查的稿子递出去。")
        log("（这份提示词本身是副产品：可以留档，也可以拿去别的工具用。）")
    else:
        print(prompt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
