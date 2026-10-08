# 用法速查（anan-wechat-suite）

> 全部命令都在**技能根目录**执行。Windows 下把 `python` 换成你的解释器路径。
> 每个脚本都支持 `--help`，拿不准就 `python scripts/xxx.py --help`。
>
> 忘了怎么叫它？说一句「**说明书**」（或「怎么用 / 教程 / 帮助」），它会把整份说明书原样贴出来。
>
> ★ **从哪一步开始看你手上有什么**：只有主题 → 搜选题开始；给一个链接 → 从抓开始；
> 给一篇稿子 → 从配图开始；只说「发出去」→ 只做发送。触发词（**全流程 / 一条龙 / 走一遍**）
> 与日常说法（「帮我写一篇 XX」「把这篇排版发出去」）都认。详见 SKILL.md 第 0 节。

---

## 零、先跑一次自检

```bash
python scripts/selftest.py
```

56 项离线检查，不需要网络和密钥。新环境先跑这个，能省掉一大半「为什么跑不通」的排查。

---

## 一、模块① 爆款文案搜索

```bash
# 只搜
python scripts/sogou_search.py "关键词"

# 多搜几条
python scripts/sogou_search.py "关键词" -n 20

# 解析出真实文章地址（mp.weixin.qq.com）
python scripts/sogou_search.py "关键词" -n 10 --resolve

# ★ 搜到就直接抓正文（前 2 篇）
python scripts/sogou_search.py "关键词" --fetch 2 -o output

# 存 JSON
python scripts/sogou_search.py "关键词" --resolve --json results.json

# 跳过 cookie 预热（预热失败时也可以这样试）
python scripts/sogou_search.py "关键词" --no-warmup
```

**参数**：`-n` 取几条（默认 10）· `--page` 起始页 · `--resolve` 解析真实链接 ·
`--fetch N` 抓前 N 篇正文（隐含 resolve）· `-o` 输出根目录 · `--json` 存 JSON · `--timeout` 超时秒数

**退出码**：`0` 至少拿到一条搜索结果 · `1` 搜索失败（被拦 / 无结果 / 网络错误）· `2` 参数错误

---

## 二、模块②③ 抓文章

```bash
# 快档：正文 + 图片 + 封面 + 信息表
python scripts/fetch_article.py "https://mp.weixin.qq.com/s/xxxx" --mode fast -o output

# 全档：再加富媒体清单 + 原始页面存档
python scripts/fetch_article.py "https://mp.weixin.qq.com/s/xxxx" --mode full -o output

# 只抓文字
python scripts/fetch_article.py "https://mp.weixin.qq.com/s/xxxx" --no-images

# 批量（务必限速）
python scripts/fetch_article.py --urls-file urls.txt --mode fast --delay 5 -o output
```

**参数**：`--mode fast|full` · `-o` 输出根目录 · `--no-images` · `--delay` 篇间隔秒数 ·
`--timeout` 超时秒数 · `--urls-file` 批量清单（每行一个链接，`#` 开头是注释）

**退出码**：`0` 至少成功一篇 · `1` 全部失败 · `2` 没给链接

`urls.txt` 长这样：

```text
# 我的素材库
https://mp.weixin.qq.com/s/h0IthL_gWPmyT8kMbc537w
https://mp.weixin.qq.com/s/xxxxxxxx
```

> ⚠️ 链接里有 `&` 时（`?__biz=...&mid=...`），命令行里**一定要加引号**，
> 否则 shell 会把 `&` 当成后台符号截断。

---

## 三、模块④⑤ 写作与改写

```bash
# 看有哪些口吻和模式
python scripts/article_writer.py --list

# 拿规则（这份 prompt.md 同时也是那份写作提示词，副产品）
#   ★ 然后由你（AI）**直接按它写出 初稿.md** —— 不要再把提示词转手给别人写
python scripts/article_writer.py --style business-insight --topic "选题" -o prompt.md
python scripts/article_writer.py --style personal-voice  --topic "选题" -o prompt.md
python scripts/article_writer.py --style viral-structure --topic "选题" --words 1500 -o prompt.md
python scripts/article_writer.py --style finance-depth  --topic "选题" --material 素材.md -o prompt.md

# 不写文件，直接打印到屏幕
python scripts/article_writer.py --style personal-voice --topic "选题"

# ★ 写完拿同一套规则验收
python scripts/article_writer.py --check 初稿.md --style personal-voice

# 改写（拿提示词 -> 你自己按它改 -> 再校验）
python scripts/article_writer.py --rewrite restructure --input 初稿.md -o 提示词.md
python scripts/article_writer.py --rewrite rewrite    --input 初稿.md -o 提示词.md
python scripts/article_writer.py --rewrite humanize   --input 初稿.md -o 提示词.md

# 改写稿校验（对比原文，验不膨胀、不新增事实）
python scripts/article_writer.py --check-rewrite 改写稿.md --rewrite rewrite --input 初稿.md
```

