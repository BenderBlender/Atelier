"""Reproducible publication images: real Qt UI, temporary data, no network calls."""
import math
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication
from sketchpad import app as app_module
from sketchpad.app import MainWindow, STYLE
from sketchpad.brush import BrushSettings, GraphiteStroke, empty_ink
from sketchpad.model import Document, Paper, PAPER_STYLES, composite_paper
from sketchpad.perspective import preset

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'docs/screenshots'
EXAMPLES = ROOT/'docs/examples'


def write_label(image, x, y, text, size=14, color='#57594e'):
    painter = QPainter(image)
    painter.setPen(QColor(color))
    painter.setFont(QFont('Segoe UI', size))
    painter.drawText(QPointF(x, y), text)
    painter.end()


def pencil_sample(image, x, y, length, settings, tilt=60, pressure=None):
    stroke = GraphiteStroke(image, settings)
    for i in range(length):
        t = i/max(1, length-1)
        stroke.add(x+i, y+math.sin(t*math.pi*2)*9,
                   pressure if pressure is not None else .12+.8*math.sin(t*math.pi/2), 0, tilt)


def samples():
    image = QImage(1320, 650, QImage.Format.Format_RGB32)
    image.fill(QColor('#f5f1e8'))
    write_label(image, 44, 55, 'КАРАНДАШ ATELIER', 25)
    write_label(image, 44, 91, 'Настоящие штрихи текущего движка · без генерации изображения', 13)
    ink = empty_ink(image.width(), image.height())
    for row, tilt in enumerate((0, 35, 65)):
        write_label(image, 44, 150+row*145, f'Наклон {tilt}°', 16)
        for col, pressure in enumerate((.2, .55, .95)):
            x = 210+col*365
            write_label(image, x, 150+row*145, f'Нажим {pressure:.0%}', 12)
            pencil_sample(ink, x, 205+row*145, 290, BrushSettings(size=6.5), tilt, pressure)
    painter = QPainter(image)
    painter.drawImage(0, 0, ink)
    painter.end()
    write_label(image, 44, 613, 'Тонкое остриё → боковая грань · зерно бумаги · накопление графита', 14)
    image.save(str(OUT/'pencil.png'))

    image = QImage(1380, 820, QImage.Format.Format_RGB32)
    image.fill(QColor('#202523'))
    write_label(image, 40, 52, 'БУМАГА И СВЕТ', 24, '#ddc89c')
    write_label(image, 40, 88, 'Белый карандаш, графит и штриховка на шести бумагах', 13, '#b1b9b0')
    sheets = []
    for index, (style, (name, _, _)) in enumerate(PAPER_STYLES.items()):
        paper = Paper(width=410, height=265, paper_style=style, name=name)
        for row, color in enumerate(((255, 255, 255), (45, 43, 40))):
            pencil_sample(paper.image, 35, 55+row*85, 340,
                          BrushSettings(size=7, color=color, opacity=.95), 60)
        for n in range(22):
            stroke = GraphiteStroke(paper.image, BrushSettings(size=3.5, color=(255, 255, 255), opacity=.94))
            for i in range(50):
                stroke.add(40+n*15+i*.2, 242-i, .2+.6*math.sin(i/50*math.pi), 18, 25)
        x, y = 40+index%3*445, 155+index//3*330
        write_label(image, x, y-15, name, 13, '#dce2da')
        doc = Document(False)
        doc.papers = [paper]
        painter = QPainter(image)
        painter.drawImage(x, y, composite_paper(doc, paper))
        painter.end()
        sheets.append(paper)
    image.save(str(OUT/'papers.png'))
    return sheets


def imported(filename, name):
    image = QImage(str(EXAMPLES/filename)).convertToFormat(QImage.Format.Format_RGBA8888)
    if image.isNull():
        raise RuntimeError(f'Missing example: {filename}')
    return Paper(name=name, width=image.width(), height=image.height(), image=image,
                 scale=1000/image.width())


