"""Typed GUI-thread tools for Atelier. No arbitrary evaluation or filesystem access."""
from copy import deepcopy
from dataclasses import asdict
import math
import uuid
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor
from .board import Change
from .model import Paper, Pin, PAPER_STYLES
from .brush import PRESETS
from .perspective import PerspectiveSettings, PROJECTIONS, STRUCTURES, PRESETS as GRID_PRESETS, preset as grid_preset
from .navigation import normalized_navigation, locate, move_node
from .sheet_clipboard import insert_sheets, copy_sheets, paste_sheets

TOOLS = {
    'desk.new': 'Create desk. {name?}. New desk becomes current.',
    'desk.select': 'Switch existing desk. {desk_id}',
    'desk.rename': 'Rename current desk. {name}',
    'desk.reference': 'Open current desk as always-on-top floating reference window. {}',
    'desk.close': 'Close current desk with normal save dialog. {}',
    'sheet.add': 'New paper. {width?:1400,height?:1000,name?}. Dimensions integer 1..8192, at most 32M pixels.',
    'sheet.select': 'Select one or several sheets. {ids:[paper_id,...]}.',
    'sheet.update': 'Set selected sheet properties. {paper_id?, values:{name?,x?,y?,angle?,scale?,locked?,paper_opacity?,ink_opacity?,paper_style?}}. Opacity 0..1; scale .08..8; angle degrees.',
    'sheet.arrange': 'Arrange selected sheets in rows without changing sizes. {columns?:3,gap?:60}. Coordinates in desk pixels.',
    'sheet.duplicate': 'Independent copies of selected sheets; optional {target_desk_id}. Preserves strokes, Alt layers, grids.',
    'sheet.copy': 'Copy selected sheets to clipboard. {}',
    'sheet.paste': 'Paste sheets or image from clipboard. {}',
    'sheet.tracing': 'Create tracing paper over selected sheet. {}',
    'sheet.delete': 'Delete selected unlocked sheets. Requires in-app confirmation. {}',
    'sheet.clear': 'Clear main image or current Alt guide layer. {guide?:false}. Requires confirmation.',
    'sheet.order': 'Move selected sheet to front/back. {direction:"front"|"back"}',
    'sheet.import': 'Open native image chooser; user picks local references. {}',
    'folder.create': 'Create folder in current desk. {name,parent_id?,paper_ids?:[]}.',
    'folder.move': 'Move a sheet or folder in navigation. {id,folder_id?:null,index?}. Does not move canvas positions.',
    'folder.rename': 'Rename a folder. {id,name}',
    'folder.ungroup': 'Remove folder but keep its contents. {id}',
    'brush.set': 'Choose preset and/or settings. {preset?,values?:{size,opacity,grain,tilt_gain,pressure_gamma,smoothing,tip_sharpness,sharpening_length,eraser_strength,eraser_size,eraser_shape,color}}. Values use native units; sharpening_length .25..10 (10=1000%). color #rrggbb, shape round|pencil. Valid ranges supplied in state.',
    'brush.reset': 'Reset current brush to factory settings. {}',
    'tool.select': 'Select tool. {tool:pencil|eraser|select|hand|rotate|pin|band|perspective|mask_add|mask_remove|mask_rect}',
    'view.set': 'Change only viewport. {fit?:true,paper_id?,rotation?,zoom?}. Zoom .06..6.',
    'grid.set': 'Edit perspective on selected paper. {preset?,values?:{...}}; all PerspectiveSettings fields supported; enums/defaults in state. Undoable.',
    'guide.add': 'Add and select Alt guide layer on current sheet. {name?,color?,opacity?}.',
    'guide.select': 'Choose Alt layer. {id}',
    'guide.update': 'Update current Alt layer. {name?,color?,opacity?,visible?}',
    'guide.delete': 'Delete selected Alt layer. Confirmation required. {}',
    'pin.add': 'Add pin. {x,y}',
    'pin.move': 'Move pin. {id,x,y}',
    'pin.delete': 'Delete pin and attached bands. {id}. Confirmation required.',
    'pin.connect': 'Join any two existing pins with elastic. {ids:[id1,id2]}',
    'band.delete': 'Delete one elastic, preserve pins. {id}',
    'guides.set': 'Pin guide settings. {visible?,extensions?,snap?,dual?,mouse_pin_id?,mouse_gain?}. dual controls independent mouse pin and pen.',
    'inpaint.set': 'Configure selection tools. {tool?:mask_add|mask_remove|mask_rect,diameter?:4..600,feather?:0..200,harmonize?,detail?}',
    'inpaint.clear': 'Clear selection only, preserve drawing. {}',
    'inpaint.rectangle': 'Select a rectangle in selected sheet pixel coordinates. {x,y,width,height,subtract?:false}. Adds to current mask. Clear first to replace. Undoable.',
    'draw.stroke': 'Draw one simple construction stroke with current pencil/eraser, normally on an Alt layer. {points:[[x,y,pressure?,tilt_x?,tilt_y?],...],guide?:true}. 2..400 points in selected sheet pixels. Do not substitute schematic strokes for requested image generation.',
    'history.undo': 'Undo current desk edit. {}',
    'history.redo': 'Redo current desk edit. {}',
    'file.save': 'Save current desk; native file chooser for new desk. {}',
    'file.open': 'Native project chooser. {}',
    'file.export': 'Native export chooser. {}',
    'assistant.generate': 'Generate/edit selected image with configured image provider. {prompt,mode?:whole|inpaint}. Must be last action; only on explicit user request. Exact user visual prompt, no inferred art direction.',
    'ui.panel': 'Show inspector. {tab:brush|pins|sheets|perspective}.',
}
DESTRUCTIVE = {'sheet.delete', 'sheet.clear', 'guide.delete', 'pin.delete', 'desk.close'}


