#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号文章抓取器（原创，零第三方依赖）
================================================
把一个微信公众号文章链接，抓成一份「可长期保存、可离线阅读」的本地资料：

    output/<文章标题>/
        <文章标题>.md        —— 正文 Markdown（图片已换成本地相对路径）
        images/01.png ...     —— 从微信服务器下载下来的原图
        文章信息.md / .csv / meta.json

同时在 output/ 下维护两张总表（可累加、可重复跑，不会重复记账）：
    output/文章信息表.csv    —— Excel 可直接打开（UTF-8 BOM）
    output/文章信息表.md     —— Markdown 表格，讲课/展示用

只依赖 Python 标准库（urllib + html.parser + csv + re），
不需要 pip install 任何东西，Python 3.8+ 均可运行。

用法：
    python wechat_spider.py "https://mp.weixin.qq.com/s/xxxxxxxx"
    python wechat_spider.py --urls-file urls.txt --delay 5
    python wechat_spider.py "<url>" --no-images

只支持微信公众号文章链接（mp.weixin.qq.com）；其他域名会在抓取前直接报错，
避免抓回一堆无关页面。确需跳过该校验时加 --allow-any-url。
"""

import argparse
import csv
import gzip
import html as htmllib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime
from html.parser import HTMLParser

# 反引号在源码里用变量表示，避免被外层工具链吃掉
BQ = chr(96)
FENCE = BQ * 3

VERSION = "1.0.0"

# ---------------------------------------------------------------- 请求头
# 关键：微信对没有「正常浏览器特征」的请求会返回验证页
# （页面文案：「环境异常，完成验证后即可继续访问」）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PAGE_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://mp.weixin.qq.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Encoding": "gzip, deflate",
    "Connection": "close",
}

# 图片防盗链：下载图片时同样必须带 Referer，否则 403
# ★ 这里**故意不要 webp**（2026-10-06 实测的坑）：
#   原来写的是 image/webp，微信 CDN 就按声明把原图转成 webp 回给我们 ——
#   抓下来的是 09.webp，而公众号发正文图的接口（media/uploadimg）**只收 jpg/png**，
#   于是这张图在发布那一步传不上去。抓取端少要一种格式，发布端就少一类失败。
#   （发布端 publish.py 也做了兜底：万一还是拿到 webp/gif，就回退用原图的微信地址。）
IMAGE_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://mp.weixin.qq.com/",
    "Accept": "image/jpeg,image/png,image/gif,image/*;q=0.8,*/*;q=0.5",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "close",
}

# 微信"环境异常"验证页的识别特征
BLOCK_MARKERS = ("环境异常", "完成验证后即可继续访问", "去验证", "该内容已被发布者删除",
                 "此内容因违规无法查看", "参数错误", "此内容发送失败无法查看")


def log(msg):
    """带时间戳的中文进度输出。"""
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------- 网络层
def http_get(url, headers, timeout=30, retries=3, expect_binary=False):
    """带重试的 GET。返回 (bytes, 响应头, 最终URL)。"""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                if "gzip" in enc:
                    raw = gzip.decompress(raw)
                elif "deflate" in enc:
                    try:
                        raw = zlib.decompress(raw)
                    except zlib.error:
                        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                return raw, resp.headers, resp.geturl()
        except urllib.error.HTTPError as e:
            last_err = "HTTP %s %s" % (e.code, e.reason)
            # 4xx（除 429）重试没意义
            if 400 <= e.code < 500 and e.code != 429:
                break
        except Exception as e:                       # 网络抖动 / 超时
            last_err = "%s: %s" % (type(e).__name__, e)
        if attempt < retries:
            time.sleep(1.5 * attempt)
    raise RuntimeError("请求失败（重试 %d 次）：%s <- %s" % (retries, last_err, url))


def decode_html(raw, headers):
    """按响应头/页面 meta 判断编码，微信基本都是 utf-8。"""
    ctype = (headers.get("Content-Type") or "").lower()
    m = re.search(r"charset=([\w-]+)", ctype)
    if m:
        try:
            return raw.decode(m.group(1), errors="replace")
        except LookupError:
            pass
    head = raw[:2048].decode("ascii", errors="ignore")
    m = re.search(r'charset=["\']?([\w-]+)', head, re.I)
    if m:
        try:
            return raw.decode(m.group(1), errors="replace")
        except LookupError:
            pass
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 元信息抽取
def _text_of(html, tag_id):
    """取 id=xxx 元素的内部文本（去标签）。"""
    m = re.search(r'<[a-zA-Z][^>]*\bid=["\']%s["\'][^>]*>(.*?)</' % re.escape(tag_id), html, re.S)
    if not m:
        return ""
    return clean_text(strip_tags(m.group(1)))


def strip_tags(s):
    """粗暴去标签，仅用于短片段（标题、作者名）。"""
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return htmllib.unescape(s)


def clean_text(s):
    """归一化空白，去掉零宽字符。"""
    s = (s or "").replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", s).strip()


def _js_var(html, name):
    """取 var name = "xxx"; 的值。"""
    m = re.search(r"var\s+%s\s*=\s*[\"'](.*?)[\"']" % re.escape(name), html)
    return htmllib.unescape(m.group(1)).strip() if m else ""


def _meta(html, key, attr="property"):
    m = re.search(r'<meta[^>]+%s=["\']%s["\'][^>]*content=["\'](.*?)["\']' % (attr, re.escape(key)),
                  html, re.I | re.S)
    if not m:
        m = re.search(r'<meta[^>]+content=["\'](.*?)["\'][^>]*%s=["\']%s["\']' % (attr, re.escape(key)),
                      html, re.I | re.S)
    return clean_text(htmllib.unescape(m.group(1))) if m else ""


def extract_meta(html, url):
    """从页面里抽标题 / 公众号 / 作者 / 发布时间。

    每一项都做多路兜底，任何一路成功即用；全失败返回空串，
    最终在报告里如实标注「未获取」。
    """
    meta = {}

    # ---- 标题 ----
    # 实测 DOM：<h1 class="rich_media_title " id="activity-name">
    #              <span class="js_title_inner">标题</span></h1>
    # 注意 class 后面有一个空格，所以 class="rich_media_title" 这种精确正则匹配不到，
    # 最稳的是认 id="activity-name" 或里面的 span.js_title_inner。
    title = ""
    m = re.search(r'<h1[^>]*id=["\']activity-name["\'][^>]*>(.*?)</h1>', html, re.S)
    if m:
        inner = re.search(r'<span[^>]*class=["\'][^"\']*js_title_inner[^"\']*["\'][^>]*>(.*?)</span>',
                          m.group(1), re.S)
        title = clean_text(strip_tags(inner.group(1) if inner else m.group(1)))
    if not title:
        title = _meta(html, "og:title")
    if not title:
        title = _js_var(html, "msg_title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S)
        if m:
            title = clean_text(strip_tags(m.group(1)))
            title = re.sub(r"\s*[-|_]\s*(微信公众平台|微信公众号)\s*$", "", title)
    meta["标题"] = title

    # ---- 公众号名 ----
    account = _text_of(html, "js_name")
    if not account:
        m = re.search(r'var\s+nickname\s*=\s*htmlDecode\(\s*["\'](.*?)["\']\s*\)', html)
        if m:
            account = clean_text(m.group(1))
    if not account:
        account = _meta(html, "og:article:author")
    meta["公众号"] = account

    # ---- 作者（很多号不单独署名，属正常缺失）----
    author = _meta(html, "author", attr="name")
    if not author:
        author = _text_of(html, "js_author_name_text")
    if not author:
        author = _text_of(html, "js_author_name")     # 这个元素常为空，兜底而已
    meta["作者"] = author

    # ---- 发布时间 ----
    # 页面上的 <em id="publish_time"> 是空的，时间由 JS 填，
    # 所以必须从脚本变量里取。优先级：createTime(已格式化) > ct / create_time(时间戳)
    pub = ""
    m = re.search(r"var\s+createTime\s*=\s*[\"']([\d\-: ]+)[\"']", html)
    if m:
        pub = m.group(1).strip()
    if not pub:
        for var in ("ct", "create_time", "ori_create_time", "createTimestamp"):
            v = _js_var(html, var)
            if v.isdigit() and len(v) == 10:
                try:
                    pub = datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M")
                    break
                except (OSError, ValueError, OverflowError):
                    pass
    if not pub:
        m = re.search(r"ori_create_time:\s*[\"']?(\d{10})", html)
        if m:
            try:
                pub = datetime.fromtimestamp(int(m.group(1))).strftime("%Y-%m-%d %H:%M")
            except (OSError, ValueError, OverflowError):
                pub = ""
    meta["发布时间"] = pub

    # ---- 封面图（正文里没有，只有页面 meta 里有）----
    meta["封面图"] = _meta(html, "og:image") or _js_var(html, "msg_cdn_url")

    # ---- 原文链接（优先页面自报的 canonical，否则用输入链接）----
    canonical = _meta(html, "og:url")
    meta["原文链接"] = canonical or url
    meta["_输入链接"] = url
    return meta


# ---------------------------------------------------------------- 正文切片
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
             "meta", "param", "source", "track", "wbr"}

# 浏览器会把 <p> 隐式闭合，微信正文里偶有未闭合标签，这里手动模拟
AUTO_CLOSE = {
    "p": {"p", "div", "section", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol",
          "li", "blockquote", "table", "pre", "figure", "hr", "article", "aside"},
    "li": {"li", "p", "div", "section", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"},
    "td": {"td", "th", "tr"},
    "th": {"td", "th", "tr"},
    "tr": {"tr"},
    "option": {"option"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
}


def slice_element_html(html, element_id):
    """取 <div id="js_content"> 的**完整内部 HTML**。

    这里是本脚本最关键的一处：正文里嵌套了大量 <div>/<section>，
    如果用 'id="js_content" 一直取到文末' 的朴素正则，
    会把页面后面 290 万字符的脚本全都吞进来（实测正是这个坑）。
    正确做法是按 <div> 开合配对，找到真正闭合的那一个。
    """
    m = re.search(r"<div\b[^>]*\bid=[\"']%s[\"'][^>]*>" % re.escape(element_id), html, re.I)
    if not m:
        return ""
    start = m.end()
    # 自闭合的 <div ... /> 直接返回空
    if m.group(0).rstrip().endswith("/>"):
        return ""
    depth = 1
    pos = start
    tag_re = re.compile(r"<(/?)div\b", re.I)
    while True:
        t = tag_re.search(html, pos)
        if not t:
            return html[start:]          # 页面结构异常，退化为取到文末
        if t.group(1):
            depth -= 1
            if depth == 0:
                return html[start:t.start()]
        else:
            if not html[t.end():t.end() + 2].lstrip().startswith("/>"):
                depth += 1
        pos = t.end()


# ---------------------------------------------------------------- 富媒体占位
# 静态页面里抓不到的富媒体类型：如实标注，绝不假装抓到
RICHTEXT_NOTES = {
    "mpvoice": "🎧 此处为公众号语音（静态页面不含音频地址，需在微信内播放）",
    "mpvideo": "🎬 此处为视频（静态页面不含视频地址，需在微信内播放）",
    "iframe": "🖼 此处为内嵌网页/视频框架（静态页面不含内容）",
    "qqmusic": "🎵 此处为 QQ 音乐（静态页面不含音频地址）",
    "mpminiprogram": "🔗 此处为小程序卡片（静态页面不含跳转内容）",
    "mp-common-videosnap": "🎬 此处为视频号动态（静态页面不含视频地址）",
    "mp-common-mpaudio": "🎧 此处为音频（静态页面不含音频地址）",
    "mpcpc": "🔗 此处为小程序卡片（静态页面不含跳转内容）",
    "wx-open-subscribe": "🔔 此处为订阅组件（静态页面不含内容）",
}

BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "main", "aside",
              "figure", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol",
              "li", "blockquote", "pre", "table", "tr", "hr", "dl", "dt", "dd"}
# 这些标签内部的换行/段落要合并，避免把一个列表项拆成多块
SUPPRESS_FLUSH = {"li", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th",
                  "blockquote", "figcaption"}


# ---------------------------------------------------------------- 图片下载器
class ImageDownloader:
    """把正文里的图片下载到本地，返回 Markdown 用的相对路径。"""

    EXT_MAP = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "gif": "gif",
               "webp": "webp", "bmp": "bmp", "svg": "svg", "other": "jpg", "": "jpg"}

    def __init__(self, images_dir, rel_prefix, enabled=True, delay=0.6, timeout=30):
        self.images_dir = images_dir
        self.rel_prefix = rel_prefix
        self.enabled = enabled
        self.delay = delay
        self.timeout = timeout
        self.counter = 0
        self.cache = {}          # url -> 本地相对路径
        self.records = []        # 成功清单
        self.failures = []       # 失败清单

    @staticmethod
    def clean_image_url(url):
        """把「逼着 CDN 转码」的查询参数去掉。

        ★ 2026-10-06 实测：微信图片地址里带 tp=webp 时，**不管 Accept 怎么写**，
          CDN 都会把原图转成 webp 回给你（同一个地址实测：带 tp=webp = 26852 字节的 WEBP；
          去掉 = 42526 字节的 JPEG）。而公众号发正文图的接口只收 jpg/png ——
          这张图会一路走到发布那一步才失败。所以在这里就把参数摘掉。
        """
        if not url:
            return url
        parts = urllib.parse.urlsplit(url)
        if not parts.query:
            return url
        pairs = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                 if k.lower() in ("tp", "wx_fmt") and v.lower() == "webp"]
        kept = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                if (k, v) not in pairs]
        # tp=webp 整条去掉；wx_fmt=webp 改成不带该参数（CDN 会回原始格式）
        kept = [(k, v) for k, v in kept if not (k.lower() == "tp" and v.lower() == "webp")]
        query = urllib.parse.urlencode(kept)
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))

    def _guess_ext(self, url, declared=""):
        fmt = ""
        q = urllib.parse.urlparse(url).query
        m = re.search(r"wx_fmt=([a-zA-Z]+)", q)
        if m:
            fmt = m.group(1).lower()
        if not fmt:
            fmt = (declared or "").lower()
        if not fmt:
            fmt = pathlib.PurePosixPath(urllib.parse.urlparse(url).path).suffix.lstrip(".").lower()
        return self.EXT_MAP.get(fmt, "jpg")

    def localize(self, raw_url, declared_type=""):
        """下载一张图，返回 (markdown里用的路径, 是否本地成功)。"""
        if not raw_url:
            return "", False
        url = htmllib.unescape(raw_url.strip())
        if url.startswith("//"):
            url = "https:" + url
        url = self.clean_image_url(url)      # ★ 别让 CDN 把原图转成 webp（见 clean_image_url）
        if url in self.cache:
            return self.cache[url], True
        if not self.enabled:
            return url, False

        self.counter += 1
        idx = self.counter
        ext = self._guess_ext(url, declared_type)
        name = "%02d.%s" % (idx, ext) if idx < 100 else "%03d.%s" % (idx, ext)
        dest = self.images_dir / name
        rel = "%s/%s" % (self.rel_prefix, name)
        try:
            data, headers, _ = http_get(url, IMAGE_HEADERS, timeout=self.timeout, retries=3)
            if len(data) < 128:
                raise RuntimeError("返回内容过小（%d 字节），可能被防盗链拦截" % len(data))
            # 用真实 Content-Type 修正扩展名
            ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype.startswith("image/"):
                real = self.EXT_MAP.get(ctype.split("/")[1], ext)
                if real != ext and real != "jpg":
                    ext = real
                    name = "%02d.%s" % (idx, ext) if idx < 100 else "%03d.%s" % (idx, ext)
                    dest = self.images_dir / name
                    rel = "%s/%s" % (self.rel_prefix, name)
            dest.write_bytes(data)
            self.records.append({"序号": idx, "文件名": name, "字节数": len(data),
                                 "类型": ctype or ext, "来源URL": url})
            self.cache[url] = rel
            log("    图片 %s 已保存（%.1f KB）" % (name, len(data) / 1024.0))
            if self.delay:
                time.sleep(self.delay)
            return rel, True
        except Exception as e:
            self.counter -= 1
            self.failures.append({"序号": idx, "来源URL": url, "原因": str(e)})
            log("    !! 图片下载失败（保留原链接）：%s" % e)
            return url, False


# ---------------------------------------------------------------- HTML -> Markdown
class MarkdownConverter(HTMLParser):
    """轻量 HTML -> Markdown。

    设计要点：
    1. 只处理公众号正文里真实出现的标签，不追求完整 HTML 规范；
    2. 空白用「待定空格」策略处理 —— 遇到纯空白数据只记一个标记，
       真正有字时才补一个空格。这样中文段落不会被编辑器缩进搞出一堆空格；
    3. <br> 在缓冲区里写成换行，落地时再按行分配前缀。
    """

    def __init__(self, image_resolver=None):
        super().__init__(convert_charrefs=True)
        self.resolver = image_resolver
        self.out = []                 # 已完成块
        self.buf = []                 # 当前行内缓冲
        self.pending_space = False
        self.no_space_after = False   # 刚输出的是「开标记」，紧接着的空白要丢掉
        self.stack = []
        self.list_stack = []          # [{"ordered":bool,"n":int}]
        self.bq_depth = 0
        self.skip_until = None        # 正在跳过的富媒体标签
        self.skip_depth = 0           # script/style 深度
        self.pre_depth = 0
        self.plain_parts = []         # 纯文本（统计字数用）
        self.rich_notes = []          # 记录遇到的富媒体类型

    # ---------- 内部工具 ----------
    def _suppressed(self):
        return any(t in SUPPRESS_FLUSH for t in self.stack)

    def _add(self, text):
        """写入一段文本，空白按「至多一个空格」规则归一化。

        微信的 &nbsp; 常常和正文粘在同一个文本节点里（convert_charrefs
        会把 '' + '正文' 合成一块），所以这里必须把首尾空白拆出来单独判断，
        不能只判断「整段是不是空白」。
        """
        if self.skip_depth or self.skip_until:
            return
        if not text:
            return
        core = text.strip()               # \xa0 也算空白，会被一起去掉
        if not core:
            if self.buf:
                self.pending_space = True
            return
        lead = text[:len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        if (lead and self.buf and not self.no_space_after
                and not self.buf[-1].endswith("\n")):
            self.buf.append(" ")
        self.pending_space = False
        self.no_space_after = False
        self.buf.append(core)
        if trail and not trail.endswith("\n"):
            self.pending_space = True

    def _marker(self, text, opening=True):
        """行内标记（** * [ ] 反引号 等）。

        微信正文常写成 <strong>&nbsp;正文</strong>，那个 &nbsp; 会让
        Markdown 变成 "** 正文**"，加粗直接失效。所以开标记之后要吃掉
        紧随的空白；闭标记之前也要把残留空白丢掉。
        """
        if self.skip_depth or self.skip_until:
            return
        self.buf.append(text)
        self.pending_space = False
        self.no_space_after = opening

    def _flush(self, force=False):
        if (self._suppressed() or self.skip_depth or self.skip_until) and not force:
            return
        raw = "".join(self.buf)
        self.buf = []
        self.pending_space = False
        self.no_space_after = False
        if not raw:
            return
        raw = raw.replace("\u00a0", " ")
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.split("\n")]
        lines = [ln for ln in lines if ln]
        if not lines:
            return
        prefix = "> " * self.bq_depth
        if self.list_stack:
            top = self.list_stack[-1]
            for i, ln in enumerate(lines):
                if i == 0:
                    bullet = ("%d. " % top["n"]) if top["ordered"] else "- "
                    if top["ordered"]:
                        top["n"] += 1
                    self.out.append(prefix + bullet + ln)
                else:
                    self.out.append(prefix + "  " + ln)
        else:
            for ln in lines:
                self.out.append(prefix + ln)

    def _blank(self):
        if self.out and self.out[-1] != "":
            self.out.append("")

    def _note(self, text):
        """插入一条富媒体占位说明（独立成块）。"""
        self._flush(force=True)
        self._blank()
        self.out.append("> " + text)
        self._blank()

    # ---------- HTMLParser 回调 ----------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}

        if tag in ("script", "style"):
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if self.skip_until:
            return
        if tag in RICHTEXT_NOTES:
            extra = ""
            if tag == "mp-common-profile":
                extra = ""
            self.rich_notes.append(tag)
            self._note(RICHTEXT_NOTES[tag])
            self.skip_until = tag
            return
        if tag == "mp-common-profile":
            nick = a.get("data-nickname", "")
            alias = a.get("data-alias", "")
            tip = "📇 此处为公众号名片"
            if nick:
                tip += "：" + nick
            if alias:
                tip += "（微信号 %s）" % alias
            tip += " —— 静态页面不含其内容"
            self.rich_notes.append(tag)
            self._note(tip)
            self.skip_until = tag
            return

        # 隐式闭合
        while self.stack and self.stack[-1] in AUTO_CLOSE and tag in AUTO_CLOSE[self.stack[-1]]:
            self.handle_endtag(self.stack[-1])
            if not self.stack:
                break

        if tag in VOID_TAGS:
            self.handle_startendtag(tag, attrs)
            return

        self.stack.append(tag)

        if tag in BLOCK_TAGS and not self._suppressed():
            self._flush()
        if tag == "br":
            pass
        elif tag in ("strong", "b"):
            self._marker("**")
        elif tag in ("em", "i"):
            self._marker("*")
        elif tag == "u":
            self._marker("<u>")
        elif tag == "code" and self.pre_depth == 0:
            self._marker(BQ)
        elif tag == "pre":
            self.pre_depth += 1
            self._flush()
            self._blank()
            self.out.append(FENCE)
        elif tag == "blockquote":
            self._flush()
            self.bq_depth += 1
        elif tag in ("ul", "ol"):
            self._flush()
            self.list_stack.append({"ordered": tag == "ol", "n": 1})
        elif tag == "li":
            self._flush()
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush()
            self._blank()
            self._marker("#" * int(tag[1]) + " ")
        elif tag == "hr":
            self._flush()
            self._blank()
            self.out.append("---")
            self._blank()
        elif tag == "a":
            href = a.get("href", "")
            self._marker("[")
            self._link_stack_push(href)
        elif tag in ("td", "th"):
            self._flush()
        elif tag == "tr":
            self._flush()

    def _link_stack_push(self, href):
        if not hasattr(self, "_links"):
            self._links = []
        self._links.append(href)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if tag == "br":
            if not self.skip_depth and not self.skip_until:
                self.buf.append("\n")
                self.pending_space = False
            return
        if tag == "hr":
            if not self.skip_depth and not self.skip_until:
                self._flush()
                self._blank()
                self.out.append("---")
                self._blank()
            return
        if tag == "img":
            self._emit_image(a)
            return
        if tag in RICHTEXT_NOTES:
            self.rich_notes.append(tag)
            self._note(RICHTEXT_NOTES[tag])
            return
        # 其它自闭合标签按普通开始标签处理（少数编辑器会写 <span />）
        if tag not in VOID_TAGS:
            self.handle_starttag(tag, attrs)

    def _emit_image(self, a):
        if self.skip_depth or self.skip_until:
            return
        # 微信正文图真实地址在 data-src（src 常是占位/懒加载图）
        url = a.get("data-src") or a.get("src") or ""
        declared = a.get("data-type", "")
        if not url:
            return
        alt = a.get("alt", "") or "图片"
        if self.resolver:
            path, ok = self.resolver(url, declared)
        else:
            path, ok = url, False
        self._flush()
        self._blank()
        self.out.append("![%s](%s)" % (alt, path))
        self._blank()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style"):
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if self.skip_until:
            if tag == self.skip_until:
                self.skip_until = None
            return
        if tag not in self.stack:
            return
        while self.stack and self.stack[-1] != tag:
            self.handle_endtag(self.stack[-1])
        if self.stack:
            self.stack.pop()

        if tag == "pre":
            self._flush(force=True)
            self.pre_depth = max(0, self.pre_depth - 1)
            self._blank()
            self.out.append(FENCE)
            self._blank()
        elif tag == "blockquote":
            self._flush(force=True)
            self.bq_depth = max(0, self.bq_depth - 1)
            self._blank()
        elif tag in ("ul", "ol"):
            self._flush(force=True)
            if self.list_stack:
                self.list_stack.pop()
            self._blank()
        elif tag == "li":
            self._flush(force=True)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush(force=True)
            self._blank()
        elif tag in ("td", "th"):
            self._flush(force=True)
            self._add(" | ")
        elif tag == "tr":
            self._flush(force=True)
        elif tag in BLOCK_TAGS:
            self._flush()
            if tag in ("p", "div", "section", "figure", "table", "article"):
                self._blank()
        elif tag in ("strong", "b"):
            self._marker("**", opening=False)
        elif tag in ("em", "i"):
            self._marker("*", opening=False)
        elif tag == "u":
            self._marker("</u>", opening=False)
        elif tag == "code" and self.pre_depth == 0:
            self._marker(BQ, opening=False)
        elif tag == "a":
            href = ""
            if getattr(self, "_links", None):
                href = self._links.pop()
            if href:
                self._marker("](%s)" % href, opening=False)
            else:
                self._marker("]", opening=False)

    def handle_data(self, data):
        if self.skip_depth or self.skip_until:
            return
        if self.pre_depth:
            self.buf.append(data)
            self.plain_parts.append(data)
            return
        self._add(data)
        if data.strip():
            self.plain_parts.append(data)

    # ---------- 收尾 ----------
    def result(self):
        self._flush(force=True)
        lines = []
        for ln in self.out:
            if ln == "" and (not lines or lines[-1] == ""):
                continue
            lines.append(ln.rstrip())
        text = "\n".join(lines).strip() + "\n"
        text = re.sub(r"\n{3,}", "\n\n", text)
        plain = clean_text(" ".join(self.plain_parts)).replace(" ", "")
        return text, plain


# ---------------------------------------------------------------- 文件名处理
BAD_FS = re.compile(r'[\\/:*?"<>|\r\n\t]')


def safe_name(title, fallback="未命名文章", limit=60):
    """把标题变成合法文件夹/文件名。"""
    s = clean_text(title) or fallback
    s = BAD_FS.sub("_", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) > limit:
        s = s[:limit].rstrip(" .")
    return s or fallback


def short_path_from_url(url):
    """从 /s/xxxx 里取短标识，用作兜底命名。"""
    m = re.search(r"/s/([A-Za-z0-9_\-]+)", url)
    if m:
        return m.group(1)[:20]
    return "article"


# ---------------------------------------------------------------- 主流程
def fetch_article(url, timeout=30):
    """抓取并解析一篇文章，返回 (meta, markdown, plain, downloader)。"""
    log("抓取页面：%s" % url)
    raw, headers, final_url = http_get(url, PAGE_HEADERS, timeout=timeout, retries=3)
    html = decode_html(raw, headers)
    log("  页面大小：%s 字节 / %s 字符" % (format(len(raw), ","), format(len(html), ",")))

    # 拦截微信的验证页 / 错误页
    if len(html) < 20000:
        for marker in BLOCK_MARKERS:
            if marker in html:
                raise RuntimeError("微信返回了拦截/错误页（命中关键词「%s」）。"
                                   "通常是请求头不完整或访问过于频繁。" % marker)

    meta = extract_meta(html, final_url)
    log("  标题：%s" % (meta["标题"] or "（未获取）"))
    log("  公众号：%s" % (meta["公众号"] or "（未获取）"))
    log("  作者：%s" % (meta["作者"] or "（未获取，该号可能未单独署名）"))
    log("  发布时间：%s" % (meta["发布时间"] or "（未获取）"))

    content_html = slice_element_html(html, "js_content")
    if not content_html:
        raise RuntimeError("页面里没有找到 id=js_content 的正文容器，"
                           "可能是分享卡片/被删文章/需要登录的内容。")
    log("  正文 HTML：%s 字符（已按 div 嵌套正确截断）" % format(len(content_html), ","))
    return meta, content_html, html


def build_article(url, outdir, want_images=True, delay=0.6, timeout=30):
    """完整处理一篇文章：抓取 -> 转 Markdown -> 下图 -> 落盘。返回统计 dict。"""
    meta, content_html, full_html = fetch_article(url, timeout=timeout)

    folder = safe_name(meta["标题"], fallback="文章_" + short_path_from_url(url))
    art_dir = outdir / folder
    img_dir = art_dir / "images"
    art_dir.mkdir(parents=True, exist_ok=True)
    if want_images:
        img_dir.mkdir(parents=True, exist_ok=True)

    dl = ImageDownloader(img_dir, "images", enabled=want_images,
                         delay=delay, timeout=timeout)
    conv = MarkdownConverter(image_resolver=dl.localize)
    conv.feed(content_html)
    conv.close()
    body_md, plain = conv.result()

    if not body_md.strip():
        log("  !! 正文解析为空，请在报告里如实标注")

    # ---- 封面图：单独存成 cover.xxx，不占正文图片编号 ----
    cover_name = ""
    if want_images and meta.get("封面图"):
        try:
            data, hdrs, _ = http_get(meta["封面图"], IMAGE_HEADERS, timeout=timeout, retries=2)
            if len(data) < 128:
                raise RuntimeError("返回内容过小，可能被防盗链拦截")
            ctype = (hdrs.get("Content-Type") or "").split(";")[0].strip().lower()
            ext = ImageDownloader.EXT_MAP.get(ctype.split("/")[-1], "jpg")
            cover_name = "cover.%s" % ext
            (art_dir / cover_name).write_bytes(data)
            log("  封面图已保存：%s（%.1f KB）" % (cover_name, len(data) / 1024.0))
        except Exception as e:
            log("  !! 封面图下载失败（表格里记原始链接）：%s" % e)

    # ---- 组装 Markdown 文件 ----
    head = []
    head.append("# %s" % (meta["标题"] or "（标题未获取）"))
    head.append("")
    head.append("> 公众号：%s  " % (meta["公众号"] or "未获取"))
    head.append("> 作者：%s  " % (meta["作者"] or "未获取"))
    head.append("> 发布时间：%s  " % (meta["发布时间"] or "未获取"))
    head.append("> 原文链接：%s  " % meta["原文链接"])
    head.append("> 抓取时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    head.append("")
    head.append("---")
    head.append("")
    md_text = "\n".join(head) + body_md
    md_path = art_dir / ("%s.md" % folder)
    md_path.write_text(md_text, encoding="utf-8")
    log("  Markdown 已写出：%s（%s 字符）" % (md_path.name, format(len(md_text), ",")))

    img_ok = len(dl.records)
    stats = {
        "标题": meta["标题"],
        "公众号": meta["公众号"],
        "作者": meta["作者"],
        "发布时间": meta["发布时间"],
        "正文字数": len(plain),
        "图片数": img_ok,
        "封面图": cover_name or meta.get("封面图", ""),
        "原文链接": meta["原文链接"],
        "抓取时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "_目录": folder,
        "_正文HTML字符数": len(content_html),
        "_Markdown字符数": len(md_text),
        "_图片失败数": len(dl.failures),
        "_富媒体占位": sorted(set(conv.rich_notes)),
    }

    # ---- meta.json ----
    (art_dir / "meta.json").write_text(
        json.dumps({"信息表": stats, "图片清单": dl.records, "图片失败": dl.failures,
                    "未抓取到的富媒体": stats["_富媒体占位"]},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 单篇信息表（md + csv）----
    write_info_files(art_dir, stats, dl)
    # ---- 汇总总表 ----
    append_index(outdir, stats)
    return stats, dl


INFO_FIELDS = ["标题", "公众号", "作者", "发布时间", "正文字数", "图片数", "封面图",
               "原文链接", "抓取时间"]


def write_info_files(art_dir, stats, dl):
    """在文章目录里写一份「文章信息」表（Markdown 表格 + CSV）。"""
    lines = ["# 文章信息表", "",
             "| 字段 | 内容 |", "| --- | --- |"]
    for k in INFO_FIELDS:
        v = str(stats.get(k, "") or "未获取")
        v = v.replace("|", "\\|")
        lines.append("| %s | %s |" % (k, v))
    lines.append("| 图片下载成功 | %d 张 |" % stats["图片数"])
    if stats["_图片失败数"]:
        lines.append("| 图片下载失败 | %d 张（Markdown 中保留原始链接） |" % stats["_图片失败数"])
    lines.append("")
    if dl.records:
        lines += ["## 图片清单", "", "| 序号 | 文件名 | 大小 | 类型 |", "| --- | --- | --- | --- |"]
        for r in dl.records:
            lines.append("| %d | %s | %.1f KB | %s |"
                         % (r["序号"], r["文件名"], r["字节数"] / 1024.0, r["类型"]))
        lines.append("")
    if dl.failures:
        lines += ["## 图片下载失败", ""]
        for f in dl.failures:
            lines.append("- 第 %d 张：%s（%s）" % (f["序号"], f["原因"], f["来源URL"][:90]))
        lines.append("")
    (art_dir / "文章信息.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    with open(art_dir / "文章信息.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(INFO_FIELDS)
        w.writerow([stats.get(k, "") for k in INFO_FIELDS])
        w.writerow([])
        w.writerow(["序号", "文件名", "字节数", "类型", "来源URL"])
        for r in dl.records:
            w.writerow([r["序号"], r["文件名"], r["字节数"], r["类型"], r["来源URL"]])
        for f in dl.failures:
            w.writerow(["失败", "", "", f["原因"], f["来源URL"]])


def append_index(outdir, stats):
    """维护 output/文章信息表.csv 与 .md —— 同一链接重复跑会覆盖，不会重复记账。"""
    csv_path = outdir / "文章信息表.csv"
    rows = {}
    if csv_path.exists():
        try:
            with open(csv_path, "r", newline="", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if r.get("原文链接"):
                        rows[r["原文链接"]] = r
        except Exception as e:
            log("  （读取旧的总表失败，将重建：%s）" % e)
    rows[stats["原文链接"]] = {k: stats.get(k, "") for k in INFO_FIELDS}
    ordered = sorted(rows.values(), key=lambda r: r.get("抓取时间", ""), reverse=True)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=INFO_FIELDS)
        w.writeheader()
        for r in ordered:
            w.writerow(r)

    md = ["# 公众号文章抓取 · 文章信息表", "",
          "共 %d 篇，按抓取时间倒序。" % len(ordered), "",
          "| " + " | ".join(INFO_FIELDS) + " |",
          "| " + " | ".join(["---"] * len(INFO_FIELDS)) + " |"]
    for r in ordered:
        md.append("| " + " | ".join(str(r.get(k, "") or "未获取").replace("|", "\\|")
                                    for k in INFO_FIELDS) + " |")
    (outdir / "文章信息表.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def read_urls_file(path):
    urls = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


WECHAT_HOST = "weixin.qq.com"


def check_wechat_url(url):
    """确认链接是微信公众号文章链接，不是就抛错。返回原链接。"""
    raw = url if "://" in url else "https://" + url
    host = (urllib.parse.urlparse(raw).hostname or "").lower()
    if not host:
        raise RuntimeError("这不是一个链接：%s" % url)
    if host != WECHAT_HOST and not host.endswith("." + WECHAT_HOST):
        raise RuntimeError("这不是微信公众号文章链接（域名是 %s）。本脚本只支持 "
                           "mp.weixin.qq.com 的文章链接；确需抓其他域名请加 --allow-any-url。"
                           % host)
    return url


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="微信公众号文章抓取器（零第三方依赖）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python wechat_spider.py \"https://mp.weixin.qq.com/s/xxxx\"\n"
               "  python wechat_spider.py --urls-file urls.txt --delay 5\n")
    ap.add_argument("url", nargs="?", help="公众号文章链接")
    ap.add_argument("--urls-file", help="批量模式：每行一个链接（# 开头为注释）")
    ap.add_argument("-o", "--outdir", default="output", help="输出根目录（默认 ./output）")
    ap.add_argument("--no-images", action="store_true", help="只抓文字，不下载图片")
    ap.add_argument("--delay", type=float, default=3.0,
                    help="每篇文章之间的间隔秒数，批量抓建议 >= 3（默认 3）")
    ap.add_argument("--img-delay", type=float, default=0.5, help="每张图片之间的间隔秒数")
    ap.add_argument("--timeout", type=int, default=30, help="单次请求超时秒数")
    ap.add_argument("--allow-any-url", action="store_true",
                    help="跳过「必须是公众号链接」的域名校验")
    args = ap.parse_args(argv)

    urls = []
    if args.url:
        urls.append(args.url)
    if args.urls_file:
        urls.extend(read_urls_file(args.urls_file))
    if not urls:
        ap.print_help()
        print("\n错误：请给出文章链接，或用 --urls-file 指定链接清单。")
        return 2

    outdir = pathlib.Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    log("微信公众号文章抓取器 v%s | 输出目录：%s" % (VERSION, outdir))
    log("本次共 %d 个链接" % len(urls))

    ok, failed = [], []
    for i, u in enumerate(urls, 1):
        if len(urls) > 1:
            log("=== [%d/%d] ===" % (i, len(urls)))
        try:
            if not args.allow_any_url:
                check_wechat_url(u)
            stats, dl = build_article(u, outdir, want_images=not args.no_images,
                                      delay=args.img_delay, timeout=args.timeout)
            ok.append(stats)
            log("完成：%s | 正文 %d 字 | 图片 %d 张"
                % (stats["标题"] or "(无标题)", stats["正文字数"], stats["图片数"]))
        except Exception as e:
            log("!! 失败：%s -> %s" % (u, e))
            failed.append({"链接": u, "原因": str(e)})
        if i < len(urls) and args.delay:
            time.sleep(args.delay)

    print()
    log("========== 汇总 ==========")
    log("成功 %d 篇，失败 %d 篇" % (len(ok), len(failed)))
    for s in ok:
        log("  ✓ %s（%d 字 / %d 图）" % (s["标题"] or "(无标题)", s["正文字数"], s["图片数"]))
    for f in failed:
        log("  ✗ %s -> %s" % (f["链接"], f["原因"]))
    log("信息表：%s" % (outdir / "文章信息表.csv"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
