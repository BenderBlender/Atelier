from dataclasses import replace
import math
import time
from PySide6.QtCore import Qt, QEvent, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QPainterPath, QPolygonF, QTransform, QUndoCommand, QUndoStack, QPointingDevice
from PySide6.QtWidgets import QWidget
from .model import Document, Paper, Pin, Band, GuideLayer, PAPER_STYLES, project_line, draw_paper_content
from .brush import BrushSettings, GraphiteStroke, pencil_contact
from .inpaint import SelectionMask, MASK_TOOLS
from .viewport_cache import ViewportCache
from .perspective import PerspectiveSettings, PerspectiveRenderer


class Change(QUndoCommand):
    def __init__(self, board, title, undo, redo):
        super().__init__(title)
        self.board, self.undo_fn, self.redo_fn = getattr(board, 'history_owner', board), undo, redo

    def undo(self):
        self.undo_fn()
        self.board.changed.emit()
        self.board.update()

    def redo(self):
        self.redo_fn()
        self.board.changed.emit()
        self.board.update()


class PaintChange(QUndoCommand):
    def __init__(self, board, paper, before, after):
        super().__init__('Штрих')
        self.board, self.paper, self.before, self.after = getattr(board, 'history_owner', board), paper, before, after
        self.first = True

    def apply(self, patches):
        p = QPainter(self.paper.image)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        for (x, y), image in patches.items():
            p.drawImage(x, y, image)
        p.end()
        self.board.changed.emit()
        self.board.update()

    def undo(self):
        self.apply(self.before)

    def redo(self):
        if self.first:
            self.first = False
            self.board.changed.emit()
        else:
            self.apply(self.after)


class PropertyChange(QUndoCommand):
    def __init__(self, board, obj, before, after, title):
        super().__init__(title)
        self.board, self.obj = getattr(board, 'history_owner', board), obj
        self.before, self.after = dict(before), dict(after)
        self.timestamp = time.monotonic()

    def id(self):
        return 101

    def mergeWith(self, other):
        if (not isinstance(other, PropertyChange) or other.obj is not self.obj
                or self.after.keys() != other.after.keys() or self.text() != other.text()
                or other.timestamp-self.timestamp > .65):
            return False
        self.after = dict(other.after)
        self.timestamp = other.timestamp
        return True

    def apply(self, values):
        for key, value in values.items():
            setattr(self.obj, key, value)
        self.board.changed.emit()
        self.board.update()

    def undo(self):
        self.apply(self.before)

    def redo(self):
        self.apply(self.after)


