# main.py 模块化与性能评估

> 评估时间：2026-08-01
> 结论：**保留 main.py，无需删除或重构**（它是健康的薄入口层，零性能负担）

## 1. 评估维度

### 1.1 模块化影响

`main.py` 角色分析（195 行）：
- 仅 `argparse` 参数定义 + 参数→config 映射 + `asyncio.run(main_async)` 调用
- 无业务逻辑：所有核心逻辑（下载编排/缓存/状态/EPUB）都在 `core/`
- `cmd_maintain` 只是 CLI 子命令，实际操作委托给 `CacheManager` / `StateManager`

**证据：main.py 是标准"薄入口层"（thin CLI entrypoint），符合分层架构。删除它会破坏入口范式，反而降低可维护性。**

### 1.2 性能影响

`main.py` 执行路径（启动期一次性）：
1. `argparse.parse_args()` —— O(1)
2. `load_config()` —— 读一次 config.json
3. 构建 `Downloader(config, log)` —— 一次对象初始化
4. `asyncio.run(main_async)` —— 单次事件循环启动

**无循环、无阻塞、无重复计算、无每次请求重建对象。** main.py 对运行时性能影响为 **0**。

真正性能瓶颈在 `core/downloader.py` 的图片下载并发与 CDN 延迟（已用 B 方案解耦 `image_max_concurrent`），与 main.py 无关。

## 2. 项目真实结构（行数）

| 文件 | 行数 | 角色 | 模块化评价 |
|------|------|------|-----------|
| `main.py` | 195 | CLI 薄入口 | ✅ 健康，无需改 |
| `core/downloader.py` | 747 | 下载编排器 | ⚠️ 偏大但职能单一 |
| `core/epub.py` | 831 | EPUB 生成 | ⚠️ 偏大但职能单一 |
| `core/cache.py` | 140 | SQLite 缓存 | ✅ |
| `core/state.py` | 116 | 状态持久化 | ✅ |
| `core/config.py` | 60 | 配置加载 | ✅ |
| `core/scraper.py` | 28 | 抽象基类 | ✅ |
| `scrapers/detail.py` | 189 | ESJZone 抓取器 | ✅ |
| `scrapers/registry.py` | 41 | 多站点路由 | ✅ |
| `utils/helpers.py` | 357 | 工具函数 | ✅ |
| `tools/_*.py` | 34/40 | 辅助脚本 | ✅ |
| `_test_run.py` | 44 | 测试入口 | ✅ |

## 3. 真实可优化点（非 main.py 问题，节后可选）

若想进一步模块化（**非必须**，当前功能正常）：

1. **`core/downloader.py` (747行) 拆分**
   - `_download_image` / `_download_images_batch` 已抽成模块级函数 → 可移入 `core/image_dl.py`
   - `_ensure_browser` / `_new_page` 浏览器管理 → 可移入 `core/browser.py`

2. **`core/epub.py` (831行) 拆分**
   - 封面下载 / 目录生成 / 章节写入 可拆为子模块
   - 但 `epub.py` 已用 `generate_epub` + `ErrorReporter` 两个公开接口，外部分层清晰

3. **重复逻辑核查**
   - `main.py::cmd_maintain` 与 `Downloader` 都操作 `CacheManager` → 无重复，前者是 CLI 包装
   - 无发现循环 import / 全局可变状态滥用

## 4. 结论

- ✅ **main.py 保留**：它是标准薄入口，不影响模块化、零性能负担
- ✅ **不重构项目**：当前分层合理（core / scrapers / utils / tools 职责清晰）
- ⚠️ **可选优化**：仅当 `downloader.py`/`epub.py` 单文件过大影响阅读时，才考虑拆子模块（非性能需求）
- 🔧 **B 方案已落地**（图片并发解耦），是本次真正有价值的性能改进

## 5. 待办（节后）

- 用户将提供新 URL，用 main.py + GUI 设计验证多站点/批量下载
- 可选：`downloader.py` / `epub.py` 子模块拆分（仅当维护困难时）
