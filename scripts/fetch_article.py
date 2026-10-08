#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓文章（快） / 抓全套（全）—— anan-wechat-suite 模块 2 & 3
=============================================================
抓取引擎**直接复用第一课 anan-wechat-article 的 wechat_spider.py**，
本文件只做两件事：① 分「快 / 全」两档调用它；② 在「全」档上补富媒体层。

    快（fast）  正文 Markdown + 正文图片 + 封面图 + 信息表
    全（full）  快档的全部，外加：
                - 富媒体清单：视频 / 音频 / 视频号 / 小程序卡片 / 公众号名片，
                  逐条标明「拿到了什么、拿不到什么、为什么」
                - 尽力解析视频的真实播放地址与封面（能拿到就给，拿不到如实说）
                - 原始页面 HTML 存档（离线留底，微信改版后仍可回溯）
                - 图片原始尺寸等扩展元数据

能力边界（**如实写清，不夸大**）：
    ✅ 能拿到：正文文字、正文配图、封面图、标题/公众号/作者/发布时间等元信息
    ✅ 能拿到（全档）：页面里以 JSON 形式内嵌的视频地址与封面
    ❌ 拿不到：阅读量 / 点赞 / 在看 / 收藏 / 评论（JS 异步填，静态页面没有）
    ❌ 拿不到：需要登录或客户端才有权限的资源（部分视频的播放地址即属此类）
    ⚠️ 注意：这不是浏览器渲染。脚本请求的是服务端返回的静态 HTML，
       微信正文图恰好把真地址放在 data-src 属性里，所以能取到；
       若某天微信改成纯 JS 注入图片，本模块会失效——届时报错而不是假装成功。

用法：
    python scripts/fetch_article.py "https://mp.weixin.qq.com/s/xxxx" --mode fast
    python scripts/fetch_article.py "https://mp.weixin.qq.com/s/xxxx" --mode full
    python scripts/fetch_article.py --urls-file urls.txt --mode full --delay 5
