#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发布（双轨）—— anan-wechat-suite 模块 8
==========================================
支持三种账号形态，自动探测能力后选路：

    服务号 / 认证号（有接口权限）
        -> 直调微信官方 API：
           GET  /cgi-bin/token                     换 access_token
           POST /cgi-bin/media/uploadimg           正文图逐张转存到微信图床，拿 url 换掉 src
           POST /cgi-bin/material/add_material     上传封面为永久素材，拿 thumb_media_id
           POST /cgi-bin/draft/add                 建草稿
           POST /cgi-bin/freepublish/submit        发布（**只在用户确认后**才走这一步）
        （对外仍叫「三步」：1 换 token / 2 上传素材（正文图 + 封面）/ 3 建草稿。）

★ 模块接缝处的隐含假设（写在这里，下一个人不用猜）：
    上游（抓取 / 改写 / 排版）交给发布的是**一份 Markdown**：
      · 图片写成本地相对路径（images/01.png）或任意外链 —— 上游不管图能不能被微信取到；
      · 正文里**只有文章本身**，不含「改了什么 / 修改说明」这类过程性内容。
    发布这一步负责把它们兑现：
      · **正文图全部转存到微信图床并把 src 换成微信的 url**（见 transfer_body_images）。
        为什么必须做：草稿里的 <img src="images/01.png"> 微信根本取不到 —— 就是碎图。
        粘贴模式不需要这一步（浏览器复制会把图片数据一起带上去），所以那条路不拦。
      · **发布前跑 guard.py 的两条机械闸门**：图必须都在微信图床 / 正文不许有过程性标题。
        任何一条不过就停下报清楚，**绝不"先发出去再说"**。
    这两条都是**踩过的坑**，不是预防性设计：2026-10-06 用户拿真文章跑完整流程，
    草稿箱里就是「所有图碎掉」+「改动说明混进正文」。

    ★ 三档要分清（用户最容易混）：
        存草稿 = draft/add            -> 只有你自己在后台看得见
        发布   = freepublish/submit   -> 所有关注者看得见（**不可撤销**）
        群发   = 推送给粉丝           -> **本脚本永远不做**：
                 ① 打扰粉丝 ② 不可撤销 ③ 一天只有 1 次额度。
                 这是有意的，不是漏了；自检里有一项专门盯着「任何脚本都不许出现群发接口」。

    ★ 发布这一步是**两步走**，不许一条命令闷头发出去：
        --publish               建草稿 + 把「标题 / 封面 / 摘要」报出来 + 停下
        --publish-go <media_id> 确认无误后才真发（复用刚建好的草稿，不重复建）

    两条路，按「有没有配 AppID / AppSecret」分流（**跟号型无关**）：
        · 配了密钥  -> 官方 API 三步：换 token -> 传封面 -> 建草稿，**直发草稿箱**。
                      2026-10-06 实测：个人订阅号（个人主体、未认证）同样三步全过。
        · 这一格明确选了粘贴（配置里 type=paste）
                    -> 生成排版好的 HTML，你复制粘贴到公众号后台 ——
                      **永远可用的保底**（写稿/改写/配图提示词/排版全自动，只有最后一步手动粘贴）。

    ★ 这台机器上**还没配置**（没有 config.json）时跑发布：
      **不会**悄悄降级成粘贴模式，而是明确报错并把下一步直接给出来
      （「跑这条开始配置：python scripts/setup.py」，外加显式走粘贴的命令）。
      这一层是**不依赖 AI 有没有读 SKILL.md** 的保险：想发布，就得先知道自己还没配。
      只想排版、先走粘贴：显式加 --mode paste（它永远可用，不需要任何密钥）。

    配置写错 / 权限不足 / IP 不在白名单
        -> **明确报错、退出码非 0、给出下一步怎么办**，绝不静默失败、绝不假装成功。

★ 密钥安全（A 方案，本技能的红线）
    - AppID / AppSecret 只存在**本机** config.json，该文件在 .gitignore 里。
    - 密钥**只发往 api.weixin.qq.com 这一个官方域名**，脚本里硬校验，
      任何其它域名一律拒绝发送（见 assert_official_host）。
    - 不上传、不转发、不写进任何日志。日志里的密钥一律打码。

用法：
    python scripts/publish.py --myip                        # 打印本机出口 IP 与该去哪儿加白名单
    python scripts/publish.py --check                       # 体检：看配置与账号能力
    python scripts/publish.py --probe                       # 只验第 1 步：换 token（过了≠能发文章）
    python scripts/publish.py --probe-full                  # 逐步验：换 token → 上传测试小图
    python scripts/publish.py --probe-full --with-draft     # 再验建草稿（草稿箱会留一篇测试草稿）
    python scripts/publish.py article.md --mode auto        # 自动选路
    python scripts/publish.py article.md --mode paste       # 只出可粘贴 HTML
    python scripts/publish.py article.md --mode api --cover images/cover.jpg --dry-run
    python scripts/publish.py article.md --mode api --cover images/cover.jpg --publish
    python scripts/publish.py --publish-go <上一步给的 media_id>          # 确认后才真发

退出码：
    0 = 成功（paste 模式下表示 HTML 已生成；api 模式下表示草稿已建；--myip 表示拿到了 IP）
    1 = 失败（没配置 / 配置写错 / 权限不足 / IP 白名单 / 网络错误…，原因与下一步会打在屏幕上）
    2 = 参数错误
