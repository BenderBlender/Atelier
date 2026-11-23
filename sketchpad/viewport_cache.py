"""Two screen-sized composites around the editable sheet, never whole documents."""
from PySide6.QtCore import QPointF, QRectF, QSize
from PySide6.QtGui import QImage, QPainter


def sheet_key(paper):
    return (paper.id, paper.image.cacheKey(), paper.name, paper.x, paper.y,
            paper.angle, paper.scale, paper.width, paper.height, paper.kind,
            paper.paper_style, paper.paper_opacity, paper.ink_opacity,
            paper.tracing, paper.locked, tuple(sorted(paper.perspective.items())),
            tuple((g.id,g.image.cacheKey(),g.visible,g.opacity,g.color) for g in paper.guide_layers))


class ViewportCache:
    def __init__(self):
        self.entries = {}
        self.builds = 0

    def clear(self):
        self.entries.clear()

    def draw(self, painter, board, sheets, slot, labels):
        if not sheets:
            self.entries.pop(slot, None)
            return
        dpr = board.devicePixelRatioF()
        key = (board.width(), board.height(), dpr, board.zoom,
               board.offset.x(), board.offset.y(), board.view_rotation,
               getattr(board.selected, 'id', None), board.tool,
               tuple(p.id for p in board.selected_papers()),
               tuple((p.id, labels[(p.x, p.y, p.angle, p.scale)].id) for p in sheets),
               tuple(sheet_key(p) for p in sheets))
        cached = self.entries.get(slot)
        if cached is None or cached[0] != key:
            size = QSize(max(1, round(board.width()*dpr)), max(1, round(board.height()*dpr)))
            image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(dpr)
            image.fill(0)
            draw = QPainter(image)
            draw.setRenderHint(QPainter.RenderHint.Antialiasing)
            draw.setTransform(board.view_transform())
            view = QRectF(board.rect())
            for paper in sheets:
                # Include the screen-sized label and shadow in the visibility test.
                bounds = board.view_transform().mapRect(paper.transform().mapRect(
                    QRectF(0, 0, paper.width, paper.height))).adjusted(-40, -40, 40, 40)
                if bounds.intersects(view):
                    board.draw_sheet(draw, paper, labels)
            draw.end()
            self.entries[slot] = (key, image)
            self.builds += 1
        painter.save()
        painter.resetTransform()
        painter.drawImage(QPointF(), self.entries[slot][1])
        painter.restore()
