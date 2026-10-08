#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
排版引擎 —— anan-wechat-suite 模块 7
========================================
把 Markdown 转成**可以直接粘进微信公众号后台**的 HTML。

设计铁律（公众号编辑器的真实限制，实测总结）：
    1. 只用**内联 style**，绝不写 <style> 标签、绝不用 class
       —— 公众号编辑器会把 <style> 和 class 全部剥掉，粘过去等于没排版。
    2. 需要背景色 / 边框的块，用 <section style="..."> 包一层
       —— 直接给 <p>/<blockquote> 加背景在部分场景会被吃掉。
    3. <li> 里以 <strong> 开头时，整行都会变粗
       —— 必须在强标签后面补 <span style="font-weight:normal"> 把字重断开。
    4. 表格用真正的 <table>，不要用 div 拼。

另外两个 Windows 上必踩的坑，本引擎已显式处理：
    - **BOM**：记事本 / PowerShell 写出的 UTF-8 文件带 BOM（\ufeff），
      会让第一行标题识别失败、H1 直接丢失。读取时显式剥掉。
    - **CRLF**：Windows 换行会让正则里的 $ 行为异常，读取时统一归一。

引擎是纯 Python 手写解析器，零第三方依赖、零网络、离线可用。

用法：
    python scripts/typeset.py article.md -o article.html
    python scripts/typeset.py article.md --theme ink --code-theme monokai -o out.html
    python scripts/typeset.py article.md --theme night --full-html -o preview.html
    python scripts/typeset.py --list-themes
