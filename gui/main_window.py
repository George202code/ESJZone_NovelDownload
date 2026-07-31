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
STORAGE_PATH = PROJECT_ROOT / "storage_state.json"

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
    progress = pyqtSignal(int, int, str)       # 章节: current_ch, total_ch, msg
    image_progress = pyqtSignal(int, int, str)  # 图片: done, total, msg
    summary = pyqtSignal(dict)                  # 下载完成后发送失败章节汇总
    finished = pyqtSignal(bool, str)            # success, epub_path

    def __init__(self, url: str, resume_mode: str, headless: bool,
                 image_proxy_enabled: bool, image_proxy_addr: str,
                 concurrent: int, retry: int):
        super().__init__()
        self.url = url
        self.resume_mode = resume_mode
        self.headless = headless
        self.image_proxy_enabled = image_proxy_enabled
        self.image_proxy_addr = image_proxy_addr
        self.concurrent = concurrent
        self.retry = retry

    def run(self):
        try:
            from core.downloader import Downloader, RESUME_MODE, RESTART_MODE
            import logging
            # 配置 logging 转发到 GUI
            root_logger = logging.getLogger()
            # ★ 必须把 root logger 级别提上来，否则子 logger 的 INFO 会被 root 过滤
            self._old_root_level = root_logger.level
            root_logger.setLevel(logging.INFO)
            # 创建转发 Handler
            from gui.gui_log_handler import GuiLogHandler
            handler = GuiLogHandler(self.log_line)
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
            root_logger.addHandler(handler)

            # 加载 config
            config = {}
            if CONFIG_PATH.exists():
                try:
                    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                except Exception:
                    config = {}

            # 注入 GUI 选项
            config['headless'] = self.headless
            if self.image_proxy_enabled:
                config['system_proxy'] = True
                config['image_proxy'] = self.image_proxy_addr
            else:
                config['system_proxy'] = False
                config['image_proxy'] = None
            config['browser_proxy'] = None   # 浏览器始终直连，不受图片代理影响
            if self.concurrent:
                config['max_concurrent'] = self.concurrent
            if self.retry:
                config['max_retries'] = self.retry

            # 注入 GUI 进度回调（章节 + 图片分离）
            def on_chapter_progress(completed, total, msg):
                self.progress.emit(completed, total, msg)

            def on_image_progress(done, total, msg):
                self.image_progress.emit(done, total, msg)

            downloader = Downloader(config=config, log=logging.getLogger("esjzone.gui"),
                                    progress_callback=on_chapter_progress,
                                    image_progress_callback=on_image_progress)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            epub_path = loop.run_until_complete(
                downloader.download_novel(self.url, auto_yes=True, resume=self.resume_mode)
            )
            loop.run_until_complete(downloader.close())
            loop.close()

            # —— 构建下载汇总（用于失败项重试）——
            try:
                from core.state import StateManager
                from utils.helpers import extract_novel_id
                novel_id = extract_novel_id(self.url)
                if novel_id:
                    state_mgr = StateManager()
                    state = state_mgr.load(novel_id)
                    if state:
                        failed = list(state.chapters.items())
                        failed = [(u, c) for u, c in failed if c.status == 'failed']
                        skipped = sum(1 for c in state.chapters.values() if c.status == 'skipped')
                        summary_data = {
                            'novel_id': novel_id,
                            'url': self.url,
                            'title': state.title,
                            'total': state.total,
                            'completed': state.completed,
                            'failed': len(failed),
                            'skipped': skipped,
                            'epub_path': epub_path or '',
                            'failed_details': [
                                {'title': ch.title, 'url': u, 'error': ch.error}
                                for u, ch in failed
                            ],
                        }
                        self.summary.emit(summary_data)
            except Exception:
                pass

            if epub_path:
                self.finished.emit(True, epub_path)
            else:
                self.finished.emit(False, "")
        except Exception as e:
            self.log_line.emit(f"[ERROR] 下载异常: {e}")
            self.finished.emit(False, str(e))
        finally:
            try:
                root_logger.removeHandler(handler)
                # 恢复原始级别
                root_logger.setLevel(self._old_root_level)
            except Exception:
                pass


