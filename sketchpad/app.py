import json
import os
from pathlib import Path
import sys
import time
from dataclasses import asdict, replace
from PySide6.QtCore import Qt, QTimer, QPointF, QEvent, QSize, QRectF, QThreadPool
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QPixmap, QCursor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QFrame, QSlider, QFileDialog, QMessageBox, QSpinBox, QDialog,
    QTextBrowser, QMenu, QListWidgetItem, QSizePolicy, QLineEdit, QPlainTextEdit, QTextEdit)
from .board import Board, Change
from .brush import PRESETS, GraphiteStroke, empty_ink, BrushSettings
from .brush_library import BrushLibrary, FIRST_BRUSH
from .model import Document, Paper, Pin, Band, PAPER_STYLES, paper_brush, save_document, load_document, export_paper
from .raw_input import RawMouse, PenMouseFilter
from .tablet import NativeTablet, tablet_capabilities
from .widgets import button, label, tool_icon
from .panels import InspectorPanels
from .desks import Desks
from .assistant_panel import AssistantPanel, saved_receipts, restore_receipts
from .inpaint import MASK_TOOLS

ROOT = Path(__file__).resolve().parent.parent
APP_TITLE = 'Atelier 0.17.1'
DATA = ROOT / 'data'
if '--input-check' in sys.argv:
    DATA = DATA / f'input-check-{os.getpid()}'
STYLE = '''
QMainWindow, QDialog { background: #1d2322; color: #dce2da; }
QWidget { font-family: "Segoe UI"; font-size: 12px; color: #e0e4df; }
QFrame#topbar { background: #1c211f; border-bottom: 1px solid #303632; }
QFrame#sidebar { background: #202523; border-left: 1px solid #303632; }
QFrame#rail { background: #1c211f; border-right: 1px solid #303632; }
QLabel#eyebrow { color: #919e94; font-size: 10px; font-weight: 600; letter-spacing: 2px; }
QLabel#muted { color: #929f96; font-size: 11px; }
QLabel#brand { font-size: 15px; color: #d6c6aa; font-weight: 600; letter-spacing: 3px; }
QLabel#panelTitle { font-size: 20px; font-weight: 600; color: #eceee8; padding-bottom: 3px; }
QPushButton, QToolButton { background: #2e3530; border: 1px solid #3b443d; border-radius: 8px; padding: 8px 12px; }
QPushButton:hover, QToolButton:hover { background: #3b473e; border-color: #66715c; }
QPushButton:checked, QToolButton:checked { background: #bda678; color: #202621; border-color: #d1bb8e; }
QPushButton:disabled { color: #626d64; }
QPushButton#primary { background: #bda678; color: #202621; border-color: #d1bb8e; font-weight: 600; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: #2a302c; border: 1px solid #3b443d; border-radius: 7px; padding: 7px; min-height: 20px; selection-background-color: #647255; }
QComboBox QAbstractItemView { background: #2b332f; color: #dce2da; selection-background-color: #4b594a; }
QSlider::groove:horizontal { height: 3px; background: #465044; border-radius: 1px; }
QSlider::sub-page:horizontal { background: #bba679; }
QSlider::handle:horizontal { background: #ddc89c; border: none; width: 16px; height: 16px; margin: -7px 0; border-radius: 8px; }
QSlider::sub-page:horizontal:disabled, QSlider::handle:horizontal:disabled { background: #566052; }
QCheckBox { spacing: 8px; padding: 3px 0; }
QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid #697461; border-radius: 3px; background: #2b332f; }
QCheckBox::indicator:checked { background: #bda678; border: 1px solid #e0c89c; }
QScrollArea, QWidget#inspectorPage { border: none; background: #202523; }
QScrollBar:vertical { background: #202523; width: 6px; margin: 0; }
QScrollBar::handle:vertical { background: #4b564b; border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QProgressBar { background: #354034; border: none; border-radius: 2px; height: 4px; }
QProgressBar::chunk { background: #c3ae81; border-radius: 2px; }
QStatusBar { background: #1b211f; color: #98a395; font-size: 11px; }
QMenu { background: #28302b; border: 1px solid #4d594c; padding: 5px; }
QMenu::item { padding: 7px 24px; }
QMenu::item:selected { background: #4a5745; }
QTextBrowser { background: #252c27; border: 1px solid #444f41; padding: 14px; }
QToolTip { background: #e7dfce; color: #262c25; border: 1px solid #bfa97b; padding: 5px; }
QPushButton#tool { background: transparent; border-color: transparent; padding: 0; border-radius: 12px; }
QPushButton#tool:hover { background: #323b34; }
QPushButton#tool:checked { background: #c2ab80; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #292f2b; color: #9fa9a1; padding: 10px 17px; border: none; border-radius: 7px; margin: 0 2px 0 2px; }
QTabBar::tab:selected { background: #424c40; color: #f0e7d6; }
QTabBar::tab:hover { background: #363e35; }
QFrame#foldout { background: #282f2a; border: 1px solid #363e35; border-radius: 10px; }
QToolButton#foldoutToggle { background: transparent; border: none; padding: 12px 10px; color: #b3bdb2; }
QToolButton#foldoutToggle:checked { color: #e3d3b4; }
QListWidget#sheets { background: transparent; color: #e0e4df; border: none; outline: 0; }
QListWidget#sheets::item { background: #2a322c; border: 1px solid #3b453c; border-radius: 9px; padding: 8px; }
QListWidget#sheets::item:selected { background: #3b473a; color: #f0e7d6; border-color: #a28f6c; }
QListWidget#sheets::item:hover { background: #343e34; }
QListWidget#bookmarks { background: transparent; border: none; outline: 0; }
QListWidget#bookmarks::item { background: #282f2b; border: 1px solid #3b443c; border-radius: 6px; padding: 0 8px; }
QListWidget#bookmarks::item:selected { background: #424c40; border-color: #ae996e; color: #f0e7d6; }
QListWidget#bookmarks::item:hover { background: #394337; }
'''



class BrushPreview(QWidget):
    def __init__(self, board):
        super().__init__()
        self.board = board
        self.setFixedHeight(66)
        self.img = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.img = empty_ink(240, 66)
        settings = self.board.stroke_settings()
        self.rendered_style = self.paper_style()
        if settings.eraser:
            # A sample pigment layer makes subtractive strokes visible in the preview.
            color = QColor(45, 43, 40, 200) if self.rendered_style == 'ivory' else QColor(255, 255, 255, 200)
            painter = QPainter(self.img)
            painter.fillRect(8, 6, 224, 54, color)
            painter.end()
        stroke = GraphiteStroke(self.img, settings)
        import math
        for i in range(208):
            stroke.add(16+i, 33+math.sin(i*.024)*8, .15+.7*(i/208), 12, 65*(i/208))
        self.update()

    def paper_style(self):
        paper = self.board.selected
        if not isinstance(paper, Paper) or paper.kind != 'paper':
            paper = next((item for item in self.board.doc.papers if item.kind == 'paper'), None)
        return paper.paper_style if paper else 'ivory'

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(paper_brush(self.paper_style()))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 5, 5)
        if self.img:
            p.drawImage(self.rect(), self.img)
        p.end()


