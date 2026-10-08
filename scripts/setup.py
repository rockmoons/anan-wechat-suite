#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置向导 —— anan-wechat-suite
==============================
一条命令问几个问题，自动写好 config.json。**你不需要打开、也不需要修改任何文件。**

为什么要有这个脚本：

    1) 微信的密钥页 2025-12-01 起迁到了「微信开发者平台」。
       老路径「公众号后台 → 设置与开发 → 基本配置」2025-12-01 起已迁移，打不开了；
       网上绝大多数教程还在教老路径，照着找必然卡住。
    2) 两条路跟**号型无关**，只跟你「愿不愿意配密钥」有关：
       · 配了 AppID + AppSecret + IP白名单 -> 官方 API 直发草稿箱
         （2026-10-06 实测：个人订阅号、个人主体、未认证，同样三步全过）
       · 不想配 / 配不通 -> 粘贴模式（永远可用的保底：排版自动，最后一步手动粘贴）
       所以第一个问题问的是「你想怎么发」，不是「你是什么号」。
    3) 手写 JSON 容易写坏引号、逗号、BOM，写坏了还看不出错在哪。

用法：
    python scripts/setup.py                        # 交互式：问几个问题就写好（推荐）
    python scripts/setup.py --update-secret        # 只换 AppSecret，别的一个都不问
    python scripts/setup.py --check                # 只看当前配置（密钥一律打码）
    python scripts/setup.py --force                # 已有配置也直接覆盖，不再询问
    python scripts/setup.py --non-interactive --kind api --appid wx... --appsecret ...
    python scripts/setup.py --non-interactive --kind paste      # 旧写法 personal 也认
    python scripts/setup.py --update-secret --appsecret ...   # 非交互换密钥

★ 反复跑是安全的（幂等）：
    · 问 AppID / AppSecret 时**直接回车 = 保持现在的值**，不会清空、不会写坏；
    · 只想换密钥就用 --update-secret，其它字段原样不动；
    · 已有配置时会先问「要覆盖吗」，答 n 或直接回车就什么都不写。

★ 回显一律打码（形如 abcd...wxyz（共 32 位）），AppSecret 输入时隐藏显示（getpass），
  明文只写进本机 config.json，不打屏、不写日志、不外发。

退出码：
    0 = config.json 已写好（或 --check 正常读到配置）
    1 = **没有写**任何文件（用户取消 / 输入不合法 / 写盘失败 / 没得改，原因都会打在屏幕上）
    2 = 参数错误
