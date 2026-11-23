"""Bookmark-like sheet navigation; folder order is independent of layer order."""
from copy import deepcopy
import json
import uuid
from PySide6.QtCore import Qt, QSize, QPointF, QRectF, QMimeData
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QListWidget, QListWidgetItem,
                              QListView, QAbstractItemView, QMenu, QInputDialog, QApplication)
from .widgets import button, label, tool_icon
from .board import Change

MIME = 'application/x-atelier-sheet-bookmark'


def normalized_navigation(value, papers):
    seen = set()
    count = 0
    def clean(nodes, depth=0):
        nonlocal count
        result = []
        if not isinstance(nodes, list) or depth > 8:
            return result
        for node in nodes[:500]:
            if not isinstance(node, dict) or count >= 500:
                continue
            identifier = node.get('id')
            if not isinstance(identifier, str) or not identifier or len(identifier) > 80 or identifier in seen:
                continue
            if node.get('type') not in ('folder', 'paper'):
                continue
            seen.add(identifier)
            count += 1
            if node.get('type') == 'folder':
                result.append({'id': identifier, 'type': 'folder',
                               'name': str(node.get('name', 'Папка'))[:80],
                               'children': clean(node.get('children'), depth+1)})
            elif node.get('type') == 'paper':
                result.append({'id': identifier, 'type': 'paper'})
        return result
    result = clean(value)
    for paper in papers:
        if paper.id not in seen:
            result.append({'id': paper.id, 'type': 'paper'})
    return result


def locate(nodes, identifier):
    for index, node in enumerate(nodes):
        if node['id'] == identifier:
            return nodes, index, node
        if node['type'] == 'folder':
            found = locate(node['children'], identifier)
            if found:
                return found
    return None


def move_node(nodes, identifier, folder=None, index=None):
    found = locate(nodes, identifier)
    if not found:
        return False
    parent, old_index, node = found
    if folder == identifier or (node['type'] == 'folder' and locate(node['children'], folder)):
        return False
    target = locate(nodes, folder) if folder else None
    if folder and (target is None or target[2]['type'] != 'folder'):
        return False
    destination = target[2]['children'] if target else nodes
    if node['type'] == 'folder':
        # Bound nesting so a saved hierarchy always round-trips intact.
        def depth(items):
            return max((1+depth(n['children']) for n in items if n['type'] == 'folder'), default=0)
        def path_depth(items, target_id, level=0):
            for n in items:
                if n['id'] == target_id:
                    return level+1
                if n['type'] == 'folder':
                    d = path_depth(n['children'], target_id, level+1)
                    if d:
                        return d
            return 0
        if (path_depth(nodes, folder) if folder else 0)+1+depth(node['children']) > 8:
            return False
    parent.pop(old_index)
    if index is None:
        index = len(destination)
    elif destination is parent and old_index < index:
        index -= 1
    destination.insert(max(0, min(index, len(destination))), node)
    return True