"""

import argparse
import html as htmllib
import os
import re
import sys

VERSION = "1.0.0"
BQ = chr(96)          # 反引号，用变量表示避免被外层工具链吃掉
FENCE = BQ * 3


# ================================================================ 页面主题
# 每个主题都是一组「样式片段字符串」，取值时统一走 theme["xxx"]，
# 缺键就回退到默认主题的同名键 —— 加主题只写差异项即可。
DEFAULT_THEME = {
    "body": "font-size:16px;color:#2c3038;line-height:1.85;letter-spacing:.4px;"
            "text-align:justify;word-break:break-word;",
    "h1": "font-size:22px;font-weight:bold;color:#1a6dd4;text-align:center;"
          "line-height:1.5;margin:0 0 22px;padding-bottom:12px;"
          "border-bottom:2px solid #e6eefb;",
    "h2": "font-size:18px;font-weight:bold;color:#14315c;line-height:1.6;"
          "margin:30px 0 16px;padding-left:11px;border-left:4px solid #1a6dd4;",
    "h3": "font-size:16px;font-weight:bold;color:#1a6dd4;line-height:1.6;margin:24px 0 12px;",
    "h4": "font-size:15px;font-weight:bold;color:#3d4655;line-height:1.6;margin:20px 0 10px;",
    "p": "margin:0 0 16px;line-height:1.85;font-size:16px;color:#2c3038;",
    "quote_wrap": "background:#f5f8fd;border-left:3px solid #1a6dd4;padding:12px 16px;"
                  "margin:16px 0;border-radius:0 6px 6px 0;",
    "quote_p": "margin:0;font-size:15px;color:#5a6472;line-height:1.8;",
    "code_inline": "background:#f0f3f8;color:#d6336c;padding:2px 5px;border-radius:3px;"
                   "font-size:14px;font-family:Consolas,Monaco,monospace;",
    "code_wrap": "background:#f7f8fa;border:1px solid #e6e9ef;border-radius:6px;"
                 "padding:14px 16px;margin:18px 0;overflow-x:auto;",
    "code": "font-size:13px;line-height:1.65;font-family:Consolas,Monaco,monospace;"
            "color:#24292f;white-space:pre;margin:0;",
    "ul": "margin:0 0 16px;padding-left:22px;",
    "ol": "margin:0 0 16px;padding-left:22px;",
    "li": "margin:0 0 8px;line-height:1.85;font-size:16px;color:#2c3038;",
    "table": "width:100%;border-collapse:collapse;margin:18px 0;font-size:14px;",
    "th": "background:#eef3fb;border:1px solid #dbe3ef;padding:8px 10px;"
          "font-weight:bold;color:#14315c;text-align:left;",
    "td": "border:1px solid #dbe3ef;padding:8px 10px;color:#2c3038;line-height:1.7;",
    "img": "max-width:100%;height:auto;display:block;margin:16px auto;border-radius:6px;",
    "hr": "border:none;border-top:1px dashed #d5dbe5;margin:26px 0;",
    "a": "color:#1a6dd4;text-decoration:none;border-bottom:1px solid #a8c8f0;",
    "strong": "font-weight:bold;color:#14315c;",
    "em": "font-style:italic;",
    "del": "text-decoration:line-through;color:#8a93a0;",
    "figure_cap": "font-size:13px;color:#8a93a0;text-align:center;margin:-8px 0 18px;",
    "anchor": "display:block;height:0;overflow:hidden;",
}

THEMES = {
    "anan": {
        "label": "阿南蓝（默认）",
        "desc": "蓝色主色，干净利落，适合工具教程 / 干货长文。",
    },
    "ink": {
        "label": "墨",
        "desc": "灰黑单色 + 衬线字体，克制、耐读，适合观点文与深度长文。",
        "body": "font-size:16px;color:#2b2b2b;line-height:1.9;letter-spacing:.5px;"
                "text-align:justify;word-break:break-word;"
                "font-family:Georgia,'Songti SC',SimSun,serif;",
        "h1": "font-size:22px;font-weight:bold;color:#1a1a1a;text-align:center;"
              "line-height:1.6;margin:0 0 24px;padding-bottom:14px;"
              "border-bottom:1px solid #dcdcdc;letter-spacing:1px;",
        "h2": "font-size:18px;font-weight:bold;color:#1a1a1a;line-height:1.6;"
              "margin:32px 0 16px;padding-left:10px;border-left:3px solid #1a1a1a;",
        "h3": "font-size:16px;font-weight:bold;color:#333;line-height:1.6;margin:24px 0 12px;",
        "p": "margin:0 0 18px;line-height:1.9;font-size:16px;color:#2b2b2b;",
        "quote_wrap": "background:#f7f7f7;border-left:3px solid #b9b9b9;padding:14px 18px;"
                      "margin:18px 0;",
        "quote_p": "margin:0;font-size:15px;color:#5f5f5f;line-height:1.85;",
        "strong": "font-weight:bold;color:#1a1a1a;",
        "a": "color:#1a1a1a;text-decoration:none;border-bottom:1px solid #bbb;",
        "th": "background:#f0f0f0;border:1px solid #d8d8d8;padding:9px 10px;"
              "font-weight:bold;color:#1a1a1a;text-align:left;",
        "td": "border:1px solid #d8d8d8;padding:9px 10px;color:#2b2b2b;line-height:1.75;",
    },
    "warm": {
        "label": "暖阳",
        "desc": "橙色主色 + 米白底，亲切，适合个人号与故事型内容。",
        "body": "font-size:16px;color:#3a3229;line-height:1.85;letter-spacing:.4px;"
                "text-align:justify;word-break:break-word;",
        "h1": "font-size:22px;font-weight:bold;color:#c8641e;text-align:center;"
              "line-height:1.5;margin:0 0 22px;padding-bottom:12px;"
              "border-bottom:2px solid #f6e3d2;",
        "h2": "font-size:18px;font-weight:bold;color:#8a4513;line-height:1.6;"
              "margin:30px 0 16px;padding-left:11px;border-left:4px solid #e08a3c;",
        "h3": "font-size:16px;font-weight:bold;color:#c8641e;line-height:1.6;margin:24px 0 12px;",
        "p": "margin:0 0 16px;line-height:1.85;font-size:16px;color:#3a3229;",
        "quote_wrap": "background:#fdf6ef;border-left:3px solid #e08a3c;padding:12px 16px;"
                      "margin:16px 0;border-radius:0 6px 6px 0;",
        "quote_p": "margin:0;font-size:15px;color:#6b5c4a;line-height:1.8;",
        "code_inline": "background:#fbf0e6;color:#b3531a;padding:2px 5px;border-radius:3px;"
                       "font-size:14px;font-family:Consolas,Monaco,monospace;",
        "th": "background:#fbf0e6;border:1px solid #ecd9c6;padding:8px 10px;"
              "font-weight:bold;color:#8a4513;text-align:left;",
        "td": "border:1px solid #ecd9c6;padding:8px 10px;color:#3a3229;line-height:1.7;",
        "strong": "font-weight:bold;color:#8a4513;",
        "a": "color:#c8641e;text-decoration:none;border-bottom:1px solid #f0c9a5;",
    },
    "forest": {
        "label": "青林",
        "desc": "墨绿主色，稳重，适合行业分析与方法论。",
        "body": "font-size:16px;color:#2a332e;line-height:1.85;letter-spacing:.4px;"
                "text-align:justify;word-break:break-word;",
        "h1": "font-size:22px;font-weight:bold;color:#2e7d5b;text-align:center;"
              "line-height:1.5;margin:0 0 22px;padding-bottom:12px;"
              "border-bottom:2px solid #dceee4;",
        "h2": "font-size:18px;font-weight:bold;color:#1d5540;line-height:1.6;"
              "margin:30px 0 16px;padding-left:11px;border-left:4px solid #2e7d5b;",
        "h3": "font-size:16px;font-weight:bold;color:#2e7d5b;line-height:1.6;margin:24px 0 12px;",
        "p": "margin:0 0 16px;line-height:1.85;font-size:16px;color:#2a332e;",
        "quote_wrap": "background:#f2f9f5;border-left:3px solid #2e7d5b;padding:12px 16px;"
                      "margin:16px 0;border-radius:0 6px 6px 0;",
        "quote_p": "margin:0;font-size:15px;color:#4c6157;line-height:1.8;",
        "code_inline": "background:#eef6f1;color:#20614a;padding:2px 5px;border-radius:3px;"
                       "font-size:14px;font-family:Consolas,Monaco,monospace;",
        "th": "background:#e9f4ee;border:1px solid #d0e6da;padding:8px 10px;"
              "font-weight:bold;color:#1d5540;text-align:left;",
        "td": "border:1px solid #d0e6da;padding:8px 10px;color:#2a332e;line-height:1.7;",
        "strong": "font-weight:bold;color:#1d5540;",
        "a": "color:#2e7d5b;text-decoration:none;border-bottom:1px solid #a9d4bf;",
    },
    "night": {
        "label": "夜航（深色）",
        "desc": "深色底 + 蓝紫高亮，适合技术向内容与夜间阅读。",
        "body": "font-size:16px;color:#d7dce5;line-height:1.85;letter-spacing:.4px;"
                "text-align:justify;word-break:break-word;background:#1b1f27;"
                "padding:20px 18px;border-radius:8px;",
        "h1": "font-size:22px;font-weight:bold;color:#8ab4ff;text-align:center;"
              "line-height:1.5;margin:0 0 22px;padding-bottom:12px;"
              "border-bottom:1px solid #2c3444;",
        "h2": "font-size:18px;font-weight:bold;color:#a8c8ff;line-height:1.6;"
              "margin:30px 0 16px;padding-left:11px;border-left:4px solid #6aa9ff;",
        "h3": "font-size:16px;font-weight:bold;color:#8ab4ff;line-height:1.6;margin:24px 0 12px;",
        "h4": "font-size:15px;font-weight:bold;color:#8ab4ff;line-height:1.6;margin:20px 0 10px;",
        "p": "margin:0 0 16px;line-height:1.85;font-size:16px;color:#d7dce5;",
        "quote_wrap": "background:#232936;border-left:3px solid #6aa9ff;padding:12px 16px;"
                      "margin:16px 0;border-radius:0 6px 6px 0;",
        "quote_p": "margin:0;font-size:15px;color:#9aa6b8;line-height:1.8;",
        "code_inline": "background:#2a3140;color:#ff9ec4;padding:2px 5px;border-radius:3px;"
                       "font-size:14px;font-family:Consolas,Monaco,monospace;",
        "code_wrap": "background:#151922;border:1px solid #2c3444;border-radius:6px;"
                     "padding:14px 16px;margin:18px 0;overflow-x:auto;",
        "code": "font-size:13px;line-height:1.65;font-family:Consolas,Monaco,monospace;"
                "color:#d7dce5;white-space:pre;margin:0;",
        "ul": "margin:0 0 16px;padding-left:22px;",
        "ol": "margin:0 0 16px;padding-left:22px;",
        "li": "margin:0 0 8px;line-height:1.85;font-size:16px;color:#d7dce5;",
        "table": "width:100%;border-collapse:collapse;margin:18px 0;font-size:14px;",
        "th": "background:#232936;border:1px solid #333c4d;padding:8px 10px;"
              "font-weight:bold;color:#a8c8ff;text-align:left;",
        "td": "border:1px solid #333c4d;padding:8px 10px;color:#d7dce5;line-height:1.7;",
        "img": "max-width:100%;height:auto;display:block;margin:16px auto;border-radius:6px;",
        "hr": "border:none;border-top:1px dashed #333c4d;margin:26px 0;",
        "a": "color:#8ab4ff;text-decoration:none;border-bottom:1px solid #3b5a8f;",
        "strong": "font-weight:bold;color:#a8c8ff;",
        "em": "font-style:italic;color:#e6e9f0;",
        "del": "text-decoration:line-through;color:#6b7688;",
        "figure_cap": "font-size:13px;color:#6b7688;text-align:center;margin:-8px 0 18px;",
    },
}
THEMES["anan"] = dict(DEFAULT_THEME, **THEMES["anan"])

# 每个主题都补齐缺省键
for _k, _v in THEMES.items():
    for _dk, _dv in DEFAULT_THEME.items():
        _v.setdefault(_dk, _dv)


# ================================================================ 代码主题
CODE_THEMES = {
    "github-light": {"label": "GitHub Light", "bg": "#f6f8fa", "text": "#24292f",
                     "comment": "#6e7781", "string": "#0a3069", "number": "#0550ae",
                     "keyword": "#cf222e", "func": "#8250df", "punct": "#57606a"},
    "vscode-dark": {"label": "VS Code Dark", "bg": "#1e1e1e", "text": "#d4d4d4",
                    "comment": "#6a9955", "string": "#ce9178", "number": "#b5cea8",
                    "keyword": "#569cd6", "func": "#dcdcaa", "punct": "#d4d4d4"},
    "monokai": {"label": "Monokai", "bg": "#272822", "text": "#f8f8f2",
                "comment": "#75715e", "string": "#e6db74", "number": "#ae81ff",
                "keyword": "#f92672", "func": "#a6e22e", "punct": "#f8f8f2"},
    "one-dark": {"label": "One Dark", "bg": "#282c34", "text": "#abb2bf",
                 "comment": "#5c6370", "string": "#98c379", "number": "#d19a66",
                 "keyword": "#c678dd", "func": "#61afef", "punct": "#abb2bf"},
    "solarized-light": {"label": "Solarized Light", "bg": "#fdf6e3", "text": "#657b83",
                        "comment": "#93a1a1", "string": "#2aa198", "number": "#d33682",
                        "keyword": "#859900", "func": "#268bd2", "punct": "#657b83"},
}

# 各语言的高亮规则：行注释 / 块注释 / 字符串 / 关键字
_LANG_RULES = {
    "python":     (("#",), (),
                   ("'''", '"""', '"', "'"),
                   "False None True and as assert async await break class continue def del elif else "
                   "except finally for from global if import in is lambda nonlocal not or pass raise "
                   "return try while with yield match case self"),
    "javascript": (("//",), ("/*", "*/"),
                   ('"', "'", BQ),
                   "break case catch class const continue debugger default delete do else export "
                   "extends finally for function if import in instanceof let new return super switch "
                   "this throw try typeof var void while with yield async await of static get set"),
    "typescript": (("//",), ("/*", "*/"),
                   ('"', "'", BQ),
                   "break case catch class const continue declare default delete do else enum export "
                   "extends finally for function if implements import in instanceof interface let new "
                   "private public protected readonly return super switch this throw try type typeof "
                   "var void while with yield async await of as any string number boolean"),
    "json":       ((), (), ('"',), "true false null"),
    "bash":       (("#",), (), ('"', "'"),
                   "if then else elif fi for while do done case esac function return exit export "
                   "local echo cd ls mkdir rm cp mv grep sed awk cat curl git python npm sudo chmod"),
    "sql":        (("--",), ("/*", "*/"), ("'", '"'),
                   "select from where group by order having insert into values update set delete "
                   "create table alter drop index join left right inner outer on as and or not null "
                   "distinct limit offset union all count sum avg max min primary key foreign"),
    "go":         (("//",), ("/*", "*/"), ('"', "'", BQ),
                   "break case chan const continue default defer else fallthrough for func go goto if "
                   "import interface map package range return select struct switch type var nil true false"),
    "java":       (("//",), ("/*", "*/"), ('"', "'"),
                   "abstract boolean break byte case catch char class const continue default do double "
                   "else enum extends final finally float for if implements import int interface long "
                   "native new package private protected public return short static super switch "
                   "synchronized this throw throws try void volatile while true false null"),
    "rust":       (("//",), ("/*", "*/"), ('"', "'"),
                   "as async await break const continue crate dyn else enum extern fn for if impl in "
                   "let loop match mod move mut pub ref return self static struct super trait type "
                   "unsafe use where while true false"),
    "c":          (("//",), ("/*", "*/"), ('"', "'"),
                   "auto break case char const continue default do double else enum extern float for "
                   "goto if int long register return short signed sizeof static struct switch typedef "
                   "union unsigned void volatile while include define ifndef endif"),
    "cpp":        (("//",), ("/*", "*/"), ('"', "'"),
                   "auto bool break case catch char class const constexpr continue default delete do "
                   "double else enum explicit extern false float for friend if inline int long "
                   "namespace new nullptr operator private protected public return short sizeof "
                   "static struct switch template this throw true try typedef typename union unsigned "
                   "using virtual void while"),
    "html":       ((), ("<!--", "-->"), ('"', "'"),
                   "html head body div span p a img ul ol li table tr td th script style link meta "
                   "section article header footer nav form input button h1 h2 h3 h4 h5 h6"),
    "css":        ((), ("/*", "*/"), ('"', "'"),
                   "color background margin padding border font display position top left right "
                   "bottom width height flex grid align justify content none block inline absolute "
                   "relative fixed important"),
    "yaml":       (("#",), (), ('"', "'"), "true false null yes no on off"),
    "php":        (("//", "#"), ("/*", "*/"), ('"', "'"),
                   "abstract and array as break case catch class clone const continue declare default "
                   "do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile "
                   "extends final finally fn for foreach function global if implements include "
                   "instanceof interface isset list namespace new or print private protected public "
                   "require return static switch throw trait try unset use var while xor yield true false null"),
    "ruby":       (("#",), (), ('"', "'"),
                   "alias and begin break case class def defined do else elsif end ensure false for "
                   "if in module next nil not or redo rescue retry return self super then true undef "
                   "unless until when while yield require attr_accessor puts"),
    "plaintext":  ((), (), (), ""),
}

