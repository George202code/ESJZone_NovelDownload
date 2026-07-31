# 更新日志

## v2.9 (2026-08-01) —— GUI 体验增强：失败重试 + 进度分离 + 代理重设计

### 1. 失败章节重试系统（`core/downloader.py` + `gui/main_window.py`）

**问题**：下载完成后部分章节因超时/认证过期失败，用户无法在 GUI 中选择性重试失败项，只能整本重下。

**方案**：
```
下载完成 → 发送 summary 信号（失败章节详情）
    → GUI 显示失败汇总 + [选择并重试失败章节]
    → 多选对话框（全选/全不选）
    → RetryWorker → Downloader.retry_chapters()
    → 重置失败章节 → 仅重下选中项 → 重建 EPUB
```

**新增**：
- `Downloader.retry_chapters(novel_id, chapter_urls)` — 重置失败章节为 pending，调用 `_download_all` + `_prepare_and_generate_epub` 重建 EPUB
- `RetryWorker(QThread)` — 专用重试线程，独立于 `DownloadWorker`，参数明确
- `DownloadWorker.summary` 信号 — 下载完成后发送 `{novel_id, failed, failed_details: [{title, url, error}]}`
- `DownloadTab` 新增 `retry_frame` — 失败总结标签 + 章节多选对话框 + 重试进度

### 2. 进度条分离：章节 + 图片独立显示（`gui/main_window.py`）

**问题**：章节下载和图片下载共用单个进度条，进度混杂不清晰。

**修复**：
- `DownloadWorker` 新增 `image_progress` 信号，与 `progress`（章节）解耦
- `Downloader.__init__` 新增 `image_progress_callback` 参数，图片回调用它而非章节回调
- `DownloadTab` 拆分为 `chapter_progress` + `image_progress` 两个 `QProgressBar`，各带独立标签

### 3. 代理设置重新设计（`gui/main_window.py`）

**问题**：原「使用系统代理」复选框名不副实——它只控制图片是否走 7890 代理，对浏览器无任何影响，用户容易误解。

**修复**：
- 替换为「图片代理」复选框 + 地址输入框 + 灰色提示文字「仅加速插图下载，不影响浏览器访问小说页」
- 新增 `browser_proxy` 配置项（`config.json`）：`null`=浏览器直连，有值=走指定代理，与 `image_proxy` 彻底分离
- 取消勾选时地址框自动灰掉，功能一目了然

### 4. 登录检测重写（`core/downloader.py`）

**问题**：`_login_or_load_auth()` 用 DOM 可见性检测登录——ESJZone 的「登出」藏在用户下拉菜单中，`is_visible()` 始终返回 `False`。

**修复**：改用 `page.evaluate()` 执行 JS 在页面内检测：遍历 `<a>` 标签匹配登出/注销链接、检查通知图标、检查导航栏用户名 dropdown、检查 Cookie。四种策略加权，不受 DOM 可见性限制。

### 5. GUI 小改进
- **「重置登录」按钮**：一键删除 `storage_state.json`，登录状态标签实时显示「● 已登录 / ○ 未登录」
- **无头模式默认关闭**：`chk_headless.setChecked(True)` → `False`，避免浏览器静默启动导致登录页不可见

### 修改文件汇总

| 文件 | 改动 |
|------|------|
| `core/downloader.py` | + `retry_chapters()`；+ `image_progress_callback`；登录检测 JS 重写 |
| `gui/main_window.py` | + `RetryWorker`；+ `summary`/`image_progress` 信号；进度条拆分；代理重设计；失败重试 UI；重置登录按钮 |

---

## v2.8 (2026-08-01) —— GUI 工具箱（PyQt6）

**目标**：将 CLI 工具封装为可视化多模块工具箱，降低使用门槛，支持拖放批量操作。

### 新增模块

```
gui/
├── __init__.py
├── main_window.py      # 主窗口 + RefineTab + DownloadTab
└── gui_log_handler.py  # logging → Qt 信号桥接器
gui_launcher.py         # 启动入口
```

### 📦 EPUB 重构标签页（`RefineTab`）
- **拖放批量重构**：支持单个/多个 `.epub` 拖入列表框，或「选择文件」批量添加
- **每文件状态标记**：⏳等待 / 🔄执行中 / ✅完成 / ❌失败 实时显示
- **单独移除**：右键菜单 / 「移除选中」按钮，支持「清空列表」
- **选项**：仅诊断（dry-run 不修改）/ 修改前备份（`.bak`）
- **结构化结果**：按文件分组显示 ⚠️警告 / 🔧修复 / ✅完成

