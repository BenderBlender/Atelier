"""Compact controls for an editable guide belonging to the selected sheet."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QComboBox,
                              QCheckBox, QDoubleSpinBox, QSpinBox, QSlider)
from .model import Paper
from .widgets import button, label, Foldout
from .perspective import PerspectiveSettings, PROJECTIONS, STRUCTURES, PRESETS, preset


class PerspectivePanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.updating = False
        self.controls = {}
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(12)
        self.source = label('Выберите лист', 'muted')
        self.source.setWordWrap(True)
        self.root.addWidget(self.source)
        self.check(self.root, 'Показать объёмную сетку', 'enabled')
        self.presets = QComboBox()
        self.presets.addItem('Готовые виды…')
        self.presets.addItems(PRESETS)
        self.presets.currentIndexChanged.connect(self.use_preset)
        self.root.addWidget(self.presets)
        self.combo(self.root, 'Проекция', 'projection', PROJECTIONS)
        self.combo(self.root, 'Конструкция', 'structure', STRUCTURES)
        self.crystal_hint = label('Кубики образуют единый объём. Ближние грани закрывают дальние; рисунок под направляющими остаётся виден.', 'muted')
        self.crystal_hint.setWordWrap(True)
        self.root.addWidget(self.crystal_hint)
        self.edit = button('Вращать на листе · K', self.edit_on_canvas,
            'Тяните пером или мышью — вращение; R — крен камеры; R + Shift — шаг 15°; Shift — сдвиг; колесо — масштаб сетки.', True)
        self.root.addWidget(self.edit)
        self.done = button('К рисованию · B', lambda: self.window.board.set_tool('pencil'))
        self.root.addWidget(self.done)
        self.check(self.root, 'Зафиксировать на листе', 'locked', 'Блокирует перетаскивание сетки. Числовые настройки остаются доступны.')
        self.slider(self.root, 'Видимость', 'opacity', 5, 100, 100, '%')
        self.slider(self.root, 'Скрытые грани', 'hidden_opacity', 0, 100, 100, '%')
        self.controls['hidden_opacity'][0].setToolTip('0% — скрыты. Проявляет линии задних граней сквозь ближние поверхности кристалла.')
        self.slider(self.root, 'Дальние линии в дымке', 'fog', 0, 100, 100, '%')

        camera = self.fold('Поворот и обзор')
        self.triple(camera, ('Азимут', 'Наклон', 'Крен'), ('yaw', 'pitch', 'roll'), -180, 180, 0, '°')
        self.controls['roll'].setToolTip('Наклон горизонта. В режиме K удерживайте R и тяните пером или мышью; Shift — шаг 15°.')
        self.number(camera, 'Дистанция камеры', 'distance', 0, 6, 2, '')
        self.controls['distance'].setToolTip('В размерах объёма. 0 — наблюдатель внутри решётки.')
        self.triple(camera, ('Камера X', 'Камера Y'), ('camera_x', 'camera_y'), -2, 2, 2, '')
        self.number(camera, 'Угол обзора', 'fov', 20, 360, 0, '°')
        self.number(camera, 'Масштаб', 'scale', .2, 4, 2, '×')
        self.number(camera, 'Ось цилиндра', 'cylinder_axis', -180, 180, 0, '°')
        self.triple(camera, ('Сдвиг X', 'Сдвиг Y'), ('offset_x', 'offset_y'), -1, 1, 2, '')

        cells = self.fold('Ячейки и перекладины')
        self.triple(cells, ('Число X', 'Число Y', 'Число Z'), ('nx', 'ny', 'nz'), 1, 12, 0, '')
        self.triple(cells, ('Шаг X', 'Шаг Y', 'Шаг Z'), ('sx', 'sy', 'sz'), .2, 4, 2, '')
        self.number(cells, 'Делений на ячейку', 'divisions', 1, 6, 0, '')
        self.number(cells, 'Толщина перекладин', 'beam', .02, .25, 2, '')
        self.number(cells, 'Кубиков на ребро ячейки', 'crystal_divisions', 3, 12, 0, '')
        self.controls['crystal_divisions'].setToolTip('Число маленьких кубиков на один шаг большой ячейки. Больше кубиков — тоньше перекладины; узлы и перекладины состыкованы без внутренних граней.')
        self.slider(cells, 'Тон видимых граней', 'face_tone', 0, 50, 100, '%')
        cells.addWidget(button('Об Эшере и Chelsea', self.history_note, 'Историческая справка с музейными источниками'))
        hint = label('Шаг задаёт размер ячеек. Во время вращения детализация временно снижается.', 'muted')
        hint.setWordWrap(True)
        cells.addWidget(hint)

        deforms = self.fold('Изгиб и деформация')
        self.slider(deforms, 'Изгиб', 'bend', -200, 200, 100, '')
        self.slider(deforms, 'Скручивание', 'twist', -180, 180, 1, '°')
        self.slider(deforms, 'Сужение по глубине', 'taper', -80, 80, 100, '')
        self.slider(deforms, 'Сдвиг по глубине', 'shear', -200, 200, 100, '')

        self.perceptual_fold = Foldout('Перцептивное пространство')
        self.root.addWidget(self.perceptual_fold)
        perception = self.perceptual_fold.content
        hint = label('Художественная настройка по мотивам Раушенбаха: средний план ближе к параллельному, дальний — к обычной перспективе.', 'muted')
        hint.setWordWrap(True)
        perception.addWidget(hint)
        self.slider(perception, 'Сила выравнивания', 'perceptual_strength', 0, 100, 100, '%')
        self.number(perception, 'Протяжённость перехода', 'perceptual_range', .15, 3, 2, '')
        self.slider(perception, 'Обратность ближнего плана', 'near_reverse', 0, 100, 100, '%')

        view = self.fold('Вид направляющих')
        self.check(view, 'Три цвета осей', 'axis_colors')
        self.check(view, 'Поверх рисунка', 'on_top', 'Выключите, чтобы положить направляющую под штрихи. Экспорт и помощник всегда получают рисунок без сетки.')
        self.number(view, 'Толщина линий', 'line_width', .5, 3, 1, '')
        view.addWidget(button('Сбросить сетку', lambda: self.replace(preset('Объём · три точки'))))
        self.root.addStretch()

    def paper(self):
        board = self.window.board
        return board.selected if isinstance(board.selected, Paper) and board.selected in board.doc.papers else None

    def fold(self, title):
        fold = Foldout(title)
        self.root.addWidget(fold)
        return fold.content

    def check(self, layout, title, key, tooltip=''):
        check = QCheckBox(title)
        check.setToolTip(tooltip)
        check.toggled.connect(lambda value: self.change(key, value))
        self.controls[key] = check
        layout.addWidget(check)

    def combo(self, layout, title, key, items):
        layout.addWidget(label(title, 'muted'))
        control = QComboBox()
        for value, text in items.items():
            control.addItem(text, value)
        control.currentIndexChanged.connect(lambda _: self.change(key, control.currentData()))
        self.controls[key] = control
        layout.addWidget(control)

    def spin(self, key, low, high, decimals, suffix):
        widget = QSpinBox() if key in ('nx', 'ny', 'nz', 'divisions', 'crystal_divisions') else QDoubleSpinBox()
        if isinstance(widget, QDoubleSpinBox):
            widget.setDecimals(decimals)
        widget.setRange(low, high)
        widget.setSingleStep(1 if decimals == 0 else .05 if key == 'beam' else .1)
        widget.setSuffix(suffix)
        widget.setKeyboardTracking(False)
        widget.setMinimumWidth(0)
        widget.valueChanged.connect(lambda value: self.change(key, value))
        self.controls[key] = widget
        return widget

    def number(self, layout, title, key, low, high, decimals, suffix):
        layout.addWidget(label(title, 'muted'))
        layout.addWidget(self.spin(key, low, high, decimals, suffix))

    def triple(self, layout, names, keys, low, high, decimals, suffix):
        row = QHBoxLayout()
        row.setSpacing(5)
        for name, key in zip(names, keys):
            column = QVBoxLayout()
            caption = label(name, 'muted')
            caption.setWordWrap(True)
            column.addWidget(caption)
            column.addWidget(self.spin(key, low, high, decimals, suffix))
            row.addLayout(column, 1)
        layout.addLayout(row)

    def slider(self, layout, title, key, low, high, factor, suffix):
        caption = label(title, 'muted')
        layout.addWidget(caption)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setTracking(False)
        slider.setAccessibleName(title)
        slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        slider.valueChanged.connect(lambda value: self.change(key, value/factor))
        self.controls[key] = (slider, caption, title, factor, suffix)
        layout.addWidget(slider)

    def replace(self, data):
        paper = self.paper()
        if paper is None:
            return
        board = self.window.board
        board.pointer_up()
        board.push_move(paper, {'perspective': paper.perspective},
                        {'perspective': PerspectiveSettings.read(data).data()}, 'Объёмная сетка')
        self.sync()

    def change(self, key, value):
        if self.updating or self.paper() is None:
            return
        data = PerspectiveSettings.read(self.paper().perspective).data()
        data[key] = value
        if key == 'projection':
            data['fov'] = PerspectiveSettings.read(data).effective_fov
        self.replace(data)

    def use_preset(self, index):
        if not self.updating and index > 0:
            self.replace(preset(self.presets.itemText(index)))
            self.presets.blockSignals(True)
            self.presets.setCurrentIndex(0)
            self.presets.blockSignals(False)

    def edit_on_canvas(self):
        self.window.board.set_tool('perspective' if self.edit.isChecked() else 'pencil')

    def history_note(self):
        from pathlib import Path
        from PySide6.QtWidgets import QDialog, QTextBrowser
        dialog = QDialog(self.window)
        dialog.setWindowTitle('Кристалл Эшера · историческая справка')
        dialog.resize(700, 620)
        layout = QVBoxLayout(dialog)
        text = QTextBrowser()
        text.document().setDefaultStyleSheet('a { color: #d8bf8d; }')
        text.setOpenExternalLinks(True)
        text.setMarkdown((Path(__file__).resolve().parents[1]/'docs/escher-history.md').read_text(encoding='utf8'))
        # Qt's Markdown importer assigns link blue explicitly; recolour anchor
        # fragments so the museum links remain readable on the dark panel.
        from PySide6.QtGui import QTextCursor, QTextCharFormat, QColor
        anchors = []
        block = text.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isAnchor():
                    anchors.append((fragment.position(), fragment.length()))
                iterator += 1
            block = block.next()
        link_format = QTextCharFormat()
        link_format.setForeground(QColor('#d8bf8d'))
        for position, length in anchors:
            cursor = QTextCursor(text.document())
            cursor.setPosition(position)
            cursor.setPosition(position+length, QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(link_format)
        layout.addWidget(text)
        layout.addWidget(button('К рисунку', dialog.accept))
        dialog.exec()

    def sync(self):
        self.updating = True
        paper = self.paper()
        s = PerspectiveSettings.read(paper.perspective if paper else {})
        self.controls['fov'].setMaximum(165 if s.projection in ('linear', 'perceptual') else
                                        180 if s.projection == 'fish' else 360 if s.projection == 'cylinder' else 350)
        self.source.setText(paper.name if paper else 'Выберите лист или референс для сетки')
        self.presets.setEnabled(paper is not None)
        for key, control in self.controls.items():
            value = getattr(s, key)
            if isinstance(control, tuple):
                slider, caption, title, factor, suffix = control
                slider.setValue(round(value*factor))
                caption.setText(f'{title} · {value*factor:.0f}{suffix}')
                slider.setEnabled(paper is not None and (key != 'hidden_opacity' or s.structure == 'crystal'))
            else:
                control.setEnabled(paper is not None)
                if isinstance(control, QCheckBox):
                    control.setChecked(value)
                elif isinstance(control, QComboBox):
                    control.setCurrentIndex(control.findData(value))
                else:
                    control.setValue(value)
        self.controls['fov'].setEnabled(paper is not None and s.projection != 'parallel')
        self.controls['cylinder_axis'].setEnabled(paper is not None and s.projection == 'cylinder')
        self.controls['beam'].setEnabled(paper is not None and s.structure == 'beams')
        crystal = s.structure == 'crystal'
        self.crystal_hint.setVisible(crystal)
        self.controls['crystal_divisions'].setEnabled(paper is not None and crystal)
        self.controls['face_tone'][0].setEnabled(paper is not None and crystal)
        self.controls['divisions'].setEnabled(paper is not None and not crystal)
        self.perceptual_fold.setVisible(s.projection == 'perceptual')
        self.edit.setChecked(self.window.board.tool == 'perspective')
        self.edit.setEnabled(paper is not None and s.enabled and not s.locked)
        self.updating = False