_LANG_ALIAS = {
    "py": "python", "js": "javascript", "jsx": "javascript", "ts": "typescript",
    "tsx": "typescript", "sh": "bash", "shell": "bash", "zsh": "bash", "console": "bash",
    "postgres": "sql", "mysql": "sql", "golang": "go", "rs": "rust", "c++": "cpp",
    "csharp": "java", "cs": "java", "yml": "yaml", "htm": "html", "xml": "html",
    "text": "plaintext", "txt": "plaintext", "": "plaintext",
}


def esc(s):
    """HTML 转义。所有进入输出的文本都必须过这里。"""
    return htmllib.escape(s, quote=False)


def resolve_lang(lang):
    lang = (lang or "").strip().lower()
    lang = _LANG_ALIAS.get(lang, lang)
    return lang if lang in _LANG_RULES else "plaintext"


def highlight(code, lang, ctheme):
    """把代码文本切成带内联颜色的 span。

    用一条组合正则单遍扫描：注释 / 字符串 / 数字 / 关键字 / 函数名，
    没命中的部分按原文转义输出。单遍扫描保证**不会破坏代码**——
    任何字符要么被某个 token 吃掉，要么原样输出，不存在漏字符的可能。
    """
    lang = resolve_lang(lang)
    line_c, block_c, quotes, kws = _LANG_RULES[lang]
    alts = []
    if block_c:
        alts.append(r"(?P<blockc>%s[\s\S]*?%s)" % (re.escape(block_c[0]), re.escape(block_c[1])))
    if line_c:
        alts.append(r"(?P<linec>%s[^\n]*)" % "|".join(re.escape(c) for c in line_c))
    if quotes:
        qs = sorted(quotes, key=len, reverse=True)
        triple = [q for q in qs if len(q) == 3]
        single = [q for q in qs if len(q) == 1]
        parts = []
        for q in triple:
            e = re.escape(q)
            parts.append(e + r"[\s\S]*?" + e)
        for q in single:
            e = re.escape(q)
            parts.append(e + r"(?:\\.|[^" + e + r"\\\n])*" + e)
        if parts:
            alts.append(r"(?P<str>" + "|".join(parts) + ")")
    alts.append(r"(?P<num>\b\d+\.?\d*\b)")
    if kws.strip():
        alts.append(r"(?P<kw>\b(?:" + "|".join(re.escape(k) for k in kws.split()) + r")\b)")
    alts.append(r"(?P<func>\b[A-Za-z_]\w*(?=\())")

    if not alts:
        return esc(code)

    pattern = re.compile("|".join(alts))
    out = []
    pos = 0
    for m in pattern.finditer(code):
        if m.start() > pos:
            out.append(esc(code[pos:m.start()]))
        kind = m.lastgroup
        color = {"blockc": ctheme["comment"], "linec": ctheme["comment"],
                 "str": ctheme["string"], "num": ctheme["number"],
                 "kw": ctheme["keyword"], "func": ctheme["func"]}.get(kind, ctheme["text"])
        style = "color:%s;" % color
        if kind == "kw":
            style += "font-weight:bold;"
        out.append('<span style="%s">%s</span>' % (style, esc(m.group(0))))
        pos = m.end()
    if pos < len(code):
        out.append(esc(code[pos:]))
    return "".join(out)


