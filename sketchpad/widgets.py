"""Small, pen-friendly interface components."""
import time
from PySide6.QtCore import QByteArray, Qt, QEvent
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


class PenButton(QPushButton):
    """Activate on tablet release without depending on driver mouse promotion."""
    def __init__(self, text):
        super().__init__(text)
        self.pen_pressed = False
        self.pen_event_at = 0.
        self.click_modifiers = None

    def tabletEvent(self, event):
        self.pen_event_at = time.monotonic()
        inside = self.rect().contains(event.position().toPoint())
        if event.type() == QEvent.Type.TabletPress:
            self.pen_pressed = self.isEnabled() and inside and event.button() == Qt.MouseButton.LeftButton
            if self.pen_pressed:
                self.setDown(True)
        elif event.type() == QEvent.Type.TabletMove and self.pen_pressed:
            self.setDown(inside)
        elif event.type() == QEvent.Type.TabletRelease:
            activate = self.pen_pressed and inside and self.isEnabled()
            self.pen_pressed = False
            self.setDown(False)
            if activate:
                if self.menu():
                    self.showMenu()
                else:
                    self.click_modifiers = event.modifiers()
                    try:
                        self.click()
                    finally:
                        self.click_modifiers = None
        event.accept()

    def mousePressEvent(self, event):
        if self.pen_pressed or (event.source() != Qt.MouseEventSource.MouseEventNotSynthesized and time.monotonic()-self.pen_event_at < .5):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.pen_pressed or (event.source() != Qt.MouseEventSource.MouseEventNotSynthesized and time.monotonic()-self.pen_event_at < .5):
            event.accept()
            return
        self.click_modifiers = event.modifiers()
        try:
            super().mouseReleaseEvent(event)
        finally:
            self.click_modifiers = None

    def event(self, event):
        if event.type() in (QEvent.Type.Hide, QEvent.Type.WindowDeactivate, QEvent.Type.EnabledChange):
            self.pen_pressed = False
            self.setDown(False)
        return super().event(event)


def button(text, callback, tooltip='', checkable=False):
    widget = PenButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setCheckable(checkable)
    widget.setToolTip(tooltip)
    widget.clicked.connect(callback)
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return widget


class Foldout(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName('foldout')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toggle = QToolButton()
        self.toggle.setObjectName('foldoutToggle')
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(self.toggle)
        self.body = QWidget()
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(12, 4, 12, 14)
        self.content.setSpacing(10)
        layout.addWidget(self.body)
        self.body.hide()
        self.toggle.toggled.connect(self.set_expanded)

    def set_expanded(self, expanded):
        self.body.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)


def tool_icon(name):
    paths = {
        'pencil': '<path d="m4 16-1 5 5-1L20 8l-4-4Zm9-9 4 4M4 16l4 4M16 4l2-2 4 4-2 2"/>',
        'eraser': '<path d="m4 13 8-10a2 2 0 0 1 3 0l6 5a2 2 0 0 1 0 3l-8 10H8l-4-4a3 3 0 0 1 0-4ZM9 7l9 8M13 21h9"/>',
        'select': '<path d="m4 3 15 9-7 1-3 7Z"/>',
        'hand': '<path d="M8 12V6a2 2 0 0 1 4 0v6-8a2 2 0 0 1 4 0v8-5a2 2 0 0 1 4 0v8c0 4-2 6-6 6h-2c-2 0-3-1-4-2l-5-6a2 2 0 0 1 3-3l2 2"/>',
        'rotate': '<path d="M4 9a8 8 0 1 1 0 7M4 3v6h6"/><path d="m10 10 5-1 1 5-5 1Z"/>',
        'pin': '<circle cx="12" cy="8" r="5"/><path d="M12 13v9M10 6h2"/>',
        'band': '<circle cx="5" cy="18" r="3"/><circle cx="19" cy="6" r="3"/><path d="m7 16 10-8"/>',
        'perspective': '<path d="m12 2 9 5v10l-9 5-9-5V7Zm0 10L3 7m9 5 9-5m-9 5v10M7 4l10 16M3 12l9 5 9-5"/>',
        'mask_add': '<path stroke-dasharray="2 3" d="M3 13V3h18v18H11"/><path d="m3 21 1-5L14 6l4 4-10 10Zm10-13 4 4"/>',
        'clear': '<path d="M5 3h9l5 5v13H5ZM14 3v6h5M9 12l6 6m0-6-6 6"/>',
        'undo': '<path d="M9 5 3 10l6 5M3 10h10a7 7 0 0 1 0 14" transform="translate(0 -3)"/>',
        'redo': '<path d="m15 5 6 5-6 5m6-5h-10a7 7 0 0 0 0 14" transform="translate(0 -3)"/>',
        'panels': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M16 4v16"/>',
        'sheet': '<path d="M5 3h9l5 5v13H5ZM14 3v6h5M8 13h8M8 17h6"/>',
        'folder': '<path d="M3 6h7l2 3h9v12H3ZM3 6V4h7l2 2h9v3"/>',
        'reference': '<rect x="3" y="7" width="14" height="14" rx="2"/><path d="M7 7V3h14v14h-4M9 15l9-9m-5 0h5v5"/>',
        'more': '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    }
    icon = QIcon()
    for state, color in ((QIcon.State.Off, '#b9c0bc'), (QIcon.State.On, '#202522')):
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g fill="none" stroke="{color}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{paths[name]}</g></svg>'
        pixmap = QPixmap(48, 48)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        QSvgRenderer(QByteArray(svg.encode())).render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(2)
        icon.addPixmap(pixmap, QIcon.Mode.Normal, state)
    return icon
