"""Immutable image snapshots compressed off the pen/UI thread."""
from dataclasses import replace
from copy import deepcopy
from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage
from .model import Document, save_document


class SaveSignals(QObject):
    finished = Signal(object, int, str)


class RecoverySave(QRunnable):
    def __init__(self, board, path, settings):
        super().__init__()
        self.signals = SaveSignals()
        self.board = board
        self.revision = getattr(board, '_desk_revision', 0)
        self.path, self.settings = path, settings
        self.doc = Document(False)
        # QImage is implicitly shared. Subsequent painting detaches the editable
        # image, leaving this snapshot intact; the worker never reads Board.
        self.doc.papers = [replace(p, image=QImage(p.image),
            guide_layers=[replace(layer, image=QImage(layer.image)) for layer in p.guide_layers]) for p in board.doc.papers]
        self.doc.pins = [replace(p) for p in board.doc.pins]
        self.doc.bands = [replace(b, pins=list(b.pins)) for b in board.doc.bands]
        self.doc.navigation = deepcopy(board.doc.navigation)

    def run(self):
        error = ''
        try:
            save_document(self.doc, self.path, self.settings)
        except Exception as exc:
            error = str(exc)
        self.signals.finished.emit(self.board, self.revision, error)
