"""Per-sheet drawing guides, kept outside the exported artwork."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QCheckBox, QSlider, QColorDialog, QLineEdit
from .model import Paper
from .widgets import label, button


class GuidePanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.updating = window, False
        root = QVBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(9)
        hint = label('Удерживайте Alt — рисуйте направляющие. Отпустите — основной рисунок. Слои сохраняются в проекте; в PNG и помощник не попадают.', 'muted')
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.layers = QComboBox()
        self.layers.currentIndexChanged.connect(self.choose)
        root.addWidget(self.layers)
        row = QHBoxLayout()
        self.add = button('+ Слой', lambda: self.window.board.add_guide_layer())
        self.remove = button('Удалить', lambda: self.window.board.delete_guide_layer(), 'Удалить выбранный слой направляющих · отмена Ctrl+Z')
        row.addWidget(self.add); row.addWidget(self.remove)
        root.addLayout(row)
        self.name = QLineEdit()
        self.name.setPlaceholderText('Название слоя')
        self.name.editingFinished.connect(lambda: self.change('name', self.name.text().strip()[:80] or 'Направляющие'))
        root.addWidget(self.name)
        row = QHBoxLayout()
        self.visible = QCheckBox('Показывать')
        self.visible.toggled.connect(lambda v: self.change('visible',v))
        self.color = button('Цвет', self.choose_color)
        row.addWidget(self.visible); row.addWidget(self.color)
        root.addLayout(row)
        self.caption = label('Видимость · 65%', 'muted')
        root.addWidget(self.caption)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0,100)
        self.opacity.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.opacity.valueChanged.connect(lambda v: self.change('opacity', v/100))
        root.addWidget(self.opacity)

    def paper(self):
        item = self.window.board.selected
        return item if isinstance(item,Paper) and item in self.window.board.doc.papers and item.kind == 'paper' else None

    def sync(self):
        self.updating = True
        paper = self.paper()
        layer = self.window.board.active_guide(paper) if paper else None
        self.layers.clear()
        if paper:
            for item in paper.guide_layers:
                self.layers.addItem(('● ' if item.visible else '○ ')+item.name, item.id)
        self.layers.setCurrentIndex(self.layers.findData(layer.id) if layer else -1)
        editable = bool(paper and not paper.locked)
        self.add.setEnabled(editable)
        self.layers.setEnabled(bool(paper))
        for widget in (self.name,self.remove,self.visible,self.color,self.opacity):
            widget.setEnabled(editable and layer is not None)
        self.name.setText(layer.name if layer else '')
        self.visible.setChecked(bool(layer and layer.visible))
        self.opacity.setValue(round(layer.opacity*100) if layer else 65)
        self.caption.setText(f'Видимость · {self.opacity.value()}%')
        self.color.setStyleSheet(f'border: 2px solid {layer.color if layer else "#58645d"};')
        self.updating = False

    def choose(self,index):
        if self.updating or not self.paper():
            return
        board, paper = self.window.board, self.paper()
        board.finish_stroke()
        paper.active_guide_id = self.layers.currentData() or ''
        board.changed.emit()
        self.sync()
        board.setFocus()

    def change(self,key,value):
        if self.updating or not self.paper() or self.paper().locked:
            return
        board = self.window.board
        layer = board.active_guide()
        if layer:
            board.finish_stroke()
            board.push_move(layer,{key:getattr(layer,key)},{key:value},'Свойства направляющих')
            self.sync()

    def choose_color(self):
        layer = self.window.board.active_guide()
        if layer:
            color = QColorDialog.getColor(QColor(layer.color),self,'Цвет направляющих')
            if color.isValid():
                self.window.board.set_guide_color(color.name())