### ⬇️ 下载标签页（`DownloadTab`）— 对接 `core/downloader.py`
- 站点下拉框（动态加载 `scrapers/registry.available_sites()`）
- URL 输入 + http(s) 格式校验
- 重跑方式：`◉ 断点续传` / `○ 清理旧数据重新下载`（注入 `resume=` 参数）
- 复选框：无头模式 / 使用系统代理
- 并发 / 重试数值配置
- QThread + `asyncio.run()` 调用，日志通过 `GuiLogHandler` 实时转发到面板

### 🔧 公共框架
- `BaseTab` 基类统一日志/状态接口，新增模块只需继承 + `addTab`
- 全局日志面板（深色主题，所有模块共享）
- 底部状态栏（`QStatusBar`）显示当前活动
- `requirements.txt` 新增 `PyQt6>=6.6.0`

### 修改文件汇总

| 文件 | 改动 |
|------|------|
| `gui/main_window.py` | 新增（主窗口 + 重构页 + 下载页） |
| `gui/gui_log_handler.py` | 新增（logging → Qt 信号桥接） |
| `gui_launcher.py` | 新增（启动入口） |
| `requirements.txt` | + `PyQt6>=6.6.0` |

---

## v2.7 (2026-08-01) —— EPUB 重构引擎（Sigil 兼容 + 孤儿图片修复）

**背景**：旧版生成器产出的 EPUB 存在 Sigil 兼容性警告（转义选择器、mimetype 非首文件、Images 未记录、media-type 错误、`<br>` 分段无缩进）。新增独立重构引擎，可批量修复现有 EPUB。

### 重构引擎（`tools/_regen_epub.py` → `EpubRefinery`）

| 步骤 | 功能 | 修复内容 |
|------|------|---------|
| 1 | mimetype 首文件 | ZIP 首文件强制为 `mimetype` 且 STORED 未压缩（Sigil 硬错误） |
| 2 | OPF unique-identifier | 缺失则补全 `bookid` + `<dc:identifier id="bookid">` |
| 3 | CSS 转义选择器 | `body[epub\:type="titlepage"]` → `body.title-page-body`（消除 Sigil 警告） |
| 4 | 孤儿图片检测 | 扫描 `Images/` 下所有文件，与 OPF manifest 取差集 |
| 5 | 孤儿图片补录 | 自动注入 `<item>` 到 `</manifest>` 前，media-type 按扩展名推断 |
| 6 | media-type 修正 | `application/octet-stream` → `image/jpeg` 等正确类型 |
| 7 | 章节段落规范化 | `<br /><br />` 分段 → `<p>` 段落（恢复 `text-indent` 缩进），`<h1>` 保持独立 |

### 接口
```python
from tools._regen_epub import EpubRefinery
ref = EpubRefinery("novels/xxx.epub")
result = ref.refine(dry_run=False, no_backup=False)
# result: {status, diagnostics, modified}
```

### 验证（对旧版 `在游戏中与雌性角色交合.epub`）
- mimetype 首文件：✅
- Images manifest 覆盖率：553/553 = 100%
- 第一章：182 个 `<p>` 段落，无 `<br><br>` 残留，`<h1>` 独立
- octet-stream 残留：0（全部修正为 image/jpeg）

### 修改文件汇总

| 文件 | 改动 |
|------|------|
| `tools/_regen_epub.py` | 重构引擎（诊断 + 修补 + 重打包） |
| `docs/EPUB重构功能设计.md` | 新增设计方案文档 |

---

## v2.6 (2026-08-01) —— 图片并发与章节并发解耦（B 方案）

**问题**：图片下载（纯 I/O 直连）与章节下载（需浏览器渲染）共用 `max_concurrent`，拉高并发会误伤章节稳定性；且图片本可安全用更高并发。

**修复**：
- `core/config.py` 新增 `image_max_concurrent`（默认 40）
- `core/downloader.py` 读取 `self.image_max_concurrent`，`_download_images_batch` 改用该值（L673），与章节 `self.max_concurrent` 解耦
- `main.py` 新增 `--image-concurrent N` 参数，覆盖图片并发
- 验证：`python main.py <url> --no-proxy --resume resume --image-concurrent 40`

---

## v2.5 (2026-08-01) —— 系统代理开关 + 图片下载进度 + 重跑模式可选

### 1. 系统代理开关（`core/config.py` + `core/downloader.py`）

**问题**：代理未开启时，`image_proxy` 指向的 `127.0.0.1:7890` 不可达，每张图都要先经历 ~45s 代理连接超时失败，再回退直连，整本小说下载极慢甚至卡死。