class RetryWorker(QThread):
    """后台执行失败章节重试的 Worker 线程"""
    log_line = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)       # 章节重试进度
    image_progress = pyqtSignal(int, int, str)  # 图片进度
    finished = pyqtSignal(bool, str)            # success, epub_path

    def __init__(self, novel_id: str, chapter_urls: list, config: dict,
                 headless: bool, image_proxy_enabled: bool, image_proxy_addr: str,
                 concurrent: int, retry: int):
        super().__init__()
        self._novel_id = novel_id
        self._chapter_urls = chapter_urls
        self._config = dict(config)
        self._headless = headless
        self._image_proxy_enabled = image_proxy_enabled
        self._image_proxy_addr = image_proxy_addr
        self._concurrent = concurrent
        self._retry = retry

    def run(self):
        import logging
        from core.downloader import Downloader

        root_logger = logging.getLogger()
        self._old_root_level = root_logger.level
        root_logger.setLevel(logging.DEBUG)

        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                               "%H:%M:%S"))
        root_logger.addHandler(handler)

        try:
            config = dict(self._config)
            config['headless'] = self._headless
            if self._image_proxy_enabled:
                config['system_proxy'] = True
                config['image_proxy'] = self._image_proxy_addr
            else:
                config['system_proxy'] = False
                config['image_proxy'] = None
            config['browser_proxy'] = None
            if self._concurrent:
                config['max_concurrent'] = self._concurrent
            if self._retry:
                config['max_retries'] = self._retry

            def on_chapter_progress(completed, total, msg):
                self.progress.emit(completed, total, msg)

            def on_image_progress(done, total, msg):
                self.image_progress.emit(done, total, msg)

            downloader = Downloader(config=config,
                                    log=logging.getLogger("esjzone.retry"),
                                    progress_callback=on_chapter_progress,
                                    image_progress_callback=on_image_progress)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            epub_path = loop.run_until_complete(
                downloader.retry_chapters(self._novel_id, self._chapter_urls)
            )
            loop.run_until_complete(downloader.close())
            loop.close()

            if epub_path:
                self.finished.emit(True, epub_path)
            else:
                self.finished.emit(False, "")
        except Exception as e:
            self.log_line.emit(f"[ERROR] 重试异常: {e}")
            self.finished.emit(False, str(e))
        finally:
            try:
                root_logger.removeHandler(handler)
                root_logger.setLevel(self._old_root_level)
            except Exception:
                pass


