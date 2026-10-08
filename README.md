# anan-wechat-suite · 阿南 · 微信公众号全流程工作台

> 给「豆包工作」用的技能包：从**找一个好选题**到**文章进了公众号草稿箱**，八个模块串成一条流水线。

## 它能干什么

| 模块 | 你说什么 | 它做什么 |
|---|---|---|
| ① 搜选题 | 搜一下 XX 的爆款 | 搜狗微信搜索，列出候选文章 |
| ② 抓文章 | 把这篇文章抓下来 | 正文 + 图片，落成本地资料包 |
| ③ 抓全套 | 完整下载这篇（连视频） | 再加富媒体清单 + 原始页面存档 |
| ④ 写初稿 | 帮我写一篇 XX | 按选定口吻直接写（改动说明单独存文件）|
| ⑤ 改写 | 把这篇改写成我的口吻 | 按选定模式改写 |
| ⑥ 配图 | 配几张图 | 出配图提示词 |
| ⑦ 排版 | 帮我排版 | 一键排成公众号可用的 HTML |
| ⑧ 发布 | 放进草稿箱 / 发出去 | 建草稿；认证号可选直接发布 |

## 三条路，怎么选

```
① 只给主题        -> 全流程：搜选题 ->（你挑）-> 抓 -> 写 -> 配图 -> 排版 -> 发
② 给一个链接      -> 抓 -> 改写 -> 配图 -> 排版 -> 发
③ 给一篇稿子      -> 配图 -> 排版 -> 发
```

**忘了怎么说？敲「说明书」**，它会把完整用法原样贴出来。

## 安装

1. 下载本仓库（或用 Releases 里的 zip）
2. 在**豆包工作**里：市场 → 管理 → 上传技能 → 选择包含 `SKILL.md` 的文件夹/压缩包
3. 装好后在对话里说 **「配置」**，它会一步步问你要 AppID / AppSecret

> ⚠️ 上传的压缩包/文件夹必须**直接包含 `SKILL.md`**（不要多套一层文件夹）。

## 特点

- **零第三方依赖**：纯 Python 标准库，不需要 `pip install`
- **密钥只在你本机**：只发往 `api.weixin.qq.com`，不经过任何中转服务器
- **发布两步走**：`--publish` 先建草稿给你看，`--publish-go` 确认后才真发
- **群发永不做**：只到草稿箱/发布，不会替你推送给粉丝

## 环境

Python 3.8+（只用标准库）。Windows / macOS / Linux 均可。

## 授权

个人学习免费；**企业商用需先获得作者授权**（微信：rockmoons）。完整条款见 [LICENSE](LICENSE)。

Copyright (c) 2026 阿南 rockmoons

## 主要文件

- [`SKILL.md`](SKILL.md) —— 技能定义（给 AI 看的说明书 + 操作剧本）
- [`references/QUICKSTART.md`](references/QUICKSTART.md) —— 说明书（可单独发给使用者）
- [`references/USAGE.md`](references/USAGE.md) —— 命令与参数
- [`references/TROUBLESHOOTING.md`](references/TROUBLESHOOTING.md) —— 报错对照
- [`references/WECHAT-HTML-RULES.md`](references/WECHAT-HTML-RULES.md) —— 公众号 HTML 规则
- [`references/WRITING-STYLES.md`](references/WRITING-STYLES.md) —— 写作风格库
- [`scripts/e2e_test.py`](scripts/e2e_test.py) —— 端到端自检（抓取 → 改写 → 排版 → 建草稿）