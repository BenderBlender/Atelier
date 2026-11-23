"""A second live viewport on a desk, with its own camera and shared undo history."""
import math
from PySide6.QtCore import Qt, QPointF, QRectF, QRect, QSize, QEvent, Signal
from PySide6.QtGui import QKeySequence, QColor, QPainter
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QMenu, QFileDialog, QApplication
from PySide6.QtGui import QImage
from pathlib import Path
from .board import Board
from .widgets import button, label, tool_icon
from .sheet_clipboard import copy_sheets, paste_sheets, duplicate_sheets
from .model import Paper, draw_paper_content


class ReferenceCanvas(Board):
    focus_requested = Signal(object)

    def __init__(self, source):
        self.key_commands = {}
        super().__init__()
        self.source = self.history_owner = source
        self.history.deleteLater()
        self.history = source.history
        self.doc = source.doc
        self.desk_id = source.desk_id
        self.tool, self.guide_visible = 'select', False
        self.selected = source.selected
        self.selected_paper_ids = set(source.selected_paper_ids)
        self.refresh_cursor()
        source.changed.connect(self.sync)

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride and event.keyCombination().toCombined() in self.key_commands:
            # A tool window shares a parent with the editor, but owns these keys.
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        callback = self.key_commands.get(event.keyCombination().toCombined())
        if callback:
            callback()
            event.accept()
        else:
            super().keyPressEvent(event)

    def sync(self):
        replaced = self.doc is not self.source.doc
        if replaced:
            self.pointer_up()
            self.doc = self.source.doc
            self.set_selection(self.doc.papers[-1] if self.doc.papers else None)
            self.fit()
        elif self.selected not in self.doc.papers:
            self.set_selection(None)
        self.update()

    def fit(self):
        self.focused_paper_id = None
        bounds = QRectF()
        for paper in self.doc.papers:
            bounds = bounds.united(paper.transform().mapRect(QRectF(0, 0, paper.width, paper.height)))
        if bounds.isEmpty():
            return
        self.view_rotation = 0
        self.zoom = max(.06, min(2., min((self.width()-48)/bounds.width(), (self.height()-64)/bounds.height())))
        self.offset = QPointF(self.width()/2, self.height()/2)-bounds.center()*self.zoom
        self.zoom_changed.emit(self.zoom)
        self.update()

    def pointer_down(self, pos, button, modifiers, pen=False, **kwargs):
        # Empty space moves the reference desk. Shift + drag selects a group.
        if (button == Qt.MouseButton.LeftButton and not modifiers and
                self.paper_at(self.to_world(pos)) is None):
            self.setFocus()
            self.drag = ('pan', pos, QPointF(self.offset))
            self.drag_source, self.drag_button = ('pen' if pen else 'mouse'), button
            self.refresh_cursor()
            return
        super().pointer_down(pos, button, modifiers, pen, **kwargs)

    def pointer_move(self, *args, **kwargs):
        super().pointer_move(*args, **kwargs)
        if self.drag and self.drag[0] in ('object', 'papers', 'resize'):
            self.source.update()

    def pen_sample(self, kind, pos, pressure, tx, ty, eraser=False, device='', modifiers=Qt.KeyboardModifier.NoModifier):
        # Reference manipulation is independent of the drawing tool in the main window.
        from PySide6.QtCore import QEvent
        if kind == QEvent.Type.TabletPress:
            self.pointer_down(pos, Qt.MouseButton.LeftButton, modifiers, True)
        elif kind == QEvent.Type.TabletMove:
            self.pointer_move(pos, pen=True, modifiers=modifiers)
        elif kind == QEvent.Type.TabletRelease:
            self.pointer_up(pen=True)

    def draw_hud(self, painter):
        painter.setPen(QColor('#9da8a0'))
        painter.drawText(12, self.height()-14, 'Двойной щелчок — фокус · Колесо — масштаб · F — всё')

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.ignore_mouse(event):
            paper = self.paper_at(self.to_world(event.position()))
            if paper:
                self.pointer_up()
                self.set_selection(paper)
                self.focus_requested.emit(paper)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class FocusedReference(QWidget):
    """Image-only view. Its gestures never reach the editable board."""
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.drag_anchor = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TabletTracking, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip('Перетащить — двигать · Колесо — размер\nДвойной щелчок или Esc — вернуться к столу')

    def paintEvent(self, event):
        paper = self.owner.focus_paper
        if paper is None:
            return
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(self.width()/paper.width, self.height()/paper.height)
        draw_paper_content(painter, paper, self.owner.canvas.perspective_renderer.draw)
        for layer in paper.guide_layers:
            if layer.visible and layer.opacity > 0:
                painter.setOpacity(layer.opacity)
                painter.drawImage(0, 0, layer.image)
        painter.end()

    def begin_drag(self, global_pos):
        self.setFocus()
        self.drag_anchor = QPointF(global_pos)
        self.drag_origin = self.owner.pos()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def move_drag(self, global_pos):
        if self.drag_anchor is not None:
            self.owner.move(self.drag_origin+(global_pos-self.drag_anchor).toPoint())

    def end_drag(self):
        self.drag_anchor = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.source() == Qt.MouseEventSource.MouseEventNotSynthesized and event.button() == Qt.MouseButton.LeftButton:
            self.begin_drag(event.globalPosition())
        event.accept()

    def mouseMoveEvent(self, event):
        if event.source() == Qt.MouseEventSource.MouseEventNotSynthesized:
            self.move_drag(event.globalPosition())
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.source() == Qt.MouseEventSource.MouseEventNotSynthesized and event.button() == Qt.MouseButton.LeftButton:
            self.end_drag()
        event.accept()

    def tabletEvent(self, event):
        if event.type() == QEvent.Type.TabletPress:
            self.begin_drag(event.globalPosition())
        elif event.type() == QEvent.Type.TabletMove:
            self.move_drag(event.globalPosition())
        elif event.type() == QEvent.Type.TabletRelease:
            self.end_drag()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.owner.exit_focus()
        event.accept()

    def wheelEvent(self, event):
        if self.drag_anchor is None:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            self.owner.zoom_focus(math.exp(max(-600, min(600, delta))*.0015), event.globalPosition())
        event.accept()

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            # Keep editor shortcuts away from the hidden, editable reference canvas.
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.owner.exit_focus()
        elif event.matches(QKeySequence.StandardKey.Copy):
            copy_sheets(self.owner.canvas)
        elif event.matches(QKeySequence.StandardKey.Save):
            self.owner.save_source()
        event.accept()

    def focusOutEvent(self, event):
        self.end_drag()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self.end_drag()
        super().hideEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction('Вернуться к столу · Esc', lambda: self.owner.exit_focus())
        top = menu.addAction('Поверх других окон')
        top.setCheckable(True)
        top.setChecked(self.owner.top.isChecked())
        top.toggled.connect(self.owner.top.setChecked)
        top.toggled.connect(self.owner.toggle_top)
        menu.addAction('Копировать изображение · Ctrl+C', lambda: copy_sheets(self.owner.canvas))
        menu.addSeparator()
        menu.addAction('Скрыть референсы', self.owner.close)
        menu.exec(event.globalPos())


