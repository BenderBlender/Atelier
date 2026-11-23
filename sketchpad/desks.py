"""Independent drawing surfaces with one native input router."""
from dataclasses import replace
import uuid
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QHBoxLayout, QTabBar, QStackedWidget, QInputDialog
from .board import Board
from .widgets import button, tool_icon


def board_property(name, default):
    return property(lambda self: getattr(self.board, '_desk_'+name, default),
                    lambda self, value: setattr(self.board, '_desk_'+name, value))


class Desks:
    path = board_property('path', None)
    dirty = board_property('dirty', False)
    revision = board_property('revision', 0)
    autosaved_revision = board_property('autosaved_revision', -1)

    def build_desks(self, outer, header):
        self.reference_windows = {}
        self.boards = [self.board]
        self.board.desk_id = uuid.uuid4().hex
        self.board.desk_name = 'Стол 1'
        self.desk_serial = 1
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(4)
        self.desk_tabs = QTabBar()
        self.desk_tabs.setObjectName('deskTabs')
        self.desk_tabs.setStyleSheet('QTabBar::tab { min-width: 80px; padding: 7px 8px; } QTabBar::scroller { width: 46px; } QTabBar QToolButton { padding: 0; border-radius: 4px; }')
        self.desk_tabs.setExpanding(False)
        self.desk_tabs.setUsesScrollButtons(True)
        self.desk_tabs.setMinimumWidth(100)
        self.desk_tabs.setTabsClosable(True)
        self.desk_tabs.setDrawBase(False)
        self.desk_tabs.setMovable(True)
        self.desk_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.desk_tabs.addTab(self.board.desk_name)
        self.desk_tabs.currentChanged.connect(self.switch_desk)
        self.desk_tabs.tabCloseRequested.connect(self.close_desk)
        self.desk_tabs.tabBarDoubleClicked.connect(self.rename_desk)
        self.desk_tabs.tabMoved.connect(self.move_desk)
        row.addWidget(self.desk_tabs, 1)
        add = button('+', self.new_desk, 'Новый независимый стол · Ctrl+Shift+N')
        add.setFixedWidth(34)
        add.setAccessibleName('Новый стол')
        row.addWidget(add)
        self.reference_button = button('', self.open_reference, 'Открыть текущий стол отдельным окном поверх всех приложений')
        self.reference_button.setIcon(tool_icon('reference'))
        self.reference_button.setAccessibleName('Плавающая доска референсов')
        self.reference_button.setFixedWidth(34)
        row.addWidget(self.reference_button)
        header.insertWidget(self.desk_header_position, bar, 1)
        from .navigation import SheetNavigator
        self.sheet_navigator = SheetNavigator(self)
        outer.addWidget(self.sheet_navigator)
        self.board_stack = QStackedWidget()
        self.board_stack.addWidget(self.board)

    def move_desk(self, source, destination):
        board = self.boards.pop(source)
        self.boards.insert(destination, board)
        self.update_desk_titles()

    def connect_board(self, board):
        board.changed.connect(lambda: self.desk_changed(board))
        board.mask_changed.connect(lambda: self.assistant.refresh_mask() if self.board is board and hasattr(self, 'assistant') else None)
        for signal, callback in ((board.selection_changed, self.refresh_selection),
                                 (board.tool_changed, self.refresh_tool),
                                 (board.rotation_changed, self.rotation_changed),
                                 (board.brush_size_step, self.adjust_brush_size),
                                 (board.telemetry, self.telemetry)):
            signal.connect(lambda *args, b=board, fn=callback: fn(*args) if self.board is b else None)
        board.zoom_changed.connect(lambda v: self.zoom_label.setText(f'{v*100:.0f}%') if self.board is board else None)
        board.message.connect(lambda s: self.statusBar().showMessage(s, 14000) if self.board is board else None)

    def desk_changed(self, board):
        board._desk_dirty = True
        board._desk_revision = getattr(board, '_desk_revision', 0)+1
        self.update_desk_titles()
        if board is self.board and (not board.dual or board.dual_before is None):
            self.refresh_selection()

    def update_desk_titles(self):
        for i, board in enumerate(self.boards):
            path = getattr(board, '_desk_path', None)
            name = board.desk_name
            self.desk_tabs.setTabText(i, name+(' •' if getattr(board, '_desk_dirty', False) else ''))
            self.desk_tabs.setTabToolTip(i, str(path) if path else name+' · двойной щелчок, чтобы переименовать')
        name = self.board.desk_name
        self.title_label.setText(name+(' · изменён' if self.dirty else ''))
        from .app import APP_TITLE
        self.setWindowTitle(APP_TITLE+' — '+name+(' *' if self.dirty else ''))
        for window in self.reference_windows.values():
            window.setWindowTitle('Референсы · '+window.source.desk_name+' · Atelier')
            window.caption.setText(window.source.desk_name)

    def open_reference(self, checked=False, board=None):
        from .reference_window import ReferenceWindow
        from PySide6.QtCore import QTimer
        board = board or self.board
        window = self.reference_windows.get(board.desk_id)
        fresh = window is None
        if fresh:
            window = ReferenceWindow(self, board)
            self.reference_windows[board.desk_id] = window
            area = self.screen().availableGeometry()
            window.move(max(area.left(), min(self.x()+90, area.right()-window.width())),
                        max(area.top(), min(self.y()+110, area.bottom()-window.height())))
        window.sync()
        window.show()
        window.raise_()
        window.activateWindow()
        window.focus_target().setFocus()
        if fresh:
            QTimer.singleShot(0, window.canvas.fit)
        return window

    def sync_reference(self, board):
        window = self.reference_windows.get(board.desk_id)
        if window:
            window.sync()

    def new_desk(self, checked=False):
        board = Board()
        board.brush = replace(self.board.brush)
        board.brush_preset = self.board.brush_preset
        board.eraser_size = self.board.eraser_size
        board.desk_id = uuid.uuid4().hex
        self.desk_serial += 1
        board.desk_name = f'Стол {self.desk_serial}'
        self.boards.append(board)
        self.board_stack.addWidget(board)
        self.connect_board(board)
        for action in self.board_actions:
            board.addAction(action)
        self.desk_tabs.addTab(board.desk_name)
        self.desk_tabs.setCurrentIndex(len(self.boards)-1)
        board.fit()
        return board

    def switch_desk(self, index):
        if not 0 <= index < len(self.boards) or self.board is self.boards[index]:
            return
        previous = self.board
        previous.pointer_up()
        previous.flush_dual_move()
        previous.set_dual(False)
        previous.space = False
        previous.native_pen = False
        self.raw.active = False
        self.board = self.boards[index]
        self.board_stack.setCurrentWidget(self.board)
        self.pen_mouse_filter.board = self.board
        # A contact begun on the old surface must be lifted before it can draw here.
        self.native_tablet.must_lift = self.native_tablet.must_lift or self.native_tablet.down
        self.native_tablet.down = self.native_tablet.captured = False
        self.native_tablet.board = self.board
        self.board.native_pen = self.native_tablet.enabled
        self.preview.board = self.board
        self.sync_board_controls()
        self.update_desk_titles()
        self.board.setFocus()

    def sync_board_controls(self):
        self.presets.blockSignals(True)
        self.presets.setCurrentText(self.board.brush_preset)
        self.presets.blockSignals(False)
        for key, (slider, _, factor, _) in self.controls.items():
            value = getattr(self.board, key) if key in ('mouse_gain', 'eraser_size') else getattr(self.board.brush, key)
            slider.blockSignals(True)
            slider.setValue(round(value*factor))
            slider.blockSignals(False)
            self.set_slider_label(key, slider.value())
        for control, value in ((self.snap_check, self.board.snap),
                               (self.visible_check, self.board.guide_visible),
                               (self.extensions_check, self.board.extensions)):
            control.blockSignals(True)
            control.setChecked(value)
            control.blockSignals(False)
        self.eraser_shape.blockSignals(True)
        self.eraser_shape.setCurrentIndex(max(0, self.eraser_shape.findData(self.board.brush.eraser_shape)))
        self.eraser_shape.blockSignals(False)
        self.controls['eraser_size'][0].setEnabled(self.board.brush.eraser_shape == 'round')
        self.dual_button.setChecked(False)
        self.dual_button.setText('Ручное управление')
        self.update_dual_text()
        self.refresh_color_buttons()
        self.refresh_selection()
        self.refresh_tool(self.board.tool)
        self.rotation_changed(self.board.view_rotation)
        self.zoom_label.setText(f'{self.board.zoom*100:.0f}%')
        self.preview.refresh()
        if hasattr(self, 'assistant'):
            self.assistant.refresh_source()

    def rename_desk(self, index):
        if not 0 <= index < len(self.boards):
            return
        board = self.boards[index]
        name, ok = QInputDialog.getText(self, 'Название стола', 'Название', text=board.desk_name)
        if ok and name.strip():
            board.desk_name = name.strip()[:80]
            self.update_desk_titles()

    def close_desk(self, index):
        if len(self.boards) == 1:
            self.statusBar().showMessage('Это последний стол. Кнопка + добавляет новый.', 5000)
            return
        target = self.boards[index]
        reference = self.reference_windows.get(target.desk_id)
        if reference:
            reference.canvas.pointer_up()
        if hasattr(self, 'assistant') and self.assistant.uses_board(target):
            self.statusBar().showMessage('Сначала дождитесь помощника или отмените его задачу.', 7000)
            return
        self.desk_tabs.setCurrentIndex(index)
        if not self.safe_to_replace():
            return
        self.desk_tabs.setCurrentIndex(0 if index else 1)
        self.desk_tabs.blockSignals(True)
        self.boards.pop(index)
        self.desk_tabs.removeTab(index)
        self.desk_tabs.setCurrentIndex(self.boards.index(self.board))
        self.desk_tabs.blockSignals(False)
        self.board_stack.removeWidget(target)
        reference = self.reference_windows.pop(target.desk_id, None)
        if reference:
            reference.canvas.pointer_up()
            target.history.clear()
            reference.hide()
            reference.deleteLater()
        target.deleteLater()
        self.update_desk_titles()

