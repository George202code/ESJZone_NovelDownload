# ESJZone 小说下载器

> **当前版本：v2.8** | [更新日志](CHANGELOG.md)

将 [esjzone.one](https://www.esjzone.one) 轻小说批量下载为 EPUB 电子书，支持断点续传、缓存加速、失败重试。同时提供 **GUI 工具箱**，支持拖放批量 EPUB 重构与可视化下载。

---

## 功能特性

| 特性 | 说明 |
|------|------|
| 🔄 断点续传 | 下载中断后自动恢复，不重复下载已完成章节 |
| 📦 智能缓存 | SQLite 缓存章节内容，7 天过期，二次下载秒级完成 |
| 🖼 图片下载 | 章节内插图自动下载嵌入 EPUB，失败图片使用占位图并记录 |
| 🎯 指数退避重试 | `1s → 2s → 4s → 8s(+抖动)` 渐进式重试，避免触发反爬 |
| 📄 专业 EPUB | 完整排版 CSS（18 类选择器）、日文注音支持、书名页/版权页/尾页、确定性 UUID、EPUB2+3 双兼容、目录页美化 + 长目录分组 |
| 🔀 分路代理 | 浏览器直连论坛 + 图片走代理加速海外 CDN，系统代理无需来回开关，**代理未开启时自动回退直连** |
| 🖼 封面优化 | 原生 `<img>` 封面（Sigil / Readest / Calibre 全兼容，不裁切不变形） |
| 📊 日志系统 | 控制台实时进度 + `logs/esjzone.log` 完整调试日志 |
| 🖥 GUI 工具箱 | PyQt6 可视化界面：拖放批量 EPUB 重构、可视化下载（站点选择/断点续传/代理控制） |

---

## 环境要求

- Python **3.10+**
- Playwright 浏览器

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 首次登录

```bash
python main.py "https://www.esjzone.one/detail/xxxxx.html"
```

弹出浏览器窗口 → 手动登录 ESJZone → 回到终端按回车。登录状态自动保存到 `storage_state.json`，后续无需重复登录。

### 3. 日常使用

```bash
# 基本下载
python main.py "https://www.esjzone.one/detail/xxxxx.html"

# 无头模式（不显示浏览器窗口）
python main.py -H "https://www.esjzone.one/detail/xxxxx.html"

# 高并发 + 详细日志
python main.py -c 8 -v "https://www.esjzone.one/detail/xxxxx.html"

# 系统代理未开启时直连（避免每图代理超时回退）
python main.py --no-proxy "https://www.esjzone.one/detail/xxxxx.html"

# 已有记录时选择：断点续传 / 清理旧数据重跑（默认 ask 交互询问）
python main.py --resume resume  "https://www.esjzone.one/detail/xxxxx.html"
python main.py --resume restart "https://www.esjzone.one/detail/xxxxx.html"

# 清理缓存后重新下载
python main.py --clear-cache "https://www.esjzone.one/detail/xxxxx.html"
```

### 4. 查看帮助

```bash
python main.py --help
```

---

## GUI 工具箱（可视化操作）

适合不熟悉命令行的用户，支持拖放批量操作与可视化配置。

### 启动

```bash
pip install PyQt6          # 首次使用需安装 GUI 依赖
python gui_launcher.py
```

### 功能模块

| 标签页 | 功能 |
|--------|------|
| 📦 **EPUB 重构** | 拖放单个/多个 `.epub` → 自动诊断 Sigil 兼容性问题 → 一键修复（孤儿图片补录、media-type 修正、章节段落规范化、mimetype 首文件修复等） |
| ⬇️ **下载** | 可视化下载：站点下拉、URL 输入、断点续传/清理重跑、无头模式/代理开关、并发/重试配置 |

### EPUB 重构页特性
- **拖放批量**：直接将 `.epub` 文件拖入列表框，或点击「选择文件」
- **状态标记**：⏳等待 / 🔄执行中 / ✅完成 / ❌失败 实时显示
- **单独移除**：右键菜单或「移除选中」按钮
- **选项**：仅诊断（dry-run 预览）/ 修改前自动备份（`.bak`）

### 下载页特性
- 站点下拉（自动加载已注册站点）
- URL 输入 + 格式校验
- 重跑方式：断点续传 / 清理旧数据
- 无头模式 / 系统代理开关
- 并发 / 重试数值配置
- 实时日志面板

---

## 命令行参数

```
python main.py [URL] [选项]

位置参数:
  URL                  小说详情页 URL

可选选项:
  -H, --headless       无头模式运行（不显示浏览器窗口）
  -c, --concurrent N   最大并发下载数 (默认: 5)
  -r, --retries N      单章最大重试次数 (默认: 3)
  -o, --output DIR     EPUB 输出目录 (默认: ./novels)
  -v, --verbose        详细日志输出 (DEBUG 级别)
  --image-proxy URL    图片下载代理地址 (如 http://127.0.0.1:7890)
  --no-proxy          关闭系统代理，图片直连（代理未开启时必加，否则每图先等代理超时再回退）
  --resume MODE        已有记录时的行为: resume=断点续传 / restart=清理旧数据重跑 / ask=交互询问(默认)
  --image-concurrent N 图片下载并发数（默认取 config.image_max_concurrent，可高于章节并发）
  --list-sites         列出支持的小说站点后退出
  --clear-cache        清理指定小说的缓存和状态后退出
```

---

## 配置文件

`config.json` 可调整的参数：

```json
{
    "cookie": "",                  // 浏览器 Cookie（暂未使用）
    "output_dir": "./novels",      // EPUB 输出目录
    "max_concurrent": 5,           // 最大并发下载数
    "max_retries": 3,              // 单章失败最大重试次数
    "max_overall_retries": 2,      // 整体重试轮数
    "timeout": 30000,              // 页面加载超时 (ms)
    "headless": false,             // 无头模式
    "slow_mo": 300,                // 操作延迟 (ms)，降低被检测风险
    "retry_base_delay": 1.0,       // 重试基础延迟 (秒)
    "retry_max_delay": 30.0,       // 重试最大延迟 (秒)
    "failure_ratio_threshold": 0.3, // 失败比例阈值（预留）
    "image_proxy": null,            // 图片下载代理，如 "http://127.0.0.1:7890"
    "browser_bypass_proxy": true,   // 浏览器绕过系统代理，确保论坛可访问
    "system_proxy": true            // 系统代理总开关；false 时图片强制直连（--no-proxy 等价设为 false）
}
```

> **分路代理**（v2.2+）：当系统代理会阻断论坛但能加速图片 CDN 时，设置 `browser_bypass_proxy: true` + `image_proxy` 即可，无需手动开关代理。
>
> 也可通过 CLI 临时指定：`--image-proxy http://127.0.0.1:7890`
>
> **代理回退**（v2.3+）：若 `image_proxy` 指向的代理未运行（如 Clash 未开启），图片下载会自动回退为直连，无需修改配置即可正常获取插图。

---

## 项目架构

```
ESJZone/
├── main.py                  # CLI 入口 (argparse + 编排)
├── config.json              # 配置文件
├── requirements.txt         # Python 依赖
├── .gitignore               # 安全排除规则
│
├── core/
│   ├── scraper.py           # 抽象基类 BaseScraper
│   ├── config.py            # 统一配置加载 (load_config / save_config + 默认值兜底)
│   ├── downloader.py        # 下载编排器 (浏览器/Page池/并发/EPUB)
│   ├── state.py             # 状态管理 (NovelState / ChapterState / StateManager)
│   ├── cache.py             # SQLite 缓存管理 (CacheManager)
│   └── epub.py              # EPUB 生成器 + ErrorReporter
│
├── scrapers/
│   ├── detail.py            # ESJZone 详情页抓取器 (DetailScraper)
│   └── registry.py          # 多站点路由 (get_scraper / available_sites)
│
├── utils/
│   └── helpers.py           # 工具函数 + logging 配置 + 占位图
│
├── gui/                     # PyQt6 可视化工具箱
│   ├── __init__.py
│   ├── main_window.py       # 主窗口 + 重构页 + 下载页
│   └── gui_log_handler.py   # logging → Qt 信号桥接
├── gui_launcher.py          # GUI 启动入口
│
├── tools/                   # 辅助脚本
│   └── _regen_epub.py       # EPUB 重构引擎 (EpubRefinery)
│
├── core/  scrapers/  ...     # 见上方模块
├── logs/                    # 运行日志 (自动创建)
├── state/                   # 下载状态快照 (*.json)
└── novels/                  # EPUB 输出目录
```

### 数据流

```
CLI (main.py)
    │
    ▼
Downloader.download_novel(url)
    ├── 1. 提取 novel_id
    ├── 2. 检查/恢复 StateManager
    ├── 3. 首次: BrowserManager → DetailScraper 解析小说信息 + 章节列表
    ├── 4. _download_all()  并发下载章节
    │       ├── Page池 复用浏览器标签页
    │       ├── CacheManager.get() 检查缓存
    │       ├── DetailScraper.parse_chapter_content()
    │       ├── CacheManager.set() 写入缓存
    │       └── StateManager.save() 批量写状态
    ├── 5. 整体重试 (max_overall_retries 轮)
    ├── 6. _prepare_and_generate_epub()
    │       ├── 并发下载所有图片
    │       ├── 组装章节 HTML
    │       └── generate_epub() 输出 .epub
    └── 7. 错误报告 (ErrorReporter)
```

---

## 下载流程

```
┌────────────┐     ┌──────────┐     ┌───────────┐     ┌──────────┐
│  输入 URL   │ ──▶ │ 解析小说  │ ──▶ │ 并发下载   │ ──▶ │ 整体重试  │
└────────────┘     └──────────┘     └───────────┘     └──────────┘
                                          │                  │
                                          ▼                  ▼
                                    ┌──────────┐     ┌──────────┐
                                    │ 图片下载   │ ──▶ │ 生成EPUB │
                                    └──────────┘     └──────────┘
```

每章下载流程：

```
缓存命中? ──Yes──▶ 直接标记完成
    │
   No
    │
从 Page 池获取标签页
    │
page.goto(url, wait_until='domcontentloaded')
    │
wait_for_selector('div.forum-content.mt-3')
    │
解析正文 + HTML 清洗 + 提取图片 URL
    │
写入 SQLite 缓存 (TTL 7 天)
    │
归还 Page 到池中
    │
成功 → 标记 completed / 失败 → 指数退避重试 (最多 max_retries 次)
```

---

## 核心优化

相比原始版本，重构后有以下关键改进：

| 优化项 | 原始版本 | 优化后 |
|--------|---------|--------|
| 浏览器标签页 | 每次新建+销毁 | Page 池固定数量复用 |
| 页面加载策略 | `networkidle` (等待全部网络停止) | `domcontentloaded` (HTML 就绪即可) |
| 状态持久化 | 每章完成都写磁盘 | 每 10 章批量写入 |
| 重试策略 | 固定 `sleep(2)` | 指数退避 + 随机抖动 |
| 日志输出 | `print()` 散乱打印 | `logging` 分级 + 文件持久化 |
| 代码结构 | 单文件 764 行 | 8 模块职责清晰 |
| 运行方式 | 交互式输入 URL | `argparse` CLI 参数 |

---

## 输出文件

| 文件 | 说明 |
|------|------|
| `novels/书名.epub` | 下载完成的电子书 |
| `logs/esjzone.log` | 详细运行日志 |
| `state/{novel_id}.json` | 下载进度快照 |
| `cache.db` | 章节内容缓存 |
| `failed_images.txt` | 下载失败的图片列表 |

---

## 常见问题

### Cookie 过期

删除 `storage_state.json` 重新运行即可重新登录。

### 部分章节超时失败

调整 `config.json` 中的参数：
- 增大 `timeout` (单位 ms)
- 增加 `max_retries`
- 降低 `max_concurrent` 减小并发

### 清理某本小说的数据

```bash
python main.py --clear-cache "https://www.esjzone.one/detail/xxxxx.html"
```

或手动删除 `state/{novel_id}.json` + 数据库条目。

---

## 依赖清单

### CLI 下载器
```
playwright>=1.40.0      # 浏览器自动化
beautifulsoup4>=4.12.0   # HTML 解析
lxml>=4.9.0              # XML/HTML 解析引擎
ebooklib>=0.18           # EPUB 生成
```

### GUI 工具箱（可选）
```
PyQt6>=6.6.0            # 可视化界面框架
```

> 安装全部依赖：`pip install -r requirements.txt`（已含 PyQt6）

---

## License

MIT