def snapshot(window):
    board = window.board
    return {'active_desk': board.desk_id, 'selected': [p.id for p in board.selected_papers()], 'tool': board.tool,
        'desks': [{'id': b.desk_id, 'name': b.desk_name, 'modified': getattr(b, '_desk_dirty', False),
            'papers': [{**p.meta(), 'perspective': p.perspective, 'guide_layers': [g.meta() for g in p.guide_layers]} for p in b.doc.papers],
            'folders': normalized_navigation(b.doc.navigation, b.doc.papers),
            'pins': [asdict(p) for p in b.doc.pins], 'bands': [asdict(p) for p in b.doc.bands]} for b in window.boards],
        'brush': asdict(board.brush), 'brush_presets': list(PRESETS),
        'brush_ranges': {k: [s.minimum()/factor, s.maximum()/factor] for k, (s, _, factor, _) in window.controls.items()},
        'paper_styles': PAPER_STYLES, 'grid_defaults': PerspectiveSettings().data(),
        'grid_projections': PROJECTIONS, 'grid_structures': STRUCTURES, 'grid_presets': list(GRID_PRESETS),
        'inpaint': {'has_selection': isinstance(board.selected, Paper) and board.inpaint.has_selection(board.selected),
                    'detail': board.inpaint.detail, 'feather': board.inpaint.feather},
        'tools': TOOLS}


def state_stamp(window):
    return (window.board.desk_id, tuple((b.desk_id, id(b.doc), getattr(b, '_desk_revision', 0)) for b in window.boards),
            tuple(p.id for p in window.board.selected_papers()),
            tuple(bool(b.stroke or b.drag or b.inpaint.owner) for b in window.boards),
            tuple(bool(w.canvas.drag) for w in window.reference_windows.values()))


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'Число вне допустимого диапазона {low}…{high}')
    return value


def name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValueError('Нужно название длиной до 100 символов')
    return value.strip()


