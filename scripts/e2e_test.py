#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端测试 —— anan-wechat-suite
================================
一条命令把**整条流水线**跑一遍：抓真文章 -> 改写 -> 排版 -> 转存正文图 -> 建草稿 -> 回读校验。

★ 这个脚本为什么存在（写下来，免得以后又被删掉）：
    2026-10-06 用户拿一篇真文章跑完整流程，草稿箱里出了两个问题：
      ① 所有图都碎掉 —— 模块之间从来没串起来跑过，发布那一步假设「微信能取到这些图」；
      ② 「改了什么」那段说明混进了正文 —— 上游产出里带着过程说明，发布假设「这就是正文」。
    这两个都不是模块内部的 bug，是**接缝**上的 bug。分模块测过一百遍也测不出来。
    所以这里有一条**真材实料的端到端**：输入是真实抓取的带图长文 + 真实的改写稿，
    **不许再用「<p>这是一条测试。</p>」这种一句话测试稿** ——
    那种测试用例太干净，把真实路径全绕过去了。

它验什么：
    [1] 抓取       真联网抓一篇带图文章，图确实落到本地，正文里确实有图片引用
    [2] 改写契约   改写提示词必须要求「正文文件」和「改动说明文件」分开（防 ② 复发）
    [3] 改写稿     是一份真实的、带图的、正文干净的长文
    [4] 排版       Markdown -> HTML，图一张不少
    [5] 反证·闸门B 往正文里塞一段「## 改了什么」-> **必须被拦**，且一个字都不许发出去
    [6] 反证·闸门A 造一份指向本机路径的正文 -> **必须被拦**
    [7] 转存       正文图逐张转存到微信图床（media/uploadimg）
    [8] 闸门A(真)  转存之后，正文里再没有一张非微信图床的图
    [9] 建草稿     真调 draft/add
    [10] 回读校验  用 draft/get 把草稿读回来：每个 img src 都必须在腾讯图床域上、
                   正文里不许出现过程性标题 —— **这一条才是「草稿箱里的图是不是好的」**

用法：
    python scripts/e2e_test.py                      # 全链路（要网络；配了密钥就真建草稿并回读）
    python scripts/e2e_test.py --no-publish         # 跑到「转存 + 闸门」就停，不碰草稿箱
    python scripts/e2e_test.py --offline --dir <抓好的目录>   # 不联网，用现成的真实产物
    python scripts/e2e_test.py --url <链接>          # 换一篇抓
    python scripts/e2e_test.py --draft 改写稿.md     # 指定一份真实改写稿（AI 写的那份）
    python scripts/e2e_test.py --cleanup            # 测完把自检草稿删掉

退出码：
    0 = 全部通过
    1 = 有失败项（失败详情已打印，且**不会**继续往下发）
    2 = 参数错误
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOM = chr(0xFEFF)
Q = chr(96)

if HERE not in sys.path:
    sys.path.insert(0, HERE)

DEFAULT_URL = "https://mp.weixin.qq.com/s/h0IthL_gWPmyT8kMbc537w"
DRAFT_TITLE_PREFIX = "【E2E 自检】"

RESULTS = []


def log(m=""):
    print(m, flush=True)


def record(ok, name, detail=""):
    RESULTS.append((ok, name, detail))
    log("  %s  %s%s" % ("PASS" if ok else "FAIL", name, ("  —— " + detail) if detail else ""))


def run_script(script, argv, timeout=600):
    """跑同目录下的另一个脚本，返回 (退出码, 合并后的输出)。"""
    cmd = [sys.executable, os.path.join(HERE, script)] + list(argv)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=timeout, env=env)
    return p.returncode, p.stdout.decode("utf-8", errors="replace")


