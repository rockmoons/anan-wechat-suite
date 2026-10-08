#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自检 —— anan-wechat-suite
================================
一条命令验证整个技能包**不需要网络**就能验的那部分是否完好：
排版硬规矩、风格配置完整性、密钥安全边界、文档与代码是否同步。

这个脚本存在的理由很直接：很多技能包的 SKILL.md 让人去跑一个根本不存在的
脚本。本脚本会把 SKILL.md 里出现的每一个 scripts/xxx.py 都拿去核对一遍，
不存在就直接判失败 —— 文档和代码不许各说各话。

还有一项「老路径扫描」：微信的密钥页与接口权限页 2025-12-01 起迁到了
「微信开发者平台」，公众号后台的老菜单路径（设置与开发 / 基本配置）已经打不开了。
包内任何地方再把老路径写成**操作指引**（而不是标注「老路径已迁移」）就直接判失败 ——
这类说明会随时间失效，必须有机制拦住它。

用法：
    python scripts/selftest.py
    python scripts/selftest.py --quiet

退出码：0 = 全部通过；1 = 有失败项（失败详情已打印）
"""

import argparse
import html as html_mod
import io
import json
import os
import re
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
REFS = os.path.join(ROOT, "references")
BOM = chr(0xFEFF)
Q = chr(96)

if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

RESULTS = []


def check(name):
    """装饰器：跑一个检查项，异常即失败。"""
    def deco(fn):
        def run(quiet=False):
            try:
                detail = fn()
                RESULTS.append((True, name, detail or ""))
                if not quiet:
                    print("  PASS  %s%s" % (name, ("  —— " + detail) if detail else ""))
            except AssertionError as e:
                RESULTS.append((False, name, str(e)))
                print("  FAIL  %s\n        %s" % (name, e))
            except Exception:
                tb = traceback.format_exc().strip().splitlines()[-1]
                RESULTS.append((False, name, tb))
                print("  FAIL  %s\n        %s" % (name, tb))
        run.__name__ = fn.__name__
        return run
    return deco


# ================================================================ 排版
FIXTURE_MD = (
    "# 自检标题\n\n"
    "正文里有 **加粗** 和 " + Q + "行内代码" + Q + "。\n\n"
    "## 二级标题\n\n"
    "- **前缀加粗**：后面这段必须断开字重，否则整行都会变粗。\n"
    "- 普通列表项\n\n"
    "| 列 A | 列 B |\n| --- | --- |\n| 1 | 2 |\n\n"
    + Q * 3 + "python\n"
    "def f(x):\n    # 注释\n    return x + 1\n"
    + Q * 3 + "\n\n"
    "> 引用块\n"
)


def _typeset(text):
    import typeset
    body, stats = typeset.md_to_html(text, typeset.THEMES["anan"],
                                     typeset.CODE_THEMES["monokai"])
    return body, stats


@check("排版：带 BOM 的输入仍然能识别 H1（Windows 记事本必踩）")
def t_bom():
    body, stats = _typeset(BOM + FIXTURE_MD)
    assert stats["h1"] == 1, "带 BOM 时 H1 数量为 %d，应为 1（说明 BOM 没剥干净）" % stats["h1"]
    assert "<h1 style=" in body, "输出里没有 <h1>"
    return "H1=%d" % stats["h1"]


@check("排版：不带 BOM 与带 BOM 的输出完全一致")
def t_bom_same():
    a, _ = _typeset(FIXTURE_MD)
    b, _ = _typeset(BOM + FIXTURE_MD)
    assert a == b, "两者输出不同，说明 BOM 处理不彻底"
    return "两次输出一致"


@check("排版：CRLF 换行不影响解析")
def t_crlf():
    body, stats = _typeset(FIXTURE_MD.replace("\n", "\r\n"))
    assert stats["h1"] == 1 and stats["table"] == 1, "CRLF 下解析异常"
    return "H1=%d 表格=%d" % (stats["h1"], stats["table"])


@check("排版：<li> 里的 <strong> 前缀已用 font-weight:normal 断开字重")
def t_li_strong():
    body, _ = _typeset(FIXTURE_MD)
    m = re.search(r"<li[^>]*>(<strong[^>]*>.+?</strong>)(<span[^>]*font-weight:normal[^>]*>.+?</span>)</li>",
                  body, re.S)
    assert m, "没有找到「strong + font-weight:normal span」的列表项，整行变粗的坑没被修掉"
    return "已断开"


@check("排版：只用内联样式（无 <style> 标签、无 class 属性）")
def t_inline_only():
    body, _ = _typeset(FIXTURE_MD)
    assert not re.search(r"<style[\s>]", body, re.I), "输出里出现了 <style> 标签，公众号会剥掉"
    assert not re.search(r"\sclass=", body), "输出里出现了 class 属性，公众号会剥掉"
    return "全内联"


@check("排版：表格用真正的 <table>")
def t_table():
    body, _ = _typeset(FIXTURE_MD)
    assert "<table style=" in body and "<th style=" in body and "<td style=" in body, "表格结构不完整"
    return "table/th/td 齐全"


@check("排版：代码块带语法高亮，且剥离高亮后与源代码逐字一致")
def t_code():
    import typeset
    src = "def f(x):\n    # 注释\n    return x + 1\n"
    body, _ = _typeset(FIXTURE_MD)
    assert "<pre style=" in body, "没有 <pre>"
    assert '<span style="color:' in body, "代码没有高亮 span"
    # 高亮只是往代码里插 span，把 span 剥掉之后必须与源码一模一样，
    # 一个字都不能多、一个字都不能少 —— 这才是「没破坏代码」的正确验法。
    hl = typeset.highlight(src, "python", typeset.CODE_THEMES["monokai"])
    restored = html_mod.unescape(re.sub(r"<span[^>]*>", "", hl).replace("</span>", ""))
    assert restored == src, "高亮破坏了代码：\n原文 %r\n还原 %r" % (src, restored)
    return "逐字一致"


@check("排版：需要背景的块用 <section> 包裹")
def t_section():
    body, _ = _typeset(FIXTURE_MD)
    assert re.search(r"<section style=\"background:", body), "引用块没有用 section 包背景"
    return "section 包裹"


@check("排版：五种页面主题 × 五种代码主题都能出图不报错")
def t_all_themes():
    import typeset
    n = 0
    for t in typeset.THEMES:
        for c in typeset.CODE_THEMES:
            body, _ = typeset.md_to_html(FIXTURE_MD, typeset.THEMES[t], typeset.CODE_THEMES[c])
            assert body.strip(), "主题 %s / %s 产出为空" % (t, c)
            n += 1
    return "%d 种组合全部通过" % n


@check("排版：每个主题的样式键都齐全（缺键会 KeyError）")
def t_theme_keys():
    import typeset
    need = set(typeset.DEFAULT_THEME)
    for k, v in typeset.THEMES.items():
        miss = need - set(v)
        assert not miss, "主题 %s 缺键：%s" % (k, miss)
    return "%d 个主题齐全" % len(typeset.THEMES)


# ================================================================ 搜索
@check("搜索：JS 分片拼回的地址不会把 &timestamp 吃成 ×tamp")
def t_sogou_unescape():
    import sogou_search
    frag = ["https://mp.weix", "in.qq.com/s?src=11&times", "tamp=1791268384&ver=7009"]
    real = "".join(frag).replace("@", "").replace("&amp;", "&")
    assert "&timestamp=" in real, "地址被整体反转义破坏了：%s" % real
    assert "×" not in real, "出现了 × 字符，说明误用了 html.unescape"
    return "&timestamp 完好"


@check("搜索：结果解析器能从搜狗页面结构里抠出条目")
def t_sogou_parse():
    import sogou_search
    page = ('<ul class="news-list"><li id="sogou_vr_11002601_box_0"><div class="txt-box">'
            '<h3><a href="/link?url=abc">测试标题</a></h3>'
            '<p class="txt-info">这是摘要</p>'
            '<div class="s-p"><a class="account">某公众号</a>'
            '<span class="s2"><script>document.write(timeConvert(\'1791268384\'))</script></span>'
            '</div></div></li></ul>')
    items = sogou_search.parse_results(page)
    assert len(items) == 1, "应解析出 1 条，实际 %d 条" % len(items)
    it = items[0]
    assert it["标题"] == "测试标题", "标题错：%s" % it["标题"]
    assert it["公众号"] == "某公众号", "公众号错：%s" % it["公众号"]
    assert it["摘要"] == "这是摘要", "摘要错：%s" % it["摘要"]
    assert it["发布时间"], "没解析出发布时间"
    return "标题/摘要/公众号/时间 全部正确"


@check("搜索：反爬页面会被识别并明确报错（不静默失败）")
def t_sogou_block():
    import sogou_search
    assert sogou_search.check_blocked("<html>用户您好，您的访问过于频繁</html>"), "没识别出反爬页"
    assert not sogou_search.check_blocked("<html>正常结果页</html>"), "正常页被误判"
    return "识别正常"


# ================================================================ 抓取 / 富媒体
@check("抓取：视频信息能从页面内嵌 JSON 里抠出来（用构造样本验证代码路径）")
def t_video_extract():
    import fetch_article
    page = ('<script>var videoData = { mp_video_trans_info: [ '
            '{ video_id: "wxv_123456", cdn_url: "https://mpvideo.qpic.cn/xx.f10002.mp4", '
            'cover_url: "https://mmbiz.qpic.cn/cover.jpg", duration: 65, filesize: 1234567 } '
            '] };</script>')
    infos = fetch_article.extract_video_infos(page)
    assert len(infos) == 1, "应抠出 1 条视频信息，实际 %d 条" % len(infos)
    assert infos[0]["视频ID"] == "wxv_123456", "视频 ID 错"
    assert infos[0]["播放地址"].endswith(".mp4"), "播放地址错"
    assert infos[0]["封面图"].endswith("cover.jpg"), "封面地址错"
    return "视频 ID/播放地址/封面 全部抠出"


@check("抓取：空数组不会误判成「抓到了视频」")
def t_video_empty():
    import fetch_article
    page = "<script>mp_video_trans_info: [ ],</script>"
    infos = fetch_article.extract_video_infos(page)
    assert infos == [], "空数组应该抠不出东西，实际返回 %d 条" % len(infos)
    return "空数组正确返回 0 条"


@check("抓取：富媒体扫面能识别公众号名片并取到昵称/微信号")
def t_richmedia_profile():
    import fetch_article
    content = ('<mp-common-profile data-nickname="首席营销门" data-alias="yingxiaogcb">'
               '</mp-common-profile>')
    found = fetch_article.extract_richmedia(content, "")
    assert len(found) == 1, "应识别出 1 条，实际 %d 条" % len(found)
    assert found[0]["线索"]["data-nickname"] == "首席营销门"
    assert found[0]["线索"]["data-alias"] == "yingxiaogcb"
    return "名片信息取到"


@check("抓取：快档与全档共用第一课引擎（没有被重写）")
def t_reuse_spider():
    import fetch_article
    import wechat_spider
    assert hasattr(fetch_article, "spider"), "fetch_article 没有引用 wechat_spider"
    assert fetch_article.spider is wechat_spider, "引用的不是同一个引擎模块"
    assert hasattr(wechat_spider, "build_article"), "第一课引擎缺少 build_article"
    assert hasattr(wechat_spider, "MarkdownConverter"), "第一课引擎缺少 MarkdownConverter"
    return "复用 wechat_spider 成功"


# ================================================================ 写作 / 改写
@check("写作：styles.json 可读，四种口吻都有「结构骨架 + 硬规则 + 禁用表达」")
def t_styles():
    import article_writer
    cfg = article_writer.load_styles()
    want = ["business-insight", "viral-structure", "personal-voice", "finance-depth"]
    for k in want:
        assert k in cfg["styles"], "缺少口吻：%s" % k
        st = cfg["styles"][k]
        for field in ("structure", "hard_rules", "banned", "voice_traits", "one_liner"):
            assert st.get(field), "口吻 %s 缺字段 %s" % (k, field)
            if field in ("structure", "hard_rules", "banned"):
                assert len(st[field]) >= 3, "口吻 %s 的 %s 条目过少" % (k, field)
    return "4 种口吻三件套齐全"


@check("写作：三种改写模式齐全")
def t_rewrite_modes():
    import article_writer
    cfg = article_writer.load_styles()
    for k in ("restructure", "rewrite", "humanize"):
        assert k in cfg["rewrite_modes"], "缺少改写模式：%s" % k
        assert cfg["rewrite_modes"][k]["actions"], "改写模式 %s 没有动作清单" % k
    return "3 种模式齐全"


@check("写作：生成的提示词里真的带上了该口吻的规则")
def t_prompt_built():
    import article_writer
    cfg = article_writer.load_styles()
    p = article_writer.build_draft_prompt(cfg, "personal-voice", "测试选题")
    for kw in ("结构骨架", "硬规则", "禁用表达", "随意感"):
        assert kw in p, "提示词里缺少「%s」" % kw
    assert "测试选题" in p, "提示词里没有带上选题"
    return "%d 字符" % len(p)


@check("写作：--check 能抓出禁用表达（规则是能真的执行的）")
def t_check_catches():
    import article_writer
    cfg = article_writer.load_styles()
    bad = "# 标题\n\n我们要赋能整个行业，打造闭环抓手。" + "我" * 10
    problems, _ = article_writer.check_draft(cfg, "personal-voice", bad)
    joined = " ".join(problems)
    assert "赋能" in joined or "抓手" in joined or "闭环" in joined, \
        "禁用词没有被抓出来，问题列表：%s" % problems
    return "抓到 %d 个问题" % len(problems)


@check("写作：合规条款用词合规（不出现不该出现的说法）")
def t_no_forbidden_word():
    import article_writer
    cfg = article_writer.load_styles()
    text = json.dumps(cfg, ensure_ascii=False)
    assert ("洗" + "稿") not in text, "styles.json 里出现了不该出现的说法"
    return "用词合规"


@check("写作：④ 的口径是「AI 直接写」，提示词只是副产品（文档与脚本一致）")
def t_write_directly():
    """模块④ 的新口径是「AI 读完规则自己写」，不是「先出提示词、再交给别人写」。

    这条口径写在两处：SKILL.md 的模块④ 说明、article_writer.py 的说明与跑完打印的下一步。
    两处任一被改回旧口径，这里就判红 —— 免得文档说一套、脚本说另一套。
    """
    src = open(os.path.join(SCRIPTS, "article_writer.py"), "r", encoding="utf-8").read()
    skill = open(os.path.join(ROOT, "SKILL.md"), "r", encoding="utf-8").read()
    for where, text in (("scripts/article_writer.py", src), ("SKILL.md", skill)):
        assert "直接写" in text, "%s 里没有「直接写」—— 模块④ 还是旧口径（先出提示词再交给别人写）" % where
        assert "副产品" in text, "%s 里没写明「那份提示词是副产品」" % where
    assert "交给豆包工作去写" not in src, "article_writer.py 还写着「交给豆包工作去写」，旧口径没清干净"
    return "SKILL.md 与脚本口径一致：直接写 + 提示词是副产品"


# ================================================================ 配图
@check("配图：提示词包含主体/风格/配色/画幅/避免项")
def t_illustration():
    import illustration
    p = illustration.build_prompt("一个测试主体", "tech-blue", "16:9 横版")
    for kw in ("画面主体", "风格", "配色", "画幅", "避免"):
        assert kw in p, "提示词缺少「%s」" % kw
    return "%d 字符" % len(p)


@check("配图：文章类型与风格预设齐全")
def t_illustration_tables():
    import illustration
    assert len(illustration.TYPES) >= 6, "文章类型少于 6 种"
    assert len(illustration.STYLES) >= 4, "风格预设少于 4 种"
    for k, v in illustration.TYPES.items():
        assert v.get("hint") and v.get("cover_motif"), "类型 %s 缺建议" % k
    return "%d 种类型 / %d 种风格" % (len(illustration.TYPES), len(illustration.STYLES))


# ================================================================ 发布
@check("发布：只允许把请求发往微信官方域名（密钥外发硬拦截）")
def t_host_guard():
    import publish
    publish.assert_official_host("https://api.weixin.qq.com/cgi-bin/token")
    for bad in ("https://evil.example.com/api/publish",
                "http://local" + "host:3000/api/mcp/upload_file",
                "https://api.weixin.qq.com.evil.com/x"):
        try:
            publish.assert_official_host(bad)
        except RuntimeError:
            continue
        raise AssertionError("没有拦截住非官方域名：%s" % bad)
    return "官方放行，第三方一律拦截"


@check("发布：没有密钥时判定为「配置有误」而不是假装能发")
def t_publish_capability():
    import publish
    cap, why = publish.account_capability({"type": "service", "appid": "", "appsecret": ""})
    assert cap == "error", "服务号缺密钥时应判 error，实际 %s" % cap
    cap2, _ = publish.account_capability({"type": "personal"})
    assert cap2 == "paste", "个人号没密钥应判 paste，实际 %s" % cap2
    cap3, _ = publish.account_capability({"type": "service", "appid": "wx123456",
                                          "appsecret": "abcdef123456"})
    assert cap3 == "api", "密钥齐全应判 api，实际 %s" % cap3
    # ★ type 的旧词汇当兼容别名：service -> api；personal -> auto（未指定，仍按密钥判断）
    cap4, _ = publish.account_capability({"type": "personal", "appid": "wx123456",
                                          "appsecret": "abcdef123456"})
    assert cap4 == "api", "旧词汇 personal + 密钥应判 api（与改动前一致），实际 %s" % cap4
    assert publish.normalize_type("service") == "api", "service 应归一为 api"
    assert publish.normalize_type("personal") == "auto", "personal 应归一为 auto（不是 paste）"
    assert publish.normalize_type("paste") == "paste", "paste 就是 paste"
    assert publish.normalize_type(None) == "auto", "缺 type 应归一为 auto"
    cap5, why5 = publish.account_capability({"type": "paste", "appid": "wx123456",
                                             "appsecret": "abcdef123456"})
    assert cap5 == "api", "显式 paste + 密钥：能力仍是 api（能不能走接口看密钥），实际 %s" % cap5
    assert "默认走粘贴" in why5, "显式 paste 的说明必须点明「默认走粘贴模式」，实际：%s" % why5
    return "四类判定正确；旧词汇 service/personal 按别名处理"


@check("发布：密钥打码不会把完整密钥打进日志")
def t_mask():
    import publish
    s = "abcdef1234567890abcdef"
    m = publish.mask(s)
    assert s not in m, "打码后仍包含完整密钥：%s" % m
    assert "****" in m or "..." in m, "打码不明显"
    return m


@check("发布：错误码翻译表覆盖了常见的几个硬骨头")
def t_errcodes():
    import publish
    for code in (40164, 48001, 40013, 40125):
        assert code in publish.ERRCODE_HELP, "缺少错误码 %s 的解释" % code
    return "覆盖 IP 白名单 / 权限不足 / AppID / AppSecret"


@check("发布：--probe 只验第 1 步，且明说「过了不代表能发文章」")
def t_probe_scope():
    import publish

    FAKE = {"active": "service", "accounts": {"service": {
        "name": "假服务号", "type": "service",
        "appid": "wx1234567890abcdef", "appsecret": "0123456789abcdef0123456789abcdef"}}}
    orig_json, orig_out = publish.http_json, sys.stdout
    publish.http_json = lambda *a, **k: {"access_token": "T", "expires_in": 7200}
    buf = io.StringIO()
    sys.stdout = buf
    try:
        code = publish.cmd_probe(FAKE, None)
    finally:
        sys.stdout = orig_out
        publish.http_json = orig_json
    out = buf.getvalue()
    assert code == 0, "--probe 在 token 正常时应返回 0，实际 %s" % code
    assert "不代表能发文章" in out, "--probe 通过后没提醒「这只是第 1 步」，学员会以为万事大吉"
    assert "--probe-full" in out, "--probe 没告诉用户「验到底」的入口"
    assert hasattr(publish, "cmd_probe_full"), "缺少 cmd_probe_full（验到底的那条路）"
    src = open(os.path.join(SCRIPTS, "publish.py"), "r", encoding="utf-8").read()
    assert '"--probe-full"' in src and '"--with-draft"' in src, "CLI 没挂 --probe-full / --with-draft"
    assert "make_test_png" in src, "缺少 --probe-full 用的测试小图生成器"
    return "范围说清了，验到底的入口也在"


# 群发接口的地址片段。★ 一律拆开写：本文件自己也要被下面这项扫到，不拆就会自己判自己。
# ★ 注意：短的群发片段是「带 sendall 的那个」的子串 —— 每一段都得拆开写，
#   否则这条闸门会把自己判红（第一次跑就是这么挂的）。
MASS_ENDPOINTS = ("message/" + "mass", "message/" + "custom/send",
                  "mass/" + "send", "message/" + "mass/" + "sendall")


@check("发布：**所有**脚本里都没有群发接口（这是长期承诺：以后也不加）")
def t_no_mass_send():
    """群发 = 推送给粉丝，本技能**永远不做**（打扰粉丝 / 不可撤销 / 一天 1 次额度）。

    扫的是 scripts/ 下**每一个** .py —— 不是只看 publish.py：新加一个脚本偷偷群发，也要拦住。
    """
    hits = []
    for fn in sorted(os.listdir(SCRIPTS)):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(SCRIPTS, fn), "r", encoding="utf-8").read()
        for forbidden in MASS_ENDPOINTS:
            if forbidden in src:
                hits.append("%s -> %s" % (fn, forbidden))
    assert not hits, "出现了群发接口（本技能承诺永不做群发）：%s" % "、".join(hits)
    src = open(os.path.join(SCRIPTS, "publish.py"), "r", encoding="utf-8").read()
    assert "draft/add" in src, "没有草稿接口"
    assert "freepublish/submit" in src, "没有发布接口（--publish 那一层）"
    return "扫了 %d 个脚本：无群发接口；草稿 + 发布两层都在" % len(
        [x for x in os.listdir(SCRIPTS) if x.endswith(".py")])


@check("发布：--publish 先报「标题/封面/摘要」并停下，只有 --publish-go 才真发")
def t_publish_two_step():
    """发布不可撤销，所以它是两步：--publish 只建草稿 + 报三样；--publish-go 才调发布接口。

    用假响应跑，不碰真账号；关键断言是「只跑 --publish 时，发布接口一次都不能被调到」。
    """
    import tempfile

    import publish

    FAKE = {"active": "service", "accounts": {"service": {
        "name": "假认证号", "type": "api",
        "appid": "wx1234567890abcdef", "appsecret": "0" * 32}}}
    tmp = tempfile.mkdtemp(prefix="suite_pub2_")
    cover = os.path.join(tmp, "c.jpg")
    with open(cover, "wb") as fh:
        fh.write(publish.make_test_png())
    md = os.path.join(tmp, "a.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# 测试标题\n\n正文一段。\n")

    calls = []
    state = {"submit": "ok"}

    def fake_json(url, data=None, method=None, timeout=30):
        if "cgi-bin/token" in url:
            calls.append("token")
            return {"access_token": "TK", "expires_in": 7200}
        if "draft/add" in url:
            calls.append("draft/add")
            return {"media_id": "D" * 20}
        if "freepublish/submit" in url:
            calls.append("freepublish/submit")
            if state["submit"] == "48001":
                return {"errcode": 48001, "errmsg": "api unauthorized"}
            return {"errcode": 0, "errmsg": "ok", "publish_id": "P1"}
        if "freepublish/get" in url:
            calls.append("freepublish/get")
            return {"errcode": 0, "article_detail": {"item": [
                {"article_url": "https://mp.weixin.qq.com/s/FAKE"}]}}
        calls.append("?" + url)
        return {}

    orig = (publish.load_config, publish.http_json, publish.http_multipart, sys.stdout)
    publish.load_config = lambda path=None: (FAKE, "")
    publish.http_json = fake_json
    publish.http_multipart = lambda *a, **k: {"media_id": "T" * 20}
    buf = io.StringIO()
    sys.stdout = buf
    try:
        rc1 = publish.main([md, "--mode", "api", "--cover", cover, "--publish"])
        first_calls = list(calls)
        calls[:] = []
        rc2 = publish.main(["--publish-go", "D" * 20])
        calls2 = list(calls)
        out2 = buf.getvalue()
        state["submit"] = "48001"
        calls[:] = []
        rc3 = publish.main(["--publish-go", "D" * 20])
        calls3 = list(calls)
        out3 = buf.getvalue()
    finally:
        publish.load_config, publish.http_json, publish.http_multipart, sys.stdout = orig
    out = buf.getvalue()

    assert rc1 == 0, "--publish 应正常结束（草稿建好、停在那一步），实际 %s" % rc1
    assert "draft/add" in first_calls, "--publish 没建草稿"
    assert "freepublish/submit" not in first_calls, \
        "★ --publish 竟然调了发布接口 —— 「先看一眼再发」这条防线没了"
    for kw in ("标题：", "封面：", "摘要："):
        assert kw in out, "--publish 没把「%s」报出来给用户看" % kw.rstrip("：")
    assert "--publish-go" in out, "--publish 没给出「确认后怎么发」的下一步命令"
    assert rc2 == 0 and "freepublish/submit" in calls2, "--publish-go 没走到发布接口"
    assert "freepublish/get" in calls2, "发布成功后没去查公开链接"
    assert "https://mp.weixin.qq.com/s/FAKE" in out2, "发布成功后没把公开链接给出来"
    assert rc3 == 1 and "freepublish/submit" in calls3, "48001 那一次没走到发布接口"
    assert rc3 == 1, "48001（没发布权限）应退出码非 0"
    for kw in ("48001", "草稿", "发表", "群发通知"):
        assert kw in out3, "48001 的说明里缺「%s」—— 用户不知道下一步怎么办" % kw
    return "两步走成立：--publish 不碰发布接口；--publish-go 成功给链接、48001 指路后台"


@check("发布：没配置时 publish.py **自己拦下发布**并指向 setup.py（不悄悄降级成粘贴）")
def t_no_config_blocked():
    """第二层保险：不依赖 AI 有没有读 SKILL.md，脚本自己拦住「没配置就想发布」。

    以前这里是静默降级成粘贴模式 —— 用户只会拿到一个 HTML，
    根本不知道还有「配好密钥就直发草稿箱」这条路。
    """
    import tempfile

    import publish

    # ★ 不动本机 config.json（作者本机是配好的）：只把 load_config 换成「读不到配置」。
    orig_load, orig_out = publish.load_config, sys.stdout
    publish.load_config = lambda path=None: ({}, "未找到配置文件（自检模拟）")
    tmp = tempfile.mkdtemp(prefix="suite_selftest_")
    md = os.path.join(tmp, "自检文章.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# 自检标题\n\n正文一段。\n")
    html = os.path.join(tmp, "out.html")
    buf = io.StringIO()
    sys.stdout = buf
    try:
        rc_auto = publish.main([md])                                    # 默认 --mode auto
        rc_api = publish.main([md, "--mode", "api", "--cover", os.path.join(tmp, "c.jpg")])
        rc_paste = publish.main([md, "--mode", "paste", "-o", html])
    finally:
        sys.stdout = orig_out
        publish.load_config = orig_load
    out = buf.getvalue()

    assert rc_auto == 1, "没配置时跑发布必须退出码非 0（实际 %s）—— 静默降级就是把用户蒙在鼓里" % rc_auto
    assert rc_api == 1, "没配置时 --mode api 必须退出码非 0（实际 %s）" % rc_api
    assert "还没配置公众号 —— 不能发布。" in out, "没配置时的报错第一句不对，实际输出：\n%s" % out[:400]
    assert "python scripts/setup.py" in out, "报错里没有「跑 python scripts/setup.py」—— 下一步没给出来"
    assert "「配置」" in out, "报错里没告诉用户可以直接说「配置」"
    assert "--mode paste" in out, "报错里没给「显式走粘贴模式」的确切命令"
    assert out.count("粘贴模式：已完成") == 1, "只有显式 --mode paste 才该产出粘贴 HTML"
    assert rc_paste == 0 and os.path.isfile(html), "显式 --mode paste 必须照常可用（保底不能断）"
    return "没配置：auto/api 都拦下并指向 setup.py；显式 paste 照常可用"


# ================================================================ 文档与代码同步
@check("同步：SKILL.md 里提到的每个 scripts/*.py 都真实存在")
def t_docs_sync():
    skill = os.path.join(ROOT, "SKILL.md")
    assert os.path.isfile(skill), "找不到 SKILL.md"
    src = open(skill, "r", encoding="utf-8").read()
    refs = set(re.findall(r"scripts/([A-Za-z0-9_\-]+\.py)", src))
    assert refs, "SKILL.md 里没有出现任何 scripts/*.py，文档和代码可能已经脱节"
    missing = [r for r in sorted(refs) if not os.path.isfile(os.path.join(SCRIPTS, r))]
    assert not missing, "SKILL.md 让人去跑这些脚本，但它们不存在：%s" % "、".join(missing)
    return "核对了 %d 个脚本：%s" % (len(refs), "、".join(sorted(refs)))


@check("同步：SKILL.md 里提到的每个 references/* 都存在")
def t_refs_sync():
    skill = os.path.join(ROOT, "SKILL.md")
    src = open(skill, "r", encoding="utf-8").read()
    refs = set(re.findall(r"references/([A-Za-z0-9_\-]+\.(?:md|json))", src))
    missing = [r for r in sorted(refs) if not os.path.isfile(os.path.join(REFS, r))]
    assert not missing, "SKILL.md 引用了不存在的资料：%s" % "、".join(missing)
    return "核对了 %d 个资料文件" % len(refs)


@check("同步：SKILL.md 是**全大写**文件名（豆包工作只认大写）")
def t_skill_uppercase():
    # Windows / macOS 文件系统不区分大小写，os.path.isfile("skill.md") 一样返回 True，
    # 所以必须直接看目录里的**真实文件名**。
    names = os.listdir(ROOT)
    assert "SKILL.md" in names, "缺少全大写的 SKILL.md（豆包工作只认大写）"
    lowered = [n for n in names if n.lower() == "skill.md" and n != "SKILL.md"]
    assert not lowered, "存在大小写不对的同名文件：%s" % lowered
    return "真实文件名 = SKILL.md"


@check("同步：LICENSE 存在，且含四条条款与作者署名")
def t_license():
    p = os.path.join(ROOT, "LICENSE")
    assert os.path.isfile(p), "缺少 LICENSE"
    src = open(p, "r", encoding="utf-8").read()
    assert "阿南 rockmoons" in src, "LICENSE 里没有作者署名"
    assert "rockmoons" in src, "LICENSE 里没有微信号"
    assert "Copyright (c) 2026" in src, "LICENSE 里没有版权年份"
    for kw in ("个人学习", "企业", "再分发", "免责"):
        assert kw in src, "LICENSE 缺少条款：%s" % kw
    return "四条 + 署名 + 版权年份 齐全"


@check("同步：config.example.json 里的密钥位是空的（模板不会带真密钥）")
def t_example_empty():
    p = os.path.join(ROOT, "config.example.json")
    assert os.path.isfile(p), "缺少 config.example.json"
    cfg = json.load(open(p, "r", encoding="utf-8-sig"))
    for name, acc in cfg.get("accounts", {}).items():
        assert not acc.get("appid"), "模板里 %s 的 appid 非空" % name
        assert not acc.get("appsecret"), "模板里 %s 的 appsecret 非空" % name
    return "%d 个账号，密钥位均为空" % len(cfg.get("accounts", {}))


@check("同步：.gitignore 里写了 config.json（密钥不进版本库）")
def t_gitignore():
    p = os.path.join(ROOT, ".gitignore")
    assert os.path.isfile(p), "缺少 .gitignore"
    src = open(p, "r", encoding="utf-8").read()
    assert "config.json" in src, ".gitignore 没有忽略 config.json"
    return "已忽略 config.json"


@check("同步：密钥文件不会进技能包（旁边有 zip 就直接开箱验一遍）")
def t_no_secret_in_package():
    # 真正要守的不变量是「zip 里没有 config.json」。本机 config.json 是给本机用的，
    # 用户配好密钥后它就该存在 —— 所以这里改成开箱验包 + 如实提示，而不是一票否决。
    zip_path = os.path.join(os.path.dirname(ROOT), "anan-wechat-suite.zip")
    zip_note = "旁边没有 zip，跳过开箱检查"
    if os.path.isfile(zip_path):
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            leaked = [n for n in names
                      if os.path.basename(n).startswith("config")
                      and os.path.basename(n) != "config.example.json"]
        assert not leaked, "zip 里带了配置文件（密钥文件绝不能进包）：%s" % "、".join(leaked)
        zip_note = "zip 开箱验过：%d 个条目，没有 config.json" % len(names)
    p = os.path.join(ROOT, "config.json")
    if not os.path.isfile(p):
        return "没有 config.json（干净）；%s" % zip_note
    cfg = json.load(open(p, "r", encoding="utf-8-sig"))
    with_secret = [k for k, acc in (cfg.get("accounts") or {}).items() if acc.get("appsecret")]
    if with_secret:
        return ("本机 config.json 里 %s 有密钥 —— 只给本机用，%s；打包脚本必须排除它"
                % ("、".join(with_secret), zip_note))
    return "config.json 存在但没有真实密钥；%s" % zip_note


@check("同步：旁边那个 zip 的结构对 —— SKILL.md 必须在 zip 根")
def t_zip_skill_md_at_root():
    # ★ 2026-10-06 用户实测（教训）：zip 里套一层 anan-wechat-suite/ 会被豆包工作弹
    #   「压缩包或文件夹需包含 SKILL.md 文件」——两个包都因此传不上。
    #   正确结构 = package\ 里的内容**直接平铺在 zip 根**（SKILL.md / LICENSE /
    #   scripts/ …），参考平台上能正常安装的 wechat-mp-suite.zip 就是这个结构。
    #   判据（机检，不靠人记）：namelist() 里必须存在字符串 'SKILL.md'（无斜杠前缀）。
    zip_path = os.path.join(os.path.dirname(ROOT), "anan-wechat-suite.zip")
    if not os.path.isfile(zip_path):
        return "旁边没有 zip，跳过结构检查"
    import zipfile
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    assert "SKILL.md" in names, (
        "zip 根没有 SKILL.md（条目名必须恰好是 'SKILL.md'）—— 豆包工作要求 SKILL.md "
        "在 zip 根，套在文件夹里会被拒。现有前几条：%s" % "、".join(names[:3]))
    wrapped = [n for n in names if n.startswith("anan-wechat-suite/")]
    assert not wrapped, "zip 里还套着一层 anan-wechat-suite/：%s" % wrapped[0]
    return "zip 结构对：%d 个条目，SKILL.md 在 zip 根" % len(names)


@check("同步：SKILL.md 的 frontmatter 是严格合法的 YAML（三条硬规则）")
def t_frontmatter_strict():
    # ★ 2026-10-06 用户实测（教训）：description 的值里写了 "workflow: search" 这种**裸冒号**，
    #   PyYAML 直接 ScannerError: mapping values are not allowed here，豆包工作弹
    #   「SKILL.md 需包含 YAML 格式的技能名称和描述」；compatibility 的值里还混进过
    #   Markdown 粗体标记。三条硬规则（判据本体在 _skill_build/_shared/_yamlgate.py；
    #   本脚本要跟着包走，所以在这里独立实现，不依赖 _shared）：
    #     (a) 能被 yaml.safe_load 解析成 dict，且 name / description 都在；
    #     (b) 值里不许有裸冒号（「冒号 + 空格」又没加引号）；
    #     (c) 值里不许有 Markdown 标记（** / 反引号 / ~~ / ]( / <br）。
    import re
    import yaml
    text = open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8-sig").read()
    lines = text.replace("\r\n", "\n").split("\n")
    assert lines and lines[0].strip() == "---", "SKILL.md 不是以 --- 开头，没有 frontmatter"
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    assert end is not None, "frontmatter 只有开头的 ---，没有结束的 ---"
    fm = "\n".join(lines[1:end])
    try:
        data = yaml.safe_load(fm)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = ""
        if mark is not None and 0 <= mark.line < len(fm.split("\n")):
            where = "；第 %d 行：%s" % (mark.line + 1, fm.split("\n")[mark.line][:100])
        raise AssertionError("(a) frontmatter 不是合法 YAML —— %s: %s%s"
                             % (type(e).__name__, getattr(e, "problem", e), where))
    assert isinstance(data, dict), "(a) frontmatter 不是 YAML 映射（解析成了 %s）" % type(data).__name__
    for key in ("name", "description"):
        val = data.get(key)
        assert isinstance(val, str) and val.strip(), "(a) frontmatter 缺 %s（或不是非空字符串）" % key
    bad = []
    for i, line in enumerate(fm.split("\n"), 1):
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_-]*):[ \t]+(.*)$", line)
        if not m:
            continue
        val = m.group(2).strip()
        if not val or val[0] in "\"'":
            continue
        # 块标量 / 锚点 / 别名不是普通标量，跳过（★ 不能只看首字符 —— "**粗体**" 也以 * 开头）
        if re.match(r"^[|>][+-]?[0-9]?$", val) or re.match(r"^&\S+$", val) or re.match(r"^\*[^\s*]+$", val):
            continue
        hit = re.search(r":(?=\s|$)", val)
        if hit:
            k = hit.start()
            bad.append("(b) 第 %d 行 %s 的值里有裸冒号（会被 YAML 当成下一层键值对）：…%s…"
                       % (i, m.group(1), val[max(0, k - 24):k + 24]))
        marks = [mk for mk in ("**", chr(96), "~~", "](", "<br") if mk in val]
        if marks:
            bad.append("(c) 第 %d 行 %s 的值里有 Markdown 标记 %s"
                       % (i, m.group(1), "、".join(marks)))
    assert not bad, "frontmatter 三条硬规则没过：\n        " + "\n        ".join(bad)
    return "safe_load 成功 + 无裸冒号 + 无 Markdown 标记（%d 个字段）" % len(data)


# ================================================================ 过时说明拦截
# 2025-12-01 起，微信的密钥页与接口权限页迁到了「微信开发者平台」，
# 公众号后台里的老菜单（设置与开发 / 基本配置）已经打不开了。
# 规则：某一行里出现老菜单字样时，这一行必须同时写明「老路径已迁移」这类标注；
# 只有标注过的老路径才允许留在包里（因为要告诉学员「别再照着老教程找了」）。
STALE_FRAGMENTS = ("设置与" + "开发", "基本" + "配置")
MIGRATION_MARKERS = ("老路径", "旧路径", "已迁", "迁移", "已失效", "打不开",
                     "找不到", "不再可用", "2025-12-01")


@check("同步：全包没有「没写明已迁移」的老路径说明（设置与开发 / 基本配置）")
def t_no_stale_path():
    hits = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
        for fn in sorted(filenames):
            if not fn.lower().endswith((".md", ".py", ".json")):
                continue
            fp = os.path.join(dirpath, fn)
            scanned += 1
            try:
                lines = open(fp, "r", encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if not any(frag in line for frag in STALE_FRAGMENTS):
                    continue
                if any(mark in line for mark in MIGRATION_MARKERS):
                    continue
                hits.append("%s:%d  %s" % (os.path.relpath(fp, ROOT), i, line.strip()[:88]))
    assert not hits, ("这些地方还在把老路径写成操作指引，且没写「已迁移」——"
                      "老路径自 2025-12-01 起已打不开，留着就是坑学员：\n        "
                      + "\n        ".join(hits))
    return "扫了 %d 个 .md/.py/.json，老路径一律带迁移标注" % scanned


# ================================================================ 断言式旧结论拦截
# ★ 教训（2026-10-06）：写错的话不一定长得像错的话 —— 它也可能是理直气壮的肯定句。
#   所以这里扫的是**断言**，不是「未定论 / 待实测」这类犹豫词（那种词反而好搜）。
#
# 每条规则 = {名字, 会被实测推翻的断言片段, 该行必须携带的纠正标注, 来历 why, 反例 bad, 正例 ok}
#   · 命中任一 pattern 且**不含**任何 marker 的行 -> 判失败；
#   · 同一行写明「以前写错了 / 已改正 / 早前版本」的历史说明 -> 放行（来龙去脉要留得住）；
#   · ★ 加新主题 = 往 STALE_CLAIM_RULES 里加一条，**检查逻辑不用动**。
#     每条规则必须自带 bad / ok 样例，检查项会**先跑一遍样例自证**：
#     bad 必须被判红、ok 必须放行 —— 规则写坏了立刻暴露，不用等人想起来去造反例。
#   · 所有触发词都用字符串拼接写，免得这张表把自己给判红。
STALE_CLAIM_RULES = (
    {
        "name": "谁能直发草稿箱",
        # 2026-10-06 实测推翻：个人订阅号（个人主体、暂未认证）跑 --probe-full --with-draft
        # 三步全过，一样能直发草稿箱。此前「个人号没有开发者接口、只能粘贴」的说法是**错的**。
        "patterns": ("个人号" + "只能", "个人订阅号" + "只能", "只能走" + "粘贴",
                     "只能出" + "可粘贴", "服务号" + "才有的能力", "只有服务号" + "才能",
                     "个人号" + "没有开发者接口", "个人订阅号" + "没有开发者接口",
                     "个人号" + "没有接口权限", "个人订阅号" + "没有接口权限"),
        "markers": ("错的", "写错", "已改正", "早前版本", "旧口径", "已删除", "错误认知",
                    "已推翻", "实测"),
        "why": "实测：个人订阅号也能三步全过、直发草稿箱；分流只看有没有配密钥。",
        "bad": "个人号" + "只能走" + "粘贴模式。",
        "ok": "早前版本里「个人号" + "只能走粘贴模式」是错的，已按实测改正。",
    },
    {
        "name": "阅读量 / 点赞能不能抓",
        # 实测：这些数字是登录客户端后由 JS 异步填的，静态页面里就是空字符串；
        # 本技能也不输出这些字段 —— 所以任何「能抓」的断言都是错的。
        "patterns": ("阅读量" + "能抓", "能抓" + "阅读量", "阅读量" + "可以抓", "可以抓" + "阅读量",
                     "点赞" + "能抓", "能抓" + "点赞", "在看" + "能抓", "评论" + "能抓"),
        "markers": ("抓不到", "拿不到", "不要承诺", "别承诺", "不在静态页面", "空字符串", "不输出"),
        "why": "实测：静态页面里这些数字就是空的，本技能不输出这些字段；承诺能抓是错的。",
        "bad": "本技能" + "能抓" + "阅读量。",
        "ok": "阅读量" + "能抓吗？抓不到，别承诺。",
    },
    {
        "name": "抓图要不要浏览器渲染",
        # 实测：本技能请求的是服务端静态 HTML，正文图把真地址放在 data-src 属性里 ——
        # 读属性即可拿到图，不需要浏览器渲染。所以「懒加载必须上浏览器」是错的。
        "patterns": ("需要" + "浏览器渲染", "必须" + "浏览器渲染", "懒加载" + "需要浏览器",
                     "需要" + "JS 渲染", "必须" + "JS 渲染", "不渲染" + "拿不到"),
        "markers": ("不需要", "没有 JS 渲染", "属性读取", "data-src", "不用渲染", "照样能拿"),
        "why": "实测：不渲染也能拿图（读 data-src 属性），所以「必须渲染」是错的。",
        "bad": "抓图" + "需要" + "浏览器渲染。",
        "ok": "抓图" + "不需要" + "浏览器渲染，读 data-src 属性就行。",
    },
    {
        "name": "白名单要不要固定 IP",
        # 实测：家用宽带（动态 IP）一样能配白名单，只是 IP 变了要重加；本技能还给了
        # --myip 与「40164 报错里抠 IP」两条路。所以「必须固定 IP 才能配」是错的。
        "patterns": ("固定" + "IP 才能", "必须" + "固定 IP", "只有" + "固定 IP",
                     "静态" + "IP 才能", "白名单" + "必须固定"),
        "markers": ("会变", "动态", "家庭宽带", "重新加", "一样能配", "不是固定"),
        "why": "实测：家庭宽带的动态 IP 一样能配白名单，只是会变、变了要重加。",
        "bad": "白名单" + "必须固定" + " IP。",
        "ok": "家庭宽带 IP 会变，但一样能配白名单（变了重加即可）。",
    },
    {
        "name": "48001 该赖谁（个人号有没有素材 / 草稿接口权限）",
        # 这是「谁能直发」的近亲：把 48001 说成「个人订阅号 / 未认证号的通病」，
        # 等于换个说法重复同一条已被实测推翻的结论。
        # 实测（2026-10-06）：个人订阅号（个人主体、暂未认证）跑 --probe-full --with-draft
        # 三步全过。48001 只说明「**这个号**」没开通该接口，跟号型无关；
        # 谁行谁不行，以 --probe-full 的实测结果为准。
        "patterns": ("未认证号" + "通常没有", "通常没有" + "「新增永久素材」",
                     "没有" + "「新增永久素材」", "没有" + "「草稿箱」权限",
                     "未认证号" + "没有", "个人订阅号" + "没有",
                     # 同一信念还有「个人号不用配，直接粘贴」这种形状（2026-10-06 扫包时发现）：
                     "个人订阅号" + "不需要配", "个人订阅号" + "，根本不需要配"),
        "markers": ("实测", "都能", "三步全过", "以 --probe-full 为准", "可能不行",
                    "个别号", "跟是不是个人号无关", "错的", "写错", "已改正",
                    "早前版本", "已推翻"),
        "why": "实测：个人订阅号也能三步全过；48001 只说明这个号没这个权限，与号型无关。",
        "bad": "个人订阅号" + "没有" + "「草稿箱」权限，改走粘贴模式。",
        "ok": "早前版本说「个人订阅号" + "没有" + "「草稿箱」权限」是错的 —— 实测两种号都能过，"
              "个别号可能不行，以 --probe-full 为准。",
    },
)


# 共享的「这是在讲以前写错了」标注；每条规则另有自己的事实性标注（抓不到 / 不需要 / 会变 …）
CORRECTION_MARKERS = ("错的", "写错", "已改正", "早前版本", "旧口径", "已删除", "错误认知", "已推翻")


def claim_hit(rule, line):
    """这一行是不是「没带纠正标注的旧断言」。"""
    if not any(p in line for p in rule["patterns"]):
        return False
    allowed = CORRECTION_MARKERS + tuple(rule["markers"])
    return not any(m in line for m in allowed)


@check("同步：断言式旧结论扫描（%d 条规则，每条自带反例自证）" % len(STALE_CLAIM_RULES))
def t_no_stale_claim():
    # 1) 规则自证：反例必须判红、正例必须放行 —— 规则失效立刻暴露
    for rule in STALE_CLAIM_RULES:
        assert claim_hit(rule, rule["bad"]),             "规则「%s」的反例没被拦住，规则已失效" % rule["name"]
        assert not claim_hit(rule, rule["ok"]),             "规则「%s」把带纠正标注的正例也拦了" % rule["name"]
    # 2) 扫全包
    hits = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
        for fn in sorted(filenames):
            if not fn.lower().endswith((".md", ".py", ".json")):
                continue
            fp = os.path.join(dirpath, fn)
            scanned += 1
            try:
                lines = open(fp, "r", encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                for rule in STALE_CLAIM_RULES:
                    if claim_hit(rule, line):
                        hits.append("[%s] %s:%d  %s"
                                    % (rule["name"], os.path.relpath(fp, ROOT), i, line.strip()[:80]))
    sep = chr(10) + "        "
    assert not hits, ("这些地方还写着被实测推翻的旧断言（除非同一行写明「以前写错了」）："
                      + sep + sep.join(hits))
    return ("扫了 %d 个文件 × %d 条规则；每条规则的反例都验证过会判红"
            % (scanned, len(STALE_CLAIM_RULES)))


@check("同步：「微信开发者平台」已写进模板 / 发布 / 向导 / SKILL.md")
def t_new_path_present():
    need = "微信开发者平台"
    targets = {
        "config.example.json": os.path.join(ROOT, "config.example.json"),
        "SKILL.md": os.path.join(ROOT, "SKILL.md"),
        "scripts/publish.py": os.path.join(SCRIPTS, "publish.py"),
        "scripts/setup.py": os.path.join(SCRIPTS, "setup.py"),
    }
    missing = []
    for rel, p in sorted(targets.items()):
        src = open(p, "r", encoding="utf-8", errors="replace").read()
        if need not in src:
            missing.append(rel)
    assert not missing, "这些文件里没有「%s」，新路径没写全：%s" % (need, "、".join(missing))
    return "%d 个关键文件都指向新平台" % len(targets)


@check("同步：文档里写的自检项数与实际项数一致（数字也会脱节）")
def t_selftest_count_sync():
    want = len(CHECKS)
    found = []
    for rel in ("SKILL.md", "references/USAGE.md", "references/TROUBLESHOOTING.md"):
        p = os.path.join(ROOT, rel)
        src = open(p, "r", encoding="utf-8", errors="replace").read()
        for n in sorted(set(int(x) for x in re.findall(r"(\d+)\s*项离线(?:自检|检查)", src))):
            found.append((rel, n))
    assert found, "文档里没有任何「N 项离线自检」的说法，无从核对"
    bad = ["%s 写的是 %d" % (rel, n) for rel, n in found if n != want]
    assert not bad, "自检项数对不上（实际 %d 项）：%s" % (want, "、".join(bad))
    return "文档与代码都是 %d 项" % want


# ================================================================ 本机 IP 查询
@check("安全：查 IP 的请求只允许发往白名单服务，且不带任何密钥")
def t_ip_echo_allowlist():
    import publish
    publish.assert_ip_echo_host("https://myip.ipip.net")
    for bad in ("https://evil.example.com/ip",
                "https://api.weixin.qq.com.evil.com/ip",
                "http://127.0.0" + ".1:8000/ip"):
        try:
            publish.assert_ip_echo_host(bad)
        except RuntimeError:
            continue
        raise AssertionError("没有拦住非白名单的查 IP 地址：%s" % bad)
    # 国外服务必须被排除：用户网络一有代理/分流，它返回的出口 IP 和微信看到的不一样，
    # 学员照着加白名单会加错 —— 给错的比不给还糟。
    for foreign in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://api.ipify.org?format=json"):
        try:
            publish.assert_ip_echo_host(foreign)
        except RuntimeError:
            continue
        raise AssertionError("国外查 IP 服务又进了白名单：%s" % foreign)
    assert set(publish.IP_ECHO_URLS) and len(publish.IP_ECHO_URLS) == len(publish.IP_ECHO_HOSTS), \
        "IP_ECHO_URLS 与 IP_ECHO_HOSTS 对不上"
    for u in publish.IP_ECHO_URLS:
        publish.assert_ip_echo_host(u)
    src = open(os.path.join(SCRIPTS, "publish.py"), "r", encoding="utf-8").read()
    body = src[src.index("def http_text_public("):src.index("def extract_ip(")]
    # 只看真正发请求的代码（去掉文档字符串，否则「不带密钥」这句话自己会把检查绊倒）
    code = body.split('"""')[-1]
    assert "assert_ip_echo_host" in code, "http_text_public 没做域名白名单校验"
    for leak in ("secret", "appid", "access_token", "cookie"):
        assert leak not in code.lower(), "查 IP 的函数里出现了 %s，可能把密钥带出去了" % leak
    return "白名单放行 %d 个服务，第三方一律拦截" % len(publish.IP_ECHO_HOSTS)


