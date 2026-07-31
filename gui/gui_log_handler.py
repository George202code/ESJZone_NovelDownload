"""
GUI 日志桥接器：将 Python logging 输出转发到 PyQt6 信号
"""
import logging
from PyQt6.QtCore import QObject, pyqtSignal


class GuiLogHandler(QObject, logging.Handler):
    """同时继承 QObject（支持信号）和 logging.Handler（接收日志）"""

    log_line = pyqtSignal(str)

    def __init__(self, emit_slot=None):
        QObject.__init__(self)
        logging.Handler.__init__(self)
        if emit_slot:
            self.log_line.connect(emit_slot)

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self.log_line.emit(msg)
        except Exception:
            pass
