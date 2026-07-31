"""
ESJZone 工具箱 - 主窗口 (PyQt6)

基于项目 GUI 设计方案（docs/GUI设计.md），使用 PyQt6 实现。
采用标签页（QTabWidget）架构，便于后续扩展新功能模块：

  - RefineTab    : EPUB 重构（CSS/Sigil兼容/孤儿图片/媒体类型/章节段落）
  - DownloadTab  : 小说下载（对接 core/downloader.py，支持断点续传/清理重跑/代理）

所有模块共享受统一的日志面板与主窗口框架。
"""

import sys
import os
import json
import asyncio
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QTextEdit, QProgressBar,
    QFileDialog, QCheckBox, QRadioButton, QComboBox, QLineEdit, QSpinBox,
    QMessageBox, QFrame, QSizePolicy, QButtonGroup, QMenu, QStatusBar,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont, QColor

# 将项目根目录加入 sys.path，确保可 import tools / core
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CONFIG_PATH = PROJECT_ROOT / "config.json"

from gui.gui_log_handler import GuiLogHandler  # 避免在 worker 内延迟导入


# ──────────────────────────── 基类 ────────────────────────────
class BaseTab(QWidget):
    """功能标签页基类，统一提供日志接口与进度回调"""

    def __init__(self, log_signal: pyqtSignal, status_signal: pyqtSignal, parent=None):
        super().__init__(parent)
        self.log_signal = log_signal
        self.status_signal = status_signal

    def log(self, msg: str):
        self.log_signal.emit(msg)

    def set_status(self, msg: str):
        self.status_signal.emit(msg)

    def setup_ui(self):
        raise NotImplementedError


# ──────────────────────────── 重构 Worker ────────────────────────────
class RefineWorker(QThread):
    """后台执行 EPUB 重构的 Worker 线程"""
    progress = pyqtSignal(int, int, str)
    log_line = pyqtSignal(str)
    finished_one = pyqtSignal(int, str, dict)  # index, filepath, result
    all_done = pyqtSignal()

    def __init__(self, items: list, dry_run: bool, no_backup: bool):
        super().__init__()
        # items: [(index, filepath), ...]
        self.items = items
        self.dry_run = dry_run
        self.no_backup = no_backup

    def run(self):
        total = len(self.items)
        from tools._regen_epub import EpubRefinery
        for i, (idx, fp) in enumerate(self.items, 1):
            self.progress.emit(i, total, os.path.basename(fp))
            try:
                ref = EpubRefinery(fp)
                result = ref.refine(dry_run=self.dry_run, no_backup=self.no_backup)
                self.finished_one.emit(idx, fp, result)
            except Exception as e:
                self.log_line.emit(f"[ERROR] {fp}: {e}")
                self.finished_one.emit(idx, fp, {"status": "error", "error": str(e)})
        self.all_done.emit()


# ──────────────────────────── 下载 Worker ────────────────────────────
class DownloadWorker(QThread):
    """后台执行小说下载的 Worker 线程"""
    log_line = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)  # current_ch, total_ch, msg
    finished = pyqtSignal(bool, str)       # success, epub_path

    def __init__(self, url: str, resume_mode: str, headless: bool,
                 use_proxy: bool, concurrent: int, retry: int):
        super().__init__()
        self.url = url
        self.resume_mode = resume_mode
        self.headless = headless
        self.use_proxy = use_proxy
        self.concurrent = concurrent
        self.retry = retry

    def run(self):
        try:
            from core.downloader import Downloader, RESUME_MODE, RESTART_MODE
            import logging
            # 配置 logging 转发到 GUI
            logger = logging.getLogger()
            # 创建转发 Handler
            from gui.gui_log_handler import GuiLogHandler
            handler = GuiLogHandler(self.log_line)
            handler.setLevel(logging.INFO)
            logger.addHandler(handler)

            # 加载 config
            config = {}
            if CONFIG_PATH.exists():
                try:
                    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                except Exception:
                    config = {}

            # 注入 GUI 选项
            config['headless'] = self.headless
            if not self.use_proxy:
                config['system_proxy'] = False
                config['image_proxy'] = None
            if self.concurrent:
                config['max_concurrent'] = self.concurrent
            if self.retry:
                config['max_retries'] = self.retry

            downloader = Downloader(config=config, log=logging.getLogger("esjzone.gui"))

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            epub_path = loop.run_until_complete(
                downloader.download_novel(self.url, auto_yes=True, resume=self.resume_mode)
            )
            loop.run_until_complete(downloader.close())
            loop.close()

            if epub_path:
                self.finished.emit(True, epub_path)
            else:
                self.finished.emit(False, "")
        except Exception as e:
            self.log_line.emit(f"[ERROR] 下载异常: {e}")
            self.finished.emit(False, str(e))
        finally:
            try:
                logging.getLogger().removeHandler(handler)
            except Exception:
                pass


