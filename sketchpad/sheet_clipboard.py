"""Lossless, independent sheet copies; interoperable clipboard with a PNG fallback."""
from copy import deepcopy
from dataclasses import replace
import io
import json
import zipfile
from PySide6.QtCore import QMimeData, QPointF, QRectF
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from .model import Document, load_document, png_bytes, composite_paper, uid
from .navigation import normalized_navigation, move_node
from .board import Change

MIME = 'application/x-atelier-sheets-v4'
LIMIT = 256*1024*1024


def clone_papers(papers):
    result = []
    for paper in papers:
        layers = [replace(layer, id=uid(), image=QImage(layer.image)) for layer in paper.guide_layers]
        active = next((new.id for old, new in zip(paper.guide_layers, layers) if old.id == paper.active_guide_id), '')
        result.append(replace(paper, id=uid(), image=QImage(paper.image), guide_layers=layers,
                              active_guide_id=active, perspective=deepcopy(paper.perspective)))
    return result


def copy_sheets(board):
    board.pointer_up()
    papers = board.selected_papers()
    if not papers:
        board.message.emit('Выберите листы инструментом V. Ctrl + щелчок добавляет лист к выбору.')
        return False
    doc = Document(False)
    doc.papers = papers
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED) as archive:
        metadata = doc.metadata()
        metadata['clipboard_desk'] = getattr(board, 'desk_id', '')
        archive.writestr('board.json', json.dumps(metadata, ensure_ascii=False))
        for paper in papers:
            archive.writestr(f'images/{paper.id}.png', png_bytes(paper.image))
            for layer in paper.guide_layers:
                archive.writestr(f'guides/{paper.id}/{layer.id}.png', png_bytes(layer.image))
    if stream.tell() > LIMIT:
        board.message.emit('Слишком большой объём для буфера. Копируйте меньше листов за один раз.')
        return False
    mime = QMimeData()
    mime.setData(MIME, stream.getvalue())
    mime.setImageData(composite_paper(board.doc, papers[-1]))
    QApplication.clipboard().setMimeData(mime)
    board.message.emit(f'Скопировано листов: {len(papers)}. Ctrl+V — вставить на любой стол.')
    return True


def insert_sheets(board, originals, same_desk=False, title='Вставить листы'):
    if not originals:
        return []
    if len(board.doc.papers)+len(originals) > 100:
        board.message.emit('На одном столе можно разместить до 100 листов.')
        return []
    board.pointer_up()
    copies = clone_papers(originals)
    bounds = QRectF()
    for paper in copies:
        bounds = bounds.united(paper.transform().mapRect(QRectF(0, 0, paper.width, paper.height)))
    center = board.to_world(QPointF(board.width()/2, board.height()/2))
    board._paste_serial = getattr(board, '_paste_serial', 0)+1
    delta = QPointF(28, 28)*(min(board._paste_serial, 12)/max(.06, board.zoom))
    if not same_desk:
        delta += center-bounds.center()
    for paper in copies:
        paper.x += delta.x()
        paper.y += delta.y()
        paper.name = paper.name+' · копия'
    document = board.doc
    before_nav = deepcopy(document.navigation)
    after_nav = normalized_navigation(before_nav, document.papers+copies)
    for paper in copies:
        move_node(after_nav, paper.id, getattr(board, '_navigation_folder', None))
    selection = board.selected_papers()
    def undo():
        for paper in copies:
            document.papers.remove(paper)
        document.navigation = deepcopy(before_nav)
        board.set_paper_selection(selection)
    def redo():
        document.papers.extend(copies)
        document.navigation = deepcopy(after_nav)
        board.set_paper_selection(copies)
    board.history.push(Change(board, title, undo, redo))
    board.message.emit(f'Добавлено копий: {len(copies)}. Ctrl+Z — отменить.')
    return copies


def paste_sheets(board):
    mime = QApplication.clipboard().mimeData()
    if mime and mime.hasFormat(MIME):
        try:
            payload = bytes(mime.data(MIME))
            if len(payload) > LIMIT:
                raise ValueError('Слишком большой буфер обмена')
            doc, _ = load_document(io.BytesIO(payload))
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                origin = json.loads(archive.read('board.json')).get('clipboard_desk')
            return insert_sheets(board, doc.papers, origin == getattr(board, 'desk_id', ''))
        except (ValueError, TypeError, KeyError, OSError, zipfile.BadZipFile):
            board.message.emit('Не удалось прочитать листы из буфера. Скопируйте их заново.')
            return []
    image = QApplication.clipboard().image()
    if not image.isNull():
        board.add_reference(image, 'Из буфера обмена')
    else:
        board.message.emit('В буфере нет листов или изображения.')
    return []


def duplicate_sheets(board):
    return insert_sheets(board, board.selected_papers(), True, 'Дублировать листы')