**口吻 key**：`business-insight` 商业洞察 · `viral-structure` 爆款结构 ·
`personal-voice` 个人口吻 · `finance-depth` 财经深度

**改写模式 key**：`restructure` 结构重组 · `rewrite` 语言改写 · `humanize` 加真实感

**退出码**：`0` 正常（--check 时表示没发现违规）· `1` --check 发现违规 / 文件读不到 · `2` 参数错误

---

## 四、模块⑥ 配图提示词

```bash
# 看有哪些文章类型与风格
python scripts/illustration.py --list-types

# 出配图方案（提示词 + 施工单）
python scripts/illustration.py --input 初稿.md -o 配图方案.md

# 指定类型 / 风格 / 张数
python scripts/illustration.py --input 初稿.md --type tutorial --style tech-blue --slots 4
python scripts/illustration.py --input 初稿.md --style paper-ink --slots 3

# 标题没识别出来时手动给主题
python scripts/illustration.py --input 初稿.md --topic "我的选题"
```

**文章类型**：`tutorial` 工具教程 · `opinion` 行业观点 · `story` 个人故事 ·
`product` 产品测评 · `news` 资讯速递 · `data` 数据复盘（默认自动判断）

**风格预设**：`tech-blue` 科技蓝 · `paper-ink` 纸感墨色 · `warm-life` 暖调生活 · `data-clean` 数据清爽

> 本模块**不出图**，只出提示词。出图在豆包工作里做。

---

## 五、模块⑦ 排版

```bash
# 看主题
python scripts/typeset.py --list-themes

# 正文片段（用于粘贴）
python scripts/typeset.py article.md -o article.html

# 完整文档（浏览器可直接打开预览）
python scripts/typeset.py article.md --full-html -o article.html

# 换主题
python scripts/typeset.py article.md --theme ink --code-theme monokai -o out.html
python scripts/typeset.py article.md --theme night --full-html -o out.html

# 去掉抓取器写在开头的「公众号/作者/时间」信息块
python scripts/typeset.py article.md --strip-header -o out.html
```

**页面主题**：`anan`（默认）· `ink` · `warm` · `forest` · `night`
**代码主题**：`github-light`（默认）· `vscode-dark` · `monokai` · `one-dark` · `solarized-light`

**退出码**：`0` 成功 · `1` 输入为空 / 读不到 · `2` 主题名或参数错

---

## 六、模块⑧ 配置与发布

**第一次用先配好**（问几个问题就写好 config.json，**不用手改任何文件**）：

```bash
# 选 1 = 直发草稿箱（会问 AppID 与 AppSecret，AppSecret 隐藏回显）—— 个人订阅号也能直发，已实测
# 选 2 = 粘贴模式（一个密钥都不问，最后一步手动粘贴）
python scripts/setup.py

# 重置过 AppSecret：只换密钥，别的一个问题都不问
python scripts/setup.py --update-secret

# 不提问（给自动化用；已有配置时必须再加 --force）
python scripts/setup.py --non-interactive --kind personal
python scripts/setup.py --non-interactive --kind service --appid wx1234567890abcdef --appsecret 32位密钥

# 只看当前配置（密钥一律打码）
python scripts/setup.py --check
```

> AppID / AppSecret 在「微信开发者平台 → 首页/服务号 → 选你的公众号 → 基础信息」。
> 老路径「公众号后台 → 设置与开发 → 基本配置」2025-12-01 起已迁移，打不开了。

