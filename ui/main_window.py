# -*- coding: utf-8 -*-
from __future__ import annotations

from core import *
import random
import re
import json
from PySide6.QtCore import (
    QPropertyAnimation, QParallelAnimationGroup, QSequentialAnimationGroup,
    QEasingCurve, QRect, QRectF, QDateTime, QSize, Signal, QEvent, Property, QPointF
)
from PySide6.QtGui import QColor, QBrush, QPainter, QPainterPath, QPen, QLinearGradient, QPixmap
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QTableView, QAbstractScrollArea, QMenu, QFrame,
    QStyledItemDelegate, QStyleOptionButton, QStyle, QStackedWidget, QScrollArea, QSplitter,
    QLineEdit, QSpinBox, QProgressBar, QListWidget, QListWidgetItem
)


class RootBackgroundWidget(QWidget):
    """支持背景壁纸 30% 不透明度叠加深色底板的现代沉浸式根窗口。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RootWidget")
        self._bg_pixmap: QPixmap | None = None

    def set_background_image(self, bg_path_or_pixmap) -> None:
        if isinstance(bg_path_or_pixmap, str) and bg_path_or_pixmap:
            self._bg_pixmap = QPixmap(bg_path_or_pixmap)
        elif isinstance(bg_path_or_pixmap, QPixmap):
            self._bg_pixmap = bg_path_or_pixmap
        else:
            self._bg_pixmap = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 16, 16)

        # 1. 深色科技底色 (纯深色基底)
        painter.fillPath(path, QBrush(QColor(16, 20, 28)))

        # 2. 40% 不透明度壁纸图层 (保证视觉通透同时极大提升文字可读性与壁纸质感)
        if self._bg_pixmap and not self._bg_pixmap.isNull():
            painter.save()
            painter.setClipPath(path)
            painter.setOpacity(0.40)
            painter.drawPixmap(rect, self._bg_pixmap)
            painter.restore()

        # 3. 顶层精致圆角微光边框
        painter.setPen(QPen(QColor(70, 95, 135, 140), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(rect).adjusted(0.75, 0.75, -0.75, -0.75), 16, 16)


class DashboardBannerWidget(QFrame):
    """参考 BetterGI 风格的高清插画横幅大卡片。"""

    def __init__(self, banner_path: str | Path | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("DashboardBannerCard")
        self.setFixedHeight(180)
        self._banner_pixmap: QPixmap | None = None
        self._load_banner(banner_path)

    def _load_banner(self, preferred_path: str | Path | None = None) -> None:
        candidates = []
        if preferred_path:
            candidates.append(Path(preferred_path))

        # 候选路径（确保任何打包环境均可加载）
        base = app_dir()
        candidates.extend([
            base / "assets" / "banner" / "banner.jpg",
            base / "assets" / "banner" / "banner.png",
        ])

        for path in candidates:
            if path and path.exists():
                pix = QPixmap(str(path))
                if not pix.isNull():
                    self._banner_pixmap = pix
                    break

    def set_banner_image(self, banner_path: str | Path) -> None:
        self._load_banner(banner_path)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 14, 14)

        # 1. 基础深色背景
        painter.fillPath(path, QBrush(QColor(18, 24, 36)))

        # 2. 绘制插画背景 (铺满横幅卡片)
        if self._banner_pixmap and not self._banner_pixmap.isNull():
            painter.save()
            painter.setClipPath(path)

            # 等比缩放覆盖全卡片，居中对齐
            scaled = self._banner_pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            draw_x = (self.width() - scaled.width()) // 2
            draw_y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(draw_x, draw_y, scaled)

            # 3. 极简轻量微透渐变：左侧轻微压暗保证文字阅读，右侧完整透出明亮插画（如同 BetterGI）
            grad = QLinearGradient(0, 0, self.width(), 0)
            grad.setColorAt(0.0, QColor(10, 14, 22, 175))
            grad.setColorAt(0.40, QColor(10, 14, 22, 120))
            grad.setColorAt(0.75, QColor(10, 14, 22, 20))
            grad.setColorAt(1.0, QColor(10, 14, 22, 0))
            painter.fillRect(rect, QBrush(grad))

            painter.restore()

        # 4. 微光圆角外边框
        painter.setPen(QPen(QColor(70, 95, 145, 140), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(rect).adjusted(0.75, 0.75, -0.75, -0.75), 14, 14)


class SwitchButton(QWidget):
    """现代 Fluent 风格流畅动画切换开关 (Toggle Switch)。"""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ToggleSwitch")
        self._checked = checked
        self._thumb_position = 1.0 if checked else 0.0
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        self._anim = QPropertyAnimation(self, b"thumb_position", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

    def get_thumb_position(self) -> float:
        return self._thumb_position

    def set_thumb_position(self, pos: float) -> None:
        self._thumb_position = pos
        self.update()

    thumb_position = Property(float, get_thumb_position, set_thumb_position)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, animate: bool = True) -> None:
        if self._checked == checked:
            return
        self._checked = checked
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._thumb_position)
            self._anim.setEndValue(1.0 if checked else 0.0)
            self._anim.start()
        else:
            self._thumb_position = 1.0 if checked else 0.0
            self.update()
        self.toggled.emit(self._checked)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()
        radius = h / 2.0

        # 背景轨道
        if self._checked:
            bg_color = QColor(59, 130, 246)
            border_color = QColor(96, 165, 250)
        else:
            bg_color = QColor(36, 42, 54)
            border_color = QColor(70, 80, 100)

        track_rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        painter.setPen(QPen(border_color, 1.0))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(track_rect, radius, radius)

        # 内部纯白滑动圆球 Thumb
        thumb_diameter = h - 6.0
        thumb_radius = thumb_diameter / 2.0
        thumb_min_x = 3.0 + thumb_radius
        thumb_max_x = w - 3.0 - thumb_radius
        current_x = thumb_min_x + (thumb_max_x - thumb_min_x) * self._thumb_position
        thumb_y = h / 2.0

        painter.setPen(Qt.PenStyle.NoPen)
        if self._checked:
            painter.setBrush(QBrush(QColor(255, 255, 255)))
        else:
            painter.setBrush(QBrush(QColor(210, 220, 235)))

        painter.drawEllipse(QPointF(current_x, thumb_y), thumb_radius, thumb_radius)


class DraggableTaskListWidget(QListWidget):
    """支持鼠标直接拖拽任务调整优先级的列表控件。"""

    order_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSpacing(6)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item {
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }
            QListWidget::item:selected {
                background: transparent;
                border: none;
            }
        """)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.order_changed.emit()


class SidebarFrame(QFrame):
    """支持 35% 不透明度 + 8px 模糊柔光磨砂质感的侧边栏框架。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("NavSidebar")
        self.setFixedWidth(168)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 12, 12)

        # 1. 35% 不透明度深色底板 (0.35 opacity: 255 * 0.35 = 89)
        painter.fillPath(path, QBrush(QColor(18, 24, 36, 89)))

        # 2. 8px 模糊感柔光叠层 (增强 Acrylic 毛玻璃层次与微透质感)
        inner_path = QPainterPath()
        inner_path.addRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), 11, 11)
        glow_grad = QLinearGradient(0, 0, 0, self.height())
        glow_grad.setColorAt(0.0, QColor(255, 255, 255, 22))
        glow_grad.setColorAt(0.20, QColor(255, 255, 255, 8))
        glow_grad.setColorAt(1.0, QColor(255, 255, 255, 2))
        painter.fillPath(inner_path, QBrush(glow_grad))

        # 3. 35% 不透明度微光边框 (255 * 0.35 = 89)
        painter.setPen(QPen(QColor(100, 140, 200, 89), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(rect).adjusted(0.6, 0.6, -0.6, -0.6), 12, 12)


class NavIconWidget(QWidget):
    """纯矢量自绘 Fluent 单色线框导航图标（彻底杜绝 Windows 彩色 Emoji）。"""

    def __init__(self, icon_type: str, color: QColor, parent: QWidget | None = None):
        super().__init__(parent)
        self.icon_type = icon_type  # 'dashboard' | 'tasks' | 'stats' | 'logs' | 'settings'
        self.icon_color = color
        self.setFixedSize(18, 18)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_color(self, color: QColor) -> None:
        self.icon_color = color
        self.update()

    def paintEvent(self, event):
        import math
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        pen = QPen(self.icon_color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        w = self.width()
        h = self.height()

        if self.icon_type == "dashboard":
            # 播放三角 (纯单色实心三角)
            painter.setBrush(QBrush(self.icon_color))
            path = QPainterPath()
            path.moveTo(4.5, 3.5)
            path.lineTo(14.0, 9.0)
            path.lineTo(4.5, 14.5)
            path.closeSubpath()
            painter.drawPath(path)

        elif self.icon_type == "tasks":
            # 任务调度清单 (单色列表矩形卡片带三行线)
            painter.drawRoundedRect(QRectF(2.5, 2.5, 13.0, 13.0), 2.5, 2.5)
            painter.drawLine(QPointF(5.5, 6.0), QPointF(12.5, 6.0))
            painter.drawLine(QPointF(5.5, 9.0), QPointF(12.5, 9.0))
            painter.drawLine(QPointF(5.5, 12.0), QPointF(10.0, 12.0))

        elif self.icon_type == "stats":
            # 柱状数据统计图表 (3 根纯单色柱条)
            painter.setBrush(QBrush(self.icon_color))
            painter.drawRect(QRectF(3.0, 9.5, 3.0, 5.5))
            painter.drawRect(QRectF(7.5, 4.5, 3.0, 10.5))
            painter.drawRect(QRectF(12.0, 7.0, 3.0, 8.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(QPointF(2.0, 15.0), QPointF(16.0, 15.0))

        elif self.icon_type == "logs":
            # 运行日志代码终端 (带有 >_ 纯单色提示符的控制台框)
            painter.drawRoundedRect(QRectF(2.5, 2.5, 13.0, 13.0), 2.5, 2.5)
            path = QPainterPath()
            path.moveTo(5.5, 6.5)
            path.lineTo(8.0, 8.0)
            path.lineTo(5.5, 9.5)
            painter.drawPath(path)
            painter.drawLine(QPointF(9.5, 9.5), QPointF(12.5, 9.5))

        elif self.icon_type == "settings":
            # 全局设置齿轮 (纯单色机械齿轮)
            cx, cy = w / 2.0, h / 2.0
            painter.drawEllipse(QPointF(cx, cy), 3.2, 3.2)
            for i in range(6):
                angle = i * (3.14159265 / 3.0)
                dx = 6.2 * math.cos(angle)
                dy = 6.2 * math.sin(angle)
                painter.drawLine(QPointF(cx + dx * 0.65, cy + dy * 0.65), QPointF(cx + dx, cy + dy))


class NavButton(QPushButton):
    """左侧侧边栏现代导航项按钮（矢量自绘纯黑白/单色图标，严格像素对齐）。"""

    def __init__(self, icon_type: str, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.icon_type = icon_type
        self.nav_text = text
        self._is_active = False
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # 内部采用精确像素对齐布局
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(12)

        # 左侧纯矢量单色图标
        self.icon_widget = NavIconWidget(icon_type, QColor("#CBD5E1"), self)
        layout.addWidget(self.icon_widget)

        # 右侧文字
        self.text_lbl = QLabel(text)
        self.text_lbl.setStyleSheet("font-size: 13.5px; font-weight: 600; background: transparent; border: none;")
        layout.addWidget(self.text_lbl, stretch=1)

        self._update_style()

    def set_active(self, active: bool) -> None:
        self._is_active = active
        self._update_style()

    def enterEvent(self, event):
        super().enterEvent(event)
        if not self._is_active:
            self.icon_widget.set_color(QColor("#FFFFFF"))

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if not self._is_active:
            self.icon_widget.set_color(QColor("#CBD5E1"))

    def _update_style(self) -> None:
        if self._is_active:
            self.setStyleSheet("""
                QPushButton {
                    background-color: rgba(59, 130, 246, 55);
                    border: none;
                    border-left: 3.5px solid #3B82F6;
                    border-radius: 8px;
                }
            """)
            self.icon_widget.set_color(QColor("#60A5FA"))
            self.text_lbl.setStyleSheet("color: #FFFFFF; font-size: 13.5px; font-weight: 700; background: transparent; border: none;")
        else:
            self.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    border: none;
                    border-left: 3.5px solid transparent;
                    border-radius: 8px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 22);
                }
            """)
            self.icon_widget.set_color(QColor("#CBD5E1"))
            self.text_lbl.setStyleSheet("color: #E2E8F0; font-size: 13.5px; font-weight: 600; background: transparent; border: none;")