"""

import argparse
import json
import os
import pathlib
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wechat_spider as spider          # 第一课引擎：抓取 / 转 Markdown / 下图 / 写表

VERSION = "1.0.0"


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------- 富媒体抽取
# 正文里出现的富媒体容器标签 -> 人类可读的类型名
RICHTEXT_TYPES = {
    "mpvideo": "视频（公众号原生）",
    "mp-common-videosnap": "视频号动态",
    "mpvoice": "语音",
    "mp-common-mpaudio": "音频",
    "qqmusic": "QQ 音乐",
    "mpminiprogram": "小程序卡片",
    "mpcpc": "小程序卡片",
    "mp-common-profile": "公众号名片",
    "iframe": "内嵌框架",
    "wx-open-subscribe": "订阅组件",
}


def _balanced_slice(text, start, open_ch="[", close_ch="]"):
    """从 start 位置起，按括号配对切出一段完整文本（用来抠内嵌 JSON）。"""
    depth = 0
    i = start
    in_str = None
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c in "\"'":
            in_str = c
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return ""


def extract_video_infos(page_html):
    """从页面里抠出内嵌的视频信息（真实播放地址 / 封面 / 时长）。

    微信把视频信息以 JS 字面量形式内嵌在页面里，形如：
        mp_video_trans_info: [ { video_id: "wxv_...", cdn_url: "...", ... } ]
    这是**页面里就有的数据**，不需要登录、不需要渲染，所以能拿到。
    抠不到就返回空列表 —— 说明该视频只有客户端才能取到地址，如实标注。
    """
    infos = []
    for m in re.finditer(r"mp_video_trans_info\s*[:=]\s*\[", page_html):
        seg = _balanced_slice(page_html, m.end() - 1)
        if not seg or seg.strip() in ("[]", ""):
            continue
        # 尽量按 JSON 解析；JS 字面量的裸键名先补引号
        candidate = seg
        try:
            data = json.loads(candidate)
        except Exception:
            fixed = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', seg)
            fixed = fixed.replace("'", '"')
            try:
                data = json.loads(fixed)
            except Exception:
                data = None
        if isinstance(data, list):
            for d in data:
                if isinstance(d, dict):
                    infos.append({
                        "视频ID": d.get("video_id") or d.get("vid") or "",
                        "播放地址": d.get("cdn_url") or d.get("url") or "",
                        "封面图": d.get("cover_url") or d.get("cover") or "",
                        "时长秒": d.get("duration") or d.get("play_length") or "",
                        "字节数": d.get("filesize") or "",
                        "来源": "页面内嵌 mp_video_trans_info",
                    })
    return infos


def extract_richmedia(content_html, page_html):
    """扫描正文容器，列出所有富媒体以及「能拿到什么」。"""
    found = []
    for tag, label in RICHTEXT_TYPES.items():
        pattern = r"<" + re.escape(tag) + r"\b([^>]*)>"
        for m in re.finditer(pattern, content_html, re.I):
            attrs_raw = m.group(1) or ""
            attrs = {}
            for am in re.finditer(r'([\w-]+)\s*=\s*"([^"]*)"', attrs_raw):
                attrs[am.group(1).lower()] = am.group(2)
            item = {
                "类型": label,
                "标签": tag,
                "可获取性": "仅占位（静态页面不含内容）",
                "线索": {},
            }
            if tag in ("mpvideo", "iframe"):
                vid = attrs.get("data-mpvid") or attrs.get("data-vid") or ""
                if vid:
                    item["线索"]["视频ID"] = vid
                src = attrs.get("data-src") or attrs.get("src") or ""
                if src:
                    item["线索"]["播放器地址"] = src
            elif tag in ("mpvoice", "mp-common-mpaudio"):
                for k in ("data-voiceid", "data-voiceencodefileid", "data-name"):
                    if attrs.get(k):
                        item["线索"][k] = attrs[k]
            elif tag == "mp-common-profile":
                for k in ("data-nickname", "data-alias"):
                    if attrs.get(k):
                        item["线索"][k] = attrs[k]
                item["可获取性"] = "已取到名片文字（昵称 / 微信号）；名片本体不在页面里"
            elif tag == "mp-common-videosnap":
                for k in ("data-videosnap-id", "data-nickname"):
                    if attrs.get(k):
                        item["线索"][k] = attrs[k]
            elif tag in ("mpminiprogram", "mpcpc"):
                for k in ("data-miniprogram-nickname", "data-miniprogram-appid",
                          "data-miniprogram-path"):
                    if attrs.get(k):
                        item["线索"][k] = attrs[k]
            found.append(item)

    # 视频真实地址：页面内嵌 JSON 里可能有
    videos = extract_video_infos(page_html)
    for v in videos:
        found.append({
            "类型": "视频真实地址（页面内嵌）",
            "标签": "mp_video_trans_info",
            "可获取性": "已取到播放地址" if v.get("播放地址") else "条目存在但无播放地址",
            "线索": v,
        })
    return found


# ---------------------------------------------------------------- 主流程
def run_full(url, outdir, timeout=30):
    """「全」档：复用第一课引擎抓取，再补富媒体层 + 原始页面存档。"""
    meta, content_html, page_html = spider.fetch_article(url, timeout=timeout)

    folder = spider.safe_name(meta["标题"], fallback="文章_" + spider.short_path_from_url(url))
    art_dir = outdir / folder
    img_dir = art_dir / "images"
    art_dir.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)

    # 复用第一课的图片下载器与 Markdown 转换器（不重写）
    dl = spider.ImageDownloader(img_dir, "images", enabled=True, delay=0.5, timeout=timeout)
    conv = spider.MarkdownConverter(image_resolver=dl.localize)
    conv.feed(content_html)
    conv.close()
    body_md, plain = conv.result()

    if not body_md.strip():
        raise RuntimeError("正文解析为空：正文容器拿到了，但没转出任何内容。"
                           "这是异常情况，请把链接报给作者排查（不要当成抓取成功）。")

    # 封面图（复用第一课的请求层）
    cover_name = ""
    if meta.get("封面图"):
        try:
            data, hdrs, _ = spider.http_get(meta["封面图"], spider.IMAGE_HEADERS,
                                            timeout=timeout, retries=2)
            if len(data) < 128:
                raise RuntimeError("返回内容过小，可能被防盗链拦截")
            ctype = (hdrs.get("Content-Type") or "").split(";")[0].strip().lower()
            ext = spider.ImageDownloader.EXT_MAP.get(ctype.split("/")[-1], "jpg")
            cover_name = "cover.%s" % ext
            (art_dir / cover_name).write_bytes(data)
            log("  封面图已保存：%s（%.1f KB）" % (cover_name, len(data) / 1024.0))
        except Exception as e:
            log("  !! 封面图下载失败（信息表里记原始链接）：%s" % e)

    # ---- 富媒体层 ----
    rich = extract_richmedia(content_html, page_html)
    real_videos = [r for r in rich if r["标签"] == "mp_video_trans_info" and r["线索"].get("播放地址")]
    log("  富媒体条目：%d 条（其中有真实播放地址的视频 %d 个）" % (len(rich), len(real_videos)))

    # 视频封面图也下下来（全档的附加值）
    for i, v in enumerate(real_videos, 1):
        cov = v["线索"].get("封面图")
        if not cov:
            continue
        try:
            data, hdrs, _ = spider.http_get(cov, spider.IMAGE_HEADERS, timeout=timeout, retries=2)
            if len(data) < 128:
                raise RuntimeError("返回内容过小")
            name = "video_%02d_cover.jpg" % i
            (art_dir / name).write_bytes(data)
            v["线索"]["封面已存为"] = name
            log("  视频封面已保存：%s" % name)
        except Exception as e:
            v["线索"]["封面下载失败"] = str(e)

    # ---- 原始页面存档 ----
    (art_dir / "原始页面.html").write_text(page_html, encoding="utf-8")
    log("  原始页面已存档：原始页面.html（%s 字符）" % format(len(page_html), ","))

    # ---- Markdown（与快档同格式，便于两种档位互换）----
    head = ["# %s" % (meta["标题"] or "（标题未获取）"), "",
            "> 公众号：%s  " % (meta["公众号"] or "未获取"),
            "> 作者：%s  " % (meta["作者"] or "未获取"),
            "> 发布时间：%s  " % (meta["发布时间"] or "未获取"),
            "> 原文链接：%s  " % meta["原文链接"],
            "> 抓取时间：%s  " % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "> 抓取档位：全（正文 + 图片 + 富媒体 + 原始页面）", "", "---", ""]
    md_text = "\n".join(head) + body_md
    md_path = art_dir / ("%s.md" % folder)
    md_path.write_text(md_text, encoding="utf-8")
    log("  Markdown 已写出：%s（%s 字符）" % (md_path.name, format(len(md_text), ",")))

    stats = {
        "标题": meta["标题"], "公众号": meta["公众号"], "作者": meta["作者"],
        "发布时间": meta["发布时间"], "正文字数": len(plain), "图片数": len(dl.records),
        "封面图": cover_name or meta.get("封面图", ""), "原文链接": meta["原文链接"],
        "抓取时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "_目录": folder, "_正文HTML字符数": len(content_html),
        "_Markdown字符数": len(md_text), "_图片失败数": len(dl.failures),
        "_富媒体占位": sorted(set(conv.rich_notes)), "_档位": "全",
        "_富媒体条目数": len(rich),
        "_可解析视频数": len(real_videos),
        "_原始页面字节数": len(page_html.encode("utf-8")),
    }

    (art_dir / "meta.json").write_text(json.dumps(
        {"信息表": stats, "图片清单": dl.records, "图片失败": dl.failures,
         "未抓取到的富媒体": stats["_富媒体占位"], "富媒体清单": rich},
        ensure_ascii=False, indent=2), encoding="utf-8")

    # 富媒体单独一份，方便一眼看「全档多了什么」
    rl = ["# 富媒体清单（全档）", "",
          "| 类型 | 可获取性 | 线索 |", "| --- | --- | --- |"]
    if rich:
        for r in rich:
            clue = "；".join("%s=%s" % (k, v) for k, v in r["线索"].items()) or "—"
            rl.append("| %s | %s | %s |" % (r["类型"], r["可获取性"], clue.replace("|", "\\|")))
    else:
        rl.append("| （本篇没有富媒体） | — | — |")
    rl += ["", "说明：", "",
           "- 「仅占位」= 该内容在静态页面里只有一个容器标签，正文没有它的实际内容。",
           "- 视频若能在页面内嵌 JSON（mp_video_trans_info）里找到，会给出真实播放地址与封面；",
           "  找不到就是找不到——那类视频只有微信客户端才拿得到地址，**不要当成抓取失败，也不要假装抓到了**。"]
    (art_dir / "富媒体清单.md").write_text("\n".join(rl) + "\n", encoding="utf-8")

    spider.write_info_files(art_dir, stats, dl)
    spider.append_index(outdir, stats)
    return stats, dl


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="抓公众号文章：快（正文+图片）/ 全（+富媒体+原始页面）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python scripts/fetch_article.py \"https://mp.weixin.qq.com/s/xxxx\" --mode fast\n"
               "  python scripts/fetch_article.py \"https://mp.weixin.qq.com/s/xxxx\" --mode full\n")
    ap.add_argument("url", nargs="?", help="公众号文章链接")
    ap.add_argument("--urls-file", help="批量：每行一个链接（# 开头为注释）")
    ap.add_argument("--mode", choices=["fast", "full"], default="fast",
                    help="fast=只抓正文+图片（默认）；full=再加富媒体与原始页面存档")
    ap.add_argument("-o", "--outdir", default="output", help="输出根目录（默认 ./output）")
    ap.add_argument("--no-images", action="store_true", help="不下载图片（仅 fast 档）")
    ap.add_argument("--delay", type=float, default=3.0, help="文章之间的间隔秒数（默认 3）")
    ap.add_argument("--timeout", type=int, default=30, help="单次请求超时秒数")
    args = ap.parse_args(argv)

    urls = []
    if args.url:
        urls.append(args.url)
    if args.urls_file:
        urls.extend(spider.read_urls_file(args.urls_file))
    if not urls:
        ap.print_help()
        print("\n错误：请给出文章链接，或用 --urls-file 指定链接清单。")
        return 2

    outdir = pathlib.Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    log("抓取器 v%s | 档位：%s | 输出：%s" % (VERSION, args.mode, outdir))
    log("本次共 %d 个链接" % len(urls))

    ok, failed = [], []
    for i, u in enumerate(urls, 1):
        if len(urls) > 1:
            log("=== [%d/%d] ===" % (i, len(urls)))
        try:
            spider.check_wechat_url(u)
            if args.mode == "fast":
                stats, _dl = spider.build_article(
                    u, outdir, want_images=not args.no_images,
                    delay=0.5, timeout=args.timeout)
                stats["_档位"] = "快"
                log("完成（快）：%s | 正文 %d 字 | 图片 %d 张"
                    % (stats["标题"] or "(无标题)", stats["正文字数"], stats["图片数"]))
            else:
                stats, _dl = run_full(u, outdir, timeout=args.timeout)
                log("完成（全）：%s | 正文 %d 字 | 图片 %d 张 | 富媒体 %d 条"
                    % (stats["标题"] or "(无标题)", stats["正文字数"],
                       stats["图片数"], stats["_富媒体条目数"]))
            ok.append(stats)
        except Exception as e:
            log("!! 失败：%s -> %s" % (u, e))
            failed.append({"链接": u, "原因": str(e)})
        if i < len(urls) and args.delay:
            import time
            time.sleep(args.delay)

    print()
    log("========== 汇总 ==========")
    log("成功 %d 篇，失败 %d 篇" % (len(ok), len(failed)))
    for s in ok:
        log("  ✓ [%s] %s（%d 字 / %d 图）"
            % (s.get("_档位", "?"), s["标题"] or "(无标题)", s["正文字数"], s["图片数"]))
    for f in failed:
        log("  ✗ %s -> %s" % (f["链接"], f["原因"]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
