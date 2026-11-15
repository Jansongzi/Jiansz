"""统一更新窗口，联网、下载和启动器等待均在后台执行。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event, Thread

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QTextEdit,
)

from core.updates import discover_items, check_item, update_item
from core.native_process import native_popen


class UpdateDialog(QDialog):
    item_changed = Signal(int, object)
    log_message = Signal(str)
    work_finished = Signal(bool)

    def __init__(self, tasks, parent=None, prepare: bool = False):
        super().__init__(parent)
        self.setWindowTitle("脚本更新")
        self.resize(920, 620)
        self.setMinimumSize(700, 460)
        self.items = discover_items(tasks)
        self.prepare = prepare
        self.busy = False
        self.stop = Event()
        self.setStyleSheet("""
            QDialog { background: #202733; color: #E2E8F0; }
            QLabel { color: #CBD5E1; }
            QTableWidget, QTextEdit { background: #151C28; color: #E2E8F0;
                gridline-color: #334155; border: 1px solid #334155; }
            QHeaderView::section { background: #2B3647; color: #CBD5E1; padding: 8px; }
            QPushButton { color: #E2E8F0; background: #29384E; border: 1px solid #435873;
                padding: 7px 12px; border-radius: 5px; }
            QPushButton:disabled { color: #64748B; background: #202733; }
            QPushButton:hover { background: #354C6B; }
        """)
        layout = QVBoxLayout(self)
        self.hint = QLabel("自动检查并更新已启用脚本，完成后启动任务。" if prepare
                           else "检查已启用任务对应的脚本版本。有新版时可选中脚本手动更新。")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.table = QTableWidget(len(self.items), 4)
        self.table.setHorizontalHeaderLabels(["脚本", "本地版本", "最新版本", "状态"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for row, item in enumerate(self.items):
            name = QTableWidgetItem(item.name)
            if not prepare:
                name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                name.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, 0, name)
            self._show_item(row, item)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.check_button = QPushButton("检查版本")
        self.update_button = QPushButton("更新所选脚本")
        self.open_button = QPushButton("打开原工具")
        self.action_buttons = [self.check_button, self.update_button, self.open_button]
        for button in self.action_buttons:
            actions.addWidget(button)
        layout.addLayout(actions)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(160)
        layout.addWidget(self.output)
        footer = QHBoxLayout()
        footer.addStretch()
        self.cancel_button = QPushButton("取消本次执行" if prepare else "关闭")
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)
        self.check_button.clicked.connect(lambda: self._start("check"))
        self.check_button.setVisible(not prepare)
        self.update_button.clicked.connect(lambda: self._start("update"))
        self.open_button.clicked.connect(self._open_tool)
        self.open_button.setVisible(not prepare)
        self.cancel_button.clicked.connect(self.reject)
        self.item_changed.connect(self._show_item)
        self.log_message.connect(self.output.append)
        self.work_finished.connect(self._finished)
        QTimer.singleShot(0, lambda: self._start("prepare" if prepare else "check"))

    def _show_item(self, row, item) -> None:
        self.items[row] = item
        values = [item.current or "—", item.latest or "—", item.status]
        for column, text in enumerate(values, start=1):
            cell = QTableWidgetItem(text)
            cell.setToolTip(item.detail or str(item.executable))
            self.table.setItem(row, column, cell)
        self.table.item(row, 0).setToolTip(str(item.executable))

    def _start(self, mode: str) -> None:
        if self.busy:
            return
        selected = [(row, item) for row, item in enumerate(self.items)
                    if self.prepare or self.table.item(row, 0).checkState() == Qt.CheckState.Checked]
        self.stop.clear()
        self.busy = True
        self.table.setEnabled(False)
        for button in self.action_buttons:
            button.setEnabled(False)
        self.cancel_button.setText("停止等待")
        if self.prepare:
            self.hint.setText("正在检查并更新启用脚本，完成后自动启动任务。")
        elif mode == "update":
            self.hint.setText("正在检查并更新所选脚本。")
        else:
            self.hint.setText("正在检查脚本版本。")
        Thread(target=self._work, args=(mode, selected), daemon=True).start()

    def _work(self, mode, selected) -> None:
        ready = False
        try:
            if mode in {"check", "prepare", "update"}:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    jobs = {pool.submit(check_item, item, self.stop, self.log_message.emit): row
                            for row, item in selected}
                    for job in as_completed(jobs):
                        self.item_changed.emit(jobs[job], job.result())
            if mode in {"prepare", "update"}:
                for row, item in selected:
                    if self.stop.is_set():
                        break
                    if item.kind == "unsupported":
                        continue
                    try:
                        if item.status in {"有更新", "更新失败"}:
                            item.status = "正在更新"
                            self.item_changed.emit(row, item)
                            # 后端按已经检查到的目标版本更新。
                            item.status = "有更新"
                            update_item(item, self.stop, self.log_message.emit)
                    except Exception as exc:
                        item.status = "更新失败"
                        item.detail = str(exc)
                        self.log_message.emit(f"{item.name}：{exc}")
                    self.item_changed.emit(row, item)
            ready = bool(selected) and not self.stop.is_set() and all(
                item.status in {"已是最新", "本地版本较新"} for _, item in selected
            )
        except Exception as exc:
            self.log_message.emit(f"更新检查失败：{exc}")
        finally:
            self.work_finished.emit(ready)

    def _finished(self, ready: bool) -> None:
        self.busy = False
        self.table.setEnabled(True)
        for button in self.action_buttons:
            button.setEnabled(True)
        self.cancel_button.setText("取消本次执行" if self.prepare else "关闭")
        if self.prepare and ready:
            self.accept()
        elif self.prepare:
            self.hint.setText("脚本尚未全部更新就绪，任务未启动。处理下方提示后，点击“重试检查并更新”。")
            self.update_button.setText("重试检查并更新")
        else:
            self.hint.setText("检查结束。选中一行可打开原工具，鼠标停在状态上可查看详细原因。")

    def _selected_item(self):
        row = self.table.currentRow()
        return self.items[row] if row >= 0 else None

    def _open_tool(self) -> None:
        item = self._selected_item()
        if item and item.executable.is_file():
            path = item.executable
            if item.key == "march7th":
                path = path.parent / "March7th Launcher.exe"
            try:
                native_popen([str(path)], cwd=str(path.parent))
            except OSError as exc:
                self.output.append(f"打开原工具失败：{exc}")

    def reject(self) -> None:
        if self.busy:
            self.stop.set()
            self.hint.setText("正在停止等待。已开始的原工具更新可继续在原工具中查看。")
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self.busy:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