class ReferenceWindow(QWidget):
    def __init__(self, window, source):
        # Independent top-level tool: minimizing Atelier must not hide references.
        # The desk registry owns it and closes it when the source/main window closes.
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.window, self.source = window, source
        self.focus_paper = self.focus_state = None
        # Set before creating the native window: Windows needs a translucent,
        # frameless surface to display PNGs without an opaque rectangle behind them.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName('inspectorPage')
        self.setMinimumSize(340, 260)
        self.resize(620, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(4)
        self.toolbar = QWidget()
        row = QHBoxLayout(self.toolbar)
        row.setContentsMargins(0, 0, 0, 0)
        self.caption = label('', 'muted')
        row.addWidget(self.caption, 1)
        self.canvas = ReferenceCanvas(source)
        self.canvas.focus_requested.connect(self.enter_focus)
        row.addWidget(button('Фокус', self.enter_focus, 'Только выбранное изображение · двойной щелчок'))
        row.addWidget(button('В кадр', self.canvas.fit, 'Показать всё · F'))
        self.top = button('Поверх', self.toggle_top, 'Оставлять окно поверх других приложений', True)
        self.top.setChecked(True)
        row.addWidget(self.top)
        row.addWidget(button('↗', self.show_desk, 'Перейти к этому столу в Atelier'))
        more = button('', self.menu, 'Добавить изображение, копировать, вставить')
        more.setIcon(tool_icon('more'))
        more.setFixedWidth(34)
        row.addWidget(more)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        self.focus_view = FocusedReference(self)
        self.focus_view.hide()
        layout.addWidget(self.focus_view, 1)
        self.status = label('Двойной щелчок по изображению — фокус без рамки.', 'muted')
        layout.addWidget(self.status)
        self.canvas.message.connect(self.status.setText)
        for key, callback in {'F': self.canvas.fit, 'Ctrl+C': lambda: copy_sheets(self.canvas),
                              'Ctrl+V': lambda: paste_sheets(self.canvas), 'Ctrl+D': lambda: duplicate_sheets(self.canvas),
                              'Ctrl+Z': self.undo, 'Ctrl+Shift+Z': self.redo, 'Ctrl+Y': self.redo,
                              'Ctrl+S': self.save_source, 'Ctrl+I': self.import_images,
                              'Delete': self.canvas.delete_selected}.items():
            self.canvas.key_commands[QKeySequence(key)[0].toCombined()] = callback
        source.changed.connect(self.sync_focus)
        self.sync()

    def paintEvent(self, event):
        # WA_TranslucentBackground is permanent for the native window. Paint the
        # ordinary chrome explicitly so its empty areas stay opaque after return.
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor('#202523') if self.focus_paper is None else Qt.GlobalColor.transparent)
        painter.end()

    def sync(self):
        self.caption.setText(self.source.desk_name)
        self.setWindowTitle('Референсы · '+self.source.desk_name+' · Atelier')
        self.canvas.sync()
        self.sync_focus()

    def focus_target(self):
        return self.focus_view if self.focus_paper is not None else self.canvas

    def enter_focus(self, paper=None):
        if not isinstance(paper, Paper):  # QPushButton passes its checked state.
            paper = self.canvas.selected
        if not isinstance(paper, Paper) or paper not in self.source.doc.papers:
            self.status.setText('Сначала выберите изображение для фокуса.')
            return
        if self.focus_paper is not None:
            return
        self.canvas.pointer_up()
        self.canvas.set_selection(paper)
        self.focus_state = (QRect(self.geometry()), self.windowState(), self.minimumSize(), self.styleSheet())
        self.focus_paper = paper
        center = QPointF(self.canvas.mapToGlobal(self.canvas.rect().center()))
        area = self.screen().availableGeometry()
        scale = min(max(160/max(paper.width, paper.height), self.canvas.zoom*paper.scale),
                    area.width()*.9/paper.width, area.height()*.9/paper.height)
        size = QSize(max(1, round(paper.width*scale)), max(1, round(paper.height*scale)))
        self.hide()
        self.setWindowState(Qt.WindowState.WindowNoState)
        self.toolbar.hide()
        self.status.hide()
        self.canvas.hide()
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)
        self.setMinimumSize(1, 1)
        self.setStyleSheet('QWidget#inspectorPage { background: transparent; border: none; }')
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        self.focus_view.show()
        self.setGeometry(QRect((center-QPointF(size.width()/2, size.height()/2)).toPoint(), size))
        self.show()
        self.raise_()
        self.activateWindow()
        self.focus_view.setFocus()

    def exit_focus(self, show=True):
        if self.focus_paper is None:
            return
        geometry, state, minimum, stylesheet = self.focus_state
        was_active = self.isActiveWindow()
        self.focus_view.end_drag()
        self.hide()
        self.focus_paper = self.focus_state = None
        self.focus_view.hide()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
        self.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, False)
        self.setStyleSheet(stylesheet)
        self.layout().setContentsMargins(6, 6, 6, 4)
        self.layout().setSpacing(4)
        self.toolbar.show()
        self.status.show()
        self.canvas.show()
        self.setMinimumSize(minimum)
        self.setGeometry(geometry)
        self.setWindowState(state)
        if show:
            self.show()
            if was_active:
                self.raise_()
                self.activateWindow()
            self.canvas.setFocus()

    def zoom_focus(self, factor, global_pos):
        paper = self.focus_paper
        if paper is None:
            return
        # Resize the floating picture around the cursor. Never change paper.scale
        # or the reference desk's saved camera, even for locked sheets.
        longest = max(paper.width, paper.height)
        scale = max(96/longest, min(4096/longest, self.width()/paper.width*factor))
        size = QSize(max(1, round(paper.width*scale)), max(1, round(paper.height*scale)))
        anchor = global_pos-QPointF(self.pos())
        origin = global_pos-QPointF(anchor.x()*size.width()/self.width(), anchor.y()*size.height()/self.height())
        self.setGeometry(QRect(origin.toPoint(), size))

    def sync_focus(self):
        if self.focus_paper is not None:
            if self.focus_paper not in self.source.doc.papers:
                self.exit_focus(show=self.isVisible())
            else:
                self.focus_view.update()

    def toggle_top(self, checked):
        geometry = self.geometry()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
        self.setGeometry(geometry)
        self.show()
        self.activateWindow()
        self.focus_target().setFocus()

    def show_desk(self):
        self.window.desk_tabs.setCurrentIndex(self.window.boards.index(self.source))
        self.window.showNormal() if self.window.isMinimized() else self.window.show()
        self.window.activateWindow()
        self.source.setFocus()

    def undo(self):
        self.canvas.pointer_up()
        self.source.pointer_up()
        self.source.history.undo()

    def save_source(self):
        self.canvas.pointer_up()
        previous = self.window.board
        self.window.desk_tabs.setCurrentIndex(self.window.boards.index(self.source))
        try:
            self.window.save_project()
        finally:
            if previous in self.window.boards:
                self.window.desk_tabs.setCurrentIndex(self.window.boards.index(previous))

    def redo(self):
        self.canvas.pointer_up()
        self.source.pointer_up()
        self.source.history.redo()

    def import_images(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Добавить референсы', '', 'Изображения (*.png *.jpg *.jpeg *.webp *.bmp)')
        for path in paths:
            self.canvas.add_reference(QImage(path), Path(path).stem)

    def menu(self):
        menu = QMenu(self)
        menu.addAction('Добавить изображения…', self.import_images)
        menu.addAction('Копировать · Ctrl+C', lambda: copy_sheets(self.canvas))
        menu.addAction('Вставить · Ctrl+V', lambda: paste_sheets(self.canvas))
        menu.addAction('Дублировать · Ctrl+D', lambda: duplicate_sheets(self.canvas))
        menu.addAction('Сохранить исходный стол · Ctrl+S', self.save_source)
        menu.addSeparator()
        menu.addAction('Отменить · Ctrl+Z', self.undo)
        menu.exec(self.mapToGlobal(self.rect().topRight()))

    def closeEvent(self, event):
        self.exit_focus(show=False)
        self.canvas.pointer_up()
        self.hide()
        event.ignore()