"""

import argparse
import base64
import html as html_mod
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(ROOT, "config.json")
EXAMPLE_PATH = os.path.join(ROOT, "config.example.json")
BOM = chr(0xFEFF)

API_BASE = "https://api.weixin.qq.com"
# ★ 密钥只允许发往这个域名。改这里等于改安全边界，别乱改。
OFFICIAL_HOSTS = ("api.weixin.qq.com",)

# ---------------------------------------------------------------- 正文图转存
# 官方接口：POST /cgi-bin/media/uploadimg?access_token=xxx，multipart 字段 media，返回 {"url": ...}
# 官方文档「上传发表内容中的图片」逐字写着：**图片仅支持 jpg/png 格式，大小必须在 1MB 以下**，
# 错误码 40005 invalid file type / 40009 invalid image size。
# 这个接口传的图**不占素材库 10 万张的额度**（官方原话），所以正文图走它、不走 add_material。
UPLOADIMG_URL = API_BASE + "/cgi-bin/media/uploadimg"
UPLOADIMG_MAX_BYTES = 1024 * 1024                 # 1MB，官方上限
UPLOADIMG_FORMATS = ("jpg", "png")                # 官方只收这两种

# ★ 下载外链 / 原图时**故意不要 webp**。
#   踩过的坑：抓取器（wechat_spider.py）发的是 image/webp，微信 CDN 就把原图回成 webp；
#   而 uploadimg 只收 jpg/png，转存时才发现图用不了。这里主动声明只收 jpg/png/gif。
IMAGE_ACCEPT = "image/jpeg,image/png,image/gif,image/*;q=0.8,*/*;q=0.5"
IMAGE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024             # 单张外链图的下载上限，防呆

# ★ 只有 --myip 会访问这几个域名：查询「本机出口 IP」的公开服务，**只收国内服务**。
#   为什么不用 api.ipify.org 这类国外服务：用户网络一旦有代理/分流，
#   访问国外服务走的是另一条出口，返回的 IP 和微信看到的根本不是一个 —— 用户照着加白名单
#   会加错，加了还是通不了。**给一个错的 IP，比不给还糟。**
#   这些请求是裸 GET，**不带 AppID、不带 AppSecret、不带 cookie**；
#   带密钥的请求只允许发往 OFFICIAL_HOSTS。
IP_ECHO_HOSTS = ("myip.ipip.net", "ipservice.ws.126.net")
# 实测（2026-10，本机）：这两家返回的 IP 与微信 40164 报出的 IP 一致；
# 而 api.ipify.org / ifconfig.me 返回 188.253.123.153（国外出口），加了没用。
IP_ECHO_URLS = ("https://myip.ipip.net", "https://ipservice.ws.126.net/locate/api/getLocByIp")

# 微信常见错误码 -> 人话 + 下一步
ERRCODE_HELP = {
    40001: ("access_token 无效或已过期", "重跑一次即可；仍失败请检查系统时间是否准确。"),
    40013: ("AppID 无效", "核对 config.json 里的 appid，注意别把 AppSecret 填到 appid 上。"),
    40125: ("AppSecret 无效",
            "AppID / AppSecret 去「微信开发者平台 → 首页/服务号 → 选你的公众号 → 基础信息」重置"
            "（老路径「设置与开发 → 基本配置」2025-12-01 起已迁移，打不开了）。"
            "AppSecret 只显示一次，重置后马上复制，再跑 python scripts/setup.py 更新 config.json。"),
    40164: ("调用方 IP 不在白名单",
            "先跑 python scripts/publish.py --myip 拿到本机出口 IP，"
            "再加到「微信开发者平台 → 选你的公众号 → 基础信息 → API IP白名单」"
            "（老路径「基本配置 → IP 白名单」已随平台迁移，找不到了）。"
            "家庭宽带 IP 会变，变了就要重新加——这正是很多人改用「粘贴模式」的原因。"),
    41002: ("缺少 appid 参数", "检查 config.json 是否读到了 appid。"),
    45009: ("接口调用频率超限", "等一会儿再试；access_token 有配额，别写循环狂刷。"),
    48001: ("接口权限不足：这个号没有开通该接口",
            "报 48001 就是**这个号确实没有这个接口的权限**（不是脚本的问题）。"
            "先用 python scripts/publish.py --probe-full 逐步验，确认哪一步断了；"
            "确认没权限就改走粘贴模式（不需要任何接口权限）："
            "python scripts/publish.py 文章.md --mode paste"),
    48002: ("粉丝未关注 / 接口需要认证", "把账号主体升级为认证服务号，或改走粘贴模式。"),
    53500: ("草稿接口需要认证", "改走粘贴模式。"),
    87014: ("内容含违规信息被拦", "检查正文，去掉敏感内容后重试。"),
    40007: ("media_id 无效", "封面图重新上传一次。"),
    40005: ("文件类型不合法",
            "微信图片接口只收 jpg / png（uploadimg 与永久素材都是）。"
            "webp / gif / svg / avif 都会被拒 —— 先把图转成 jpg 或 png 再发。"),
    40009: ("图片尺寸/体积太大",
            "uploadimg 要求单张图在 1MB 以下（官方原话）。"
            "把这张图压到 1MB 以内（缩尺寸或降质量）再发；不要靠重试。"),
}


def log(m):
    print(m, flush=True)


_GUARD = None


def guard_mod():
    """取发布前闸门模块（guard.py）。

    延迟导入：publish.py 既能当脚本跑，也能被别的脚本 import，
    两种情况下本目录都不一定已经在 sys.path 上。
    """
    global _GUARD
    if _GUARD is None:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import guard as _g
        _GUARD = _g
    return _GUARD


def mask(s):
    """密钥打码：只留头尾，避免日志泄露。"""
    if not s:
        return "（空）"
    if len(s) <= 8:
        return s[0] + "*" * (len(s) - 1)
    return "%s...%s（共 %d 位）" % (s[:4], s[-4:], len(s))


def assert_official_host(url):
    """硬校验：只允许把请求（含密钥）发往微信官方域名。"""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host not in OFFICIAL_HOSTS:
        raise RuntimeError(
            "安全拦截：本脚本只允许把请求发往 %s，拒绝了发往 %s 的请求。\n"
            "这是为了防止密钥被外发到第三方中转服务。"
            % ("、".join(OFFICIAL_HOSTS), host or "(空)"))


def assert_ip_echo_host(url):
    """硬校验：查 IP 的请求只允许发往 IP_ECHO_HOSTS 里的公开服务。"""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host not in IP_ECHO_HOSTS:
        raise RuntimeError(
            "安全拦截：查 IP 只允许访问 %s，拒绝了发往 %s 的请求。"
            % ("、".join(IP_ECHO_HOSTS), host or "(空)"))


def http_text_public(url, timeout=8):
    """取一段纯文本（仅 --myip 用）。不带任何密钥、不带 cookie。"""
    assert_ip_echo_host(url)
    req = urllib.request.Request(url, headers={"User-Agent": "anan-wechat-suite/%s" % VERSION})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(4096)
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s %s" % (e.code, e.reason))
    except Exception as e:
        raise RuntimeError("%s: %s" % (type(e).__name__, e))
    return raw.decode("utf-8", errors="replace")


def extract_ip(text):
    """从一段文本里抠出 IP（先来先得）。抠不到返回空串，绝不猜。"""
    for m in re.finditer(r"[0-9A-Fa-f:.]{4,}", text or ""):
        cand = m.group(0).strip(".:")
        try:
            ip = ipaddress.ip_address(cand)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            continue
        return str(ip)
    return ""


# ---------------------------------------------------------------- 配置
def load_config(path=CONFIG_PATH):
    """读本机配置。返回 (config, 状态说明)。读不到不算致命——提示用户就行。"""
    if not os.path.isfile(path):
        return {}, "未找到配置文件 %s" % path
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        return {}, "配置文件不是合法 JSON（第 %d 行）：%s" % (e.lineno, e.msg)
    except OSError as e:
        return {}, "配置文件读取失败：%s" % e
    return cfg, ""


def active_account(cfg, name=None):
    """取当前账号。返回 (账号dict, 账号名, 说明)。"""
    accounts = cfg.get("accounts") or {}
    if not accounts:
        return {}, "", "配置里没有 accounts"
    key = name or cfg.get("active") or next(iter(accounts))
    if key not in accounts:
        return {}, key, "配置里没有名为 %s 的账号" % key
    return accounts[key], key, ""


# ---------------------------------------------------------------- type 词汇
# 主词汇是**路线**：
#     "api"   = 这一格走官方 API 直发草稿箱
#     "paste" = 这一格走粘贴模式（即使存着密钥也默认粘贴，--mode api 可覆盖）
# 旧词汇（说的是「什么号」）当**兼容别名**读进来，老配置照常能跑：
#     "service"  -> 等价于 "api"（老配置里它就是为了直发）
#     "personal" -> 等价于 "auto"（**不能映射成 paste**：老配置里它只是「个人号」这个标签，
#                   当年把它硬判成 paste 是建立在「个人号没有接口权限」这个错误认知上的；
#                   那已被实测推翻。映射成 auto = 未指定路线，仍按「有没有密钥」判断，
#                   于是老配置的行为与本次改动前完全一致 —— 有密钥照旧直发，没密钥照常粘贴。）
TYPE_ALIASES = {"service": "api", "personal": "auto"}
LEGACY_TYPES = tuple(TYPE_ALIASES)
API_ROUTE_TYPES = ("api", "service")      # 缺密钥时算「配置写错」，不是「没有权限」


KNOWN_TYPES = ("api", "paste", "auto")


def normalize_type(value):
    """把 type 归一到 {api, paste, auto}。旧词汇按 TYPE_ALIASES 映射，未知值一律 auto。"""
    raw = (value or "").strip().lower()
    return TYPE_ALIASES.get(raw, raw or "auto")


def type_note(value):
    """type 取值上的**温和提示**（只提示，不拦人）。正常取值返回空串。

    未知取值不让技能跑不起来（用户手改配置写错一个词，不至于整包不可用），
    但要在 --check 里看得见，好让他知道该重跑配置向导。
    """
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if raw in LEGACY_TYPES:
        return "（旧词汇，按 %s 处理）" % normalize_type(raw)
    if raw not in KNOWN_TYPES:
        return "（取值 %s 不认识，按「未指定」处理 —— 建议重跑 python scripts/setup.py）" % raw
    return ""


def account_capability(acc):
    """判断这个账号走哪条路。返回 (能力, 说明)。

    ★ **分流只看一件事：有没有配齐 appid + appsecret。**
      **号型不参与判断** —— 2026-10-06 实测：个人订阅号（个人主体、暂未认证）
      跑 --probe-full --with-draft 三步全过，同样能直发草稿箱。
      所以「个人订阅号没有开发者接口」这种说法是本技能早期写错的，已删除。

    "api"   = 有 appid + appsecret —— 直发草稿箱（换 token → 传封面 → 建草稿）
    "paste" = 没有密钥 —— 走粘贴模式（永远可用的保底：排版自动，最后一步手动粘贴）
    "error" = 这一格标的是「直发」却缺密钥（配置写错了，不是「没有权限」）
    "none"  = 没有账号配置
    """
    if not acc:
        return "none", "没有账号配置"
    atype = normalize_type(acc.get("type"))
    appid = (acc.get("appid") or "").strip()
    secret = (acc.get("appsecret") or acc.get("secret") or "").strip()
    missing = []
    if not appid:
        missing.append("appid")
    if not secret:
        missing.append("appsecret")
    if appid and secret:
        if atype == "paste":
            return "api", ("已配置 appid 与 appsecret，可以走官方接口；"
                           "但这一格标的是 paste —— **默认走粘贴模式**，加 --mode api 才走直发")
        return "api", "已配置 appid 与 appsecret —— 走官方 API 直发草稿箱"
    if atype in API_ROUTE_TYPES:
        return "error", "这一格配的是「直发草稿箱」却缺少：" + "、".join(missing)
    return "paste", ("未配置密钥（缺 %s）—— 走粘贴模式"
                     "（不需要任何接口权限；想改成直发就把 AppID / AppSecret 填进来）"
                     % "、".join(missing))


def no_config_help(action, why="", article=None, misconfig=False, paste_hint=False):
    """没配置时的统一报错：★ 必须把「下一步怎么办」直接给出来。

    这一层**不依赖 AI 读没读 SKILL.md** —— 用户想发布而机器上没配置，脚本自己拦住。
    为什么不能悄悄降级成粘贴模式：那样用户根本不知道还有「直发草稿箱」这条路，
    而 AI 的引导剧本只能靠自觉照做，做不到 100%。

    @param action 不能做的事（发布 / 探测），拼进第一句
    @param why account_capability() 给出的判定依据，空串则不打印
    @param article 文章路径；给了就补一条「显式走粘贴模式」的确切命令
    @param misconfig 配置存在但配错了（缺密钥），第一句改口径，其余一字不差
    @param paste_hint 是否补一句粘贴模式提示（文章路径已另给命令时不重复）
    """
    log("!! %s —— 不能%s。" % ("公众号配置不完整" if misconfig else "还没配置公众号", action))
    log("   这一步需要 AppID / AppSecret（或先选粘贴模式）。")
    log("")
    log("   跑这条开始配置：")
    log("     python scripts/setup.py")
    log("   （或告诉我「配置」，我来一步步问你）")
    if why:
        log("")
        log("   当前判定：%s" % why)
    if article:
        log("")
        log("   想先走粘贴模式（不需要任何密钥）：")
        log("     python scripts/publish.py \"%s\" --mode paste" % article)
    elif paste_hint:
        log("")
        log("   现在可以走的路线是粘贴模式（不需要任何密钥）：")
        log("     python scripts/publish.py 文章.md --mode paste")


# ---------------------------------------------------------------- HTTP
def http_json(url, data=None, method=None, timeout=30):
    """发 JSON 请求。url 必须过官方域名校验。"""
    assert_official_host(url)
    body = None
    headers = {"User-Agent": "anan-wechat-suite/%s" % VERSION}
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s %s（微信接口异常，稍后重试）" % (e.code, e.reason))
    except Exception as e:
        raise RuntimeError("网络请求失败：%s: %s" % (type(e).__name__, e))
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise RuntimeError("微信返回的不是 JSON（前 200 字）：%s"
                           % raw[:200].decode("utf-8", errors="replace"))


def http_multipart(url, fields, files, timeout=60):
    """发 multipart/form-data（上传图片素材用）。url 必须过官方域名校验。"""
    assert_official_host(url)
    boundary = "----ananwechatsuite" + uuid.uuid4().hex
    buf = []
    for k, v in fields.items():
        buf.append(("--%s\r\n" % boundary).encode("utf-8"))
        buf.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode("utf-8"))
        buf.append(str(v).encode("utf-8"))
        buf.append(b"\r\n")
    for k, (filename, data, ctype) in files.items():
        buf.append(("--%s\r\n" % boundary).encode("utf-8"))
        buf.append(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                    % (k, filename)).encode("utf-8"))
        buf.append(("Content-Type: %s\r\n\r\n" % ctype).encode("utf-8"))
        buf.append(data)
        buf.append(b"\r\n")
    buf.append(("--%s--\r\n" % boundary).encode("utf-8"))
    body = b"".join(buf)
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "multipart/form-data; boundary=%s" % boundary,
        "Content-Length": str(len(body)),
        "User-Agent": "anan-wechat-suite/%s" % VERSION,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s %s（上传素材失败）" % (e.code, e.reason))
    except Exception as e:
        raise RuntimeError("网络请求失败：%s: %s" % (type(e).__name__, e))
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise RuntimeError("微信返回的不是 JSON（前 200 字）：%s"
                           % raw[:200].decode("utf-8", errors="replace"))


def explain(resp):
    """把微信错误码翻成人话 + 下一步。"""
    code = resp.get("errcode")
    if not code:
        return None
    msg = resp.get("errmsg", "")
    help_text, todo = ERRCODE_HELP.get(code, ("未收录的错误码", "把完整报错贴出来再判断。"))
    lines = ["微信接口返回错误 errcode=%s" % code,
             "  含义：%s" % help_text,
             "  原文：%s" % msg,
             "  怎么办：%s" % todo]
    if code == 40164:
        m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", msg)
        if m:
            lines.append("  需要加白名单的 IP：%s" % m.group(1))
    return "\n".join(lines)


# ---------------------------------------------------------------- 三步 API
def token_url(appid, secret):
    """换 access_token 的地址（--probe 与 --myip 共用）。"""
    return ("%s/cgi-bin/token?grant_type=client_credential&appid=%s&secret=%s"
            % (API_BASE, urllib.parse.quote(appid), urllib.parse.quote(secret)))


def step1_token(appid, secret, timeout=30, label="1/3"):
    """换 access_token。label 只影响打印的「步骤 x/y」，让 --publish-go 那条路读起来不别扭。"""
    url = token_url(appid, secret)
    log("  步骤 %s：换 access_token ..." % label)
    resp = http_json(url, timeout=timeout)
    if resp.get("errcode"):
        return None, explain(resp)
    tok = resp.get("access_token")
    if not tok:
        return None, "微信没返回 access_token，原始响应：%s" % json.dumps(resp, ensure_ascii=False)
    log("    成功（有效期 %s 秒；access_token 不落盘）" % resp.get("expires_in", "?"))
    return tok, ""


def step2_upload_thumb(token, cover_path, timeout=60):
    if not cover_path or not os.path.isfile(cover_path):
        return None, ("封面图不存在：%s\n"
                      "  公众号草稿要求必须有封面（thumb_media_id 是必填项）。"
                      "请用 --cover 指定一张 jpg/png 封面图。" % cover_path)
    ext = os.path.splitext(cover_path)[1].lower()
    ctype = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(ext)
    if not ctype:
        return None, ("封面图格式不支持：%s\n  微信永久图片素材只收 jpg / png，"
                      "webp、gif、svg 都会被拒。请先转成 jpg。" % ext)
    with open(cover_path, "rb") as f:
        data = f.read()
    log("  步骤 2/3：上传素材 —— 封面 -> 永久素材（%s，%.1f KB）..."
        % (os.path.basename(cover_path), len(data) / 1024.0))
    mid, err, _code = upload_image_material(token, os.path.basename(cover_path), data, ctype,
                                            timeout=timeout)
    if err:
        return None, err
    log("    成功（media_id 已获取）")
    return mid, ""


def upload_image_material(token, filename, data, ctype, timeout=60):
    """把一张图上传为永久素材。返回 (media_id, 错误说明, errcode)。

    网络层错误也转成返回值（不抛异常），好让调用方逐步报告、不吐 traceback。
    """
    url = "%s/cgi-bin/material/add_material?access_token=%s&type=image" % (API_BASE, token)
    try:
        resp = http_multipart(url, {}, {"media": (filename, data, ctype)}, timeout=timeout)
    except RuntimeError as e:
        return None, str(e), None
    code = resp.get("errcode")
    if code:
        return None, explain(resp), code
    mid = resp.get("media_id")
    if not mid:
        return None, ("上传成功但没拿到 media_id，原始响应：%s"
                      % json.dumps(resp, ensure_ascii=False)), None
    return mid, "", None


def make_test_png(width=320, height=200, rgb=(235, 242, 255)):
    """生成一张纯色小 PNG（只为验「能不能传素材」，纯标准库，不落盘）。"""
    import struct
    import zlib

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)      # 8 位 RGB
    raw = (b"\x00" + bytes(rgb) * width) * height                     # 每行：filter=0 + 像素
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def step3_add_draft(token, title, html, thumb_media_id, author="", digest="",
                    source_url="", timeout=60):
    payload = {"articles": [{
        "title": title[:64],
        "author": author[:8] if author else "",
        "digest": digest[:120] if digest else "",
        "content": html,
        "content_source_url": source_url,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }]}
    log("  步骤 3/3：建草稿（只到草稿箱，不群发）...")
    url = "%s/cgi-bin/draft/add?access_token=%s" % (API_BASE, token)
    resp = http_json(url, data=payload, timeout=timeout)
    if resp.get("errcode"):
        return None, explain(resp)
    mid = resp.get("media_id")
    if not mid:
        return None, "接口没返回 media_id，原始响应：%s" % json.dumps(resp, ensure_ascii=False)
    log("    成功（草稿 media_id 已获取）")
    return mid, ""


# ---------------------------------------------------------------- 正文图转存
def sniff_image_format(data):
    """按文件头判断图片格式（不看扩展名 —— 扩展名经常是骗人的）。

    @param data 图片字节
    @returns 'jpg' / 'png' / 'gif' / 'webp' / 'bmp' / 'tiff' / '?'
    """
    if not data:
        return "?"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"BM":
        return "bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return "?"


CTYPE_BY_FORMAT = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif",
                   "webp": "image/webp", "bmp": "image/bmp", "tiff": "image/tiff"}


def find_article_gallery(md_path):
    """读抓取器落在同目录的 meta.json，拿「本地图片文件名 -> 当初的来源 URL」。

    为什么需要它：抓下来的本地图可能是 webp / gif（微信 CDN 或原站就给的这个格式），
    uploadimg 收不了；而这张图的**原始地址往往本来就在微信图床**上，直接用原始地址就行，
    不用（也没法在零依赖的前提下）在本地重新编码。抓取器已把来源 URL 写进 meta.json。

    @param md_path 文章 Markdown 路径
    @returns {文件名: 来源URL}；没有 meta.json 就返回空表（不是错误，文章可能是手写的）
    """
    path = os.path.join(os.path.dirname(os.path.abspath(md_path)), "meta.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "rb") as f:
            meta = json.loads(f.read().decode("utf-8-sig", errors="replace"))
    except (ValueError, OSError):
        return {}
    out = {}
    for rec in (meta.get("图片清单") or []):
        name = (rec.get("文件名") or "").strip()
        url = (rec.get("来源URL") or "").strip()
        if name and url:
            out[name] = url
            out.setdefault(os.path.splitext(name)[0], url)
    return out


def is_remote_url(src):
    """这个 src 是不是个 http(s) 地址。"""
    s = (src or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://") or s.startswith("//")


def resolve_local_path(src, base_dir):
    """把 <img src> 里的本地路径解析成磁盘路径（相对路径按文章所在目录算）。"""
    s = urllib.parse.unquote(src.split("#", 1)[0].split("?", 1)[0])
    if s.lower().startswith("file:///"):
        s = urllib.parse.unquote(s[8:])
        if re.match(r"^/[A-Za-z]:", s):
            s = s[1:]
    s = s.replace("/", os.sep).replace("\\", os.sep)
    if os.path.isabs(s):
        return os.path.normpath(s)
    return os.path.normpath(os.path.join(base_dir, s))


def download_image(url, timeout=30):
    """下载一张外链图。返回 (data, 错误)。

    ★ 这里**不带密钥**，也允许任意域名 —— 只是读一张公开图片，不涉及安全边界。
      （带 AppID / AppSecret 的请求只允许发往 api.weixin.qq.com，那条红线在 assert_official_host。）
    """
    if url.startswith("//"):
        url = "https:" + url
    req = urllib.request.Request(url, headers={
        "User-Agent": IMAGE_UA,
        "Accept": IMAGE_ACCEPT,        # ★ 不要 webp，见文件头 IMAGE_ACCEPT 的注释
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(MAX_DOWNLOAD_BYTES + 1)
    except urllib.error.HTTPError as e:
        return None, "HTTP %s %s" % (e.code, e.reason)
    except Exception as e:
        return None, "下载失败：%s: %s" % (type(e).__name__, e)
    if len(data) > MAX_DOWNLOAD_BYTES:
        return None, "超过单张 %dMB 的下载上限" % (MAX_DOWNLOAD_BYTES // 1024 // 1024)
    return data, ""


def upload_body_image(token, filename, data, timeout=60):
    """把一张正文图转存到微信图床（media/uploadimg）。返回 (url, 错误说明)。

    官方：仅支持 jpg/png、单张 1MB 以下、不占素材库额度。网络层错误也转成返回值。
    """
    ctype = CTYPE_BY_FORMAT.get(sniff_image_format(data), "application/octet-stream")
    url = "%s?access_token=%s" % (UPLOADIMG_URL, token)
    try:
        resp = http_multipart(url, {}, {"media": (filename, data, ctype)}, timeout=timeout)
    except RuntimeError as e:
        return None, str(e)
    if resp.get("errcode"):
        return None, explain(resp)
    got = resp.get("url")
    if not got:
        return None, ("上传成功但没拿到 url，原始响应：%s"
                      % json.dumps(resp, ensure_ascii=False))
    return got, ""


_RE_IMG_SRC = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>\"']+))", re.I)


def _rewrite_img_srcs(html, new_by_index):
    """把第 N 张图的 src 换成新版（值给了空串就整张删掉）。"""
    counter = [0]

    def sub(m):
        counter[0] += 1
        i = counter[0]
        if i not in new_by_index:
            return m.group(0)
        repl = new_by_index[i]
        for g in (1, 2, 3):
            if m.group(g) is not None:
                if repl == "":
                    return ""
                return html[m.start(0):m.start(g)] + repl + html[m.end(g):m.end(0)]
        return m.group(0)

    return _RE_IMG_SRC.sub(sub, html)


def transfer_body_images(token, html, base_dir, gallery=None, drop=(), timeout=60):
    """把正文里的每一张图转存到微信图床，并返回换好 src 的新 HTML。

    ★ 这一步就是「草稿里所有图都碎掉」那个 bug 的修复点：上游只管给本地路径或外链，
      这里负责把它们变成微信取得到的地址。失败**逐张如实报**，不静默跳过。

    每张图的处理顺序：
      ① src 已经是微信图床地址        -> 原样保留（不用白传一遍）
      ② 本地文件 / 外链 / data: 图    -> 取到字节（外链先下载）
      ③ 字节是 jpg/png 且 1MB 以下    -> uploadimg 转存，src 换成微信给的 url
      ④ 字节是 webp/gif/超大          -> 先用「来源 URL」（meta.json）兜底：若那张图的原始
         地址本来就在微信图床，直接用原始地址（本地这份是 CDN 转码出来的，传不上去）
      ⑤ 兜不住 -> 记为失败，返回给调用方拦下发布

    @param token access_token
    @param html 正文 HTML（typeset 的产物）
    @param base_dir 文章所在目录（解析本地相对路径用）
    @param gallery find_article_gallery 的结果
    @param drop 要显式丢弃的图片序号集合（用户用 --drop-image 指定）
    @returns (新 HTML, 报告列表)；报告每项是 dict
    """
    guard = guard_mod()
    gallery = gallery or {}
    drop = set(drop or ())
    report = []
    new_by_index = {}
    items = _RE_IMG_SRC.findall(html)
    total = len(items)
    if total:
        log("  步骤 2/3：上传素材 —— 正文图 %d 张转存到微信图床（media/uploadimg）" % total)
    for idx, m in enumerate(_RE_IMG_SRC.finditer(html), 1):
        raw_src = html_mod.unescape(m.group(1) or m.group(2) or m.group(3) or "")
        rec = {"index": idx, "src": raw_src, "fmt": "", "size": 0,
               "status": "", "new_src": raw_src, "reason": ""}
        report.append(rec)

        if idx in drop:
            rec["status"] = "dropped"
            rec["reason"] = "你用 --drop-image 显式丢弃了这一张"
            new_by_index[idx] = ""
            log("    %d/%d %s" % (idx, total, rec["reason"]))
            log("         -> 已从正文里删掉这张 <img>（你自己要求的）")
            continue

        if guard.is_wechat_image_url(raw_src):
            rec["status"] = "kept"
            rec["reason"] = "本来就在微信图床"
            log("    %d/%d 已在微信图床，跳过：%s" % (idx, total, short(raw_src)))
            continue

        # ---- 取字节
        # uploadimg 的 multipart 需要一个**像图片文件的文件名**：
        # 传 URL 本身（"640?wx_fmt=png&from=appmsg"）会被微信判 40005 invalid file type。
        upload_name = ""
        if raw_src.startswith("data:"):
            try:
                head, b64 = raw_src.split(",", 1)
                data = base64.b64decode(b64)
            except Exception as e:
                rec["status"] = "failed"
                rec["reason"] = "data: 图解码失败：%s" % e
                log("    %d/%d 失败：%s" % (idx, total, rec["reason"]))
                continue
            origin = ""
        elif is_remote_url(raw_src):
            data, err = download_image(raw_src, timeout=timeout)
            if err:
                rec["status"] = "failed"
                rec["reason"] = "外链图下载失败：%s" % err
                log("    %d/%d 失败：%s" % (idx, total, rec["reason"]))
                continue
            origin = raw_src
        else:
            path = resolve_local_path(raw_src, base_dir)
            if not os.path.isfile(path):
                rec["status"] = "failed"
                rec["reason"] = "本地图不存在：%s" % path
                log("    %d/%d 失败：%s" % (idx, total, rec["reason"]))
                continue
            with open(path, "rb") as f:
                data = f.read()
            upload_name = os.path.basename(path)
            origin = gallery.get(os.path.basename(path), "") or \
                gallery.get(os.path.splitext(os.path.basename(path))[0], "")

        fmt = sniff_image_format(data)
        rec["fmt"], rec["size"] = fmt, len(data)
        brief = "%s %.1fKB" % (fmt, len(data) / 1024.0)
        if not upload_name:
            # 外链 / data: 图：从地址里取个像样的文件名，取不到就按真实格式兜一个
            upload_name = os.path.basename(urllib.parse.urlsplit(
                raw_src if not raw_src.startswith("data:") else "").path) or "image"
            if "." not in upload_name:
                upload_name += "." + (fmt if fmt != "jpg" else "jpg")

        if fmt not in UPLOADIMG_FORMATS or len(data) > UPLOADIMG_MAX_BYTES:
            why = ("格式 %s 不在官方允许的 jpg/png 里" % fmt if fmt not in UPLOADIMG_FORMATS
                   else "体积 %.1fKB 超过官方 1MB 上限" % (len(data) / 1024.0))
            if origin and guard.is_wechat_image_url(origin):
                rec["status"] = "kept"
                rec["new_src"] = origin
                rec["reason"] = "%s；改用这张图的原始微信地址" % why
                new_by_index[idx] = html_mod.escape(origin, quote=True)
                log("    %d/%d %s" % (idx, total, brief))
                log("         %s -> 用原图微信地址（本地这份是 CDN 转码出来的）" % why)
                continue
            rec["status"] = "failed"
            rec["reason"] = why + "；也没有可用的微信原始地址兜底"
            log("    %d/%d 失败：%s" % (idx, total, rec["reason"]))
            continue

        log("    %d/%d %s -> 上传中 ..." % (idx, total, brief))
        got, err = upload_body_image(token, upload_name, data, timeout=timeout)
        if err:
            # 兜底：这张图有微信原始地址的话，宁可先用它，也别把碎图发出去
            if origin and guard.is_wechat_image_url(origin):
                rec["status"] = "kept"
                rec["new_src"] = origin
                rec["reason"] = "转存失败（%s），改用原图微信地址兜底" % err.split("\n")[0]
                new_by_index[idx] = html_mod.escape(origin, quote=True)
                log("         转存失败：%s" % err.split("\n")[0])
                log("         已改用这张图的原始微信地址兜底。")
                continue
            rec["status"] = "failed"
            rec["reason"] = "转存失败：%s" % err
            log("         失败。")
            continue
        rec["status"] = "uploaded"
        rec["new_src"] = got
        new_by_index[idx] = html_mod.escape(got, quote=True)

    return _rewrite_img_srcs(html, new_by_index), report


def short(s, n=78):
    """打印用：太长的地址截断。"""
    s = s or ""
    return s if len(s) <= n else s[:n] + "…"


def print_image_report(report):
    """把转存结果按人话打出来（成功也算清楚：哪些传了、哪些本来就在图床）。"""
    if not report:
        return
    up = [r for r in report if r["status"] == "uploaded"]
    kept = [r for r in report if r["status"] == "kept"]
    bad = [r for r in report if r["status"] == "failed"]
    drop = [r for r in report if r["status"] == "dropped"]
    log("    正文图小结：共 %d 张 —— 转存 %d / 本来就在图床 %d / 失败 %d / 显式丢弃 %d"
        % (len(report), len(up), len(kept), len(bad), len(drop)))
    for r in kept:
        log("      · 第 %d 张：%s" % (r["index"], r["reason"]))
    if bad:
        log("    !! 有 %d 张图没转存成功：" % len(bad))
        for r in bad:
            log("      · 第 %d 张（%s，%s）：%s"
                % (r["index"], r["fmt"] or "未知格式", r["src"][:60], r["reason"]))


# ---------------------------------------------------------------- 第 4 步：发布
FREEPUBLISH_SUBMIT = API_BASE + "/cgi-bin/freepublish/submit"
FREEPUBLISH_GET = API_BASE + "/cgi-bin/freepublish/get"

# 48001 = 这个号没有发布权限。微信 2025 年 7 月起收回了这几类账号的调用权限。
NO_PUBLISH_PERMISSION = (
    "微信 2025 年 7 月起收回了这几类账号的调用权限：个人主体、企业未认证、不支持认证。",
    "草稿已经建好了：去公众号后台 → 草稿箱 → 找到这篇 → 点「发表」。",
    "（发布接口调不通 ≠ 白做：草稿箱里那一篇就是刚建好的，打开就能发。）",
)

# ★ 用户点「发表」时最容易踩的坑：那个「群发通知」开关默认是开的。
GROUP_NOTIFY_SWITCH = (
    "★ 在后台点「发表」时，弹窗里有个「群发通知」开关：",
    "  ✅ 开启（默认）= 推送给粉丝，一天只能 1 次",
    "  ❌ 关掉        = 不推送，只上公众号主页，不限次数",
    "★ 它默认是开的 —— 不想打扰粉丝，记得手动关掉。",
)


def step4_publish(token, media_id, timeout=30, label="4/4"):
    """把已建好的草稿提交发布（freepublish/submit）。返回 (publish_id, err)。

    ★ 发出去**不可撤销**、所有关注者都能看到，所以只在用户明确确认后调。
    """
    log("  步骤 %s：发布（freepublish/submit，发出去不可撤销）..." % label)
    url = "%s?access_token=%s" % (FREEPUBLISH_SUBMIT, token)
    resp = http_json(url, data={"media_id": media_id}, timeout=timeout)
    if resp.get("errcode"):
        return "", explain(resp), resp.get("errcode")
    pid = resp.get("publish_id") or ""
    log("    发布请求已提交（publish_id=%s）" % (pid or "(微信没返回)"))
    return pid, "", 0


def find_mp_url(obj, _depth=0):
    """在微信返回的 JSON 里找 mp.weixin.qq.com 公开链接。找不到返回空串。"""
    if _depth > 6:
        return ""
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, str) and "mp.weixin.qq.com" in v:
                return v
            found = find_mp_url(v, _depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_mp_url(v, _depth + 1)
            if found:
                return found
    return ""


def fetch_published_url(token, publish_id, tries=4, wait=3):
    """发布是异步的：轮询 freepublish/get 取公开链接。

    取不到就返回空串 + 一句实话 —— **绝不编一个链接**（编了用户点进去是 404）。
    """
    if not publish_id:
        return "", "微信没返回 publish_id，查不到公开链接"
    for i in range(tries):
        try:
            resp = http_json("%s?access_token=%s" % (FREEPUBLISH_GET, token),
                             data={"publish_id": publish_id}, timeout=30)
        except RuntimeError as e:
            return "", "查询发布结果失败（%s）" % e
        if resp.get("errcode"):
            return "", explain(resp).replace(chr(10), " ")
        url = find_mp_url(resp)
        if url:
            return url, ""
        if i < tries - 1:
            time.sleep(wait)
    return "", "发布是异步的，这次没等到公开链接（没失败，去后台「发表记录」看一眼就有）"


def print_publish_checklist(title, cover, digest, acc, key, appid):
    """★ 发布前必须先给用户看的三样：标题 / 封面 / 摘要。"""
    log("========== 发布前，先看清楚这三样 ==========")
    log("标题：%s" % (title or "(空)"))
    log("封面：%s" % os.path.abspath(cover))
    log("摘要：%s" % (digest or "（空 —— 公众号会自动截取正文开头几句当摘要）"))
    log("将发到：%s（appid %s）" % (acc.get("name") or key, mask(appid)))
    log("★ 发出去不可撤销，所有关注者都能看到 —— 所以要先看一眼。")


# ---------------------------------------------------------------- 粘贴模式
def make_paste_html(md_path, out_html, theme, code_theme, full=True):
    """用本技能的排版引擎生成可直接粘贴的 HTML。"""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import typeset
    raw = open(md_path, "rb").read()
    text = raw.decode("utf-8-sig", errors="replace").replace(BOM, "")
    title = ""
    for ln in text.split("\n"):
        if ln.startswith("# "):
            title = ln[2:].strip()
            break
    body, stats = typeset.md_to_html(text, typeset.THEMES[theme], typeset.CODE_THEMES[code_theme])
    if full:
        out = typeset.build_document(body, typeset.THEMES[theme], theme, title)
    else:
        out = body + "\n"
    parent = os.path.dirname(os.path.abspath(out_html))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_html, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
    return title, stats, len(out)


# ---------------------------------------------------------------- CLI
def cmd_check(cfg, cfg_note, acc_name):
    log("========== 账号与配置体检 ==========")
    log("")
    log("配置文件：%s" % CONFIG_PATH)
    if cfg_note:
        log("  状态：%s" % cfg_note)
    else:
        log("  状态：已读取")
    log("")
    acc, key, note = active_account(cfg, acc_name)
    if not acc:
        log("当前账号：%s" % (key or "(无)"))
        log("  问题：%s" % note)
    else:
        log("当前账号：%s（配置名 %s）" % (acc.get("name") or "(未命名)", key))
        log("  appid    ：%s" % (mask(acc.get("appid", "")) if acc.get("appid") else "未配置"))
        log("  appsecret：%s" % mask(acc.get("appsecret") or acc.get("secret") or ""))
        raw_type = (acc.get("type") or "").strip().lower()
        log("  type     ：%s%s" % (raw_type or "(未指定)", type_note(raw_type)))
    log("")
    cap, why = account_capability(acc)
    log("判定能力：%s" % {"api": "走官方 API 直发草稿箱",
                          "paste": "走粘贴模式（没配密钥；这条路永远可用）",
                          "error": "配置有误，需要先修",
                          "none": "没有可用账号配置"}[cap])
    log("  依据：%s" % why)
    if cap == "api":
        log("  （拿不准这个号到底行不行？跑 python scripts/publish.py --probe-full 逐步验：")
        log("    换 token → 传素材 → 建草稿，哪一步断了会说哪一步。）")
    if (acc.get("appsecret") or acc.get("secret") or "").strip() and normalize_type(acc.get("type")) == "paste":
        log("")
        log("  ★ 这一格存着密钥，但当前标着 paste（你选的是粘贴模式）—— 默认不会走接口。")
        log("    想走直发：跑文章时加 --mode api；或重跑 python scripts/setup.py 选 [1] 改成直发。")
        log("    （密钥留着没动，可以随时切回去。）")
    log("")

    if cap == "api":
        log("可以继续跑：")
        log("  python scripts/publish.py --probe                  # 先验一次权限与 IP 白名单")
        log("  python scripts/publish.py 文章.md --mode api --cover images/cover.jpg")
    elif cap == "paste":
        log("下一步（粘贴模式：排版自动，最后一步复制粘贴到后台；不需要任何密钥）：")
        log("  python scripts/publish.py 文章.md --mode paste --theme anan")
        log("  然后把生成的 HTML 用浏览器打开，全选复制，粘进公众号编辑器。")
        log("")
        log("想改用 API 直发（服务号）的话，跑一次配置向导就行，不用手改文件：")
        log("  python scripts/setup.py")
    else:
        log("请先修好配置（不用手改文件）：")
        log("  python scripts/setup.py            # 问几个问题就写好 config.json")
        log("  然后重跑：python scripts/publish.py --check")
        log("  （想手动改也能改，模板：%s）" % EXAMPLE_PATH)
    log("")
    log("★ 密钥只存在本机 config.json，只发往 api.weixin.qq.com，不上传不外发。")
    return 0 if cap in ("api", "paste") else 1


def cmd_publish_go(cfg, acc_name, media_id):
    """★ 真发布：拿已经建好的草稿 media_id 调 freepublish/submit。

    为什么单独一条命令：发布不可撤销，必须「先看一眼、再发」——
    所以建草稿（--publish）与真发（--publish-go）分成两步，中间那一步是给人看的。
    复用同一个 media_id，不会重复建草稿、不会重复传封面。
    """
    acc, key, note = active_account(cfg, acc_name)
    cap, why = account_capability(acc)
    if cap != "api":
        no_config_help("发布", why=why, misconfig=(cap == "error"))
        return 1
    appid = (acc.get("appid") or "").strip()
    secret = (acc.get("appsecret") or acc.get("secret") or "").strip()
    log("========== 发布（freepublish/submit，发出去不可撤销） ==========")
    log("账号：%s" % (acc.get("name") or key))
    log("appid：%s" % mask(appid))
    log("草稿 media_id：%s" % media_id)
    log("")
    tok, err = step1_token(appid, secret, label="1/2")
    if err:
        log("")
        log("!! 第 1 步（换 token）就断了：")
        for line in err.split("\n"):
            log("  " + line)
        log("")
        log("草稿还在草稿箱里，去公众号后台自己点发表也行。")
        return 1

    pid, err, code = step4_publish(tok, media_id, label="2/2")
    if err:
        if code == 48001:
            # ★ 如实说，并直接给下一步：后台怎么发 + 那个默认开着的「群发通知」开关
            log("")
            log("!! 你这个号没有发布权限（微信接口返回 errcode=48001 / api unauthorized）。")
            log("")
            log("========== 草稿没白建，去后台点一下就行 ==========")
            for line in NO_PUBLISH_PERMISSION:
                log(line)
            log("")
            for line in GROUP_NOTIFY_SWITCH:
                log(line)
            return 1
        log("")
        log("!! 发布失败：")
        for line in err.split("\n"):
            log("  " + line)
        log("")
        log("========== 没发出去 ==========")
        if code == 40007:
            # ERRCODE_HELP 里的 40007 说的是封面，这一步说的却是草稿 —— 别让用户白折腾封面
            log("这里的 media_id 指的是**草稿**的 media_id（不是封面）：多半是草稿被删了、")
            log("或者 id 抄错/过期了。去后台「草稿箱」看一眼，或重跑一次 --publish 拿新的 id。")
        log("草稿还在草稿箱里（后台 → 草稿箱），你可以自己点发表。")
        return 1

    url, why = fetch_published_url(tok, pid)
    log("")
    log("========== 发布成功 ==========")
    if url:
        log("公开链接：%s" % url)
    else:
        log("公开链接：这次没取到 —— %s" % why)
    log("★ 想推送给粉丝要靠微信的群发能力，本脚本**没有**群发接口，永远不会替你群发。")
    return 0


def cmd_probe(cfg, acc_name):
    acc, key, note = active_account(cfg, acc_name)
    cap, why = account_capability(acc)
    log("========== 接口权限探测（会真的调一次微信接口） ==========")
    log("")
    if cap != "api":
        no_config_help("探测", why=why, misconfig=(cap == "error"), paste_hint=True)
        if cap == "error":
            log("")
            log("   这不是网络问题，是配置没配齐。密钥在")
            log("   「微信开发者平台 → 首页/服务号 → 选公众号 → 基础信息」；")
            log("   老路径「设置与开发 → 基本配置」2025-12-01 起已迁移，打不开了。")
        return 1
    appid = (acc.get("appid") or "").strip()
    secret = (acc.get("appsecret") or acc.get("secret") or "").strip()
    log("账号：%s" % (acc.get("name") or key))
    log("appid：%s" % mask(appid))
    log("")
    if (acc.get("type") or "").strip().lower() == "personal":
        log("★ 这一格标的是 personal（旧词汇，说的是号型）：**个人订阅号也能直发草稿箱** ——")
        log("  2026-10-06 实测三步全过。probe 只测第 1 步；要一路验到底用 --probe-full。")
    log("只验证第 1 步（换 token）。这一步过了，说明 AppID/AppSecret/IP 白名单都是通的。")
    log("")
    tok, err = step1_token(appid, secret)
    if err:
        log("")
        log("!! 探测失败：")
        for line in err.split("\n"):
            log("  " + line)
        log("")
        log("结论：现在走 API 发不了（**这是实测结果，不是预设结论**）。")
        log("建议改走粘贴模式（不需要任何接口权限，这条路已实测可用）：")
        log("  python scripts/publish.py 文章.md --mode paste")
        return 1
    log("")
    log("第 1 步通过：AppID / AppSecret / IP 白名单 都是通的。")
    log("（token 只在内存里用，没有写进磁盘。）")
    log("")
    log("⚠️ 但这**不代表能发文章** —— 上面只验了「换 token」这一步。")
    log("   这个号有没有「传素材 / 建草稿」的接口权限，要逐步验才知道：")
    log("     python scripts/publish.py --probe-full")
    return 0


DRAFT_TITLE = "【测试】anan-wechat-suite 权限探测，可删除"


def cmd_probe_full(cfg, acc_name, with_draft=False):
    """完整权限探测：第 1 步换 token → 第 2 步传一张测试小图 →（可选）第 3 步建测试草稿。

    为什么默认停在第 2 步：第 2 步只上传一张自带的小图，可重复、代价小；
    第 3 步会**真在你的草稿箱里留一篇**，所以默认不做，要验到底得显式加 --with-draft。

    每一步都单独报告成功/失败，失败时给 errcode + 含义 + 下一步。
    48001（接口权限不足）会被单独点出来 —— 那正是「这个号到底行不行」的实测答案。
    """
    log("========== 完整权限探测：逐步验，哪一步断了就说哪一步 ==========")
    log("")
    acc, key, note = active_account(cfg, acc_name)
    cap, why = account_capability(acc)
    if cap != "api":
        no_config_help("探测", why=why, misconfig=(cap == "error"), paste_hint=True)
        return 1
    appid = (acc.get("appid") or "").strip()
    secret = (acc.get("appsecret") or acc.get("secret") or "").strip()
    log("账号：%s" % (acc.get("name") or key))
    log("appid：%s" % mask(appid))
    if (acc.get("type") or "").strip().lower() == "personal":
        log("★ 这一格标的是 personal（旧词汇，说的是号型）：**实测个人订阅号同样能三步全过、直发草稿箱**"
            "（2026-10-06，个人主体 + 暂未认证）。")
    log("")
    log("这会真的调微信接口，顺序与副作用：")
    log("  第 1 步 换 access_token            验 AppID / AppSecret / IP 白名单（无副作用）")
    log("  第 2 步 上传一张测试小图做永久素材  验 material/add_material")
    log("          （会在你的「素材管理 → 图片」里留一张小图，不需要可以直接删）")
    if with_draft:
        log("  第 3 步 建一篇测试草稿              验 draft/add")
        log("          （会在草稿箱里留一篇标题带「【测试】」的草稿，看完请自己删掉）")
    else:
        log("  第 3 步 建草稿                      **默认不测**（会往你草稿箱里放东西）")
        log("          要验到底就加 --with-draft")
    log("")

    done = []

    # ---- 第 1 步 ----
    tok, err = step1_token(appid, secret)
    if err:
        log("")
        log("!! 第 1 步就断了：")
        for line in err.split("\n"):
            log("  " + line)
        log("")
        log("========== 结论 ==========")
        log("❌ 第 1 步 换 access_token：失败")
        log("⏹ 第 2、3 步：跳过（第 1 步不过，后面必然不过）")
        log("")
        log("先解决第 1 步（密钥 / IP 白名单），或直接改走粘贴模式（不需要任何接口权限）：")
        log("  python scripts/publish.py 文章.md --mode paste")
        return 1
    done.append(("第 1 步 换 access_token", True, "（AppID / AppSecret / IP 白名单都是通的）"))

    # ---- 第 2 步 ----
    png = make_test_png()
    log("  步骤 2/3：上传测试小图（%d 字节）为永久素材 ..." % len(png))
    mid, err, code = upload_image_material(tok, "anan-wechat-suite-probe.png", png, "image/png")
    if err:
        log("    ✗ 失败")
        log("")
        for line in err.split("\n"):
            log("  " + line)
        log("")
        if code == 48001:
            log("★ 这就是实测答案：**你这个号没有「上传永久素材」权限（errcode=48001）**。")
            log("  官方 API 直发这条路对你这个号走不通 —— 但这不影响发文：")
        else:
            log("这条接口没通过，官方 API 直发这条路先放一放：")
        log("  python scripts/publish.py 文章.md --mode paste")
        log("  （粘贴模式不需要任何接口权限，已实测可用。）")
        log("")
        log("========== 结论 ==========")
        log("✅ 第 1 步 换 access_token：通过")
        log("❌ 第 2 步 上传永久素材：失败%s" % ("（errcode=48001）" if code == 48001 else ""))
        log("⏹ 第 3 步 建草稿：跳过（第 2 步不过，第 3 步必然不过）")
        return 1
    done.append(("第 2 步 上传永久素材", True, "（这个号有 material/add_material 权限）"))
    log("    成功（media_id 已获取）")

    # ---- 第 3 步（可选）----
    if with_draft:
        html = ("<p>这是 anan-wechat-suite 的权限探测草稿（只为验证 draft/add 权限）。</p>"
                "<p>确认完请直接删掉这篇草稿。</p>")
        did, err = step3_add_draft(tok, DRAFT_TITLE, html, mid)
        if err:
            log("")
            log("!! 第 3 步失败：")
            for line in err.split("\n"):
                log("  " + line)
            log("")
            log("========== 结论 ==========")
            log("✅ 第 1 步 换 access_token：通过")
            log("✅ 第 2 步 上传永久素材：通过")
            log("❌ 第 3 步 建草稿：失败")
            log("")
            log("能传素材但建不了草稿 —— 官方 API 直发仍然走不通，改走粘贴模式：")
            log("  python scripts/publish.py 文章.md --mode paste")
            return 1
        done.append(("第 3 步 建草稿", True, "（这个号有 draft/add 权限）"))

    # ---- 结论 ----
    log("")
    log("========== 结论 ==========")
    for name, ok, note_text in done:
        log("%s %s：通过%s" % ("✅" if ok else "❌", name, note_text))
    if not with_draft:
        log("⏸ 第 3 步 建草稿：没测（默认不往你草稿箱里放东西）")
        log("   要验到底：python scripts/publish.py --probe-full --with-draft")
    else:
        log("⚠️ 草稿箱里现在多了一篇「%s」—— 看完请自己删掉。" % DRAFT_TITLE)
    log("")
    log("★ 记住：--probe 只验第 1 步，**过了不等于能发文章** —— 这就是 --probe-full 存在的理由。")
    log("（第 2 步上传的那张小图在「素材管理 → 图片」里，不需要可以直接删。）")
    return 0


def _print_whitelist_howto():
    """白名单该去哪儿加（两个入口），以及「只能你本人手动加」这句实话。"""
    log("⚠️ 加白名单这一步必须你本人登录微信页面操作 —— 这是微信的安全设计")
    log("   （如果脚本能替你加，那别人也能替你加）")
    log("")
    log("去这里把它加进去（两个入口，哪个找得到就用哪个）：")
    log("  主路径：微信开发者平台 → 首页/服务号 → 选你的公众号 → 基础信息 → API IP白名单")
    log("          （平台网址 https://developers.weixin.qq.com/platform —— 记得点「前往控制台」）")
    log("  兜底  ：那页上找不到的话，就找「开发密钥」区域"
        "（就是 AppID / AppSecret 所在的那一页）——")
    log("          API IP白名单 就在 AppSecret 下面一行")
    log("  （注意：微信公众号后台的老路径「设置与开发 → 基本配置」已经打不开了）")


def _print_ip_tips():
    log("提示：家庭宽带的 IP 会变，变了就要重新加。")
    log("提示：被白名单折腾烦了，随时可以退回粘贴模式（不需要任何接口权限，谁都能用）：")
    log("  python scripts/publish.py 文章.md --mode paste")


def cmd_myip(cfg, acc_name):
    """打印本机出口 IP，并告诉用户该去哪儿把它加进白名单。

    ★ 权威性分两级，别搞混（搞混会让用户加错 IP，那比不给还糟）：
      · **权威**：微信 40164 报错里给出的那个 IP —— 那是微信从连接里看到的源 IP。
      · **参考**：公开 IP 查询服务返回的 IP —— 只代表「访问那个服务时」的出口，
        用户网络有代理或分流时，可能和微信看到的完全不是一个。
        所以参考值必须标明不确定性；**没有权威来源时，绝不说它是权威**。

    路线 1：用已配置的密钥调一次 token 接口。报 40164 就拿到权威 IP；
            token 成功说明白名单本来就配好了（此时不需要加任何东西）。
    路线 2（仅在没有权威 IP 时）：问国内公开 IP 服务，拿到参考 IP。
    一条都没拿到就**如实说拿不到**并给替代办法，绝不编一个 IP。
    """
    log("========== 本机出口 IP ==========")
    log("")
    authoritative = ""
    reference = ""
    token_ok = False
    reasons = []

    acc, key, _note = active_account(cfg, acc_name)
    cap, why = account_capability(acc)
    if cap == "api":
        appid = (acc.get("appid") or "").strip()
        secret = (acc.get("appsecret") or acc.get("secret") or "").strip()
        log("路线 1/2：拿 %s 调一次微信 token 接口 —— 只有微信报出来的 IP，才是它真正看到的那个 ..."
            % mask(appid))
        try:
            resp = http_json(token_url(appid, secret), timeout=20)
        except RuntimeError as e:
            resp = None
            reasons.append("微信接口没调通（%s）" % e)
            log("  没调通：%s" % e)
        if resp is not None:
            code = resp.get("errcode")
            if code == 40164:
                authoritative = extract_ip(resp.get("errmsg", ""))
                if authoritative:
                    log("  微信报 40164：这个 IP 不在白名单。（这就是权威答案）")
                else:
                    reasons.append("微信报了 40164，但报错里没带 IP（原文：%s）"
                                   % resp.get("errmsg", ""))
                    log("  微信报了 40164，但报错里没带 IP。")
            elif code:
                reasons.append("微信返回 errcode=%s（%s）" % (code, resp.get("errmsg", "")))
                log("  微信返回 errcode=%s：%s" % (code, resp.get("errmsg", "")))
            elif resp.get("access_token"):
                token_ok = True
                log("  token 换成功了。")
    else:
        reasons.append("没有可用的 AppID/AppSecret（%s），微信这条线走不了" % why)
        log("路线 1/2：跳过 —— %s" % why)

    if not authoritative:
        log("")
        log("路线 2/2：问国内公开的 IP 查询服务（裸 GET，不带任何密钥，只作参考）...")
        for url in IP_ECHO_URLS:
            host = urllib.parse.urlparse(url).hostname
            try:
                text = http_text_public(url)
            except Exception as e:
                log("  %s：失败（%s）" % (host, e))
                reasons.append("%s 没问到：%s" % (host, e))
                continue
            got = extract_ip(text)
            if got:
                reference = got
                log("  %s：拿到 %s" % (host, got))
                break
            log("  %s：返回里没有 IP（原文前 60 字：%s）" % (host, text.strip()[:60]))
            reasons.append("%s 返回的内容里没有 IP" % host)

    log("")
    if authoritative:
        log("本机出口 IP（**微信自己看到的那个**，权威）：")
        log("    %s" % authoritative)
        log("")
        _print_whitelist_howto()
        log("")
        _print_ip_tips()
        log("")
        log("加完之后跑这条验证：")
        log("  python scripts/publish.py --probe")
        if reasons:
            log("")
            log("（补充：%s）" % "；".join(reasons))
        log("")
        log("★ 本命令没有发送也没有打印任何密钥。")
        return 0

    if not reference:
        log("!! 没能自动获取本机出口 IP（微信和公开服务都没问出来）。")
        log("")
        log("原因：")
        for r in reasons:
            log("  · %s" % r)
        log("")
        log("你可以这样做（任选一条）：")
        log("  1) 浏览器里搜「IP」，第一个结果通常就显示你的公网 IP；")
        log("  2) 跑 python scripts/publish.py --probe —— 白名单没配好时，")
        log("     微信会把**它看到的那个 IP** 打进 40164 报错里（那才是权威值）；")
        log("  3) 不折腾白名单，走粘贴模式（不需要任何接口权限）：")
        log("     python scripts/publish.py 文章.md --mode paste")
        log("")
        log("退出码 1：拿不到就是拿不到，不会编一个 IP 给你。")
        return 1

    if token_ok:
        log("✓ 你的白名单**已经配好了** —— 这个号现在就能直接发，**不需要加任何 IP**。")
        log("")
        log("参考 IP（来自国内公开查询服务，仅供参考）：")
        log("    %s" % reference)
        log("")
        log("⚠️ 注意：这只是访问那个查询服务时的出口 IP，不一定等于微信看到的 IP。")
        log("   如果你的网络有代理或分流，两者会不一样。")
        log("   真要动白名单时，请以微信报错里给出的那个 IP 为准"
            "（跑 python scripts/publish.py --probe 触发一次）。")
        log("")
        log("（如果你确实要去看一眼白名单入口："
            "微信开发者平台 → 选你的公众号 → 基础信息 → API IP白名单）")
        if reasons:
            log("")
            log("（补充：%s）" % "；".join(reasons))
        log("")
        log("★ 本命令没有发送也没有打印任何密钥。")
        return 0

    log("参考 IP（来自国内公开查询服务，仅供参考）：")
    log("    %s" % reference)
    log("")
    log("⚠️ 注意：这只是访问那个查询服务时的出口 IP，**不一定等于微信看到的 IP**。")
    log("   如果你的网络有代理或分流，两者会不一样 —— 照着它加白名单可能加了也没用。")
    log("   要加白名单，请以**微信报错里给出的那个 IP** 为准：")
    log("   配好 AppID / AppSecret 后跑 python scripts/publish.py --probe，")
    log("   白名单没配好时，微信会把它的那个 IP 打进 40164 报错里。")
    log("")
    _print_whitelist_howto()
    log("")
    _print_ip_tips()
    log("")
    log("加完之后跑这条验证：")
    log("  python scripts/publish.py --probe")
    if reasons:
        log("")
        log("（补充：%s）" % "；".join(reasons))
    log("")
    log("★ 本命令没有发送也没有打印任何密钥。")
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="发布（双轨）：配了 AppID/AppSecret 就走官方 API 直发草稿箱"
                    "（个人订阅号与认证服务号都支持，已实测）；明确选了粘贴模式就走可粘贴 HTML。"
                    "★ 还没配置就发布时不会悄悄降级，而是报错并指向 python scripts/setup.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python scripts/publish.py --myip\n"
               "  python scripts/publish.py --check\n"
               "  python scripts/publish.py --probe\n"
               "  python scripts/publish.py --probe-full\n"
               "  python scripts/publish.py --probe-full --with-draft\n"
               "  python scripts/publish.py 文章.md --mode paste --theme anan\n"
               "  python scripts/publish.py 文章.md --mode api --cover images/cover.jpg\n"
               "  python scripts/publish.py 文章.md --mode api --cover images/cover.jpg --publish\n"
               "  python scripts/publish.py --publish-go <上面给的 media_id>   # 确认后才真发\n")
    ap.add_argument("article", nargs="?", help="文章 Markdown")
    ap.add_argument("--myip", action="store_true",
                    help="打印本机出口 IP，并提示该去哪儿把它加进 IP 白名单")
    ap.add_argument("--check", action="store_true", help="只做配置与账号能力体检")
    ap.add_argument("--probe", action="store_true",
                    help="只验第 1 步：换 token（= AppID / AppSecret / IP 白名单）")
    ap.add_argument("--probe-full", action="store_true",
                    help="逐步验到底：换 token → 上传测试小图（验素材权限）；加 --with-draft 再验建草稿")
    ap.add_argument("--with-draft", action="store_true",
                    help="配合 --probe-full：额外建一篇【测试】草稿验 draft/add（会在草稿箱留一篇，需自己删）")
    ap.add_argument("--mode", choices=["auto", "api", "paste"], default="auto",
                    help="auto=按账号能力自动选路（默认）；没配置会明确报错并指向 setup.py，"
                         "不会悄悄降级；api=强制官方接口；paste=强制粘贴模式（不需要密钥）")
    ap.add_argument("--account", help="指定用 config.json 里的哪个账号（默认用 active）")
    ap.add_argument("--cover", help="封面图路径（api 模式必填，jpg/png）")
    ap.add_argument("--theme", default="anan", help="排版主题（默认 anan）")
    ap.add_argument("--code-theme", default="github-light", help="代码高亮主题")
    ap.add_argument("-o", "--output", help="粘贴模式下 HTML 的输出路径")
    ap.add_argument("--author", default="阿南 rockmoons", help="草稿作者（默认 阿南 rockmoons）")
    ap.add_argument("--digest", default="", help="草稿摘要（不填留空）")
    ap.add_argument("--source-url", default="", help="原文链接（转载时填）")
    ap.add_argument("--ignore-guard", action="store_true",
                    help="★ 显式关掉发布前闸门（过程性标题 / 非微信图床的图 会原样进草稿，风险自负）")
    ap.add_argument("--drop-image", default="",
                    help="按序号丢弃正文里的图，逗号分隔（例：--drop-image 3,7）。"
                         "转存不了又必须发时用；丢弃会在日志里如实写出来，不静默")
    ap.add_argument("--dry-run", action="store_true",
                    help="api 模式下只跑到「准备就绪」就停，不真的建草稿")
    ap.add_argument("--publish", action="store_true",
                    help="建完草稿**先不发布**：把标题 / 封面 / 摘要报出来给你看一眼，"
                         "确认后再跑 --publish-go（发布不可撤销）")
    ap.add_argument("--publish-go", metavar="MEDIA_ID",
                    help="★ 确认后的真发布：用上一步给的草稿 media_id 调 freepublish/submit"
                         "（认证号才行；没权限会如实报 48001 并告诉你后台怎么发）")
    args = ap.parse_args(argv)

    cfg, cfg_note = load_config()

    if args.myip:
        return cmd_myip(cfg, args.account)
    if args.publish_go:
        return cmd_publish_go(cfg, args.account, args.publish_go)
    if args.check:
        return cmd_check(cfg, cfg_note, args.account)
    if args.with_draft and not args.probe_full:
        print("错误：--with-draft 只能配合 --probe-full 使用（它会往草稿箱里放一篇测试草稿）。")
        return 2
    if args.probe_full:
        return cmd_probe_full(cfg, args.account, with_draft=args.with_draft)
    if args.probe:
        return cmd_probe(cfg, args.account)

    if not args.article:
        ap.print_help()
        print("\n错误：请给出文章 Markdown 路径，或用 --check / --probe 做体检。")
        return 2
    if not os.path.isfile(args.article):
        print("错误：找不到文章文件：%s" % args.article)
        return 1

    acc, key, note = active_account(cfg, args.account)
    cap, why = account_capability(acc)

    mode = args.mode
    if mode == "auto":
        # ★ 分流只看密钥：配齐了就直发草稿箱；这一格明确标了 paste 就走粘贴（保底）。
        #   没配置 / 配置没配齐的，走下面的 else：明确报错并指向 setup.py，不悄悄降级。
        #   号型不参与判断 —— 个人订阅号已实测能三步全过。
        atype = normalize_type(acc.get("type"))
        if cap == "api" and atype != "paste":
            mode = "api"
            log("自动选路 -> 官方 API 直发草稿箱。原因：%s" % why)
            log("（不想直发就用 --mode paste，粘贴模式永远可用。）")
        elif cap == "api":
            # 这一格明确标了 paste（用户选过「粘贴模式」）—— 尊重选择，即使密钥还在
            mode = "paste"
            log("自动选路 -> 粘贴模式（这一格标的是 paste）。原因：%s" % why)
        elif cap == "paste":
            mode = "paste"
            log("自动选路 -> 粘贴模式。原因：%s" % why)
        else:
            # ★ 没配置（cap=none）或配置没配齐（cap=error）：明确报错并指向配置。
            #   以前这里是「静默降级成粘贴模式」—— 用户想发布却只拿到一个 HTML 文件，
            #   根本不知道还有「配好密钥就能直发草稿箱」这条路。这一层不靠 AI 读文档。
            no_config_help("发布", why=why, article=args.article, misconfig=(cap == "error"))
            return 1

    guard = guard_mod()
    drop_indexes = set()
    for piece in (args.drop_image or "").replace("，", ",").split(","):
        piece = piece.strip()
        if piece:
            if not piece.isdigit():
                print("错误：--drop-image 只收序号（逗号分隔），收到：%s" % piece)
                return 2
            drop_indexes.add(int(piece))

    # ★ 闸门 B（在动手之前先拦）：正文里不许有「改了什么 / 修改说明」这类过程性标题。
    #   两条路都拦 —— 粘贴模式排出来的 HTML 同样会进草稿箱。
    with open(args.article, "rb") as f:
        md_text = f.read().decode("utf-8-sig", errors="replace").replace(BOM, "")
    proc = guard.check_markdown(md_text)
    if proc:
        if args.ignore_guard:
            log("★ 你显式关掉了发布前闸门（--ignore-guard）：下面这些过程性标题会原样进草稿。")
            for x in proc:
                log("  ! %s" % x)
        else:
            log("")
            log("!! 发布前闸门 B 拦下：正文里有**过程性标题**（这段不是文章，是写给用户看的改动说明）。")
            for x in proc:
                log("  ✗ %s" % x)
            log("")
            log("为什么拦：「改了什么 / 修改说明 / 改动清单」这类内容一旦留在正文里，")
            log("  排版和发布会把它当文章一起带走，读者在公众号上会看到它。")
            log("怎么修（二选一）：")
            log("  1) 打开 %s，把这一段删掉，过程说明另存一个文件" % args.article)
            log("  2) 一条命令自动挪走：")
            log("     python scripts/guard.py \"%s\" --fix" % args.article)
            log("     （会把这一段挪到 %s.改动说明.md，正文保持干净）"
                % os.path.splitext(os.path.basename(args.article))[0])
            log("确认无误就是要这样发：加 --ignore-guard（不推荐）。")
            return 1

    if mode == "paste":
        if not acc:
            log("走粘贴模式（不需要密钥）。%s" % (note or ""))
        if args.publish:
            log("（提示：--publish 是给「直发草稿箱」那条路用的。粘贴模式不经过微信接口，")
            log("  没有可发布的草稿，所以这一步被忽略了；要发布请用 --mode api。）")
        out_html = args.output or (os.path.splitext(args.article)[0] + ".wechat.html")
        try:
            title, stats, size = make_paste_html(args.article, out_html, args.theme,
                                                 args.code_theme, full=True)
        except Exception as e:
            log("!! 生成可粘贴 HTML 失败：%s" % e)
            return 1
        log("")
        log("========== 粘贴模式：已完成 ==========")
        log("文章：%s" % (title or "(未识别到标题)"))
        log("HTML：%s（%s 字节）" % (os.path.abspath(out_html), format(size, ",")))
        log("主题：%s / 代码主题：%s" % (args.theme, args.code_theme))
        log("统计：H1 %d / H2 及以下 %d / 代码块 %d / 表格 %d / 列表 %d"
            % (stats["h1"], stats["h2"], stats["code"], stats["table"], stats["list"]))
        log("")
        log("接下来这样发：")
        log("  1) 用浏览器打开上面这个 HTML")
        log("  2) 在页面上全选（Ctrl+A）→ 复制（Ctrl+C）")
        log("  3) 打开公众号后台「草稿箱 → 新的创作 → 图文消息」，粘贴（Ctrl+V）")
        log("     浏览器复制会带上图片数据，公众号会自动把图上传到素材库")
        log("  4) 填标题、选封面，确认无误后自己点发布")
        log("")
        log("注意：本地图片路径直接粘是无效的，必须走浏览器复制这一步。")
        log("（粘贴模式下 img src 保持本地相对路径是**有意的** —— 浏览器复制会把图片数据一起带上去；")
        log("  直发草稿箱那条路则必须先转存到微信图床，见 SKILL.md「模块接缝处的契约」。）")
        return 0

    # ---- API 模式 ----
    if cap != "api":
        no_config_help("发布", why=why, article=args.article, misconfig=(cap == "error"))
        return 1

    appid = (acc.get("appid") or "").strip()
    secret = (acc.get("appsecret") or acc.get("secret") or "").strip()
    log("========== API 模式：发到草稿箱（不群发） ==========")
    log("账号：%s" % (acc.get("name") or key))
    log("appid：%s" % mask(appid))
    log("appsecret：%s" % mask(secret))
    log("★ 密钥只发往 %s，不发往任何其它域名。" % OFFICIAL_HOSTS[0])
    log("")
    if not args.cover:
        log("!! 缺少封面图。公众号草稿要求 thumb_media_id 必填，没有封面建不了草稿。")
        log("   请用 --cover 指定一张 jpg/png，例如：")
        log("   python scripts/publish.py \"%s\" --mode api --cover images/cover.jpg"
            % args.article)
        return 1

    # 先用本技能排版引擎生成正文 HTML（公众号需要的是 HTML，不是 Markdown）
    try:
        import typeset
        raw = open(args.article, "rb").read().decode("utf-8-sig", errors="replace").replace(BOM, "")
        title = ""
        for ln in raw.split("\n"):
            if ln.startswith("# "):
                title = ln[2:].strip()
                break
        if not title:
            log("!! 文章第一行不是「# 标题」，拿不到标题。公众号草稿的标题是必填项。")
            return 1
        body, _st = typeset.md_to_html(raw, typeset.THEMES[args.theme],
                                       typeset.CODE_THEMES[args.code_theme])
    except Exception as e:
        log("!! 排版失败，先修好文章再来发布：%s" % e)
        return 1

    log("文章：%s" % title)
    log("正文 HTML：%s 字符" % format(len(body), ","))
    log("封面：%s" % args.cover)
    log("")

    tok, err = step1_token(appid, secret)
    if err:
        log("")
        log("!! 第 1 步失败：")
        for line in err.split("\n"):
            log("  " + line)
        log("")
        log("建议：改走粘贴模式，不需要任何接口权限：")
        log("  python scripts/publish.py \"%s\" --mode paste" % args.article)
        return 1

    # ★ 正文图转存：这一步以前没有 —— 结果是草稿里所有图都碎掉（src 还指着本机路径）。
    body, img_report = transfer_body_images(
        tok, body, os.path.dirname(os.path.abspath(args.article)),
        gallery=find_article_gallery(args.article), drop=drop_indexes)
    print_image_report(img_report)

    # ★ 闸门 A（转存之后、建草稿之前）：正文里还有没有非微信图床的图。
    img_problems, img_stat = guard.check_images(body)
    if img_problems:
        if args.ignore_guard:
            log("★ 你显式关掉了发布前闸门（--ignore-guard）：下面这些图会以碎图形式进草稿。")
            for x in img_problems:
                log("  ! %s" % x)
        else:
            log("")
            log("!! 发布前闸门 A 拦下：正文里还有 %d 张图没进微信图床 —— 发出去就是碎图。"
                % img_stat["bad"])
            for x in img_problems:
                log("  ✗ %s" % x)
            log("")
            log("为什么拦：草稿里的 <img src=\"本机路径\"> 微信根本取不到，")
            log("  读者看到的就是一个个碎图占位。图必须先在微信自己的图床上。")
            log("怎么修：")
            log("  1) 把上面那几张图转成 jpg / png（微信图片接口只收这两种，且单张 1MB 以下）")
            log("  2) 图确实不想要了：--drop-image 3,7（按上面的序号丢弃，日志会如实写出来）")
            log("  3) 就是想先发出去：--ignore-guard（不推荐，草稿里那些图会是碎的）")
            return 1

    thumb, err = step2_upload_thumb(tok, args.cover)
    if err:
        log("")
        log("!! 第 2 步失败：")
        for line in err.split("\n"):
            log("  " + line)
        log("")
        log("建议：封面可以先用排版好的 HTML 手动在后台传；或改走粘贴模式。")
        return 1

    if args.dry_run:
        log("")
        log("--dry-run：已跑到「准备就绪」，**没有真的建草稿**。")
        log("  去掉 --dry-run 就会执行第 3 步建草稿。")
        return 0

    mid, err = step3_add_draft(tok, title, body, thumb,
                               author=args.author, digest=args.digest,
                               source_url=args.source_url)
    if err:
        log("")
        log("!! 第 3 步失败：")
        for line in err.split("\n"):
            log("  " + line)
        log("")
        log("建议：改走粘贴模式，内容照样能发出去：")
        log("  python scripts/publish.py \"%s\" --mode paste" % args.article)
        return 1

    log("")
    log("草稿已建好（media_id=%s）。" % mid)

    if args.publish:
        # ★ 两步走的第一步：只建草稿 + 把该看的报出来，**不发布**
        print_publish_checklist(title, args.cover, args.digest, acc, key, appid)
        log("")
        log("========== 还没有发出去 ==========")
        log("草稿已经建好了。上面三样确认无误，再跑这下一条**真发**")
        log("（复用刚建好的草稿，不会重复建、不会重复传封面）：")
        log("  python scripts/publish.py --publish-go %s%s"
            % (mid, (" --account %s" % args.account) if args.account else ""))
        log("")
        log("★ 发出去就不可撤销、所有关注者都能看到 —— 这就是要先看一眼的原因。")
        return 0

    log("========== 完成 ==========")
    log("去公众号后台「草稿箱」看一眼，确认无误后自己点发布。")
    log("想让它替你发布：加 --publish（先看清楚标题/封面/摘要，再 --publish-go 真发）。")
    log("★ 本脚本**没有**群发接口，永远不会替你群发。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