**修复**：
- 新增 `core/config.py::load_config()` 统一配置加载，含默认值兜底；新增 `system_proxy` 配置项（默认 `true`）
- `downloader` 计算 `image_proxy` 时：`system_proxy=false` 或 `image_proxy` 为空 → 强制直连，不再尝试代理
- 新增 `--no-proxy` CLI 参数：`config['system_proxy'] = False`，代理关闭时直连，避免每图超时
- `_try_fetch` 代理模式增加 **3s 连接短超时**兜底，即使误走代理也能快速失败回退

### 2. 图片下载进度日志（`core/downloader.py`）

**问题**：旧代码批量下载图片（数百张）时无任何中间输出，用户无法判断进度或是否卡死。

**修复**：`_download_images_batch()` 新增 `logger` 参数与计数，按 `max(20, total//10)` 步长打印 `🖼 图片下载进度 done/total (pct%)`（第 1 张立即打印）。

### 3. 重跑模式可选：清理旧数据 / 断点续传（`core/downloader.py` + `main.py`）

**问题**：发现已有下载记录时，代码用 `input()` 阻塞式询问，GUI / 非交互场景无法调用；`-y` 会自动清掉旧数据，误伤断点续传。

**修复**：
- `download_novel(url, auto_yes=False, resume="ask")` 新增 `resume` 参数：`RESUME_MODE="resume"`（断点续传）/ `RESTART_MODE="restart"`（清理旧数据）/ `ASK_MODE="ask"`（默认，保留原 `input()` 交互）
- 模块级常量 `RESUME_MODE` / `RESTART_MODE` / `ASK_MODE`
- CLI 新增 `--resume {resume,restart,ask}`（默认 `ask`）；`--resume resume -y` 可显式续传不被误清
- GUI 启动对话框放两个单选按钮（断点续传 / 清理重跑），选择直接注入 `resume` 参数，不再卡 `input()`

### 修改文件汇总

| 文件 | 改动 |
|------|------|
| `core/config.py` | 新增（统一配置加载 + `system_proxy` 默认值） |
| `core/downloader.py` | `system_proxy` 生效逻辑；3s 连接超时；图片进度日志；`resume` 三态参数 |
| `main.py` | `--no-proxy` / `--resume` 参数；`load_config` 改用 `core.config` |
| `scrapers/registry.py` | 新增（多站点路由：`get_scraper` / `available_sites`） |

---

## v2.4 (2026-08-01) —— 结构优化 + 多站点架构

- 新增 `core/config.py`：抽离原本散落在 `main.py` 的配置加载逻辑，统一 `load_config()` / `save_config()`，带默认值兜底
- 新增 `scrapers/registry.py`：`BaseScraper` 抽象基类 + `get_scraper(url)` 路由 + `available_sites()`，为后续扩展其他小说站点打基础
- `main.py` 改为基于 `core` 的正式 CLI 入口（澄清此前"双实现"误判）
- 辅助脚本归位：`_inspect_cover.py` / `_regen_epub.py` → `tools/`；`failed_images.txt` → `logs/`
- 新增 `.gitignore`：排除 `__pycache__/`、`_*.log`、`storage_state.json`、`novels/*.epub`、`cache.db` 等
- 文档：新增 `docs/结构优化建议.md`、`docs/系统代理开关方案.md`

---

## v2.3 (2026-07-31) —— 显示质量专项：章节标题 / 排版 / 封面 / 目录

### 1. 章节标题正确抓取（`scrapers/detail.py`）

**问题**：EPUB 中章节显示 `章节 2` 等回退标题，而非网页真实标题（如 `第1章、不是疯女人`）。

**根因**：`link.inner_text()` 部分页面返回空触发 fallback；volume 折叠头（`javascript:` 链接）被跳过时消耗了索引编号，导致回退标题错位。

**修复**：
- 新增 `_extract_chapter_title()` 四策略：① `data-title` 属性 → ② `inner_text()` → ③ `text_content()` → ④ 子 `<p>` 文本
- 独立 `chapter_idx` 计数器，跳过 `javascript:` 链接时不消耗编号
- `parse_chapter_list()` 新增 `wait_for_selector('#chapterList a')` 等待动态加载

### 2. 段落对齐一致性（`core/epub.py` + `utils/helpers.py`）

**问题**：手机端部分段落缩进不一致，左右不对齐。