# ================================================================ 行内解析
_RE_CODE = re.compile(r"(" + BQ + r"+)(.+?)\1")
_RE_IMG = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_RE_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_RE_BOLD = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", re.S)
_RE_ITAL = re.compile(r"(?<!\*)\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)", re.S)
_RE_BOLDITAL = re.compile(r"\*\*\*(?!\s)(.+?)(?<!\s)\*\*\*", re.S)
_RE_DEL = re.compile(r"~~(?!\s)(.+?)(?<!\s)~~", re.S)
_RE_AUTOLINK = re.compile(r"(?<![\"'=])\b(https?://[^\s<>()\u4e00-\u9fff]+)")


def render_inline(text, theme):
    """行内 Markdown -> HTML。先抠出行内代码并占位，避免代码里的符号被当成语法。"""
    if not text:
        return ""
    stash = []

    def _stash(html):
        stash.append(html)
        return "\x00%d\x00" % (len(stash) - 1)

    def _code(m):
        return _stash('<code style="%s">%s</code>' % (theme["code_inline"], esc(m.group(2))))

    text = _RE_CODE.sub(_code, text)

    def _img(m):
        alt, src = m.group(1), m.group(2)
        return _stash('<img src="%s" alt="%s" style="%s"/>'
                      % (esc(src), esc(alt), theme["img"]))

    text = _RE_IMG.sub(_img, text)

    def _link(m):
        return _stash('<a href="%s" style="%s">%s</a>'
                      % (esc(m.group(2)), theme["a"], esc(m.group(1))))

    text = _RE_LINK.sub(_link, text)

    text = esc(text)
    text = _RE_BOLDITAL.sub(r'<strong style="%s"><em>%s</em></strong>' % (theme["strong"], r"\1"), text)
    text = _RE_BOLD.sub(r'<strong style="%s">%s</strong>' % (theme["strong"], r"\1"), text)
    text = _RE_ITAL.sub(r'<em style="%s">%s</em>' % (theme["em"], r"\1"), text)
    text = _RE_DEL.sub(r'<del style="%s">%s</del>' % (theme["del"], r"\1"), text)
    text = _RE_AUTOLINK.sub(r'<a href="\1" style="%s">\1</a>' % theme["a"], text)

    def _unstash(m):
        return stash[int(m.group(1))]

    return re.sub("\x00(\\d+)\x00", _unstash, text)