# ──────────────────────────── EPUB 重构标签页 ────────────────────────────
class RefineTab(BaseTab):
    STATUS_WAIT = "⏳"
    STATUS_DONE = "✅"
    STATUS_ERR = "❌"
    STATUS_RUN = "🔄"

    def __init__(self, log_signal, status_signal, parent=None):
        super().__init__(log_signal, status_signal, parent)
        self.files = {}      # index -> filepath
        self.next_index = 0
        self.file_status = {}  # index -> status char
        self.worker = None
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── 标题 + 操作 ──
        header = QHBoxLayout()
        title = QLabel("EPUB 重构工具")
        title.setFont(QFont("", 14, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch(1)
        self.btn_add = QPushButton("选择文件")
        self.btn_add.clicked.connect(self.pick_files)
        self.btn_remove = QPushButton("移除选中")
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_clear = QPushButton("清空列表")
        self.btn_clear.clicked.connect(self.clear_list)
        header.addWidget(self.btn_add)
        header.addWidget(self.btn_remove)
        header.addWidget(self.btn_clear)
        layout.addLayout(header)

        hint = QLabel("将 .epub 文件拖拽到下方列表框，或点击「选择文件」批量添加。")
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        # ── 拖放列表框（带状态列）──
        self.drop_list = QListWidget()
        self.drop_list.setAcceptDrops(True)
        self.drop_list.setDragEnabled(False)
        self.drop_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.drop_list.customContextMenuRequested.connect(self.show_context_menu)
        self.drop_list.setStyleSheet(
            "QListWidget { border: 2px dashed #aaa; border-radius: 6px; padding: 6px; }"
            "QListWidget::item { padding: 4px; }"
        )
        layout.addWidget(self.drop_list, stretch=3)

        # ── 选项 ──
        opt_row = QHBoxLayout()
        self.chk_dry = QCheckBox("仅诊断（不修改文件）")
        self.chk_backup = QCheckBox("修改前创建备份 (.bak)")
        self.chk_backup.setChecked(True)
        opt_row.addWidget(self.chk_dry)
        opt_row.addWidget(self.chk_backup)
        opt_row.addStretch(1)
        layout.addLayout(opt_row)

        # ── 进度条 ──
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # ── 执行按钮 ──
        run_row = QHBoxLayout()
        run_row.addStretch(1)
        self.btn_run = QPushButton("开始重构")
        self.btn_run.setStyleSheet(
            "QPushButton { background: #2d8cf0; color: white; padding: 8px 20px; border-radius: 4px; font-weight: bold; }"
            "QPushButton:disabled { background: #aaa; }"
        )
        self.btn_run.clicked.connect(self.start_refine)
        run_row.addWidget(self.btn_run)
        layout.addLayout(run_row)

        # ── 结果区 ──
        result_label = QLabel("诊断 / 执行结果")
        result_label.setFont(QFont("", 11, QFont.Weight.Bold))
        layout.addWidget(result_label)
        self.result_view = QTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setStyleSheet("QTextEdit { font-family: Consolas, monospace; font-size: 11px; }")
        layout.addWidget(self.result_view, stretch=4)

    # ── 拖放 ──
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        added = 0
        for url in urls:
            fp = url.toLocalFile()
            if fp.lower().endswith('.epub') and os.path.isfile(fp):
                self.add_file(fp)
                added += 1
        if added:
            self.log(f"通过拖放添加了 {added} 个 EPUB 文件")
        event.acceptProposedAction()

    # ── 文件管理 ──
    def add_file(self, filepath: str):
        if filepath in self.files.values():
            return
        idx = self.next_index
        self.next_index += 1
        self.files[idx] = filepath
        self.file_status[idx] = self.STATUS_WAIT
        item = QListWidgetItem(f"{self.STATUS_WAIT} {os.path.basename(filepath)}")
        item.setToolTip(filepath)
        item.setData(Qt.ItemDataRole.UserRole, idx)
        self.drop_list.addItem(item)

    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择 EPUB 文件", "", "EPUB Files (*.epub)")
        for f in files:
            self.add_file(f)
        if files:
            self.log(f"通过对话框添加了 {len(files)} 个 EPUB 文件")

    def remove_selected(self):
        items = self.drop_list.selectedItems()
        if not items:
            return
        for item in items:
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx in self.files:
                del self.files[idx]
            if idx in self.file_status:
                del self.file_status[idx]
            self.drop_list.takeItem(self.drop_list.row(item))
        self.log(f"已移除 {len(items)} 个文件")

    def clear_list(self):
        self.files.clear()
        self.file_status.clear()
        self.drop_list.clear()

    def show_context_menu(self, pos):
        menu = QMenu(self)
        remove_act = menu.addAction("移除选中")
        remove_act.triggered.connect(self.remove_selected)
        clear_act = menu.addAction("清空列表")
        clear_act.triggered.connect(self.clear_list)
        menu.exec(self.drop_list.mapToGlobal(pos))

    def _update_item_status(self, idx: int, status: str):
        self.file_status[idx] = status
        for i in range(self.drop_list.count()):
            item = self.drop_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == idx:
                fp = self.files.get(idx, "")
                item.setText(f"{status} {os.path.basename(fp)}")
                break

    # ── 执行 ──
    def start_refine(self):
        if not self.files:
            QMessageBox.warning(self, "提示", "请先添加至少一个 EPUB 文件。")
            return
        self.btn_run.setEnabled(False)
        self.result_view.clear()
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.progress.setMaximum(len(self.files))

        items = list(self.files.items())
        for idx, _ in items:
            self._update_item_status(idx, self.STATUS_RUN)

        self.worker = RefineWorker(
            items, dry_run=self.chk_dry.isChecked(),
            no_backup=not self.chk_backup.isChecked())
        self.worker.progress.connect(self.on_progress)
        self.worker.log_line.connect(lambda s: self.log(s))
        self.worker.finished_one.connect(self.on_finished_one)
        self.worker.all_done.connect(self.on_all_done)
        self.worker.start()

    def on_progress(self, cur, total, fname):
        self.progress.setValue(cur)
        self.log(f"[{cur}/{total}] 处理: {fname}")

    def on_finished_one(self, idx, filepath, result):
        status = result.get("status")
        if status == "error":
            self._update_item_status(idx, self.STATUS_ERR)
        else:
            self._update_item_status(idx, self.STATUS_DONE)

        self.result_view.append(f"\n=== {os.path.basename(filepath)} ===")
        diag = result.get("diagnostics", [])
        for level, cat, msg in diag:
            prefix = {"ERROR": "❌", "WARNING": "⚠️", "FIX": "🔧", "DATA": "📊"}.get(level, "•")
            self.result_view.append(f"  {prefix} [{cat}] {msg}")
        if status == "refined":
            self.result_view.append(f"  ✅ 已重构: {', '.join(result.get('modified', []))}")
        elif status == "clean":
            self.result_view.append("  ✅ 无需修改（已符合规范）")
        elif status == "dry-run":
            self.result_view.append(f"  🔍 诊断完成，将修改: {', '.join(result.get('would_modify', []))}")
        elif status == "error":
            self.result_view.append(f"  ❌ 错误: {result.get('error', '未知')}")

    def on_all_done(self):
        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        self.log("全部文件处理完成。")


# ──────────────────────────── 下载标签页 ────────────────────────────
class DownloadTab(BaseTab):
    def __init__(self, log_signal, status_signal, parent=None):
        super().__init__(log_signal, status_signal, parent)
        self.worker = None
        self._load_config()
        self.setup_ui()

    def _load_config(self):
        self.config = {}
        if CONFIG_PATH.exists():
            try:
                self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                self.config = {}

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ── 站点选择 ──
        site_row = QHBoxLayout()
        site_row.addWidget(QLabel("站点:"))
        self.site_combo = QComboBox()
        self.site_combo.addItem("ESJZone (默认)")
        self._load_sites()
        site_row.addWidget(self.site_combo)
        site_row.addStretch(1)
        layout.addLayout(site_row)

        # ── URL 输入 ──
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://www.esjzone.one/detail/xxxxxxxxxx.html")
        url_row.addWidget(self.url_input, stretch=1)
        layout.addLayout(url_row)

        # ── 选项区 ──
        opt_frame = QFrame()
        opt_frame.setFrameStyle(QFrame.Shape.Box)
        opt_frame.setStyleSheet("QFrame { border: 1px solid #ddd; border-radius: 4px; padding: 8px; }")
        opt_layout = QVBoxLayout(opt_frame)

        # 重跑方式
        opt_layout.addWidget(QLabel("重跑方式:"))
        self.resume_group = QButtonGroup(self)
        r1 = QRadioButton("断点续传（保留已有进度）")
        r2 = QRadioButton("清理旧数据重新下载")
        r1.setChecked(True)
        self.resume_group.addButton(r1, 0)
        self.resume_group.addButton(r2, 1)
        opt_layout.addWidget(r1)
        opt_layout.addWidget(r2)

        # 复选框行
        chk_row = QHBoxLayout()
        self.chk_headless = QCheckBox("无头模式")
        self.chk_headless.setChecked(True)
        self.chk_proxy = QCheckBox("使用系统代理")
        self.chk_proxy.setChecked(bool(self.config.get("system_proxy", False)))
        chk_row.addWidget(self.chk_headless)
        chk_row.addWidget(self.chk_proxy)
        chk_row.addStretch(1)
        opt_layout.addLayout(chk_row)

        # 并发/重试
        num_row = QHBoxLayout()
        num_row.addWidget(QLabel("并发:"))
        self.spin_concurrent = QSpinBox()
        self.spin_concurrent.setRange(1, 20)
        self.spin_concurrent.setValue(int(self.config.get("max_concurrent", 5)))
        num_row.addWidget(self.spin_concurrent)
        num_row.addWidget(QLabel("重试:"))
        self.spin_retry = QSpinBox()
        self.spin_retry.setRange(1, 10)
        self.spin_retry.setValue(int(self.config.get("max_retries", 3)))
        num_row.addWidget(self.spin_retry)
        num_row.addStretch(1)
        opt_layout.addLayout(num_row)

        layout.addWidget(opt_frame)

        # ── 进度 ──
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # ── 开始按钮 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_download = QPushButton("开始下载")
        self.btn_download.setStyleSheet(
            "QPushButton { background: #19be6b; color: white; padding: 8px 24px; border-radius: 4px; font-weight: bold; }"
            "QPushButton:disabled { background: #aaa; }"
        )
        self.btn_download.clicked.connect(self.start_download)
        btn_row.addWidget(self.btn_download)
        layout.addLayout(btn_row)

        # ── 结果 ──
        result_label = QLabel("下载日志")
        result_label.setFont(QFont("", 11, QFont.Weight.Bold))
        layout.addWidget(result_label)
        self.result_view = QTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setStyleSheet("QTextEdit { font-family: Consolas, monospace; font-size: 11px; }")
        layout.addWidget(self.result_view, stretch=1)

    def _load_sites(self):
        try:
            from scrapers.registry import available_sites
            sites = available_sites()
            for s in sites:
                if s != "ESJZone (默认)":
                    self.site_combo.addItem(s)
        except Exception:
            pass

    def start_download(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入小说详情页 URL。")
            return
        if not url.startswith("http"):
            QMessageBox.warning(self, "提示", "URL 格式无效，请以 http(s):// 开头。")
            return

        resume_mode = "resume" if self.resume_group.checkedId() == 0 else "restart"
        self.btn_download.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # 不确定进度
        self.result_view.clear()
        self.log(f"开始下载: {url}")

        self.worker = DownloadWorker(
            url=url,
            resume_mode=resume_mode,
            headless=self.chk_headless.isChecked(),
            use_proxy=self.chk_proxy.isChecked(),
            concurrent=self.spin_concurrent.value(),
            retry=self.spin_retry.value(),
        )
        self.worker.log_line.connect(lambda s: self._append_log(s))
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def _append_log(self, msg: str):
        self.result_view.append(msg)
        self.log(msg)

    def on_finished(self, success: bool, path: str):
        self.progress.setVisible(False)
        self.btn_download.setEnabled(True)
        if success:
            self.result_view.append(f"\n✅ 下载完成: {path}")
            self.log(f"下载完成: {path}")
            self.set_status(f"下载完成: {os.path.basename(path)}")
        else:
            self.result_view.append(f"\n❌ 下载失败")
            self.log("下载失败")


# ──────────────────────────── 主窗口 ────────────────────────────
class MainWindow(QMainWindow):
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESJZone 工具箱")
        self.setMinimumSize(860, 680)
        self.setup_ui()

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # ── 标签页 ──
        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)

        self.refine_tab = RefineTab(self.log_signal, self.status_signal)
        self.download_tab = DownloadTab(self.log_signal, self.status_signal)

        self.tabs.addTab(self.refine_tab, "📦 EPUB 重构")
        self.tabs.addTab(self.download_tab, "⬇️ 下载")

        main_layout.addWidget(self.tabs, stretch=4)

        # ── 全局日志 ──
        log_label = QLabel("全局日志")
        log_label.setFont(QFont("", 11, QFont.Weight.Bold))
        log_label.setContentsMargins(12, 6, 0, 2)
        main_layout.addWidget(log_label)

        self.log_panel = QTextEdit()
        self.log_panel.setReadOnly(True)
        self.log_panel.setMaximumHeight(130)
        self.log_panel.setStyleSheet(
            "QTextEdit { font-family: Consolas, monospace; font-size: 10px; background: #1e1e1e; color: #dcdcdc; }")
        main_layout.addWidget(self.log_panel, stretch=1)

        self.log_signal.connect(self.append_log)

        # ── 状态栏 ──
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪")
        self.status_signal.connect(self.status_bar.showMessage)

    def append_log(self, msg: str):
        self.log_panel.append(msg)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