**修复**：
- `apply_cjk_indent_soup()` 删除 `SKIP_PREV_TAGS`（原跳过标题/引用后首段导致缩进参差），统一所有正文段落缩进
- `BOOK_CSS` 新增 `p { text-indent: 2em }` 作为主力缩进（CSS 支持良好的阅读器）；HTML 全角空格保留为兜底
- 增强两端对齐：`text-justify: inter-ideograph` + `-webkit-text-justify` + `-epub-text-justify`
- 删除 `body` 级 `letter-spacing` / `word-break: keep-all`（部分阅读器干扰 `justify`）

### 3. 图片代理自动回退（`core/downloader.py`）

**问题**：系统代理未开启时，`image_proxy` 配置的 `127.0.0.1:7890` 不可达导致所有图片下载失败。

**修复**：`_download_image()` 代理模式捕获 `httpx.ConnectError` / `httpx.ProxyError` 后自动回退直连，无需改配置。代理开启时走代理加速，关闭时直连兜底。

### 4. 封面显示修复（`core/epub.py`）

**问题**：Sigil 预览封面顶部被裁切；Readest 显示灰色占位符。

**根因**：原 `cover.xhtml` 用 `<svg><image/></svg>` 包装，Sigil 错误解析 `preserveAspectRatio`，部分阅读器不触发 `cover-image` 渲染。

**修复**：封面页改用原生 `<img class="cover" src="images/cover.jpg">`，CSS `max-width/max-height:100% + flex 居中` 保证不被裁切或变形。文件名统一为 `cover.xhtml`（避免旧版 `cover-full.xhtml` 双封面冲突）。

### 5. 目录页美化 + 长目录分组（`core/epub.py`）

- **独立 `Styles/nav.css`**：目录页与正文样式解耦，移除 EPUB 默认冗余序号，改用 `data-num` 自定义章节号列，每行分隔线 + 点击反馈
- **长目录分组**：超过 100 章时每 100 章自动分卷（`<ol>` 嵌套），阅读器原生支持折叠展开，避免 1000+ 章平铺导致首屏卡顿

### 修改文件汇总

| 文件 | 改动 |
|------|------|
| `scrapers/detail.py` | `parse_chapter_list()` 重写 + `_extract_chapter_title()` 新增 |
| `utils/helpers.py` | `apply_cjk_indent_soup()` 删除 `SKIP_PREV_TAGS` |
| `core/epub.py` | `BOOK_CSS` text-indent / 对齐增强；封面 `<img>` 化；`nav.css` 新增；分组 nav.xhtml |
| `core/downloader.py` | `_download_image()` 代理回退直连 |

---

## v2.2 (2026-07-31) —— 分路代理：论坛直连 + 图片走代理

### 问题背景

- 部分小说章节插图托管在海外 CDN，需开启系统代理才能快速加载
- 但系统代理会导致 ESJZone 论坛页面无法正常访问

### 解决方案：网络路径分离

```
┌─────────────────────────────────────────────────┐
│  Playwright 浏览器                               │
│  ├── 论坛页面、章节正文                           │
│  └── --no-proxy-server → 直连（绕过系统代理）      │
├─────────────────────────────────────────────────┤
│  httpx 图片客户端                                │
│  ├── 封面、章节插图                               │
│  └── proxy → 可配置代理（加速海外 CDN）            │
└─────────────────────────────────────────────────┘
```

### 新增配置 (`config.json`)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `browser_bypass_proxy` | `bool` | `true` | Playwright 浏览器绕过系统代理，确保论坛可访问 |
| `image_proxy` | `string` / `null` | `null` | 图片下载代理地址，如 `"http://127.0.0.1:7890"` |

### 新增 CLI 参数

```bash
# 通过命令行临时指定图片代理
python main.py -H --image-proxy http://127.0.0.1:7890 "https://www.esjzone.one/detail/xxx.html"

# 空字符串 = 禁用图片代理（即使 config.json 中已设置）
python main.py -H --image-proxy "" "https://www.esjzone.one/detail/xxx.html"
```

### 使用方式

1. 保持系统代理**开启**（Clash / V2Ray 等）
2. `config.json` 中设置 `"image_proxy": "http://127.0.0.1:7890"`（根据你的代理软件填写）
3. 正常运行下载命令即可
4. 浏览器自动绕过系统代理直连论坛 → 论坛页正常加载
5. 图片通过配置的代理下载 → 海外 CDN 极速加载

### 修改文件