# ================================================================ 块级解析
_RE_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
_RE_HR = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_RE_UL = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_RE_OL = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_RE_TASK = re.compile(r"^\[([ xX])\]\s+(.*)$")
_RE_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_RE_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_RE_FENCE = re.compile(r"^\s*(" + BQ + r"{3,}|~{3,})\s*([\w+#.-]*)\s*$")


def split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells, buf, esc_next = [], [], False
    for ch in s:
        if esc_next:
            buf.append(ch)
            esc_next = False
        elif ch == "\\":
            esc_next = True
        elif ch == "|":
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    cells.append("".join(buf).strip())
    return cells


def render_list(lines, theme, start=0):
    """渲染（可嵌套的）列表。返回 (html, 消耗的行数)。

    ★ 公众号硬经验：<li> 里若以 <strong> 开头，整行都会变粗。
      所以这里检测到「强标签打头」时，立刻用 <span style="font-weight:normal">
      把后面的正文包起来，断开字重。
    """
    html_parts = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m_ul = _RE_UL.match(line)
        m_ol = _RE_OL.match(line)
        if not (m_ul or m_ol):
            break
        indent = len((m_ul or m_ol).group(1).replace("\t", "    "))
        ordered = bool(m_ol)
        marker = (m_ol.group(2) + ". ") if ordered else "• "
        content = (m_ol.group(3) if ordered else m_ul.group(3))

        # 收集该项的续行（缩进更深的普通行 / 子列表）
        sub_lines = []
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if not nxt.strip():
                break
            n_indent = len(nxt) - len(nxt.lstrip())
            if n_indent <= indent and (_RE_UL.match(nxt) or _RE_OL.match(nxt)):
                break
            if n_indent > indent:
                sub_lines.append(nxt[indent + 2:] if len(nxt) > indent + 2 else nxt.strip())
                j += 1
                continue
            break

        task = _RE_TASK.match(content)
        if task:
            checked = task.group(1).lower() == "x"
            content = ("☑ " if checked else "☐ ") + task.group(2)

        inner = render_inline(content, theme)
        # 续行：普通文字接在后面，列表行另起子列表
        plain_follow, nested = [], []
        for sl in sub_lines:
            if _RE_UL.match(sl) or _RE_OL.match(sl):
                nested.append(sl)
            elif sl.strip():
                plain_follow.append(sl.strip())
        if plain_follow:
            inner += "<br/>" + "<br/>".join(render_inline(x, theme) for x in plain_follow)

        # ★ 字重断开：<li><strong>…</strong>正文</li> 会让整行变粗
        if inner.startswith("<strong"):
            close = inner.find("</strong>")
            if close != -1:
                head = inner[:close + len("</strong>")]
                tail = inner[close + len("</strong>"):]
                if tail.strip():
                    inner = head + '<span style="font-weight:normal;">' + tail + "</span>"
        if inner.startswith("<strong") and not nested:
            pass  # 整项就是加粗前缀，保持加粗

        if nested:
            sub_html, _used = render_list(nested, theme)
            inner += sub_html

        html_parts.append('<li style="%s">%s</li>' % (theme["li"], inner))
        i = j

    tag = "ol" if ordered else "ul"
    style_key = "ol" if ordered else "ul"
    attr = ' start="%d"' % start if (ordered and start and start != 1) else ""
    return "<%s%s style=\"%s\">%s</%s>" % (tag, attr, theme[style_key], "".join(html_parts), tag), i


