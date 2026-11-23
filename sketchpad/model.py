from dataclasses import dataclass, field, asdict
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import uuid
import zipfile
import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QTransform
from .brush import empty_ink, image_array, paper_tooth


PAPER_STYLES = {
    'ivory': ('Светлая · слоновая кость', '#f5f1e8', 0.),
    'graphite': ('Графитовая · серая', '#626661', 9.),
    'anthracite': ('Антрацит · почти чёрная', '#292d30', 7.),
    'kraft': ('Крафт · тёмный', '#806249', 12.),
    'umber': ('Умбра · тёплая', '#514239', 9.),
    'slate': ('Сланец · холодная', '#43566b', 8.),
}


@lru_cache(maxsize=len(PAPER_STYLES))
def paper_brush(style='ivory'):
    """Texture lives in sheet coordinates and is shared by canvas and export."""
    _, color, texture = PAPER_STYLES[style]
    if not texture:
        return QBrush(QColor(color))
    image = empty_ink(512, 512)
    rgba = image_array(image)
    base = QColor(color)
    height = (paper_tooth()-.5)*texture
    for c, value in enumerate((base.red(), base.green(), base.blue())):
        rgba[..., c] = np.clip(np.round(value+height), 0, 255).astype(np.uint8)
    rgba[..., 3] = 255
    return QBrush(image)


def uid():
    return uuid.uuid4().hex[:12]


@dataclass
class GuideLayer:
    name: str = 'Направляющие'
    id: str = field(default_factory=uid)
    color: str = '#49a9da'
    opacity: float = .65
    visible: bool = True
    image: QImage = field(default_factory=QImage, repr=False)

    def meta(self):
        return {k: getattr(self, k) for k in self.__dataclass_fields__ if k != 'image'}


@dataclass
class Paper:
    id: str = field(default_factory=uid)
    name: str = 'Лист 01'
    x: float = 0.
    y: float = 0.
    width: int = 1400
    height: int = 1000
    angle: float = 0.
    scale: float = 1.
    kind: str = 'paper'
    image: QImage = field(default_factory=QImage, repr=False)
    locked: bool = False
    paper_opacity: float = 1.
    ink_opacity: float = 1.
    tracing: bool = False
    paper_style: str = 'ivory'
    perspective: dict = field(default_factory=dict)
    guide_layers: list = field(default_factory=list, repr=False)
    active_guide_id: str = ''

    def __post_init__(self):
        if self.image.isNull():
            self.image = empty_ink(self.width, self.height)
        if self.perspective:
            from .perspective import PerspectiveSettings
            self.perspective = PerspectiveSettings.read(self.perspective).data()
        elif not isinstance(self.perspective, dict):
            self.perspective = {}

    def transform(self):
        t = QTransform()
        t.translate(self.x, self.y)
        t.rotate(self.angle)
        t.scale(self.scale, self.scale)
        return t

    def local(self, point):
        return self.transform().inverted()[0].map(point)

    def contains(self, point):
        p = self.local(point)
        return 0 <= p.x() <= self.width and 0 <= p.y() <= self.height

    def meta(self):
        return {k: getattr(self, k) for k in self.__dataclass_fields__ if k not in ('image', 'perspective', 'guide_layers')}


@dataclass
class Pin:
    x: float
    y: float
    id: str = field(default_factory=uid)


@dataclass
class Band:
    pins: list
    id: str = field(default_factory=uid)
    name: str = 'Резинка 01'
    color: str = '#d4b77a'
    visible: bool = True


def project_line(point, start, end):
    dx, dy = end.x()-start.x(), end.y()-start.y()
    length = dx*dx + dy*dy
    if length < 1e-8:
        return point, float('inf')
    t = ((point.x()-start.x())*dx+(point.y()-start.y())*dy)/length
    projected = QPointF(start.x()+dx*t, start.y()+dy*t)
    return projected, math.hypot(projected.x()-point.x(), projected.y()-point.y())