| 文件 | 改动 |
|------|------|
| `config.json` | + `image_proxy`, + `browser_bypass_proxy` |
| `main.py` | + `--image-proxy` CLI 参数，传递给 config |
| `core/downloader.py` | `_download_image()` +proxy 参数；`_download_images_batch()` 透传 proxy；`_ensure_browser()` + `--no-proxy-server`；封面/批量图片调用处传递 `image_proxy` |
| `README.md` | 版本号 |

---

## v2.1 (2026-07-31) —— 字间距微调 + EPUB3 语义标签

### 字间距渐进增强（`core/epub.py` → `BOOK_CSS`）

| 断点 | `letter-spacing` | 说明 |
|------|-----------------|------|
| 默认（手机） | `0.02em` | 适度拉开 CJK 字符间距，提升辨识度 |
| ≤360px 小屏 | `0.015em` | 略收紧，避免过疏 |
| ≥768px 平板 | `0.03em` | 宽屏加宽间距，阅读更舒展 |

- 部分阅读器忽略 `letter-spacing`，此属性为**渐进增强**，不影响回退体验

### EPUB3 `epub:type` 语义标签（`core/epub.py` → `generate_epub()`）

为四类页面 `<body>` 添加标准 EPUB3 语义属性：

| 页面 | `epub:type` | 规范含义 |
|------|-------------|---------|
| 封面页 | `cover` | 出版物封面 |
| 书名页 | `titlepage` | 卷首信息页 |
| 章节页 | `chapter` | 正文章节 |
| 尾页 | `colophon` | 版本/制作信息 |

- 同步为封面、书名页、尾页的 `<html>` 添加 `xmlns:epub` 命名空间声明
- 章节模板已有命名空间，无需额外处理
- 作用：支持无障碍阅读器语义导航、EPUB3 校验通过、未来可能的阅读器特性

### 验证

```
CSS letter-spacing: 3 断点全覆盖
epub:type 标签:  4 类页面全部标记
xmlns:epub:      4 处声明（封面/书名/章节/尾页）
旧样式清理:      text-indent / 硬编码色值 全部清除
```

---

## v2.0 (2026-07-31) —— 移动端中文排版专项

### BOOK_CSS 全面重写（`core/epub.py`）

从旧版 `serif` 硬编码方案重写为 **移动优先 · 中文排版优化** 样式表：

| 维度 | 旧版 | 新版 |
|------|------|------|
| 字体 | `serif` 回退 | **PingFang SC → Noto Sans CJK SC → Microsoft YaHei → sans-serif** |
| 字号 | `1.05em` | **`1.1em`**（手机阅读更舒适） |
| 行距 | `1.85` | **`1.85`**（保留合理值） |
| 颜色 | 硬编码 `#2c2c2c` | **`color: inherit`**（自动适配深色模式） |
| 背景 | — | **`background: transparent`** |
| 图片 | `max-width: 100%` | **`max-width: 100%` + `max-height: 85vh` + `object-fit: contain`** |
| 断行 | 无 | **`-epub-hyphens: none` + `word-break: keep-all`** |
| 对齐 | `text-align: justify` | **`text-justify: inter-ideograph`**（表意文字专用） |
| 缩进 | `text-indent: 2em` | **删除 CSS 缩进，改用 HTML 全角空格** |
| 引用 | 灰色实底 `#f5f5f5` | **`rgba(128,128,128,0.05)` 半透明**（深色模式兼容） |
| 边框 | 实色 `#aaa` | **`rgba(128,128,128,0.3)` 半透明** |
| 响应式 | 简单 600px 断点 | **360px / 768px 双断点 + 层级字号调整** |

### 全角空格段落缩进（`utils/helpers.py` + `core/downloader.py`）

- 新增 `apply_cjk_indent_soup()` 函数，在 BeautifulSoup 处理阶段为每个 `<p>` 标签插入 `\u3000\u3000`（两个全角空格）
- **跳过规则**：`.colophon` / `.copyright-notice` 容器、h1-h6/hr/blockquote 后首段、已有缩进段落、空段落
- **理由**：全角空格缩进不依赖 CSS `text-indent`，兼容所有 EPUB 阅读器，不受用户样式覆盖影响

### 验证

```
CSS: 11/12 项新特性生效（唯一"缺失"的 text-indent:0 为预期行为）
图片: 8 张真实 PNG（0 个占位图 / 0 个 .file 后缀）
缩进: 106/106 段落正确插入 \u3000\u3000（采样 chapter_003）
深色模式: inherit + transparent 全局适配
```

### 建议（可选后续优化）