class FluentCard(QFrame):
    """BetterGI 风格的深色质感卡片容器。"""

    def __init__(self, title: str = "", subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("FluentCard")
        self.setStyleSheet("""
            QFrame#FluentCard {
                background-color: rgba(34, 39, 50, 230);
                border: 1px solid rgba(65, 75, 95, 140);
                border-radius: 10px;
            }
        """)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(16, 14, 16, 14)
        self.main_layout.setSpacing(12)

        if title:
            header_layout = QVBoxLayout()
            header_layout.setSpacing(2)
            self.title_label = QLabel(title)
            self.title_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #60A5FA; background: transparent;")
            header_layout.addWidget(self.title_label)

            if subtitle:
                self.subtitle_label = QLabel(subtitle)
                self.subtitle_label.setStyleSheet("font-size: 11.5px; color: #8C9BB4; background: transparent;")
                header_layout.addWidget(self.subtitle_label)

class StartupReportDialog(QDialog):
    """上次运行异常报告弹窗（支持长文本滚动、固定高度、随时关闭）。"""

    def __init__(self, report_text: str, report_dir: Path, parent=None):
        super().__init__(parent)
        self.report_dir = report_dir
        self.setWindowTitle("上次运行异常报告")
        self.setFixedSize(540, 460)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setStyleSheet("""
            QDialog {
                background-color: #1E232F;
                border: 1px solid rgba(70, 85, 110, 180);
                border-radius: 10px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(14)

        # 顶部标题栏
        top_h = QHBoxLayout()
        top_h.setSpacing(12)

        icon_lbl = QLabel("⚠️")
        icon_lbl.setStyleSheet("font-size: 26px; background: transparent; border: none;")
        top_h.addWidget(icon_lbl)

        v_head = QVBoxLayout()
        v_head.setSpacing(2)
        title_lbl = QLabel("上次运行报告")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #F1F5F9; background: transparent; border: none;")
        sub_lbl = QLabel("以下是上一次任务调度中记录的异常/跳过信息：")
        sub_lbl.setStyleSheet("font-size: 12px; color: #94A3B8; background: transparent; border: none;")
        v_head.addWidget(title_lbl)
        v_head.addWidget(sub_lbl)
        top_h.addLayout(v_head, stretch=1)
        layout.addLayout(top_h)

        # 中间滚动文本区
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(report_text)
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: rgba(13, 16, 23, 240);
                color: #E2E8F0;
                border: 1px solid rgba(60, 75, 100, 160);
                border-radius: 8px;
                font-size: 12.5px;
                line-height: 1.5;
                padding: 10px;
            }
        """)
        layout.addWidget(self.text_edit, stretch=1)

        # 底部操作按钮行
        btn_h = QHBoxLayout()
        btn_h.setSpacing(12)

        self.btn_open_log = QPushButton("📁 打开日志目录")
        self.btn_open_log.setFixedHeight(36)
        self.btn_open_log.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_log.setStyleSheet("""
            QPushButton {
                background-color: rgba(45, 55, 72, 220);
                color: #E2E8F0;
                border: 1px solid rgba(80, 100, 130, 140);
                border-radius: 6px;
                padding: 0 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(60, 75, 100, 240);
                color: #FFFFFF;
            }
        """)
        self.btn_open_log.clicked.connect(self._on_open_log)
        btn_h.addWidget(self.btn_open_log)

        btn_h.addStretch(1)

        self.btn_close = QPushButton("我知道了")
        self.btn_close.setFixedHeight(36)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border: 1px solid #3B82F6;
                border-radius: 6px;
                padding: 0 24px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1D4ED8;
            }
        """)
        self.btn_close.clicked.connect(self.accept)
        btn_h.addWidget(self.btn_close)

        layout.addLayout(btn_h)

    def _on_open_log(self):
        try:
            if hasattr(self.parent(), "open_directory"):
                self.parent().open_directory(self.report_dir)
            else:
                import os
                os.startfile(str(self.report_dir))
        except Exception:
            pass


class MasterTaskItemWidget(QFrame):
    """左侧任务列表中的单个任务卡片项 (极简干净 Fluent 风格)。"""

    clicked = Signal(int)
    toggled = Signal(int, bool)

    def __init__(self, index: int, name: str, enabled: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("MasterTaskItem")
        self.task_index = index
        self._selected = False
        self.setFixedHeight(46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(10)

        # 拖拽手柄
        self.handle_label = QLabel("⠿")
        self.handle_label.setObjectName("ItemHandle")
        self.handle_label.setCursor(Qt.CursorShape.SizeVerCursor)
        self.handle_label.setToolTip("按住上下拖拽调整执行优先级")
        self.handle_label.setStyleSheet("color: #64748B; font-size: 15px; background: transparent; border: none;")
        layout.addWidget(self.handle_label)

        # 状态指示灯
        self.dot_label = QLabel("●")
        self.dot_label.setObjectName("ItemDot")
        self._update_dot(enabled)
        layout.addWidget(self.dot_label)

        # 任务名称
        self.name_label = QLabel(name)
        self.name_label.setObjectName("ItemName")
        self.name_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #E2E8F0; background: transparent; border: none;")
        layout.addWidget(self.name_label, stretch=1)

        # 切换开关 Toggle Switch
        self.switch_btn = SwitchButton(checked=enabled)
        self.switch_btn.toggled.connect(lambda chk: self._on_switch_toggled(chk))
        layout.addWidget(self.switch_btn)

        self._update_style()

    def _update_dot(self, enabled: bool) -> None:
        if enabled:
            self.dot_label.setStyleSheet("color: #10B981; font-size: 10px; background: transparent; border: none;")
        else:
            self.dot_label.setStyleSheet("color: #64748B; font-size: 10px; background: transparent; border: none;")

    def _on_switch_toggled(self, chk: bool) -> None:
        self._update_dot(chk)
        self.toggled.emit(self.task_index, chk)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._update_style()

    def update_info(self, index: int, name: str, enabled: bool) -> None:
        self.task_index = index
        self.name_label.setText(name)
        self.switch_btn.setChecked(enabled, animate=False)
        self._update_dot(enabled)

    def _update_style(self) -> None:
        if self._selected:
            self.setStyleSheet("""
                QFrame#MasterTaskItem {
                    background-color: rgba(59, 130, 246, 45);
                    border: 1.5px solid #3B82F6;
                    border-radius: 8px;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame#MasterTaskItem {
                    background-color: rgba(30, 36, 48, 160);
                    border: 1px solid rgba(60, 72, 95, 120);
                    border-radius: 8px;
                }
                QFrame#MasterTaskItem:hover {
                    background-color: rgba(45, 54, 70, 200);
                    border-color: rgba(85, 105, 140, 180);
                }
            """)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.task_index)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class SpeechBubbleLabel(QLabel):
    clicked = Signal()

    """代码绘制的简小松发言气泡。

    v31.1：气泡高度会跟随文本自动调整，短句保持小气泡；
    偶发长句最多扩大到三行高度，避免文字被裁切。
    """

    MIN_BUBBLE_HEIGHT = 76
    MAX_BUBBLE_HEIGHT = 138

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(self.MIN_BUBBLE_HEIGHT)
        self.setMaximumHeight(self.MIN_BUBBLE_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        QTimer.singleShot(0, self._adjust_height_to_text)

    def setText(self, text: str) -> None:  # type: ignore[override]
        super().setText(str(text or ""))
        QTimer.singleShot(0, self._adjust_height_to_text)

    def sizeHint(self) -> QSize:  # type: ignore[override]
        hint = super().sizeHint()
        return QSize(hint.width(), self.height())

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._adjust_height_to_text()

    def mousePressEvent(self, event):  # type: ignore[override]
        self.clicked.emit()
        super().mousePressEvent(event)

    def _adjust_height_to_text(self) -> None:
        width = max(160, self.width() - 50)
        metrics = self.fontMetrics()
        text_rect = metrics.boundingRect(
            QRect(0, 0, width, 1000),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            self.text(),
        )
        # 文字区上下 padding + 气泡尾巴高度；限制最大高度，避免气泡变说明书。
        desired = text_rect.height() + 38
        desired = max(self.MIN_BUBBLE_HEIGHT, min(self.MAX_BUBBLE_HEIGHT, desired))
        if self.minimumHeight() != desired or self.maximumHeight() != desired:
            self.setMinimumHeight(desired)
            self.setMaximumHeight(desired)
            self.updateGeometry()
        self.update()

    def paintEvent(self, event):  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = max(1, self.width())
        h = max(1, self.height())
        tail_h = 14.0
        margin = 3.0
        body = QRectF(margin, margin, w - margin * 2, h - tail_h - margin * 2)

        path = QPainterPath()
        path.addRoundedRect(body, 22.0, 22.0)

        # 小尾巴朝下，指向下方简小松；位置略偏右，接近参考图气泡。
        tail_w = min(34.0, max(20.0, w * 0.085))
        tail_x = min(w - tail_w - 28.0, max(28.0, w * 0.70))
        path.moveTo(tail_x, body.bottom() - 1.0)
        path.lineTo(tail_x + tail_w * 0.42, body.bottom() + tail_h)
        path.lineTo(tail_x + tail_w, body.bottom() - 1.0)
        path.closeSubpath()

        # 晶莹剔透微光渐变背景
        gradient = QLinearGradient(0, 0, 0, h)
        gradient.setColorAt(0.0, QColor(255, 255, 255, 248))
        gradient.setColorAt(1.0, QColor(242, 247, 255, 240))

        painter.setPen(QPen(QColor(115, 145, 205, 210), 1.8))
        painter.setBrush(QBrush(gradient))
        painter.drawPath(path)

        painter.setPen(QColor(42, 58, 88, 240))
        font = self.font()
        font.setBold(True)
        painter.setFont(font)
        text_rect = body.adjusted(20.0, 8.0, -20.0, -8.0)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            self.text(),
        )


class WinTitleButton(QPushButton):
    """Windows 11 Fluent 矢量自绘窗口控制按钮（最小化、最大化、关闭）。"""

    def __init__(self, btn_type: str, parent=None):
        super().__init__(parent)
        self.btn_type = btn_type  # 'min' | 'max' | 'close'
        self.is_maximized_state = False
        self.setFixedSize(46, 34)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_maximized_state(self, maximized: bool) -> None:
        self.is_maximized_state = maximized
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 悬停与按下背景
        if self.isDown():
            if self.btn_type == "close":
                painter.fillRect(self.rect(), QColor("#C4101E"))
            else:
                painter.fillRect(self.rect(), QColor(255, 255, 255, 65))
        elif self.underMouse():
            if self.btn_type == "close":
                painter.fillRect(self.rect(), QColor("#E81123"))
            else:
                painter.fillRect(self.rect(), QColor(255, 255, 255, 38))

        # 绘制纯白高清矢量图标
        pen = QPen(QColor(255, 255, 255), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        cx = self.width() / 2.0
        cy = self.height() / 2.0

        if self.btn_type == "min":
            # 最小化横线 (长 11px)
            painter.drawLine(int(cx - 5.5), int(cy + 0.5), int(cx + 5.5), int(cy + 0.5))
        elif self.btn_type == "max":
            if self.is_maximized_state:
                # 还原图标：前后双层方框
                painter.drawRect(int(cx - 4.5), int(cy - 2.5), 7, 7)
                painter.drawLine(int(cx - 2.5), int(cy - 5.5), int(cx + 5.5), int(cy - 5.5))
                painter.drawLine(int(cx + 5.5), int(cy - 5.5), int(cx + 5.5), int(cy + 2.5))
                painter.drawLine(int(cx + 2.5), int(cy + 2.5), int(cx + 5.5), int(cy + 2.5))
            else:
                # 最大化方框 (9px x 9px)
                painter.drawRect(int(cx - 4.5), int(cy - 4.5), 9, 9)
        elif self.btn_type == "close":
            # 关闭叉号 (9px x 9px)
            painter.drawLine(int(cx - 4.5), int(cy - 4.5), int(cx + 4.5), int(cy + 4.5))
            painter.drawLine(int(cx + 4.5), int(cy - 4.5), int(cx - 4.5), int(cy + 4.5))


class CustomTitleBar(QWidget):
    """自定义全透明沉浸式顶栏（取代原生黑框标题栏）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self.setObjectName("CustomTitleBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(0)

        # 标题栏图标与文字
        title_box = QHBoxLayout()
        title_box.setSpacing(8)

        self.icon_label = QLabel()
        base = getattr(parent, "base_dir", app_dir())
        icon_candidates = [
            base / "assets" / "icon.ico",
            base / "icon.ico",
        ]
        for ip in icon_candidates:
            if ip and ip.exists():
                pix = QPixmap(str(ip))
                if not pix.isNull():
                    scaled_pix = pix.scaled(20, 20, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    self.icon_label.setPixmap(scaled_pix)
                    title_box.addWidget(self.icon_label)
                    break

        self.title_label = QLabel("简小松·脚本调度助手 v0.1.1")
        self.title_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #FFFFFF; background: transparent; border: none;")
        title_box.addWidget(self.title_label)
        layout.addLayout(title_box)

        layout.addStretch(1)

        # 右上角标准 Fluent 窗口控制按钮
        self.min_btn = WinTitleButton("min", self)
        self.min_btn.clicked.connect(self._on_minimize)

        self.max_btn = WinTitleButton("max", self)
        self.max_btn.clicked.connect(self._toggle_maximize)

        self.close_btn = WinTitleButton("close", self)
        self.close_btn.clicked.connect(self._on_close)

        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

        self._drag_pos = None

    def _on_minimize(self):
        win = self.window()
        if win:
            win.showMinimized()

    def _on_close(self):
        win = self.window()
        if win:
            win.close()

    def _toggle_maximize(self):
        win = self.window()
        if not win:
            return
        if win.isMaximized():
            win.showNormal()
            self.max_btn.set_maximized_state(False)
        else:
            win.showMaximized()
            self.max_btn.set_maximized_state(True)

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            if win:
                self._drag_pos = event.globalPosition().toPoint() - win.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            win = self.window()
            if win and not win.isMaximized():
                win.move(event.globalPosition().toPoint() - self._drag_pos)
                event.accept()

    def mouseDoubleClickEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()
            event.accept()


class FrozenColumnsTableWidget(QTableWidget):
    """现代化任务表格控件（单层无缝纯净渲染，消除覆盖与错位）。"""

    def __init__(self, rows: int, columns: int, frozen_columns: int = 2, parent=None):
        super().__init__(rows, columns, parent)


class MainWindow(QMainWindow):
    COL_ENABLED = 0
    COL_NAME = 1
    COL_PATH = 2
    COL_BROWSE = 3
    COL_ORDER = 4
    COL_TIMEOUT = 5
    COL_ACTION = 6
    COL_USE_ARGS = 7
    COL_ARGS = 8
    COL_WAIT_MODE = 9
    COL_WAIT_PROCESS = 10
    COL_CONFIRM_ENTER = 11
    COL_CONCURRENT_GROUP = 12
    COL_CONCURRENT_POLICY = 13
    COL_WATCHDOG = 14
    COL_PROCESS_KEYWORDS = 15
    COL_WINDOW_KEYWORDS = 16
    COL_ADVANCED = 17

    def __init__(self) -> None:
        super().__init__()
        self.base_dir = app_dir()
        self.config_manager = ConfigManager(self.base_dir / CONFIG_FILE_NAME)
        self.file_logger = FileLogger(self.base_dir / LOG_DIR_NAME)
        self.stats_manager = RuntimeStatsManager(self.base_dir)
        self.config = self.config_manager.load()

        self.runner_thread: Optional[QThread] = None
        self.runner_worker: Optional[ScriptRunnerWorker] = None
        self.is_running = False

        # v28.7：简小松状态机。
        # idle：未执行任务时待机；work：执行中；rest：暂停/结束；error：异常提示。
        # error 带最低展示锁定时间，连续触发按 15s、30s、45s... 叠加。
        self.status_character_state = "idle"
        self.status_character_last_non_error_state = "idle"
        self.status_character_error_count = 0
        self.status_character_error_locked_until_ms = 0
        self.status_character_random_timer: Optional[QTimer] = None  # v28.8：保留兼容，不再高频随机换图
        self.shutdown_warning_dialog: Optional[QDialog] = None
        self.shutdown_warning_timer: Optional[QTimer] = None
        self.shutdown_warning_remaining = 0
        self.shutdown_warning_countdown_label: Optional[QLabel] = None
        self.status_character_error_timer: Optional[QTimer] = None
        # v31：统一气泡优先级，避免卡片提示/新手引导覆盖运行中或异常提示。
        self.bubble_priority = 0
        self.bubble_restore_timer: Optional[QTimer] = None
        self._reported_abnormal_messages: set[str] = set()
        self.run_task_error_count = 0
        # v31.3：未配置引导气泡支持点击循环播放，避免单条短提示信息量不足。
        self.current_bubble_kind = "idle"
        self.guide_bubble_index = 0
        self.guide_bubble_texts = [
            "还没有配置任务哦～",
            "可以点击左上角\n“文件”导入配置。",
            "也可以在任务执行卡片里\n手动设置配置。",
            "配置完成后，点击\n开始执行就可以啦。",
            "第一次使用的话，\n可以先观察一下运行状态哦～",
        ]

        self.current_task_name = "-"
        self.current_elapsed = 0
        self.current_progress = (0, 0)
        self.tasks_list: List[TaskConfig] = []
        self.current_task_idx = 0
        self._master_task_widgets: List[MasterTaskItemWidget] = []

        self.setWindowTitle("简小松·脚本调度助手 v0.1.1")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(960, 600)
        self.resize(1060, 680)

        self._build_ui()
        self._connect_signals()
        self._load_config_to_ui()
        self.apply_theme(self.config.theme)

        self.append_log("程序启动。")
        self.append_log(f"配置文件：{self.config_manager.config_path}")
        self.append_log(f"日志目录：{self.file_logger.log_dir}")
        self.append_log(f"资源目录：{self.base_dir / 'assets'}")
        QTimer.singleShot(0, lambda: self.set_status_character_state("idle", force=True))
        QTimer.singleShot(900, lambda: self.show_first_config_guide_if_needed(force=True))
        self.refresh_runtime_stats_view()
        QTimer.singleShot(800, self.show_startup_problem_report_if_needed)

        if self.config.auto_start_tasks:
            self.append_log("已启用程序启动后自动开始执行任务。")
            QTimer.singleShot(1500, self.start_tasks)

    def _build_ui(self) -> None:
        """现代化 BetterGI 风格侧边栏导航与多页面架构。"""
        root = RootBackgroundWidget(self)
        self.root_widget = root
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 4, 10, 6)
        root_layout.setSpacing(6)

        # 1. 顶部全透明沉浸式自定义标题栏
        self.title_bar = CustomTitleBar(self)
        root_layout.addWidget(self.title_bar)

        # 2. 中间主体区：左侧边栏 + 右侧页面栈
        body_layout = QHBoxLayout()
        body_layout.setContentsMargins(0, 2, 0, 0)
        body_layout.setSpacing(8)

        # ---- 左侧侧边栏 (35% 不透明度 + 8px 模糊感 Acrylic 磨砂底板) ----
        sidebar = SidebarFrame()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(8, 14, 8, 14)
        sidebar_layout.setSpacing(6)

        # 顶部菜单标题 (精美 Section Header 标头，与下方选项形成鲜明对比)
        menu_title_label = QLabel("≡  功能导航")
        menu_title_label.setStyleSheet("""
            font-size: 11.5px;
            font-weight: 700;
            color: #94A3B8;
            letter-spacing: 1.5px;
            padding-left: 8px;
            margin-top: 2px;
            margin-bottom: 8px;
            background: transparent;
            border: none;
        """)
        sidebar_layout.addWidget(menu_title_label)

        # 导航项按钮 (纯矢量自绘 Fluent 单色图标 + 像素级对齐)
        self.nav_btn_dashboard = NavButton("dashboard", "启动中心")
        self.nav_btn_tasks = NavButton("tasks", "任务调度")
        self.nav_btn_stats = NavButton("stats", "运行统计")
        self.nav_btn_logs = NavButton("logs", "运行日志")
        self.nav_btn_settings = NavButton("settings", "全局设置")

        self.nav_buttons = [
            self.nav_btn_dashboard,
            self.nav_btn_tasks,
            self.nav_btn_stats,
            self.nav_btn_logs,
            self.nav_btn_settings,
        ]

        for idx, btn in enumerate(self.nav_buttons):
            if idx == 4:
                sidebar_layout.addStretch(1)
            sidebar_layout.addWidget(btn)
            btn.clicked.connect(lambda _c=False, i=idx: self._switch_page(i))

        body_layout.addWidget(sidebar)

        # ---- 右侧多页面堆叠区 (QStackedWidget) ----
        self.stacked_widget = QStackedWidget()
        body_layout.addWidget(self.stacked_widget, stretch=1)
        root_layout.addLayout(body_layout, stretch=1)

        # 构建 5 个页面
        self._build_dashboard_page()
        self._build_tasks_page()
        self._build_stats_page()
        self._build_logs_page()
        self._build_settings_page()

        # 3. 底部极简超链接栏
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(8, 0, 8, 2)
        footer_layout.setSpacing(4)
        self.author_link_button = QPushButton("联系作者")
        self.github_link_button = QPushButton("GitHub")
        self.about_link_button = QPushButton("关于 / 说明")
        self.about_link_button.setVisible(False)

        link_style = """
            QPushButton {
                color: #60A5FA;
                text-decoration: none;
                border: none;
                background: transparent;
                padding: 2px 8px;
                font-size: 11.5px;
            }
            QPushButton:hover {
                color: #93C5FD;
                text-decoration: underline;
            }
        """

        for btn in [self.author_link_button, self.github_link_button]:
            btn.setFlat(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(link_style)
            footer_layout.addWidget(btn)

        footer_layout.addStretch(1)
        self.donation_button = QPushButton("赞赏支持")
        self.donation_button.setVisible(False)
        root_layout.addLayout(footer_layout)

        self._switch_page(0)
        self._build_menu()

    def _switch_page(self, index: int) -> None:
        """切换侧边栏页面并高亮对应项。"""
        self.stacked_widget.setCurrentIndex(index)
        for idx, btn in enumerate(self.nav_buttons):
            btn.set_active(idx == index)
        if index == 1:
            self._render_master_task_list()

    def _build_dashboard_page(self) -> None:
        """页面 0：启动中心 (Dashboard) - 精简大气 Banner 与核心仪表盘。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

        # 顶部 BetterGI 风格高清插画大横幅 Banner (高度 240px，充分饱满展示)
        banner_img_path = self.base_dir / "assets" / "banner" / "banner.jpg"
        banner_card = DashboardBannerWidget(banner_img_path if banner_img_path.exists() else None, page)
        banner_card.setFixedHeight(240)
        banner_layout = QHBoxLayout(banner_card)
        banner_layout.setContentsMargins(36, 28, 36, 28)
        banner_layout.setSpacing(16)

        banner_text_layout = QVBoxLayout()
        banner_text_layout.setSpacing(10)
        banner_text_layout.addStretch(1)

        title_label = QLabel("简小松·脚本调度助手")
        title_label.setStyleSheet("font-size: 26px; font-weight: 800; color: #FFFFFF; background: transparent; border: none;")
        sub_title_label = QLabel("自动化二游脚本调度神器 · 稳定高效 · 一条龙托管")
        sub_title_label.setStyleSheet("font-size: 14px; font-weight: 500; color: #BAE6FD; background: transparent; border: none;")
        
        banner_text_layout.addWidget(title_label)
        banner_text_layout.addWidget(sub_title_label)
        banner_text_layout.addStretch(1)
        banner_layout.addLayout(banner_text_layout, stretch=1)

        # 虚拟占位（保持兼容）
        self.status_character_text_label = SpeechBubbleLabel("")
        self.status_character_text_label.setVisible(False)
        self.status_character_label = QLabel()
        self.status_character_label.setVisible(False)

        layout.addWidget(banner_card)

        # 核心控制卡片
        ctrl_card = FluentCard("核心调度控制", "一键执行全部已启用的任务流程")
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(10)

        self.start_button = QPushButton("▶  开始执行全部任务")
        self.start_button.setFixedHeight(42)
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setStyleSheet("""
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                font-size: 13.5px;
                font-weight: 700;
                border: 1px solid #3B82F6;
                border-radius: 8px;
                padding: 0 18px;
            }
            QPushButton:hover {
                background-color: #1D4ED8;
                border-color: #60A5FA;
            }
            QPushButton:disabled {
                background-color: rgba(37, 99, 235, 100);
                color: rgba(255, 255, 255, 140);
                border-color: rgba(59, 130, 246, 80);
            }
        """)

        self.resume_button = QPushButton("▶ 继续")
        self.pause_button = QPushButton("❚❚ 暂停")
        self.stop_button = QPushButton("■ 停止 (F8)")
        self.emergency_stop_button = QPushButton("▲ 紧急停止")
        self.cancel_shutdown_button = QPushButton("✕ 取消关机")

        for b in [self.resume_button, self.pause_button, self.stop_button, self.emergency_stop_button, self.cancel_shutdown_button]:
            b.setFixedHeight(42)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("""
                QPushButton {
                    background-color: rgba(38, 48, 64, 200);
                    color: #F1F5F9;
                    border: 1px solid rgba(80, 100, 130, 140);
                    border-radius: 8px;
                    padding: 0 12px;
                    font-size: 12.5px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: rgba(56, 70, 96, 240);
                    border-color: rgba(100, 130, 175, 180);
                    color: #FFFFFF;
                }
                QPushButton:disabled {
                    background-color: rgba(30, 36, 48, 120);
                    color: #64748B;
                    border-color: rgba(60, 72, 90, 80);
                }
            """)

        self.resume_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.emergency_stop_button.setEnabled(False)

        ctrl_layout.addWidget(self.start_button, stretch=3)
        ctrl_layout.addWidget(self.resume_button)
        ctrl_layout.addWidget(self.pause_button)
        ctrl_layout.addWidget(self.stop_button)
        ctrl_layout.addWidget(self.emergency_stop_button)
        ctrl_layout.addWidget(self.cancel_shutdown_button)
        ctrl_card.main_layout.addLayout(ctrl_layout)
        layout.addWidget(ctrl_card)

        # 状态仪表盘卡片
        status_card = FluentCard("调度运行仪表盘", "实时查看任务队列执行进度与耗时")
        status_h = QHBoxLayout()
        status_h.setSpacing(14)

        # 状态指示灯
        self.status_value_label = QLabel("● 空闲待命")
        self.status_value_label.setStyleSheet("""
            color: #38BDF8;
            background: rgba(56, 189, 248, 30);
            border: 1px solid rgba(56, 189, 248, 100);
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 13.5px;
            font-weight: bold;
        """)
        self.status_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 进度与当前任务
        info_v = QVBoxLayout()
        info_v.setSpacing(4)
        self.task_value_label = QLabel("当前运行：暂无运行任务")
        self.task_value_label.setStyleSheet("font-size: 13.5px; font-weight: bold; color: #F1F5F9; background: transparent;")
        self.progress_value_label = QLabel("进度：0 / 0 任务 (0%)")
        self.progress_value_label.setStyleSheet("font-size: 12.5px; color: #94A3B8; background: transparent;")
        info_v.addWidget(self.task_value_label)
        info_v.addWidget(self.progress_value_label)

        # 秒表 (纯单色数字计时)
        self.elapsed_value_label = QLabel("00:00")
        self.elapsed_value_label.setStyleSheet("""
            color: #10B981;
            background: rgba(16, 185, 129, 30);
            border: 1px solid rgba(16, 185, 129, 100);
            border-radius: 8px;
            padding: 8px 16px;
            font-family: 'Cascadia Code', Consolas, monospace;
            font-size: 15px;
            font-weight: bold;
        """)
        self.elapsed_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        status_h.addWidget(self.status_value_label)
        status_h.addLayout(info_v, stretch=1)
        status_h.addWidget(self.elapsed_value_label)
        status_card.main_layout.addLayout(status_h)
        layout.addWidget(status_card)

        # 快捷管理跳转卡片
        quick_card = FluentCard("已配置任务速览", "前往【任务调度】页面可自由增删、调序与配置具体参数")
        quick_layout = QHBoxLayout()
        self.dashboard_task_summary_label = QLabel("当前已就绪 0 个调度任务")
        self.dashboard_task_summary_label.setStyleSheet("color: #E2E8F0; font-size: 13px; background: transparent;")
        btn_goto_tasks = QPushButton("前往任务调度 一条龙 ➔")
        btn_goto_tasks.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_goto_tasks.setStyleSheet("""
            QPushButton {
                background-color: rgba(59, 130, 246, 35);
                color: #60A5FA;
                border: 1px solid #3B82F6;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(59, 130, 246, 70);
                color: #FFFFFF;
            }
        """)
        btn_goto_tasks.clicked.connect(lambda: self._switch_page(1))
        quick_layout.addWidget(self.dashboard_task_summary_label, stretch=1)
        quick_layout.addWidget(btn_goto_tasks)
        quick_card.main_layout.addLayout(quick_layout)
        layout.addWidget(quick_card)

        self.stacked_widget.addWidget(page)

    def _build_tasks_page(self) -> None:
        """页面 1：任务调度 (一条龙 Master-Detail) - 复刻 BetterGI 截图 1。"""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        # ====== 左侧 Master 任务列表栏 (宽 320px) ======
        master_frame = QFrame()
        master_frame.setObjectName("MasterPanel")
        master_frame.setFixedWidth(320)
        master_frame.setStyleSheet("""
            QFrame#MasterPanel {
                background-color: rgba(26, 30, 40, 230);
                border: 1px solid rgba(60, 72, 92, 140);
                border-radius: 10px;
            }
        """)
        master_layout = QVBoxLayout(master_frame)
        master_layout.setContentsMargins(12, 12, 12, 12)
        master_layout.setSpacing(10)

        # Master 顶栏操作区
        master_header = QHBoxLayout()
        master_title = QLabel("任务列表")
        master_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #FFFFFF; background: transparent; border: none;")
        master_header.addWidget(master_title)
        master_header.addStretch(1)

        # 简洁清爽的添加按钮与一键启动按钮
        self.add_button = QPushButton("＋ 添加")
        self.add_button.setFixedHeight(28)
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(48, 56, 70, 220);
                color: #F1F5F9;
                border: 1px solid rgba(80, 95, 120, 160);
                border-radius: 6px;
                padding: 0 10px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: rgba(65, 75, 95, 240);
                color: #FFFFFF;
            }
        """)

        self.tasks_page_start_button = QPushButton("▶ 启动")
        self.tasks_page_start_button.setFixedHeight(28)
        self.tasks_page_start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tasks_page_start_button.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3B82F6, stop:1 #2563EB);
                color: #FFFFFF;
                border: 1px solid #60A5FA;
                border-radius: 6px;
                padding: 0 12px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #60A5FA, stop:1 #3B82F6);
            }
        """)
        self.tasks_page_start_button.clicked.connect(self.start_tasks)

        # 兼容旧引用
        self.delete_button = QPushButton()
        self.delete_button.setVisible(False)
        self.up_button = QPushButton()
        self.up_button.setVisible(False)
        self.down_button = QPushButton()
        self.down_button.setVisible(False)

        master_header.addWidget(self.add_button)
        master_header.addWidget(self.tasks_page_start_button)
        master_layout.addLayout(master_header)

        # Master 拖拽任务列表区 (支持直接鼠标上下拖动调整优先级)
        self.task_list_widget = DraggableTaskListWidget()
        self.task_list_widget.order_changed.connect(self._on_task_order_dragged)
        master_layout.addWidget(self.task_list_widget, stretch=1)

        layout.addWidget(master_frame)

        # ====== 右侧 Detail 任务配置卡片群 (滚动区域) ======
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        detail_container = QWidget()
        detail_container.setStyleSheet("background: transparent;")
        detail_layout = QVBoxLayout(detail_container)
        detail_layout.setContentsMargins(0, 0, 4, 0)
        detail_layout.setSpacing(12)

        # 顶部配置预设管理栏
        preset_card = FluentCard()
        preset_layout = QHBoxLayout()
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(8)

        preset_label = QLabel("配置方案")
        preset_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #60A5FA; background: transparent; border: none;")
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("默认配置")
        self.preset_combo.setFixedHeight(34)
        self.preset_combo.setStyleSheet("""
            QComboBox {
                background-color: rgba(30, 36, 48, 240);
                color: #FFFFFF;
                border: 1px solid rgba(80, 95, 120, 160);
                border-radius: 6px;
                padding: 4px 10px;
                font-weight: 600;
            }
        """)

        self.file_button = QPushButton("📁 导入 / 导出 ▼")
        self.file_button.setFixedHeight(34)
        self.file_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.file_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(48, 56, 70, 220);
                color: #F1F5F9;
                border: 1px solid rgba(80, 95, 120, 140);
                border-radius: 6px;
                padding: 0 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(65, 75, 95, 240);
                color: #FFFFFF;
            }
        """)

        self.save_button = QPushButton("💾 保存当前配置")
        self.save_button.setFixedHeight(34)
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(59, 130, 246, 35);
                color: #60A5FA;
                border: 1px solid #3B82F6;
                border-radius: 6px;
                padding: 0 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3B82F6;
                color: #FFFFFF;
            }
        """)

        self.export_config_button = QPushButton("导出配置")
        self.import_config_button = QPushButton("导入配置")

        preset_layout.addWidget(preset_label)
        preset_layout.addWidget(self.preset_combo, stretch=1)
        preset_layout.addWidget(self.file_button)
        preset_layout.addWidget(self.save_button)
        preset_card.main_layout.addLayout(preset_layout)
        detail_layout.addWidget(preset_card)

        # 辅助创建高对比度表单标签
        def make_lbl(t: str) -> QLabel:
            lbl = QLabel(t)
            lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: #F1F5F9; background: transparent; border: none;")
            return lbl

        # Detail 卡片 1：基础参数配置
        basic_card = FluentCard("基础运行参数", "设置脚本名称、运行路径与最大执行时间")
        basic_form = QVBoxLayout()
        basic_form.setSpacing(10)

        # 任务名称
        row_name = QHBoxLayout()
        row_name.addWidget(make_lbl("任务名称："))
        self.detail_name_edit = QLineEdit()
        self.detail_name_edit.setPlaceholderText("例如: 自动一条龙 / 每日委托")
        row_name.addWidget(self.detail_name_edit, stretch=1)
        basic_form.addLayout(row_name)

        # 脚本路径
        row_path = QHBoxLayout()
        row_path.addWidget(make_lbl("脚本路径："))
        self.detail_path_edit = QLineEdit()
        self.detail_path_edit.setPlaceholderText("可执行文件 / Python 脚本 / 启动器路径")
        self.detail_browse_btn = QPushButton("📁 浏览文件...")
        self.detail_browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.detail_browse_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(48, 56, 70, 220);
                color: #E2E8F0;
                border: 1px solid rgba(80, 95, 120, 140);
                border-radius: 6px;
                padding: 4px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(65, 75, 95, 240);
                color: #FFFFFF;
            }
        """)
        row_path.addWidget(self.detail_path_edit, stretch=1)
        row_path.addWidget(self.detail_browse_btn)
        basic_form.addLayout(row_path)

        # 启动参数
        row_args = QHBoxLayout()
        row_args.addWidget(make_lbl("启用命令/参数："))
        self.detail_args_switch = SwitchButton()
        row_args.addWidget(self.detail_args_switch)
        row_args.addSpacing(10)
        row_args.addWidget(make_lbl("命令行参数："))
        self.detail_args_edit = QLineEdit()
        self.detail_args_edit.setPlaceholderText("启动参数，例如: --auto --task daily")
        row_args.addWidget(self.detail_args_edit, stretch=1)
        basic_form.addLayout(row_args)

        # 执行顺序与超时时间
        row_order = QHBoxLayout()
        row_order.addWidget(make_lbl("运行顺序："))
        self.detail_order_spin = QSpinBox()
        self.detail_order_spin.setRange(1, 999)
        self.detail_order_spin.setFixedWidth(80)
        row_order.addWidget(self.detail_order_spin)
        row_order.addSpacing(20)
        row_order.addWidget(make_lbl("最大运行时间："))
        self.detail_timeout_spin = QSpinBox()
        self.detail_timeout_spin.setRange(0, 9999)
        self.detail_timeout_spin.setSuffix(" 分钟")
        self.detail_timeout_spin.setValue(30)
        self.detail_timeout_spin.setFixedWidth(110)
        row_order.addWidget(self.detail_timeout_spin)
        row_order.addStretch(1)
        basic_form.addLayout(row_order)

        basic_card.main_layout.addLayout(basic_form)
        detail_layout.addWidget(basic_card)

        # Detail 卡片 2：超时与调度策略
        timeout_card = FluentCard("超时与调度策略", "当脚本超出最大执行时间时的处理方案")
        timeout_form = QVBoxLayout()
        timeout_form.setSpacing(10)

        row_to = QHBoxLayout()
        row_to.addWidget(make_lbl("超时处理方式："))
        self.detail_action_combo = QComboBox()
        for k, v in TIMEOUT_ACTIONS.items():
            self.detail_action_combo.addItem(v, k)
        row_to.addWidget(self.detail_action_combo, stretch=1)
        timeout_form.addLayout(row_to)

        row_enter = QHBoxLayout()
        row_enter.addWidget(make_lbl("自动按 Enter 确认延时："))
        self.detail_enter_delay_spin = QSpinBox()
        self.detail_enter_delay_spin.setRange(0, 3600)
        self.detail_enter_delay_spin.setSuffix(" 秒 (0为不自动按)")
        row_enter.addWidget(self.detail_enter_delay_spin, stretch=1)
        timeout_form.addLayout(row_enter)

        timeout_card.main_layout.addLayout(timeout_form)
        detail_layout.addWidget(timeout_card)

        # Detail 卡片 3：完成判断与等待策略
        wait_card = FluentCard("完成判断与等待策略", "判断本任务是否已经顺利执行完毕")
        wait_form = QVBoxLayout()
        wait_form.setSpacing(10)

        row_wm = QHBoxLayout()
        row_wm.addWidget(make_lbl("完成判断方式："))
        self.detail_wait_mode_combo = QComboBox()
        for k, v in WAIT_MODES.items():
            self.detail_wait_mode_combo.addItem(v, k)
        row_wm.addWidget(self.detail_wait_mode_combo, stretch=1)
        wait_form.addLayout(row_wm)

        row_wp = QHBoxLayout()
        row_wp.addWidget(make_lbl("等待关键词/进程名："))
        self.detail_wait_process_edit = QLineEdit()
        self.detail_wait_process_edit.setPlaceholderText("用于进程出现/消失判断，例如: Endfield.exe")
        row_wp.addWidget(self.detail_wait_process_edit, stretch=1)
        wait_form.addLayout(row_wp)

        wait_card.main_layout.addLayout(wait_form)
        detail_layout.addWidget(wait_card)

        # Detail 卡片 4：进阶与守护策略
        adv_card = FluentCard("进阶与守护策略", "多任务并发、外部进程守护与窗口清理")
        adv_form = QVBoxLayout()
        adv_form.setSpacing(10)

        row_wd = QHBoxLayout()
        row_wd.addWidget(make_lbl("启用守护监控："))
        self.detail_watchdog_switch = SwitchButton()
        row_wd.addWidget(self.detail_watchdog_switch)
        row_wd.addStretch(1)
        adv_form.addLayout(row_wd)

        row_cg = QHBoxLayout()
        row_cg.addWidget(make_lbl("并发组名："))
        self.detail_concurrent_group_edit = QLineEdit()
        self.detail_concurrent_group_edit.setPlaceholderText("相同组名任务将并发启动")
        row_cg.addWidget(self.detail_concurrent_group_edit, stretch=1)
        row_cg.addSpacing(10)
        row_cg.addWidget(make_lbl("组完成策略："))
        self.detail_concurrent_policy_combo = QComboBox()
        for k, v in CONCURRENT_POLICIES.items():
            self.detail_concurrent_policy_combo.addItem(v, k)
        row_cg.addWidget(self.detail_concurrent_policy_combo, stretch=1)
        adv_form.addLayout(row_cg)

        row_pk = QHBoxLayout()
        row_pk.addWidget(make_lbl("目标进程关键词："))
        self.detail_process_keywords_edit = QLineEdit()
        self.detail_process_keywords_edit.setPlaceholderText("多个用逗号隔开，例如: Endfield.exe, OK-WW")
        row_pk.addWidget(self.detail_process_keywords_edit, stretch=1)
        adv_form.addLayout(row_pk)

        row_wk = QHBoxLayout()
        row_wk.addWidget(make_lbl("游戏窗口关键词："))
        self.detail_window_keywords_edit = QLineEdit()
        self.detail_window_keywords_edit.setPlaceholderText("多个用逗号隔开，例如: 鸣潮, 星穹铁道")
        row_wk.addWidget(self.detail_window_keywords_edit, stretch=1)
        adv_form.addLayout(row_wk)

        # 底部删除任务按钮
        del_task_btn_row = QHBoxLayout()
        del_task_btn_row.addStretch(1)
        self.card_delete_task_button = QPushButton("🗑  删除此任务")
        self.card_delete_task_button.setFixedHeight(32)
        self.card_delete_task_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.card_delete_task_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(220, 38, 38, 25);
                color: #F87171;
                border: 1px solid rgba(239, 68, 68, 100);
                border-radius: 6px;
                padding: 0 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(220, 38, 38, 70);
                color: #FFFFFF;
            }
        """)
        self.card_delete_task_button.clicked.connect(self.delete_selected_row)
        del_task_btn_row.addWidget(self.card_delete_task_button)
        adv_form.addLayout(del_task_btn_row)

        adv_card.main_layout.addLayout(adv_form)
        detail_layout.addWidget(adv_card)

        detail_layout.addStretch(1)
        detail_scroll.setWidget(detail_container)
        layout.addWidget(detail_scroll, stretch=1)

        # 兼容旧代码引用 table
        self.table = QTableWidget(0, 18)
        self.table.setVisible(False)

        # 连接 Detail 表单输入即时双向保存
        self.detail_name_edit.textEdited.connect(self._sync_current_detail_to_task)
        self.detail_path_edit.textEdited.connect(self._sync_current_detail_to_task)
        self.detail_args_edit.textEdited.connect(self._sync_current_detail_to_task)
        self.detail_args_switch.toggled.connect(lambda _c: self._sync_current_detail_to_task())
        self.detail_order_spin.valueChanged.connect(lambda _v: self._sync_current_detail_to_task())
        self.detail_timeout_spin.valueChanged.connect(lambda _v: self._sync_current_detail_to_task())
        self.detail_action_combo.currentIndexChanged.connect(lambda _i: self._sync_current_detail_to_task())
        self.detail_enter_delay_spin.valueChanged.connect(lambda _v: self._sync_current_detail_to_task())
        self.detail_wait_mode_combo.currentIndexChanged.connect(lambda _i: self._sync_current_detail_to_task())
        self.detail_wait_process_edit.textEdited.connect(self._sync_current_detail_to_task)
        self.detail_watchdog_switch.toggled.connect(lambda _c: self._sync_current_detail_to_task())
        self.detail_concurrent_group_edit.textEdited.connect(self._sync_current_detail_to_task)
        self.detail_concurrent_policy_combo.currentIndexChanged.connect(lambda _i: self._sync_current_detail_to_task())
        self.detail_process_keywords_edit.textEdited.connect(self._sync_current_detail_to_task)
        self.detail_window_keywords_edit.textEdited.connect(self._sync_current_detail_to_task)
        self.detail_browse_btn.clicked.connect(self._on_detail_browse_file)

        self.stacked_widget.addWidget(page)

    def _build_stats_page(self) -> None:
        """页面 2：运行统计 (Stats)。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        card = FluentCard("运行耗时统计", "记录各项脚本的执行次数、平均耗时与最近完成时间")
        btn_row = QHBoxLayout()
        self.refresh_stats_button = QPushButton("刷新统计")
        self.open_stats_button = QPushButton("打开统计目录")
        for b in [self.refresh_stats_button, self.open_stats_button]:
            b.setFixedHeight(34)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("""
                QPushButton {
                    background-color: rgba(48, 56, 70, 220);
                    color: #E2E8F0;
                    border: 1px solid rgba(80, 95, 120, 140);
                    border-radius: 6px;
                    padding: 0 14px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: rgba(65, 75, 95, 240);
                    color: #FFFFFF;
                }
            """)
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        card.main_layout.addLayout(btn_row)

        self.stats_table = QTableWidget(0, 5)
        self.stats_table.setHorizontalHeaderLabels(["脚本名称", "次数", "平均耗时", "最近耗时", "最近完成时间"])
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stats_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.stats_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.stats_table.setAlternatingRowColors(True)
        stats_header = self.stats_table.horizontalHeader()
        stats_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 5):
            stats_header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        card.main_layout.addWidget(self.stats_table)

        layout.addWidget(card)
        self.stacked_widget.addWidget(page)

    def _build_logs_page(self) -> None:
        """页面 3：运行日志 (Logs)。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        card = FluentCard("运行日志终端", "实时输出脚本运行控制台日志")
        btn_row = QHBoxLayout()
        self.clear_log_button = QPushButton("清空日志")
        self.copy_log_button = QPushButton("复制全部")
        self.open_log_dir_button = QPushButton("打开日志目录")
        for b in [self.clear_log_button, self.copy_log_button, self.open_log_dir_button]:
            b.setFixedHeight(34)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("""
                QPushButton {
                    background-color: rgba(48, 56, 70, 220);
                    color: #E2E8F0;
                    border: 1px solid rgba(80, 95, 120, 140);
                    border-radius: 6px;
                    padding: 0 14px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: rgba(65, 75, 95, 240);
                    color: #FFFFFF;
                }
            """)
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        card.main_layout.addLayout(btn_row)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.log_edit.setStyleSheet("""
            QTextEdit {
                background-color: rgba(18, 21, 28, 240);
                color: #E2E8F0;
                border: 1px solid rgba(50, 60, 80, 160);
                border-radius: 8px;
                font-family: 'Cascadia Code', Consolas, 'Segoe UI Mono', monospace;
                font-size: 12.5px;
                padding: 10px;
            }
        """)
        card.main_layout.addWidget(self.log_edit)

        layout.addWidget(card)
        self.stacked_widget.addWidget(page)

    def _build_settings_page(self) -> None:
        """页面 4：全局设置 (Settings) - 现代 Fluent Toggle Switch 切换开关风格。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

        def make_switch_row(title: str, desc: str, switch_btn: SwitchButton) -> QWidget:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(8, 6, 8, 6)
            h.setSpacing(12)

            v = QVBoxLayout()
            v.setSpacing(2)
            lbl_title = QLabel(title)
            lbl_title.setStyleSheet("font-size: 13.5px; font-weight: 600; color: #F1F5F9; background: transparent; border: none;")
            v.addWidget(lbl_title)
            if desc:
                lbl_desc = QLabel(desc)
                lbl_desc.setStyleSheet("font-size: 12px; color: #94A3B8; background: transparent; border: none;")
                v.addWidget(lbl_desc)

            h.addLayout(v, stretch=1)
            h.addWidget(switch_btn)
            return w

        # 卡片 1：自动化与启动策略
        card1 = FluentCard("自动化与启动策略", "配置系统开机自启与自动执行选项")
        f1 = QVBoxLayout()
        f1.setSpacing(8)

        self.windows_startup_checkbox = SwitchButton()
        self.auto_start_tasks_checkbox = SwitchButton()
        self.timeout_screenshot_checkbox = SwitchButton()

        f1.addWidget(make_switch_row("Windows 开机自启动本程序", "系统登录时自动在后台启动本程序", self.windows_startup_checkbox))
        f1.addWidget(make_switch_row("程序启动后自动开始执行任务", "软件打开后无需手动点击，自动进入任务调度流程", self.auto_start_tasks_checkbox))
        f1.addWidget(make_switch_row("超时处理时自动保存前后屏幕截图", "当任务发生超时异常时，自动截屏存入日志目录便于排查", self.timeout_screenshot_checkbox))
        card1.main_layout.addLayout(f1)
        layout.addWidget(card1)

        # 卡片 2：关机与退出策略
        card2 = FluentCard("关机与退出策略", "所有任务执行完毕后的系统行为")
        f2 = QVBoxLayout()
        f2.setSpacing(8)

        self.shutdown_checkbox = SwitchButton()
        self.auto_exit_checkbox = SwitchButton()

        f2.addWidget(make_switch_row("所有任务完成后自动关机", "全部任务执行完毕后触发安全关机倒计时", self.shutdown_checkbox))

        # 关机倒计时行
        r_shut = QHBoxLayout()
        r_shut.setContentsMargins(8, 2, 8, 2)
        lbl_delay = QLabel("关机倒计时：")
        lbl_delay.setStyleSheet("font-size: 13.5px; font-weight: 600; color: #F1F5F9; background: transparent; border: none;")
        r_shut.addWidget(lbl_delay)
        self.shutdown_delay_spin = QSpinBox()
        self.shutdown_delay_spin.setRange(0, 24 * 3600)
        self.shutdown_delay_spin.setSuffix(" 秒")
        self.shutdown_delay_spin.setSingleStep(10)
        self.shutdown_delay_spin.setFixedWidth(110)
        r_shut.addWidget(self.shutdown_delay_spin)
        r_shut.addStretch(1)
        f2.addLayout(r_shut)

        f2.addWidget(make_switch_row("所有任务完成后自动退出程序", "全部调度任务顺利结束后自动关闭本程序窗口", self.auto_exit_checkbox))
        card2.main_layout.addLayout(f2)
        layout.addWidget(card2)

        # 卡片 3：界面主题与外观
        card3 = FluentCard("界面主题与外观", "个性化切换主界面主题色彩")
        f3 = QHBoxLayout()
        f3.setContentsMargins(8, 6, 8, 6)
        lbl_theme = QLabel("当前主题：")
        lbl_theme.setStyleSheet("font-size: 13.5px; font-weight: 600; color: #F1F5F9; background: transparent; border: none;")
        f3.addWidget(lbl_theme)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("经典风 (推荐)", "jiansz")
        self.theme_combo.addItem("深色模式", "dark")
        self.theme_combo.addItem("浅色模式", "light")
        self.theme_combo.addItem("校园风", "campus")
        self.theme_combo.addItem("清新风", "fresh")
        self.theme_combo.addItem("暖色幻想风", "fantasy")
        self.theme_label = QLabel("当前主题：-")
        self.theme_label.setVisible(False)
        f3.addWidget(self.theme_combo)
        f3.addStretch(1)
        card3.main_layout.addLayout(f3)
        layout.addWidget(card3)

        layout.addStretch(1)
        self.stacked_widget.addWidget(page)

    def _render_master_task_list(self) -> None:
        """根据 self.tasks_list 动态渲染左侧 Master 拖拽任务卡片列表。"""
        self.task_list_widget.blockSignals(True)
        self.task_list_widget.clear()
        self._master_task_widgets.clear()

        for idx, task in enumerate(self.tasks_list):
            item = QListWidgetItem(self.task_list_widget)
            item.setSizeHint(QSize(0, 48))
            item.setData(Qt.ItemDataRole.UserRole, idx)

            item_widget = MasterTaskItemWidget(idx, task.name, task.enabled, self.task_list_widget)
            item_widget.clicked.connect(self._on_master_task_clicked)
            item_widget.toggled.connect(self._on_master_task_toggled)

            self.task_list_widget.addItem(item)
            self.task_list_widget.setItemWidget(item, item_widget)
            self._master_task_widgets.append(item_widget)

        self.task_list_widget.blockSignals(False)

        if self.tasks_list:
            idx = max(0, min(self.current_task_idx, len(self.tasks_list) - 1))
            self._select_master_task(idx, load_detail=True)
        else:
            self._clear_detail_form()

        # 更新 Dashboard 上的速览数字
        enabled_count = sum(1 for t in self.tasks_list if t.enabled)
        self.dashboard_task_summary_label.setText(f"当前已配置 {len(self.tasks_list)} 个任务，其中已启用 {enabled_count} 个")

    def _on_task_order_dragged(self) -> None:
        """用户鼠标拖拽放下后，重新排列表格与数据优先级。"""
        new_tasks: List[TaskConfig] = []
        for i in range(self.task_list_widget.count()):
            item = self.task_list_widget.item(i)
            widget = self.task_list_widget.itemWidget(item)
            if isinstance(widget, MasterTaskItemWidget):
                orig_idx = widget.task_index
                if 0 <= orig_idx < len(self.tasks_list):
                    new_tasks.append(self.tasks_list[orig_idx])

        if len(new_tasks) == len(self.tasks_list):
            self.tasks_list = new_tasks
            self._normalize_orders(self.tasks_list)
            self._render_master_task_list()
            self.save_config_from_ui()

    def _select_master_task(self, index: int, load_detail: bool = True) -> None:
        """选中左侧任务项。"""
        self.current_task_idx = index
        for idx, w in enumerate(self._master_task_widgets):
            w.set_selected(idx == index)
        if 0 <= index < self.task_list_widget.count():
            self.task_list_widget.setCurrentRow(index)
        if load_detail and 0 <= index < len(self.tasks_list):
            self._load_task_detail_to_form(self.tasks_list[index])

    def _on_master_task_clicked(self, index: int) -> None:
        """点击左侧任务项切换右侧 Detail。"""
        if 0 <= index < len(self.tasks_list):
            self._sync_current_detail_to_task()
            self._select_master_task(index, load_detail=True)

    def _on_master_task_toggled(self, index: int, enabled: bool) -> None:
        """点击任务 Switch 开关实时生效。"""
        if 0 <= index < len(self.tasks_list):
            self.tasks_list[index].enabled = enabled
            self.save_config_from_ui()
            enabled_count = sum(1 for t in self.tasks_list if t.enabled)
            self.dashboard_task_summary_label.setText(f"当前已配置 {len(self.tasks_list)} 个任务，其中已启用 {enabled_count} 个")

    def _load_task_detail_to_form(self, task: TaskConfig) -> None:
        """将选中的 TaskConfig 属性填充至右侧表单。"""
        self.detail_name_edit.blockSignals(True)
        self.detail_path_edit.blockSignals(True)
        self.detail_args_edit.blockSignals(True)
        self.detail_args_switch.blockSignals(True)
        self.detail_order_spin.blockSignals(True)
        self.detail_timeout_spin.blockSignals(True)
        self.detail_action_combo.blockSignals(True)
        self.detail_enter_delay_spin.blockSignals(True)
        self.detail_wait_mode_combo.blockSignals(True)
        self.detail_wait_process_edit.blockSignals(True)
        self.detail_watchdog_switch.blockSignals(True)
        self.detail_concurrent_group_edit.blockSignals(True)
        self.detail_concurrent_policy_combo.blockSignals(True)
        self.detail_process_keywords_edit.blockSignals(True)
        self.detail_window_keywords_edit.blockSignals(True)

        self.detail_name_edit.setText(task.name)
        self.detail_path_edit.setText(task.script_path)
        self.detail_args_edit.setText(task.args)
        self.detail_args_switch.setChecked(task.use_args, animate=False)
        self.detail_order_spin.setValue(task.order)
        self.detail_timeout_spin.setValue(task.timeout_minutes)

        idx = self.detail_action_combo.findData(task.timeout_action)
        if idx >= 0:
            self.detail_action_combo.setCurrentIndex(idx)

        self.detail_enter_delay_spin.setValue(task.confirm_enter_delay_seconds)

        w_idx = self.detail_wait_mode_combo.findData(task.wait_mode)
        if w_idx >= 0:
            self.detail_wait_mode_combo.setCurrentIndex(w_idx)

        self.detail_wait_process_edit.setText(task.wait_process_name)
        self.detail_watchdog_switch.setChecked(task.enable_watchdog, animate=False)
        self.detail_concurrent_group_edit.setText(task.concurrent_group)

        cp_idx = self.detail_concurrent_policy_combo.findData(task.concurrent_policy)
        if cp_idx >= 0:
            self.detail_concurrent_policy_combo.setCurrentIndex(cp_idx)

        self.detail_process_keywords_edit.setText(", ".join(task.process_keywords))
        self.detail_window_keywords_edit.setText(", ".join(task.window_keywords))

        self.detail_name_edit.blockSignals(False)
        self.detail_path_edit.blockSignals(False)
        self.detail_args_edit.blockSignals(False)
        self.detail_args_switch.blockSignals(False)
        self.detail_order_spin.blockSignals(False)
        self.detail_timeout_spin.blockSignals(False)
        self.detail_action_combo.blockSignals(False)
        self.detail_enter_delay_spin.blockSignals(False)
        self.detail_wait_mode_combo.blockSignals(False)
        self.detail_wait_process_edit.blockSignals(False)
        self.detail_watchdog_switch.blockSignals(False)
        self.detail_concurrent_group_edit.blockSignals(False)
        self.detail_concurrent_policy_combo.blockSignals(False)
        self.detail_process_keywords_edit.blockSignals(False)
        self.detail_window_keywords_edit.blockSignals(False)

    def _sync_current_detail_to_task(self) -> None:
        """从右侧表单实时同步属性回当前 TaskConfig。"""
        if not (0 <= self.current_task_idx < len(self.tasks_list)):
            return
        task = self.tasks_list[self.current_task_idx]
        task.name = self.detail_name_edit.text().strip() or f"任务{self.current_task_idx + 1}"
        task.script_path = self.detail_path_edit.text().strip()
        task.args = self.detail_args_edit.text().strip()
        task.use_args = self.detail_args_switch.isChecked()
        task.order = self.detail_order_spin.value()
        task.timeout_minutes = self.detail_timeout_spin.value()
        task.timeout_action = str(self.detail_action_combo.currentData() or "kill_and_continue")
        task.confirm_enter_delay_seconds = self.detail_enter_delay_spin.value()
        task.wait_mode = str(self.detail_wait_mode_combo.currentData() or "direct_process")
        task.wait_process_name = self.detail_wait_process_edit.text().strip()
        task.enable_watchdog = self.detail_watchdog_switch.isChecked()
        task.concurrent_group = self.detail_concurrent_group_edit.text().strip()
        task.concurrent_policy = str(self.detail_concurrent_policy_combo.currentData() or "wait_all")
        task.process_keywords = normalize_keywords(self.detail_process_keywords_edit.text())
        task.window_keywords = normalize_keywords(self.detail_window_keywords_edit.text())

        # 更新左侧对应项的文字与状态
        if self.current_task_idx < len(self._master_task_widgets):
            self._master_task_widgets[self.current_task_idx].update_info(self.current_task_idx, task.name, task.enabled)

    def _clear_detail_form(self) -> None:
        """清空右侧表单。"""
        self.detail_name_edit.clear()
        self.detail_path_edit.clear()
        self.detail_args_edit.clear()
        self.detail_args_switch.setChecked(False)
        self.detail_order_spin.setValue(1)
        self.detail_timeout_spin.setValue(30)
        self.detail_action_combo.setCurrentIndex(0)
        self.detail_enter_delay_spin.setValue(0)
        self.detail_wait_mode_combo.setCurrentIndex(0)
        self.detail_wait_process_edit.clear()
        self.detail_watchdog_switch.setChecked(False)
        self.detail_concurrent_group_edit.clear()
        self.detail_concurrent_policy_combo.setCurrentIndex(0)
        self.detail_process_keywords_edit.clear()
        self.detail_window_keywords_edit.clear()

    def _on_detail_browse_file(self) -> None:
        """右侧表单选择脚本路径。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择脚本或程序",
            str(Path.home()),
            "可执行文件/脚本 (*.exe *.bat *.cmd *.ps1 *.py *.lnk *.vbs);;所有文件 (*.*)",
        )
        if path:
            self.detail_path_edit.setText(path)
            self._sync_current_detail_to_task()

    def _status_character_image_candidates(self, state: str) -> List[Path]:
        """返回某个状态可用的简小松素材。"""
        status_character_dir = self.base_dir / "assets" / "status_character"
        state_aliases = {
            "idle": ["idle"],
            "rest": ["rest", "pause", "paused", "done", "finish", "finished"],
            "error": ["error", "err", "exception", "warning"],
            "work": ["work", "working", "run", "running", "gaming"],
        }
        aliases = state_aliases.get(state, [state])
        result: List[Path] = []
        for alias in aliases:
            folder = status_character_dir / alias
            if folder.exists():
                for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
                    result.extend(sorted(folder.glob(ext)))
            for ext in ("png", "jpg", "jpeg", "webp"):
                result.extend(sorted(status_character_dir.glob(f"{alias}_*.{ext}")))
                result.extend(sorted(status_character_dir.glob(f"{alias}*.{ext}")))
                single = status_character_dir / f"{alias}.{ext}"
                if single.exists():
                    result.append(single)
        seen = set()
        unique: List[Path] = []
        for path in result:
            key = str(path.resolve())
            if key not in seen and path.exists():
                seen.add(key)
                unique.append(path)
        return unique

    def _load_status_character_image(self, state: str, random_pick: bool = True) -> None:
        candidates = self._status_character_image_candidates(state)
        if not candidates and state != "idle":
            candidates = self._status_character_image_candidates("idle")
        if candidates:
            path = random.choice(candidates) if random_pick else candidates[0]
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                target_w = max(280, self.status_character_label.width())
                target_h = max(380, self.status_character_label.height())
                self.status_character_label.setPixmap(
                    pixmap.scaled(target_w, target_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
                self.status_character_label.setText("")
                return
        self.status_character_label.setPixmap(QPixmap())
        self.status_character_label.setText(
            "简小松素材预留\nassets/status_character/\n\n"
            "推荐：idle×3 / rest×3 / error×2 / work×2\n"
            "例如 assets/status_character/idle/idle_1.png"
        )

    def _default_status_character_text(self, state: str) -> str:
        text_map = {
            "idle": "简小松待命中～",
            "work": random.choice([
                "正在执行中，我会帮你看着的。",
                "任务已经跑起来啦～先别急着关窗口哦。",
                "目前正在运行中，有问题我会提醒你的。",
                "简小松正在确认运行状态。",
            ]),
            "rest": "现在先休息一下，需要时再继续吧。",
            "error": "这次运行好像遇到问题了，我先帮你标出来。",
        }
        return text_map.get(state, text_map["idle"])

    def _priority_for_status_character_state(self, state: str) -> int:
        return {"error": 100, "work": 70, "rest": 50, "guide": 30, "idle": 20}.get(state, 20)

    def _normalize_bubble_text(self, text: str) -> str:
        """统一兜底气泡文案，避免旧长句或说明书式文本撑破气泡。"""
        raw = str(text or "").strip()
        if not raw:
            return "简小松待命中～"
        # 兼容旧版本残留的长引导句：统一替换成短句池。
        if "还未设置配置文件" in raw or "请在任务执行中设置配置" in raw or "左上角文件导入配置" in raw:
            guide_texts = getattr(self, "guide_bubble_texts", None)
            if guide_texts:
                return guide_texts[0]
            return "还没有配置任务哦～"
        # 气泡最多三行；异常详细内容应留在日志区，不直接塞进气泡。
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(lines) > 3:
            raw = "\n".join(lines[:3])
        if len(raw) > 72:
            raw = raw[:69].rstrip("，。,. ") + "……"
        return raw

    def _set_status_character_bubble(self, text: str, *, priority: int, duration_seconds: int = 0, force: bool = False, bubble_kind: str = "normal") -> bool:
        """v31：统一气泡调度入口。低优先级不能覆盖高优先级。

        v31.3：bubble_kind="guide" 时，气泡可点击循环下一条引导短句。
        """
        if not force and priority < getattr(self, "bubble_priority", 0):
            return False
        self.bubble_priority = priority
        self.current_bubble_kind = bubble_kind
        if bubble_kind == "guide":
            self.status_character_text_label.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.status_character_text_label.unsetCursor()
        self.status_character_text_label.setText(self._normalize_bubble_text(text))
        if duration_seconds > 0:
            if self.bubble_restore_timer is None:
                self.bubble_restore_timer = QTimer(self)
                self.bubble_restore_timer.setSingleShot(True)
                self.bubble_restore_timer.timeout.connect(self._restore_status_character_message_after_success)
            self.bubble_restore_timer.start(max(1, int(duration_seconds) * 1000))
        return True

    def set_status_character_state(self, state: str, *, force: bool = False, error_reason: str = "") -> None:
        """切换简小松状态；error 有锁定时间，避免一闪而过。"""
        valid_states = {"idle", "rest", "error", "work"}
        if state not in valid_states:
            state = "idle"

        now_ms = QDateTime.currentMSecsSinceEpoch()
        if self.status_character_state == "error" and state != "error" and not force:
            if now_ms < self.status_character_error_locked_until_ms:
                return

        if state == "error":
            self.status_character_error_count += 1
            lock_seconds = self.status_character_error_count * 15
            self.status_character_error_locked_until_ms = now_ms + lock_seconds * 1000
            if self.status_character_state != "error":
                self.status_character_last_non_error_state = self.status_character_state
            self.status_character_state = "error"
            self._load_status_character_image("error", random_pick=True)
            self._set_status_character_bubble(error_reason or self._default_status_character_text("error"), priority=100, force=True)
            self._schedule_status_character_error_release(lock_seconds)
            return

        if self.status_character_state == "error" and force:
            self.status_character_error_count = 0
            self.status_character_error_locked_until_ms = 0

        if self.status_character_state == state and not force:
            return
        self.status_character_state = state
        self.status_character_last_non_error_state = state
        self.bubble_priority = self._priority_for_status_character_state(state)
        self._load_status_character_image(state, random_pick=True)
        self._set_status_character_bubble(self._default_status_character_text(state), priority=self.bubble_priority, force=True)

    def show_temporary_status_character_message(self, text: str, duration_seconds: int = 10, priority: int = 30) -> None:
        """显示不会切换图片的临时气泡文案；通过优先级避免互相打断。"""
        if getattr(self, "status_character_state", "idle") == "error":
            return
        self._set_status_character_bubble(text, priority=priority, duration_seconds=duration_seconds)

    def _restore_status_character_message_after_success(self) -> None:
        if getattr(self, "status_character_state", "idle") == "error":
            return
        if self.is_running:
            self.bubble_priority = 70
            self.current_bubble_kind = "work"
            self.status_character_text_label.unsetCursor()
            self.status_character_text_label.setText(self._normalize_bubble_text(self._default_status_character_text("work")))
        else:
            self.bubble_priority = self._priority_for_status_character_state(getattr(self, "status_character_state", "idle"))
            self.current_bubble_kind = getattr(self, "status_character_state", "idle")
            self.status_character_text_label.unsetCursor()
            self.status_character_text_label.setText(self._normalize_bubble_text(self._default_status_character_text(getattr(self, "status_character_state", "idle"))))

    def has_usable_task_config(self) -> bool:
        """判断当前是否已经有可执行的任务配置。

        用于首次使用引导：只要表格/配置里还没有启用且脚本路径有效的任务，
        就认为用户仍需要导入或设置配置。
        """
        try:
            tasks = self.collect_tasks_from_table(sort_by_order=False)
        except Exception:
            tasks = list(getattr(self.config, "tasks", []) or [])
        for task in tasks:
            if not getattr(task, "enabled", True):
                continue
            script_path = str(getattr(task, "script_path", "") or "").strip()
            if script_path:
                return True
        return False

    def show_first_config_guide_if_needed(self, *, force: bool = False) -> None:
        """未导入/未设置配置时，用简小松气泡给出轻量引导。

        guide 的优先级高于普通 idle/卡片说明，低于运行中、完成/暂停和异常。
        这样首次打开且未配置时，用户会先看到导入/配置提示；开始执行后则不会打断运行态。
        """
        if self.is_running or getattr(self, "status_character_state", "idle") == "error":
            return
        if self.has_usable_task_config():
            return
        guide_texts = getattr(self, "guide_bubble_texts", []) or ["还没有配置任务哦～"]
        self.guide_bubble_index = max(0, min(getattr(self, "guide_bubble_index", 0), len(guide_texts) - 1))
        self._set_status_character_bubble(
            guide_texts[self.guide_bubble_index],
            priority=30,
            duration_seconds=12,
            force=force,
            bubble_kind="guide",
        )

    def on_status_character_bubble_clicked(self) -> None:
        """v31.3：引导气泡期间，点击气泡循环播放下一条引导。"""
        if getattr(self, "current_bubble_kind", "") != "guide":
            return
        if self.is_running or getattr(self, "status_character_state", "idle") == "error":
            return
        if self.has_usable_task_config():
            return
        guide_texts = getattr(self, "guide_bubble_texts", []) or ["还没有配置任务哦～"]
        self.guide_bubble_index = (getattr(self, "guide_bubble_index", 0) + 1) % len(guide_texts)
        self._set_status_character_bubble(
            guide_texts[self.guide_bubble_index],
            priority=30,
            duration_seconds=12,
            force=True,
            bubble_kind="guide",
        )

    def _friendly_error_bubble_text(self, message: str) -> str:
        """把日志/异常内容转换成更像桌面助手的气泡文案。详细原因仍保留在日志区。"""
        raw = str(message or "").strip()
        clean = raw.split("] ", 1)[-1] if "] " in raw else raw
        task_match = re.search(r"任务「([^」]+)」", clean)
        task_part = f"任务「{task_match.group(1)}」" if task_match else "当前任务"

        if any(key in clean for key in ["启动失败", "构建启动命令失败", "脚本不存在", "脚本路径为空", "路径不是文件", "WinError 740", "请求的操作需要提升"]):
            return f"{task_part}没有顺利启动。简小松先帮你标出来，详细原因可以看日志。"
        if any(key in clean for key in ["已超时", "超时", "强制结束"]):
            return f"{task_part}运行太久了，已经按设置处理。简小松会先保持异常提示。"
        if any(key in clean for key in ["疑似过早退出", "提前退出", "过早退出"]):
            return f"{task_part}很快就退出了，可能没有正常跑完。详细情况在日志里。"
        if any(key in clean for key in ["未检测到", "判定为异常"]):
            return f"{task_part}没有检测到预期结果，简小松先记为异常。"
        return f"{task_part}遇到异常了。简小松先提示你，详细信息可以打开日志查看。"

    def _schedule_status_character_error_release(self, lock_seconds: int) -> None:
        if self.status_character_error_timer is None:
            self.status_character_error_timer = QTimer(self)
            self.status_character_error_timer.setSingleShot(True)
            self.status_character_error_timer.timeout.connect(self._release_status_character_error_if_due)
        self.status_character_error_timer.start(max(1, lock_seconds * 1000))

    def _release_status_character_error_if_due(self) -> None:
        now_ms = QDateTime.currentMSecsSinceEpoch()
        if self.status_character_state != "error":
            return
        remaining_ms = self.status_character_error_locked_until_ms - now_ms
        if remaining_ms > 0:
            self.status_character_error_timer.start(max(1, remaining_ms))
            return
        next_state = "work" if self.is_running else "rest"
        self.set_status_character_state(next_state, force=True)

    def _start_status_character_random_timer(self) -> None:
        """v28.8：兼容旧接口。

        简小松现在采用“进入状态时随机一次”的低干扰策略，
        不再用定时器持续换图；否则 idle 时会一直动，反而抢注意力。
        """
        if self.status_character_random_timer is not None:
            self.status_character_random_timer.stop()

    def _refresh_status_character_random_image(self) -> None:
        """保留调试入口：手动调用时才随机刷新当前状态图。"""
        if self.status_character_state == "error":
            return
        self._load_status_character_image(self.status_character_state, random_pick=True)

    def _set_layout_visible(self, layout, visible: bool) -> None:
        """递归设置布局内控件可见性，用于可折叠区域。"""
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.setVisible(visible)
            if child_layout is not None:
                self._set_layout_visible(child_layout, visible)

    def _make_group_collapsible(self, group: QGroupBox) -> None:
        """让 QGroupBox 点击标题即可展开/收起。"""
        base_title = group.title().replace("▼ ", "").replace("▶ ", "")
        group.setCheckable(True)
        group.setChecked(True)
        group.setTitle(f"▼ {base_title}")

        def on_toggled(checked: bool) -> None:
            group.setTitle(f"{'▼' if checked else '▶'} {base_title}")
            layout = group.layout()
            if layout is not None:
                self._set_layout_visible(layout, checked)
            if checked:
                group.setMaximumHeight(16777215)
            else:
                # 保留标题栏高度，使用户可以再次点击展开。
                group.setMaximumHeight(44)

        group.toggled.connect(on_toggled)

    def _build_menu(self) -> None:
        """v28.3：弱化传统菜单栏，把“文件”入口合并到顶部控制条。"""
        file_menu = QMenu(self)

        save_action = QAction("保存配置", self)
        save_action.triggered.connect(self.save_config_from_ui)
        file_menu.addAction(save_action)

        export_action = QAction("导出配置", self)
        export_action.triggered.connect(self.export_config)
        file_menu.addAction(export_action)

        import_action = QAction("导入配置", self)
        import_action.triggered.connect(self.import_config)
        file_menu.addAction(import_action)

        # 发布前用户模式：隐藏 UI 参数导入/导出，避免暴露开发调参入口。
        file_menu.addSeparator()

        open_config_action = QAction("打开配置所在目录", self)
        open_config_action.triggered.connect(lambda: self.open_directory(self.base_dir))
        file_menu.addAction(open_config_action)

        open_log_action = QAction("打开日志目录", self)
        open_log_action.triggered.connect(lambda: self.open_directory(self.file_logger.log_dir))
        file_menu.addAction(open_log_action)

        file_menu.addSeparator()

        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        self.file_button.setMenu(file_menu)
        self.menuBar().hide()

    def _connect_signals(self) -> None:
        self.add_button.clicked.connect(self.add_task_row)
        self.delete_button.clicked.connect(self.delete_selected_row)
        self.up_button.clicked.connect(self.move_selected_row_up)
        self.down_button.clicked.connect(self.move_selected_row_down)
        self.save_button.clicked.connect(self.save_config_from_ui)
        self.start_button.clicked.connect(self.start_tasks)
        self.resume_button.clicked.connect(self.resume_tasks)
        self.pause_button.clicked.connect(self.pause_tasks)
        self.stop_button.clicked.connect(self.stop_tasks)
        self.emergency_stop_button.clicked.connect(self.emergency_stop_tasks)
        self.cancel_shutdown_button.clicked.connect(self.cancel_shutdown)
        self.theme_combo.currentIndexChanged.connect(self.on_theme_combo_changed)
        self._setup_shortcuts()
        self.export_config_button.clicked.connect(self.export_config)
        self.import_config_button.clicked.connect(self.import_config)
        self.refresh_stats_button.clicked.connect(self.refresh_runtime_stats_view)
        self.open_stats_button.clicked.connect(lambda: self.open_directory(self.base_dir / "runtime_stats"))
        self.clear_log_button.clicked.connect(self.clear_log)
        self.copy_log_button.clicked.connect(self.copy_log)
        self.open_log_dir_button.clicked.connect(lambda: self.open_directory(self.file_logger.log_dir))
        self.author_link_button.clicked.connect(lambda: self.open_external_url(AUTHOR_URL))
        self.github_link_button.clicked.connect(lambda: self.open_external_url(GITHUB_URL))
        self.about_link_button.clicked.connect(self.open_local_documentation)

    def clear_log(self) -> None:
        self.log_edit.clear()

    def copy_log(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self.log_edit.toPlainText())
            self.show_temporary_status_character_message("日志内容已复制到剪贴板！", 5)

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("F5"), self, activated=self.start_tasks)
        QShortcut(QKeySequence("F6"), self, activated=self.resume_tasks)
        QShortcut(QKeySequence("F7"), self, activated=self.pause_tasks)
        QShortcut(QKeySequence("F8"), self, activated=self.stop_tasks)
        QShortcut(QKeySequence("Ctrl+Alt+F8"), self, activated=self.emergency_stop_tasks)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)
        self.append_log("快捷键：F5启动，F6继续，F7暂停，F8停止执行，Ctrl+Alt+F8紧急停止全部，Ctrl+Q关闭程序。")

    def refresh_runtime_stats_view(self) -> None:
        """在 UI 中用表格展示历史耗时统计，避免长脚本名导致列对齐错乱。"""
        try:
            summary_path = self.base_dir / "runtime_stats" / "runtime_summary.json"
            if not summary_path.exists():
                self.stats_table.setRowCount(0)
                return
            with summary_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not data:
                self.stats_table.setRowCount(0)
                return

            rows = []
            for name, item in data.items():
                if not isinstance(item, dict):
                    continue
                rows.append((
                    str(name),
                    int(item.get("count", 0)),
                    int(float(item.get("average_seconds", 0))),
                    int(item.get("last_seconds", 0)),
                    str(item.get("last_time", "")),
                ))
            rows.sort(key=lambda x: x[0].lower())

            self.stats_table.setRowCount(len(rows))
            for row, (name, count, avg, last, last_time) in enumerate(rows):
                values = [name, str(count), format_seconds(avg), format_seconds(last), last_time]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if col in {1, 2, 3}:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    else:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                    self.stats_table.setItem(row, col, item)
            self.stats_table.resizeRowsToContents()
        except Exception as exc:
            self.stats_table.setRowCount(1)
            self.stats_table.setItem(0, 0, QTableWidgetItem(f"读取统计失败：{exc}"))

    def append_log(self, message: str, already_formatted: bool = False) -> None:
        if already_formatted:
            line = message
            self.file_logger.write_line(line)
        else:
            line = self.file_logger.log(message)

        self.log_edit.append(line)
        cursor = self.log_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_edit.setTextCursor(cursor)

    def add_task(self) -> None:
        tasks = self.collect_tasks_from_table(sort_by_order=False)
        next_order = len(tasks) + 1
        tasks.append(
            TaskConfig(
                enabled=True,
                name=f"新任务{next_order}",
                script_path="",
                order=next_order,
                timeout_minutes=30,
                timeout_action="kill_and_continue",
                use_args=False,
                args="",
                wait_mode="direct_process",
                wait_process_name="",
                confirm_enter_delay_seconds=0,
                concurrent_group="",
                concurrent_policy="wait_all",
                enable_watchdog=False,
                process_keywords=[],
                window_keywords=[],
            )
        )
        self.reload_table(tasks)
        self.table.selectRow(len(tasks) - 1)

    def delete_selected_task(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self._show_themed_message("提示", "请先选择要删除的任务。")
            return

        task_name = self._item_text(row, self.COL_NAME, default=f"第 {row + 1} 行")
        if not self._ask_themed_yes_no("确认删除", f"确定删除任务「{task_name}」吗？"):
            return

        tasks = self.collect_tasks_from_table(sort_by_order=False)
        if 0 <= row < len(tasks):
            tasks.pop(row)
        self._normalize_orders(tasks)
        self.reload_table(tasks)

    def move_selected_task_up(self) -> None:
        row = self.table.currentRow()
        if row <= 0:
            return

        tasks = self.collect_tasks_from_table(sort_by_order=False)
        tasks[row - 1], tasks[row] = tasks[row], tasks[row - 1]
        self._normalize_orders(tasks)
        self.reload_table(tasks)
        self.table.selectRow(row - 1)

    def move_selected_task_down(self) -> None:
        row = self.table.currentRow()
        tasks = self.collect_tasks_from_table(sort_by_order=False)
        if row < 0 or row >= len(tasks) - 1:
            return

        tasks[row + 1], tasks[row] = tasks[row], tasks[row + 1]
        self._normalize_orders(tasks)
        self.reload_table(tasks)
        self.table.selectRow(row + 1)


    def _create_centered_checkbox(self, checked: bool = True) -> QWidget:
        """创建居中对齐、整格可点且无重影的复选框容器。"""
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cb = QCheckBox()
        cb.setChecked(checked)
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(cb)
        container.checkbox = cb  # type: ignore[attr-defined]
        return container

    def on_table_cell_clicked(self, row: int, col: int) -> None:
        """单击单元格快速响应：点击启用列或参数列直接切换勾选状态。"""
        if row < 0:
            return
        if col in {self.COL_ENABLED, self.COL_USE_ARGS, self.COL_WATCHDOG}:
            w = self.table.cellWidget(row, col)
            if hasattr(w, "checkbox"):
                w.checkbox.setChecked(not w.checkbox.isChecked())

    def on_table_cell_double_clicked(self, row: int, col: int) -> None:
        """双击路径列直接弹出文件浏览选择，操作顺手丝滑。"""
        if row >= 0 and col in {self.COL_PATH, self.COL_BROWSE}:
            self.browse_script_for_row(row)

    def on_table_context_menu(self, pos) -> None:
        """表格右键快捷菜单：随时随地快速增删移换。"""
        global_pos = self.table.viewport().mapToGlobal(pos)
        row = self.table.rowAt(pos.y())

        menu = QMenu(self)
        add_action = menu.addAction("＋ 添加新任务")
        add_action.triggered.connect(self.add_task_row)

        if row >= 0:
            self.table.selectRow(row)
            menu.addSeparator()
            browse_action = menu.addAction("📁 浏览选择脚本文件...")
            browse_action.triggered.connect(lambda: self.browse_script_for_row(row))

            toggle_action = menu.addAction("✔ 切换启用/禁用状态")
            toggle_action.triggered.connect(lambda: self.on_table_cell_clicked(row, self.COL_ENABLED))

            up_action = menu.addAction("↑ 上移任务")
            up_action.triggered.connect(self.move_selected_task_up)

            down_action = menu.addAction("↓ 下移任务")
            down_action.triggered.connect(self.move_selected_task_down)

            menu.addSeparator()
            del_action = menu.addAction("🗑 删除此任务")
            del_action.triggered.connect(self.delete_selected_task)

        menu.addSeparator()
        adv_action = menu.addAction("⚙ 进阶设置...")
        adv_action.triggered.connect(lambda: self.open_task_advanced_settings(max(0, row)))

        menu.exec(global_pos)

    def _apply_frozen_identity_cell_style(self, item: Optional[QTableWidgetItem]) -> None:
        """冻结列样式由 QSS 统一接管，不再硬编码背景色以避免选中产生色差。"""
        pass

    def reload_table(self, tasks: List[TaskConfig]) -> None:
        self.table.setRowCount(0)

        tasks_to_show = list(tasks)
        tasks_to_show.sort(key=lambda task: task.order)

        for row, task in enumerate(tasks_to_show):
            self.table.insertRow(row)
            self._set_task_row(row, task)
            self.table.setRowHeight(row, 36)

        self._normalize_order_cells()

    def _set_task_row(self, row: int, task: TaskConfig) -> None:
        # 1. 启用列：真正原生居中 QCheckBox，绝对零重影、零偏位、零白框
        self.table.setItem(row, self.COL_ENABLED, QTableWidgetItem(""))
        self.table.setCellWidget(row, self.COL_ENABLED, self._create_centered_checkbox(task.enabled))

        # 2. 游戏/脚本名称
        name_item = QTableWidgetItem(task.name)
        name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, self.COL_NAME, name_item)

        # 3. 脚本路径
        path_item = QTableWidgetItem(task.script_path)
        path_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
        path_item.setToolTip(task.script_path or "双击或点击选择按钮以定位脚本路径")
        self.table.setItem(row, self.COL_PATH, path_item)

        # 4. 浏览选择按钮
        browse_button = QPushButton("选择")
        browse_button.setFixedHeight(28)
        browse_button.setFixedWidth(56)
        browse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_button.clicked.connect(lambda _checked=False, r=row: self.browse_script_for_row(r))
        self.table.setCellWidget(row, self.COL_BROWSE, browse_button)

        # 5. 顺序列：只读显示，由上移下移按钮调整
        order_item = QTableWidgetItem(str(task.order))
        order_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        order_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, self.COL_ORDER, order_item)

        # 6. 最大运行时间
        timeout_item = QTableWidgetItem(str(task.timeout_minutes))
        timeout_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
        timeout_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, self.COL_TIMEOUT, timeout_item)

        # 7. 超时处理方式
        action_combo = QComboBox()
        action_combo.setFixedHeight(28)
        action_combo.setMinimumWidth(140)
        for value, label in TIMEOUT_ACTIONS.items():
            action_combo.addItem(label, value)
        index = action_combo.findData(task.timeout_action)
        if index < 0:
            index = action_combo.findData("kill_and_continue")
        action_combo.setCurrentIndex(index)
        self.table.setCellWidget(row, self.COL_ACTION, action_combo)

        # 8. 启用参数列
        self.table.setItem(row, self.COL_USE_ARGS, QTableWidgetItem(""))
        self.table.setCellWidget(row, self.COL_USE_ARGS, self._create_centered_checkbox(task.use_args))

        # 9. 启动命令/参数
        args_item = QTableWidgetItem(task.args)
        args_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, self.COL_ARGS, args_item)

        # 10. 完成判断方式
        wait_combo = QComboBox()
        wait_combo.setFixedHeight(28)
        wait_combo.setMinimumWidth(125)
        for value, label in WAIT_MODES.items():
            wait_combo.addItem(label, value)
        wait_index = wait_combo.findData(task.wait_mode)
        if wait_index < 0:
            wait_index = wait_combo.findData("direct_process")
        wait_combo.setCurrentIndex(wait_index)
        self.table.setCellWidget(row, self.COL_WAIT_MODE, wait_combo)

        # 11. 等待关键词
        wait_process_item = QTableWidgetItem(task.wait_process_name)
        wait_process_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, self.COL_WAIT_PROCESS, wait_process_item)

        # 12. 自动 Enter 秒数
        confirm_item = QTableWidgetItem(str(task.confirm_enter_delay_seconds))
        confirm_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
        confirm_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, self.COL_CONFIRM_ENTER, confirm_item)

        # 13. 并发组
        concurrent_group_item = QTableWidgetItem(task.concurrent_group)
        concurrent_group_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
        concurrent_group_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, self.COL_CONCURRENT_GROUP, concurrent_group_item)

        # 14. 组策略
        concurrent_policy_combo = QComboBox()
        concurrent_policy_combo.setFixedHeight(28)
        for value, label in CONCURRENT_POLICIES.items():
            concurrent_policy_combo.addItem(label, value)
        policy_index = concurrent_policy_combo.findData(task.concurrent_policy)
        if policy_index < 0:
            policy_index = concurrent_policy_combo.findData("wait_all")
        concurrent_policy_combo.setCurrentIndex(policy_index)
        self.table.setCellWidget(row, self.COL_CONCURRENT_POLICY, concurrent_policy_combo)

        # 15. 守护监控
        self.table.setItem(row, self.COL_WATCHDOG, QTableWidgetItem(""))
        self.table.setCellWidget(row, self.COL_WATCHDOG, self._create_centered_checkbox(task.enable_watchdog))

        # 16. 目标进程关键词
        proc_kw_item = QTableWidgetItem(keywords_to_text(task.process_keywords))
        proc_kw_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, self.COL_PROCESS_KEYWORDS, proc_kw_item)

        # 17. 游戏窗口关键词
        win_kw_item = QTableWidgetItem(keywords_to_text(task.window_keywords))
        win_kw_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, self.COL_WINDOW_KEYWORDS, win_kw_item)

        # v27.2：高级列由标题右侧全局按钮统一展开/收起；每行不再放按钮。
        self.table.setItem(row, self.COL_ADVANCED, QTableWidgetItem(""))
        self.table.setColumnHidden(self.COL_ADVANCED, True)
        for col in getattr(self, "advanced_task_columns", []):
            self.table.setColumnHidden(col, not getattr(self, "advanced_columns_visible", False))

    def set_task_advanced_columns_visible(self, visible: bool) -> None:
        """显示/隐藏任务表格的高级运行列。"""
        self.advanced_columns_visible = visible
        for col in getattr(self, "advanced_task_columns", []):
            self.table.setColumnHidden(col, not visible)

        button_text = "收起进阶" if visible else "进阶设置"
        if hasattr(self, "task_advanced_toggle_button"):
            self.task_advanced_toggle_button.setText(button_text)

        # 展开后给长文本列更多空间，并自动拉到最右侧；收起时恢复紧凑。
        header = self.table.horizontalHeader()
        if visible:
            header.setSectionResizeMode(self.COL_PROCESS_KEYWORDS, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(self.COL_PROCESS_KEYWORDS, 170)
            header.setSectionResizeMode(self.COL_WINDOW_KEYWORDS, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(self.COL_WINDOW_KEYWORDS, 170)
            QTimer.singleShot(0, lambda: self.table.horizontalScrollBar().setValue(self.table.horizontalScrollBar().maximum()))
        else:
            header.setSectionResizeMode(self.COL_PROCESS_KEYWORDS, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(self.COL_WINDOW_KEYWORDS, QHeaderView.ResizeMode.ResizeToContents)
        if hasattr(self.table, "_sync_frozen_columns"):
            self.table._sync_frozen_columns()

    def _load_config_to_ui(self) -> None:
        """将 self.config 加载至 UI 状态。"""
        self.tasks_list = [TaskConfig(**t.__dict__) if hasattr(t, "__dict__") else t for t in (self.config.tasks or [])]
        self._normalize_orders(self.tasks_list)
        self.current_task_idx = 0
        self._render_master_task_list()

        # 加载设置项
        self.shutdown_checkbox.setChecked(self.config.shutdown_after_done)
        self.shutdown_delay_spin.setValue(self.config.shutdown_delay_seconds)
        self.auto_exit_checkbox.setChecked(self.config.auto_exit_after_done)
        self.auto_start_tasks_checkbox.setChecked(self.config.auto_start_tasks)
        self.windows_startup_checkbox.setChecked(self.config.windows_startup)
        self.timeout_screenshot_checkbox.setChecked(self.config.enable_timeout_screenshot)

    def collect_tasks_from_table(self, sort_by_order: bool = True) -> List[TaskConfig]:
        """收集当前 UI 中的任务配置（无缝兼容调度器接口）。"""
        self._sync_current_detail_to_task()
        tasks = [TaskConfig(**t.__dict__) if hasattr(t, "__dict__") else t for t in self.tasks_list]
        if sort_by_order:
            tasks.sort(key=lambda task: task.order)
            self._normalize_orders(tasks)
        return tasks

    def add_task_row(self, task: TaskConfig | None = None) -> None:
        """添加新任务。"""
        self._sync_current_detail_to_task()
        if task is None:
            task = TaskConfig(
                enabled=True,
                name=f"新任务 {len(self.tasks_list) + 1}",
                script_path="",
                order=len(self.tasks_list) + 1,
                timeout_minutes=30,
            )
        self.tasks_list.append(task)
        self._normalize_orders(self.tasks_list)
        self.current_task_idx = len(self.tasks_list) - 1
        self._render_master_task_list()
        self.save_config_from_ui()

    def delete_selected_row(self) -> None:
        """删除当前选中的任务。"""
        if not (0 <= self.current_task_idx < len(self.tasks_list)):
            return
        del self.tasks_list[self.current_task_idx]
        self._normalize_orders(self.tasks_list)
        self.current_task_idx = max(0, min(self.current_task_idx, len(self.tasks_list) - 1))
        self._render_master_task_list()
        self.save_config_from_ui()

    def move_selected_row_up(self) -> None:
        """上移当前选中的任务。"""
        idx = self.current_task_idx
        if idx <= 0 or idx >= len(self.tasks_list):
            return
        self._sync_current_detail_to_task()
        self.tasks_list[idx - 1], self.tasks_list[idx] = self.tasks_list[idx], self.tasks_list[idx - 1]
        self._normalize_orders(self.tasks_list)
        self.current_task_idx = idx - 1
        self._render_master_task_list()
        self.save_config_from_ui()

    def move_selected_row_down(self) -> None:
        """下移当前选中的任务。"""
        idx = self.current_task_idx
        if idx < 0 or idx >= len(self.tasks_list) - 1:
            return
        self._sync_current_detail_to_task()
        self.tasks_list[idx + 1], self.tasks_list[idx] = self.tasks_list[idx], self.tasks_list[idx + 1]
        self._normalize_orders(self.tasks_list)
        self.current_task_idx = idx + 1
        self._render_master_task_list()
        self.save_config_from_ui()

    def reload_table(self, tasks: List[TaskConfig]) -> None:
        """重新加载任务列表。"""
        self.tasks_list = [TaskConfig(**t.__dict__) if hasattr(t, "__dict__") else t for t in tasks]
        self._normalize_orders(self.tasks_list)
        self._render_master_task_list()

    def _normalize_orders(self, tasks: List[TaskConfig]) -> None:
        for index, task in enumerate(tasks, start=1):
            task.order = index

    def save_config_from_ui(self) -> None:
        tasks = self.collect_tasks_from_table(sort_by_order=True)
        self.config.tasks = tasks
        self.config.shutdown_after_done = self.shutdown_checkbox.isChecked()
        self.config.shutdown_delay_seconds = int(self.shutdown_delay_spin.value())
        self.config.auto_exit_after_done = self.auto_exit_checkbox.isChecked()
        self.config.auto_start_tasks = self.auto_start_tasks_checkbox.isChecked()
        self.config.windows_startup = self.windows_startup_checkbox.isChecked()
        self.config.enable_timeout_screenshot = self.timeout_screenshot_checkbox.isChecked()
        self._apply_windows_startup(self.config.windows_startup)

        try:
            self.config_manager.save(self.config)
            self.append_log("配置已保存。")
        except Exception as exc:
            self._show_themed_message("保存失败", f"保存配置失败：{exc}", QMessageBox.Icon.Critical)
            self.append_log(f"保存配置失败：{exc}")

    def start_tasks(self) -> None:
        if self.is_running:
            return

        self.save_config_from_ui()

        tasks = self.collect_tasks_from_table(sort_by_order=True)
        enabled_tasks = [task for task in tasks if task.enabled]
        if not enabled_tasks:
            self.append_log("【提示】当前没有启用的任务，请在【任务调度】页面开启至少一个任务开关。")
            self._show_themed_message("提示", "当前没有启用的任务。\n请在【任务调度】页面开启至少一个任务。")
            return

        # 检查未填路径的任务并给出友好提示
        for task in enabled_tasks:
            if not task.script_path.strip():
                if task.name.strip():
                    task.script_path = task.name.strip()
                    self.append_log(f"【提示】任务「{task.name}」未指定完整路径，将尝试直接作为程序名启动。")

        self.config.tasks = tasks
        self.config.shutdown_after_done = self.shutdown_checkbox.isChecked()
        self.config.shutdown_delay_seconds = int(self.shutdown_delay_spin.value())
        self.config.auto_exit_after_done = self.auto_exit_checkbox.isChecked()
        self.config.auto_start_tasks = self.auto_start_tasks_checkbox.isChecked()
        self.config.windows_startup = self.windows_startup_checkbox.isChecked()
        self.config.enable_timeout_screenshot = self.timeout_screenshot_checkbox.isChecked()
        self._apply_windows_startup(self.config.windows_startup)

        self.is_running = True
        self.run_task_error_count = 0
        self.set_controls_running(True)
        self.status_value_label.setText("● 正在执行")
        self.status_value_label.setStyleSheet("""
            QLabel#StatusBadge {
                color: #047857;
                background: rgba(209, 250, 229, 235);
                border: 1px solid rgba(52, 211, 153, 220);
                border-radius: 8px;
                padding: 4px 12px;
                font-weight: 800;
                font-size: 13px;
            }
        """)
        self.task_value_label.setText(f"准备执行第 1/{len(enabled_tasks)} 个任务")
        self.elapsed_value_label.setText("00:00")
        self.progress_value_label.setText(f"0 / {len(enabled_tasks)} 任务")

        self.append_log(f"========== 调度流程启动，共 {len(enabled_tasks)} 个已启用任务 ==========")

        self.runner_thread = QThread(self)
        self.runner_worker = ScriptRunnerWorker(
            tasks=tasks,
            shutdown_after_done=self.config.shutdown_after_done,
            shutdown_delay_seconds=self.config.shutdown_delay_seconds,
            logger=self.file_logger,
            stats_manager=self.stats_manager,
            enable_timeout_screenshot=getattr(self.config, "enable_timeout_screenshot", False),
        )
        self.runner_worker.moveToThread(self.runner_thread)

        self.runner_thread.started.connect(self.runner_worker.run)
        self.runner_worker.log_signal.connect(self.on_worker_log)
        self.runner_worker.status_signal.connect(self.on_worker_status)
        self.runner_worker.progress_signal.connect(self.on_worker_progress)
        self.runner_worker.task_started_signal.connect(self.on_worker_task_started)
        self.runner_worker.elapsed_signal.connect(self.on_worker_elapsed)
        self.runner_worker.shutdown_prompt_signal.connect(self.show_shutdown_warning_dialog)
        self.runner_worker.task_error_signal.connect(self.on_worker_task_error)
        self.runner_worker.task_launch_success_signal.connect(self.on_worker_task_launch_success)
        self.runner_worker.finished_signal.connect(self.on_worker_finished)

        self.runner_worker.finished_signal.connect(self.runner_thread.quit)
        self.runner_thread.finished.connect(self.runner_worker.deleteLater)
        self.runner_thread.finished.connect(self.runner_thread.deleteLater)

        self.runner_thread.start()
        self.append_log("后台执行引擎已就绪，正在顺序启动任务...")

    def _update_status_view(self, status: str) -> None:
        status_clean = str(status or "").strip().lstrip("● ").strip()
        if not status_clean:
            status_clean = "空闲待命"

        if any(kw in status_clean for kw in ["异常", "错误", "失败", "报错"]):
            display_text = f"● {status_clean}"
            style = """
                QLabel#StatusBadge {
                    color: #B91C1C;
                    background: rgba(254, 226, 226, 235);
                    border: 1px solid rgba(248, 113, 113, 220);
                    border-radius: 8px;
                    padding: 4px 12px;
                    font-weight: 800;
                    font-size: 13px;
                }
            """
        elif "运行" in status_clean or "启动" in status_clean:
            display_text = f"● {status_clean}"
            style = """
                QLabel#StatusBadge {
                    color: #047857;
                    background: rgba(209, 250, 229, 235);
                    border: 1px solid rgba(52, 211, 153, 220);
                    border-radius: 8px;
                    padding: 4px 12px;
                    font-weight: 800;
                    font-size: 13px;
                }
            """
        elif "暂停" in status_clean:
            display_text = f"● {status_clean}"
            style = """
                QLabel#StatusBadge {
                    color: #B45309;
                    background: rgba(254, 243, 199, 235);
                    border: 1px solid rgba(251, 191, 36, 220);
                    border-radius: 8px;
                    padding: 4px 12px;
                    font-weight: 800;
                    font-size: 13px;
                }
            """
        elif "完成" in status_clean:
            display_text = f"● {status_clean}"
            style = """
                QLabel#StatusBadge {
                    color: #047857;
                    background: rgba(209, 250, 229, 235);
                    border: 1px solid rgba(52, 211, 153, 220);
                    border-radius: 8px;
                    padding: 4px 12px;
                    font-weight: 800;
                    font-size: 13px;
                }
            """
        elif "停止" in status_clean:
            display_text = f"● {status_clean}"
            style = """
                QLabel#StatusBadge {
                    color: #4B5563;
                    background: rgba(243, 244, 246, 235);
                    border: 1px solid rgba(209, 213, 219, 220);
                    border-radius: 8px;
                    padding: 4px 12px;
                    font-weight: 800;
                    font-size: 13px;
                }
            """
        else:
            display_text = f"● {status_clean}"
            style = """
                QLabel#StatusBadge {
                    color: #2563EB;
                    background: rgba(219, 234, 254, 230);
                    border: 1px solid rgba(147, 197, 253, 220);
                    border-radius: 8px;
                    padding: 4px 12px;
                    font-weight: 800;
                    font-size: 13px;
                }
            """
        self.status_value_label.setText(display_text)
        self.status_value_label.setStyleSheet(style)

    def pause_tasks(self) -> None:
        if not self.is_running or self.runner_worker is None:
            return
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(True)
        self._update_status_view("已暂停")
        self.set_status_character_state("rest")
        self.show_temporary_status_character_message("先暂停一下吧，我在这里等你回来。", 10, priority=55)
        self.runner_worker.request_pause()

    def resume_tasks(self) -> None:
        if not self.is_running or self.runner_worker is None:
            return
        self.resume_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self._update_status_view("运行中")
        self.set_status_character_state("work")
        self.runner_worker.request_resume()

    def stop_tasks(self) -> None:
        if not self.is_running or self.runner_worker is None:
            return

        self.resume_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self._update_status_view("正在停止执行")
        self.set_status_character_state("rest")
        self.show_temporary_status_character_message("收到停止指令啦，我会帮你收尾当前任务。", 10, priority=55)
        self.append_log("用户点击停止执行。")
        self.runner_worker.request_stop()

    def emergency_stop_tasks(self) -> None:
        if not self._ask_themed_yes_no(
            "确认紧急停止",
            "紧急停止会扫描所有任务配置，并按以下顺序强制收尾：\n"
            "1. 启动脚本进程 / launched script\n"
            "2. 目标进程 / target process\n"
            "3. 游戏窗口 / 扩展进程\n"
            "4. 游戏窗口关键词对应 PID 兜底清理\n\n"
            "确定要执行紧急停止吗？",
            QMessageBox.Icon.Warning,
        ):
            return

        self.resume_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.emergency_stop_button.setEnabled(False)
        self._update_status_view("紧急停止中")
        self.set_status_character_state("rest")
        self.show_temporary_status_character_message("正在紧急停止，我会尽量把相关进程一起处理掉。", 12, priority=95)
        self.append_log("用户触发紧急停止全部。")
        if self.runner_worker is not None:
            self.runner_worker.request_emergency_stop()
        else:
            self.append_log("当前没有正在运行的调度任务，紧急停止仅记录日志。")

    @Slot(str)
    def on_worker_log(self, line: str) -> None:
        self.log_edit.append(line)
        cursor = self.log_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_edit.setTextCursor(cursor)

        # v31.10：收紧兜底 error 识别。
        error_keywords = [
            "启动失败", "构建启动命令失败", "脚本不存在", "脚本路径为空",
            "路径不是文件", "请求的操作需要提升", "WinError 740",
            "执行器发生异常", "保存异常报告失败",
            "强制结束失败", "kill 失败", "终止失败",
            "紧急停止失败", "关闭失败",
        ]
        timeout_error_markers = [
            "已超时",
            "超时：正在强制结束",
        ]
        if any(keyword in line for keyword in error_keywords) or any(keyword in line for keyword in timeout_error_markers):
            self._record_abnormal_report(line)
            self.set_status_character_state("error", error_reason=self._friendly_error_bubble_text(line))

    @Slot(str)
    def on_worker_task_launch_success(self, task_name: str) -> None:
        if getattr(self, "status_character_state", "idle") == "error":
            return
        self._update_status_view("运行中")
        self.set_status_character_state("work", force=True)
        self.show_temporary_status_character_message("启动成功啦～接下来我会帮你盯着进度。", 10, priority=80)

    @Slot(str)
    def on_worker_task_error(self, message: str) -> None:
        self.run_task_error_count += 1
        self._record_abnormal_report(message)
        self._update_status_view("出现异常")
        self.set_status_character_state("error", error_reason=self._friendly_error_bubble_text(message))

    @Slot(str)
    def on_worker_status(self, status: str) -> None:
        self._update_status_view(status)
        if any(keyword in status for keyword in ["异常", "错误", "失败", "报错"]):
            self.set_status_character_state("error", error_reason="当前状态出现异常。简小松会先停留在提示状态，方便你查看。")
        elif status in ["已暂停", "已停止", "已完成", "超时处理中"]:
            self.set_status_character_state("rest")
        elif "运行" in status:
            self.set_status_character_state("work")

    @Slot(int, int)
    def on_worker_progress(self, current: int, total: int) -> None:
        self.current_progress = (current, total)
        self.progress_value_label.setText(f"{current} / {total} 任务")

    @Slot(str, int, int)
    def on_worker_task_started(self, task_name: str, index: int, total: int) -> None:
        self.current_task_name = task_name
        self.current_elapsed = 0
        self.task_value_label.setText(task_name)
        self.elapsed_value_label.setText("00:00")
        self.progress_value_label.setText(f"{index} / {total} 任务")

    @Slot(int)
    def on_worker_elapsed(self, elapsed: int) -> None:
        self.current_elapsed = elapsed
        self.elapsed_value_label.setText(format_seconds(elapsed))

    @Slot(bool)
    def on_worker_finished(self, stopped_or_error: bool) -> None:
        self.is_running = False
        self.set_controls_running(False)
        self.resume_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.emergency_stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(False)

        # 断开引用，避免重复 stop。
        self.runner_worker = None
        self.runner_thread = None

        if stopped_or_error:
            current_status = self.status_value_label.text()
            if "停止" in current_status and self.status_character_state != "error":
                self._update_status_view("已停止")
                self.set_status_character_state("rest")
                self.show_temporary_status_character_message("已经按你的要求停止啦，后续任务不会继续执行。", 12, priority=60)
            else:
                self._update_status_view("已完成（异常）")
                count = max(1, int(getattr(self, "run_task_error_count", 0)))
                msg = f"任务结束，发生了 {count} 次任务异常。"
                self._record_abnormal_report(msg)
                self.set_status_character_state("error", error_reason=msg)
        else:
            self._update_status_view("已全部完成")
            self.set_status_character_state("rest")
            self.show_temporary_status_character_message("任务已经顺利完成啦～这轮工作收好啦。", 12, priority=60)

        self.refresh_runtime_stats_view()

        if self.config.auto_exit_after_done and not stopped_or_error:
            self.append_log("已启用任务完成后自动退出程序，程序即将退出。")
            QTimer.singleShot(1500, QApplication.instance().quit)

    def set_controls_running(self, running: bool) -> None:
        """根据调度运行状态统一更新所有交互控件的可用性。"""
        controls_disabled_when_running = [
            "add_button", "delete_button", "up_button", "down_button",
            "save_button", "start_button", "tasks_page_start_button",
            "table", "shutdown_checkbox", "shutdown_delay_spin",
            "auto_exit_checkbox", "auto_start_tasks_checkbox",
            "windows_startup_checkbox", "timeout_screenshot_checkbox",
            "export_config_button", "import_config_button",
        ]
        for name in controls_disabled_when_running:
            w = getattr(self, name, None)
            if w is not None and hasattr(w, "setEnabled"):
                w.setEnabled(not running)

        if hasattr(self, "resume_button") and self.resume_button:
            self.resume_button.setEnabled(False)
        if hasattr(self, "pause_button") and self.pause_button:
            self.pause_button.setEnabled(running)
        if hasattr(self, "stop_button") and self.stop_button:
            self.stop_button.setEnabled(running)
        if hasattr(self, "emergency_stop_button") and self.emergency_stop_button:
            self.emergency_stop_button.setEnabled(True)
        if hasattr(self, "cancel_shutdown_button") and self.cancel_shutdown_button:
            self.cancel_shutdown_button.setEnabled(True)
        if hasattr(self, "refresh_stats_button") and self.refresh_stats_button:
            self.refresh_stats_button.setEnabled(True)
        if hasattr(self, "open_stats_button") and self.open_stats_button:
            self.open_stats_button.setEnabled(True)

    def toggle_theme(self) -> None:
        order = ["light", "dark", "jiansz", "campus", "fresh", "fantasy"]
        current = self.config.theme if self.config.theme in order else "jiansz"
        new_theme = order[(order.index(current) + 1) % len(order)]
        self.config.theme = new_theme
        self.apply_theme(new_theme)
        try:
            self.save_config_from_ui()
        except Exception:
            pass

    def on_theme_combo_changed(self) -> None:
        theme = self.theme_combo.currentData()
        if not theme:
            return
        if getattr(self, "_applying_theme", False):
            return
        self.config.theme = str(theme)
        self.apply_theme(str(theme))
        try:
            self.save_config_from_ui()
        except Exception:
            pass

    def _theme_asset_folder(self, theme: str) -> str:
        """v18：主题名到资源目录的映射。"""
        if theme == "jiansz":
            return "classic"
        if theme == "fantasy":
            return "warm"
        if theme in {"campus", "fresh"}:
            return theme
        # 浅色/深色暂时复用经典背景；没有图片时自动回退纯色。
        return "classic"

    def _asset_path(self, *parts) -> Path:
        return self.base_dir / "assets" / Path(*parts)

    def _existing_asset_path(self, *parts) -> Path | None:
        path = self._asset_path(*parts)
        return path if path.exists() else None

    def apply_theme(self, theme: str) -> None:
        palettes = {
            "light": {
                "name": "浅色", "bg": "#1A202C", "fg": "#F1F5F9", "panel": "#222734", "panel2": "#2A3142",
                "border": "#4B5563", "accent": "#6C8EBF", "accent2": "#8FAADE", "muted": "#94A3B8",
                "button": "#2D3748", "button_hover": "#374151", "table": "#1E2430", "header": "#2D3748",
                "select": "#3B82F6", "danger": "#EF4444", "danger2": "#B91C1C",
            },
            "dark": {
                "name": "深色", "bg": "#10141C", "fg": "#F1F5F9", "panel": "#1E2430", "panel2": "#262D3D",
                "border": "#475569", "accent": "#3B82F6", "accent2": "#60A5FA", "muted": "#94A3B8",
                "button": "#2A3242", "button_hover": "#374151", "table": "#161B24", "header": "#242C3A",
                "select": "#3B82F6", "danger": "#EF4444", "danger2": "#B91C1C",
            },
            "jiansz": {
                "name": "经典风", "bg": "#10141C", "fg": "#F1F5F9", "panel": "#1E2430", "panel2": "#262D3D",
                "border": "#475569", "accent": "#3B82F6", "accent2": "#60A5FA", "muted": "#94A3B8",
                "button": "#2A3242", "button_hover": "#374151", "table": "#161B24", "header": "#242C3A",
                "select": "#3B82F6", "danger": "#EF4444", "danger2": "#B91C1C",
            },
            "campus": {
                "name": "校园风", "bg": "#10141C", "fg": "#F1F5F9", "panel": "#1E2430", "panel2": "#262D3D",
                "border": "#475569", "accent": "#3B82F6", "accent2": "#60A5FA", "muted": "#94A3B8",
                "button": "#2A3242", "button_hover": "#374151", "table": "#161B24", "header": "#242C3A",
                "select": "#3B82F6", "danger": "#EF4444", "danger2": "#B91C1C",
            },
            "fresh": {
                "name": "清新风", "bg": "#10141C", "fg": "#F1F5F9", "panel": "#1E2430", "panel2": "#262D3D",
                "border": "#475569", "accent": "#10B981", "accent2": "#34D399", "muted": "#94A3B8",
                "button": "#2A3242", "button_hover": "#374151", "table": "#161B24", "header": "#242C3A",
                "select": "#10B981", "danger": "#EF4444", "danger2": "#B91C1C",
            },
            "fantasy": {
                "name": "暖色幻想风", "bg": "#10141C", "fg": "#F1F5F9", "panel": "#1E2430", "panel2": "#262D3D",
                "border": "#475569", "accent": "#F59E0B", "accent2": "#FBBF24", "muted": "#94A3B8",
                "button": "#2A3242", "button_hover": "#374151", "table": "#161B24", "header": "#242C3A",
                "select": "#F59E0B", "danger": "#EF4444", "danger2": "#B91C1C",
            },
        }
        if theme not in palettes:
            theme = "jiansz"
        pal = palettes[theme]
        self.config.theme = theme
        self.theme_label.setText(f"当前主题：{pal['name']}")
        if hasattr(self, "theme_combo"):
            self._applying_theme = True
            idx = self.theme_combo.findData(theme)
            if idx >= 0:
                self.theme_combo.setCurrentIndex(idx)
            self._applying_theme = False

        bg_path = self._existing_asset_path("bg", self._theme_asset_folder(theme), "bg_01.png")
        if hasattr(self, "root_widget") and hasattr(self.root_widget, "set_background_image"):
            self.root_widget.set_background_image(str(bg_path) if bg_path else None)

        self.setStyleSheet(f"""
            QWidget {{
                background-color: transparent;
                color: {pal['fg']};
                font-size: 13px;
                font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", system-ui, sans-serif;
            }}
            QWidget#RootWidget {{
                background-color: transparent;
            }}
            QGroupBox {{
                border: 1px solid rgba(140, 175, 230, 135);
                border-radius: 14px;
                margin-top: 10px;
                padding-top: 12px;
                background-color: rgba(255, 255, 255, 45);
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 8px 0 8px;
                color: {pal['muted']};
                font-weight: bold;
                font-size: 12.5px;
            }}
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(48, 56, 72, 220), stop:1 rgba(38, 44, 58, 220));
                color: #F1F5F9;
                border: 1px solid rgba(75, 88, 115, 160);
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(65, 75, 98, 240), stop:1 rgba(50, 60, 80, 240));
                border-color: #3B82F6;
                color: #FFFFFF;
            }}
            QPushButton:pressed {{
                background-color: rgba(59, 130, 246, 50);
                padding-top: 7px;
                padding-bottom: 5px;
            }}
            QPushButton:disabled {{
                color: #64748B;
                background: rgba(30, 36, 48, 120);
                border-color: rgba(60, 70, 90, 80);
            }}
            QComboBox, QSpinBox, QLineEdit {{
                background-color: rgba(24, 28, 38, 240);
                color: #F1F5F9;
                border: 1px solid rgba(65, 78, 102, 180);
                border-radius: 6px;
                padding: 4px 10px;
                min-height: 26px;
            }}
            QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{
                border: 1.5px solid #3B82F6;
                background-color: rgba(30, 36, 48, 255);
            }}
            QCheckBox {{
                background: transparent;
                color: #E2E8F0;
                spacing: 6px;
                font-size: 13px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 1.5px solid rgba(130, 160, 220, 210);
                border-radius: 5px;
                background-color: rgba(30, 36, 48, 245);
            }}
            QCheckBox::indicator:hover {{
                border-color: #3B82F6;
                background-color: rgba(45, 54, 72, 255);
            }}
            QCheckBox::indicator:checked {{
                border: 1.5px solid #3B82F6;
                background-color: #3B82F6;
            }}
            QTableWidget, QTableView {{
                background-color: rgba(20, 24, 34, 235);
                alternate-background-color: rgba(26, 31, 42, 235);
                color: #F1F5F9;
                border: 1px solid rgba(55, 65, 85, 160);
                border-radius: 8px;
                gridline-color: rgba(45, 55, 75, 100);
                selection-background-color: rgba(59, 130, 246, 50);
                selection-color: #FFFFFF;
                outline: none;
            }}
            QTableWidget::item, QTableView::item {{
                padding: 6px 10px;
                border: none;
            }}
            QTableWidget::item:selected, QTableView::item:selected {{
                background-color: rgba(59, 130, 246, 45);
                color: #60A5FA;
                font-weight: bold;
            }}
            QTableWidget::item:hover, QTableView::item:hover {{
                background-color: rgba(59, 130, 246, 25);
            }}
            QHeaderView::section {{
                background-color: rgba(28, 34, 48, 245);
                color: #93C5FD;
                border: none;
                border-bottom: 1px solid rgba(60, 75, 105, 180);
                padding: 7px 10px;
                font-weight: 700;
                font-size: 12.5px;
            }}
            QTextEdit {{
                background-color: rgba(20, 28, 44, 238);
                color: #79E89E;
                font-family: "Cascadia Code", "Fira Code", Consolas, monospace;
                font-size: 12.5px;
                border: 1px solid rgba(80, 120, 190, 140);
                border-radius: 12px;
                padding: 8px;
                selection-background-color: #3B82F6;
            }}
            QHeaderView::section {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(238, 245, 255, 245), stop:1 rgba(225, 236, 252, 245));
                color: #273E61;
                border: 1px solid rgba(183, 199, 230, 130);
                padding: 6px;
                font-weight: 700;
            }}
            QScrollBar:vertical {{
                border: none;
                background: rgba(240, 245, 255, 100);
                width: 8px;
                border-radius: 4px;
                margin: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(140, 175, 230, 180);
                border-radius: 4px;
                min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(100, 145, 220, 240);
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar:horizontal {{
                border: none;
                background: rgba(240, 245, 255, 100);
                height: 8px;
                border-radius: 4px;
                margin: 2px;
            }}
            QScrollBar::handle:horizontal {{
                background: rgba(140, 175, 230, 180);
                border-radius: 4px;
                min-width: 24px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: rgba(100, 145, 220, 240);
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}
            QMenuBar, QMenu {{
                background-color: rgba(255, 255, 255, 245);
                color: {pal['fg']};
                border: 1px solid rgba(183, 199, 230, 160);
                border-radius: 8px;
                padding: 4px;
            }}
            QMenu::item:selected {{
                background-color: {pal['select']};
                color: #1A3865;
                border-radius: 6px;
            }}
            QPushButton#TaskAdvancedToggleButton {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255,255,255,230), stop:1 rgba(235,244,255,230));
                color: #2D4E7B;
                border: 1px solid {pal['border']};
                border-radius: 9px;
                padding: 0px;
                font-weight: 700;
            }}
            QPushButton#TaskAdvancedToggleButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(235,244,255,245), stop:1 rgba(215,233,255,245));
                border-color: {pal['accent']};
                color: #1A3865;
            }}
            QFrame#StatusCharacterContainer {{
                background-color: rgba(255, 255, 255, 60);
                border: 1.5px solid rgba(160, 195, 240, 140);
                border-radius: 20px;
            }}
            QFrame#StatusDashboardCard {{
                background-color: rgba(255, 255, 255, 185);
                border: 1.5px solid rgba(160, 195, 240, 170);
                border-radius: 14px;
            }}
            QLabel#StatusCharacterImage {{
                border: none;
                background: transparent;
                color: {pal['accent']};
            }}
            QLabel#StatusCharacterSpeechBubble {{
                color: {pal['fg']};
                background: transparent;
                font-weight: 700;
                padding: 0px;
            }}
            QDialog#SettingsDialog {{
                background-color: rgba(236,246,255,250);
                color: {pal['fg']};
            }}
            QDialog#SettingsDialog QGroupBox {{
                background-color: rgba(255,255,255,185);
                border: 1px solid rgba(138,183,226,160);
                border-radius: 14px;
                margin-top: 12px;
                padding-top: 14px;
            }}
            QDialog#SettingsDialog QLabel, QDialog#SettingsDialog QCheckBox {{
                color: {pal['fg']};
                background: transparent;
                font-size: 14px;
            }}
            QDialog#SettingsDialog QSpinBox, QDialog#SettingsDialog QComboBox {{
                color: {pal['fg']};
                background-color: rgba(255,255,255,245);
                border: 1px solid rgba(138,183,226,180);
                border-radius: 8px;
                padding: 6px 10px;
                min-height: 28px;
            }}
            QMessageBox {{
                background-color: rgba(236,246,255,252);
                color: #1F344D;
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 14px;
            }}
            QMessageBox QLabel {{
                color: #1F344D;
                background: transparent;
                font-size: 14px;
                font-weight: 600;
                padding: 4px;
            }}
            QMessageBox QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFFFFF, stop:1 #EAF3FF);
                color: #2D5892;
                border: 1px solid rgba(138,183,226,180);
                border-radius: 9px;
                padding: 7px 18px;
                min-width: 72px;
                min-height: 30px;
                font-weight: 700;
            }}
            QMessageBox QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #EAF3FF, stop:1 #D4E7FF);
                border-color: rgba(79,138,217,230);
            }}
        """)

        # 确保启动中心核心控制按钮保持 Fluent 纯蓝与单色深色科技质感
        if hasattr(self, "start_button"):
            self.start_button.setStyleSheet("""
                QPushButton {
                    background-color: #2563EB;
                    color: #FFFFFF;
                    font-size: 13.5px;
                    font-weight: 700;
                    border: 1px solid #3B82F6;
                    border-radius: 8px;
                    padding: 0 18px;
                }
                QPushButton:hover {
                    background-color: #1D4ED8;
                    border-color: #60A5FA;
                }
                QPushButton:disabled {
                    background-color: rgba(37, 99, 235, 100);
                    color: rgba(255, 255, 255, 140);
                    border-color: rgba(59, 130, 246, 80);
                }
            """)

        ctrl_btn_style = """
            QPushButton {
                background-color: rgba(38, 48, 64, 200);
                color: #F1F5F9;
                border: 1px solid rgba(80, 100, 130, 140);
                border-radius: 8px;
                padding: 0 12px;
                font-size: 12.5px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(56, 70, 96, 240);
                border-color: rgba(100, 130, 175, 180);
                color: #FFFFFF;
            }
            QPushButton:disabled {
                background-color: rgba(30, 36, 48, 120);
                color: #64748B;
                border-color: rgba(60, 72, 90, 80);
            }
        """

        for btn_name in ["resume_button", "pause_button", "stop_button", "emergency_stop_button", "cancel_shutdown_button"]:
            if hasattr(self, btn_name):
                getattr(self, btn_name).setStyleSheet(ctrl_btn_style)


    def _current_startup_command(self) -> str:
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        return '"{}" "{}"'.format(sys.executable, self.base_dir / "main.py")

    def _apply_windows_startup(self, enabled: bool) -> None:
        if platform.system().lower() != "windows":
            return
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, self._current_startup_command())
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
        except Exception as exc:
            self.append_log(f"更新 Windows 开机自启动失败：{exc}")


    def export_config(self) -> None:
        """导出一份适合分享的配置文件。"""
        try:
            tasks = self.collect_tasks_from_table(sort_by_order=True)
            export_config = AppConfig(
                theme=self.config.theme,
                shutdown_after_done=self.shutdown_checkbox.isChecked(),
                shutdown_delay_seconds=int(self.shutdown_delay_spin.value()),
                auto_exit_after_done=self.auto_exit_checkbox.isChecked(),
                auto_start_tasks=self.auto_start_tasks_checkbox.isChecked(),
                windows_startup=False,  # 分享配置不携带开机自启动，避免导入者误开。
                tasks=tasks,
            )

            default_path = str(self.base_dir / "auto_script_config_share.json")
            path, _ = QFileDialog.getSaveFileName(
                self,
                "导出配置",
                default_path,
                "JSON 配置文件 (*.json);;所有文件 (*.*)",
            )
            if not path:
                return

            with Path(path).open("w", encoding="utf-8") as f:
                json.dump(export_config.to_dict(), f, ensure_ascii=False, indent=2)
            self.append_log(f"配置已导出：{path}")
            self._show_themed_message("导出成功", f"配置已导出：\n{path}")
        except Exception as exc:
            self._show_themed_message("导出失败", f"导出配置失败：{exc}", QMessageBox.Icon.Critical)
            self.append_log(f"导出配置失败：{exc}")

    def import_config(self) -> None:
        """导入别人分享的配置文件，并刷新到当前界面。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入配置",
            str(self.base_dir),
            "JSON 配置文件 (*.json);;所有文件 (*.*)",
        )
        if not path:
            return

        try:
            with Path(path).open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("配置文件顶层结构不是对象")

            imported = AppConfig.from_dict(data)

            # 分享配置只导入“任务执行配置”，不覆盖本机行为设置。
            # 这些本机设置包括：自动关机、自动退出、开机自启动、全局截图开关、主题等。
            # 这样可以避免别人分享的配置让本机意外启用自动关机等系统级行为。
            local_config = self.config
            self.config = AppConfig(
                theme=local_config.theme,
                shutdown_after_done=local_config.shutdown_after_done,
                shutdown_delay_seconds=local_config.shutdown_delay_seconds,
                auto_exit_after_done=local_config.auto_exit_after_done,
                auto_start_tasks=local_config.auto_start_tasks,
                windows_startup=local_config.windows_startup,
                enable_timeout_screenshot=local_config.enable_timeout_screenshot,
                tasks=imported.tasks,
            )

            self._load_config_to_ui()
            self.config_manager.save(self.config)
            self.append_log(f"配置已导入并保存：{path}")
            self.append_log("导入说明：仅导入任务配置；自动关机、开机自启动、截图开关等本机设置保持不变。")
            if self.has_usable_task_config():
                self.show_temporary_status_character_message("配置导入完成啦～可以先试着开始执行。", 10, priority=30)
            else:
                self.show_first_config_guide_if_needed()
            self._show_themed_message(
                "导入成功",
                "配置已导入。\n已仅导入任务配置；自动关机、开机自启动、截图开关等本机设置保持不变。",
            )
        except Exception as exc:
            self._show_themed_message("导入失败", f"导入配置失败：{exc}", QMessageBox.Icon.Critical)
            self.append_log(f"导入配置失败：{exc}")

    def open_external_url(self, url: str) -> None:
        try:
            webbrowser.open(url)
            self.append_log(f"已打开链接：{url}")
        except Exception as exc:
            self._show_themed_message("打开失败", f"无法打开链接：{exc}", QMessageBox.Icon.Warning)

    def open_local_documentation(self) -> None:
        docs_path = self.base_dir / LOCAL_DOC_RELATIVE_PATH
        if not docs_path.exists():
            self._show_themed_message(
                "说明文档不存在",
                f"未找到本地说明文档：{docs_path}\n\n请确认 docs/index.html 已与程序一起放置。",
            )
            return
        try:
            if platform.system().lower() == "windows":
                os.startfile(str(docs_path))  # type: ignore[attr-defined]
            else:
                webbrowser.open(docs_path.as_uri())
            self.append_log(f"已打开本地说明文档：{docs_path}")
        except Exception as exc:
            self._show_themed_message("打开失败", f"无法打开说明文档：{exc}", QMessageBox.Icon.Warning)

    def _show_themed_message(self, title: str, text: str, icon=QMessageBox.Icon.Information) -> None:
        """统一浅色玻璃风提示框，避免系统暗色 QMessageBox 导致文字不可读。"""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(icon)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        self._prepare_popup_dialog(box)
        box.adjustSize()
        self._center_popup_on_screen(box)
        box.exec()

    def _ask_themed_yes_no(self, title: str, text: str, icon=QMessageBox.Icon.Question) -> bool:
        box = QMessageBox(self)
        self._prepare_popup_dialog(box)
        box.setWindowTitle(title)
        box.setIcon(icon)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.adjustSize()
        self._center_popup_on_screen(box)
        return box.exec() == QMessageBox.StandardButton.Yes

    def show_donation_dialog(self) -> None:
        """显示本地赞赏码图片。把图片放到程序目录 donation/ 下即可。"""
        donation_dir = self.base_dir / "donation"
        candidates = []
        for name in [
            "wechat_reward.png", "wechat_reward.jpg", "wechat_reward.jpeg",
            "reward.png", "reward.jpg", "reward.jpeg",
            "wechat.png", "wechat.jpg", "wechat.jpeg",
            "alipay.png", "alipay.jpg", "alipay.jpeg",
            "donate.png", "donate.jpg", "donate.jpeg",
        ]:
            path = donation_dir / name
            if path.exists():
                candidates.append(path)

        if not candidates:
            self._show_themed_message(
                "赞赏支持",
                "请先在程序目录下创建 donation 文件夹，并放入微信赞赏码图片。\n\n"
                "推荐文件名：\n"
                "wechat_reward.png（首选）/ reward.png / wechat.png",
            )
            return

        dialog = QDialog(self)
        dialog.setObjectName("DonationDialog")
        dialog.setWindowTitle("赞赏支持")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        title = QLabel("☕ 赞赏支持 / Sponsor")
        title.setObjectName("DonationTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        tip = QLabel(
            "此程序为免费开源项目。若你为软件本体付了钱，请立即退款。\n"
            "赞赏完全自愿，用于支持作者继续开发与维护。\n"
            "赞赏前请优先点击下方 GitHub / B站按钮确认官方来源，警惕二次打包篡改收款码。"
        )
        tip.setObjectName("DonationTip")
        tip.setWordWrap(True)
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tip)

        for image_path in candidates[:2]:
            label = QLabel()
            label.setObjectName("DonationImage")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap(str(image_path))
            if not pixmap.isNull():
                pixmap = pixmap.scaled(360, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                label.setPixmap(pixmap)
            else:
                label.setText(f"无法读取图片：{image_path.name}")
            layout.addWidget(label)

        link_layout = QHBoxLayout()
        github_button = QPushButton("打开 GitHub 官方仓库")
        bilibili_button = QPushButton("打开作者 B站主页")
        close_button = QPushButton("关闭")
        for btn in [github_button, bilibili_button, close_button]:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        github_button.clicked.connect(lambda: self.open_external_url(GITHUB_URL))
        bilibili_button.clicked.connect(lambda: self.open_external_url(AUTHOR_URL))
        close_button.clicked.connect(dialog.accept)
        link_layout.addWidget(github_button)
        link_layout.addWidget(bilibili_button)
        link_layout.addWidget(close_button)
        layout.addLayout(link_layout)

        dialog.setStyleSheet("""
            QDialog#DonationDialog {
                background-color: rgba(236, 246, 255, 245);
                color: #1F344D;
            }
            QLabel#DonationTitle {
                color: #315F9A;
                font-size: 22px;
                font-weight: 900;
                background: transparent;
            }
            QLabel#DonationTip {
                color: #31506F;
                background-color: rgba(255, 255, 255, 176);
                border: 1px solid rgba(138, 183, 226, 150);
                border-radius: 14px;
                padding: 12px;
                line-height: 150%;
            }
            QLabel#DonationImage {
                background-color: rgba(255, 255, 255, 130);
                border: 1px solid rgba(138, 183, 226, 130);
                border-radius: 18px;
                padding: 10px;
                color: #31506F;
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 185);
                color: #315F9A;
                border: 1px solid rgba(138, 183, 226, 150);
                border-radius: 9px;
                padding: 8px 13px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: rgba(221, 239, 255, 220);
                border: 1px solid rgba(88, 145, 207, 190);
            }
        """)
        dialog.resize(580, 680)
        dialog.exec()


    def _record_abnormal_report(self, message: str, task_name: str = "") -> None:
        """v31：把运行中识别到的异常写入下次启动汇报文件。"""
        clean_message = str(message or "").strip()
        if not clean_message:
            return
        key = clean_message[:240]
        if key in self._reported_abnormal_messages:
            return
        self._reported_abnormal_messages.add(key)
        report_path = self.base_dir / "logs" / "last_abnormal_report.json"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"events": []}
            if report_path.exists():
                try:
                    data = json.loads(report_path.read_text(encoding="utf-8"))
                except Exception:
                    data = {"events": []}
            events = data.setdefault("events", [])
            severity = "warning" if ("提醒" in clean_message or "配置不匹配" in clean_message) else "error"
            events.append({
                "time": QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss"),
                "task_name": task_name or getattr(self, "current_task_name", "-") or "当前任务",
                "message": clean_message,
                "severity": severity,
                "log_file": self.file_logger.log_path().name if hasattr(self, "file_logger") else "",
                "log_session": getattr(getattr(self, "file_logger", None), "session_label", ""),
            })
            report_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self.append_log(f"写入下次异常报告失败：{exc}")



    def _classic_dialog_stylesheet(self) -> str:
        return """
            QDialog, QMessageBox {
                background-color: rgba(236, 246, 255, 248);
                color: #1F344D;
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QLabel {
                color: #1F344D;
                background: transparent;
                font-size: 14px;
                font-weight: 600;
                padding: 4px;
            }
            QPushButton {
                background-color: rgba(255,255,255,225);
                color: #315F9A;
                border: 1px solid rgba(138,183,226,170);
                border-radius: 9px;
                padding: 7px 18px;
                min-width: 76px;
                min-height: 30px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: rgba(221,239,255,235);
                border-color: rgba(88,145,207,210);
            }
        """

    def _center_popup_on_screen(self, dialog: QDialog) -> None:
        try:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            frame = dialog.frameGeometry()
            frame.moveCenter(geo.center())
            dialog.move(frame.topLeft())
        except Exception:
            pass

    def _prepare_popup_dialog(self, dialog: QDialog, *, modal: bool = True, stay_on_top: bool = True) -> None:
        dialog.setStyleSheet(self._classic_dialog_stylesheet())
        if modal:
            dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        if stay_on_top:
            dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

    def show_startup_problem_report_if_needed(self) -> None:
        """v30：启动时只汇报上一次运行产生的异常报告。

        正常运行不会主动弹窗；异常项包括启动失败、过早退出、超时强退、
        监控的游戏/窗口/进程没有出现等。弹出后删除报告，避免每次启动重复打扰。
        """
        report_path = self.base_dir / "logs" / "last_abnormal_report.json"
        if not report_path.exists():
            return

        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
            events = data.get("events", [])
        except Exception as exc:
            self.append_log(f"读取上次异常报告失败：{exc}")
            return

        if not events:
            try:
                report_path.unlink()
            except Exception:
                pass
            return

        def brief_reason(message: str, severity: str) -> str:
            if "疑似过早退出" in message:
                return "快速退出（未达到 1/6 预期时长）"
            if "已超时" in message or "超时" in message:
                return "超时退出" + ("，已尝试保存现场截图" if "截图" in message else "")
            if "启动失败" in message:
                reason = message.split("启动失败", 1)[-1].strip("，：: ") or "启动失败"
                return "启动失败" if len(reason) > 60 else f"启动失败：{reason}"
            if "未检测到窗口标题关键词" in message:
                return "运行后未检测到目标窗口，可能是窗口标题关键词配置不匹配" if severity == "warning" else "脚本启动后，目标游戏/窗口未启动"
            if "未检测到指定进程" in message:
                return "运行后未检测到目标进程，可能是目标进程名配置不匹配" if severity == "warning" else "脚本启动后，目标进程未出现"
            if "未检测到命令行关键词" in message:
                return "运行后未检测到命令行关键词，可能是关键词配置不匹配" if severity == "warning" else "脚本启动后，目标命令行关键词未出现"
            return message

        error_lines = []
        warning_lines = []
        for event in events:
            index = len(error_lines) + len(warning_lines) + 1
            task_name = str(event.get("task_name") or f"进程{index}")
            message = str(event.get("message") or "未知异常")
            severity = str(event.get("severity") or "error")
            line = f"{task_name}：{brief_reason(message, severity)}"
            if severity == "warning":
                warning_lines.append(line)
            else:
                error_lines.append(line)

        parts = ["上次运行报告："]
        if error_lines:
            parts.append("\n异常：")
            parts.extend(f"{i}. {line}" for i, line in enumerate(error_lines, start=1))
        if warning_lines:
            parts.append("\n提醒：")
            parts.extend(f"{i}. {line}" for i, line in enumerate(warning_lines, start=1))
        log_file = str(data.get("log_file") or (events[0].get("log_file") if events else "") or "")
        if log_file:
            parts.append(f"\n对应日志：{log_file}")
        parts.append("\n正常完成的任务不会在这里汇报。")
        msg = "\n".join(parts)
        
        dialog = StartupReportDialog(msg, report_path.parent, self)
        dialog.exec()

        try:
            report_path.unlink()
        except Exception as exc:
            self.append_log(f"删除已读异常报告失败：{exc}")

    @Slot(int)
    def show_shutdown_warning_dialog(self, delay_seconds: int = 60) -> None:
        """任务完成后的可取消关机提示。

        v28.8：不再静默强制关机。先发送 60 秒/配置秒数关机命令，
        同时弹出置顶警告窗口；“取消关机”才会执行 shutdown /a，
        “关闭弹窗”或不操作都会继续倒计时关机。
        """
        if platform.system().lower() != "windows":
            self.append_log("当前非 Windows 环境，自动关机提示仅记录，不发送关机命令。")
            self._show_themed_message("自动关机", "当前非 Windows 环境，未发送关机命令。")
            return

        delay = max(1, int(delay_seconds or 60))
        self.shutdown_warning_remaining = delay
        try:
            subprocess.Popen(
                ["shutdown", "/s", "/t", str(delay)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
            self.append_log(f"已发送延迟关机命令：shutdown /s /t {delay}")
        except Exception as exc:
            self._show_themed_message("关机失败", f"发送关机命令失败：{exc}", QMessageBox.Icon.Critical)
            self.append_log(f"发送关机命令失败：{exc}")
            return

        if self.shutdown_warning_dialog is not None:
            try:
                self.shutdown_warning_dialog.close()
            except Exception:
                pass

        dialog = QDialog(self)
        dialog.setWindowTitle("即将自动关机")
        dialog.setObjectName("ShutdownWarningDialog")
        self._prepare_popup_dialog(dialog)

        layout = QVBoxLayout(dialog)
        title = QLabel("⚠ 1分钟后将关机" if delay == 60 else f"⚠ {delay} 秒后将关机")
        title.setStyleSheet("font-size: 20px; font-weight: 800; color: #B3261E;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        body = QLabel("若您仍需使用电脑，请点击『取消关机』。\n点击『关闭弹窗』或不进行任何操作，将继续倒计时关机。")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setStyleSheet("font-size: 14px; line-height: 1.5;")
        layout.addWidget(body)

        countdown = QLabel(f"剩余：{self.shutdown_warning_remaining} 秒")
        countdown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        countdown.setStyleSheet("font-size: 18px; font-weight: 700; color: #7A1E1E;")
        layout.addWidget(countdown)
        self.shutdown_warning_countdown_label = countdown

        buttons = QHBoxLayout()
        cancel_button = QPushButton("取消关机")
        close_button = QPushButton("关闭弹窗")
        cancel_button.setStyleSheet("background-color: #B3261E; color: white; font-weight: 700; padding: 8px 16px; border-radius: 8px;")
        close_button.setStyleSheet("padding: 8px 16px; border-radius: 8px;")
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(close_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        cancel_button.clicked.connect(self._cancel_shutdown_from_warning)
        close_button.clicked.connect(dialog.close)

        self.shutdown_warning_dialog = dialog
        if self.shutdown_warning_timer is None:
            self.shutdown_warning_timer = QTimer(self)
            self.shutdown_warning_timer.timeout.connect(self._tick_shutdown_warning)
        self.shutdown_warning_timer.start(1000)

        dialog.resize(460, 250)
        self._center_popup_on_screen(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _tick_shutdown_warning(self) -> None:
        self.shutdown_warning_remaining = max(0, self.shutdown_warning_remaining - 1)
        if self.shutdown_warning_countdown_label is not None:
            self.shutdown_warning_countdown_label.setText(f"剩余：{self.shutdown_warning_remaining} 秒")
        if self.shutdown_warning_remaining <= 0 and self.shutdown_warning_timer is not None:
            self.shutdown_warning_timer.stop()

    def _cancel_shutdown_from_warning(self) -> None:
        self.cancel_shutdown()
        if self.shutdown_warning_timer is not None:
            self.shutdown_warning_timer.stop()
        if self.shutdown_warning_dialog is not None:
            self.shutdown_warning_dialog.close()
            self.shutdown_warning_dialog = None

    def cancel_shutdown(self) -> None:
        if platform.system().lower() != "windows":
            self.append_log("【提示】取消关机指令主要面向 Windows 系统。")
            return

        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.Popen(
                ["shutdown", "/a"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=creationflags,
            )
            self.append_log("【系统】已发送取消自动关机指令 (shutdown /a)。")
        except Exception as exc:
            self.append_log(f"取消关机指令发送失败：{exc}")

    def open_directory(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            if platform.system().lower() == "windows":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif platform.system().lower() == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            self._show_themed_message("打开失败", f"无法打开目录：{exc}", QMessageBox.Icon.Warning)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.is_running:
            if not self._ask_themed_yes_no(
                "任务正在运行",
                "当前任务还在运行。退出程序会尝试停止当前脚本，确定要退出吗？",
                QMessageBox.Icon.Warning,
            ):
                event.ignore()
                return

            if self.runner_worker is not None:
                self.runner_worker.request_stop()

        self.append_log("程序退出。")
        event.accept()