class Document:
    def __init__(self, starter=True):
        self.papers = []
        self.pins = []
        self.bands = []
        self.navigation = []
        if starter:
            self.papers.append(Paper())
            a, b, c = Pin(-90, 280), Pin(1490, 280), Pin(700, 670)
            self.pins = [a, b, c]
            self.bands = [Band([a.id, c.id]), Band([b.id, c.id], name='Резинка 02')]

    def pin(self, id):
        return next((x for x in self.pins if x.id == id), None)

    def lines(self, band):
        if not band or not band.visible:
            return []
        pins = [self.pin(id) for id in band.pins]
        if len(pins) != 2 or any(p is None for p in pins):
            return []
        return [(QPointF(pins[0].x, pins[0].y), QPointF(pins[1].x, pins[1].y))]

    def metadata(self):
        from .navigation import normalized_navigation
        return {'version': 4, 'papers': [p.meta() for p in self.papers], 'pins': [asdict(p) for p in self.pins], 'bands': [asdict(b) for b in self.bands],
                'navigation': normalized_navigation(self.navigation, self.papers),
                'guide_layers': {p.id: [layer.meta() for layer in p.guide_layers] for p in self.papers if p.guide_layers},
                'perspective_guides': {p.id: dict(p.perspective) for p in self.papers if p.perspective}}


def png_bytes(image):
    array = QByteArray()
    buf = QBuffer(array)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buf, 'PNG'):
        raise OSError('Не удалось записать изображение')
    return bytes(array)