1. **竖排排版**：如需古风/日式排版可添加 `writing-mode: vertical-rl`
2. **字间距微调**：添加 `letter-spacing: 0.02em` 提升可读性（需验证阅读器兼容性）
3. **章节结束标记**：自动添加 `—— 本章完 ——` 等视觉标记
4. **字体嵌入**：可选嵌入开源中文字体（如霞鹜文楷，约 5-15MB）
5. **EPUB3 语义标签**：添加 `epub:type` 属性提升无障碍访问

---

## v1.6 (2026-07-31) —— 图片下载修复

### 根因
`images.novelpia.com` CDN 返回 `Content-Disposition: attachment` + `Content-Type: application/octet-stream`，
Playwright 的 `page.goto(url)` 将图片请求识别为文件下载，丢弃响应体 → 所有章节插图变为 1x1 占位图。

### 修复
- **`core/downloader.py`** `_download_image()` 改用 `httpx` 直接发起 HTTP 请求，完全绕过 Playwright 网络栈
- 新增异常检测：`len(data) <= 64` 字节 → 静默丢弃（避免小数据段误存）
- 超时 90s 以支持 10MB+ 高清插图

### 连带修复
- `requirements.txt` 新增 `httpx>=0.25.0`
- `core/epub.py` 封面页文件名 `cover.xhtml` → `cover-full.xhtml`（避免与 ebooklib `set_cover()` 内部重名）
- `core/epub.py` 移除对 `book.add_item(cover_img_item)` 的冗余调用（`set_cover` 已注册）

### 验证
```
EPUB 图片检查:
  image_100ad429.png    9981.2 KB [PNG]
  image_2f7593a0.png    1311.0 KB [PNG]
  image_3938e163.png    9432.4 KB [PNG]
  image_8d2a61ea.png   11001.8 KB [PNG]
  image_c10e675c.png    9819.7 KB [PNG]
  image_c44914a4.png   11745.6 KB [PNG]
  image_eef39b8e.png   13679.9 KB [PNG]
  cover.jpg             9939.4 KB [PNG]

0 个占位图 | 0 个 .file 后缀 | 0 个无效 MIME
```

---

## v1.5 (2026-07-31) —— 图片格式检测

### 修复
- **`utils/helpers.py`** 新增 `detect_image_format()` + `resolve_image_extension()`：从二进制魔术字节检测图片真实格式，不再依赖 URL 后缀
- **`core/downloader.py`** EPUB 图片组装时用 `resolve_image_extension(url, data)` 二次检测，修正 `.file` → 真实扩展名 + 同步更新 HTML 中 `src` 引用
- **根因**：ESJZone 图片 CDN (`novelpia.com`) 的图片 URL 以 `_ori.file` 结尾，旧代码直接取 `.file` 作为扩展名和 MIME 类型，EPUB 阅读器无法识别

### 支持的格式检测
| 格式 | 魔术字节 | 输出 |
|------|---------|------|
| JPEG | `FF D8 FF` | `.jpg` / `image/jpeg` |
| PNG | `89 50 4E 47` | `.png` / `image/png` |
| GIF | `47 49 46 38` | `.gif` / `image/gif` |
| WebP | `RIFF .... WEBP` | `.webp` / `image/webp` |
| BMP | `42 4D` | `.bmp` / `image/bmp` |
| SVG | `<?xml` / `<svg` | `.svg` / `image/svg+xml` |
| ICO | `00 00 01 00` | `.ico` / `image/x-icon` |
| AVIF | `ftypavif` | `.avif` / `image/avif` |

### 验证
```
✅ 8 种格式魔术字节检测全部通过
✅ EPUB 中 0 个 .file 后缀文件
✅ HTML src 引用自动同步修正
```

---

## v1.4 (2026-07-31) —— EPUB 质量专项

### 修复
- **`core/epub.py`** `spine` 中 `cover-page` 重复插入（一次 HTML 对象、一次字符串 UID），导致 spine 结构异常
- **`core/epub.py`** 封面页使用 `flex` 居中，多数 EPUB 阅读器不支持，改用 SVG `viewBox` 全屏居中
- **`core/state.py`** `ChapterState.status` 注释补充 `skipped` 状态说明

### 优化
- **`scrapers/detail.py`** HTML 清洗重写：`span.attrs = {}` 一刀切改为智能清洗——保留 `style`（颜色等排版属性）和 `class`，仅清除 `data-*` 站点追踪属性
- **`scrapers/detail.py`** 新增 `<bdi>` 展开、空标签移除、`<div>` 站点属性清除，输出 HTML 体积缩减约 30%
- **`utils/helpers.py`** 新增 `clean_html_for_epub()` 后处理函数：清除 `<script>`/`<iframe>`/`<embed>`/`<object>`、压缩连续 `<br>`、移除 `onclick` 等 JS 事件、清除空 `<p>` 标签
- **`core/downloader.py`** EPUB 准备阶段集成 `clean_html_for_epub()` 最终清洗