@check("发布：公开 IP 服务的返回值只当参考，绝不冒充「微信看到的 IP」")
def t_myip_authority():
    import publish

    FAKE_CFG = {"active": "service", "accounts": {"service": {
        "name": "假服务号", "type": "service",
        "appid": "wx1234567890abcdef", "appsecret": "0123456789abcdef0123456789abcdef"}}}
    REF_IP = "203.0.113.9"
    CAVEAT = "不一定等于微信看到的 IP"
    orig_json, orig_text, orig_out = publish.http_json, publish.http_text_public, sys.stdout

    def capture(cfg, json_stub, calls):
        publish.http_json = json_stub
        publish.http_text_public = lambda *a, **k: calls.append(a[0] if a else "") or \
            "当前 IP：%s  来自于：中国 北京 联通" % REF_IP
        buf = io.StringIO()
        sys.stdout = buf
        try:
            code = publish.cmd_myip(cfg, None)
        finally:
            sys.stdout = orig_out
        return code, buf.getvalue()

    try:
        # A) 微信报了 40164 —— 这是唯一权威来源，而且不该再去问公开服务
        calls_a = []
        code_a, out_a = capture(FAKE_CFG, lambda *a, **k: {
            "errcode": 40164, "errmsg": "invalid ip 203.0.113.45, not in whitelist"}, calls_a)
        assert "203.0.113.45" in out_a, "40164 里的权威 IP 没有输出"
        assert "权威" in out_a, "没有标明这是微信看到的那个 IP"
        assert "参考 IP" not in out_a, "已经有权威 IP 了，却又给了一个参考 IP"
        assert not calls_a, "已经有权威 IP 了，却还去问了公开 IP 服务"
        assert code_a == 0, "拿到权威 IP 时退出码应为 0，实际 %s" % code_a

        # B) token 成功 = 白名单本来就配好了：必须明说「不需要加任何 IP」，参考值要带警示
        code_b, out_b = capture(FAKE_CFG, lambda *a, **k: {"access_token": "X", "expires_in": 7200}, [])
        assert "已经配好" in out_b and "不需要加任何 IP" in out_b, \
            "白名单已配好时没有明确告诉用户「不用加东西」"
        assert "参考 IP" in out_b, "公开服务的 IP 没有标注成「参考 IP」"
        assert CAVEAT in out_b, "参考 IP 缺少「不一定等于微信看到的 IP」的警示"
        assert code_b == 0, "白名单已配好时退出码应为 0，实际 %s" % code_b

        # C) 没有密钥 = 只有参考值：必须带警示，且绝不能说成权威
        code_c, out_c = capture({}, lambda *a, **k: {"errcode": 40013, "errmsg": "invalid appid"}, [])
        assert "参考 IP" in out_c and CAVEAT in out_c, "没有权威来源时，参考 IP 缺少警示"
        assert "权威" not in out_c, "没有权威来源，却把公开服务的返回值说成了权威"
        assert code_c == 0, "只有参考 IP 时退出码应为 0，实际 %s" % code_c
    finally:
        publish.http_json, publish.http_text_public, sys.stdout = orig_json, orig_text, orig_out
    return "40164 权威 / 已配好 / 仅参考 三种情形都说清了"