"""

import argparse
import getpass
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(ROOT, "config.json")

if HERE not in sys.path:
    sys.path.insert(0, HERE)

import publish  # noqa: E402  —— 复用同一套打码与配置读取，保证和发布脚本口径一致

# 两格 = 两条路：KIND_SERVICE 那一格走 API 直发，KIND_PERSONAL 那一格走粘贴。
# ★ 这跟「你的号是不是服务号」无关 —— 2026-10-06 实测：个人订阅号（未认证）也能直发草稿箱。
KIND_PERSONAL = "personal"
KIND_SERVICE = "service"
# CLI 上更直观的别名：api = 直发草稿箱 / paste = 粘贴模式（service / personal 是旧写法，继续认）
KIND_ALIASES = {"api": KIND_SERVICE, "service": KIND_SERVICE,
                "paste": KIND_PERSONAL, "personal": KIND_PERSONAL}

DEFAULT_NAME = {"personal": "我的公众号", "service": "我的公众号"}
PLACEHOLDER_NAME = "（未配置）"

# ---- 写进 config.json 的说明文字。路径是 2026 年实测有效的，别改回老路径 ----
NOTE_ABOUT = "本文件由 python scripts/setup.py 生成，**不要手动改它**；要改就跑那条命令。"
NOTE_SECURITY = ("密钥只存在本机这个文件里，只发往 api.weixin.qq.com 官方域名，"
                 "不上传、不外发、不写日志。不要把这个文件发给任何人，也不要提交到 git。")
NOTE_HOWTO = ("AppID 与 AppSecret 在「微信开发者平台」（首页/服务号 → 选你的公众号 → 基础信息）；"
              "接口权限在「接口管理 → 接口权限与额度」。"
              "老路径「公众号后台 → 设置与开发 → 基本配置」2025-12-01 起已迁移，打不开了。")
NOTE_IP = ("「基础信息」页下方的「API IP白名单」要加上本机出口 IP（跑 python scripts/publish.py --myip 能看到），"
           "否则换 token 报 40164。家庭宽带 IP 会变，变了要重加——这正是「粘贴模式」存在的意义。")

SCENARIO_LEAK = ("如果你怀疑 AppSecret 泄露了：去微信开发者平台 → 选你的公众号 → 基础信息"
                 "（AppID/AppSecret 那一页）→ 开发密钥 →「重置」，旧密钥立刻作废，"
                 "然后回来重跑一次 python scripts/setup.py 把新的填进去。")
SCENARIO_IP = ("报 40164 就是 IP 不在白名单。跑 python scripts/publish.py --myip 看该加哪个 IP，"
               "加在「基础信息 → API IP白名单」。")


class Cancelled(Exception):
    """用户取消或输入结束：此时**什么都不写**，绝不留下半份配置。"""


def log(m=""):
    print(m, flush=True)


def mask(s):
    """密钥 / AppID 上屏前一律打码。"""
    return publish.mask(s)


def has_secret(cfg):
    """配置里是否已经存了 AppSecret（只看有没有，不打印内容）。"""
    for acc in ((cfg or {}).get("accounts") or {}).values():
        if (acc.get("appsecret") or acc.get("secret") or "").strip():
            return True
    return False


# ---------------------------------------------------------------- 校验
def validate_appid(v):
    """检查 AppID 形态。返回错误说明；空字符串表示通过。"""
    v = (v or "").strip()
    if not v:
        return "AppID 不能为空"
    if not v.startswith("wx"):
        return "AppID 以 wx 开头，你填的是：%s" % mask(v)
    if len(v) != 18:
        return "AppID 是 18 位（wx + 16 位），你填的是 %d 位：%s" % (len(v), mask(v))
    return ""


def validate_secret(v):
    """检查 AppSecret 形态。返回错误说明；空字符串表示通过。"""
    v = (v or "").strip()
    if not v:
        return "AppSecret 不能为空"
    if len(v) < 16:
        return "AppSecret 一般是 32 位，你填的只有 %d 位：%s" % (len(v), mask(v))
    return ""


# ---------------------------------------------------------------- 读写配置
def empty_account(name, kind):
    return {"name": name, "type": kind, "appid": "", "appsecret": ""}


def build_config(existing, kind, name, appid="", appsecret=""):
    """在已有配置之上生成新配置。

    只覆盖选中的那一格，另一格原样保留（不销毁用户已经填过的密钥）。
    type 记的是**路线**：api = 直发草稿箱 / paste = 粘贴模式（跟「号型」无关）。
    """
    accounts = (existing or {}).get("accounts") or {}
    merged = {
        KIND_SERVICE: accounts.get(KIND_SERVICE) or empty_account(PLACEHOLDER_NAME, KIND_SERVICE),
        KIND_PERSONAL: accounts.get(KIND_PERSONAL) or empty_account(PLACEHOLDER_NAME, KIND_PERSONAL),
    }
    if kind == KIND_PERSONAL:
        # 粘贴那一格：不动已有密钥（老配置里可能存着，留着不碍事，--mode api 还能用）
        merged[KIND_PERSONAL] = {"name": name, "type": "paste",
                                 "appid": appid.strip(), "appsecret": appsecret.strip()}
    else:
        merged[KIND_SERVICE] = {"name": name, "type": "api",
                                "appid": appid.strip(), "appsecret": appsecret.strip()}
    return {
        "_说明": NOTE_ABOUT,
        "_安全红线": NOTE_SECURITY,
        "_怎么填": NOTE_HOWTO,
        "_IP白名单": NOTE_IP,
        "active": kind,
        "accounts": merged,
    }


def write_config(path, cfg):
    """原子写：先写临时文件再替换，避免中途失败留下坏 JSON。"""
    tmp = path + ".tmp"
    text = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    if os.name == "posix":
        os.chmod(path, 0o600)
    return len(text)


# ---------------------------------------------------------------- 交互
def ask(prompt, default=""):
    """读一行。EOF（Ctrl+Z 或管道结束）视为取消。"""
    try:
        line = input(prompt)
    except EOFError:
        raise Cancelled("输入结束了（Ctrl+Z / Ctrl+C），没有写任何文件。")
    line = line.strip()
    return line if line else default


def ask_kind(default_kind):
    """第 1 步问的是「你想怎么发」，不是「你是什么号」——两条路跟号型无关。"""
    log("第 1 步：你想怎么把文章发到公众号？")
    log("")
    log("  [1] 直发草稿箱（推荐）—— 需要配 AppID + AppSecret + IP白名单，配一次以后全自动")
    log("      两种号都支持：个人订阅号（含未认证）、认证服务号，都实测通过")
    log("  [2] 走粘贴模式 —— 不用配任何东西，技能排好版你自己粘")
    log("      适合：不想折腾密钥、或公司不让配")
    log("")
    d = "1" if default_kind == KIND_SERVICE else "2"
    for _ in range(3):
        raw = ask("  请选择 [1/2]（直接回车 = %s）：" % d, d)
        if raw in ("1", "2"):
            return KIND_SERVICE if raw == "1" else KIND_PERSONAL
        log("  ！只能填 1 或 2，你填的是「%s」。" % raw)
    raise Cancelled("连续 3 次都没选对，没有写任何文件。")


def ask_name(kind, existing):
    acc = ((existing or {}).get("accounts") or {}).get(kind) or {}
    default = (acc.get("name") or "").strip()
    if not default or default == PLACEHOLDER_NAME:
        default = DEFAULT_NAME[kind]
    log("")
    log("第 2 步：给这个号起个名字（只写在本机配置里，随便填，不影响发布）")
    return ask("  公众号名称（直接回车 = %s）：" % default, default)


def ask_appid(existing):
    acc = ((existing or {}).get("accounts") or {}).get(KIND_SERVICE) or {}
    default = (acc.get("appid") or "").strip()
    log("")
    log("第 3 步：AppID")
    log("  在「微信开发者平台 → 前往控制台 → 我的业务与服务 → 基础信息」页面顶部，形如 wx 开头 18 位。")
    log("  （选号时注意：个人号在「公众号」那一项下，企业服务号在「服务号」那一项下 —— 两个是不同的菜单）")
    log("  还没登录过？先打开 https://mp.weixin.qq.com/，用【企业管理员】的个人微信扫码登录，")
    log("  再按上面的路径进；平台网址 https://developers.weixin.qq.com/platform（进去先点「前往控制台」）")
    tip = "（直接回车＝保持现在的 %s）" % mask(default) if default else ""
    for _ in range(3):
        raw = ask("  AppID%s：" % tip, default)
        err = validate_appid(raw)
        if not err:
            return raw.strip()
        log("  ！%s" % err)
    raise Cancelled("连续 3 次都没填对，没有写任何文件。")


def ask_secret(existing, attempts=3):
    """问 AppSecret。隐藏回显；**直接回车 = 保持现在的值**。"""
    acc = ((existing or {}).get("accounts") or {}).get(KIND_SERVICE) or {}
    default = (acc.get("appsecret") or acc.get("secret") or "").strip()
    log("")
    log("第 4 步：AppSecret")
    log("  在同一个「基础信息」页面点「启用」/「重置」后显示。**只显示一次**，看到就复制保存。")
    log("  下面是隐藏回显的输入（屏幕上不会显示你粘贴的内容），粘贴完直接回车。")
    if default:
        tip = "（直接回车＝保持现在的 %s；输入新值＝替换）" % mask(default)
    else:
        tip = "（输入新值，输入时不显示）"
    for _ in range(attempts):
        try:
            raw = getpass.getpass("  AppSecret%s：" % tip)
        except EOFError:
            raise Cancelled("输入结束了（Ctrl+Z / Ctrl+C），没有写任何文件。")
        except Exception as e:  # 极少数终端拿不到控制台
            raise Cancelled("无法隐藏回显（%s）。请在真正的终端窗口里跑本脚本，不要用管道喂输入。" % e)
        raw = raw.strip()
        if not raw and default:
            log("  已选择：保持现在的 AppSecret（%s）不变。" % mask(default))
            return default
        err = validate_secret(raw)
        if not err:
            return raw
        log("  ！%s" % err)
    raise Cancelled("连续 3 次都没填对，没有写任何文件。")


# ---------------------------------------------------------------- 输出
def print_next_steps(kind):
    log("")
    log("=" * 62)
    if kind == KIND_SERVICE:
        log("  下一步（就两条命令，会真的调微信接口）")
        log("=" * 62)
        log("")
        log("  python scripts/publish.py --probe-full      # 逐步验：换 token → 传素材（→ 加 --with-draft 再验建草稿）")
        log("")
        log("--probe 只验第 1 步（换 token）；**过了不等于能发文章** —— 要验到底就用 --probe-full。")
        log("个人订阅号也能直发草稿箱（2026-10-06 实测三步全过），所以这一步值得跑。")
        log("")
        log("以后要改密钥（比如重置过 AppSecret），**不用改任何文件**，重跑一次就行：")
        log("  python scripts/setup.py --update-secret")
        log("")
        log(SCENARIO_LEAK)
        log(SCENARIO_IP)
        log("")
        log("★ 密钥只存在本机 %s，只发往 api.weixin.qq.com，不上传不外发。" % CONFIG_PATH)
    else:
        log("  ★ 你选了粘贴模式（不需要任何密钥，永远可用的保底）")
        log("=" * 62)
        log("")
        log("不需要 AppID、不需要 AppSecret、不需要 IP 白名单也能发文。")
        log("能力边界说清楚：**写稿 / 改写 / 配图提示词 / 排版全自动**；")
        log("只有最后「把文章复制粘贴到公众号后台 + 点发布」这一步是**手动**的。")
        log("")
        log("（想改成直发草稿箱随时可以：重跑 python scripts/setup.py 选 [1]，")
        log("  把 AppID / AppSecret 配上就行 —— **两种号都支持**，个人订阅号已实测能直发。）")
        log("")
        log("以后发文就两条命令：")
        log("")
        log("  python scripts/typeset.py 文章.md --full-html -o 文章.html")
        log("  python scripts/publish.py 文章.md --mode paste")
        log("")
        log("然后用浏览器打开生成的 HTML → 全选复制（Ctrl+A / Ctrl+C）→ 粘进公众号编辑器。")
        log("")
        log("想看当前配置：python scripts/setup.py --check（密钥一律打码显示）")


def cmd_check():
    cfg, note = publish.load_config(CONFIG_PATH)
    log("========== 当前配置 ==========")
    log("")
    log("配置文件：%s" % CONFIG_PATH)
    if note:
        log("  状态：%s" % note)
        log("")
        log("还没有配置就跑：python scripts/setup.py")
        return 1
    log("  状态：已读取")
    acc, key, acc_note = publish.active_account(cfg)
    if not acc:
        log("当前账号：%s" % (key or "(无)"))
        log("  问题：%s" % acc_note)
        return 1
    log("当前账号：%s（配置名 %s）" % (acc.get("name") or "(未命名)", key))
    log("  appid    ：%s" % (mask(acc.get("appid")) if acc.get("appid") else "未配置"))
    log("  appsecret：%s" % mask(acc.get("appsecret") or acc.get("secret") or ""))
    raw_type = (acc.get("type") or "").strip().lower()
    log("  type     ：%s%s" % (raw_type or "(未指定)", publish.type_note(raw_type)))
    cap, why = publish.account_capability(acc)
    log("判定能力：%s" % {"api": "走官方 API 直发草稿箱",
                          "paste": "走粘贴模式（没配密钥；这条路永远可用）",
                          "error": "配置有误，需要先修",
                          "none": "没有可用账号配置"}[cap])
    log("  依据：%s" % why)
    if (acc.get("appsecret") or acc.get("secret") or "").strip() and publish.normalize_type(raw_type) == "paste":
        log("")
        log("  ★ 这一格存着密钥，但当前标着 paste（你选的是粘贴模式）—— 默认不会走接口。")
        log("    想走直发：跑文章时加 --mode api；或重跑 python scripts/setup.py 选 [1] 改成直发。")
        log("    （密钥留着没动，可以随时切回去。）")
    log("")
    log("要改密钥：python scripts/setup.py --update-secret（不用打开任何文件）")
    log("★ 上面显示的都是打码值，本脚本不会把完整密钥打到屏幕上。")
    return 0


def print_config_hint(have):
    if have:
        log("想只换 AppSecret（比如刚重置过密钥）？不用回答别的问题，直接跑：")
        log("  python scripts/setup.py --update-secret")
        log("")


# ---------------------------------------------------------------- 主流程
def interactive(force):
    existing, note = publish.load_config(CONFIG_PATH)
    have = os.path.isfile(CONFIG_PATH)
    # 默认选「直发草稿箱」（推荐路线）；已有配置时沿用当前那一格
    default_kind = (existing.get("active") or KIND_SERVICE) if have else KIND_SERVICE
    if default_kind not in (KIND_PERSONAL, KIND_SERVICE):
        default_kind = KIND_PERSONAL

    log("=" * 62)
    log("  anan-wechat-suite · 配置向导")
    log("  一条命令写好配置 —— 你不需要打开、也不需要修改任何文件")
    log("=" * 62)
    log("")
    log("先说清楚两件事（能省你半小时）：")
    log("  · 微信的密钥页 2025-12-01 起迁到了「微信开发者平台」；")
    log("    老路径「公众号后台 → 设置与开发 → 基本配置」已经打不开了，网上教程大多还是老的。")
    log("  · **你的号是哪种都行**：2026-10-06 实测，个人订阅号（个人主体、未认证）")
    log("    同样能换 token → 传素材 → 建草稿，**直发草稿箱**。")
    log("")

    if have:
        log("检测到已有配置：%s" % CONFIG_PATH)
        log("  里面的密钥不会被打出来，也不会随技能包外发；问到时**直接回车 = 保持现在的值**。")
        if force:
            log("  --force：跳过下面的询问，直接重新配置。")
        else:
            ans = ask("  要覆盖吗？[y/N]（直接回车 = 不覆盖）：", "n").strip().lower()
            if ans not in ("y", "yes", "是", "1"):
                raise Cancelled("你选择了不覆盖，没有改动任何文件。")
        log("")
        print_config_hint(have)

    kind = ask_kind(default_kind)
    name = ask_name(kind, existing)

    if kind == KIND_SERVICE:
        appid = ask_appid(existing)
        secret = ask_secret(existing)
        log("")
        log("-" * 62)
        log("将写入：%s" % CONFIG_PATH)
        log("  active    ：直发草稿箱（走官方 API）")
        log("  name      ：%s" % name)
        log("  appid     ：%s" % mask(appid))
        log("  appsecret ：%s" % mask(secret))
    else:
        # 粘贴路线：不动已有密钥（老配置里可能存着），只是不追问
        prev = ((existing or {}).get("accounts") or {}).get(KIND_PERSONAL) or {}
        appid = (prev.get("appid") or "").strip()
        secret = (prev.get("appsecret") or prev.get("secret") or "").strip()
        log("")
        log("-" * 62)
        log("将写入：%s" % CONFIG_PATH)
        log("  active    ：粘贴模式（不需要任何密钥）")
        log("  name      ：%s" % name)
        log("  密钥      ：跳过（粘贴模式不需要）")
        if appid and secret:
            log("  （这一格里原来存着密钥，我给你留着了 —— 想改回直发：重跑 setup.py 选 1）")

    cfg = build_config(existing if have else {}, kind, name, appid, secret)
    size = write_config(CONFIG_PATH, cfg)
    log("")
    log("已写好：%s（%d 字节）" % (CONFIG_PATH, size))
    print_next_steps(kind)
    return 0


def update_secret(existing, have, new_secret=None, kind=None):
    """只换 AppSecret，其它字段一律原样不动。"""
    log("=" * 62)
    log("  只更新 AppSecret —— 其它字段一个都不动")
    log("=" * 62)
    log("")
    if not have:
        log("!! 还没有配置：%s" % CONFIG_PATH)
        log("   先跑 python scripts/setup.py 做一次完整配置，再回来换密钥。")
        return 1
    acc, key, note = publish.active_account(existing, kind)
    if not acc:
        log("!! 读不出当前账号：%s" % note)
        log("   先跑 python scripts/setup.py --check 看一眼，或重跑 setup.py 重新配置。")
        return 1
    if not (acc.get("appid") or "").strip():
        # 没有 AppID = 这一格是粘贴路线，没有密钥可换
        log("当前账号「%s」这一格没有 AppID —— 它走的是粘贴模式，不需要密钥。"
            % (acc.get("name") or key))
        log("想改成直发草稿箱（两种号都支持，个人订阅号已实测能直发）：")
        log("  python scripts/setup.py            # 选 [1]，按提示填 AppID / AppSecret")
        log("现在就能发的路：python scripts/publish.py 文章.md --mode paste")
        return 1

    log(SCENARIO_LEAK)
    log("")
    log(SCENARIO_IP)
    log("")
    if new_secret is None:
        log("当前账号：%s（配置名 %s）" % (acc.get("name") or "(未命名)", key))
        log("  appid    ：%s" % (mask(acc.get("appid")) if acc.get("appid") else "未配置"))
        log("  appsecret：%s" % mask(acc.get("appsecret") or acc.get("secret") or ""))
        secret = ask_secret(existing)
    else:
        err = validate_secret(new_secret)
        if err:
            log("!! 参数不合法：%s" % err)
            return 2
        secret = new_secret.strip()
        log("当前账号：%s（配置名 %s）" % (acc.get("name") or "(未命名)", key))
        log("  新 appsecret：%s" % mask(secret))

    cfg = json.loads(json.dumps(existing))  # 深拷贝：只动 appsecret，其它原样保留
    accounts = cfg.setdefault("accounts", {})
    target = accounts.get(key) or {}
    target["appsecret"] = secret
    accounts[key] = target
    cfg.pop("_更新说明", None)
    size = write_config(CONFIG_PATH, cfg)
    log("")
    log("已更新：%s（%d 字节）" % (CONFIG_PATH, size))
    log("  appsecret：%s" % mask(secret))
    log("")
    log("下一步（验一次，确认新密钥和新白名单都通）：")
    log("  python scripts/publish.py --probe")
    log("")
    log("★ 其它字段（账号名、AppID、active）一个字都没动。")
    return 0


def non_interactive(args):
    kind = KIND_ALIASES.get((args.kind or "").strip().lower())
    if kind is None:
        log("!! --non-interactive 的 --kind 只能是 api / paste（也接受 service / personal 这两个旧写法）")
        return 2
    existing, _note = publish.load_config(CONFIG_PATH)
    have = os.path.isfile(CONFIG_PATH)
    if have and not args.force:
        log("!! 已存在 %s，非交互模式不会覆盖它。" % CONFIG_PATH)
        log("   要覆盖请显式加 --force（另一个账号的数据会被保留）。")
        log("   只想换密钥：--update-secret --appsecret ...（不用加 --force）")
        return 1

    if kind == KIND_SERVICE:
        err = validate_appid(args.appid) or validate_secret(args.appsecret)
        if err:
            log("!! 参数不合法：%s" % err)
            log("   密钥只回显打码值；请重新给出正确的 --appid / --appsecret。")
            return 2
        appid, secret = args.appid.strip(), args.appsecret.strip()
    else:
        # 粘贴路线：默认不带密钥；但如果主动给了，照样存下来（--mode api 时还能用）
        appid, secret = args.appid.strip(), args.appsecret.strip()
        if bool(appid) != bool(secret):
            log("!! --appid 与 --appsecret 要么都给，要么都不给（现在只给了一个）。")
            return 2
        if appid:
            err = validate_appid(appid) or validate_secret(secret)
            if err:
                log("!! 参数不合法：%s" % err)
                return 2

    name = (args.name or "").strip() or DEFAULT_NAME[kind]
    cfg = build_config(existing if have else {}, kind, name, appid, secret)
    size = write_config(CONFIG_PATH, cfg)
    log("已写好：%s（%d 字节）" % (CONFIG_PATH, size))
    if kind == KIND_SERVICE:
        log("  active    ：直发草稿箱（走官方 API）")
        log("  name      ：%s" % name)
        log("  appid     ：%s" % mask(appid))
        log("  appsecret ：%s" % mask(secret))
    else:
        log("  active    ：粘贴模式（不需要密钥；最后一步复制粘贴是手动的）")
        log("  name      ：%s" % name)
        if appid:
            log("  appid     ：%s" % mask(appid))
            log("  appsecret ：%s" % mask(secret))
            log("  ★ 这一格也带了密钥：可以用 python scripts/publish.py --probe-full --account personal 验一下")
            log("     （type 记的是 paste，所以自动选路仍走粘贴；要强制走 API 加 --mode api）")
        else:
            log("  密钥      ：未配置（粘贴模式不需要；想改成直发就重跑 setup.py 选 [1]）")
    print_next_steps(kind)
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="配置向导：问几个问题就写好配置，不需要打开或修改任何文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python scripts/setup.py                      # 交互式配置\n"
               "  python scripts/setup.py --update-secret      # 只换 AppSecret（重置密钥后用这条）\n"
               "  python scripts/setup.py --check              # 看当前配置（打码）\n"
               "  python scripts/setup.py --force              # 直接重配，不再询问\n"
               "  python scripts/setup.py --non-interactive --kind personal\n"
               "  python scripts/setup.py --update-secret --appsecret 32位新密钥\n")
    ap.add_argument("--check", action="store_true", help="只看当前配置（密钥一律打码），不写文件")
    ap.add_argument("--update-secret", action="store_true",
                    help="只更新当前账号的 AppSecret，其它字段一个字都不动")
    ap.add_argument("--force", action="store_true", help="已有配置时直接覆盖，不再询问")
    ap.add_argument("--non-interactive", action="store_true",
                    help="不提问，全靠参数；给自动化用（已有配置时必须再加 --force）")
    ap.add_argument("--kind", choices=["api", "paste", "service", "personal"],
                    help="走哪条路：api（旧写法 service）=直发草稿箱 / paste（旧写法 personal）=粘贴模式")
    ap.add_argument("--name", help="公众号名称（只写进本机配置）")
    ap.add_argument("--appid", default="", help="AppID（--kind api 用）")
    ap.add_argument("--appsecret", default="", help="AppSecret（--kind api 或 --update-secret 用）")
    args = ap.parse_args(argv)

    try:
        if args.check:
            return cmd_check()
        existing, _note = publish.load_config(CONFIG_PATH)
        have = os.path.isfile(CONFIG_PATH)
        if args.update_secret:
            secret = args.appsecret.strip() or None
            return update_secret(existing, have, new_secret=secret, kind=args.kind)
        if args.non_interactive:
            return non_interactive(args)
        return interactive(args.force)
    except Cancelled as e:
        log("")
        log("已取消：%s" % e)
        log("（退出码 1 = 没有写任何文件；重跑 python scripts/setup.py 可以再来一次。）")
        return 1
    except KeyboardInterrupt:
        log("")
        log("已取消：你按了 Ctrl+C，没有写任何文件。")
        return 1
    except OSError as e:
        log("")
        log("!! 写盘失败：%s" % e)
        log("   配置**没有**写成功，请检查目录权限后重试。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