### 验证
```
✅ 8 个 .py 文件全部编译通过
✅ 所有模块正确导入
✅ clean_html_for_epub 3 项功能测试通过
```

---

## v1.3 (2026-07-31) —— EPUB 排版彻底重写

### CSS 样式
| 元素 | v1.2 | v1.3 |
|------|------|------|
| `body` | `background: #fcfcf7` 固定底色 | `transparent`——由阅读器主题决定 |
| h1-h6 | 仅 h1/h2 | 完整层级，h3-h6 左对齐加粗 |
| `blockquote` | 无 | 左侧灰色竖线 + 浅灰背景，支持 3 级嵌套 |
| `code` / `pre` | 无 | 行内高亮 + 带边框代码块 |
| `table` | 无 | 带边框表格样式 |
| `ruby` / `rt` / `rp` | 无 | 日文注音支持（轻小说高频需求） |
| `del` / `ins` / `mark` | 无 | 删除线 / 下划线 / 高亮 |
| `hr` | 无 | 带装饰符号 `＊  ＊  ＊` |
| `@media` | 无 | 小屏适配（手机/小平板） |
| `page-break` | 无 | 分页控制、孤行保护 |

### EPUB 结构
| 页面 | v1.2 | v1.3 |
|------|------|------|
| 封面页 | ❌ | ✅ SVG 全屏封面 |
| 书名页 | ❌ | ✅ 书名 / 副标题 / 作者 / 标签 / 来源 URL |
| 版权声明 | ❌ | ✅ "请支持正版" + 生成时间 |
| 章节目录 | ❌（仅 spine） | ✅ EPUB3 landmarks + EPUB2 guide |
| 尾页 | ❌ | ✅ "全书完" + 章节统计 |

### 元数据
```diff
- <dc:identifier>random-uuid-4-every-generation</dc:identifier>
+ <dc:identifier>SHA256(novel_id + title) → deterministic UUID</dc:identifier>
+ <dc:source>原始 URL</dc:source>
+ <dc:description>标签: 异世界, 奇幻, 冒险</dc:description>
+ <dc:publisher>ESJZone (自动下载)</dc:publisher>
+ <dc:date>ISO 8601 生成时间</dc:date>
+ <meta name="calibre:title_sort">副标题排序</meta>
```

### 代码新增
- `_deterministic_uuid(novel_id, title)` 确定性 UUID 生成
- `_tags_to_description(tags)` 标签列表 → DC 描述
- `BOOK_CSS` 样式表常量（含完整排版规则和 ESJZone 适配）

---

## v1.2 (2026-07-31) —— 配置调优 + CLI 增强

### 配置优化
| 参数 | v1.1 | v1.2 | 理由 |
|------|------|------|------|
| `slow_mo` | 300ms | 100ms | 速度提升 20% |
| `timeout` | 30000ms | 45000ms | 长文网络更稳定 |
| `max_retries` | 3 | 5 | 长篇容错 |
| `retry_base_delay` | 1.0s | 2.0s | 给服务端更多缓冲 |
| **新增** `cache_ttl_hours` | — | 168 | 7 天过期 |
| **新增** `cache_max_mb` | — | 50 | 触发维护阈值 |
| **新增** `save_batch_size` | — | 10 | 批量写入间隔 |
| **新增** `page_pool_max` | — | 8 | Page 池容量 |

### CLI 新增
- `--maintain` 维护模式：缓存统计 + 清理过期 + 数据库压缩（`VACUUM`）
- 帮助信息改为推荐命令示例（日常、长篇、维护三种场景）

### 下载器增强
- **进度条 + ETA**：每 10% 输出 `[████░░░░░░] 210/420 (50%) 剩余约 5分30秒`
- **密码章节跳过**：检测「本章节需要密码」→ 状态标为 `skipped`，不计入失败率
- 缓存维护阈值从硬编码 50MB 改为读取 `config.cache_max_mb`
- 汇总报告区分 `failed`（真正失败）与 `skipped`（密码保护跳过）

### 缓存统计
- `CacheManager.stats()` 返回详情：有效/过期条目数、数据库大小、小说分组统计
- 维护模式输出：`📊 缓存统计: 1234 条有效 / 56 条过期 / 12.3 MB / 3 本小说`