def md_to_html(md, theme, ctheme, title_hint=""):
    """Markdown -> 公众号可粘贴的 HTML（全内联样式）。"""
    # ---- 显式处理 BOM 与换行（Windows 记事本必踩）----
    md = md.replace("\ufeff", "")
    md = md.replace("\r\n", "\n").replace("\r", "\n")

    lines = md.split("\n")
    out = []
    i = 0
    n = len(lines)
    in_fence = False
    fence_lang = ""
    fence_buf = []
    stats = {"h1": 0, "h2": 0, "code": 0, "table": 0, "list": 0, "img": 0, "quote": 0}

    def flush_para(buf):
        if not buf:
            return
        text = " ".join(buf).strip()
        if text:
            out.append('<p style="%s">%s</p>' % (theme["p"], render_inline(text, theme)))

    para = []
    while i < n:
        line = lines[i]

        # ---- 代码围栏 ----
        mf = _RE_FENCE.match(line)
        if mf:
            if not in_fence:
                flush_para(para); para = []
                in_fence = True
                fence_lang = mf.group(2) or ""
                fence_buf = []
            else:
                code = "\n".join(fence_buf)
                body = highlight(code, fence_lang, ctheme)
                out.append('<section style="%s"><pre style="%s">%s</pre></section>'
                           % (theme["code_wrap"], theme["code"], body))
                stats["code"] += 1
                in_fence = False
                fence_buf = []
            i += 1
            continue
        if in_fence:
            fence_buf.append(line)
            i += 1
            continue

        # ---- 空行 ----
        if not line.strip():
            flush_para(para); para = []
            i += 1
            continue

        # ---- 标题 ----
        mh = _RE_HEAD.match(line)
        if mh:
            flush_para(para); para = []
            lvl = len(mh.group(1))
            content = mh.group(2).strip().rstrip("#").strip()
            key = "h%d" % min(lvl, 4)
            stats["h1" if lvl == 1 else "h2"] += 1
            tag = "h%d" % min(lvl, 4)
            out.append('<%s style="%s">%s</%s>'
                       % (tag, theme[key], render_inline(content, theme), tag))
            i += 1
            continue

        # ---- 分隔线 ----
        if _RE_HR.match(line) and not _RE_UL.match(line):
            flush_para(para); para = []
            out.append('<hr style="%s"/>' % theme["hr"])
            i += 1
            continue

        # ---- 表格 ----
        if "|" in line and i + 1 < n and _RE_TABLE_SEP.match(lines[i + 1]):
            flush_para(para); para = []
            header = split_row(line)
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(split_row(lines[i]))
                i += 1
            th = "".join('<th style="%s">%s</th>' % (theme["th"], render_inline(c, theme))
                         for c in header)
            body = ""
            for r in rows:
                body += "<tr>" + "".join(
                    '<td style="%s">%s</td>' % (theme["td"], render_inline(c, theme))
                    for c in r) + "</tr>"
            out.append('<table style="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>'
                       % (theme["table"], th, body))
            stats["table"] += 1
            continue

        # ---- 引用 ----
        if _RE_QUOTE.match(line):
            flush_para(para); para = []
            qbuf = []
            while i < n and _RE_QUOTE.match(lines[i]):
                qbuf.append(_RE_QUOTE.match(lines[i]).group(1))
                i += 1
            inner = "<br/>".join(render_inline(x, theme) for x in qbuf if x.strip())
            out.append('<section style="%s"><p style="%s">%s</p></section>'
                       % (theme["quote_wrap"], theme["quote_p"], inner))
            stats["quote"] += 1
            continue

        # ---- 列表 ----
        if _RE_UL.match(line) or _RE_OL.match(line):
            flush_para(para); para = []
            start = 0
            mo = _RE_OL.match(line)
            if mo:
                start = int(mo.group(2))
            block = [lines[i]]
            j = i + 1
            while j < n and (lines[j].strip() and
                             (lines[j].startswith((" ", "\t")) or
                              _RE_UL.match(lines[j]) or _RE_OL.match(lines[j]))):
                block.append(lines[j])
                j += 1
            html, _ = render_list(block, theme, start=start)
            out.append(html)
            stats["list"] += 1
            i = j
            continue

        # ---- 图片独占一行 ----
        if line.strip().startswith("!["):
            flush_para(para); para = []
            out.append('<p style="%s">%s</p>'
                       % (theme["p"], render_inline(line.strip(), theme)))
            stats["img"] += 1
            i += 1
            continue

        para.append(line.strip())
        i += 1

    flush_para(para)

    # 正文首尾的标题重复（抓取来的 md 第一行常是 # 标题）不做特殊处理，
    # 交给用户按需删；这里只保证不丢内容。
    body = "\n".join(out)
    return body, stats