```bash
# 本机出口 IP + 该去哪儿把它加进白名单（不加就报 40164）
python scripts/publish.py --myip

# 体检：看配置与账号能力
python scripts/publish.py --check

# 只验第 1 步：换 token（= AppID/AppSecret/IP 白名单）—— 过了 ≠ 能发文章
python scripts/publish.py --probe

# 逐步验到底：换 token → 真上传一张测试小图（验素材权限）
python scripts/publish.py --probe-full

# 再验建草稿（会在草稿箱留一篇【测试】草稿，用完自己删）
python scripts/publish.py --probe-full --with-draft

# 自动选路（推荐）：配了密钥就直发草稿箱；**没配置会明确报错并指向 setup.py**，不会悄悄降级
python scripts/publish.py 文章.md --mode auto --cover images/cover.jpg

# 走粘贴模式（没配密钥 / 不想配时；**唯一不需要任何配置**的路，要显式加 --mode paste）
python scripts/publish.py 文章.md --mode paste --theme anan
python scripts/publish.py 文章.md --mode paste -o 待粘贴.html

# 强制走 API（服务号）
python scripts/publish.py 文章.md --mode api --cover images/cover.jpg
python scripts/publish.py 文章.md --mode api --cover images/cover.jpg --dry-run

# ★ 要连发布一起做（认证号）：两步走，中间那一步是给人看的
python scripts/publish.py 文章.md --mode api --cover images/cover.jpg --publish
#    -> 建草稿 + 报「标题 / 封面 / 摘要」，**不会发布**；确认后再跑它给出的那条：
python scripts/publish.py --publish-go <上一步的 media_id>          # 真发；没权限会如实报 48001

# 指定账号
python scripts/publish.py 文章.md --mode auto --account personal
```

**setup.py 参数**：`--check` 只看配置 · `--update-secret` 只换 AppSecret · `--force` 直接覆盖已有配置 · `--non-interactive` 不提问 ·
`--kind personal|service` · `--name` · `--appid` · `--appsecret`

**publish.py 参数**：`--myip` 查本机出口 IP · `--mode auto|api|paste` · `--account` 指定账号 ·
`--cover` 封面图（api 模式必填）· `--theme` / `--code-theme` · `-o` 粘贴模式的 HTML 输出路径 ·
`--author` 草稿作者 · `--digest` 摘要 · `--source-url` 原文链接 · `--dry-run` ·
`--publish`（建草稿 + 报三样，先不发）· `--publish-go MEDIA_ID`（★ 确认后才真发，不可撤销）
**没有群发参数，以后也不会有** —— 群发（推送给粉丝）本技能有意不做：打扰粉丝 / 不可撤销 / 一天 1 次额度。

**退出码**：`0` 成功 · `1` 失败（没密钥 / 权限不足 / IP 白名单 / 网络…）· `2` 参数错误
**setup.py 退出码**：`0` 配置已写好 · `1` **没有写**任何文件（取消 / 输入不合法 / 写盘失败）· `2` 参数错误

---

## 七、把流程串起来：一次完整的发文

```bash
# 0) 先自检 + 配好账号（想直发选 1 配密钥，个人号也能直发；不想配选 2 走粘贴）
#    另外：抓 / 搜 / 写 / 改 / 图 / 排 都不需要配置，没配置照样跑 —— 只有发布要（或者显式 --mode paste）
python scripts/selftest.py
python scripts/setup.py

# 1) 找选题，顺手抓两篇对标
python scripts/sogou_search.py "AI 写作 工具" -n 10 --fetch 2 -o output

# 2) 精读对标后，抓一篇完整的存档
python scripts/fetch_article.py "https://mp.weixin.qq.com/s/xxxx" --mode full -o output

# 3) 拿写作规则（prompt.md = 副产品，可留档 / 可拿去别的工具用）
python scripts/article_writer.py --style personal-voice --topic "我用 AI 写作踩过的三个坑" -o prompt.md
#    -> ★ 你直接按它写出 初稿.md（不要把提示词转手给别人），然后用第 4 步验收

# 4) 验收
python scripts/article_writer.py --check 初稿.md --style personal-voice

# 5) 配图提示词
python scripts/illustration.py --input 初稿.md -o 配图方案.md
#    -> 把提示词交给豆包工作出图，存进 images/

# 6) 排版
python scripts/typeset.py 初稿.md --theme anan --full-html -o 初稿.html

# 7) 发布（默认只到草稿箱；要连发布一起做加 --publish，看过三样再 --publish-go）
python scripts/publish.py 初稿.md --mode auto --cover images/cover.jpg
```

---

## 八、退出码速查

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 失败：原因会打在同一屏上，照做即可 |
| 2 | 参数错误：命令没给对 |

**所有脚本都遵守这个约定，而且失败时一定说话。**
任何情况下都不会出现「没抓到东西却打印成功」这种事。

---

作者：阿南 rockmoons（微信：rockmoons）