def save_document(doc, path, settings=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    meta = doc.metadata()
    meta['settings'] = settings or {}
    with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.writestr('board.json', json.dumps(meta, ensure_ascii=False))
        for paper in doc.papers:
            z.writestr(f'images/{paper.id}.png', png_bytes(paper.image))
            for layer in paper.guide_layers:
                z.writestr(f'guides/{paper.id}/{layer.id}.png', png_bytes(layer.image))
    os.replace(temp, path)


def load_document(path):
    with zipfile.ZipFile(path) as z:
        if sum(x.file_size for x in z.infolist()) > 512*1024*1024:
            raise ValueError('Проект слишком большой для прототипа (512 МБ)')
        meta = json.loads(z.read('board.json'))
        if meta.get('version') not in (1, 2, 3, 4):
            raise ValueError('Неизвестная версия проекта')
        if len(meta['papers']) > 100:
            raise ValueError('Слишком много листов')
        doc = Document(False)
        ids = set()
        for m in meta['papers']:
            if not (1 <= m['width'] <= 8192 and 1 <= m['height'] <= 8192 and m['width']*m['height'] <= 32_000_000):
                raise ValueError('Недопустимый размер листа')
            if m['id'] in ids or m['kind'] not in ('paper', 'reference'):
                raise ValueError('Повреждённая структура проекта')
            ids.add(m['id'])
            if not isinstance(m.get('paper_style', 'ivory'), str) or m.get('paper_style', 'ivory') not in PAPER_STYLES:
                raise ValueError('Неизвестный вид бумаги')
            if not all(math.isfinite(m[k]) for k in ('x', 'y', 'angle', 'scale')) or not .01 <= m['scale'] <= 100:
                raise ValueError('Недопустимое положение листа')
            for key in ('paper_opacity', 'ink_opacity'):
                if not isinstance(m.get(key, 1.), (int, float)) or not 0 <= m.get(key, 1.) <= 1:
                    raise ValueError('Недопустимая прозрачность листа')
            image = QImage.fromData(z.read(f"images/{m['id']}.png")).convertToFormat(QImage.Format.Format_RGBA8888)
            if image.isNull() or image.width() != m['width'] or image.height() != m['height']:
                raise ValueError('Изображение листа повреждено')
            doc.papers.append(Paper(**m, image=image))
        doc.pins = [Pin(**p) for p in meta['pins']]
        for data in meta['bands']:
            data = dict(data)
            if meta['version'] < 4 and 'x' in data and 'y' in data:
                # Preserve old V-shaped guides exactly, replacing their ring
                # with a real shared pin and each arm with a direct connection.
                center = Pin(data.pop('x'), data.pop('y'))
                doc.pins.append(center)
                for i, pin_id in enumerate(data.pop('pins')):
                    values = {**data, 'pins': [pin_id, center.id]}
                    if i:
                        values.update(id=uid(), name=data.get('name', 'Резинка')+f' · {i+1}')
                    doc.bands.append(Band(**values))
            else:
                doc.bands.append(Band(**data))
        for paper in doc.papers:
            layers = meta.get('guide_layers', {}).get(paper.id, [])
            if not isinstance(layers, list) or len(layers) > 32:
                raise ValueError('Слишком много слоёв направляющих')
            layer_ids = set()
            for data in layers:
                layer = GuideLayer(**data)
                if (layer.id in layer_ids or not QColor(layer.color).isValid()
                        or not isinstance(layer.opacity, (int, float)) or not 0 <= layer.opacity <= 1):
                    raise ValueError('Повреждён слой направляющих')
                layer_ids.add(layer.id)
                layer.image = QImage.fromData(z.read(f'guides/{paper.id}/{layer.id}.png')).convertToFormat(QImage.Format.Format_RGBA8888)
                if layer.image.isNull() or layer.image.size() != paper.image.size():
                    raise ValueError('Повреждено изображение направляющих')
                paper.guide_layers.append(layer)
        from .navigation import normalized_navigation
        doc.navigation = normalized_navigation(meta.get('navigation'), doc.papers)
        from .perspective import PerspectiveSettings
        guides = meta.get('perspective_guides', {})
        if isinstance(guides, dict):
            for paper in doc.papers:
                if paper.id in guides:
                    paper.perspective = PerspectiveSettings.read(guides[paper.id]).data()
        if len(doc.pins) > 1000 or len(doc.bands) > 1000:
            raise ValueError('Слишком много направляющих')
        for obj in doc.pins:
            if not math.isfinite(obj.x) or not math.isfinite(obj.y):
                raise ValueError('Недопустимые координаты направляющей')
        for b in doc.bands:
            if len(b.pins) != 2 or b.pins[0] == b.pins[1] or any(doc.pin(p) is None for p in b.pins):
                raise ValueError('Резинка ссылается на отсутствующий пин')
        return doc, meta.get('settings', {})


def draw_paper_content(painter, paper, guide=None):
    """Paper and ink have independent opacity: tracing must not fade new ink."""
    from PySide6.QtCore import QRectF
    painter.save()
    if paper.kind == 'paper':
        painter.setOpacity(paper.paper_opacity)
        painter.fillRect(QRectF(0, 0, paper.width, paper.height), paper_brush(paper.paper_style))
    painter.setOpacity(paper.ink_opacity)
    if guide:
        guide(painter, paper, False)
    painter.drawImage(0, 0, paper.image)
    if guide:
        guide(painter, paper, True)
    painter.restore()


def composite_paper(doc, paper, transparent=False):
    """Crop the visible stack through the selected sheet to its original pixels."""
    if transparent:
        out = empty_ink(paper.width, paper.height)
        p = QPainter(out)
        p.setOpacity(paper.ink_opacity)
        p.drawImage(0, 0, paper.image)
        p.end()
        return out
    out = QImage(paper.width, paper.height, QImage.Format.Format_RGB32)
    out.fill(QColor('#f5f1e8'))
    p = QPainter(out)
    inverse = paper.transform().inverted()[0]
    for item in doc.papers:
        p.save()
        p.setTransform(inverse)
        p.setTransform(item.transform(), True)
        draw_paper_content(p, item)
        p.restore()
        if item is paper:
            break
    p.end()
    return out


def export_paper(paper, path, doc=None, transparent=False):
    if doc is None:
        doc = Document(False)
        doc.papers = [paper]
    out = composite_paper(doc, paper, transparent)
    if not out.save(str(path), 'PNG'):
        raise OSError('Не удалось экспортировать PNG')