def set_document(board, papers, name):
    board.doc = Document(False)
    board.doc.papers = papers
    board.doc.navigation = [{'type': 'paper', 'id': p.id} for p in papers]
    board.set_selection(papers[-1])
    board.band = board.mouse_pin = None
    board.guide_visible = False
    board.desk_name = name


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    # The offscreen Qt platform does not enumerate Windows system fonts itself.
    for filename in ('segoeui.ttf', 'segoeuib.ttf', 'seguisym.ttf'):
        font = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'/filename
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont('Segoe UI', 10))
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    sheets = samples()
    with tempfile.TemporaryDirectory() as temp, patch.object(app_module, 'DATA', Path(temp)):
        window = MainWindow(recovery=False)
        for timer in (window.autosave_timer, window.ui_timer, window.brush_save_timer, window.native_tablet.timer):
            timer.stop()
        try:
            window.resize(1480, 930)
            window.show()
            app.processEvents()
            sketch = imported('sketch.png', 'Исходный набросок')
            refined = imported('refined.png', 'Свет у окна · вариант помощника')
            art = window.board
            set_document(art, [sketch, refined], 'Скетчбук')
            grids = window.new_desk()
            names = ['Кристалл Эшера', 'Кристалл · цилиндр', 'Кристалл · рыбий глаз']
            papers = [Paper(name=n, width=1400, height=1000, perspective=preset(n)) for n in names]
            set_document(grids, papers, 'Перспектива')
            swatches = window.new_desk()
            for index, paper in enumerate(sheets):
                paper.x, paper.y = index%3*470, index//3*325
            set_document(swatches, sheets, 'Бумага и свет')
            window.desk_tabs.setCurrentIndex(0)
            window.update_desk_titles()
            window.sheet_navigator.refresh()
            window.sheet_navigator.open(refined.id)
            art.set_tool('pencil')
            window.sidebar_tabs.setCurrentIndex(0)
            app.processEvents()
            window.grab().save(str(OUT/'workspace.png'))

            helper = window.assistant
            window.set_assistant_visible(True)
            helper.resize(540, 850)
            helper.chat.clear()
            helper.messages = []
            demo = Path(temp)/'demonstration.png'
            shutil.copyfile(EXAMPLES/'refined.png', demo)
            helper.say('Вы', 'Демонстрация: доработать набросок карандашом, сохранив композицию и свет.')
            helper.say('Помощник', 'Пример сохранённого результата генерации. Вариант открыт на отдельном листе; исходный набросок остаётся на столе.', result=demo)
            helper.composer.setPlainText('Как можно сделать световой акцент выразительнее?')
            app.processEvents()
            helper.chat.verticalScrollBar().setValue(0)
            helper.grab().save(str(OUT/'assistant.png'))
            window.set_assistant_visible(False)

            ref = window.open_reference()
            ref.resize(850, 650)
            app.processEvents()
            ref.canvas.fit()
            ref.canvas.focused_paper_id = refined.id
            ref.grab().save(str(OUT/'references.png'))
            ref.enter_focus(refined)
            app.processEvents()
            ref.grab().save(str(OUT/'focus.png'))
            ref.close()

            window.desk_tabs.setCurrentIndex(1)
            window.sheet_navigator.open(papers[0].id)
            grids.set_tool('perspective')
            window.sidebar_tabs.setCurrentIndex(3)
            app.processEvents()
            window.grab().save(str(OUT/'perspective.png'))
        finally:
            for ref in window.reference_windows.values():
                ref.hide()
            window.assistant.hide()
            window.hide()
            for timer in (window.autosave_timer, window.ui_timer, window.brush_save_timer):
                timer.stop()
            app.removeEventFilter(window)
            app.removeNativeEventFilter(window.raw)
            app.removeNativeEventFilter(window.pen_mouse_filter)
            window.native_tablet.close()
    print('Saved 7 publication images in docs/screenshots. No generation requests were sent.')


if __name__ == '__main__':
    main()