def execute(window, tool, args, allow_destructive=False):
    if tool not in TOOLS or not isinstance(args, dict):
        raise ValueError('Неизвестная команда помощника')
    if tool in DESTRUCTIVE and not allow_destructive:
        raise ValueError('Для этой команды нужно подтверждение в чате')
    board = window.board
    board.pointer_up()
    board.flush_dual_move()
    def paper():
        p = next((p for p in board.doc.papers if p.id == args.get('paper_id')), None) if args.get('paper_id') else board.selected
        if not isinstance(p, Paper) or p not in board.doc.papers:
            raise ValueError('Сначала выберите лист')
        if p is not board.selected:
            board.set_selection(p)
        return p
    def editable():
        p = paper()
        if p.locked:
            raise ValueError('Лист закреплён; сначала снимите закрепление')
        return p
    def nav_edit(fn, title):
        doc = board.doc
        before = deepcopy(normalized_navigation(doc.navigation, doc.papers))
        after = deepcopy(before)
        fn(after)
        board.history.push(Change(board, title, lambda: setattr(doc, 'navigation', deepcopy(before)),
                                  lambda: setattr(doc, 'navigation', deepcopy(after))))
    if tool == 'desk.new':
        b = window.new_desk()
        if 'name' in args:
            b.desk_name = name(args['name'])
        window.update_desk_titles()
        return {'desk_id': b.desk_id}
    if tool == 'desk.select':
        target = next((b for b in window.boards if b.desk_id == args.get('desk_id')), None)
        if not target:
            raise ValueError('Стол не найден')
        window.desk_tabs.setCurrentIndex(window.boards.index(target))
    elif tool == 'desk.rename':
        board.desk_name = name(args.get('name'))
        window.update_desk_titles()
    elif tool == 'desk.reference':
        window.open_reference()
    elif tool == 'desk.close':
        window.close_desk(window.boards.index(board))
    elif tool == 'sheet.add':
        width, height = (int(number(args.get(k, d), 1, 8192)) for k, d in [('width', 1400), ('height', 1000)])
        if width*height > 32_000_000 or len(board.doc.papers) >= 100:
            raise ValueError('Превышен размер или количество листов')
        board.add_paper(width, height)
        if 'name' in args:
            board.selected.name = name(args['name'])
            board.changed.emit()
        return {'paper_id': board.selected.id}
    elif tool == 'sheet.select':
        ids = args.get('ids', [])
        papers = [p for p in board.doc.papers if p.id in ids]
        if not papers or len(papers) != len(set(ids)):
            raise ValueError('Листы не найдены')
        board.set_paper_selection(papers)
    elif tool == 'sheet.update':
        p = paper()
        values = dict(args.get('values', {}))
        allowed = {'name', 'x', 'y', 'angle', 'scale', 'locked', 'paper_opacity', 'ink_opacity', 'paper_style'}
        if not values or values.keys()-allowed:
            raise ValueError('Неизвестное свойство листа')
        if p.locked and values != {'locked': False}:
            raise ValueError('Лист закреплён')
        for key, val in values.items():
            if key == 'name': values[key] = name(val)
            elif key == 'paper_style':
                if val not in PAPER_STYLES: raise ValueError('Неизвестная бумага')
            elif key == 'locked':
                if not isinstance(val, bool): raise ValueError('locked должен быть true/false')
            else:
                low, high = (0, 1) if 'opacity' in key else (.08, 8) if key == 'scale' else (-360, 360) if key == 'angle' else (-1e6, 1e6)
                number(val, low, high)
        board.push_move(p, {k: getattr(p, k) for k in values}, values, 'Свойства листа · помощник')
    elif tool == 'sheet.arrange':
        papers = [p for p in board.selected_papers() if not p.locked]
        if not papers: raise ValueError('Нет выбранных незакреплённых листов')
        columns, gap = int(number(args.get('columns', 3), 1, 20)), number(args.get('gap', 60), 0, 2000)
        before = [(p, p.x, p.y) for p in papers]
        bounds = [p.transform().mapRect(QRectF(0, 0, p.width, p.height)) for p in papers]
        x0, y0 = min(r.left() for r in bounds), min(r.top() for r in bounds)
        cellw, cellh = max(r.width() for r in bounds)+gap, max(r.height() for r in bounds)+gap
        after = [(p, p.x+x0+(i%columns)*cellw-r.left(), p.y+y0+(i//columns)*cellh-r.top()) for i, (p, r) in enumerate(zip(papers, bounds))]
        def place(items):
            for p, x, y in items: p.x, p.y = x, y
        board.history.push(Change(board, 'Разложить листы', lambda: place(before), lambda: place(after)))
        board.fit()
    elif tool == 'sheet.duplicate':
        if not board.selected_papers(): raise ValueError('Нет выбранных листов')
        target = next((b for b in window.boards if b.desk_id == args.get('target_desk_id', board.desk_id)), None)
        if not target: raise ValueError('Стол не найден')
        copies = insert_sheets(target, board.selected_papers(), target is board)
        return {'paper_ids': [p.id for p in copies]}
    elif tool == 'sheet.copy':
        if not copy_sheets(board): raise ValueError('Листы не скопированы: проверьте выделение и размер буфера')
    elif tool == 'sheet.paste':
        count = len(board.doc.papers)
        paste_sheets(board)
        if len(board.doc.papers) == count: raise ValueError('В буфере нет подходящих листов или изображения')
    elif tool == 'sheet.tracing':
        paper()
        board.add_tracing()
    elif tool == 'sheet.delete':
        if not any(not p.locked for p in board.selected_papers()): raise ValueError('Нет выбранных незакреплённых листов')
        board.delete_selected()
    elif tool == 'sheet.clear':
        editable()
        if args.get('guide') is True and board.active_guide() is None: raise ValueError('У листа нет Alt-слоя. Основной рисунок сохранён')
        board.clear_paper(guide=args.get('guide') is True)
    elif tool == 'sheet.order':
        p = editable()
        if args.get('direction') not in ('front', 'back'): raise ValueError('Нужно front или back')
        doc = board.doc
        before = list(doc.papers)
        rest = [x for x in before if x is not p]
        after = rest+[p] if args['direction'] == 'front' else [p]+rest
        board.history.push(Change(board, 'Порядок листов', lambda: setattr(doc, 'papers', list(before)), lambda: setattr(doc, 'papers', list(after))))
    elif tool == 'sheet.import': window.import_reference()
    elif tool.startswith('folder.'):
        def edit(nodes):
            if tool == 'folder.create':
                identifier = uuid.uuid4().hex
                nodes.append({'id': identifier, 'type': 'folder', 'name': name(args.get('name')), 'children': []})
                if args.get('parent_id') and not move_node(nodes, identifier, args['parent_id']): raise ValueError('Родительская папка не найдена')
                for p in args.get('paper_ids', []):
                    if not any(x.id == p for x in board.doc.papers) or not move_node(nodes, p, identifier): raise ValueError('Лист не найден')
            elif tool == 'folder.move':
                if not move_node(nodes, args.get('id'), args.get('folder_id'), args.get('index')): raise ValueError('Нельзя переместить в эту папку')
            else:
                found = locate(nodes, args.get('id'))
                if not found or found[2]['type'] != 'folder': raise ValueError('Папка не найдена')
                parent, index, node = found
                if tool == 'folder.rename': node['name'] = name(args.get('name'))
                else: parent[index:index+1] = node['children']
        nav_edit(edit, 'Папки · помощник')
    elif tool == 'brush.set':
        values = args.get('values', {})
        if not isinstance(values, dict): raise ValueError('Некорректные настройки кисти')
        for key, val in values.items():
            if key in window.controls:
                slider, _, factor, _ = window.controls[key]
                number(val, slider.minimum()/factor, slider.maximum()/factor)
            elif key == 'color':
                if not isinstance(val, str) or not QColor(val).isValid(): raise ValueError('Некорректный цвет')
            elif key == 'eraser_shape':
                if val not in ('round', 'pencil'): raise ValueError('Некорректная форма клячки')
            else: raise ValueError('Неизвестное свойство кисти')
        if 'preset' in args:
            if args['preset'] not in PRESETS: raise ValueError('Неизвестная кисть')
            window.presets.setCurrentText(args['preset'])
        for key, val in values.items():
            if key in window.controls:
                slider, _, factor, _ = window.controls[key]
                slider.setValue(round(val*factor))
            elif key == 'color': window.set_color(QColor(val).getRgb()[:3])
            elif key == 'eraser_shape': window.eraser_shape.setCurrentIndex(window.eraser_shape.findData(val))
    elif tool == 'brush.reset': window.reset_brush()
    elif tool == 'tool.select':
        if args.get('tool') not in ('pencil', 'eraser', 'select', 'hand', 'rotate', 'pin', 'band', 'perspective', 'mask_add', 'mask_remove', 'mask_rect'):
            raise ValueError('Неизвестный инструмент')
        window.choose_tool(args['tool'])
    elif tool == 'view.set':
        if args.get('fit'): board.fit()
        if args.get('paper_id'): window.sheet_navigator.open(paper().id)
        if 'rotation' in args: board.set_view_rotation(number(args['rotation'], -360, 360))
        if 'zoom' in args:
            center = QPointF(board.width()/2, board.height()/2)
            world = board.to_world(center)
            board.zoom = number(args['zoom'], .06, 6)
            board.offset += center-board.to_screen(world)
            board.zoom_changed.emit(board.zoom)
            board.update()
    elif tool == 'grid.set':
        p = editable()
        values = args.get('values', {})
        if not isinstance(values, dict) or values.keys()-PerspectiveSettings().data().keys(): raise ValueError('Неизвестная настройка сетки')
        data = dict(p.perspective)
        if 'preset' in args:
            if args['preset'] not in GRID_PRESETS: raise ValueError('Неизвестный пресет сетки')
            data = grid_preset(args['preset'])
        data.update(values)
        board.push_move(p, {'perspective': p.perspective}, {'perspective': PerspectiveSettings.read(data).data()}, 'Сетка · помощник')
    elif tool.startswith('guide.'):
        p = editable()
        if tool == 'guide.add' and board.add_guide_layer() is None: raise ValueError('Слой не создан: достигнут лимит или выбран референс вместо бумаги')
        if tool == 'guide.select':
            layer = next((g for g in p.guide_layers if g.id == args.get('id')), None)
            if not layer: raise ValueError('Слой не найден')
            p.active_guide_id = layer.id
            board.changed.emit()
        elif tool == 'guide.delete': board.delete_guide_layer()
        else:
            layer = board.active_guide()
            if not layer: raise ValueError('Нет слоя направляющих')
            values = {}
            for k in ('name', 'opacity', 'visible'):
                if k in args:
                    if k == 'name': values[k] = name(args[k])
                    elif k == 'opacity': values[k] = number(args[k], 0, 1)
                    elif isinstance(args[k], bool): values[k] = args[k]
                    else: raise ValueError('visible должен быть true/false')
            if 'color' in args:
                if not isinstance(args['color'], str) or not QColor(args['color']).isValid(): raise ValueError('Некорректный цвет')
                board.set_guide_color(args['color'])
            if values: board.push_move(layer, {k: getattr(layer, k) for k in values}, values, 'Слой направляющих · помощник')
    elif tool == 'pin.add':
        if len(board.doc.pins) >= 1000: raise ValueError('Достигнут лимит пинов')
        board.add_pin(QPointF(number(args.get('x'), -1e6, 1e6), number(args.get('y'), -1e6, 1e6)))
    elif tool in ('pin.move', 'pin.delete'):
        pin = board.doc.pin(args.get('id'))
        if not pin: raise ValueError('Пин не найден')
        if tool == 'pin.move': board.push_move(pin, {'x': pin.x, 'y': pin.y}, {k: number(args.get(k), -1e6, 1e6) for k in ('x', 'y')})
        else:
            board.set_selection(pin)
            board.delete_selected()
    elif tool == 'pin.connect':
        ids = args.get('ids')
        if not isinstance(ids, list) or len(ids) != 2 or ids[0] == ids[1] or any(board.doc.pin(i) is None for i in ids): raise ValueError('Нужны два разных пина')
        board.add_band(ids)
    elif tool == 'band.delete':
        band = next((b for b in board.doc.bands if b.id == args.get('id')), None)
        if not band: raise ValueError('Резинка не найдена')
        board.set_selection(band)
        board.delete_selected()
    elif tool == 'guides.set':
        for key, widget in [('visible', window.visible_check), ('extensions', window.extensions_check), ('snap', window.snap_check)]:
            if key in args:
                if not isinstance(args[key], bool): raise ValueError(key+' должен быть true/false')
                widget.setChecked(args[key])
        if 'mouse_gain' in args:
            slider, _, factor, _ = window.controls['mouse_gain']
            slider.setValue(round(number(args['mouse_gain'], slider.minimum()/factor, slider.maximum()/factor)*factor))
        if 'mouse_pin_id' in args:
            pin = board.doc.pin(args['mouse_pin_id'])
            if not pin: raise ValueError('Пин не найден')
            board.set_mouse_pin(pin)
        if isinstance(args.get('dual'), bool) and board.dual != args['dual']: window.toggle_dual()
    elif tool == 'inpaint.set':
        panel = window.assistant
        panel.mode.setCurrentIndex(panel.mode.findData('inpaint'))
        if 'tool' in args:
            if args['tool'] not in ('mask_add', 'mask_remove', 'mask_rect'): raise ValueError('Неизвестная кисть выделения')
            panel.activate_mask(args['tool'])
        for key, control in [('diameter', panel.mask_size), ('feather', panel.feather)]:
            if key in args: control.setValue(round(number(args[key], control.minimum(), control.maximum())))
        for key, control in [('harmonize', panel.harmonize), ('detail', panel.detail)]:
            if key in args:
                if not isinstance(args[key], bool): raise ValueError('Нужно true/false')
                control.setChecked(args[key])
    elif tool == 'inpaint.clear': board.inpaint.clear()
    elif tool == 'inpaint.rectangle':
        p = paper()
        x, y = number(args.get('x'), 0, p.width-1), number(args.get('y'), 0, p.height-1)
        width, height = number(args.get('width'), 1, p.width-x), number(args.get('height'), 1, p.height-y)
        window.assistant.mode.setCurrentIndex(window.assistant.mode.findData('inpaint'))
        selection = board.inpaint
        selection.begin(p.transform().map(QPointF(x, y)), 'agent', subtract=args.get('subtract') is True, rectangle=True)
        selection.move(p.transform().map(QPointF(x+width, y+height)), 'agent')
        selection.finish('agent')
    elif tool == 'draw.stroke':
        p = editable()
        if p.kind != 'paper': raise ValueError('Для рисования по референсу сначала добавьте кальку')
        points = args.get('points', [])
        if not isinstance(points, list) or not 2 <= len(points) <= 400: raise ValueError('Нужны 2–400 точек штриха')
        samples = []
        for point in points:
            if not isinstance(point, list) or not 2 <= len(point) <= 5: raise ValueError('Некорректная точка штриха')
            x, y = number(point[0], 0, p.width-1), number(point[1], 0, p.height-1)
            pressure = number(point[2], .01, 1) if len(point) > 2 else .5
            tx = number(point[3], -85, 85) if len(point) > 3 else 0
            ty = number(point[4], -85, 85) if len(point) > 4 else 0
            samples.append((p.transform().map(QPointF(x, y)), pressure, tx, ty))
        previous_tool, previous_guide, previous_focus = board.tool, board.guide_drawing, board.focused_paper_id
        try:
            board.focused_paper_id = p.id
            board.set_tool('eraser' if previous_tool == 'eraser' else 'pencil')
            board.set_guide_drawing(args.get('guide', True) is True)
            board.start_stroke(*samples[0], source='agent')
            for sample in samples[1:]: board.sample_stroke(*sample)
            board.finish_stroke()
        finally:
            board.set_tool(previous_tool)
            board.set_guide_drawing(previous_guide)
            board.focused_paper_id = previous_focus
    elif tool == 'history.undo':
        if not board.history.canUndo(): raise ValueError('Нет изменений для отмены')
        board.history.undo()
    elif tool == 'history.redo':
        if not board.history.canRedo(): raise ValueError('Нет изменений для повтора')
        board.history.redo()
    elif tool == 'file.save':
        if not window.save_project(): raise ValueError('Сохранение не завершено или отменено')
    elif tool == 'file.open':
        active = window.assistant.agent_active
        window.assistant.agent_active = False
        try: window.open_project()
        finally: window.assistant.agent_active = active
    elif tool == 'file.export': window.export()
    elif tool == 'ui.panel':
        tabs = {'brush': 0, 'pins': 1, 'sheets': 2, 'perspective': 3}
        if args.get('tab') not in tabs: raise ValueError('Неизвестная панель')
        window.sidebar.show()
        window.sidebar_tabs.setCurrentIndex(tabs[args['tab']])
    elif tool == 'assistant.generate':
        prompt = args.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000: raise ValueError('Нужен промпт длиной до 8000 символов')
        if args.get('mode', 'whole') not in ('whole', 'inpaint'): raise ValueError('Неизвестный режим генерации')
        return {'generation': {'prompt': prompt, 'mode': args.get('mode', 'whole')}}
    else: raise ValueError('Команда не реализована')
    window.refresh_selection()
    return {'ok': True, 'desk_id': window.board.desk_id, 'selected': [p.id for p in window.board.selected_papers()]}
