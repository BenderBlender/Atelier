"""Paper-space selection and deterministic masked compositing."""
import math
import numpy as np
from scipy import ndimage
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QUndoCommand, QUndoStack
from .brush import empty_ink, image_array
from .model import Paper, png_bytes

MASK_TOOLS = ('mask_add', 'mask_remove', 'mask_rect')
MASK_COLOR = QColor(234, 112, 76)
DEFAULT_FEATHER = 40
MAX_FEATHER = 200
MAX_INPUT_SIDE = 2048
MAX_DETAIL_SCALE = 4


class MaskChange(QUndoCommand):
    def __init__(self, selection, before, after):
        super().__init__('Выделение для перерисовки')
        self.selection, self.before, self.after = selection, before, after
        self.first = True

    def apply(self, data):
        self.selection.image = QImage.fromData(data, 'PNG').convertToFormat(QImage.Format.Format_RGBA8888)
        self.selection.notify()

    def undo(self):
        self.apply(self.before)

    def redo(self):
        if self.first:
            self.first = False
            self.selection.notify()
        else:
            self.apply(self.after)


class SelectionMask:
    def __init__(self, board):
        self.board = board
        self.document = None
        self.paper = None
        self.image = QImage()
        self.diameter = 100
        self.feather = DEFAULT_FEATHER
        self.harmonize = True
        self.detail = False
        self.visible = False
        self.owner = None
        self.last = self.start = None
        self.before = None
        self.subtract = False
        self.rectangle = False
        self.history = QUndoStack(board)
        self.history.setUndoLimit(24)

    def matches(self, paper):
        return (self.document is self.board.doc and self.paper is paper
                and paper is not None and paper in self.board.doc.papers and not self.image.isNull())

    def ensure(self, paper):
        if not isinstance(paper, Paper) or paper not in self.board.doc.papers:
            return False
        if not self.matches(paper):
            self.finish()
            self.history.clear()
            self.document, self.paper = self.board.doc, paper
            self.image = empty_ink(paper.width, paper.height)
            self.notify()
        return True

    def notify(self):
        self.board.mask_changed.emit()
        self.board.update()

    def alpha(self):
        return image_array(self.image)[..., 3]

    def has_selection(self, paper):
        return self.matches(paper) and bool(np.any(self.alpha()))

    def bounds(self):
        if self.image.isNull():
            return None
        ys, xs = np.nonzero(self.alpha())
        return (int(xs.min()), int(ys.min()), int(xs.max()+1), int(ys.max()+1)) if len(xs) else None

    def begin(self, world, owner, subtract=False, rectangle=False):
        if self.owner and self.owner != owner:
            if owner != 'pen':
                return
            self.finish()
        paper = self.board.selected
        if not self.ensure(paper) or not paper.contains(world):
            return
        self.owner = owner
        self.before = png_bytes(self.image)
        self.subtract, self.rectangle = subtract, rectangle
        self.start = self.last = paper.local(world)
        if not rectangle:
            self.paint_line(self.last, self.last)
        self.board.update()

    def paint_line(self, start, end):
        painter = QPainter(self.image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.subtract:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        pen = QPen(MASK_COLOR, self.diameter, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if start == end:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(MASK_COLOR)
            painter.drawEllipse(end, self.diameter/2, self.diameter/2)
        else:
            painter.drawLine(start, end)
        painter.end()

    def move(self, world, owner):
        if self.owner != owner:
            return False
        point = self.paper.local(world)
        if not self.rectangle:
            self.paint_line(self.last, point)
        self.last = point
        self.board.update()
        return True

    def finish(self, owner=None):
        if not self.owner or owner is not None and self.owner != owner:
            return
        if self.rectangle:
            painter = QPainter(self.image)
            if self.subtract:
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(QRectF(self.start, self.last).normalized(), MASK_COLOR)
            painter.end()
        after = png_bytes(self.image)
        before = self.before
        self.owner = self.before = None
        self.rectangle = False
        if after != before:
            self.history.push(MaskChange(self, before, after))
        else:
            self.notify()

    def clear(self):
        self.finish()
        if self.matches(self.board.selected) and self.has_selection(self.paper):
            before = png_bytes(self.image)
            self.image.fill(0)
            self.history.push(MaskChange(self, before, png_bytes(self.image)))

    def draw(self, painter):
        if not self.visible or not self.matches(self.board.selected):
            return
        painter.save()
        painter.setTransform(self.paper.transform(), True)
        painter.setOpacity(.38)
        painter.drawImage(0, 0, self.image)
        painter.setOpacity(1.)
        if self.owner and self.rectangle:
            painter.setPen(QPen(MASK_COLOR, 1.2/(self.board.zoom*self.paper.scale), Qt.PenStyle.DashLine))
            painter.setBrush(QColor(234, 112, 76, 50))
            painter.drawRect(QRectF(self.start, self.last).normalized())
        painter.restore()


def api_mask(selection):
    """OpenAI mask: transparent edits, opaque protects (inverse of selection)."""
    image = empty_ink(selection.width(), selection.height())
    pixels = image_array(image)
    pixels[..., :3] = 255
    pixels[..., 3] = np.where(image_array(selection)[..., 3] > 0, 0, 255)
    return image


def region_guide(source, selection):
    image = source.convertToFormat(QImage.Format.Format_RGBA8888)
    painter = QPainter(image)
    painter.setOpacity(.48)
    painter.drawImage(0, 0, selection)
    painter.end()
    return image


def selection_bounds(selection):
    if selection.isNull():
        return None
    ys, xs = np.nonzero(image_array(selection)[..., 3])
    return (int(xs.min()), int(ys.min()), int(xs.max()+1), int(ys.max()+1)) if len(xs) else None


def validate_crop(crop, size):
    """Crop coordinates always refer to original paper pixels, never the viewport."""
    if crop is None:
        return None
    if not isinstance(crop, (list, tuple)) or len(crop) != 4 or any(type(v) is not int for v in crop):
        raise ValueError('Некорректные координаты увеличенного фрагмента.')
    x, y, width, height = crop
    if x < 0 or y < 0 or width < 1 or height < 1 or x+width > size.width() or y+height > size.height():
        raise ValueError('Увеличенный фрагмент выходит за границы исходного листа.')
    return tuple(crop)


def detail_crop(selection, feather=DEFAULT_FEATHER):
    bounds = selection_bounds(selection)
    if bounds is None:
        raise ValueError('Выделите область для перерисовки.')
    left, top, right, bottom = bounds
    # Retain nearby structure and light, plus room for feathering. One crop
    # encloses all selected islands; requests are never multiplied per island.
    padding = max(64, math.ceil(max(right-left, bottom-top)*.35), min(MAX_FEATHER, max(0, int(feather)))*2)
    width, height = right-left+2*padding, bottom-top+2*padding
    width, height = max(width, math.ceil(height/1.5)), max(height, math.ceil(width/1.5))
    width, height = min(width, selection.width()), min(height, selection.height())
    x = max(0, min(selection.width()-width, (left+right-width)//2))
    y = max(0, min(selection.height()-height, (top+bottom-height)//2))
    return x, y, width, height


def prepare_inpaint_inputs(original, selection, detail=False, feather=DEFAULT_FEATHER):
    if original.isNull() or original.size() != selection.size() or selection_bounds(selection) is None:
        raise ValueError('Выделение не совпадает с исходным листом или пусто.')
    crop = detail_crop(selection, feather) if detail else None
    image = original.copy(*crop) if crop else original.copy()
    mask = selection.copy(*crop) if crop else selection.copy()
    side = max(image.width(), image.height())
    target = min(MAX_INPUT_SIDE, side*MAX_DETAIL_SCALE) if detail else min(MAX_INPUT_SIDE, side)
    if target != side:
        image = image.scaled(target, target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        mask = mask.scaled(image.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return image, mask, crop


def frame_prompt(selection, crop=None):
    left, top, right, bottom = selection_bounds(selection)
    x, y, width, height = crop or (0, 0, selection.width(), selection.height())
    bounds = [round((v-origin)/size*100, 2) for v, origin, size in
              ((left, x, width), (top, y, height), (right, x, width), (bottom, y, height))]
    brief = '\nEditable bounding box, in percent of the attached image [left, top, right, bottom]: '+str(bounds)
    if crop:
        brief += ('\nThis is an enlarged detail crop of a larger drawing. Return this exact crop framing and aspect ratio. '
                  'Do not zoom out, recenter, or reconstruct the full scene. Use the surrounding unselected context '
                  'to maintain line connections, object proportions and light. Draw fine details at the enlarged scale '
                  'while matching the existing medium. Preserve branch continuity and plausible anatomy/contact where relevant; '
                  'do not invent detached fragments or extra limbs.')
    return brief


def smoothstep(value):
    value = np.clip(value, 0, 1)
    return value*value*(3-2*value)


def selection_geometry(selection, radius):
    alpha = image_array(selection)[..., 3].astype(np.float32)/255
    inside = alpha > 0
    radius = max(0, min(MAX_FEATHER, int(radius)))
    if not radius or not np.any(inside) or np.all(inside):
        return alpha, None, None
    # Distance to the actual contour gives a ramp starting near zero, unlike
    # blurring a binary mask then clipping (which leaves ~50% at the edge).
    distance = ndimage.distance_transform_edt(inside).astype(np.float32)
    labels, count = ndimage.label(inside, structure=np.ones((3, 3)))
    maxima = ndimage.maximum(distance, labels, np.arange(1, count+1))
    widths = np.minimum(radius, np.maximum(.5, (maxima-.5)*.7)).astype(np.float32)
    width = np.concatenate(([1.], widths)).astype(np.float32)[labels]
    fade = smoothstep(np.maximum(distance-.5, 0)/width)
    return alpha*fade, distance, width


def feather_alpha(selection, radius):
    return selection_geometry(selection, radius)[0]


def match_edge_tone(original, generated, distance, width):
    """Estimate gentle background drift near the contour, not new object colors.

    Work on a small grid and adjust low frequencies only in the transition band.
    Strong content differences and sharp edges are excluded from the estimate.
    """
    inside = distance > 0
    ys, xs = np.nonzero(inside)
    padding = max(16, int(width[inside].max()*3))
    y0, y1 = max(0, ys.min()-padding), min(inside.shape[0], ys.max()+padding+1)
    x0, x1 = max(0, xs.min()-padding), min(inside.shape[1], xs.max()+padding+1)
    roi = np.s_[y0:y1, x0:x1]
    step = max(1, int(np.ceil(max(y1-y0, x1-x0)/384)))
    old = original[roi][::step, ::step, :3].astype(np.float32)
    new = generated[roi][::step, ::step, :3].astype(np.float32)
    d = distance[roi][::step, ::step]
    w = width[roi][::step, ::step]
    old_low = ndimage.gaussian_filter(old, (1.2, 1.2, 0))
    new_low = ndimage.gaussian_filter(new, (1.2, 1.2, 0))
    delta = old_low-new_low
    difference = np.max(np.abs(delta), axis=2)
    # Include a narrow inside collar: generated backgrounds can change only
    # within the selected region, leaving no useful outside mismatch to sample.
    ring = (d > 0) & (d < np.maximum(6, w*.5))
    confidence = ring.astype(np.float32)*(1-smoothstep((difference-12)/24))
    for low in (old_low, new_low):
        gray = low.mean(axis=2)
        dx = np.abs(np.diff(gray, axis=1, append=gray[:, -1:]))
        dy = np.abs(np.diff(gray, axis=0, append=gray[-1:, :]))
        confidence *= 1-smoothstep((np.maximum(dx, dy)-2)/6)
    if confidence.sum() < 8:
        return None
    sigma = max(3., min(32., padding/(step*3)))
    support = ndimage.gaussian_filter(confidence, sigma)
    correction = ndimage.gaussian_filter(delta*confidence[..., None], (sigma, sigma, 0))
    correction /= np.maximum(support[..., None], 1e-5)
    correction = np.clip(correction, -24, 24)
    correction *= smoothstep(support/.03)[..., None]
    # Smoothly turn correction off before the unchanged-color interior.
    correction *= (1-smoothstep(d/np.maximum(w*2, 1)))[..., None]
    shape = (y1-y0, x1-x0)
    correction = ndimage.zoom(correction, (shape[0]/correction.shape[0], shape[1]/correction.shape[1], 1), order=1)
    return roi, correction


def merge_inpaint(original, generated, selection, feather=DEFAULT_FEATHER, harmonize=True, crop=None):
    if original.size() != selection.size() or original.isNull() or generated.isNull():
        raise ValueError('Размер выделения не совпадает с исходным листом.')
    crop = validate_crop(crop, original.size())
    if crop:
        x, y, width, height = crop
        bounds = selection_bounds(selection)
        if bounds is None or bounds[0] < x or bounds[1] < y or bounds[2] > x+width or bounds[3] > y+height:
            raise ValueError('Выделение выходит за границы сохранённого фрагмента.')
        patch = merge_inpaint(original.copy(*crop), generated, selection.copy(*crop), feather, harmonize)
        output = original.convertToFormat(QImage.Format.Format_RGBA8888).copy()
        image_array(output)[y:y+height, x:x+width] = image_array(patch)
        return output
    ratio = (generated.width()/generated.height())/(original.width()/original.height())
    if abs(ratio-1) > .025:
        raise ValueError('Модель изменила пропорции кадра. Результат сохранён отдельно, чтобы не сместить выделенный фрагмент.')
    scaled = generated.scaled(original.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    scaled = scaled.convertToFormat(QImage.Format.Format_RGBA8888)
    output = original.convertToFormat(QImage.Format.Format_RGBA8888)
    # Explicit copy ensures the caller's source is never detached or mutated through NumPy.
    output = output.copy()
    pixels, new = image_array(output), image_array(scaled)
    alpha, distance, width = selection_geometry(selection, feather)
    if harmonize and distance is not None:
        tone = match_edge_tone(pixels, new, distance, width)
        if tone is not None:
            roi, correction = tone
            new[roi][..., :3] = np.clip(np.round(new[roi][..., :3].astype(np.float32)+correction), 0, 255).astype(np.uint8)
    active = alpha > 0
    # Touch only selected pixels; even invisible RGB bytes outside remain exact.
    weight = (alpha[active]*(new[..., 3][active].astype(np.float32)/255))[:, None]
    pixels[active, :3] = np.round(pixels[active, :3]*(1-weight)+new[active, :3]*weight).astype(np.uint8)
    pixels[active, 3] = np.round(pixels[active, 3]+(255-pixels[active, 3])*weight[:, 0]).astype(np.uint8)
    return output