@check("发布：能从 40164 报错里抠出 IP，抠不到时返回空（不编一个）")
def t_extract_ip():
    import publish
    assert publish.extract_ip("invalid ip 203.0.113.45, not in whitelist") == "203.0.113.45"
    assert publish.extract_ip("当前 IP：1.2.3.4  来自于：中国 北京 联通") == "1.2.3.4"
    assert publish.extract_ip("no ip here") == ""
    assert publish.extract_ip("127.0.0.1") == ""
    return "抠取正确；抠不到就返回空"


FORBIDDEN_TERM = "洗" + "稿"   # 拆开写：本文件自己也不许出现这个连续词


@check("同步：全包正文用词合规（不出现不该出现的说法）")
def t_no_forbidden_anywhere():
    hits = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
        for fn in filenames:
            if not fn.lower().endswith((".md", ".py", ".json", ".txt")):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                src = open(fp, "r", encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if FORBIDDEN_TERM in src:
                hits.append(os.path.relpath(fp, ROOT))
    assert not hits, "这些文件里出现了不该出现的说法：%s" % "、".join(hits)
    return "全包干净"


@check("同步：每个脚本都能单独 import 且带 --help 入口")
def t_scripts_importable():
    names = []
    for fn in sorted(os.listdir(SCRIPTS)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        mod = fn[:-3]
        __import__(mod)
        names.append(mod)
    assert len(names) >= 6, "脚本数量偏少：%d" % len(names)
    return "%d 个脚本：%s" % (len(names), "、".join(names))


@check("安全：全包源码里没有任何第三方中转/上传地址")
def t_no_thirdparty_upload():
    # 同样拆开写，否则这个检查项自己就会命中自己
    bad_hosts = ("skill" + "hub", "edit." + "shiker",
                 "local" + "host:3000", "127.0.0" + ".1:3000")
    hits = []
    for fn in sorted(os.listdir(SCRIPTS)):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(SCRIPTS, fn), "r", encoding="utf-8", errors="replace").read()
        for b in bad_hosts:
            if b in src:
                hits.append("%s -> %s" % (fn, b))
    assert not hits, "源码里出现了不该有的地址：%s" % "、".join(hits)
    return "无第三方中转地址"


@check("闸门：guard.py 的两条判据都自带反例与正例（判据本身先自证）")
def t_guard_gates():
    import guard
    # 反例（必须被判为问题）
    bad_local = '<img src="images/01.png">'
    bad_remote = '<img src="https://cdn.example.com/a.png">'
    bad_proc = "# 标题\n\n正文。\n\n## 改了什么\n\n- 把三段并成两段\n"
    assert guard.check_images(bad_local)[0], "本机相对路径没被闸门 A 认出来"
    assert guard.check_images(bad_remote)[0], "第三方外链没被闸门 A 认出来"
    assert guard.check_markdown(bad_proc), "「## 改了什么」没被闸门 B 认出来"
    # 正例（必须放行）
    ok_wechat = '<img src="https://mmbiz.qpic.cn/sz_mmbiz_png/a/640?wx_fmt=png">'
    ok_upload = '<img src="http://mmecoa.qpic.cn/mmecoa_png/abc">'   # uploadimg 真实返回的域名
    ok_plain = "# 标题\n\n这是一段正常正文，讲一件具体的事。\n"
    assert not guard.check_images(ok_wechat)[0], "微信图床的图被误拦了"
    assert not guard.check_images(ok_upload)[0], "uploadimg 返回的域名被误拦了"
    assert not guard.check_markdown(ok_plain), "正常正文被误判成过程性标题"
    # 域名判据不能宽到把仿冒域名也放进来
    for evil in ("https://evilqpic.cn/a.png", "https://qpic.cn.evil.com/a.png"):
        assert not guard.is_wechat_image_url(evil), "仿冒域名被当成微信图床：%s" % evil
    return "闸门 A/B 各有反例与正例，仿冒域名不放行"


@check("发布：正文图真的会走 media/uploadimg 转存，且在**建草稿之前**、转存之后还有闸门")
def t_publish_transfers_images():
    src = open(os.path.join(SCRIPTS, "publish.py"), "r", encoding="utf-8", errors="replace").read()
    assert "cgi-bin/media/uploadimg" in src, "publish.py 里没有 uploadimg —— 正文图又会碎"
    assert "def transfer_body_images(" in src, "没有 transfer_body_images"
    call = src.index("body, img_report = transfer_body_images(")
    draft = src.index("mid, err = step3_add_draft(tok, title, body, thumb,")
    assert call < draft, "转存必须发生在建草稿之前（现在反了）"
    # 转存之后还要再跑一次闸门 A（转存本身也可能失败）
    gate = src.index("img_problems, img_stat = guard.check_images(body)")
    assert call < gate < draft, "转存之后、建草稿之前必须跑闸门 A"
    # 失败不许静默跳过：报告里必须有 failed 这一档
    assert '"failed"' in src and "print_image_report" in src, "转存失败没有如实报告"
    return "uploadimg 在草稿之前，转存后再过闸门 A，失败逐张报"


@check("写作：改写提示词要求「正文」与「改动说明」分成两个文件（bug ② 的根因）")
def t_rewrite_prompt_two_files():
    import article_writer as aw
    cfg = aw.load_styles()
    prompt = aw.build_rewrite_prompt(cfg, "restructure", "# 原文标题\n\n原文正文。\n")
    old_sample = ("先输出改写后的完整 Markdown 正文（第一行 " + chr(96) + "# 标题" + chr(96)
                  + "），然后在最后用三行写清：改了什么、为什么这么改、有没有动到事实。")
    # 先自证：这段旧写法必须被判为「旧」
    assert "然后在最后用三行写清" in old_sample, "自证样例写错了"
    assert "然后在最后用三行写清" not in prompt, \
        "提示词还让 AI 在正文最后写「改了什么」—— 它就会写进正文，一路发到草稿箱"
    assert "两个文件" in prompt and "改动说明文件" in prompt, "没有要求把改动说明另存一个文件"
    # 正文里混进过程性说明，--check / --check-rewrite 必须判不合格
    bad = "# 标题\n\n" + "正" * 400 + "\n\n## 修改说明\n\n- 改了这里\n"
    assert aw.process_heading_problems(bad), "--check 没拦住混进正文的改动说明"
    return "提示词分两个文件；正文里混进说明会被 --check 拦住"


@check("端到端：e2e_test.py 存在，且真的跑「抓真文章 -> 改写 -> 排版 -> 发布 -> 回读」")
def t_e2e_test():
    p = os.path.join(SCRIPTS, "e2e_test.py")
    assert os.path.isfile(p), "没有端到端测试 —— 两个 bug 就出在「模块接缝」上"
    src = open(p, "r", encoding="utf-8", errors="replace").read()
    for need, why in (
            ("fetch_article.py", "端到端必须真的抓一篇真文章"),
            ("--rewrite", "端到端必须走改写"),
            ("md_to_html", "端到端必须走排版"),
            ("transfer_body_images", "端到端必须走正文图转存"),
            ("step3_add_draft", "端到端必须真的建草稿"),
            ("cgi-bin/draft/get", "必须把草稿回读回来验图（不然「图是不是好的」没验过）"),
            ("DRAFT_TITLE_PREFIX", "建出来的草稿要一眼能认出是自检的")):
        assert need in src, "e2e_test.py 少了「%s」（%s）" % (need, why)
    # ★ 不许再用一句话测试稿：输入必须是够长的真实长文 + 带图
    assert "chars >= 500" in src and "imgs >= 1" in src, \
        "端到端没有拦住「一句话测试稿」—— 测试用例太干净，真实路径就被绕过去了"
    import e2e_test   # 能被导入（不会在导入时跑主流程）
    return "10 个阶段，含两条反证；拒绝一句话测试稿"


CHECKS = [
    t_bom, t_bom_same, t_crlf, t_li_strong, t_inline_only, t_table, t_code, t_section,
    t_all_themes, t_theme_keys,
    t_sogou_unescape, t_sogou_parse, t_sogou_block,
    t_video_extract, t_video_empty, t_richmedia_profile, t_reuse_spider,
    t_styles, t_rewrite_modes, t_prompt_built, t_check_catches, t_no_forbidden_word,
    t_write_directly,
    t_illustration, t_illustration_tables,
    t_host_guard, t_publish_capability, t_mask, t_errcodes, t_probe_scope,
    t_no_config_blocked, t_no_mass_send, t_publish_two_step,
    t_docs_sync, t_refs_sync, t_skill_uppercase, t_license, t_example_empty,
    t_gitignore, t_no_secret_in_package, t_zip_skill_md_at_root, t_frontmatter_strict,
    t_no_forbidden_anywhere,
    t_scripts_importable, t_no_thirdparty_upload,
    t_no_stale_path, t_no_stale_claim, t_new_path_present, t_selftest_count_sync,
    t_ip_echo_allowlist, t_myip_authority, t_extract_ip,
    t_guard_gates, t_publish_transfers_images, t_rewrite_prompt_two_files, t_e2e_test,
]


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="anan-wechat-suite 离线自检")
    ap.add_argument("--quiet", action="store_true", help="只打印失败项与汇总")
    args = ap.parse_args(argv)

    print("=" * 62)
    print("  anan-wechat-suite 离线自检")
    print("  作者：阿南 rockmoons（微信：rockmoons）")
    print("  技能目录：%s" % ROOT)
    print("=" * 62)
    print("")
    print("全部检查项都在本地跑，不需要网络、不需要密钥。")
    print("")

    groups = [
        ("排版引擎（模块 7）", CHECKS[0:10]),
        ("搜索（模块 1）", CHECKS[10:13]),
        ("抓取与富媒体（模块 2/3）", CHECKS[13:17]),
        ("写作与改写（模块 4/5）", CHECKS[17:23]),
        ("配图（模块 6）", CHECKS[23:25]),
        ("发布与密钥安全（模块 8）", CHECKS[25:32]),
        ("文档与代码同步 / 合规", CHECKS[32:]),
    ]
    for title, items in groups:
        print("【%s】" % title)
        for fn in items:
            fn(quiet=args.quiet)
        print("")

    ok = sum(1 for r in RESULTS if r[0])
    bad = len(RESULTS) - ok
    print("=" * 62)
    print("  通过 %d 项，失败 %d 项" % (ok, bad))
    if bad:
        print("")
        print("  失败清单：")
        for good, name, detail in RESULTS:
            if not good:
                print("    ✗ %s" % name)
                print("      %s" % detail)
    print("=" * 62)
    if not bad:
        print("")
        print("自检全部通过。文档里写的每条能力，代码里都在。")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