# ================================================================ 组装 / CLI
def build_document(body_html, theme, theme_name, title=""):
    """包成一份完整的 HTML 文档，方便在浏览器里预览与存档。

    注意：正文部分**仍然是全内联样式**，所以同一份内容既能浏览器预览，
    也能整段复制粘进公众号。
    """
    t = theme
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"utf-8\"/>\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>\n"
        "<title>%s</title>\n"
        "<!-- 由 anan-wechat-suite typeset 生成 | 主题 %s | 作者 阿南 rockmoons -->\n"
        "</head>\n<body style=\"margin:0;padding:24px;background:#eceff3;\">\n"
        "<article style=\"max-width:677px;margin:0 auto;background:#fff;"
        "padding:28px 22px;border-radius:10px;"
        "box-shadow:0 2px 16px rgba(0,0,0,.06);\">\n"
        "<section data-tool=\"anan-wechat-suite\" data-theme=\"%s\" style=\"%s\">\n"
        "%s\n</section>\n</article>\n</body>\n</html>\n"
    ) % (esc(title or "阿南 · 公众号排版"), theme_name, theme_name, t["body"], body_html)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="Markdown -> 微信公众号可粘贴 HTML（纯本地引擎，零依赖零网络）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python scripts/typeset.py article.md -o article.html\n"
               "  python scripts/typeset.py article.md --theme ink --code-theme monokai -o out.html\n"
               "  python scripts/typeset.py --list-themes\n")
    ap.add_argument("input", nargs="?", help="输入的 Markdown 文件")
    ap.add_argument("-o", "--output", help="输出的 HTML 文件（默认 <输入名>.html）")
    ap.add_argument("--theme", default="anan", help="页面主题（默认 anan，见 --list-themes）")
    ap.add_argument("--code-theme", default="github-light", help="代码高亮主题（默认 github-light）")
    ap.add_argument("--full-html", action="store_true",
                    help="输出完整 HTML 文档（浏览器可直接打开预览）；默认只输出正文片段")
    ap.add_argument("--strip-header", action="store_true",
                    help="去掉正文开头的引用块（抓取来的 md 顶部有「公众号/作者/时间」那几行）")
    ap.add_argument("--list-themes", action="store_true", help="列出所有主题")
    args = ap.parse_args(argv)

    if args.list_themes:
        print("页面主题（--theme）：")
        for k, v in THEMES.items():
            print("  %-8s %-14s %s" % (k, v["label"], v["desc"]))
        print()
        print("代码主题（--code-theme）：")
        for k, v in CODE_THEMES.items():
            print("  %-16s %s" % (k, v["label"]))
        return 0

    if not args.input:
        ap.print_help()
        print("\n错误：请给出要排版的 Markdown 文件。")
        return 2
    if not os.path.isfile(args.input):
        print("错误：找不到输入文件：%s" % args.input)
        return 1
    if args.theme not in THEMES:
        print("错误：没有这个页面主题：%s" % args.theme)
        print("可用主题：" + "、".join(THEMES))
        return 2
    if args.code_theme not in CODE_THEMES:
        print("错误：没有这个代码主题：%s" % args.code_theme)
        print("可用主题：" + "、".join(CODE_THEMES))
        return 2

    try:
        raw = open(args.input, "rb").read()
    except OSError as e:
        print("错误：读取输入文件失败：%s" % e)
        return 1

    # ★ BOM 显式处理：带 BOM 时若不剥掉，首个 # 标题会被当成普通段落、H1 直接丢失
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig", errors="replace")
    text = text.replace("\ufeff", "")

    if args.strip_header:
        # 去掉抓取器写的那种顶部引用块（> 公众号：xxx 连续若干行，直到第一个 ---）
        lines = text.split("\n")
        idx = 0
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        if idx < len(lines) and lines[idx].lstrip().startswith(">"):
            while idx < len(lines) and (lines[idx].lstrip().startswith(">") or not lines[idx].strip()):
                idx += 1
            if idx < len(lines) and _RE_HR.match(lines[idx]):
                idx += 1
            text = "\n".join(lines[idx:])

    title = ""
    for ln in text.split("\n"):
        if ln.startswith("# "):
            title = ln[2:].strip()
            break

    body, stats = md_to_html(text, THEMES[args.theme], CODE_THEMES[args.code_theme])

    if not body.strip():
        print("错误：排版结果为空 —— 输入文件里没有可识别的内容。"
              "请确认文件非空、编码为 UTF-8。")
        return 1

    if args.full_html:
        result = build_document(body, THEMES[args.theme], args.theme, title)
    else:
        result = body + "\n"

    out_path = args.output or (os.path.splitext(args.input)[0] + ".html")
    parent = os.path.dirname(os.path.abspath(out_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(result)

    print("排版完成")
    print("  输入    ：%s（%s 字节%s）"
          % (args.input, format(len(raw), ","), "，带 BOM 已自动剥除" if had_bom else ""))
    print("  输出    ：%s（%s 字节）" % (os.path.abspath(out_path), format(len(result.encode('utf-8')), ",")))
    print("  页面主题：%s（%s）" % (args.theme, THEMES[args.theme]["label"]))
    print("  代码主题：%s（%s）" % (args.code_theme, CODE_THEMES[args.code_theme]["label"]))
    print("  统计    ：H1 %d 个 / H2 及以下 %d 个 / 代码块 %d 个 / 表格 %d 个 / 列表 %d 个 / 引用 %d 处"
          % (stats["h1"], stats["h2"], stats["code"], stats["table"], stats["list"], stats["quote"]))
    if not stats["h1"]:
        print("  提示    ：没识别到 H1 标题。若原文首行就是标题，请检查该行是否是 '# 标题' 格式。")

    # ★ 模块接缝：排版只做 Markdown -> HTML，**不判断这份 Markdown 是不是"文章"**。
    #   「改了什么 / 修改说明」这类过程性内容会照原样排进 HTML，一路跟到草稿箱（踩过）。
    #   这里给一个提醒；**硬拦在 publish.py**（发布前那道闸门才是必须过的）。
    warn = _process_heading_warning(text)
    if warn:
        print("")
        for line in warn:
            print(line)
        print("  （这条只是提醒。发布时 publish.py 会**直接拦下**，不会让它进草稿箱。）")
        print("  一条命令挪走：python scripts/guard.py \"%s\" --fix" % args.input)
    return 0


def _process_heading_warning(text):
    """排版时提醒：正文里混进了过程性标题。判据与发布前闸门同一份（guard.py）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        if here not in sys.path:
            sys.path.insert(0, here)
        import guard
    except ImportError:
        return []
    hits = guard.find_process_sections(text)
    if not hits:
        return []
    out = ["★ 提醒：这份 Markdown 里有 %d 处过程性标题（不是文章内容）：" % len(hits)]
    for n, lv, raw in hits[:5]:
        out.append("    · 第 %d 行（%s）：%s" % (n, lv, raw[:70]))
    if len(hits) > 5:
        out.append("    · …还有 %d 处" % (len(hits) - 5))
    return out


if __name__ == "__main__":
    sys.exit(main())