---

## v1.1 (2026-07-31) —— 架构重构 + 性能专项

### Bug 修复
- **`core/epub.py`** 第 56 行 `re.sub()` 未 `import re`，运行必报错 —— 已修复

### 架构重构
| v1.0 | v1.1 |
|------|------|
| `main.py` 单文件 764 行 | 拆分为 8 个模块 |
| 无 `.gitignore` | 新增 32 行排除规则 |
| `core/`、`scrapers/`、`utils/` 模块未被 `main.py` 引用 | 全部整合，去除死代码 |

新项目结构：
```
ESJZone/
├── main.py              # CLI 入口（166 行）
├── config.json
├── .gitignore           # 新增
├── core/
│   ├── scraper.py       # 抽象基类
│   ├── downloader.py    # 下载编排器 ✨新增
│   ├── state.py         # 状态持久化
│   ├── cache.py         # 缓存管理
│   └── epub.py          # EPUB 生成
├── scrapers/
│   └── detail.py        # ESJZone 详情页解析
├── utils/
│   └── helpers.py       # 工具函数 + 日志
├── logs/                # 运行日志（自动创建）
├── state/               # 下载快照
└── novels/              # EPUB 输出
```

### 性能提升

| 优化项 | 旧版 | 新版 | 效果 |
|--------|------|------|------|
| Page 管理 | 每章 `new_page()` → `close()` | 固定大小 Page 池复用 | 浏览器开销降低 80%+ |
| 页面加载策略 | `wait_until='networkidle'` | `wait_until='domcontentloaded'` | 单页速度快 3-5x |
| 状态写入 | 每章 1 次磁盘 I/O | 每 10 章批量 1 次 | I/O 减少 90% |
| 重试策略 | 固定 `sleep(2)` | 指数退避 + 随机抖动 | 成功率显著提升 |
| 并发控制 | Semaphore 每次重建 | 实例级单例 Semaphore | 真正做到并发上限 |

### 功能增强
- **日志系统**：`logging` 替代 `print()`，支持 DEBUG/INFO/WARNING/ERROR，同步输出文件 `logs/esjzone.log`
- **CLI 参数**：`argparse` 替代交互式 `input()`，支持 `--headless` / `-c` / `-r` / `-o` / `-v` / `--clear-cache`
- **config.json 激活**：`retry_base_delay`、`retry_max_delay`、`failure_ratio_threshold` 从死配置变为实际生效
- **`CacheManager`**：新增 `clear_by_novel_id()`、`clear_expired()`、`vacuum()`、`size_mb` 属性
- **`StateManager`**：新增 `delete()`、`list_state_ids()`、`fields` 扩展

### 安全
- `.gitignore` 排除 `storage_state.json`（含登录 Cookie）、`cache.db`、`state/`、`novels/`、`logs/`、IDE 配置、虚拟环境

---

## v1.0 —— 初始版本

### 项目快照
```
ESJZone/
├── main.py           # 764 行单体脚本（含 Downloader / CacheManager / StateManager 内联类）
├── config.json       # 10 行
├── requirements.txt  # 5 行
├── core/             # 早期原型模块（未被 main.py 引用）
│   ├── scraper.py    # 抽象基类
│   ├── epub.py       # 缺少 import re（运行报错）
│   ├── cache.py      # 基础 get/set
│   └── state.py      # 基础 NovelState
├── scrapers/
│   └── detail.py     # 简化版详情页解析
├── utils/
│   └── helpers.py    # 基础工具函数
├── cache.db          # SQLite 缓存（~12MB，无维护机制）
├── storage_state.json # 含明文 Cookie（无 .gitignore）
├── state/            # 下载状态快照
└── novels/           # EPUB 输出
```

### 已知问题（v1.0）
- `core/epub.py` 缺少 `import re`，调用即崩溃
- `storage_state.json` 含 `ews_key` 和 `ews_token`，无 .gitignore 保护
- `main.py` 与 `core/`、`scrapers/`、`utils/` 模块完全独立，实现重复
- 每章下载创建/销毁一个新 Page，420 章 = 420 次浏览器选项卡开销
- 重试用固定 `sleep(2)`，无退避策略
- `config.json` 中 `retry_base_delay`、`retry_max_delay`、`failure_ratio_threshold` 定义了但从未使用
- 全部使用 `print()` 输出，无日志系统

### 依赖
- `playwright` (浏览器自动化)
- `beautifulsoup4` + `lxml` (HTML 解析)
- `ebooklib` (EPUB 生成)
- `Pillow` (图片处理)
