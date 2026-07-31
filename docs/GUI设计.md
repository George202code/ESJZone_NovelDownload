# ESJZone 工具箱 GUI 设计方案（PyQt6）

> 状态：**已实现核心功能**（EPUB 重构 + 下载模块均已上线）
> 目标：多模块工具箱（下载 / 重构等），可扩展架构
>
> ## 已实现模块
> ### 📦 EPUB 重构标签页（`RefineTab`）
> - 拖放批量 .epub（单/多个文件）
> - 每文件状态标记（⏳等待 / 🔄执行中 / ✅完成 / ❌失败）
> - 单独移除（右键菜单 / 「移除选中」按钮）/ 清空列表
> - 选项：仅诊断（dry-run）/ 修改前备份（.bak）
> - 进度条 + 结构化结果（按文件分组，区分 ⚠️警告 🔧修复 ✅完成）
>
> ### ⬇️ 下载标签页（`DownloadTab`）— 对接 `core/downloader.py`
> - 站点下拉框（动态加载 `scrapers/registry.available_sites()`）
> - URL 输入 + 校验
> - 重跑方式：`◉ 断点续传` / `○ 清理旧数据重新下载`（注入 `resume=` 参数）
> - 复选框：无头模式 / 使用系统代理（关闭→直连，避免代理超时）
> - 并发 / 重试 数值配置
> - QThread + asyncio.run 调用，日志实时转发到面板
>
> ### 🔧 公共框架
> - 全局日志面板（所有模块共享，深色主题）
> - 底部状态栏（QStatusBar 显示当前活动）
> - `BaseTab` 基类统一日志/状态接口
> - `GuiLogHandler` 桥接 Python logging → Qt 信号
>
> ## 启动方式
> ```bash
> python gui_launcher.py
> ```
>
> ## 文件结构
> ```
> gui/
> ├── __init__.py
> ├── main_window.py      # 主窗口 + RefineTab + DownloadTab
> └── gui_log_handler.py  # logging → Qt 信号桥接器
> gui_launcher.py         # 启动入口
> ```

## 1. 技术栈

- **PyQt6**：主窗口 + 队列列表 + 日志面板
- **QThread 桥接 asyncio**：`core/downloader.py` 的 `download_novel` 是 `async` 函数，GUI 在 `QThread` 内 `asyncio.run()` 调用，避免阻塞 UI
- **信号（Signal）**：`progress(int, int, str)` / `log_line(str)` / `finished(str)`
- **多站点**：`scrapers/registry.py` 的 `available_sites()` 返回站点列表，下拉框动态填充

## 2. 界面布局

```
┌───────────────────────────────────────────────────────────┐
│  ESJZone 下载器                      站点:[Detail ▼]  [+添加] │
├───────────────────────────────────────────────────────────┤
│  队列                                                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ☑ 直到我的人生走向破灭   1781790150  进度 305/306  ⏸   │   │
│  │ ☐ 另一本小说            1234567890  等待中          ⏸   │   │
│  └─────────────────────────────────────────────────────┘   │
│  [开始队列] [暂停] [移除]                                     │
├───────────────────────────────────────────────────────────┤
│  当前任务                                                     │
│  URL: [https://www.esjzone.one/detail/1781790150.html   ]   │
│  ☑ 无头模式   ☑ 系统代理(关→直连)                            │
│  重跑方式:  (•) 断点续传   ( ) 清理旧数据重新下载             │
│  并发:[5]  重试:[3]                                          │
│  [开始下载]                                                   │
├───────────────────────────────────────────────────────────┤
│  日志                                                         │
│  [01:28:58] 🖼 图片下载进度 41/794 (5%)                       │
│  [01:29:10] 🖼 图片下载进度 61/794 (8%)                       │
└───────────────────────────────────────────────────────────┘
```

## 3. 重跑方式选择（断点续传 / 清理旧数据）

对应 `core/downloader.py` 的 `download_novel(url, resume=...)` 三态参数：

| GUI 选项 | 注入参数 | 行为 |
|----------|----------|------|
| ◉ 断点续传（默认） | `resume=RESUME_MODE` | 保留 `state/{id}.json` + `cache.db`，继续未完成的章节/图片 |
| ○ 清理旧数据重新下载 | `resume=RESTART_MODE` | 删除状态 + 缓存后全量重抓 |

- GUI **不使用** `input()`，选择经 `resume` 参数注入，QThread 内调用 `download_novel(url, resume=mode)`
- 等价于 CLI：`--resume resume` / `--resume restart`；`-y` 在 `ask` 模式下会误清数据，GUI 已显式传值避免此坑
- 队列中每本小说可单独记住各自的上次选择

## 4. 代理控制

- 复选框「系统代理」绑定 `config['system_proxy']`
- 取消勾选 → 等价于 `--no-proxy`：`image_proxy` 强制为 `None`，图片直连，避免代理关闭时每图 ~45s 超时
- 底层 `_try_fetch` 仍有 3s 连接超时兜底，即使误走代理也能快速回退

## 5. 进度与日志

- `download_novel` 内图片批量下载按 `max(20, total//10)` 步长 emit `progress`
- `log_line` 信号转发 `logging` 输出到日志面板
- 单本完成后 emit `finished(epub_path)`，列表项标记 ✅ 并自动开始队列下一本

## 6. 与 CLI 的映射

| GUI 操作 | CLI 等价 |
|----------|----------|
| URL + 开始下载 | `python main.py <url>` |
| 无头模式 | `-H` |
| 系统代理关闭 | `--no-proxy` |
| 断点续传 | `--resume resume` |
| 清理重跑 | `--resume restart` |
| 并发 / 重试 | `-c N` / `-r N` |
| 站点下拉 | `available_sites()` |