def read_text(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8-sig", errors="replace").replace(BOM, "")


def write_text(path, text):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def find_first_article_md(out_dir):
    """在抓取产物里找正文 Markdown（跳过信息表）。"""
    for dirpath, dirnames, filenames in os.walk(out_dir):
        dirnames[:] = [d for d in dirnames if d != "images"]
        for fn in sorted(filenames):
            if fn.endswith(".md") and not fn.startswith("文章信息") and fn != "富媒体清单.md":
                return os.path.join(dirpath, fn)
    return None


def count_images(md_text):
    return len(re.findall(r"!\[[^\]]*\]\(([^)]+)\)", md_text))


def mechanical_restructure(text):
    """没有 --draft 时的替身改写：脚本替不了 AI 的文学改写，只做**结构重排**（相邻段落合并）。

    目的不是假装会写文章，而是保证端到端跑在一份**真实的、带图的长文**上 ——
    而不是「<p>这是一条测试。</p>」。图片块、标题、引用块原样保留、位置不动。
    """
    lines = text.split("\n")
    out, buf = [], []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        structural = (s.startswith("#") or s.startswith("![") or s.startswith(">")
                      or s.startswith("- ") or s.startswith("* ") or re.match(r"^\d+\.\s", s))
        if structural:
            if buf:
                out.append(" ".join(buf)); buf = []
            out.append(s)
        else:
            buf.append(s)
            if len(buf) >= 2:
                out.append(" ".join(buf)); buf = []
    if buf:
        out.append(" ".join(buf))
    return "\n\n".join(out) + "\n"


def build_draft(article_md, work_dir, draft_arg):
    """产出「改写稿.md」：给了 --draft 就用真人的，没给就机械重排一份。"""
    if draft_arg:
        if not os.path.isfile(draft_arg):
            raise SystemExit("找不到 --draft 指定的改写稿：%s" % draft_arg)
        dst = os.path.join(os.path.dirname(article_md), "改写稿.md")
        write_text(dst, read_text(draft_arg))
        return dst, "用你给的 --draft（真实改写稿）"
    dst = os.path.join(os.path.dirname(article_md), "改写稿.md")
    write_text(dst, mechanical_restructure(read_text(article_md)))
    return dst, "机械结构重排（没给 --draft；脚本替不了 AI 的文学改写）"


# ---------------------------------------------------------------- 阶段
def stage_fetch(args):
    log("")
    log("========== [1/10] 抓真文章（真联网） ==========")
    out_dir = os.path.join(args.work, "out")
    if args.offline:
        md = args.dir and find_first_article_md(args.dir)
        if not md:
            record(False, "抓取", "--offline 需要 --dir 指向一份已有的抓取产物")
            return None
        record(True, "抓取（离线复用现成产物）", md)
        return md
    code, out = run_script("fetch_article.py", [args.url, "--mode", "fast", "-o", out_dir])
    log(out.strip())
    md = find_first_article_md(out_dir)
    if code != 0 or not md:
        record(False, "抓取", "fetch_article.py 退出码 %s" % code)
        return None
    text = read_text(md)
    imgs = count_images(text)
    img_dir = os.path.join(os.path.dirname(md), "images")
    on_disk = len(os.listdir(img_dir)) if os.path.isdir(img_dir) else 0
    ok = imgs >= 1 and on_disk >= 1
    record(ok, "抓取", "正文 %d 字 / 图片引用 %d 处 / 本地图 %d 个"
           % (len(re.sub(r"\s", "", text)), imgs, on_disk))
    if not ok:
        record(False, "抓取", "这篇没有图 —— 端到端必须跑在**带图**的真实素材上")
    return md


def stage_rewrite_prompt(article_md, work_dir):
    log("")
    log("========== [2/10] 改写提示词的交付契约 ==========")
    prompt_path = os.path.join(work_dir, "改写提示词.md")
    code, out = run_script("article_writer.py",
                           ["--rewrite", "restructure", "--input", article_md, "-o", prompt_path])
    if code != 0 or not os.path.isfile(prompt_path):
        record(False, "改写提示词", "article_writer.py 退出码 %s" % code)
        return False
    prompt = read_text(prompt_path)

    def has_old_contract(p):
        """旧写法：让 AI 在正文的最后面接着写「改了什么」。"""
        return ("然后在最后用三行写清" in p
                or "先输出改写后的完整 Markdown 正文（第一行" in p)

    good = "两个文件" in prompt and "改动说明文件" in prompt
    # ★ 先自证这条断言的判据本身是对的：拿一段**旧写法**的样本文本喂进去，必须判为「旧」。
    #   （不这么验一次，判据写错也看不出来 —— 只会一直 PASS。）
    old_sample = ("先输出改写后的完整 Markdown 正文（第一行 # 标题），"
                  "然后在最后用三行写清：改了什么、为什么这么改、有没有动到事实。")
    record(has_old_contract(old_sample), "（自证）这条断言的判据认得出旧写法",
           "否则下面那条 PASS 没有意义")
    record(not has_old_contract(prompt) and good, "改写提示词要求正文与改动说明分成两个文件",
           "（这就是 bug ② 的源头：提示词一旦让 AI 把说明写在正文后面，它就会写进去）")
    if has_old_contract(prompt):
        record(False, "改写提示词", "还留着旧写法：让 AI 在正文最后写「改了什么」")
    return good and not has_old_contract(prompt)


def stage_draft(article_md, work_dir, draft_arg):
    log("")
    log("========== [3/10] 改写稿（真实长文 + 带图） ==========")
    draft_path, how = build_draft(article_md, work_dir, draft_arg)
    text = read_text(draft_path)
    imgs = count_images(text)
    chars = len(re.sub(r"\s", "", text))
    sys.path.insert(0, HERE)
    import guard
    proc = guard.check_markdown(text)
    import article_writer
    problems = article_writer.process_heading_problems(text)
    ok = imgs >= 1 and chars >= 500 and not proc
    record(ok, "改写稿", "%s；%d 字 / %d 张图 / 过程性标题 %d 处" % (how, chars, imgs, len(proc)))
    if not ok and proc:
        record(False, "改写稿", "正文里带过程性标题：%s" % proc[:2])
    if not ok and chars < 500:
        record(False, "改写稿", "太短（%d 字）—— 端到端不许用一句话测试稿" % chars)
    return draft_path


def stage_typeset(draft_path):
    log("")
    log("========== [4/10] 排版 ==========")
    sys.path.insert(0, HERE)
    import typeset
    import guard
    text = read_text(draft_path)
    body, stats = typeset.md_to_html(text, typeset.THEMES["anan"], typeset.CODE_THEMES["github-light"])
    n_md = count_images(text)
    n_html = len(guard.iter_img_srcs(body))
    ok = n_md >= 1 and n_html == n_md
    record(ok, "排版把 %d 张图一张不少地带进 HTML" % n_md, "HTML 里 %d 张" % n_html)
    return body, stats


def stage_negative_controls(draft_path, body, work_dir):
    log("")
    log("========== [5/10] 反证·闸门 B：正文里塞「改了什么」必须被拦 ==========")
    sys.path.insert(0, HERE)
    import guard
    bad_path = os.path.join(work_dir, "反证_带改动说明.md")
    bad = read_text(draft_path) + ("\n## 改了什么\n\n"
                                   "- 把原文的三段并成两段\n"
                                   "- 删掉了广告语气\n"
                                   "\n为什么这么改：原文太啰嗦。\n")
    write_text(bad_path, bad)
    problems = guard.check_markdown(bad)
    record(bool(problems), "guard.py 认出过程性标题", problems[0] if problems else "没认出来！")
    code, out = run_script("publish.py", [bad_path, "--mode", "paste", "-o",
                                          os.path.join(work_dir, "反证_不该生成.html")])
    blocked = code != 0 and "闸门 B" in out
    record(blocked, "publish.py 直接拦下这份稿（粘贴模式也拦）", "退出码 %s" % code)
    if not blocked:
        log(out[-2000:])
    # --fix 能把这一段挪走
    code2, out2 = run_script("guard.py", [bad_path, "--fix"])
    fixed = read_text(bad_path)
    moved = os.path.isfile(os.path.splitext(bad_path)[0] + ".改动说明.md")
    record(code2 == 0 and not guard.check_markdown(fixed) and moved,
           "guard.py --fix 把它挪到单独的 .改动说明.md", "退出码 %s" % code2)

    log("")
    log("========== [6/10] 反证·闸门 A：正文里留着本机图片路径必须被拦 ==========")
    local_html = re.sub(r'src="[^"]*"', 'src="images/01.png"', body)
    if "images/01.png" not in local_html:          # 正文里本来就没有图的话，造一张
        local_html = '<p>正文</p><img src="images/01.png"/>' + local_html
    img_problems, stat = guard.check_images(local_html)
    record(stat["bad"] >= 1 and bool(img_problems), "guard.py 认出非微信图床的 <img src>",
           img_problems[0] if img_problems else "没认出来！")

    # ★ 端到端反证：真的拿一份「正文图还是本机路径」的稿子去跑发布，必须是**非零退出**。
    #   这里故意放在一个没有 images\ 的目录里 —— 转存拿不到字节，闸门 A 必须把它拦在草稿之前。
    bad_dir = os.path.join(work_dir, "反证_本地图")
    bad_md = os.path.join(bad_dir, "反证_本地图.md")
    body_text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", read_text(draft_path)).strip()
    write_text(bad_md, body_text + "\n\n![图片](images/01.png)\n")
    cover = os.path.join(os.path.dirname(draft_path), "cover.jpg")
    code, out = run_script("publish.py",
                           [bad_md, "--mode", "api", "--cover", cover, "--dry-run"])
    blocked = code != 0 and "闸门 A" in out and "发出去就是碎图" in out
    record(blocked, "publish.py 拦下「正文图还是本机路径」的稿子（闸门 A 真跑）",
           "退出码 %s" % code)
    if not blocked:
        log(out[-2500:])
    # 显式丢弃那一张之后，同一份稿子应该放行（说明出路是通的，不是死路）
    code2, out2 = run_script("publish.py",
                             [bad_md, "--mode", "api", "--cover", cover, "--dry-run",
                              "--drop-image", "1"])
    ok2 = code2 == 0 and "已从正文里删掉这张" in out2
    record(ok2, "同一条命令加 --drop-image 1 就能过（给出路，不是死路）", "退出码 %s" % code2)
    return bad_path


def stage_transfer(draft_path, body):
    log("")
    log("========== [7/10] 正文图转存到微信图床（media/uploadimg） ==========")
    sys.path.insert(0, HERE)
    import publish
    import guard
    cfg = publish.load_config()[0]
    acc, key, note = publish.active_account(cfg, None)
    cap, why = publish.account_capability(acc)
    if cap != "api":
        record(False, "转存", "没有可用密钥（%s）—— 这一段需要真调微信接口" % why)
        return None, None, None
    tok, err = publish.step1_token((acc.get("appid") or "").strip(),
                                   (acc.get("appsecret") or acc.get("secret") or "").strip())
    if err:
        record(False, "转存", "换 access_token 失败：%s" % err.split("\n")[0])
        return None, None, None
    new_body, report = publish.transfer_body_images(
        tok, body, os.path.dirname(os.path.abspath(draft_path)),
        gallery=publish.find_article_gallery(draft_path), drop=())
    publish.print_image_report(report)
    bad = [r for r in report if r["status"] == "failed"]
    record(not bad, "每一张正文图都有去处（转存 / 用原图微信地址）",
           "共 %d 张，失败 %d 张" % (len(report), len(bad)))
    for r in bad:
        log("      · 第 %d 张：%s" % (r["index"], r["reason"]))
    return tok, new_body, report


def stage_gate_a(body):
    log("")
    log("========== [8/10] 闸门 A（转存之后，真跑一遍） ==========")
    sys.path.insert(0, HERE)
    import guard
    problems, stat = guard.check_images(body)
    record(not problems, "正文里所有 <img src> 都在腾讯图床域上",
           "共 %d 张，非图床 %d 张" % (stat["total"], stat["bad"]))
    for p in problems[:5]:
        log("      · %s" % p)
    return not problems


def stage_draft_add(tok, body, draft_path, title, cover):
    log("")
    log("========== [9/10] 建草稿（真调 draft/add） ==========")
    sys.path.insert(0, HERE)
    import publish
    thumb, err = publish.step2_upload_thumb(tok, cover)
    if err:
        record(False, "上传封面", err.split("\n")[0])
        return None
    full_title = (DRAFT_TITLE_PREFIX + title)[:64]
    mid, err = publish.step3_add_draft(tok, full_title, body, thumb,
                                       author="anan-wechat-suite 自检")
    if err:
        record(False, "建草稿", err.split("\n")[0])
        return None
    record(True, "草稿已建（标题带 %s 前缀，方便你一眼认出）" % DRAFT_TITLE_PREFIX, mid)
    return mid


def stage_read_back(tok, mid):
    log("")
    log("========== [10/10] 回读校验：草稿箱里那份，图到底是不是好的 ==========")
    sys.path.insert(0, HERE)
    import publish
    import guard
    url = "%s/cgi-bin/draft/get?access_token=%s" % (publish.API_BASE, tok)
    try:
        resp = publish.http_json(url, data={"media_id": mid})
    except RuntimeError as e:
        record(False, "回读草稿", str(e))
        return
    if resp.get("errcode"):
        record(False, "回读草稿", publish.explain(resp))
        return
    items = resp.get("news_item") or []
    if not items:
        record(False, "回读草稿", "接口没返回 news_item")
        return
    content = items[0].get("content") or ""
    problems, stat = guard.check_images(content)
    record(not problems, "草稿正文里每一张图都在腾讯图床域上（这就是「图是不是碎的」）",
           "草稿里 %d 张图，非图床 %d 张" % (stat["total"], stat["bad"]))
    for p in problems[:5]:
        log("      · %s" % p)
    proc = guard.check_markdown(content)
    record(not proc, "草稿正文里没有过程性标题（改动说明没被带进去）",
           "命中 %d 处" % len(proc))
    for p in proc[:5]:
        log("      · %s" % p)
    log("")
    log("  草稿标题：%s" % items[0].get("title"))


def stage_cleanup(tok, mid):
    log("")
    log("========== 清理：删掉这篇自检草稿 ==========")
    sys.path.insert(0, HERE)
    import publish
    url = "%s/cgi-bin/draft/delete?access_token=%s" % (publish.API_BASE, tok)
    try:
        resp = publish.http_json(url, data={"media_id": mid})
    except RuntimeError as e:
        log("  删除失败：%s" % e)
        return
    if resp.get("errcode"):
        log("  删除失败：%s" % publish.explain(resp))
        return
    log("  已删除（media_id=%s）" % mid)


# ---------------------------------------------------------------- 主流程
def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="端到端测试：抓真文章 -> 改写 -> 排版 -> 转存 -> 建草稿 -> 回读校验",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0=全过  1=有失败  2=参数错误")
    ap.add_argument("--url", default=DEFAULT_URL, help="要抓的真实文章链接")
    ap.add_argument("--dir", help="--offline 时用哪份现成的抓取产物")
    ap.add_argument("--offline", action="store_true", help="不联网抓取，用现成产物")
    ap.add_argument("--no-publish", action="store_true", help="只跑到转存 + 闸门，不建草稿")
    ap.add_argument("--draft", help="指定一份真实的改写稿（AI 写的那份）")
    ap.add_argument("--cover", help="封面图；默认用抓取产物里的 cover.jpg")
    ap.add_argument("--work", help="工作目录（默认 _e2e_run）")
    ap.add_argument("--cleanup", action="store_true", help="测完把自检草稿删掉")
    args = ap.parse_args(argv)

    # 工作目录默认落在技能目录里的 _e2e_run（打包脚本把它排除在 zip 之外）：
    # 抓下来的真文章、改写稿、反证件都留在那儿，方便你复看。
    args.work = args.work or os.path.join(ROOT, "_e2e_run")
    os.makedirs(args.work, exist_ok=True)
    t0 = time.time()

    log("========== anan-wechat-suite 端到端测试 ==========")
    log("技能包：%s" % ROOT)
    log("工作目录：%s" % args.work)
    log("文章：%s" % ("（离线，用 %s）" % args.dir if args.offline else args.url))

    article_md = stage_fetch(args)
    if not article_md:
        return 1
    stage_rewrite_prompt(article_md, args.work)
    draft_path = stage_draft(article_md, args.work, args.draft)
    body, _stats = stage_typeset(draft_path)
    stage_negative_controls(draft_path, body, args.work)

    cover = args.cover or os.path.join(os.path.dirname(article_md), "cover.jpg")
    if not os.path.isfile(cover):
        record(False, "封面", "找不到封面图：%s" % cover)
        return 1

    tok, new_body, _report = stage_transfer(draft_path, body)
    if not tok:
        return 1
    if not stage_gate_a(new_body):
        return 1

    mid = None
    if args.no_publish:
        log("")
        log("--no-publish：到「转存 + 闸门」为止，**没有碰草稿箱**。")
    else:
        title = ""
        for ln in read_text(draft_path).split("\n"):
            if ln.startswith("# "):
                title = ln[2:].strip()
                break
        mid = stage_draft_add(tok, new_body, draft_path, title, cover)
        if mid:
            stage_read_back(tok, mid)
            if args.cleanup:
                stage_cleanup(tok, mid)

    log("")
    log("========== 结果 ==========")
    bad = [r for r in RESULTS if not r[0]]
    for ok, name, detail in RESULTS:
        log("  %s  %s%s" % ("PASS" if ok else "FAIL", name, ("  —— " + detail) if detail else ""))
    log("")
    log("共 %d 项：通过 %d，失败 %d，耗时 %.1f 秒"
        % (len(RESULTS), len(RESULTS) - len(bad), len(bad), time.time() - t0))
    if mid and not args.cleanup:
        log("")
        log("★ 草稿箱里留了一篇标题以「%s」开头的草稿（media_id=%s）。" % (DRAFT_TITLE_PREFIX, mid))
        log("  去公众号后台「草稿箱」打开它，**看图是不是都正常显示** —— 这就是这次要验的东西。")
        log("  看完删掉：加 --cleanup 重跑，或手动在后台删。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