# ──────────────────────────── 拖放列表框 ────────────────────────────
class DropListWidget(QListWidget):
    """支持拖放 .epub 文件的 QListWidget"""
    files_dropped = pyqtSignal(list)  # list[str]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(False)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        files = []
        for url in urls:
            fp = url.toLocalFile()
            if fp.lower().endswith('.epub') and os.path.isfile(fp):
                files.append(fp)
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()
        else:
            event.ignore()


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
        self.drop_list = DropListWidget()
        self.drop_list.files_dropped.connect(self._on_files_dropped)
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

    # ── 拖放回调 ──
    def _on_files_dropped(self, files: list):
        added = 0
        for fp in files:
            if fp not in self.files.values():
                self.add_file(fp)
                added += 1
        if added:
            self.log(f"通过拖放添加了 {added} 个 EPUB 文件")

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

        # 复选框行（无头模式 + 登录状态）
        chk_row = QHBoxLayout()
        self.chk_headless = QCheckBox("无头模式")
        self.chk_headless.setChecked(False)
        chk_row.addWidget(self.chk_headless)

        self._login_status_label = QLabel(self._login_status_text())
        self._login_status_label.setStyleSheet("color: #666;")
        chk_row.addWidget(self._login_status_label)
        self.btn_reset_auth = QPushButton("重置登录")
        self.btn_reset_auth.setMaximumWidth(80)
        self.btn_reset_auth.setStyleSheet(
            "QPushButton { padding: 2px 8px; border: 1px solid #e09090; border-radius: 3px; color: #c0392b; }"
            "QPushButton:hover { background: #fdf0f0; }"
        )
        self.btn_reset_auth.clicked.connect(self._reset_login_state)
        chk_row.addWidget(self.btn_reset_auth)
        chk_row.addStretch(1)
        opt_layout.addLayout(chk_row)

        # ── 图片代理设置 ──
        proxy_row = QHBoxLayout()
        proxy_row.setSpacing(6)
        self.chk_image_proxy = QCheckBox("图片代理")
        self.chk_image_proxy.setChecked(bool(self.config.get("system_proxy", False)))
        self.chk_image_proxy.setToolTip("启用后，插图下载将通过代理服务器加速")
        proxy_row.addWidget(self.chk_image_proxy)

        proxy_row.addWidget(QLabel("地址:"))
        self.input_image_proxy = QLineEdit()
        self.input_image_proxy.setMaximumWidth(200)
        default_proxy = self.config.get("image_proxy", "http://127.0.0.1:7890")
        self.input_image_proxy.setText(default_proxy if default_proxy else "http://127.0.0.1:7890")
        self.input_image_proxy.setEnabled(self.chk_image_proxy.isChecked())
        self.chk_image_proxy.toggled.connect(lambda checked: self.input_image_proxy.setEnabled(checked))
        proxy_row.addWidget(self.input_image_proxy)

        proxy_hint = QLabel("仅加速插图下载，不影响浏览器访问小说页")
        proxy_hint.setStyleSheet("color: #888; font-size: 11px;")
        proxy_row.addWidget(proxy_hint)
        proxy_row.addStretch(1)
        opt_layout.addLayout(proxy_row)

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

        # ── 章节进度 ──
        self.chapter_progress = QProgressBar()
        self.chapter_progress.setVisible(False)
        self.chapter_progress.setFormat("章节: %v/%m  (%p%)")
        layout.addWidget(self.chapter_progress)

        # ── 图片进度 ──
        self.image_progress = QProgressBar()
        self.image_progress.setVisible(False)
        self.image_progress.setFormat("图片: %v/%m  (%p%)")
        layout.addWidget(self.image_progress)

        # ── 失败项重试区域 ──
        self.retry_frame = QFrame()
        self.retry_frame.setVisible(False)
        retry_layout = QVBoxLayout(self.retry_frame)
        retry_layout.setContentsMargins(0, 2, 0, 2)

        self.retry_summary = QLabel()
        self.retry_summary.setWordWrap(True)
        self.retry_summary.setStyleSheet("color: #c0392b; font-weight: bold;")
        retry_layout.addWidget(self.retry_summary)

        self.retry_detail = QLabel()
        self.retry_detail.setWordWrap(True)
        self.retry_detail.setStyleSheet("color: #666; font-size: 11px;")
        retry_layout.addWidget(self.retry_detail)

        retry_btn_row = QHBoxLayout()
        retry_btn_row.addStretch(1)
        self.btn_retry = QPushButton("选择并重试失败章节")
        self.btn_retry.setStyleSheet(
            "QPushButton { padding: 4px 16px; border: 1px solid #e67e22; border-radius: 3px; color: #e67e22; }"
            "QPushButton:hover { background: #fef5e7; }"
        )
        self.btn_retry.clicked.connect(self._on_retry_clicked)
        retry_btn_row.addWidget(self.btn_retry)
        retry_btn_row.addStretch(1)
        retry_layout.addLayout(retry_btn_row)

        layout.addWidget(self.retry_frame)

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

    def _login_status_text(self) -> str:
        """获取登录状态描述文字"""
        import json
        store = STORAGE_PATH
        if store.exists():
            try:
                data = json.loads(store.read_text(encoding="utf-8"))
                if data.get("cookies"):
                    return "● 已登录"
            except Exception:
                pass
        return "○ 未登录"

    def _reset_login_state(self):
        """重置登录状态：删除 storage_state.json"""
        if not STORAGE_PATH.exists():
            QMessageBox.information(self, "提示", "当前没有登录状态需要重置。")
            return
        reply = QMessageBox.question(
            self,
            "确认重置",
            "将删除已保存的登录信息，下次下载时需要重新登录。\n\n确定重置吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            STORAGE_PATH.unlink()
            self._login_status_label.setText(self._login_status_text())
            QMessageBox.information(self, "完成", "登录状态已重置。\n下次下载时将弹出浏览器窗口供登录。")

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
        self.btn_retry.setEnabled(False)
        self.retry_frame.setVisible(False)      # 新下载时隐藏失败重试区
        self._last_summary = None
        # 章节进度条：初始不确定模式
        self.chapter_progress.setVisible(True)
        self.chapter_progress.setRange(0, 0)
        self.chapter_progress.setFormat("章节: 正在初始化...")
        self._chapter_progress_set = False
        # 图片进度条：初始隐藏，有图片时才显示
        self.image_progress.setVisible(False)
        self._image_progress_set = False
        self.result_view.clear()
        self.log(f"开始下载: {url}")

        self.worker = DownloadWorker(
            url=url,
            resume_mode=resume_mode,
            headless=self.chk_headless.isChecked(),
            image_proxy_enabled=self.chk_image_proxy.isChecked(),
            image_proxy_addr=self.input_image_proxy.text().strip(),
            concurrent=self.spin_concurrent.value(),
            retry=self.spin_retry.value(),
        )
        self.worker.log_line.connect(lambda s: self._append_log(s))
        self.worker.finished.connect(self.on_finished)
        self.worker.progress.connect(self.on_chapter_progress)
        self.worker.image_progress.connect(self.on_image_progress)
        self.worker.summary.connect(self._on_summary)          # 下载完成后接收失败汇总
        self.worker.start()

    def _append_log(self, msg: str):
        self.result_view.append(msg)
        self.log(msg)

    def on_chapter_progress(self, completed: int, total: int, msg: str):
        """接收章节下载进度，更新章节进度条"""
        if not self._chapter_progress_set and total > 0:
            self.chapter_progress.setRange(0, total)
            self._chapter_progress_set = True
        if self._chapter_progress_set:
            self.chapter_progress.setValue(completed)
            self.chapter_progress.setFormat(f"章节: {msg}")
        self.log(msg)

    def on_image_progress(self, done: int, total: int, msg: str):
        """接收图片下载进度，更新图片进度条"""
        # 首次收到图片进度 → 显示进度条
        if not self._image_progress_set and total > 0:
            self.image_progress.setVisible(True)
            self.image_progress.setRange(0, total)
            self._image_progress_set = True
        if self._image_progress_set:
            self.image_progress.setValue(done)
            self.image_progress.setFormat(f"图片: {msg}")
        self.log(msg)

    def on_finished(self, success: bool, path: str):
        self.chapter_progress.setVisible(False)
        self.chapter_progress.setFormat("章节: %v/%m  (%p%)")
        self.image_progress.setVisible(False)
        self.image_progress.setFormat("图片: %v/%m  (%p%)")
        self.btn_download.setEnabled(True)
        if success:
            self.result_view.append(f"\n✅ 下载完成: {path}")
            self.log(f"下载完成: {path}")
            self.set_status(f"下载完成: {os.path.basename(path)}")
        else:
            self.result_view.append(f"\n❌ 下载失败")
            self.log("下载失败")
        # 无失败项时启用按钮
        if not self._last_summary or not self._last_summary.get('failed', 0):
            self.btn_download.setEnabled(True)

    # ── 下载汇总与失败重试 ──
    def _on_summary(self, data: dict):
        """接收 DownloadWorker 的下载汇总"""
        failed = data.get('failed', 0)
        self._last_summary = data
        if failed > 0:
            details = data.get('failed_details', [])
            total = data.get('total', 0)
            completed = data.get('completed', 0)
            skipped = data.get('skipped', 0)

            self.retry_summary.setText(
                f"⚠ 下载完成: {completed}/{total} 章成功, {failed} 章失败"
                + (f", {skipped} 章跳过" if skipped else "")
            )
            detail_lines = []
            for d in details[:8]:  # 最多显示 8 条
                err = d['error'][:60] if d.get('error') else '未知错误'
                detail_lines.append(f"   · {d['title'][:30]} — {err}")
            if len(details) > 8:
                detail_lines.append(f"   ... 还有 {len(details) - 8} 个失败项")
            self.retry_detail.setText('\n'.join(detail_lines))
            self.retry_frame.setVisible(True)
            self.btn_retry.setEnabled(True)
        else:
            self.retry_frame.setVisible(False)

    def _on_retry_clicked(self):
        """打开章节选择对话框，确认后启动重试"""
        if not self._last_summary:
            return
        failed = self._last_summary.get('failed_details', [])
        if not failed:
            QMessageBox.information(self, "提示", "没有需要重试的失败章节。")
            return

        # 构建选择对话框
        dialog = QMessageBox(self)
        dialog.setWindowTitle("选择重试章节")
        dialog.setText(f"共 {len(failed)} 个失败章节，请选择需要重试的项目：")
        dialog.setIcon(QMessageBox.Icon.Question)

        # 用自定义 widget 展示复选框列表
        from PyQt6.QtWidgets import QVBoxLayout as VBox
        container = QWidget()
        layout = VBox(container)
        layout.setContentsMargins(0, 8, 0, 8)
        checkboxes = []
        for i, d in enumerate(failed):
            cb = QCheckBox(f"{d['title'][:40]} — {d.get('error', '')[:50]}")
            cb.setChecked(True)  # 默认全选
            checkboxes.append(cb)
            layout.addWidget(cb)

        # 全选/全不选按钮
        toggle_row = QHBoxLayout()
        btn_all = QPushButton("全选")
        btn_none = QPushButton("全不选")
        btn_all.clicked.connect(lambda: [cb.setChecked(True) for cb in checkboxes])
        btn_none.clicked.connect(lambda: [cb.setChecked(False) for cb in checkboxes])
        toggle_row.addStretch(1)
        toggle_row.addWidget(btn_all)
        toggle_row.addWidget(btn_none)
        layout.addLayout(toggle_row)

        dialog.layout().addWidget(container, dialog.layout().count() - 1, 0, 1,
                                  dialog.layout().columnCount())
        cancel_btn = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        confirm_btn = dialog.addButton("确认重试", QMessageBox.ButtonRole.AcceptRole)
        dialog.exec()

        if dialog.clickedButton() != confirm_btn:
            return

        # 收集选中章节
        selected = [failed[i] for i, cb in enumerate(checkboxes) if cb.isChecked()]
        if not selected:
            return

        self._start_retry(selected)

    def _start_retry(self, selected: list):
        """启动 RetryWorker 重试选中章节"""
        novel_id = self._last_summary['novel_id']
        urls = [d['url'] for d in selected]

        self.btn_download.setEnabled(False)
        self.btn_retry.setEnabled(False)
        self.retry_frame.setVisible(False)
        self.chapter_progress.setVisible(True)
        self.chapter_progress.setRange(0, 0)
        self.chapter_progress.setFormat("章节: 正在重试...")
        self._chapter_progress_set = False
        self.image_progress.setVisible(False)
        self._image_progress_set = False

        self.log(f"🔄 开始重试 {len(urls)} 个失败章节...")

        cfg = dict(self.config)
        self._retry_worker = RetryWorker(
            novel_id=novel_id,
            chapter_urls=urls,
            config=cfg,
            headless=self.chk_headless.isChecked(),
            image_proxy_enabled=self.chk_image_proxy.isChecked(),
            image_proxy_addr=self.input_image_proxy.text().strip(),
            concurrent=self.spin_concurrent.value(),
            retry=self.spin_retry.value(),
        )
        self._retry_worker.log_line.connect(lambda s: self._append_log(s))
        self._retry_worker.finished.connect(self._on_retry_finished)
        self._retry_worker.progress.connect(self.on_chapter_progress)
        self._retry_worker.image_progress.connect(self.on_image_progress)
        self._retry_worker.start()

    def _on_retry_finished(self, success: bool, path: str):
        """重试完成回调"""
        self.chapter_progress.setVisible(False)
        self.chapter_progress.setFormat("章节: %v/%m  (%p%)")
        self.image_progress.setVisible(False)
        self.image_progress.setFormat("图片: %v/%m  (%p%)")
        self.btn_download.setEnabled(True)
        self.btn_retry.setEnabled(True)
        self._last_summary = None  # 清除旧 summary

        if success:
            self.result_view.append(f"\n✅ 重试完成，EPUB 已更新: {path}")
            self.log(f"重试完成: {path}")
            self.set_status(f"重试完成: {os.path.basename(path)}")
        else:
            self.result_view.append(f"\n❌ 重试失败")
            self.log("重试失败")
        self.retry_frame.setVisible(False)


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