class Board(QWidget):
    changed = Signal()
    mask_changed = Signal()
    selection_changed = Signal()
    telemetry = Signal(dict)
    message = Signal(str)
    tool_changed = Signal(str)
    zoom_changed = Signal(float)
    rotation_changed = Signal(float)
    brush_size_step = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.brush_preset = 'Графит · HB'
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TabletTracking, True)
        self.doc = Document()
        self.history = QUndoStack(self)
        self.history.setUndoLimit(60)
        self.brush = BrushSettings()
        self.eraser_size = 22.
        self.zoom = .65
        self.offset = QPointF(120, 100)
        self.view_rotation = 0.
        self.rotate_key_tool = None
        self.rotate_key_started = 0.
        self.rotate_key_dragged = False
        self.perspective_roll_key = False
        self.tool = 'pencil'
        self.selected = self.doc.papers[0]
        self.selected_paper_ids = {self.selected.id}
        self.focused_paper_id = None
        self.band = self.doc.bands[0]
        self.dual = False
        self.dual_paused = False
        self.snap = False
        self.guide_visible = True
        self.extensions = False
        self.mouse_gain = 1.
        self.stroke = None
        self.stroke_source = None
        self.stroke_paper = None
        self.stroke_line = None
        self.stroke_target = None
        self.guide_drawing = False
        self.last_sample = None
        self.drag = None
        self.drag_source = None
        self.drag_button = None
        self.space = False
        self.cursor_world = None
        self.cursor_tilt = (0., 0.)
        self.cursor_pressure = .5
        self.pen_seen = 0.
        self.pen_events = 0
        self.pending_pin = None
        self.telemetry_at = 0.
        self.dual_before = None
        self.mouse_target_mode = 'pin'
        self.mouse_pin = self.doc.pins[-1]
        self.native_pen = False
        self.inpaint = SelectionMask(self)
        self.viewport_cache = ViewportCache()
        self.perspective_renderer = PerspectiveRenderer()
        self.use_viewport_cache = True
        self.refresh_cursor()

    def uses_brush_cursor(self):
        panning = (self.space and not self.dual) or (self.drag and self.drag[0] == 'pan')
        return self.tool in ('pencil', 'eraser', *MASK_TOOLS) and not panning

    def refresh_cursor(self):
        if self.uses_brush_cursor():
            shape = Qt.CursorShape.BlankCursor
        elif self.drag and self.drag[0] in ('pan', 'rotate', 'perspective'):
            shape = Qt.CursorShape.ClosedHandCursor
        elif self.tool in ('hand', 'rotate', 'perspective') or (self.space and not self.dual):
            shape = Qt.CursorShape.OpenHandCursor
        elif self.tool in ('pin', 'band'):
            shape = Qt.CursorShape.CrossCursor
        else:
            shape = Qt.CursorShape.ArrowCursor
        self.setCursor(shape)

    def view_transform(self):
        transform = QTransform()
        transform.translate(self.offset.x(), self.offset.y())
        transform.scale(self.zoom, self.zoom)
        transform.rotate(self.view_rotation)
        return transform

    def to_world(self, p):
        return self.view_transform().inverted()[0].map(p)

    def to_screen(self, p):
        return self.view_transform().map(p)

    def set_view_rotation(self, degrees, anchor=None):
        if not math.isfinite(degrees):
            return
        self.finish_stroke()
        self.flush_dual_move()
        anchor = QPointF(self.width()/2, self.height()/2) if anchor is None else anchor
        fixed = self.to_world(anchor)
        cursor = self.to_screen(self.cursor_world) if self.cursor_world is not None else None
        self.view_rotation = (degrees+180)%360-180
        self.offset += anchor-self.to_screen(fixed)
        if cursor is not None:
            self.cursor_world = self.to_world(cursor)
        self.rotation_changed.emit(self.view_rotation)
        self.update()

    def reset_view_rotation(self):
        self.pointer_up()
        self.set_view_rotation(0.)

    def set_tool(self, tool):
        self.pointer_up()
        self.perspective_roll_key = False
        if tool not in ('pencil','eraser'):
            self.guide_drawing = False
        if tool == 'perspective':
            paper = self.selected if isinstance(self.selected, Paper) else next(iter(reversed(self.visible_papers())), None)
            if paper is not None:
                self.set_selection(paper)
                settings = PerspectiveSettings.read(paper.perspective)
                if not settings.enabled:
                    self.push_move(paper, {'perspective': paper.perspective},
                                   {'perspective': {**settings.data(), 'enabled': True}}, 'Объёмная сетка')
        self.tool = tool
        self.pending_pin = None
        self.refresh_cursor()
        self.tool_changed.emit(tool)
        self.update()

    def fit(self):
        self.finish_stroke()
        self.focused_paper_id = None
        bounds = QRectF()
        for p in self.doc.papers:
            bounds = bounds.united(p.transform().mapRect(QRectF(0, 0, p.width, p.height)))
        for pin in self.doc.pins:
            bounds = bounds.united(QRectF(pin.x-10, pin.y-10, 20, 20))
        if bounds.isEmpty():
            bounds = QRectF(0, 0, 1400, 1000)
        bounds.adjust(-80, -100, 80, 100)
        bounds = QTransform().rotate(self.view_rotation).mapRect(bounds)
        self.zoom = max(.08, min(2., min(self.width()/bounds.width(), self.height()/bounds.height())))
        self.offset = QPointF(self.width()/2, self.height()/2)-bounds.center()*self.zoom
        self.zoom_changed.emit(self.zoom)
        self.update()

    def paper_at(self, world, drawable=False):
        focused = self.focused_paper()
        if focused and not focused.contains(world):
            return None
        for paper in reversed(self.visible_papers()):
            if paper.contains(world):
                return paper if not drawable or (paper.kind == 'paper' and not paper.locked) else None
        return None

    def visible_papers(self):
        # Opening an underlying sheet must reveal it without changing stack
        # order. Lower sheets remain available through tracing transparency.
        if self.focused_paper_id:
            for i, paper in enumerate(self.doc.papers):
                if paper.id == self.focused_paper_id:
                    if paper.kind == 'paper' and paper.paper_opacity >= .999:
                        return [paper]
                    bounds = paper.transform().mapRect(QRectF(0, 0, paper.width, paper.height))
                    return [item for item in self.doc.papers[:i+1]
                            if item.transform().mapRect(QRectF(0, 0, item.width, item.height)).intersects(bounds)]
        return self.doc.papers

    def focused_paper(self):
        return next((p for p in self.doc.papers if p.id == self.focused_paper_id), None)

    def set_selection(self, obj):
        self.selected = obj
        self.selected_paper_ids = {obj.id} if isinstance(obj, Paper) else set()
        if self.focused_paper_id and isinstance(obj, Paper):
            self.focused_paper_id = obj.id
        if isinstance(obj, Pin):
            self.set_mouse_pin(obj)
        self.selection_changed.emit()
        self.update()

    def selected_papers(self):
        if not isinstance(self.selected, Paper) or self.selected not in self.doc.papers:
            return []
        ids = self.selected_paper_ids if self.selected.id in self.selected_paper_ids else {self.selected.id}
        return [p for p in self.doc.papers if p.id in ids]

    def set_paper_selection(self, papers):
        papers = [p for p in papers if p in self.doc.papers]
        self.selected = papers[-1] if papers else None
        self.selected_paper_ids = {p.id for p in papers}
        if len(papers) > 1:
            self.focused_paper_id = None
        elif self.focused_paper_id and papers:
            self.focused_paper_id = papers[0].id
        self.selection_changed.emit()
        self.update()

    def toggle_paper_selection(self, paper):
        papers = self.selected_papers()
        papers.remove(paper) if paper in papers else papers.append(paper)
        self.set_paper_selection(papers)

    def set_mouse_pin(self, pin):
        if pin is not self.mouse_pin:
            self.flush_dual_move()
            self.mouse_pin = pin

    def mouse_target(self):
        return self.mouse_pin if self.mouse_pin in self.doc.pins else None

    def set_mouse_target_mode(self, mode):
        self.flush_dual_move()
        self.mouse_target_mode = 'pin'
        self.update()

    def control_hit(self, world):
        if not self.guide_visible:
            return None
        for pin in reversed(self.doc.pins):
            if math.hypot(pin.x-world.x(), pin.y-world.y())*self.zoom < 13:
                return pin
        for band in reversed(self.doc.bands):
            for a, b in self.doc.lines(band):
                projected, distance = project_line(world, a, b)
                if distance*self.zoom < 6 and (projected-a).manhattanLength()+(projected-b).manhattanLength() <= (b-a).manhattanLength()+1e-6:
                    return band
        return None

    def push_move(self, obj, before, after, title='Перемещение'):
        if before == after:
            return
        self.history.push(PropertyChange(self, obj, before, after, title))

    def add_paper(self, width=1400, height=1000):
        center = self.to_world(QPointF(self.width()/2, self.height()/2))
        n = sum(p.kind == 'paper' for p in self.doc.papers)+1
        paper = Paper(name=f'Лист {n:02d}', x=center.x()-width/2+60, y=center.y()-height/2+60, width=width, height=height)
        self.history.push(Change(self, 'Новый лист', lambda: self.doc.papers.remove(paper), lambda: self.doc.papers.append(paper)))
        self.set_selection(paper)
        self.set_tool('select')

    def add_reference(self, image, name, world=None):
        if image.isNull():
            self.message.emit('Не удалось прочитать изображение')
            return
        from PySide6.QtGui import QImage
        if max(image.width(), image.height()) > 2400:
            image = image.scaled(2400, 2400, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        center = world or self.to_world(QPointF(self.width()/2, self.height()/2))
        paper = Paper(name=name, x=center.x(), y=center.y(), width=image.width(), height=image.height(), kind='reference', scale=min(1., 500/image.width()), image=image.convertToFormat(QImage.Format.Format_RGBA8888))
        self.history.push(Change(self, 'Референс', lambda: self.doc.papers.remove(paper), lambda: self.doc.papers.append(paper)))
        self.set_selection(paper)
        self.set_tool('select')

    def set_paper_style(self, style):
        paper = self.selected
        if (style not in PAPER_STYLES or not isinstance(paper, Paper)
                or paper not in self.doc.papers or paper.kind != 'paper'):
            return
        self.finish_stroke()
        self.push_move(paper, {'paper_style': paper.paper_style}, {'paper_style': style}, 'Бумага листа')
        self.selection_changed.emit()

    def add_tracing(self):
        self.finish_stroke()
        source = self.selected
        if not isinstance(source, Paper) or source not in self.doc.papers:
            self.message.emit('Сначала выберите лист или референс для кальки.')
            return
        paper = Paper(name=f'Калька · {source.name}', x=source.x, y=source.y,
                      width=source.width, height=source.height, angle=source.angle,
                      scale=source.scale, paper_opacity=.25, tracing=True)
        self.history.push(Change(self, 'Добавить кальку', lambda: self.doc.papers.remove(paper), lambda: self.doc.papers.append(paper)))
        self.set_selection(paper)
        self.set_tool('pencil')
        self.message.emit('Калька добавлена поверх скетча. Прозрачность — в разделе «Объект на доске».')

    def clear_paper(self, guide=False):
        self.finish_stroke()
        paper = self.selected
        if not isinstance(paper, Paper) or paper not in self.doc.papers or paper.kind != 'paper':
            self.message.emit('Выберите лист или кальку для очистки.')
            return
        if paper.locked:
            self.message.emit('Сначала снимите закрепление листа.')
            return
        target = self.active_guide(paper) if guide else paper
        if target is None:
            self.message.emit('На этом листе нет слоя направляющих для очистки.')
            return
        before = target.image.copy()
        def clear():
            target.image.fill(0)
        def restore():
            target.image = before.copy()
        self.history.push(Change(self, 'Очистить слой направляющих' if guide else 'Очистить лист', restore, clear))
        self.message.emit(f'Слой «{target.name}» очищен. Ctrl+Z — вернуть направляющие.' if guide else
                          f'«{paper.name}» очищен. Ctrl+Z — вернуть рисунок.')

    def add_pin(self, world):
        pin = Pin(world.x(), world.y())
        self.history.push(Change(self, 'Новый пин', lambda: self.doc.pins.remove(pin), lambda: self.doc.pins.append(pin)))
        self.set_selection(pin)
        return pin

    def add_band(self, pins=None):
        if not pins:
            self.set_tool('band')
            return
        if len(pins) != 2 or pins[0] == pins[1] or any(self.doc.pin(id) is None for id in pins):
            return
        existing = next((b for b in self.doc.bands if set(b.pins) == set(pins)), None)
        if existing:
            self.band = existing
            self.set_selection(existing)
            return existing
        band = Band(list(pins), name=f'Резинка {len(self.doc.bands)+1:02d}', color=['#d4b77a', '#84b9ba', '#c999ab', '#a7b78b'][len(self.doc.bands)%4])
        def add():
            self.doc.bands.append(band)
        def remove():
            self.doc.bands.remove(band)
        self.history.push(Change(self, 'Новая резинка', remove, add))
        self.band = band
        self.set_selection(band)
        return band

    def delete_band(self):
        if self.band in self.doc.bands:
            self.set_selection(self.band)
            self.delete_selected()

    def delete_selected(self):
        self.finish_stroke()
        obj = self.selected
        if obj is None:
            return
        if isinstance(obj, Paper):
            if obj not in self.doc.papers:
                return
            items = [(self.doc.papers.index(p), p) for p in self.selected_papers() if not p.locked]
            if not items:
                self.message.emit('Выбранные листы закреплены.')
                return
            def restore_papers():
                for i, p in items:
                    self.doc.papers.insert(i, p)
                self.set_paper_selection([p for _, p in items])
            def remove_papers():
                for _, p in items:
                    self.doc.papers.remove(p)
                self.set_selection(None)
            self.history.push(Change(self, 'Удалить листы', restore_papers, remove_papers))
        elif isinstance(obj, Band):
            if obj not in self.doc.bands:
                return
            i = self.doc.bands.index(obj)
            self.history.push(Change(self, 'Удалить резинку', lambda: self.doc.bands.insert(i, obj), lambda: self.doc.bands.remove(obj)))
        elif isinstance(obj, Pin):
            if obj not in self.doc.pins:
                return
            i = self.doc.pins.index(obj)
            attached = [(j, b) for j, b in enumerate(self.doc.bands) if obj.id in b.pins]
            def remove():
                self.doc.pins.remove(obj)
                for _, b in attached:
                    self.doc.bands.remove(b)
            def restore():
                self.doc.pins.insert(i, obj)
                for j, b in attached:
                    self.doc.bands.insert(j, b)
            self.history.push(Change(self, 'Удалить пин', restore, remove))
        self.set_selection(None)

    def stroke_settings(self, eraser=False):
        settings = replace(self.brush)
        settings.eraser = self.tool == 'eraser' or eraser
        if settings.eraser and settings.eraser_shape != 'pencil':
            settings.size, settings.smoothing = self.eraser_size, 0.
        return settings

    def active_guide(self, paper=None):
        paper = paper or self.selected
        if not isinstance(paper, Paper):
            return None
        return next((layer for layer in paper.guide_layers if layer.id == paper.active_guide_id),
                    paper.guide_layers[-1] if paper.guide_layers else None)

    def add_guide_layer(self, paper=None):
        from .brush import empty_ink
        paper = paper or self.selected
        if not isinstance(paper, Paper) or paper.locked or paper.kind != 'paper':
            return None
        if len(paper.guide_layers) >= 32:
            self.message.emit('На листе уже 32 слоя направляющих.')
            return None
        self.finish_stroke()
        old = paper.active_guide_id
        layer = GuideLayer(name=f'Направляющие {len(paper.guide_layers)+1:02d}', image=empty_ink(paper.width,paper.height))
        def add():
            paper.guide_layers.append(layer)
            paper.active_guide_id = layer.id
        def remove():
            paper.guide_layers.remove(layer)
            paper.active_guide_id = old
        self.history.push(Change(self, 'Новый слой направляющих', remove, add))
        self.selection_changed.emit()
        return layer

    def delete_guide_layer(self):
        paper = self.selected
        layer = self.active_guide(paper)
        if layer is None or paper.locked:
            return
        self.finish_stroke()
        index, old = paper.guide_layers.index(layer), paper.active_guide_id
        def remove():
            paper.guide_layers.remove(layer)
            paper.active_guide_id = paper.guide_layers[-1].id if paper.guide_layers else ''
        def restore():
            paper.guide_layers.insert(index,layer)
            paper.active_guide_id = old
        self.history.push(Change(self, 'Удалить слой направляющих', restore, remove))
        self.selection_changed.emit()

    def set_guide_color(self, color):
        from .brush import image_array
        layer = self.active_guide()
        if layer is None or self.selected.locked or not QColor(color).isValid():
            return
        self.finish_stroke()
        before = {'color': layer.color, 'image': layer.image.copy()}
        image = layer.image.copy()
        c = QColor(color)
        image_array(image)[...,:3] = (c.red(),c.green(),c.blue())
        self.push_move(layer,before,{'color':c.name(),'image':image},'Цвет направляющих')
        self.selection_changed.emit()

    def set_guide_drawing(self, enabled):
        enabled = bool(enabled) and self.tool in ('pencil','eraser')
        if enabled == self.guide_drawing:
            return
        # Split a held stroke at the modifier boundary without moving its tip.
        sample, source = self.last_sample, self.stroke_source
        eraser = self.stroke.settings.eraser if self.stroke else False
        self.finish_stroke()
        self.guide_drawing = enabled
        if sample and source:
            self.start_stroke(*sample, eraser=eraser, source=source)
        self.update()

    def start_stroke(self, world, pressure, tx, ty, eraser=False, source='pen'):
        paper = self.paper_at(world, True)
        if not paper:
            return
        self.set_selection(paper)
        self.stroke_paper = paper
        s = self.stroke_settings(eraser)
        target = paper
        if self.guide_drawing:
            target = self.active_guide(paper) or self.add_guide_layer(paper)
            if target is None:
                self.stroke_paper = None
                return
            if not target.visible:
                self.push_move(target, {'visible': False}, {'visible': True}, 'Показать направляющие')
            color = QColor(target.color)
            s.color = (color.red(),color.green(),color.blue())
        self.stroke_paper = paper
        self.stroke_target = target
        self.stroke = GraphiteStroke(target.image, s)
        self.stroke_source = source
        self.stroke_line = None
        if self.snap and not s.eraser and self.guide_visible:
            candidates = []
            for b in self.doc.bands:
                if self.dual:
                    if not self.mouse_pin or self.mouse_pin.id not in b.pins:
                        continue
                for i, (a, end) in enumerate(self.doc.lines(b)):
                    _, distance = project_line(world, a, end)
                    candidates.append((distance, b, i))
            if candidates:
                distance, b, i = min(candidates, key=lambda item: item[0])
                if distance*self.zoom < 28:
                    self.stroke_line = (b, i)
        self.sample_stroke(world, pressure, tx, ty)

    def sample_stroke(self, world, pressure, tx, ty, final=False):
        if not self.stroke:
            return
        if self.stroke_line:
            b, i = self.stroke_line
            lines = self.doc.lines(b)
            if i < len(lines):
                world, _ = project_line(world, *lines[i])
        local = self.stroke_paper.local(world)
        # Tilt direction must rotate with the sheet; pencil dimensions are paper pixels.
        angle = math.radians(-self.stroke_paper.angle-self.view_rotation)
        sx, sy = math.tan(math.radians(tx)), math.tan(math.radians(ty))
        ltx = math.degrees(math.atan(sx*math.cos(angle)-sy*math.sin(angle)))
        lty = math.degrees(math.atan(sx*math.sin(angle)+sy*math.cos(angle)))
        self.stroke.add(local.x(), local.y(), pressure, ltx, lty, final)
        self.last_sample = (world, pressure, tx, ty)
        self.update()

    def finish_stroke(self):
        if self.stroke:
            before, after = self.stroke.patches()
            if before:
                self.history.push(PaintChange(self, self.stroke_target, before, after))
        self.stroke = self.stroke_paper = self.stroke_line = self.last_sample = self.stroke_source = self.stroke_target = None

    def set_dual(self, active):
        self.pointer_up()
        self.flush_dual_move()
        self.dual = active
        self.dual_paused = False
        self.space = False
        self.refresh_cursor()
        if active:
            if self.mouse_pin not in self.doc.pins:
                self.mouse_pin = self.doc.pins[0] if self.doc.pins else self.add_pin(self.to_world(QPointF(self.width()/2, self.height()/2)))
            self.set_tool('pencil')
        self.message.emit('Две руки: мышь двигает выбранный пин, перо рисует. Space — пауза, M — ручной режим.' if active else 'Ручной режим: перетащите пины, затем рисуйте.')
        self.update()

    def raw_move(self, dx, dy):
        target = self.mouse_target()
        if not self.dual or self.dual_paused or self.tool in ('rotate', 'perspective') or not self.window().isActiveWindow() or target is None:
            return
        if self.dual_before is None:
            self.dual_before = (target, {'x': target.x, 'y': target.y})
        delta = (self.to_world(QPointF(dx, dy))-self.to_world(QPointF()))*self.mouse_gain
        target.x += delta.x()
        target.y += delta.y()
        # Vanishing points may intentionally be outside the visible sheet/viewport.
        self.changed.emit()
        self.update()

    def flush_dual_move(self):
        if self.dual_before:
            obj, before = self.dual_before
            self.dual_before = None
            self.push_move(obj, before, {'x': obj.x, 'y': obj.y}, 'Движение пина мышью' if isinstance(obj, Pin) else 'Движение резинки мышью')

    def rotation_drag(self, pos, angle):
        pivot = QPointF(self.width()/2, self.height()/2)
        vector = pos-pivot
        return {'pivot': pivot, 'last': QPointF(pos), 'angle': angle,
                'linear': math.hypot(vector.x(), vector.y()) < 32}

    @staticmethod
    def drag_rotation_angle(state, pos, modifiers):
        old, new = state['last']-state['pivot'], pos-state['pivot']
        if state['linear']:
            delta = (pos.x()-state['last'].x())*.4
        elif math.hypot(new.x(), new.y()) >= 16:
            delta = math.degrees(math.atan2(old.x()*new.y()-old.y()*new.x(),
                                           old.x()*new.x()+old.y()*new.y()))
        else:
            return None
        state['angle'] += delta
        state['last'] = QPointF(pos)
        return round(state['angle']/15)*15 if modifiers & Qt.KeyboardModifier.ShiftModifier else state['angle']

    def pointer_down(self, pos, button, modifiers, pen=False, pressure=.5, tx=0, ty=0, eraser=False):
        self.setFocus()
        world = self.to_world(pos)
        source = 'pen' if pen else 'mouse'
        if pen or (self.stroke_source != 'pen' and self.inpaint.owner != 'pen'):
            self.cursor_world = world
            self.cursor_tilt = (tx, ty)
            self.cursor_pressure = pressure or .5
        if (self.space and not (self.dual and pen)) or button == Qt.MouseButton.MiddleButton or self.tool == 'hand':
            if not pen:
                self.drag = ('pan', pos, QPointF(self.offset))
                self.drag_source, self.drag_button = source, button
                self.refresh_cursor()
            return
        if button != Qt.MouseButton.LeftButton and not eraser:
            return
        if self.tool == 'perspective':
            if self.drag and self.drag_source != source:
                return
            paper = self.selected
            if isinstance(paper, Paper) and paper in self.doc.papers:
                settings = PerspectiveSettings.read(paper.perspective)
                if settings.enabled and not settings.locked:
                    motion = {'mode': 'move' if modifiers & Qt.KeyboardModifier.ShiftModifier else 'orbit'}
                    if self.perspective_roll_key:
                        motion = {**self.rotation_drag(pos, settings.roll), 'mode': 'roll'}
                    self.drag = ('perspective', paper, QPointF(pos), paper.perspective,
                                 motion)
                    self.drag_source, self.drag_button = source, button
                    self.refresh_cursor()
                elif settings.locked:
                    self.message.emit('Сетка зафиксирована. Снимите фиксацию во вкладке «Объём».')
            return
        if self.tool == 'rotate':
            if self.drag and self.drag_source != source:
                return
            self.drag = ('rotate', self.rotation_drag(pos, self.view_rotation))
            self.drag_source, self.drag_button = source, button
            self.refresh_cursor()
            self.update()
            return
        if self.tool in MASK_TOOLS:
            self.inpaint.begin(world, source, self.tool == 'mask_remove' or eraser or bool(modifiers & Qt.KeyboardModifier.AltModifier), self.tool == 'mask_rect')
            return
        if self.dual and not pen:
            return
        if self.tool == 'pin':
            self.add_pin(world)
            return
        if self.tool == 'band':
            hit = self.control_hit(world)
            if not isinstance(hit, Pin):
                self.pending_pin = None
                self.message.emit('Выберите установленный пин. P — поставить новый.')
            elif self.pending_pin not in self.doc.pins:
                self.pending_pin = hit
                self.set_selection(hit)
                self.message.emit('Выберите второй пин. Можно соединить первый пин с несколькими другими. Esc — закончить.')
            elif hit is self.pending_pin:
                self.pending_pin = None
            else:
                self.add_band([self.pending_pin.id, hit.id])
            self.update()
            return
        hit = self.control_hit(world)
        # A manual gesture starting on a guide belongs to that guide until
        # release, for both pen and mouse. Dual mode reserves the pen for ink.
        if hit and not self.dual and (isinstance(hit, Pin) or self.tool == 'select'):
            if self.drag and self.drag_source != source:
                return
            self.set_selection(hit)
            if isinstance(hit, Band):
                self.band = hit
                self.selection_changed.emit()
                return
            self.drag = ('object', hit, world, {'x': hit.x, 'y': hit.y})
            self.drag_source, self.drag_button = source, button
            return
        # Paper headers stay draggable in pencil mode.
        header = None
        for paper in reversed(self.visible_papers()):
            local = paper.local(world)
            if 0 <= local.x() <= paper.width and -34/self.zoom/paper.scale <= local.y() < 0:
                header = paper
                break
        if self.tool == 'select' or (header and not pen):
            if self.drag and self.drag_source != source:
                return
            paper = header or self.paper_at(world)
            additive = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
            if paper and additive:
                self.toggle_paper_selection(paper)
                return
            if not paper:
                initial = self.selected_papers() if additive else []
                self.set_paper_selection(initial)
                self.drag = ('marquee', world, world, initial)
                self.drag_source, self.drag_button = source, button
                return
            if paper not in self.selected_papers():
                self.set_selection(paper)
            if paper and not paper.locked:
                local = paper.local(world)
                corner = paper.transform().map(QPointF(paper.width, paper.height))
                papers = self.selected_papers()
                if len(papers) > 1:
                    self.drag = ('papers', [(p, (p.x, p.y)) for p in papers if not p.locked], world)
                elif (corner-world).manhattanLength()*self.zoom < 24:
                    self.drag = ('resize', paper, world, {'scale': paper.scale})
                else:
                    self.drag = ('object', paper, world, {'x': paper.x, 'y': paper.y})
                self.drag_source, self.drag_button = source, button
            return
        if self.tool in ('pencil', 'eraser'):
            if pen and self.stroke_source == 'mouse':
                # Some drivers emit an unmarked mouse press just before the
                # first tablet packet. The pressure-bearing pen takes priority.
                self.finish_stroke()
            if self.stroke and self.stroke_source != source:
                return
            self.set_guide_drawing(bool(modifiers & Qt.KeyboardModifier.AltModifier))
            self.start_stroke(world, pressure, tx, ty, eraser, source)

    def pointer_move(self, pos, pen=False, pressure=.5, tx=0, ty=0, modifiers=Qt.KeyboardModifier.NoModifier):
        if self.stroke_source == ('pen' if pen else 'mouse'):
            self.set_guide_drawing(bool(modifiers & Qt.KeyboardModifier.AltModifier))
        world = self.to_world(pos)
        source = 'pen' if pen else 'mouse'
        if pen or (self.stroke_source != 'pen' and self.inpaint.owner != 'pen'):
            self.cursor_world = world
            self.cursor_tilt = (tx, ty)
            self.cursor_pressure = pressure or .5
        if self.inpaint.owner == source:
            self.inpaint.move(world, source)
        elif self.drag and self.drag_source == source:
            kind = self.drag[0]
            if kind == 'pan':
                self.offset = self.drag[2]+pos-self.drag[1]
            elif kind == 'marquee':
                self.drag = ('marquee', self.drag[1], world, self.drag[3])
            elif kind == 'papers':
                for paper, (x, y) in self.drag[1]:
                    paper.x, paper.y = x+world.x()-self.drag[2].x(), y+world.y()-self.drag[2].y()
            elif kind == 'rotate':
                state = self.drag[1]
                angle = self.drag_rotation_angle(state, pos, modifiers)
                if angle is None:
                    return
                self.rotate_key_dragged = True
                self.set_view_rotation(angle, state['pivot'])
            elif kind == 'object':
                _, obj, start, before = self.drag
                obj.x = before['x']+world.x()-start.x()
                obj.y = before['y']+world.y()-start.y()
            elif kind == 'resize':
                _, paper, start, before = self.drag
                origin = QPointF(paper.x, paper.y)
                a, b = world-origin, start-origin
                paper.scale = max(.08, min(8., before['scale']*math.hypot(a.x(), a.y())/max(1, math.hypot(b.x(), b.y()))))
            elif kind == 'perspective':
                _, paper, start, before, motion = self.drag
                settings = PerspectiveSettings.read(before).data()
                if motion['mode'] == 'roll':
                    angle = self.drag_rotation_angle(motion, pos, modifiers)
                    if angle is None:
                        return
                    settings['roll'] = (angle+180)%360-180
                elif motion['mode'] == 'move':
                    delta = paper.local(self.to_world(pos))-paper.local(self.to_world(start))
                    settings['offset_x'] += delta.x()/paper.width
                    settings['offset_y'] += delta.y()/paper.height
                else:
                    delta = pos-start
                    settings['yaw'] = (settings['yaw']+delta.x()*.35+180)%360-180
                    settings['pitch'] = (settings['pitch']-delta.y()*.35+180)%360-180
                paper.perspective = PerspectiveSettings.read(settings).data()
            self.update()
        elif self.stroke and self.stroke_source == source:
            self.sample_stroke(world, pressure, tx, ty)
        else:
            self.update()

    def pointer_up(self, pen=None):
        source = None if pen is None else 'pen' if pen else 'mouse'
        self.inpaint.finish(source)
        if source is None or self.stroke_source == source:
            self.finish_stroke()
        if source is not None and self.drag_source != source:
            return
        drag = self.drag
        self.drag = self.drag_source = self.drag_button = None
        self.refresh_cursor()
        if drag and drag[0] == 'marquee':
            rect = QRectF(drag[1], drag[2]).normalized()
            selected = list(drag[3])
            if rect.width()*self.zoom > 3 or rect.height()*self.zoom > 3:
                for paper in self.visible_papers():
                    if paper not in selected and rect.intersects(paper.transform().mapRect(QRectF(0, 0, paper.width, paper.height))):
                        selected.append(paper)
            self.set_paper_selection(selected)
        if drag and drag[0] == 'papers':
            before = drag[1]
            after = [(p, (p.x, p.y)) for p, _ in before]
            def place(items):
                for paper, (x, y) in items:
                    paper.x, paper.y = x, y
            if before != after:
                self.history.push(Change(self, 'Переместить листы', lambda: place(before), lambda: place(after)))
            self.selection_changed.emit()
        if drag and drag[0] == 'perspective':
            _, paper, _, before, motion = drag
            title = 'Крен камеры' if motion['mode'] == 'roll' else 'Объёмная сетка'
            self.push_move(paper, {'perspective': before}, {'perspective': paper.perspective}, title)
            self.selection_changed.emit()
            self.update()
        if drag and drag[0] in ('object', 'resize'):
            _, obj, _, before = drag
            self.push_move(obj, before, {key: getattr(obj, key) for key in before})
            self.selection_changed.emit()

    def tabletEvent(self, event):
        event.accept()
        if self.native_pen:
            return
        self.pen_sample(event.type(), event.position(), event.pressure(), event.xTilt(), event.yTilt(),
                        event.pointerType() == QPointingDevice.PointerType.Eraser,
                        event.pointingDevice().name(), event.modifiers())

    def pen_sample(self, kind, pos, pressure, tx, ty, eraser=False, device='Wacom напрямую', modifiers=Qt.KeyboardModifier.NoModifier):
        self.pen_seen = time.monotonic()
        self.pen_events += 1
        if kind == QEvent.Type.TabletPress:
            self.pointer_down(pos, Qt.MouseButton.LeftButton, modifiers, True, pressure, tx, ty, eraser)
        elif kind == QEvent.Type.TabletMove:
            self.pointer_move(pos, True, pressure, tx, ty, modifiers)
        elif kind == QEvent.Type.TabletRelease:
            if self.stroke and self.stroke_source == 'pen' and self.last_sample:
                self.sample_stroke(self.to_world(pos), self.last_sample[1], tx, ty, True)
            self.pointer_up(pen=True)
        if self.pen_seen-self.telemetry_at > .045:
            self.telemetry_at = self.pen_seen
            self.telemetry.emit({'pressure': pressure, 'tx': tx, 'ty': ty, 'events': self.pen_events, 'device': device, 'eraser': eraser})

    def ignore_mouse(self, event):
        # A hovering pen must not suppress releases from a physical mouse.
        # WinTab-promoted native mouse messages are filtered by PenMouseFilter.
        return event.source() != Qt.MouseEventSource.MouseEventNotSynthesized

    def mousePressEvent(self, event):
        if self.ignore_mouse(event):
            return
        self.pointer_down(event.position(), event.button(), event.modifiers())

    def mouseMoveEvent(self, event):
        if self.ignore_mouse(event) or self.dual and not self.drag:
            return
        self.pointer_move(event.position(), modifiers=event.modifiers())

    def mouseReleaseEvent(self, event):
        if self.ignore_mouse(event) or self.dual and not self.drag:
            return
        if self.drag_source == 'mouse' and event.button() != self.drag_button:
            return
        self.pointer_up(pen=False)

    def wheelEvent(self, event):
        if self.stroke or self.drag or self.inpaint.owner:
            return
        if self.tool == 'perspective' and isinstance(self.selected, Paper):
            paper = self.selected
            settings = PerspectiveSettings.read(paper.perspective)
            if settings.enabled and not settings.locked:
                data = settings.data()
                data['scale'] *= math.exp(event.angleDelta().y()*.0015)
                self.push_move(paper, {'perspective': paper.perspective},
                               {'perspective': PerspectiveSettings.read(data).data()}, 'Объёмная сетка')
            event.accept()
            return
        anchor = self.to_world(event.position())
        self.zoom = max(.06, min(6., self.zoom*math.exp(event.angleDelta().y()*.0015)))
        self.offset += event.position()-self.to_screen(anchor)
        self.zoom_changed.emit(self.zoom)
        self.update()
        event.accept()

    def keyPressEvent(self, event):
        direction = {Qt.Key.Key_BracketLeft: -1, Qt.Key.Key_BracketRight: 1,
                     ord('Х'): -1, ord('Ъ'): 1}.get(event.key())
        if direction is None:
            direction = {'[': -1, ']': 1, 'х': -1, 'ъ': 1}.get(event.text().lower())
        if (direction and self.tool in ('pencil', 'eraser', 'mask_add', 'mask_remove')
                and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier |
                                            Qt.KeyboardModifier.MetaModifier |
                                            Qt.KeyboardModifier.ShiftModifier)):
            # Canvas-only key handling leaves text fields alone. Alt remains
            # available while drawing guides; held keys use normal repeat.
            self.brush_size_step.emit(direction)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Alt and not event.isAutoRepeat():
            self.set_guide_drawing(True)
            event.accept()
        elif event.key() == Qt.Key.Key_R and not event.isAutoRepeat() and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)):
            if self.tool == 'perspective':
                self.pointer_up()
                self.perspective_roll_key = True
                self.update()
            else:
                self.rotate_key_tool = self.tool if self.tool != 'rotate' else None
                self.rotate_key_started = time.monotonic()
                self.rotate_key_dragged = False
                self.set_tool('rotate')
            event.accept()
        elif event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.space = True
            self.dual_paused = True
            self.flush_dual_move()
            self.refresh_cursor()
            self.update()
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Alt and not event.isAutoRepeat():
            self.set_guide_drawing(False)
            event.accept()
        elif event.key() == Qt.Key.Key_R and not event.isAutoRepeat():
            if self.perspective_roll_key:
                self.pointer_up()
                self.perspective_roll_key = False
                self.update()
            previous = self.rotate_key_tool
            self.rotate_key_tool = None
            if previous and self.tool == 'rotate' and (self.rotate_key_dragged or time.monotonic()-self.rotate_key_started > .25):
                self.set_tool(previous)
            event.accept()
        elif event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.space = False
            self.dual_paused = False
            self.refresh_cursor()
            self.update()
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self.pointer_up()
        self.guide_drawing = False
        self.perspective_roll_key = False
        previous = self.rotate_key_tool
        self.rotate_key_tool = None
        if previous and self.tool == 'rotate':
            self.set_tool(previous)
        self.space = False
        self.dual_paused = False
        self.refresh_cursor()
        super().focusOutEvent(event)

    def enterEvent(self, event):
        if self.cursor_world is None and not self.dual:
            self.cursor_world = self.to_world(event.position())
            self.cursor_tilt = (0., 0.)
            self.cursor_pressure = .5
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self.stroke:
            self.cursor_world = None
            self.update()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()

    def dropEvent(self, event):
        from PySide6.QtGui import QImage
        from pathlib import Path
        for i, url in enumerate(event.mimeData().urls()):
            path = url.toLocalFile()
            if path:
                self.add_reference(QImage(path), Path(path).stem, self.to_world(event.position())+QPointF(i*35, i*35))
        if event.mimeData().hasImage() and not event.mimeData().hasUrls():
            self.add_reference(QImage(event.mimeData().imageData()), 'Референс', self.to_world(event.position()))
        event.acceptProposedAction()

    def paintEvent(self, event):
        self.perspective_renderer.preview_id = self.drag[1].id if self.drag and self.drag[0] == 'perspective' else None
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor('#252b2a'))
        p.save()
        p.setTransform(self.view_transform())
        grid = 60*self.zoom
        if grid > 18:
            bounds = self.view_transform().inverted()[0].mapRect(QRectF(self.rect()))
            p.setPen(QPen(QColor('#3b4140'), 1/self.zoom))
            x = math.floor(bounds.left()/60)*60
            while x < bounds.right():
                y = math.floor(bounds.top()/60)*60
                while y < bounds.bottom():
                    p.drawPoint(QPointF(x, y))
                    y += 60
                x += 60
        # Aligned tracing/assistant sheets share a label anchor: show the top one.
        papers = self.visible_papers()
        label_tops = {(paper.x, paper.y, paper.angle, paper.scale): paper for paper in papers}
        active = self.stroke_paper or (self.selected if isinstance(self.selected, Paper) else None)
        p.save()
        focused = self.focused_paper()
        if focused:
            clip = QPainterPath()
            clip.addRect(QRectF(0, 0, focused.width, focused.height))
            p.setClipPath(focused.transform().map(clip))
        if self.use_viewport_cache:
            split = next((i for i, sheet in enumerate(papers) if sheet is active), len(papers))
            self.viewport_cache.draw(p, self, papers[:split], 'below', label_tops)
            if split < len(papers):
                self.draw_sheet(p, active, label_tops)
            self.viewport_cache.draw(p, self, papers[split+1:], 'above', label_tops)
        else:
            for paper in papers:
                self.draw_sheet(p, paper, label_tops)
        p.restore()
        self.inpaint.draw(p)
        if self.drag and self.drag[0] == 'marquee':
            p.setPen(QPen(QColor('#c4aa79'), 1/self.zoom, Qt.PenStyle.DashLine))
            p.setBrush(QColor(196, 170, 121, 25))
            p.drawRect(QRectF(self.drag[1], self.drag[2]).normalized())
        if self.guide_visible:
            for band in self.doc.bands:
                if not band.visible:
                    continue
                color = QColor(band.color)
                for a, end in self.doc.lines(band):
                    if self.extensions:
                        d = end-a
                        length = math.hypot(d.x(), d.y())
                        if length > 1:
                            d = d/length*15000
                            pale = QColor(color)
                            pale.setAlpha(65)
                            p.setPen(QPen(pale, .8/self.zoom, Qt.PenStyle.DashLine))
                            p.drawLine(a-d, a+d)
                    shadow = QPen(QColor(0, 0, 0, 45), 3/self.zoom)
                    p.setPen(shadow)
                    p.drawLine(a+QPointF(0, 1.5/self.zoom), end+QPointF(0, 1.5/self.zoom))
                    p.setPen(QPen(color, (1.6 if band is self.band else 1.)/self.zoom))
                    p.drawLine(a, end)
            for pin in self.doc.pins:
                center = QPointF(pin.x, pin.y)
                p.setPen(QPen(QColor('#202522'), 1.5/self.zoom))
                p.setBrush(QColor('#c5a66b'))
                p.drawEllipse(center, 6.5/self.zoom, 6.5/self.zoom)
                p.setPen(QPen(QColor('#f7dfae'), 1.2/self.zoom))
                p.drawLine(center+QPointF(-2/self.zoom, -2/self.zoom), center+QPointF(1/self.zoom, -2/self.zoom))
                if self.selected is pin or self.pending_pin is pin:
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(QColor('#e0cda7'), 1/self.zoom))
                    p.drawEllipse(center, 11/self.zoom, 11/self.zoom)
                if self.dual and self.mouse_target() is pin:
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(QColor('#82d2ca'), 1.5/self.zoom, Qt.PenStyle.DashLine))
                    p.drawEllipse(center, 16/self.zoom, 16/self.zoom)
        if self.cursor_world is not None and self.uses_brush_cursor():
            if self.tool in MASK_TOOLS:
                paper = self.selected if isinstance(self.selected, Paper) else None
                radius = self.inpaint.diameter/2*(paper.scale if paper else 1.)
                p.setPen(QPen(QColor('#f4b093'), 1.2/self.zoom))
                p.setBrush(Qt.BrushStyle.NoBrush)
                if self.tool == 'mask_rect':
                    d = 8/self.zoom
                    p.drawLine(self.cursor_world-QPointF(d, 0), self.cursor_world+QPointF(d, 0))
                    p.drawLine(self.cursor_world-QPointF(0, d), self.cursor_world+QPointF(0, d))
                else:
                    p.drawEllipse(self.cursor_world, radius, radius)
            else:
                self.draw_brush_cursor(p)
        p.restore()
        # Quiet viewport HUD; all text is screen-sized.
        self.draw_hud(p)
        p.end()

    def hideEvent(self, event):
        # Closed/hidden desks retain their artwork, not redundant viewport pixels.
        self.viewport_cache.clear()
        self.perspective_renderer.clear()
        super().hideEvent(event)

    def draw_sheet(self, p, paper, label_tops):
        screen = (paper.transform()*self.view_transform()).mapRect(QRectF(0, 0, paper.width, paper.height))
        if not screen.adjusted(-40, -40, 40, 40).intersects(QRectF(self.rect())):
            return
        p.save()
        p.setTransform(paper.transform(), True)
        rect = QRectF(0, 0, paper.width, paper.height)
        if paper.paper_opacity >= .99 and paper.kind == 'paper':
            p.fillRect(rect.translated(7/self.zoom, 10/self.zoom), QColor(0, 0, 0, 42))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom*paper.scale < .95)
        draw_paper_content(p, paper, self.perspective_renderer.draw)
        for layer in paper.guide_layers:
            if layer.visible and layer.opacity > 0:
                p.setOpacity(layer.opacity)
                p.drawImage(0, 0, layer.image)
        p.setOpacity(1.)
        scale = self.zoom*paper.scale
        if paper in self.selected_papers():
            p.setPen(QPen(QColor('#c4aa79'), 1.2/scale))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect)
            if self.tool == 'select' and len(self.selected_papers()) == 1:
                p.fillRect(QRectF(paper.width-5/scale, paper.height-5/scale, 10/scale, 10/scale), QColor('#c4aa79'))
        p.setPen(QColor('#b3bbb5'))
        font = QFont('Segoe UI', 9)
        font.setPixelSize(max(1, round(12/scale)))
        p.setFont(font)
        if label_tops[(paper.x, paper.y, paper.angle, paper.scale)] is paper:
            p.drawText(QPointF(0, -12/scale), f'{paper.name}  ·  {"РЕФЕРЕНС" if paper.kind == "reference" else "КАЛЬКА" if paper.tracing else str(paper.width)+" × "+str(paper.height)}' + ('  ·  ЗАКРЕПЛЁН' if paper.locked else ''))
        p.restore()

    def draw_brush_cursor(self, p):
        s = self.stroke_settings()
        contact = pencil_contact(s, self.cursor_pressure, *self.cursor_tilt)
        paper = self.paper_at(self.cursor_world, True)
        scale = paper.scale if paper else 1.
        p.save()
        p.translate(self.cursor_world)
        p.rotate(math.degrees(contact.angle)-self.view_rotation)
        p.scale(scale, scale)
        dark = paper and QColor(PAPER_STYLES[paper.paper_style][1]).lightness() < 140
        cursor_color = QColor(225, 228, 220, 190) if dark else QColor(110, 113, 106, 160)
        p.setPen(QPen(cursor_color, .8/(self.zoom*scale)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in contact.outline()]))
        if not s.eraser or s.eraser_shape == 'pencil':
            # The small mark identifies the exact nib position, not the
            # centre of the much larger side-shading contact.
            p.drawPoint(QPointF())
        p.restore()

    def draw_hud(self, p):
        p.setFont(QFont('Segoe UI', 9))
        p.setPen(QColor('#9da8a0'))
        hint = 'Пробел + движение — доска    ·    Колесо — масштаб    ·    F — всё в кадре'
        if self.guide_drawing:
            layer = self.active_guide()
            hint = 'ALT · НАПРАВЛЯЮЩИЕ' + (' · '+layer.name if layer else ' · новый слой') + ' · Отпустите Alt — основной рисунок'
        if self.dual:
            target_name = 'пин' if self.mouse_target_mode == 'pin' else 'резинка'
            hint = 'ПАУЗА МЫШИ' if self.dual_paused else f'ДВЕ РУКИ   /   Мышь — {target_name}    ·    Перо — карандаш    ·    M — выйти'
        if self.tool == 'rotate':
            hint = f'ПОВОРОТ ВИДА {self.view_rotation:+.0f}°   ·   Потяните пером или мышью   ·   Shift — шаг 15°   ·   B — карандаш'
            pivot = QPointF(self.width()/2, self.height()/2)
            p.setPen(QPen(QColor('#c4aa79'), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(pivot, 24, 24)
            direction = QTransform().rotate(self.view_rotation).map(QPointF(0, -35))
            p.drawLine(pivot, pivot+direction)
            p.setPen(QColor('#9da8a0'))
        if self.tool in MASK_TOOLS:
            hint = 'ВЫДЕЛЕНИЕ   ·   Alt — убрать область   ·   Ctrl+Z — отменить выделение   ·   Esc — к карандашу'
        if self.tool == 'perspective':
            hint = 'ОБЪЁМ   ·   Тяните — вращение   ·   R — крен   ·   Shift — сдвиг   ·   Колесо — размер   ·   B — рисовать'
            if self.perspective_roll_key:
                hint = 'КРЕН КАМЕРЫ   ·   Тяните по кругу   ·   Shift — шаг 15°   ·   Отпустите R — вращать объём'
                pivot = QPointF(self.width()/2, self.height()/2)
                p.setPen(QPen(QColor('#d8bf8d'), 1))
                p.drawEllipse(pivot, 18, 18)
                p.drawLine(pivot+QPointF(-28, 0), pivot+QPointF(28, 0))
                p.setPen(QColor('#9da8a0'))
        p.drawText(QRectF(20, self.height()-35, self.width()-40, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, hint)
        if self.tool == 'band':
            p.drawText(QPointF(20, 28), 'Выберите первый пин, затем соединяйте его с другими. Esc — закончить. P — новый пин.')
