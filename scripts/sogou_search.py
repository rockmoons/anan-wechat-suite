#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
爆款文案搜索（搜狗微信）—— anan-wechat-suite 模块 1
=====================================================
走搜狗微信搜索（weixin.sogou.com/weixin），零配置、零第三方依赖。

本模块相对常见实现的关键差异：
    搜狗给的是 /link?url=... 跳转链接，**真实文章地址需要自己拼**。
    跳转页返回的是一段 JS，真地址被切成 10 来片用 url += '...' 拼起来；
    本脚本把这些碎片按顺序接回去，拿到 mp.weixin.qq.com 的真实地址，
    于是「搜到 -> 直接抓正文」这条链是通的。
    拼不出来时**如实告知这条通道走不通**，绝不假装成功。

用法：
    python scripts/sogou_search.py "豆包工作 教程"
    python scripts/sogou_search.py "AI 写作" -n 10 --resolve
    python scripts/sogou_search.py "AI 写作" --fetch 2 -o output    # 搜到后直接抓前 2 篇正文
    python scripts/sogou_search.py "AI 写作" --json results.json

退出码：
    0 = 至少拿到一条搜索结果
    1 = 搜索失败（被拦截 / 无结果 / 网络错误）
    2 = 参数错误
"""

import argparse
import gzip
import html as htmllib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime

VERSION = "1.0.0"

# 随机 UA 池：固定 UA 跑多了容易被搜狗盯上
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
]

WARMUP_URL = "https://v.sogou.com/v?ie=utf8&query=&p=40030600"
SEARCH_HOST = "weixin.sogou.com"
SEARCH_REFERER = "https://weixin.sogou.com/"

# 搜狗反爬/出错时页面里会出现的特征词
BLOCK_MARKERS = ("用户您好，您的访问过于频繁", "antispider", "请输入验证码",
                 "需要验证", "访问过于频繁", "网络不给力")


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def rand_ua():
    return random.choice(UA_POOL)


def polite_sleep(lo=0.8, hi=2.0):
    """随机延迟：固定间隔比随机间隔更容易被识别为机器人。"""
    time.sleep(random.uniform(lo, hi))


# ---------------------------------------------------------------- 网络
def http_get(url, headers=None, timeout=30, retries=3):
    """带指数退避重试的 GET，返回 (bytes, headers, final_url)。"""
    last = None
    for attempt in range(1, retries + 1):
        hdrs = dict(headers or {})
        hdrs.setdefault("User-Agent", rand_ua())
        try:
            req = urllib.request.Request(url, headers=hdrs)
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
            last = "HTTP %s %s" % (e.code, e.reason)
            if 400 <= e.code < 500 and e.code not in (408, 429):
                break
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
        if attempt < retries:
            time.sleep(1.2 * attempt + random.uniform(0, 0.6))
    raise RuntimeError("请求失败（重试 %d 次）：%s <- %s" % (retries, last, url))


def decode(raw, headers):
    ctype = (headers.get("Content-Type") or "").lower()
    m = re.search(r"charset=([\w-]+)", ctype)
    for cand in ([m.group(1)] if m else []) + ["utf-8", "gb18030"]:
        try:
            return raw.decode(cand, errors="replace")
        except LookupError:
            continue
    return raw.decode("utf-8", errors="replace")


def warm_up(timeout=30):
    """先访问 v.sogou.com 拿一个 SNUID cookie。

    这一步是免配置的关键：带着它去搜，命中反爬的概率明显更低。
    拿不到也不致命，如实告警后继续。
    """
    log("预热：请求 %s 取 cookie ..." % WARMUP_URL)
    try:
        raw, headers, _ = http_get(WARMUP_URL, {
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        }, timeout=timeout, retries=2)
    except Exception as e:
        log("!! 预热失败（将继续不带 cookie 尝试）：%s" % e)
        return ""
    sc = headers.get_all("Set-Cookie") or []
    cookie = ""
    for item in sc:
        first = item.split(";")[0].strip()
        if first and "=" in first:
            cookie = (cookie + "; " + first) if cookie else first
    if cookie:
        log("  cookie 已获取：%s" % cookie[:80])
    else:
        log("!! 预热没拿到 Set-Cookie（将不带 cookie 尝试）")
    return cookie


# ---------------------------------------------------------------- 搜索
def clean(s):
    return re.sub(r"\s+", " ", htmllib.unescape(s or "")).strip()


def strip_tags(s):
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s or "", flags=re.S | re.I)
    return clean(re.sub(r"<[^>]+>", "", s))


def parse_results(page_html):
    """从搜狗搜索结果页里解析出条目。

    结果块结构：ul.news-list > li > div.txt-box > h3 > a[href=/link?url=...]
    时间戳藏在 div.s-p > span.s2 的 <script>timeConvert('1234567890')</script> 里。
    """
    items = []
    chunks = re.split(r"<li\b", page_html)
    for chunk in chunks:
        m_link = re.search(r'<a[^>]+href="(/link\?url=[^"]+)"', chunk)
        if not m_link:
            continue
        m_h3 = re.search(r"<h3[^>]*>(.*?)</h3>", chunk, re.S)
        title = strip_tags(m_h3.group(1)) if m_h3 else ""
        m_info = re.search(r'<p[^>]*class="[^"]*txt-info[^"]*"[^>]*>(.*?)</p>', chunk, re.S)
        summary = strip_tags(m_info.group(1)) if m_info else ""
        m_acct = re.search(r'<a[^>]*class="[^"]*account[^"]*"[^>]*>(.*?)</a>', chunk, re.S)
        account = strip_tags(m_acct.group(1)) if m_acct else ""
        ts = ""
        m_ts = re.search(r"timeConvert\('(\d{9,12})'\)", chunk)
        if m_ts:
            try:
                ts = datetime.fromtimestamp(int(m_ts.group(1))).strftime("%Y-%m-%d")
            except (OSError, ValueError, OverflowError):
                ts = ""
        if not ts:
            m_ts2 = re.search(r"document\.write\(timeConvert\('(\d{9,12})'\)\)", chunk)
            if m_ts2:
                try:
                    ts = datetime.fromtimestamp(int(m_ts2.group(1))).strftime("%Y-%m-%d")
                except (OSError, ValueError, OverflowError):
                    ts = ""
        items.append({
            "标题": title,
            "摘要": summary,
            "公众号": account,
            "发布时间": ts,
            "搜狗跳转": "https://" + SEARCH_HOST + htmllib.unescape(m_link.group(1)),
            "真实链接": "",
            "解析状态": "未解析",
        })
    return items


def check_blocked(page_html):
    low = page_html
    for marker in BLOCK_MARKERS:
        if marker in low:
            return marker
    return ""


def search(keyword, page=1, cookie="", timeout=30, retries=3):
    """搜一页，返回 (items, 原始页面字符数)。失败抛 RuntimeError。"""
    q = urllib.parse.quote(keyword)
    url = "https://%s/weixin?query=%s&type=2&page=%d" % (SEARCH_HOST, q, page)
    log("搜索第 %d 页：%s" % (page, keyword))
    headers = {
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Referer": SEARCH_REFERER,
        "Connection": "close",
    }
    if cookie:
        headers["Cookie"] = cookie
    raw, hdrs, _ = http_get(url, headers, timeout=timeout, retries=retries)
    page_html = decode(raw, hdrs)
    marker = check_blocked(page_html)
    if marker:
        raise RuntimeError(
            "搜狗返回了反爬/验证页（命中特征「%s」）。这是搜狗侧的限制，不是脚本 bug。"
            "请等待几分钟到几小时再试，或换关键词、减少翻页。" % marker)
    items = parse_results(page_html)
    return items, len(page_html)


# ---------------------------------------------------------------- 真实链接解析
JS_FRAG = re.compile(r"url\s*\+=\s*'([^']*)'")


def resolve_real_url(sogou_link, keyword, cookie="", timeout=30):
    """把搜狗 /link?url=... 跳转链解析成 mp.weixin.qq.com 的真实地址。

    跳转页本体只是一段 JS，真地址被拆成若干片：
        url += 'https://mp.weix';
        url += 'in.qq.com/s?src=11&times';
        ...
    按顺序接起来（并去掉占位用的 '@'）就是真地址。

    返回 (真实链接, 说明)。解析不出来时真实链接为空串，说明里写清原因。
    """
    full = sogou_link
    if " " in full:
        # href 里有未编码的空格，直接请求会报 InvalidURL
        full = urllib.parse.quote(full, safe="/?=&%.-_:")
    headers = {
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://%s/weixin?query=%s&type=2" % (SEARCH_HOST, urllib.parse.quote(keyword)),
        "Connection": "close",
    }
    if cookie:
        headers["Cookie"] = cookie
    try:
        raw, hdrs, final = http_get(full, headers, timeout=timeout, retries=2)
    except Exception as e:
        return "", "跳转页请求失败：%s" % e
    body = decode(raw, hdrs)

    if "mp.weixin.qq.com" in final and "/link?" not in final:
        return final, "HTTP 直接跳转"

    frags = JS_FRAG.findall(body)
    if frags:
        real = "".join(frags).replace("@", "").strip()
        # 只还原 &amp;，**不能**整体 html.unescape：
        # 真地址里含 "&timestamp="，而 "&times" 恰好是 HTML 实体 ×，
        # 整体反转义会把 &timestamp 吃成 ×tamp（实测踩过这个坑）。
        real = real.replace("&amp;", "&")
        if "mp.weixin.qq.com" in real:
            return real, "JS 分片拼接（%d 片）" % len(frags)
        return "", "拼出的地址不是公众号链接：%s" % real[:120]

    if "验证" in body or "antispider" in body:
        return "", "跳转页返回了验证页（搜狗反爬）"
    return "", "跳转页里没有可拼接的 url 分片（搜狗改了跳转实现）"


# ---------------------------------------------------------------- 抓正文（链上模块 2/3）
def fetch_by_sogou_result(item, outdir, mode="fast", timeout=30):
    """拿到真实链接后，交给第一课的抓取引擎把正文抓下来。"""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import wechat_spider as spider           # 复用第一课引擎，不重写

    url = item.get("真实链接") or ""
    if not url:
        return None, "没有真实链接，无法抓取"
    try:
        stats, _dl = spider.build_article(url, __import__("pathlib").Path(outdir),
                                          want_images=True, delay=0.5, timeout=timeout)
        return stats, ""
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------- 输出
def print_table(items, show_resolve):
    if not items:
        print("（没有解析到任何搜索结果）")
        return
    for i, it in enumerate(items, 1):
        print("%2d. %s" % (i, it["标题"] or "（无标题）"))
        meta = []
        if it["公众号"]:
            meta.append("公众号：" + it["公众号"])
        if it["发布时间"]:
            meta.append("发布：" + it["发布时间"])
        if meta:
            print("    " + " | ".join(meta))
        if it["摘要"]:
            s = it["摘要"]
            print("    摘要：" + (s[:110] + "…" if len(s) > 110 else s))
        if show_resolve:
            if it["真实链接"]:
                print("    真实链接：[%s] %s" % (it["解析状态"], it["真实链接"]))
            else:
                print("    真实链接：✗ 未拿到（%s）" % it["解析状态"])
        print("    搜狗跳转：" + it["搜狗跳转"][:100] + ("…" if len(it["搜狗跳转"]) > 100 else ""))
        print()


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="爆款文案搜索（搜狗微信，零配置零依赖）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python scripts/sogou_search.py \"豆包工作 教程\"\n"
               "  python scripts/sogou_search.py \"AI 写作\" -n 10 --resolve\n"
               "  python scripts/sogou_search.py \"AI 写作\" --fetch 2 -o output\n")
    ap.add_argument("keyword", nargs="?", help="搜索关键词")
    ap.add_argument("-n", "--num", type=int, default=10, help="取多少条结果（默认 10，即第一页）")
    ap.add_argument("--page", type=int, default=1, help="从第几页开始（默认 1）")
    ap.add_argument("--resolve", action="store_true",
                    help="解析出 mp.weixin.qq.com 真实链接（默认不解析，只给搜狗跳转链）")
    ap.add_argument("--fetch", type=int, default=0, metavar="N",
                    help="搜到后自动抓取前 N 篇正文（隐含 --resolve）")
    ap.add_argument("-o", "--outdir", default="output", help="--fetch 时的输出根目录")
    ap.add_argument("--json", dest="json_out", help="把结果写成 JSON 文件")
    ap.add_argument("--no-warmup", action="store_true", help="跳过 v.sogou.com 预热")
    ap.add_argument("--timeout", type=int, default=30, help="单次请求超时秒数")
    args = ap.parse_args(argv)

    if not args.keyword:
        ap.print_help()
        print("\n错误：请给出搜索关键词。")
        return 2
    if args.fetch:
        args.resolve = True

    cookie = "" if args.no_warmup else warm_up(timeout=args.timeout)

    # 翻页取够 num 条
    items = []
    page = args.page
    try:
        while len(items) < args.num:
            batch, chars = search(args.keyword, page=page, cookie=cookie, timeout=args.timeout)
            log("  第 %d 页解析到 %d 条（页面 %s 字符）" % (page, len(batch), format(chars, ",")))
            if not batch:
                break
            items.extend(batch)
            if len(batch) < 10:
                break
            page += 1
            if page - args.page >= 5:
                log("  已翻 5 页，停止（避免触发反爬）")
                break
            polite_sleep(1.2, 2.4)
    except Exception as e:
        log("!! 搜索失败：%s" % e)
        print()
        log("========== 结果 ==========")
        log("搜索失败，没有拿到任何结果。上面那条就是原因，请照它处理。")
        return 1

    items = items[:args.num]
    if not items:
        log("!! 搜狗返回了页面，但没有解析出任何结果条目。")
        log("   可能：关键词太冷门、被限流、或搜狗改了结果页结构。")
        log("   —— 这是实情，不要当成「搜到了」。")
        return 1

    # 解析真实链接
    if args.resolve:
        log("解析真实链接（%d 条）..." % len(items))
        resolved = 0
        for i, it in enumerate(items, 1):
            real, why = resolve_real_url(it["搜狗跳转"], args.keyword, cookie, timeout=args.timeout)
            it["真实链接"] = real
            it["解析状态"] = why
            if real:
                resolved += 1
                log("  [%d/%d] ✓ %s" % (i, len(items), real[:80]))
            else:
                log("  [%d/%d] ✗ %s" % (i, len(items), why))
            if i < len(items):
                polite_sleep(0.6, 1.6)
        print()
        if resolved == 0:
            log("!! %d 条全部解析失败 —— 这条通道（搜到 -> 直接抓正文）当前走不通。" % len(items))
            log("   搜索本身是好的，上面每条结果的标题/摘要/公众号/时间都是真实搜到的；")
            log("   但拿不到 mp.weixin.qq.com 真实地址，就无法自动抓正文。")
            log("   建议：把搜狗跳转链复制到浏览器打开，拿到地址栏里的 mp.weixin.qq.com 链接再抓。")
        else:
            log("真实链接解析成功 %d/%d 条。" % (resolved, len(items)))

    # 抓正文
    fetched = []
    if args.fetch:
        targets = [it for it in items if it["真实链接"]][:args.fetch]
        if not targets:
            print()
            log("!! --fetch 要求抓正文，但没有一条拿到真实链接 —— 抓取无法进行。")
            print_table(items, True)
            if args.json_out:
                _dump_json(args, items, fetched, args.json_out)
            return 1
        print()
        log("开始抓取前 %d 篇正文 -> %s" % (len(targets), os.path.abspath(args.outdir)))
        for i, it in enumerate(targets, 1):
            log("=== 抓取 [%d/%d] %s ===" % (i, len(targets), it["标题"][:40]))
            stats, err = fetch_by_sogou_result(it, args.outdir, timeout=args.timeout)
            if stats:
                it["抓取结果"] = {"标题": stats["标题"], "正文字数": stats["正文字数"],
                                  "图片数": stats["图片数"], "目录": stats["_目录"]}
                fetched.append(stats)
                log("  ✓ 已保存：%s（%d 字 / %d 图）"
                    % (stats["_目录"], stats["正文字数"], stats["图片数"]))
            else:
                it["抓取结果"] = {"错误": err}
                log("  ✗ 抓取失败：%s" % err)
            if i < len(targets):
                time.sleep(3)

    print()
    log("========== 搜索结果（关键词：%s） ==========" % args.keyword)
    print()
    print_table(items, args.resolve)

    log("========== 汇总 ==========")
    log("搜索到 %d 条结果" % len(items))
    if args.resolve:
        log("真实链接解析成功 %d 条" % sum(1 for it in items if it["真实链接"]))
    if args.fetch:
        log("正文抓取成功 %d 篇，失败 %d 篇"
            % (len(fetched), len([it for it in items if it.get("抓取结果", {}).get("错误")])))

    if args.json_out:
        _dump_json(args, items, fetched, args.json_out)

    # 拿到了搜索结果就算成功；搜索本身没结果/被拦才算失败
    return 0


def _dump_json(args, items, fetched, path):
    payload = {
        "关键词": args.keyword,
        "页数": args.page,
        "结果数": len(items),
        "搜索时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "渠道": "搜狗微信 weixin.sogou.com",
        "结果": items,
        "抓取汇总": [{"标题": s["标题"], "目录": s["_目录"], "字数": s["正文字数"],
                      "图片": s["图片数"]} for s in fetched],
    }
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log("结果已写入：%s" % os.path.abspath(path))


if __name__ == "__main__":
    sys.exit(main())