class MainWindow(Desks, InspectorPanels, QMainWindow):
    def __init__(self, recovery=True, brush_path=None):
        super().__init__()
        self.setWindowTitle(APP_TITLE+' — скетчпад')
        self.resize(1480, 960)
        self.setMinimumSize(1040, 720)
        self.board = Board()
        self._restoring_brush = True
        self.brush_library = BrushLibrary(brush_path or DATA/'brushes.json')
        self.board.brush_preset = self.brush_library.active
        self.board.brush = self.brush_library.get(self.board.brush_preset)
        self.board.brush.eraser_strength = self.brush_library.eraser['strength']
        self.board.brush.eraser_shape = self.brush_library.eraser['shape']
        self.board.eraser_size = self.brush_library.eraser['size']
        self.brush_save_timer = QTimer(self)
        self.brush_save_timer.setSingleShot(True)
        self.brush_save_timer.timeout.connect(self.flush_brush_preferences)
        self.path = None
        self.dirty = False
        self.revision = 0
        self.autosaved_revision = -1
        self.updating = False
        self.pen_stats = {'events': 0, 'max_pressure': 0., 'max_tilt_x': 0., 'max_tilt_y': 0.}
        self.diagnostics_revision = -1
        self.controls = {}
        self.control_rows = {}
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.top = QFrame()
        self.top.setObjectName('topbar')
        self.top.setFixedHeight(58)
        row = QHBoxLayout(self.top)
        row.setContentsMargins(18, 9, 14, 9)
        row.addWidget(label('ATELIER', 'brand'))
        row.addSpacing(6)
        file_button = button('Доска', lambda: None, 'Открыть, сохранить, экспортировать')
        file_menu = QMenu(file_button)
        file_menu.addAction('Открыть…', self.open_project)
        file_menu.addAction('Сохранить', self.save_project)
        file_menu.addAction('Сохранить как…', lambda: self.save_project(save_as=True))
        file_menu.addSeparator()
        file_menu.addAction('Экспорт PNG…', self.export)
        file_menu.addSeparator()
        file_menu.addAction('Как работать · F1', self.help)
        file_button.setMenu(file_menu)
        row.addWidget(file_button)
        add_button = button('+ Добавить', lambda: None)
        add_menu = QMenu(add_button)
        add_menu.addAction('Лист…', self.new_paper)
        add_menu.addAction('Калька · T', lambda: self.board.add_tracing())
        add_menu.addAction('Референс…', self.import_reference)
        add_menu.addAction('Объёмная сетка · K', lambda: self.board.set_tool('perspective'))
        add_button.setMenu(add_menu)
        row.addWidget(add_button)
        self.title_label = label('Новая доска', 'muted')
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title_label.setMinimumWidth(80)
        self.title_label.setMaximumWidth(260)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.hide()
        self.desk_header_position = row.count()
        self.assistant_button = button('✦ Помощник', self.toggle_assistant, 'Доработать скетч в чате', True)
        row.addWidget(self.assistant_button)
        self.view_angle = QSpinBox()
        self.view_angle.setRange(-180, 180)
        self.view_angle.setPrefix('Вид ')
        self.view_angle.setSuffix('°')
        self.view_angle.setFixedWidth(105)
        self.view_angle.setKeyboardTracking(False)
        self.view_angle.setToolTip('Поворот вида всей доски. R + движение пера или мыши; Shift — шаг 15°. Изображения и экспорт не поворачиваются.')
        self.view_angle.valueChanged.connect(lambda v: self.board.set_view_rotation(v))
        row.addWidget(self.view_angle)
        row.addWidget(button('↺', lambda: self.board.reset_view_rotation(), 'Вернуть вид к 0°'))
        self.save_button = button('Сохранить', lambda: self.save_project(), 'Сохранить всю доску · Ctrl+S')
        self.save_button.setObjectName('primary')
        row.addWidget(self.save_button)
        self.panels_button = button('', self.toggle_panels, 'Скрыть / показать панели · Tab', True)
        self.panels_button.setIcon(tool_icon('panels'))
        self.panels_button.setIconSize(QSize(22, 22))
        self.panels_button.setChecked(True)
        self.panels_button.setAccessibleName('Показать панели')
        self.panels_button.setFixedWidth(40)
        row.addWidget(self.panels_button)
        outer.addWidget(self.top)
        self.build_desks(outer, row)
        center = QHBoxLayout()
        center.setSpacing(0)
        self.rail = QFrame()
        self.rail.setObjectName('rail')
        self.rail.setFixedWidth(66)
        rail = QVBoxLayout(self.rail)
        rail.setContentsMargins(9, 6, 9, 8)
        rail.setSpacing(3)
        self.tool_buttons = {}
        for tool, text, tip in [('pencil', 'B\nГрафит', 'Карандаш · B'), ('eraser', 'E\nКлячка', 'Клячка · E · мягко снимает пигмент по фактуре бумаги; работает и обратная сторона пера'), ('select', 'V\nВыбор', 'Перемещение, масштаб и поворот листов · V'), ('hand', 'H\nДоска', 'Перемещение доски · H / пробел'), ('rotate', 'R\nПоворот', 'Повернуть вид пером или мышью. Удерживайте R для временного поворота. Shift — шаг 15°.'), ('pin', 'P\nПин', 'Поставить пин · P'), ('band', 'G\nРезинка', 'Соединить два пина резинкой · G')]:
            w = button('', lambda checked=False, t=tool: self.choose_tool(t), tip, True)
            w.setObjectName('tool')
            w.setIcon(tool_icon(tool))
            w.setIconSize(QSize(24, 24))
            w.setAccessibleName(text.replace('\n', ' · '))
            w.setFixedSize(48, 40)
            rail.addWidget(w)
            self.tool_buttons[tool] = w
        for tool, tip in [('mask_add', 'Inpaint · выделить область для помощника · I'), ('perspective', 'Вращать объёмную сетку · K; удерживайте R — крен камеры')]:
            control = button('', lambda checked=False, t=tool: self.choose_tool(t), tip, True)
            control.setObjectName('tool')
            control.setIcon(tool_icon(tool))
            control.setIconSize(QSize(24, 24))
            control.setFixedSize(48, 40)
            control.setAccessibleName(tip)
            rail.addWidget(control)
            self.tool_buttons[tool] = control
        rail.addStretch()
        self.clear_rail = button('', self.clear_current_paper, 'Очистить рисунок листа · Alt — только выбранный слой направляющих · Ctrl+Z — отмена')
        self.clear_rail.setObjectName('tool')
        self.clear_rail.setIcon(tool_icon('clear'))
        self.clear_rail.setIconSize(QSize(22, 22))
        self.clear_rail.setFixedSize(48, 40)
        self.clear_rail.setAccessibleName('Очистить лист')
        rail.addWidget(self.clear_rail)
        for icon, callback, title in (('undo', self.undo, 'Отменить · Ctrl+Z'), ('redo', self.redo, 'Повторить · Ctrl+Shift+Z')):
            control = button('', callback, title)
            control.setObjectName('tool')
            control.setIcon(tool_icon(icon))
            control.setIconSize(QSize(22, 22))
            control.setAccessibleName(title)
            control.setFixedSize(48, 40)
            rail.addWidget(control)
        self.zoom_label = button('65%', lambda: self.board.fit(), 'Показать всё · F')
        rail.addWidget(self.zoom_label)
        center.addWidget(self.rail)
        center.addWidget(self.board_stack, 1)
        self.sidebar = self.build_sidebar(BrushPreview)
        center.addWidget(self.sidebar)
        self.assistant = AssistantPanel(self, DATA)
        self.assistant.hide()
        self.assistant_positioned = False
        outer.addLayout(center, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage('Готово. B — карандаш, V — перемещение. Все файлы остаются на компьютере.')
        self.connect_board(self.board)
        self.shortcuts()
        self.raw = RawMouse(int(self.winId()), self.route_raw_mouse)
        QApplication.instance().installNativeEventFilter(self.raw)
        self.pen_mouse_filter = PenMouseFilter(self, self.board)
        QApplication.instance().installNativeEventFilter(self.pen_mouse_filter)
        QApplication.instance().installEventFilter(self)
        self.input_label.setText('Мышь: независимый Raw Input готов' if self.raw.available else self.raw.error)
        self.dual_button.setEnabled(self.raw.available)
        self.native_tablet = NativeTablet(self, self.board)
        self.hardware = tablet_capabilities()
        self.pen_backend.blockSignals(True)
        self.pen_backend.setCurrentIndex(0 if self.native_tablet.available else 1)
        self.pen_backend.blockSignals(False)
        self.native_tablet.set_enabled(self.native_tablet.available)
        if not self.native_tablet.available:
            self.pen_backend.setToolTip(self.native_tablet.error+'; работает стандартный ввод.')
        QApplication.instance().aboutToQuit.connect(self.native_tablet.close)
        self.autosave_timer = QTimer(self)
        self.recovery_pool = QThreadPool(self)
        self.recovery_pool.setMaxThreadCount(1)
        self.recovery_job = None
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start(30000)
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.update_input_status)
        self.ui_timer.start(1000)
        self.refresh_selection()
        self.sync_brush_controls()
        self._restoring_brush = False
        self.refresh_tool('pencil')
        QTimer.singleShot(0, self.board.fit)
        if recovery:
            QTimer.singleShot(250, self.offer_recovery)

    def section(self, title):
        self.side.addWidget(label(title, 'eyebrow'))

    def separator(self):
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet('background: #384135;')
        self.side.addSpacing(8)
        self.side.addWidget(line)
        self.side.addSpacing(7)

    def slider(self, title, key, low, high, value, factor, suffix):
        row = QWidget()
        body = QVBoxLayout(row)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        heading = QHBoxLayout()
        heading.addWidget(label(title))
        heading.addStretch()
        val = label('', 'muted')
        heading.addWidget(val)
        body.addLayout(heading)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setValue(value)
        slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        slider.setMinimumHeight(24)
        slider.setAccessibleName(title)
        self.controls[key] = (slider, val, factor, suffix)
        self.control_rows[key] = row
        slider.valueChanged.connect(lambda v, k=key: self.slider_changed(k, v))
        body.addWidget(slider)
        self.side.addWidget(row)
        self.set_slider_label(key, value)

    def set_slider_label(self, key, value):
        _, val, factor, suffix = self.controls[key]
        val.setText((f'{value}' if suffix == '%' else f'{value/factor:g}')+suffix)

    def slider_changed(self, key, value):
        self.set_slider_label(key, value)
        factor = self.controls[key][2]
        if key in ('mouse_gain', 'eraser_size'):
            setattr(self.board, key, value/factor)
        else:
            setattr(self.board.brush, key, value/factor)
        if key != 'mouse_gain':
            self.preview.timer.start(100)
            self.board.update()
            self.remember_brush(eraser=key in ('eraser_size', 'eraser_strength'))

    def change_eraser_shape(self, index):
        self.board.finish_stroke()
        shape = self.eraser_shape.itemData(index)
        self.board.brush.eraser_shape = shape if shape in ('round', 'pencil') else 'round'
        self.controls['eraser_size'][0].setEnabled(self.board.brush.eraser_shape == 'round')
        self.refresh_brush_controls()
        self.preview.timer.start(100)
        self.board.update()
        self.remember_brush(eraser=True)

    def adjust_brush_size(self, direction):
        board = self.board
        if board.tool in ('mask_add', 'mask_remove'):
            slider, factor = self.assistant.mask_size, 1
            title = 'Кисть выделения'
        elif board.tool in ('pencil', 'eraser'):
            erasing = board.tool == 'eraser' or bool(board.stroke and board.stroke.settings.eraser)
            key = 'eraser_size' if erasing and board.brush.eraser_shape == 'round' else 'size'
            slider, _, factor, _ = self.controls[key]
            title = 'Клячка' if erasing else 'Карандаш'
        else:
            return
        value = slider.value()
        new = max(value+1, round(value*1.1)) if direction > 0 else min(value-1, round(value/1.1))
        slider.setValue(new)  # Shares bounds, labels and saved brush preferences.
        if board.stroke and board.tool in ('pencil', 'eraser'):
            board.stroke.settings.size = slider.value()/factor
        self.statusBar().showMessage(f'{title} · {slider.value()/factor:g} px   ·   [ меньше / ] больше', 2000)

    def preset_changed(self, name):
        if name not in PRESETS:
            return
        self.board.finish_stroke()
        self.board.brush_preset = name
        self.board.brush = replace(self.brush_library.get(name), eraser_strength=self.board.brush.eraser_strength,
                                   eraser_shape=self.board.brush.eraser_shape)
        self.sync_brush_controls()
        self.brush_library.select(name)
        self.brush_save_timer.start(400)

    def sync_brush_controls(self):
        self.presets.blockSignals(True)
        self.presets.setCurrentText(self.board.brush_preset)
        self.presets.blockSignals(False)
        for key, (slider, _, factor, _) in self.controls.items():
            if key == 'mouse_gain':
                continue
            value = self.board.eraser_size if key == 'eraser_size' else getattr(self.board.brush, key)
            slider.blockSignals(True)
            slider.setValue(round(value*factor))
            slider.blockSignals(False)
            self.set_slider_label(key, slider.value())
        self.eraser_shape.blockSignals(True)
        self.eraser_shape.setCurrentIndex(max(0, self.eraser_shape.findData(self.board.brush.eraser_shape)))
        self.eraser_shape.blockSignals(False)
        self.controls['eraser_size'][0].setEnabled(self.board.brush.eraser_shape == 'round')
        self.refresh_brush_controls()
        self.refresh_color_buttons()
        self.preview.timer.start(100)
        self.board.update()

    def remember_brush(self, eraser=False):
        if self._restoring_brush:
            return
        if eraser:
            self.brush_library.remember_eraser(self.board.brush, self.board.eraser_size)
        else:
            self.brush_library.remember(self.board.brush_preset, self.board.brush)
        self.brush_library.select(self.board.brush_preset)
        self.brush_save_timer.start(400)

    def flush_brush_preferences(self):
        self.brush_save_timer.stop()
        try:
            self.brush_library.save()
        except OSError:
            self.statusBar().showMessage('Не удалось сохранить настройки кистей на диск. В этом окне они сохранены.', 8000)

    def reset_brush(self):
        self.board.finish_stroke()
        if self.board.tool == 'eraser':
            self.board.eraser_size = 22
            self.board.brush.eraser_strength = .65
            self.board.brush.eraser_shape = 'round'
            self.remember_brush(eraser=True)
            self.sync_brush_controls()
        else:
            self.brush_library.reset(self.board.brush_preset)
            self.preset_changed(self.board.brush_preset)
        self.flush_brush_preferences()
        self.statusBar().showMessage('Исходные настройки выбранной кисти восстановлены', 5000)

    def set_color(self, color):
        self.board.brush.color = color
        self.refresh_color_buttons()
        self.preview.timer.start(100)
        self.remember_brush()

    def refresh_color_buttons(self):
        for color, control in self.color_buttons.items():
            control.setChecked(color == self.board.brush.color)

    def change_paper_style(self, index):
        if not self.updating:
            self.board.set_paper_style(self.paper_combo.itemData(index))

    def rotation_changed(self, angle):
        self.view_angle.blockSignals(True)
        self.view_angle.setValue(round(angle))
        self.view_angle.blockSignals(False)

    def refresh_tool(self, tool):
        for key, btn in self.tool_buttons.items():
            btn.setChecked(key == tool or key == 'mask_add' and tool in MASK_TOOLS)
        self.refresh_brush_controls()
        if tool in ('pencil', 'eraser', 'select', 'pin', 'band'):
            self.sidebar_tabs.setCurrentIndex(0 if tool in ('pencil', 'eraser') else 2 if tool == 'select' else 1)
        elif tool == 'perspective':
            self.sidebar_tabs.setCurrentIndex(3)
        if hasattr(self, 'perspective_panel'):
            self.perspective_panel.sync()

        if hasattr(self, 'guide_panel'):
            self.guide_panel.sync()
        self.preview.timer.start(50)
        if hasattr(self, 'assistant'):
            self.assistant.refresh_mask()

    def choose_tool(self, tool):
        if tool == 'mask_add':
            self.set_assistant_visible(True)
            self.assistant.mode.setCurrentIndex(self.assistant.mode.findData('inpaint'))
            self.assistant.activate_mask(tool)
            return
        self.board.set_tool(tool)
        self.board.setFocus()

    def clear_current_paper(self, checked=False):
        # Read modifiers from the actual release, including tablet events.
        # Canvas focus/Alt drawing state may have changed over the inspector.
        modifiers = getattr(self.sender(), 'click_modifiers', None)
        if modifiers is None:
            modifiers = QApplication.keyboardModifiers()
        self.board.clear_paper(guide=bool(modifiers & Qt.KeyboardModifier.AltModifier))

    def refresh_brush_controls(self):
        erasing = self.board.tool == 'eraser'
        shaped = self.board.brush.eraser_shape == 'pencil'
        self.brush_title.setText('Клячка' if erasing else 'Карандаш')
        self.brush_reset.setToolTip('Сбросить размер, силу и форму клячки' if erasing else 'Сбросить только «'+self.board.brush_preset+'» к исходным настройкам')
        self.eraser_form.setVisible(erasing)
        self.presets.setVisible(not erasing or shaped)
        self.palette.setVisible(not erasing)
        self.control_rows['size'].setVisible(not erasing or shaped)
        self.control_rows['tip_sharpness'].setVisible(not erasing or shaped)
        self.control_rows['tilt_gain'].setVisible(not erasing or shaped)
        self.control_rows['sharpening_length'].setVisible(not erasing or shaped)
        self.control_rows['eraser_size'].setVisible(erasing and not shaped)
        self.control_rows['opacity'].setVisible(not erasing)
        self.control_rows['eraser_strength'].setVisible(erasing)
        self.brush_detail.setVisible(not erasing or shaped)
        self.eraser_hint.setVisible(erasing and shaped)

    def refresh_selection(self):
        self.updating = True
        self.band_combo.blockSignals(True)
        self.band_combo.clear()
        for b in self.board.doc.bands:
            self.band_combo.addItem(b.name, b.id)
        if self.board.band not in self.board.doc.bands:
            self.board.band = self.board.doc.bands[0] if self.board.doc.bands else None
        self.band_combo.setCurrentIndex(self.board.doc.bands.index(self.board.band) if self.board.band else -1)
        self.band_combo.blockSignals(False)
        self.pin_combo.blockSignals(True)
        self.pin_combo.clear()
        for i, pin in enumerate(self.board.doc.pins):
            self.pin_combo.addItem(f'Пин {i+1:02d}', pin.id)
        if self.board.mouse_pin not in self.board.doc.pins:
            self.board.mouse_pin = self.board.doc.pins[0] if self.board.doc.pins else None
        self.pin_combo.setCurrentIndex(self.board.doc.pins.index(self.board.mouse_pin) if self.board.mouse_pin else -1)
        self.pin_combo.setEnabled(self.board.mouse_target_mode == 'pin')
        self.pin_combo.blockSignals(False)
        self.object_list.blockSignals(True)
        scroll_position = self.object_list.verticalScrollBar().value()
        self.object_list.clear()
        selected_row = -1
        for index, sheet in enumerate(reversed(self.board.doc.papers)):
            kind = 'Референс' if sheet.kind == 'reference' else 'Калька' if sheet.tracing else 'Лист'
            item = QListWidgetItem(self.sheet_icon(sheet), sheet.name+'\n'+kind+(' · закреплён' if sheet.locked else ''))
            item.setData(Qt.ItemDataRole.UserRole, sheet.id)
            item.setToolTip(sheet.name+' · '+kind)
            self.object_list.addItem(item)
            if sheet is self.board.selected:
                selected_row = index
        obj = self.board.selected
        paper = obj if isinstance(obj, Paper) and obj in self.board.doc.papers else None
        self.object_list.setCurrentRow(selected_row)
        self.object_list.setFixedHeight(min(220, max(84, len(self.board.doc.papers)*72+8)))
        self.object_list.verticalScrollBar().setValue(scroll_position)
        self.object_list.blockSignals(False)
        for control in (self.angle, self.scale, self.object_name, self.lock_check):
            control.setEnabled(bool(paper))
        self.object_name.setText(paper.name if paper else '')
        self.angle.setValue(round(paper.angle) if paper else 0)
        self.scale.setValue(round(paper.scale*100) if paper else 100)
        self.lock_check.setChecked(paper.locked if paper else False)
        self.tracing_button.setEnabled(bool(paper))
        self.clear_button.setEnabled(bool(paper and paper.kind == 'paper' and not paper.locked))
        self.clear_rail.setEnabled(self.clear_button.isEnabled())
        self.delete_band_button.setEnabled(self.board.band in self.board.doc.bands)
        self.paper_combo.setEnabled(bool(paper and paper.kind == 'paper'))
        self.paper_combo.setCurrentIndex(self.paper_combo.findData(paper.paper_style) if paper and paper.kind == 'paper' else -1)
        if self.preview.rendered_style != self.preview.paper_style():
            self.preview.timer.start(50)
        self.preview.update()
        self.paper_alpha.setEnabled(bool(paper and paper.kind == 'paper'))
        self.ink_alpha.setEnabled(bool(paper))
        self.paper_alpha.setValue(round((1-paper.paper_opacity)*100) if paper else 0)
        self.ink_alpha.setValue(round(paper.ink_opacity*100) if paper else 100)
        self.paper_alpha_label.setText(f'Прозрачность бумаги · {self.paper_alpha.value()}%')
        self.ink_alpha_label.setText(f'Видимость рисунка · {self.ink_alpha.value()}%')
        self.updating = False
        if hasattr(self, 'assistant'):
            self.assistant.refresh_source()
        if hasattr(self, 'sheet_navigator'):
            self.sheet_navigator.refresh()
        if hasattr(self, 'perspective_panel'):
            self.perspective_panel.sync()

        if hasattr(self, 'guide_panel'):
            self.guide_panel.sync()

    def paper_transparency(self, value):
        self.paper_alpha_label.setText(f'Прозрачность бумаги · {value}%')
        self.transform_object('paper_opacity', 1-value/100)

    def ink_visibility(self, value):
        self.ink_alpha_label.setText(f'Видимость рисунка · {value}%')
        self.transform_object('ink_opacity', value/100)

    def choose_pin(self, index):
        if self.updating or not 0 <= index < len(self.board.doc.pins):
            return
        self.board.set_mouse_pin(self.board.doc.pins[index])
        self.board.set_selection(self.board.mouse_pin)

    def change_mouse_target(self, index):
        self.board.set_mouse_target_mode('pin')
        self.pin_combo.setEnabled(True)
        self.update_dual_text()
        self.board.setFocus()

    def update_dual_text(self):
        self.dual_hint.setText('Мышь двигает выбранный пин со всеми его резинками. Space — пауза. Перо рисует отдельно.' if self.board.dual else 'Поставьте пины (P) и соедините их резинками (G). Один пин можно связать с несколькими. M — две руки.')

    def change_pen_backend(self, index):
        if not hasattr(self, 'native_tablet'):
            return
        if index == 0 and not self.native_tablet.available:
            self.pen_backend.setCurrentIndex(1)
            self.statusBar().showMessage(self.native_tablet.error, 12000)
            return
        self.native_tablet.set_enabled(index == 0)
        self.pen_live.setText('Перо: ожидаю касания\nНажим —    Наклон —')
        self.board.setFocus()

    def choose_band(self, index):
        if self.updating or not 0 <= index < len(self.board.doc.bands):
            return
        self.board.flush_dual_move()
        self.board.band = self.board.doc.bands[index]
        self.board.set_selection(self.board.band)

    def choose_object(self, index):
        if self.updating or index < 0:
            return
        item = self.object_list.item(index)
        if item:
            sheet = next((p for p in self.board.doc.papers if p.id == item.data(Qt.ItemDataRole.UserRole)), None)
            if sheet:
                self.board.set_selection(sheet)
        self.board.setFocus()

    def sheet_icon(self, paper):
        key = (paper.image.cacheKey(), paper.width, paper.height, paper.paper_style, paper.kind)
        if getattr(paper, '_thumb_key', None) == key:
            return paper._thumb_icon
        thumb = QPixmap(60, 44)
        thumb.fill(Qt.GlobalColor.transparent)
        painter = QPainter(thumb)
        scale = min(58/paper.width, 42/paper.height)
        rect = QRectF((60-paper.width*scale)/2, (44-paper.height*scale)/2, paper.width*scale, paper.height*scale)
        painter.fillRect(rect, QColor(PAPER_STYLES[paper.paper_style][1]) if paper.kind == 'paper' else QColor('#39423b'))
        painter.drawImage(rect, paper.image)
        painter.end()
        icon = QIcon(thumb)
        icon.addPixmap(thumb, QIcon.Mode.Selected)
        paper._thumb_key, paper._thumb_icon = key, icon
        return icon

    def rename_object(self):
        paper = self.board.selected
        if not self.updating and isinstance(paper, Paper) and self.object_name.text().strip():
            self.board.push_move(paper, {'name': paper.name}, {'name': self.object_name.text().strip()}, 'Название')

    def transform_object(self, key, value):
        if self.updating:
            return
        paper = self.board.selected
        if isinstance(paper, Paper):
            self.board.finish_stroke()
            self.board.push_move(paper, {key: getattr(paper, key)}, {key: value}, 'Свойства листа')

    def raise_object(self):
        obj = self.board.selected
        if isinstance(obj, Paper) and obj in self.board.doc.papers:
            doc = self.board.doc
            before = list(doc.papers)
            after = [p for p in before if p is not obj]+[obj]
            self.board.history.push(Change(self.board, 'Порядок листов', lambda: setattr(doc, 'papers', list(before)), lambda: setattr(doc, 'papers', list(after))))

    def toggle_dual(self, checked=None):
        if not self.raw.available:
            self.statusBar().showMessage(self.raw.error, 10000)
            return
        active = not self.board.dual
        self.board.set_dual(active)
        self.raw.active = active
        self.dual_button.setChecked(active)
        self.dual_button.setText('Две руки · мышь + перо' if active else 'Ручное управление')
        self.update_dual_text()
        self.board.setFocus()

    def guides_visible(self, value):
        self.board.guide_visible = value
        self.board.update()

    def extensions(self, value):
        self.board.extensions = value
        self.board.update()

    def telemetry(self, data):
        self.pressure.setValue(round(data['pressure']*1000))
        self.device_label.setText(data['device'] or 'Перо Wacom / WinTab')
        self.telemetry_label.setText(f"Нажим {data['pressure']*100:.0f}%    X {data['tx']:+.0f}° / Y {data['ty']:+.0f}°")
        self.pen_live.setText(f"{data['device']}\nНажим {data['pressure']*100:.0f}% · наклон {data['tx']:+.0f}° / {data['ty']:+.0f}°")
        self.pen_stats.update(events=data['events'], source=data['device'],
            max_pressure=max(self.pen_stats['max_pressure'], data['pressure']),
            max_tilt_x=max(self.pen_stats['max_tilt_x'], abs(data['tx'])),
            max_tilt_y=max(self.pen_stats['max_tilt_y'], abs(data['ty'])))

    def update_input_status(self):
        if self.raw.available:
            self.input_label.setText(f'Raw Input · событий мыши: {self.raw.events}')
        if self.native_tablet.enabled or self.pen_stats['events'] != self.diagnostics_revision:
            try:
                DATA.mkdir(parents=True, exist_ok=True)
                (DATA/'pen-diagnostics.json').write_text(json.dumps({'hardware': self.hardware, 'session': self.pen_stats,
                    'native': {'enabled':self.native_tablet.enabled,'active':self.native_tablet.active,
                        'enable_ok':self.native_tablet.enable_ok,'overlap_ok':self.native_tablet.overlap_ok,
                        'packets':self.native_tablet.packets,'polls':self.native_tablet.polls,
                        'max_pressure':self.native_tablet.max_pressure,'max_tilt':self.native_tablet.max_tilt,
                        'last_packet':self.native_tablet.last_packet, 'error':self.native_tablet.error}}, ensure_ascii=False, indent=2), encoding='utf8')
                self.diagnostics_revision = self.pen_stats['events']
            except OSError:
                pass

    def on_change(self):
        self.desk_changed(self.board)

    def shortcuts(self):
        self.board_actions = []
        mapping = {'B': lambda: self.board.set_tool('pencil'), 'E': lambda: self.board.set_tool('eraser'),
            'V': lambda: self.board.set_tool('select'), 'H': lambda: self.board.set_tool('hand'),
            'P': lambda: self.board.set_tool('pin'), 'G': lambda: self.board.set_tool('band'),
            'K': lambda: self.board.set_tool('perspective'),
            'T': lambda: self.board.add_tracing(), 'Ctrl+Shift+Delete': lambda: self.board.clear_paper(),
            'I': lambda: self.choose_tool('mask_add'), 'M': self.toggle_dual, 'F': lambda: self.board.fit(), 'Ctrl+S': self.save_project,
            'Ctrl+Shift+S': lambda: self.save_project(save_as=True), 'Ctrl+O': self.open_project,
            'Ctrl+N': self.new_paper, 'Ctrl+Shift+N': self.new_desk, 'Ctrl+I': self.import_reference, 'Ctrl+V': self.paste_reference,
            'Ctrl+C': self.copy_sheets, 'Ctrl+D': self.duplicate_sheets,
            'Ctrl+Z': lambda: self.undo(text_focus=True), 'Ctrl+Shift+Z': lambda: self.redo(text_focus=True), 'Ctrl+Y': lambda: self.redo(text_focus=True),
            'Delete': lambda: self.board.delete_selected(), 'F1': self.help,
            'Tab': self.toggle_panels, 'Esc': self.escape}
        for key, fn in mapping.items():
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(fn)
            if len(key) == 1 or key in ('Delete', 'Tab', 'Esc'):
                action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
                self.board.addAction(action)
                self.board_actions.append(action)
            else:
                self.addAction(action)

    def escape(self):
        if self.board.dual:
            self.toggle_dual()
        self.board.pending_pin = None
        self.board.set_tool('pencil')

    def toggle_panels(self):
        show = not self.sidebar.isVisible()
        self.sidebar.setVisible(show)
        self.rail.setVisible(show)
        self.panels_button.setChecked(show)
        self.assistant.refresh_mask()
        self.board.setFocus()

    def toggle_assistant(self):
        self.set_assistant_visible(not self.assistant.isVisible())

    def set_assistant_visible(self, show):
        if not show and self.board.tool in MASK_TOOLS:
            self.board.set_tool('pencil')
        if show and not self.assistant_positioned:
            available = self.screen().availableGeometry()
            width = self.assistant.width()
            height = min(820, max(520, available.height()-70))
            self.assistant.resize(width, height)
            # Prefer unused desktop space; otherwise float over canvas while
            # keeping the brush inspector accessible on the same screen.
            x = self.frameGeometry().right()+12
            if x+width > available.right():
                x = self.sidebar.mapToGlobal(self.sidebar.rect().topLeft()).x()-width-12
            x = max(available.left(), min(x, available.right()-width))
            y = max(available.top()+30, min(self.frameGeometry().top()+90, available.bottom()-height-10))
            self.assistant.move(x, y)
            self.assistant_positioned = True
        self.assistant_button.setChecked(show)
        self.assistant.setVisible(show)
        self.assistant.refresh_source()
        if show:
            self.assistant.raise_()
            self.assistant.activateWindow()
            self.assistant.composer.setFocus()
        else:
            self.activateWindow()
            self.board.setFocus()

    def tablet_input_active(self):
        return self.isActiveWindow() or self.assistant.isActiveWindow() or any(w.isActiveWindow() for w in self.reference_windows.values())

    def route_raw_mouse(self, dx, dy):
        # The floating reference desk owns mouse movement while it is under the cursor.
        # Keep the main pen route active independently for two-handed drawing.
        point = QCursor.pos()
        if any(w.isVisible() and (w.canvas.drag or w.focus_view.drag_anchor is not None or
                                 w.frameGeometry().contains(point)) for w in self.reference_windows.values()):
            return
        self.board.raw_move(dx, dy)

    def tablet_point_allowed(self, position):
        point = self.board.mapToGlobal(position.toPoint())
        return not any(w.isVisible() and w.frameGeometry().contains(point) for w in [self.assistant, *self.reference_windows.values()])

    def undo(self, checked=False, text_focus=False):
        editor = QApplication.focusWidget()
        if text_focus and isinstance(editor, (QLineEdit, QPlainTextEdit, QTextEdit)):
            editor.undo()
            return
        if self.board.tool in MASK_TOOLS:
            self.board.pointer_up()
            self.board.inpaint.history.undo()
            return
        self.board.finish_stroke()
        self.board.flush_dual_move()
        self.board.history.undo()

    def redo(self, checked=False, text_focus=False):
        editor = QApplication.focusWidget()
        if text_focus and isinstance(editor, (QLineEdit, QPlainTextEdit, QTextEdit)):
            editor.redo()
            return
        if self.board.tool in MASK_TOOLS:
            self.board.pointer_up()
            self.board.inpaint.history.redo()
            return
        self.board.finish_stroke()
        self.board.history.redo()

    def new_paper(self):
        menu = QMenu(self)
        for title, size in [('Альбомный · 1400 × 1000', (1400, 1000)), ('Портретный · 1000 × 1400', (1000, 1400)), ('Квадрат · 1400 × 1400', (1400, 1400)), ('Большой · 2400 × 1700', (2400, 1700))]:
            menu.addAction(title, lambda checked=False, s=size: self.board.add_paper(*s))
        menu.addSeparator()
        menu.addAction('Калька над выбранным листом', self.board.add_tracing)
        from PySide6.QtGui import QCursor
        menu.exec(QCursor.pos())

    def import_reference(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Добавить референсы', str(ROOT), 'Изображения (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)')
        for path in paths:
            self.board.add_reference(QImage(path), Path(path).stem)

    def paste_reference(self):
        editor = QApplication.focusWidget()
        if isinstance(editor, (QLineEdit, QPlainTextEdit, QTextEdit)):
            editor.paste()
            return
        from .sheet_clipboard import paste_sheets
        paste_sheets(self.board)

    def copy_sheets(self):
        editor = QApplication.focusWidget()
        if isinstance(editor, (QLineEdit, QPlainTextEdit, QTextEdit)):
            editor.copy()
            return
        from .sheet_clipboard import copy_sheets
        copy_sheets(self.board)

    def duplicate_sheets(self):
        if isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit, QTextEdit)):
            return
        from .sheet_clipboard import duplicate_sheets
        duplicate_sheets(self.board)

    def settings(self, board=None):
        board = board or self.board
        return {'brush': asdict(board.brush), 'zoom': board.zoom,
            'brush_preset': board.brush_preset,
            'inpaint_receipts': saved_receipts(board),
            'inpaint_detail': board.inpaint.detail,
            'selected_paper': board.selected.id if isinstance(board.selected, Paper) else None,
            'view_rotation': board.view_rotation,
            'offset': [board.offset.x(), board.offset.y()], 'snap': board.snap,
            'mouse_gain': board.mouse_gain, 'eraser_size': board.eraser_size}

    def save_project(self, checked=False, save_as=False):
        self.board.finish_stroke()
        self.board.flush_dual_move()
        path = self.path
        if path is None or save_as:
            filename, _ = QFileDialog.getSaveFileName(self, 'Сохранить доску', str(path or ROOT/'Моя доска.atelier'), 'Доска Atelier (*.atelier)')
            if not filename:
                return False
            path = Path(filename)
            if path.suffix.lower() != '.atelier':
                path = path.with_suffix('.atelier')
        try:
            save_document(self.board.doc, path, self.settings())
            self.path = path
            if self.board.desk_name.startswith('Стол '):
                self.board.desk_name = path.stem
            self.dirty = False
            self.title_label.setText(path.stem+' · сохранена')
            self.setWindowTitle(f'{APP_TITLE} — {path.stem}')
            self.statusBar().showMessage('Сохранено: '+str(path), 8000)
            self.update_desk_titles()
            self.write_session(clean=not any(getattr(b, "_desk_dirty", False) for b in self.boards))
            return True
        except Exception as exc:
            QMessageBox.warning(self, 'Не удалось сохранить', str(exc))
            return False

    def safe_to_replace(self):
        if not self.dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle('Изменения на доске')
        box.setText('Сохранить изменения перед закрытием доски?')
        save = box.addButton('Сохранить', QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton('Не сохранять', QMessageBox.ButtonRole.DestructiveRole)
        box.addButton('Отмена', QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is save:
            return self.save_project()
        return box.clickedButton() is discard

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Открыть доску', str(ROOT), 'Доска Atelier (*.atelier)')
        if path:
            self.new_desk()
            self.load_path(Path(path))

    def load_path(self, path, recovered=False):
        if hasattr(self, 'assistant') and self.assistant.uses_board(self.board):
            self.statusBar().showMessage('Дождитесь помощника или откройте файл на новом столе.', 7000)
            return
        try:
            doc, settings = load_document(path)
            reference = self.reference_windows.get(self.board.desk_id)
            if reference:
                reference.canvas.pointer_up()
            self.board.finish_stroke()
            if self.board.dual:
                self.toggle_dual()
            self.board.history.clear()
            self.board.doc = doc
            self.board.inpaint.detail = settings.get('inpaint_detail') is True
            self.board.focused_paper_id = None
            restore_receipts(self.board, settings.get('inpaint_receipts', {}), (ROOT/'data', ROOT/'qa', DATA))
            self.board.selected = next((p for p in doc.papers if p.id == settings.get('selected_paper')),
                                       doc.papers[-1] if doc.papers else None)
            self.board.band = doc.bands[0] if doc.bands else None
            self.board.mouse_pin = doc.pins[0] if doc.pins else None
            self.path = None if recovered else path
            if not recovered:
                self.board.desk_name = path.stem
            self.dirty = recovered
            self.revision += 1
            brush = {**asdict(BrushSettings()), **settings.get('brush', {})}
            self._restoring_brush = True
            name = settings.get('brush_preset')
            self.board.brush_preset = name if isinstance(name, str) and name in PRESETS else FIRST_BRUSH
            self.board.brush = BrushSettings()
            shape_index = self.eraser_shape.findData(brush.get('eraser_shape', 'round'))
            self.eraser_shape.setCurrentIndex(max(0, shape_index))
            self.board.brush.eraser_shape = self.eraser_shape.currentData()
            for key, (slider, _, factor, _) in self.controls.items():
                if key in brush and isinstance(brush[key], (float, int)):
                    slider.setValue(round(brush[key]*factor))
                elif key in settings and isinstance(settings[key], (float, int)):
                    slider.setValue(round(settings[key]*factor))
                self.slider_changed(key, slider.value())
            color = brush.get('color', [45, 43, 40])
            if isinstance(color, (tuple, list)) and len(color) == 3 and all(isinstance(c, (float, int)) and 0 <= c <= 255 for c in color):
                self.board.brush.color = tuple(color)
            self.refresh_color_buttons()
            self.sync_brush_controls()
            self._restoring_brush = False
            self.snap_check.setChecked(bool(settings.get('snap', False)))
            self.refresh_selection()
            angle = settings.get('view_rotation', 0.)
            self.board.set_view_rotation(float(angle) if isinstance(angle, (int, float)) else 0.)
            self.board.fit()
            self.preview.refresh()
            self.title_label.setText('Восстановленная доска' if recovered else path.stem)
            self.setWindowTitle(APP_TITLE+' — '+self.title_label.text())
            self.update_desk_titles()
            self.statusBar().showMessage('Доска открыта', 5000)
            self.sync_reference(self.board)
        except Exception as exc:
            self._restoring_brush = False
            QMessageBox.warning(self, 'Не удалось открыть доску', str(exc))

    def export(self):
        self.board.finish_stroke()
        paper = self.board.selected
        if not isinstance(paper, Paper) or paper.kind != 'paper':
            paper = next((p for p in self.board.doc.papers if p.kind == 'paper'), None)
        if paper is None:
            self.statusBar().showMessage('Сначала добавьте лист', 5000)
            return
        path, selected_filter = QFileDialog.getSaveFileName(self, 'Экспорт листа', str(ROOT/(paper.name+'.png')), 'PNG — видимый результат (*.png);;PNG — только рисунок, без бумаги (*.png)')
        if path:
            try:
                export_paper(paper, path, self.board.doc, transparent='без бумаги' in selected_filter)
                self.statusBar().showMessage('Лист экспортирован: '+path, 8000)
            except Exception as exc:
                QMessageBox.warning(self, 'Ошибка экспорта', str(exc))

    def write_session(self, clean):
        DATA.mkdir(parents=True, exist_ok=True)
        temp = DATA/'session.json.tmp'
        desks = [{'id': b.desk_id, 'name': b.desk_name,
                  'path': str(getattr(b, '_desk_path', '') or ''),
                  'dirty': getattr(b, '_desk_dirty', False)} for b in self.boards]
        temp.write_text(json.dumps({'clean': clean, 'desks': desks,
                                   'active': self.board.desk_id}, ensure_ascii=False), encoding='utf8')
        os.replace(temp, DATA/'session.json')

    def autosave(self):
        if self.recovery_job is not None:
            return
        # Background desks used to save synchronously even during a pen stroke
        # on the active desk, starving both WinTab and repaint events.
        if any(b.stroke or b.drag or b.inpaint.owner or b.dual_before for b in self.boards) or any(w.canvas.drag for w in self.reference_windows.values()):
            self.autosave_timer.start(2000)
            return
        self.autosave_timer.start(30000)
        try:
            for b in self.boards:
                revision = getattr(b, '_desk_revision', 0)
                if (not getattr(b, '_desk_dirty', False) or b.stroke or b.drag
                        or revision == getattr(b, '_desk_autosaved_revision', -1)):
                    continue
                from .autosave import RecoverySave
                self.recovery_job = RecoverySave(b, DATA/('recovery-'+b.desk_id+'.atelier'), self.settings(b))
                self.recovery_job.signals.finished.connect(self.recovery_finished)
                self.recovery_pool.start(self.recovery_job)
                break
        except Exception as exc:
            self.recovery_job = None
            self.statusBar().showMessage('Не удалось создать резервную копию: '+str(exc), 12000)

    def recovery_finished(self, board, revision, error):
        self.recovery_job = None
        if getattr(self, '_closing', False):
            return
        if error:
            self.statusBar().showMessage('Не удалось создать резервную копию: '+error, 12000)
            return
        if board in self.boards:
            board._desk_autosaved_revision = revision
        self.write_session(clean=False)
        self.statusBar().showMessage('Резервная копия сохранена · '+time.strftime('%H:%M'), 3000)
        # Save remaining desks one at a time, after checking pen activity again.
        self.autosave_timer.start(2000)

    def offer_recovery(self):
        try:
            session = json.loads((DATA/'session.json').read_text(encoding='utf8'))
            if not session.get('clean', True):
                entries = session.get('desks')
                if entries is None:
                    entries = [{'id': '', 'name': 'Восстановленный стол', 'dirty': True}]
                candidates = []
                for entry in entries:
                    identifier = entry.get('id', '')
                    if identifier and (len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier)):
                        continue
                    recovery_path = DATA/('recovery-'+identifier+'.atelier' if identifier else 'recovery.atelier')
                    path = recovery_path if entry.get('dirty') else Path(entry.get('path') or '')
                    if path.is_file():
                        candidates.append((entry, path))
                if not candidates:
                    return
                result = QMessageBox.question(self, 'Восстановление', 'Найдены незакрытые столы. Восстановить их?', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if result == QMessageBox.StandardButton.Yes:
                    active = 0
                    for i, (entry, path) in enumerate(candidates):
                        if i:
                            self.new_desk()
                        self.load_path(path, recovered=bool(entry.get('dirty')))
                        self.board.desk_name = entry.get('name') or self.board.desk_name
                        if entry.get('path'):
                            self.path = Path(entry['path'])
                        if entry.get('id') == session.get('active'):
                            active = i
                    self.desk_tabs.setCurrentIndex(active)
                    self.update_desk_titles()
        except (OSError, ValueError):
            pass

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ApplicationDeactivate:
            self.board.pointer_up()
            self.board.flush_dual_move()
            self.board.space = False
            self.board.dual_paused = False
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        for reference in self.reference_windows.values():
            reference.canvas.pointer_up()
        self.board.finish_stroke()
        self.board.flush_dual_move()
        if hasattr(self, 'assistant') and self.assistant.busy:
            self.statusBar().showMessage('Дождитесь помощника или отмените задачу перед закрытием.', 8000)
            event.ignore()
            return
        for index in range(len(self.boards)):
            self.desk_tabs.setCurrentIndex(index)
            if not self.safe_to_replace():
                event.ignore()
                return
        self._closing = True
        self.recovery_pool.waitForDone()
        self.write_session(clean=True)
        self.flush_brush_preferences()
        self.raw.active = False
        QApplication.instance().removeNativeEventFilter(self.raw)
        QApplication.instance().removeNativeEventFilter(self.pen_mouse_filter)
        self.native_tablet.close()
        self.assistant.hide()
        for window in self.reference_windows.values():
            window.hide()
        event.accept()

    def help(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Atelier · как работать')
        dialog.resize(720, 660)
        layout = QVBoxLayout(dialog)
        text = QTextBrowser()
        text.setHtml('''<h2 style="color:#d8bf8d">Ваш стол для рисунка</h2>
<p><b>Чат · версия 0.17.</b> «Помощник» теперь отвечает на вопросы об инструментах, обсуждает выбранный скетч и выполняет команды Atelier: листы, папки, кисти, Alt-слои, пины, сетки и раскладка референсов. Enter отправляет сообщение, Shift+Enter добавляет строку. История и новый чат — в меню с тремя точками. Перед удалением/очисткой он показывает команды для подтверждения. Изменения рисунка и объектов доступны через обычную отмену.</p>
<p><b>Изображения и Higgsfield.</b> Выберите «Изображение · генерация», затем провайдера в «Настройки». Доступны ChatGPT, OpenAI API и Higgsfield. Для Higgsfield по умолчанию выбраны Seedream 5.5, Auto и 2K; настройки зависят от модели. Auto использует пропорции исходника, а у моделей без Auto выбирает ближайший формат. Вход находится в «Подключение». Переписка работает через ChatGPT/OpenAI, изображения — через выбранный сервис. Без галочки улучшения введённый промпт передаётся без переписывания. «Получить результат» проверяет существующее задание Higgsfield без новой генерации.</p>
<p><b>Плавающие референсы.</b> Значок двух окон рядом с вкладками столов открывает текущий стол отдельным окном поверх приложений. Его можно перемещать за пределы Atelier. Масштаб независим, листы и отмена общие с исходным столом. Тяните изображение для перемещения, пустой фон — для сдвига вида; колесо — масштаб, F — всё в кадре. «Поверх» отключает закрепление поверх окон. Закрытие скрывает окно, сохраняя стол.</p>
<p><b>Фокус на референсе · 0.17.1.</b> Двойной щелчок по изображению в плавающем окне или кнопка «Фокус» убирает рамку и панели. Тяните картинку для перемещения по экрану, вращайте колесо для изменения размера. Повторный двойной щелчок или Esc возвращает прежний вид стола. Исходный лист не перемещается и не масштабируется. Правый щелчок открывает меню возврата и переключатель «Поверх других окон».</p>
<p><b>Копии и группы.</b> V — выбор, Ctrl/Shift + щелчок добавляет листы в выделение. Можно обвести группу на пустом фоне; в окне референсов удерживайте Shift. Ctrl+C/Ctrl+V копируют листы внутри стола и между столами; Ctrl+D создаёт копии сразу. Рисунок, Alt-слои и настройки сетки сохраняются, копии независимы. Выбранные незакреплённые листы можно перемещать вместе. В полях ввода клавиши работают с текстом.</p>
<p><b>Несколько столов.</b> Кнопка + рядом с вкладками или Ctrl+Shift+N создаёт независимый стол со своими листами, пинами, историей отмены и сохранением. Двойной щелчок по вкладке — название. «Открыть» добавляет стол из файла. После переключения M снова включает управление мышью на активном столе.</p>
<p><b>✦ Помощник.</b> Открывает чат с десятью художественными пресетами и свободным описанием. Для генерации выберите режим «Изображение», лист, стиль и нажмите «Создать изображение». Кнопки «Кадр / Графит / Перо / Кисть» запускают пресет сразу. Без галочки пользовательский текст не переписывается и стиль из меню к нему не добавляется. Галочка «Улучшить мой промпт» уточняет описание с выбранным стилем. Отправленные запросы остаются в истории и в поле ввода. Результат целого листа сохраняет разрешение модели; на доске занимает прежнее место. Выбранный скетч отправляется в выбранный сервис: OpenAI через подписку ChatGPT/API либо Higgsfield; настройки входа находятся в «Подключение». API оплачивается отдельно. Новый лист появляется над исходным на том же столе, даже если вы переключили вкладку. Исходник сохраняется; результат можно отменить. На время работы помощника исходный стол нельзя закрыть: дождитесь результата или нажмите «Отмена».</p>
<p><b>Кнопки слева.</b> Значок выделения включает Inpaint (I), куб — вращение сетки (K). Значок листа с крестиком над отменой очищает рисунок выбранного листа. Удерживайте Alt при нажатии, чтобы очистить только выбранный слой направляющих; основной рисунок и другие слои сохранятся. То же действует для «Очистить рисунок» во вкладке «Лист». Если слоя направляющих нет, Alt + очистка ничего не удаляет. Ctrl+Z возвращает штрихи. В «Объём» ползунок «Скрытые грани» проявляет задние линии кристалла; 0% — выключено.</p><p><b>Inpaint — локальная перерисовка.</b> В помощнике переключите режим на «только выделенная область». «Кисть +» и «Прямоуг.» выделяют участок на выбранном листе; «Убрать −», Alt или обратная сторона пера вычитают из выделения. Размер и мягкость края раскрываются отдельно. Напишите, что изменить, и нажмите «Перерисовать область». Вне выделения пиксели сохраняются точно, результат добавляется новым листом. В режиме выделения Ctrl+Z отменяет выделение. Esc возвращает карандаш. «Очистить» убирает только выделение. Рабочее выделение хранится в текущем сеансе, изображения результата — в проекте.</p>
<p><b>Плавный переход.</b> В «Размер кисти и мягкость края» задайте мягкость 0–200 пикселей (по умолчанию 40). «Согласовать фон у края» подгоняет оттенок фона в переходной полосе. Оставляйте немного фона вокруг объекта: переход идёт внутрь выделения. После генерации выберите её результат и нажмите «Новый вариант перехода», чтобы применить другие настройки без нового запроса к модели. Появится отдельный лист; предыдущий вариант сохранится. Область генерации собирается заново, последующие ручные правки внутри неё остаются на предыдущем листе. Связь сохраняется в проекте; для настройки нужны исходный лист и сохранённые файлы задачи.</p>
<p><b>Больше деталей · крупный план.</b> Галочка в Inpaint увеличивает небольшой выделенный фрагмент вместе с окружающим фоном для обработки помощником. Результат уменьшается и вставляется на место, размер листа сохраняется. Режим предназначен для мелких объектов; для общей композиции его можно отключить. Он помогает рассмотреть детали, но не гарантирует правильной анатомии или соединений. Требуется новая генерация.</p>
<p><b>Плавающий помощник.</b> Его окно можно переносить за заголовок и менять по высоте. Панель кистей остаётся доступной. Крестик или кнопка «Помощник» скрывают окно, не отменяя генерацию. Положение запоминается до закрытия приложения. Tab скрывает панели доски независимо от помощника. По умолчанию в обоих режимах выбран пункт «Сохранить стиль исходника».</p>
<p><b>Память кистей и сброс.</b> Каждый карандаш помнит изменённые настройки и цвет при переключении и после перезапуска приложения. Размер, сила и форма клячки сохраняются отдельно. Кнопка ↺ рядом с названием инструмента сбрасывает только выбранный карандаш либо клячку к исходным настройкам. Другие кисти и рисунок сохраняются. Открытие старого проекта само по себе не меняет личную библиотеку кистей.</p>
<p><b>Размер кисти.</b> [ уменьшает размер, ] увеличивает; в русской раскладке — Х и Ъ. Удержание клавиши повторяет изменение. Работает для карандаша, клячки и кисти выделения Inpaint, в том числе с Alt при рисовании направляющих. Ползунок и сохранённые настройки кисти обновляются сразу. В полях ввода эти клавиши печатают текст.</p><p><b>Направляющие · Alt.</b> В режиме карандаша или клячки удерживайте Alt для рисования на выбранном слое направляющих. Первый слой создаётся автоматически. Отпускание Alt возвращает основной рисунок, в том числе посреди штриха. Во вкладке «Лист → Слои направляющих» можно добавлять, выбирать, переименовывать и удалять слои, задавать цвет и видимость. Направляющие сохраняются в проекте, но не входят в PNG и снимок для помощника. В Inpaint Alt по-прежнему убирает выделение.</p><p><b>Панели.</b> Справа четыре вкладки: «Кисть», «Пины», «Лист» и «Объём». Основные настройки кисти видны сразу, дополнительные раскрываются в «Фактура и поведение». Диагностика находится в «Перо и диагностика». Вкладки столов находятся в верхней строке; листы и папки — в компактной строке над холстом. Кнопка справа сверху или Tab скрывает и возвращает панели. Меню «Доска» содержит файлы и экспорт, «+ Добавить» — листы и референсы.</p>
<p><b>B — карандаш.</b> Острый кончик остаётся в точке пера. При наклоне за ним раскрывается боковая грань: узкая у острия и широкая, мягкая дальше от него. Нажим меняет плотность и толщину; поворот наклона поворачивает грань вокруг кончика. «Боковая грань грифеля» регулирует её раскрытие, 0 — только остриё. Малый нажим — зернистый светлый след; повторные проходы накапливают графит.</p>
<p><b>Острота кончика.</b> 0% — притуплённый, 50% — исходная заточка, 100% — максимально тонкое остриё. Ползунок меняет кончик, сохраняя ширину раскрытой боковой грани. Настройка сохраняется в проекте.</p>
<p><b>Длина заточки.</b> Регулятор в основной панели удлиняет контакт грифеля вдоль наклона пера: 25% — короткая заточка, 100% — прежняя форма, 1000% — очень длинная. При боковой штриховке поперёк наклона след становится шире. Толщина вертикального острия сохраняется. «Боковая грань грифеля» рядом управляет общим раскрытием контакта; «Длина заточки» — его вытянутостью. Работает также у клячки «Как карандаш» и сохраняется в проекте.</p>
<p><b>Бумага и свет.</b> Выберите лист, затем во вкладке «Листы» — «Бумага выбранного листа»: светлая, графитовая, антрацит, тёмный крафт, умбра или сланец. Цвет бумаги не перекрашивает штрихи, Ctrl+Z отменяет смену. Белый кружок справа в палитре — карандаш для бликов и светов. Нажим и повторные проходы усиливают белый; «Плотность штриха» регулирует его насыщенность. Вид бумаги сохраняется в проекте и обычном PNG. Прозрачный PNG содержит только штрихи.</p>
<p><b>E — клячка.</b> Мягко снимает пигмент по зерну бумаги, оставляя лёгкий фактурный след. В списке «Форма клячки» выберите «Круглая» или «Как карандаш». Вторая повторяет текущую рисующую кисть: остриё выбирает тонкую линию, наклон раскрывает широкую боковую грань. Она использует «Толщину», «Остроту кончика», «Зерно бумаги», «Боковую грань грифеля», «Длину заточки», кривую нажима и стабилизацию карандаша. «Размер круглой клячки» действует только на круглую форму. «Сила клячки» задаёт интенсивность снятия пигмента у обеих форм вместо плотности штриха. Нажим и повторные проходы снимают больше. Работает с графитом, цветными и белыми штрихами, а также обратной стороной пера. Форма сохраняется в проекте. «Листы → Свойства и порядок → Очистить рисунок» или Ctrl+Shift+Delete стирает весь выбранный лист; Ctrl+Z восстанавливает его.</p>
<p><b>R — поворот вида.</b> Удерживайте R и потяните пером или мышью: после отпускания R вернётся прежний инструмент. Короткое нажатие R или кнопка «Поворот» оставляет этот инструмент выбранным; B возвращает карандаш. Shift даёт шаг 15°. Поле «Вид» сверху позволяет задать угол, кнопка ↺ сбрасывает его к 0°. Поворачивается только вид: листы, пины и изображения сохраняют взаимное положение, экспорт не меняется. Угол сохраняется в проекте. В режиме двух рук мышь на время поворота перестаёт двигать пин.</p>
<p><b>V — выбор.</b> Перемещайте листы и референсы. Нижний правый угол выбранного листа меняет масштаб; угол и масштаб также доступны в панели. Заголовок листа можно тянуть любым инструментом. Закрепление блокирует перемещение и рисование.</p>
<p><b>Пины и резинки.</b> P ставит пины. G: выберите первый пин, затем соединяйте его с любыми другими. Один пин может держать несколько резинок. Нажмите первый пин повторно или пустое место, чтобы выбрать другой исходный пин; Esc завершает соединение. V позволяет выбрать резинку по линии; Delete или «Удалить резинку» во вкладке «Пины» убирает только связь. Пины можно перемещать пером до рисования. Визуальное продление линий и привязка штриха сохранены.</p>
<p><b>Привязка.</b> Включите её для ровных линий. Начните штрих не далее 28 экранных пикселей от линии: выберется ближайшая ветвь и останется выбранной до конца штриха. Без привязки резинки служат визуальными направляющими.</p>
<p><b>M — две руки.</b> Выберите пин, которым будет управлять мышь. Пин можно выбрать на доске или в списке; он остаётся целью мыши при рисовании. Мышь двигает его без нажатия, перо независимо рисует. Space временно останавливает мышь; M или Esc возвращает ручной режим. Пин может уходить за пределы окна.</p>
<p><b>T — калька.</b> Выберите лист или референс и нажмите T или «+ Калька». Новый лист совпадёт с исходным по размеру, положению и повороту. «Прозрачность бумаги» 0% скрывает нижний скетч, 100% полностью показывает его. Новый рисунок остаётся чётким; его плотность отдельно задаётся регулятором «Видимость рисунка». Ластик и очистка затрагивают только выбранный лист.</p>
<p><b>Доска.</b> Пробел + перетаскивание или средняя кнопка — панорама. Колесо — масштаб под курсором. F — всё в кадре. Tab — скрыть боковые панели.</p>
<p><b>Объёмная сетка · K.</b> Выберите лист и нажмите K или «+ Добавить → Объёмная сетка». Во вкладке «Объём» — 15 готовых видов, обычная и параллельная перспектива, цилиндрическая, сферическая, равноудалённая и стереографическая проекции. Конструкция: кристалл Эшера из кубиков, прозрачная объёмная решётка, прозрачные перекладины, размеченные грани куба или пол. Перетаскивание вращает каркас, Shift при начале перетаскивания сдвигает его на листе, колесо меняет масштаб. B возвращает карандаш. «Зафиксировать» защищает от перемещения на холсте.</p>
<p><b>Кристалл Эшера.</b> Передние поверхности скрывают задние грани и дальние линии. «Кубиков на ребро ячейки» задаёт 3–12 маленьких кубиков на шаг большой ячейки: чем больше значение, тем тоньше её перекладины. «Тон видимых граней» можно убрать до нуля — скрытие задних линий останется. Исходный рисунок не закрывается непрозрачной заливкой. В разделе «Ячейки и перекладины» кнопка «Об Эшере и Chelsea» открывает историческую справку с музейными ссылками. Прежний вид сохранён как «Прозрачные перекладины · 0.14».</p>
<p>В «Ячейки и перекладины» задаются число ячеек 1–12 по каждой оси, их относительные размеры и мелкие деления. В «Поворот и обзор» — три угла, положение камеры, дистанция и угол обзора; дистанция 0 помещает наблюдателя внутрь объёма. «Изгиб и деформация» изменяет саму конструкцию. Дымка ослабляет дальние линии. Перцептивный режим — художественная аппроксимация по мотивам Раушенбаха с плавным переходом от более параллельного среднего плана к перспективному дальнему, не точная модель человеческого зрения.</p>
<p>Сетка отдельная у каждого листа, сохраняется в проекте и поддерживает отмену. Она не меняет уже нарисованные штрихи и не включается в PNG или снимок для помощника. «Поверх рисунка» позволяет видеть её и на непрозрачном изображении; отключение располагает направляющую под штрихами. Во время рисования готовая сетка переиспользуется.</p>
<p><b>R — крен камеры в режиме объёма.</b> После K удерживайте R и тяните пером или мышью по кругу вокруг центра окна. Горизонт сетки наклоняется, а канвас и рисунок сохраняют своё положение. R + Shift даёт шаг 15°. При начале жеста около центра можно тянуть горизонтально. Отпускание R возвращает обычное вращение объёма. Крен сохраняется с листом и отменяется через Ctrl+Z; фиксация сетки блокирует этот жест. В режиме рисования R по-прежнему вращает канвас.</p>
<p><b>Референсы.</b> Меню «+ Добавить → Референс», Ctrl+I, перетаскивание файлов или Ctrl+V для изображения из буфера. Они сохраняются внутри проекта.</p>
<p><b>Файлы.</b> Ctrl+S — проект .atelier, Ctrl+O — открыть. «Доска → Экспорт PNG» предлагает видимый результат с нижними листами или только рисунок на прозрачном фоне. Направляющие в PNG не входят. Ctrl+Z / Ctrl+Shift+Z отменяют и возвращают штрихи и изменения объектов. Резервная копия создаётся каждые 30 секунд после завершения штриха. Старые проекты открываются; обновлённые сохраняются в формате версии 4. Старые кольца превращаются в общие пины без изменения формы направляющих.</p>
<p><b>Wacom.</b> Прямое чтение драйвера получает координаты пера, нажим и наклон отдельно от системной мыши. Показания видны сверху, под названием кисти. Если прямой ввод несовместим с вашим драйвером, переключите список на «Стандартный ввод Qt». При переключении поднимите перо и снова коснитесь листа. Нужны драйвер Wacom с WinTab и перо, поддерживающее наклон.</p>
<p style="color:#b8a582">Это самостоятельный прототип карандаша, а не копия движка Procreate. Один основной рисунок на лист и отдельные рисуемые слои направляющих.</p>''')
        layout.addWidget(text)
        layout.addWidget(button('К рисунку', dialog.accept))
        dialog.exec()


def main():
    from .brush_native import warmup
    warmup()
    from .crystal_native import warmup as warmup_crystal
    warmup_crystal()
    def report_exception(kind, error, tb):
        import traceback
        DATA.mkdir(parents=True, exist_ok=True)
        with (DATA/'errors.log').open('a', encoding='utf8') as log:
            traceback.print_exception(kind, error, tb, file=log)
    sys.excepthook = report_exception
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_CompressHighFrequencyEvents, False)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_CompressTabletEvents, False)
    app = QApplication(sys.argv)
    app.setApplicationName('Atelier')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    app.setFont(QFont('Segoe UI', 10))
    window = MainWindow(recovery='--no-recovery' not in sys.argv and '--open' not in sys.argv,
                        brush_path=ROOT/'data'/'brushes.json')
    window.show()
    window.board.setFocus()
    if '--open' in sys.argv:
        index = sys.argv.index('--open')
        if index+1 < len(sys.argv):
            window.load_path(Path(sys.argv[index+1]))
    if '--assistant' in sys.argv or '--inpaint' in sys.argv:
        window.toggle_assistant()
    if '--inpaint' in sys.argv:
        window.assistant.mode.setCurrentIndex(window.assistant.mode.findData('inpaint'))
    if '--perspective' in sys.argv:
        def show_perspective():
            if isinstance(window.board.selected, Paper):
                window.sheet_navigator.open(window.board.selected.id)
            window.board.set_tool('perspective')
        QTimer.singleShot(0, show_perspective)
    return app.exec()