class BookmarkList(QListWidget):
    def __init__(self, navigator):
        super().__init__()
        self.navigator = navigator
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setSpacing(3)
        self.setIconSize(QSize(16, 16))
        self.setFixedHeight(32)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setObjectName('bookmarks')
        self.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.itemClicked.connect(navigator.bookmark_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(navigator.context_menu)

    def startDrag(self, actions):
        item = self.currentItem()
        if item is None:
            return
        mime = QMimeData()
        mime.setData(MIME, json.dumps([self.navigator.window.board.desk_id,
                                     item.data(Qt.ItemDataRole.UserRole)]).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(MIME):
            item = self.itemAt(event.position().toPoint())
            if item:
                self.setCurrentItem(item)
            event.acceptProposedAction()

    def dropEvent(self, event):
        try:
            desk, source = json.loads(bytes(event.mimeData().data(MIME)))
            if desk != self.navigator.window.board.desk_id:
                return
            item = self.itemAt(event.position().toPoint())
            folder, index = self.navigator.folder, None
            if item:
                target_id = item.data(Qt.ItemDataRole.UserRole)
                node = locate(self.navigator.nodes(), target_id)[2]
                rect = self.visualItemRect(item)
                relative = (event.position().x()-rect.x())/max(1, rect.width())
                if node['type'] == 'folder' and .22 < relative < .78:
                    folder = target_id
                else:
                    siblings = self.navigator.children()
                    index = next(i for i, n in enumerate(siblings) if n['id'] == target_id)
                    index += relative > .5
            self.navigator.move(source, folder, index)
            event.acceptProposedAction()
        except (ValueError, TypeError, IndexError):
            event.ignore()

    def wheelEvent(self, event):
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value()-event.angleDelta().y())
        event.accept()


class SheetNavigator(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.folder = None
        self.desk_id = None
        self.signature = None
        row = QHBoxLayout(self)
        row.setContentsMargins(66, 0, 10, 0)
        row.setSpacing(5)
        self.home = button('Весь стол', self.show_board, 'Показать все листы и референсы на столе')
        self.up = button('‹', self.parent_folder, 'На уровень выше · перетащить лист в другую папку можно через правую кнопку')
        self.up.setFixedWidth(32)
        self.location = label('', 'muted')
        self.location.setMaximumWidth(140)
        self.list = BookmarkList(self)
        self.add = button('+', self.add_menu, 'Новый лист или папка')
        self.add.setFixedWidth(32)
        for control in (self.home, self.up, self.add):
            control.setFixedHeight(28)
            control.setStyleSheet('padding: 3px 8px; min-height: 0;')
        row.addWidget(self.home)
        row.addWidget(self.up)
        row.addWidget(self.location)
        row.addWidget(self.list, 1)
        row.addWidget(self.add)

    def nodes(self):
        doc = self.window.board.doc
        doc.navigation = normalized_navigation(doc.navigation, doc.papers)
        return doc.navigation

    def children(self):
        nodes = self.nodes()
        found = locate(nodes, self.folder) if self.folder else None
        return found[2]['children'] if found else nodes

    def refresh(self):
        board = self.window.board
        if self.desk_id != board.desk_id:
            self.folder = getattr(board, '_navigation_folder', None)
            self.desk_id = board.desk_id
        nodes = self.nodes()
        found = locate(nodes, self.folder) if self.folder else None
        if self.folder and not found:
            self.folder = None
        papers = {p.id: p for p in board.doc.papers}
        children = self.children()
        signature = (self.desk_id, self.folder, json.dumps(children, ensure_ascii=False),
                     tuple((p.id, p.name, p.kind) for p in board.doc.papers))
        if signature != self.signature:
            scroll = self.list.horizontalScrollBar().value()
            self.list.clear()
            for node in children:
                paper = papers.get(node['id'])
                if node['type'] == 'paper' and paper is None:
                    continue
                title = node['name']+'  ▸' if node['type'] == 'folder' else paper.name
                item = QListWidgetItem(tool_icon('folder' if node['type'] == 'folder' else 'sheet'), title)
                item.setData(Qt.ItemDataRole.UserRole, node['id'])
                item.setToolTip(title+'\nПеретащите для перестановки или в середину папки. Правый щелчок — действия. Колесо — прокрутка вкладок.')
                item.setSizeHint(QSize(min(240, max(105, self.fontMetrics().horizontalAdvance(title)+40)), 26))
                self.list.addItem(item)
            self.list.horizontalScrollBar().setValue(scroll)
            self.signature = signature
        selected = {p.id for p in board.selected_papers()}
        for i in range(self.list.count()):
            self.list.item(i).setSelected(self.list.item(i).data(Qt.ItemDataRole.UserRole) in selected)
        self.up.setEnabled(bool(self.folder))
        self.up.setToolTip('На уровень выше · '+(found[2]['name'] if found else 'Корень'))
        self.location.setVisible(bool(found))
        self.location.setText(found[2]['name']+' /' if found else '')
        board._navigation_folder = self.folder
        self.home.setText('Все листы' if board.focused_paper_id else 'Весь стол')

    def open(self, identifier):
        found = locate(self.nodes(), identifier)
        if not found:
            return
        if found[2]['type'] == 'folder':
            self.folder = identifier
            self.refresh()
            return
        board = self.window.board
        paper = next((p for p in board.doc.papers if p.id == identifier), None)
        if paper:
            board.pointer_up()
            board.focused_paper_id = paper.id
            board.set_selection(paper)
            bounds = board.view_transform().mapRect(paper.transform().mapRect(QRectF(0, 0, paper.width, paper.height)))
            factor = min((board.width()-70)/max(1., bounds.width()), (board.height()-80)/max(1., bounds.height()))
            board.zoom = max(.06, min(6., board.zoom*factor))
            center = paper.transform().map(QPointF(paper.width/2, paper.height/2))
            board.offset += QPointF(board.width()/2, board.height()/2)-board.to_screen(center)
            board.zoom_changed.emit(board.zoom)
            board.update()
            board.setFocus()

    def show_board(self):
        board = self.window.board
        board.focused_paper_id = None
        board.fit()
        self.refresh()
        board.setFocus()

    def parent_folder(self):
        def parent(nodes, target, owner=None):
            for n in nodes:
                if n['id'] == target:
                    return True, owner
                if n['type'] == 'folder':
                    found, value = parent(n['children'], target, n['id'])
                    if found:
                        return True, value
            return False, None
        self.folder = parent(self.nodes(), self.folder)[1]
        self.refresh()

    def edit(self, title, operation):
        board = self.window.board
        board.pointer_up()
        doc = board.doc
        before = deepcopy(self.nodes())
        after = deepcopy(before)
        operation(after)
        if before != after:
            board.history.push(Change(board, title, lambda: setattr(doc, 'navigation', deepcopy(before)),
                                      lambda: setattr(doc, 'navigation', deepcopy(after))))
            self.refresh()

    def move(self, identifier, folder, index=None):
        self.edit('Перемещение вкладки', lambda nodes: move_node(nodes, identifier, folder, index))

    def new_folder(self):
        name, ok = QInputDialog.getText(self, 'Новая папка', 'Название папки')
        if ok and name.strip():
            def insert(nodes):
                found = locate(nodes, self.folder) if self.folder else None
                target = found[2]['children'] if found else nodes
                target.append({'id': 'folder-'+uuid.uuid4().hex, 'type': 'folder',
                               'name': name.strip()[:80], 'children': []})
            self.edit('Новая папка', insert)

    def bookmark_clicked(self, item):
        identifier = item.data(Qt.ItemDataRole.UserRole)
        board = self.window.board
        paper = next((p for p in board.doc.papers if p.id == identifier), None)
        if paper and QApplication.keyboardModifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            board.toggle_paper_selection(paper)
            self.refresh()
        else:
            self.open(identifier)

    def new_sheet(self):
        before = {p.id for p in self.window.board.doc.papers}
        self.window.new_paper()
        paper = self.window.board.selected
        if paper and paper.id not in before and self.folder:
            self.move(paper.id, self.folder)

    def add_menu(self):
        menu = QMenu(self)
        menu.addAction('Новый лист', self.new_sheet)
        menu.addAction('Новая папка', self.new_folder)
        menu.exec(self.add.mapToGlobal(self.add.rect().bottomLeft()))

    def context_menu(self, pos):
        menu = QMenu(self)
        item = self.list.itemAt(pos)
        if item:
            identifier = item.data(Qt.ItemDataRole.UserRole)
            node = locate(self.nodes(), identifier)[2]
            menu.addAction('Открыть', lambda: self.open(identifier))
            if node['type'] == 'paper':
                def select_item():
                    board = self.window.board
                    paper = next((p for p in board.doc.papers if p.id == identifier), None)
                    if paper not in board.selected_papers():
                        board.set_selection(paper)
                def copy_item():
                    select_item()
                    self.window.copy_sheets()
                def duplicate_item():
                    select_item()
                    self.window.duplicate_sheets()
                menu.addAction('Копировать листы · Ctrl+C', copy_item)
                menu.addAction('Дублировать · Ctrl+D', duplicate_item)
            move = menu.addMenu('Переместить в…')
            move.addAction('Корень стола', lambda: self.move(identifier, None))
            def folders(nodes, path=''):
                for n in nodes:
                    if n['type'] == 'folder' and n['id'] != identifier:
                        name = path+n['name']
                        move.addAction(name, lambda checked=False, key=n['id']: self.move(identifier, key))
                        folders(n['children'], name+' / ')
            folders(self.nodes())
            if node['type'] == 'folder':
                def rename():
                    name, ok = QInputDialog.getText(self, 'Название папки', 'Название', text=node['name'])
                    if ok and name.strip():
                        self.edit('Название папки', lambda nodes: locate(nodes, identifier)[2].update(name=name.strip()[:80]))
                menu.addAction('Переименовать', rename)
                def ungroup(nodes):
                    parent, index, n = locate(nodes, identifier)
                    parent[index:index+1] = n['children']
                menu.addAction('Убрать папку, оставить содержимое', lambda: self.edit('Убрать папку', ungroup))
            menu.addSeparator()
        menu.addAction('Новый лист', self.new_sheet)
        menu.addAction('Новая папка', self.new_folder)
        menu.exec(self.list.viewport().mapToGlobal(pos))
