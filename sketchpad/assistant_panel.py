"""Conversational sketch helper and GUI-thread-only canvas integration."""
from dataclasses import dataclass
import html
import json
import os
from pathlib import Path
import uuid
from PySide6.QtCore import Qt, QThread, Signal, QUrl, QRectF, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QImage, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser, QPlainTextEdit, QScrollArea, QSlider,
                              QComboBox, QCheckBox, QLineEdit, QLabel, QProgressBar, QFileDialog)
from . import assistant_backend as backend
from .board import Change
from .brush import empty_ink, image_array
from .model import Paper, composite_paper, png_bytes
from .widgets import button, label, Foldout
from .assistant_chat import ChatMixin, ChatComposer
from .inpaint import (MASK_TOOLS, DEFAULT_FEATHER, MAX_FEATHER, api_mask, region_guide, merge_inpaint,
                      prepare_inpaint_inputs, frame_prompt, validate_crop)


@dataclass
class Source:
    board: object
    document: object
    paper_id: str
    name: str
    title: str
    mask: QImage | None = None
    feather: int = DEFAULT_FEATHER
    harmonize: bool = True
    crop: tuple | None = None


def saved_receipts(board):
    return {paper.id: {k: receipt[k] for k in ('source_id', 'mask_path', 'raw_path', 'title', 'crop') if k in receipt}
            for paper in board.doc.papers if (receipt := getattr(paper, 'inpaint_receipt', None))
            and receipt.get('document_id') == id(board.doc)}


def restore_receipts(board, values, roots):
    """Optional local edit recipes. Missing files never prevent opening artwork."""
    if not isinstance(values, dict):
        return
    allowed = [Path(root).resolve() for root in roots]
    papers = {p.id: p for p in board.doc.papers}
    for identifier, record in values.items():
        if (identifier not in papers or not isinstance(record, dict)
                or not isinstance(record.get('source_id'), str) or record['source_id'] not in papers):
            continue
        if not all(isinstance(record.get(key), str) for key in ('mask_path', 'raw_path', 'title')):
            continue
        try:
            crop = validate_crop(record.get('crop'), papers[record['source_id']].image.size())
            raw, mask = (Path(record[key]) for key in ('raw_path', 'mask_path'))
            if not all(path.is_absolute() and not path.drive.startswith('\\\\') for path in (raw, mask)):
                continue
            raw, mask = raw.resolve(), mask.resolve()
            if raw.parent != mask.parent or not any(raw.is_relative_to(root) for root in allowed):
                continue
            if raw.suffix.lower() not in ('.png', '.webp', '.jpg', '.jpeg') or mask.suffix.lower() != '.png':
                continue
            if not all(path.is_file() and path.stat().st_size <= backend.MAX_IMAGE for path in (raw, mask)):
                continue
            papers[identifier].inpaint_receipt = {**record, 'document_id': id(board.doc), 'crop': crop,
                                                 'raw_path': str(raw), 'mask_path': str(mask)}
        except (OSError, ValueError):
            continue


def place_result(source, path, title, after=None, output_name='inpaint.png'):
    """Never write to a new document that happens to occupy the same tab."""
    board = source.board
    original = next((p for p in source.document.papers if p.id == source.paper_id), None)
    if board.doc is not source.document or original is None:
        raise backend.AssistantError('Исходный лист был удалён или заменён. Результат сохранён в файле; его можно добавить как референс.')
    if after is not None and (after not in board.doc.papers or (after.width, after.height) != (original.width, original.height)):
        raise backend.AssistantError('Лист для нового варианта был удалён или изменён.')
    reader = QImageReader(str(path))
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0 or size.width()*size.height() > 32_000_000:
        raise backend.AssistantError('Сервис вернул некорректное или слишком большое изображение.')
    image = reader.read()
    if image.isNull():
        raise backend.AssistantError('Не удалось прочитать изображение результата.')
    if source.mask is not None:
        try:
            # Preserve even strokes added outside the selection while the job ran.
            ink = merge_inpaint(composite_paper(board.doc, original), image, source.mask, source.feather, source.harmonize, source.crop)
            if after is not None:
                # Preserve manual additions made on the previous result outside
                # the generated region; inside, construct a fresh variant.
                previous = composite_paper(board.doc, after).convertToFormat(QImage.Format.Format_RGBA8888)
                protected = image_array(source.mask)[..., 3] == 0
                image_array(ink)[protected] = image_array(previous)[protected]
        except ValueError as exc:
            raise backend.AssistantError(str(exc)) from exc
        final_path = Path(path).with_name(output_name)
        if not ink.save(str(final_path), 'PNG'):
            raise backend.AssistantError('Не удалось сохранить итог локальной перерисовки.')
        final_path.with_suffix('.json').write_text(json.dumps({'source_id': source.paper_id, 'raw_result': Path(path).name,
                                                              'feather': source.feather, 'harmonize': source.harmonize,
                                                              'crop': source.crop, 'image': final_path.name}), encoding='utf8')
    else:
        # Preserve generated pixels instead of downsampling them to the sketch.
        # Match the old physical footprint by changing the sheet's view scale.
        factor = max(1., image.width()/original.width, image.height()/original.height)
        ink = empty_ink(round(original.width*factor), round(original.height*factor))
        fit = min(ink.width()/image.width(), ink.height()/image.height())
        width, height = image.width()*fit, image.height()*fit
        painter = QPainter(ink)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(QRectF((ink.width()-width)/2, (ink.height()-height)/2, width, height), image)
        painter.end()
    anchor = after or original
    paper = Paper(name='ИИ · '+title, x=anchor.x, y=anchor.y, width=ink.width(),
                  height=ink.height(), angle=anchor.angle, scale=anchor.scale*original.width/ink.width(),
                  image=ink, paper_style=original.paper_style)
    index = source.document.papers.index(anchor)+1
    if source.mask is not None:
        mask_path = Path(path).with_name('selection-'+paper.id+'.png')
        if not source.mask.save(str(mask_path), 'PNG'):
            raise backend.AssistantError('Не удалось сохранить область для настройки перехода.')
        # Pixel data remains self-contained; the optional recipe uses local job files.
        paper.inpaint_receipt = {'document_id': id(board.doc), 'source_id': source.paper_id,
                                 'mask_path': str(mask_path), 'raw_path': str(path), 'title': source.title, 'crop': source.crop}

    def undo():
        if paper in source.document.papers:
            source.document.papers.remove(paper)
        if board.selected is paper:
            board.set_selection(anchor)

    def redo():
        source.document.papers.insert(min(index, len(source.document.papers)), paper)
        board.set_selection(paper)

    # Do not merge asynchronous insertion into a live pen gesture.
    board.pointer_up()
    board.flush_dual_move()
    board.history.push(Change(board, 'Результат помощника', undo, redo))
    return paper


class Worker(QThread):
    progress = Signal(str)
    succeeded = Signal(dict)
    failed = Signal(str)

    def __init__(self, task, parent):
        super().__init__(parent)
        self.task = task
        self.cancel = backend.Cancellation()

    def run(self):
        try:
            result = self.task(self.cancel, self.progress.emit)
            self.cancel.check()
            self.succeeded.emit(result)
        except backend.AssistantError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit('Не удалось завершить задачу. Проверьте подключение и повторите её вручную.')
        finally:
            self.task = None


class AssistantPanel(ChatMixin, QWidget):
    def __init__(self, window, data):
        super().__init__(window, Qt.WindowType.Tool)
        self.setWindowTitle('Помощник · Atelier')
        self.setMinimumHeight(520)
        self.resize(380, 820)
        self.window, self.data = window, Path(data)
        self.setObjectName('inspectorPage')
        self.setMinimumWidth(380)
        self.worker = None
        self.source = None
        self.busy = False
        self.styles = backend.load_presets()
        self.last_result = None
        self.messages = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(label('Помощник', 'panelTitle'))
        heading.addStretch()
        close = button('×', window.toggle_assistant, 'Скрыть помощника')
        close.setFixedWidth(34)
        heading.addWidget(close)
        layout.addLayout(heading)
        source_row = QHBoxLayout()
        self.source_preview = QLabel()
        self.source_preview.setFixedSize(58, 42)
        source_row.addWidget(self.source_preview)
        self.source_label = label('', 'muted')
        self.source_label.setWordWrap(True)
        source_row.addWidget(self.source_label, 1)
        layout.addLayout(source_row)
        self.chat = QTextBrowser()
        self.chat.setOpenLinks(False)
        self.chat.anchorClicked.connect(self.open_result)
        self.chat.setMinimumHeight(160)
        self.chat.setStyleSheet('QTextBrowser { background:#1c2320; border:1px solid #343e35; border-radius:10px; padding:9px; }')
        layout.addWidget(self.chat, 3)
        history = self.data/'assistant-history.json'
        try:
            if history.is_file() and history.stat().st_size < 16*1024*1024:
                records = json.loads(history.read_text(encoding='utf8'))
                if isinstance(records,list):
                    self.messages = [m for m in records if isinstance(m,dict) and isinstance(m.get('who'),str)
                                     and isinstance(m.get('text'),str) and (m.get('result') is None or isinstance(m['result'],str))]
        except (OSError, ValueError):
            pass
        for message in self.messages:
            self.append_message(message['who'], message['text'], message.get('result'))
        if not self.messages:
            self.say('Помощник', 'Помогу с рисунком и инструментами Atelier, разложу листы и референсы, настрою кисть или перспективу. Для генерации выберите режим «Изображение».')
        main = layout
        form = QWidget()
        form.setObjectName('inspectorPage')
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(9)
        self.options_scroll = QScrollArea()
        self.options_scroll.setWidgetResizable(True)
        self.options_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.options_scroll.setWidget(form)
        self.options_scroll.setMinimumHeight(100)
        main.addWidget(self.options_scroll, 1)
        self.mode = QComboBox()
        self.mode.addItem('Весь лист · доработка скетча', 'whole')
        self.mode.addItem('Inpaint · только выделенная область', 'inpaint')
        layout.addWidget(self.mode)
        self.mask_controls = QWidget()
        mask_layout = QVBoxLayout(self.mask_controls)
        mask_layout.setContentsMargins(0, 0, 0, 0)
        mask_layout.setSpacing(7)
        tools_row = QHBoxLayout()
        tools_row.setSpacing(4)
        self.mask_buttons = {}
        for title, tool in [('Кисть +', 'mask_add'), ('Убрать −', 'mask_remove'), ('Прямоуг.', 'mask_rect')]:
            control = button(title, lambda checked=False, t=tool: self.activate_mask(t), 'Выделить область на выбранном листе', True)
            control.setStyleSheet('padding:7px 8px;')
            self.mask_buttons[tool] = control
            tools_row.addWidget(control)
        mask_layout.addLayout(tools_row)
        self.detail = QCheckBox('Больше деталей · крупный план')
        self.detail.setToolTip('Для небольших участков: увеличить выделение с запасом фона для помощника, затем вернуть на место. Для общей композиции оставьте выключенным. Применяется при новой генерации.')
        self.detail.toggled.connect(self.detail_changed)
        mask_layout.addWidget(self.detail)
        self.mask_detail = Foldout('Размер кисти и мягкость края')
        mask_layout.addWidget(self.mask_detail)
        self.mask_size_label = label('Кисть выделения · 100 px', 'muted')
        self.mask_detail.content.addWidget(self.mask_size_label)
        self.mask_size = QSlider(Qt.Orientation.Horizontal)
        self.mask_size.setRange(8, 500)
        self.mask_size.setValue(100)
        self.mask_size.setToolTip('Размер кисти выделения: [ — меньше, ] — больше; в русской раскладке Х и Ъ.')
        self.mask_size.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mask_size.valueChanged.connect(self.mask_size_changed)
        self.mask_detail.content.addWidget(self.mask_size)
        self.feather_label = label(f'Мягкость края · {DEFAULT_FEATHER} px', 'muted')
        self.mask_detail.content.addWidget(self.feather_label)
        self.feather = QSlider(Qt.Orientation.Horizontal)
        self.feather.setRange(0, MAX_FEATHER)
        self.feather.setValue(DEFAULT_FEATHER)
        self.feather.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.feather.setToolTip('Смягчение идёт внутрь выделения. За его границей пиксели не меняются.')
        self.feather.valueChanged.connect(self.feather_changed)
        self.mask_detail.content.addWidget(self.feather)
        self.harmonize = QCheckBox('Согласовать фон у края')
        self.harmonize.setChecked(True)
        self.harmonize.setToolTip('Мягко подогнать оттенок фона в переходной полосе. Цвет новых объектов в центре сохраняется.')
        self.harmonize.toggled.connect(self.harmonize_changed)
        self.mask_detail.content.addWidget(self.harmonize)
        self.reblend = button('Новый вариант перехода', self.reblend_result,
                             'Применить эти настройки к области исходной генерации. Новый лист, без повторного запроса к модели. Нужны сохранённые файлы исходной задачи.')
        self.mask_detail.content.addWidget(self.reblend)
        self.reblend.hide()
        selection_row = QHBoxLayout()
        self.mask_label = label('Выделите область на листе', 'muted')
        self.mask_label.setWordWrap(True)
        selection_row.addWidget(self.mask_label, 1)
        self.clear_mask = button('Очистить', self.clear_selection, 'Убрать только выделение. Рисунок не изменяется.')
        selection_row.addWidget(self.clear_mask)
        mask_layout.addLayout(selection_row)
        hint = label('Закрасьте область. Alt — убрать · Esc — карандаш.', 'muted')
        hint.setWordWrap(True)
        mask_layout.addWidget(hint)
        layout.addWidget(self.mask_controls)
        self.mask_controls.hide()
        quick = QHBoxLayout()
        quick.setSpacing(5)
        for title, index in [('Кадр', 0), ('Графит', 1), ('Перо', 2), ('Кисть', 3)]:
            item = button(title, lambda checked=False, i=index: self.quick_start(i), 'Сразу создать: '+self.styles[index]['title'])
            item.setStyleSheet('padding:7px 9px;')
            quick.addWidget(item)
        self.quick_row = QWidget()
        self.quick_row.setLayout(quick)
        quick.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.quick_row)
        self.preset = QComboBox()
        self.preset.setMinimumContentsLength(20)
        self.preset.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for item in self.styles:
            self.preset.addItem(item['title'], item['id'])
            self.preset.setItemData(self.preset.count()-1, item['description'], Qt.ItemDataRole.ToolTipRole)
        self.preset.addItem('Свой стиль · свободное описание', 'custom')
        self.preset.addItem('Сохранить стиль исходника', 'preserve')
        self.preset.setCurrentIndex(self.preset.findData('preserve'))
        self.preset.currentIndexChanged.connect(self.style_changed)
        layout.addWidget(self.preset)
        self.composer = ChatComposer()
        self.composer.setPlaceholderText('Что улучшить? Можно добавить пожелания к стилю…')
        self.composer.setFixedHeight(68)
        self.composer.setStyleSheet('QPlainTextEdit { background:#2a302c; border:1px solid #465141; border-radius:9px; padding:7px; }')
        main.addWidget(self.composer)
        self.enhance = QCheckBox('Улучшить мой промпт')
        self.enhance.setToolTip('Выключено: ваш текст передаётся без переписывания и применения стиля из меню. Включено: уточнить описание с выбранным стилем. Быстрые пресеты запускаются отдельно.')
        main.addWidget(self.enhance)
        self.prompt_hint = label('Без галочки: ваш текст без изменений; стиль из меню не применяется.', 'muted')
        self.prompt_hint.setWordWrap(True)
        main.addWidget(self.prompt_hint)
        self.enhance.toggled.connect(lambda active: self.prompt_hint.setText(
            'Описание будет уточнено с учётом выбранного стиля.' if active else
            'Без галочки: ваш текст без изменений; стиль из меню не применяется.'))
        self.provider = QComboBox()
        self.provider.addItem('Подписка ChatGPT', 'subscription')
        self.provider.addItem('OpenAI API', 'api')
        self.provider.addItem('Higgsfield', 'higgsfield')
        self.provider.currentIndexChanged.connect(self.provider_changed)
        layout.addWidget(self.provider)
        self.cost_hint = label('', 'muted')
        self.cost_hint.setWordWrap(True)
        layout.addWidget(self.cost_hint)
        self.status = label('Готов к работе', 'muted')
        self.status.setWordWrap(True)
        main.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        main.addWidget(self.progress)
        actions = QHBoxLayout()
        self.send = button('✦  Доработать скетч', self.start_generation)
        self.send.setObjectName('primary')
        self.stop = button('Отмена', self.cancel_task)
        self.stop.hide()
        actions.addWidget(self.send, 1)
        actions.addWidget(self.stop)
        main.addLayout(actions)
        self.connection = Foldout('Подключение')
        layout.addWidget(self.connection)
        self.subscription_controls = QWidget()
        sub = QVBoxLayout(self.subscription_controls)
        sub.setContentsMargins(0, 0, 0, 0)
        login_row = QHBoxLayout()
        self.check = button('Проверить', self.check_connection)
        self.login = button('Войти', self.login_subscription, 'Открыть официальный вход ChatGPT в браузере')
        login_row.addWidget(self.check)
        login_row.addWidget(self.login)
        sub.addLayout(login_row)
        self.codex = QLineEdit()
        self.codex.setPlaceholderText('Codex · автоматически')
        self.codex.setToolTip('Обычно менять не нужно. Можно указать полный путь к codex.exe.')
        sub.addWidget(self.codex)
        self.connection.content.addWidget(self.subscription_controls)
        self.api_controls = QWidget()
        api = QVBoxLayout(self.api_controls)
        api.setContentsMargins(0, 0, 0, 0)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText('API-ключ · только на время сеанса')
        self.api_key.setToolTip('Ключ не записывается в проекты и файлы настроек. Можно использовать переменную OPENAI_API_KEY.')
        api.addWidget(self.api_key)
        self.image_model = QLineEdit(backend.IMAGE_MODEL)
        self.image_model.setToolTip('Модель генерации изображений OpenAI API')
        api.addWidget(self.image_model)
        self.text_model = QLineEdit(backend.TEXT_MODEL)
        self.text_model.setToolTip('Модель OpenAI API для улучшения промпта')
        api.addWidget(self.text_model)
        self.connection.content.addWidget(self.api_controls)
        layout.addStretch()
        self.whole_preset = self.preset.currentIndex()
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.setup_higgsfield(layout)
        layout.insertWidget(0, self.provider)
        layout.insertWidget(1, self.higgs_controls)
        self.setup_chat(main, layout)
        self.provider_changed()
        self.refresh_source()

    def closeEvent(self, event):
        # Closing the floating window hides it; a running job keeps its owner.
        self.window.set_assistant_visible(False)
        event.ignore()

    def say(self, who, text, result=None):
        self.messages.append({'who': who, 'text': text, 'result': str(result) if result else None})
        self.persist_chat()
        self.append_message(who,text,result)

    def append_message(self, who, text, result=None):
        self.render_message(who, text, result)

    def persist_chat(self):
        try:
            self.data.mkdir(parents=True,exist_ok=True)
            path = self.data/'assistant-history.json'
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.messages,ensure_ascii=False,indent=2),encoding='utf8')
            temp.replace(path)
        except OSError:
            # Conversation remains visible even if the history disk is unavailable.
            pass

    def open_result(self, url):
        # Chat links are created exclusively by this widget for its own output files.
        path = Path(url.toLocalFile()).resolve()
        roots = (self.data/'assistant', backend.ROOT/'data', backend.ROOT/'qa')
        if url.isLocalFile() and any(path.is_relative_to(root.resolve()) for root in roots) and path.is_file():
            QDesktopServices.openUrl(url)

    def refresh_source(self):
        paper = self.window.board.selected
        if isinstance(paper, Paper):
            self.source_label.setText(self.window.board.desk_name+' · '+paper.name+'\nСнимок выбранного листа')
            self.source_preview.setPixmap(self.window.sheet_icon(paper).pixmap(58, 42))
        else:
            self.source_label.setText('Выберите лист со скетчем')
            self.source_preview.clear()
        self.refresh_mask()

    def mode_changed(self):
        local = self.mode.currentData() == 'inpaint'
        if local:
            self.whole_preset = self.preset.currentIndex()
            self.preset.setCurrentIndex(self.preset.findData('preserve'))
        else:
            self.preset.setCurrentIndex(self.whole_preset)
            if self.window.board.tool in MASK_TOOLS:
                self.window.board.set_tool('pencil')
        self.mask_controls.setVisible(local)
        self.quick_row.setVisible(not local)
        self.update_send_label()
        self.style_changed()
        self.refresh_mask()
        if local:
            self.activate_mask('mask_add')

    def activate_mask(self, tool):
        board = self.window.board
        if not board.inpaint.ensure(board.selected):
            self.say('Помощник', 'Выберите лист или изображение, на котором нужно выделить область.')
            return
        if board.dual:
            self.window.toggle_dual()
        board.set_tool(tool)
        board.inpaint.visible = True
        self.refresh_mask()
        board.setFocus()

    def mask_size_changed(self, value):
        self.window.board.inpaint.diameter = value
        self.mask_size_label.setText(f'Кисть выделения · {value} px')
        self.window.board.update()

    def feather_changed(self, value):
        self.window.board.inpaint.feather = value
        self.feather_label.setText(f'Мягкость края · {value} px')

    def harmonize_changed(self, value):
        self.window.board.inpaint.harmonize = value

    def detail_changed(self, value):
        self.window.board.inpaint.detail = value

    def reblend_result(self):
        board, target = self.window.board, self.window.board.selected
        receipt = getattr(target, 'inpaint_receipt', None)
        if self.busy or not receipt or receipt['document_id'] != id(board.doc):
            return
        try:
            mask = QImage(receipt['mask_path']).convertToFormat(QImage.Format.Format_RGBA8888)
            if mask.isNull():
                raise backend.AssistantError('Сохранённое выделение недоступно. Создайте новую локальную правку.')
            source = Source(board, board.doc, receipt['source_id'], '', receipt['title'], mask,
                            board.inpaint.feather, board.inpaint.harmonize, receipt.get('crop'))
            board.pointer_up()
            name = 'inpaint-soft-'+uuid.uuid4().hex[:12]+'.png'
            paper = place_result(source, Path(receipt['raw_path']), 'Мягкий переход', after=target, output_name=name)
            path = Path(receipt['raw_path']).with_name(name)
            self.last_result = {'path': str(path), 'raw_path': receipt['raw_path']}
            board.set_tool('pencil')
            self.say('Помощник', 'Добавлен новый вариант перехода. Предыдущий лист сохранён; запрос к модели не отправлялся.', path)
            self.status.setText('Переход обновлён без генерации')
        except (backend.AssistantError, OSError) as exc:
            self.say('Помощник', str(exc))

    def clear_selection(self):
        self.window.board.inpaint.clear()

    def refresh_mask(self):
        board = self.window.board
        selection = board.inpaint
        selection.visible = self.mode.currentData() == 'inpaint' and self.isVisible()
        for tool, control in self.mask_buttons.items():
            control.setChecked(board.tool == tool)
        for control, value in ((self.mask_size, selection.diameter), (self.feather, selection.feather)):
            control.blockSignals(True)
            control.setValue(value)
            control.blockSignals(False)
        self.mask_size_label.setText(f'Кисть выделения · {selection.diameter} px')
        self.feather_label.setText(f'Мягкость края · {selection.feather} px')
        self.harmonize.blockSignals(True)
        self.harmonize.setChecked(selection.harmonize)
        self.harmonize.blockSignals(False)
        self.detail.blockSignals(True)
        self.detail.setChecked(selection.detail)
        self.detail.blockSignals(False)
        receipt = getattr(board.selected, 'inpaint_receipt', None)
        self.reblend.setVisible(bool(receipt and receipt['document_id'] == id(board.doc)))
        self.reblend.setEnabled(not self.busy)
        selected = selection.has_selection(board.selected)
        self.mask_label.setText('Область готова к перерисовке' if selected else 'Выделите область на листе')
        self.clear_mask.setEnabled(selected)
        board.update()

    def style_changed(self):
        if self.mode.currentData() == 'inpaint':
            self.composer.setPlaceholderText('Что изменить внутри выделения? Например: заменить горшок на стеклянную вазу…')
            return
        custom = self.preset.currentData() == 'custom'
        self.composer.setPlaceholderText('Опишите технику, характер линий, свет и желаемую доработку…' if custom else 'Что улучшить? Можно добавить пожелания к стилю…')

    def provider_changed(self):
        subscription = self.provider.currentData() == 'subscription'
        higgsfield = self.provider.currentData() == 'higgsfield'
        self.subscription_controls.setVisible(subscription or higgsfield)
        self.api_controls.setVisible(self.provider.currentData() == 'api')
        self.higgs_controls.setVisible(higgsfield)
        self.higgs_connection.setVisible(higgsfield)
        self.cost_hint.setText('Изображения: Higgsfield, расходуются кредиты аккаунта. Переписка: подписка ChatGPT.' if higgsfield else
                              'Используется лимит подписки ChatGPT.' if subscription else 'OpenAI API оплачивается отдельно от подписки ChatGPT.')

    def uses_board(self, board):
        return self.busy and (getattr(self, 'agent_active', False) or self.source is not None and self.source.board is board)

    def start_worker(self, task, success):
        if self.busy:
            return
        self.busy = True
        self.send.setEnabled(False)
        for widget in (self.provider, self.connection, self.quick_row, self.preset, self.mode, self.channel, self.higgs_controls):
            widget.setEnabled(False)
        self.progress.show()
        self.stop.show()
        self.worker = Worker(task, self)
        self.reblend.setEnabled(False)
        self.worker.progress.connect(self.status.setText)
        self.worker.succeeded.connect(success)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def worker_finished(self):
        worker = self.worker
        self.worker = None
        self.busy = False
        self.source = None
        self.send.setEnabled(not self.pending_actions)
        for widget in (self.provider, self.connection, self.quick_row, self.preset, self.mode, self.channel, self.higgs_controls):
            widget.setEnabled(True)
        self.progress.hide()
        self.stop.hide()
        worker.deleteLater()
        self.refresh_mask()
        if self.after_worker:
            callback, self.after_worker = self.after_worker, None
            QTimer.singleShot(0, callback)

    def failed(self, message):
        chat = self.agent_active
        self.agent_active = False
        self.after_worker = None
        self.status.setText('Не удалось завершить запрос' if chat else 'Задача завершена без нового листа')
        self.say('Помощник', message)

    def cancel_task(self):
        self.agent_active = False
        self.after_worker = None
        if self.worker:
            self.status.setText('Останавливаю задачу…')
            self.worker.cancel.cancel()

    def check_connection(self):
        path = self.codex.text()
        self.status.setText('Проверяю вход…')
        self.start_worker(lambda cancel, progress: backend.subscription_status(cancel, path), self.connection_result)

    def login_subscription(self):
        path = self.codex.text()
        self.status.setText('Завершите вход в открывшемся браузере…')
        self.start_worker(lambda cancel, progress: backend.subscription_login(cancel, path), self.connection_result)

    def connection_result(self, result):
        self.status.setText(result['message'])
        self.say('Помощник', result['message'])

    def quick_start(self, index):
        self.channel.setCurrentIndex(1)
        self.preset.setCurrentIndex(index)
        self.start_generation(force_style=True)

    def start_generation(self, checked=False, force_style=False):
        if self.busy:
            return
        board = self.window.board
        paper = board.selected
        if not isinstance(paper, Paper):
            self.say('Помощник', 'Сначала выберите лист со скетчем.')
            return
        preset = next((p for p in self.styles if p['id'] == self.preset.currentData()), None)
        notes = self.composer.toPlainText()
        local = self.mode.currentData() == 'inpaint'
        enhanced = self.enhance.isChecked()
        if notes.strip() and not enhanced and not force_style:
            preset = None
        if not preset and self.preset.currentData() == 'preserve' and (not notes.strip() or enhanced):
            preset = {'id': 'preserve', 'title': 'Стиль исходника',
                      'prompt': 'Preserve the source medium, palette and stroke character. Follow the user request. Do not introduce sepia, yellow tint or aged paper unless requested.'}
        try:
            board.pointer_up()
            if local and not board.inpaint.has_selection(paper):
                raise backend.AssistantError('Выделите нужное место кистью или прямоугольником, затем опишите изменение.')
            prompt = backend.build_prompt(preset, notes, inpaint=local, enhance=enhanced, force_style=force_style)
            key = self.api_key.text().strip() or os.environ.get('OPENAI_API_KEY', '')
            if self.provider.currentData() == 'api' and not key:
                raise backend.AssistantError('Раскройте «Подключение» и введите API-ключ. Он останется только в памяти до закрытия Atelier.')
            board.pointer_up()
            board.flush_dual_move()
            image = composite_paper(board.doc, paper)
            selection = board.inpaint.image.copy() if local else None
            crop = None
            if local:
                image, resized, crop = prepare_inpaint_inputs(image, selection, board.inpaint.detail, board.inpaint.feather)
            # Whole-image edits receive the original raster without an
            # unconditional 2048px resize. The provider may still resize it.
            directory = (self.data/'assistant'/uuid.uuid4().hex).resolve()
            directory.mkdir(parents=True)
            source_path = directory/'source.png'
            source_path.write_bytes(png_bytes(image))
            mask_path = guide_path = None
            if local:
                selection.save(str(directory/'selection.png'), 'PNG')
                mask_path, guide_path = directory/'mask.png', directory/'region.png'
                mask_path.write_bytes(png_bytes(api_mask(resized)))
                guide_path.write_bytes(png_bytes(region_guide(image, resized)))
            region_context = backend.INPAINT_BRIEF+frame_prompt(selection, crop) if local else ''
            title = ('Локальная правка' if local else 'Свой стиль') if not preset else ('Локально · ' if local else '')+preset['title']
            if crop:
                title += ' · Детали'
            self.source = Source(board, board.doc, paper.id, paper.name, title, selection, board.inpaint.feather, board.inpaint.harmonize, crop)
            metadata = {'desk': board.desk_name, 'source_id': paper.id, 'source_name': paper.name,
                        'preset': preset['id'] if preset else 'custom', 'notes': notes,
                        'mode': 'inpaint' if local else 'whole', 'feather': board.inpaint.feather if local else 0,
                        'harmonize': board.inpaint.harmonize if local else False, 'crop': crop,
                        'input_size': [image.width(), image.height()]}
            (directory/'source.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf8')
            request = backend.Request(source_path, directory, prompt, self.provider.currentData(),
                                      self.enhance.isChecked(), key, self.image_model.text().strip(),
                                      self.text_model.text().strip(), self.codex.text().strip())
            request.mask, request.guide = mask_path, guide_path
            request.context = region_context
            if request.provider == 'higgsfield':
                request.provider_options = {**self.higgs_settings(), 'source_size': [image.width(), image.height()]}
                if not request.provider_options.get('workspace'):
                    raise backend.AssistantError('Раскройте «Подключение» и выполните вход в Higgsfield. Затем выберите рабочее пространство.')
                self.higgs_save_settings()
            (directory/'request.json').write_text(json.dumps({'prompt': prompt, 'context': region_context,
                'enhance': enhanced, 'provider': request.provider}, ensure_ascii=False, indent=2), encoding='utf8')
        except (backend.AssistantError, OSError, ValueError) as exc:
            self.say('Помощник', str(exc))
            return
        self.say('Вы', title+' · '+board.desk_name+' / '+paper.name+('\n'+notes if notes else ''), source_path)
        # Keep the brief available for editing/retrying as well as in the chat.
        self.status.setText('Подготавливаю скетч…')

        def task(cancel, progress):
            try:
                return backend.generate(request, cancel, progress)
            finally:
                request.key = ''

        self.start_worker(task, self.generation_result)

    def generation_result(self, result):
        self.last_result = result
        path = Path(result['path'])
        if self.worker.cancel.event.is_set():
            self.say('Помощник', 'Запрос уже успел завершиться. Файл сохранён, но лист не добавлен после отмены.', path)
            self.status.setText('Задача отменена')
            return
        try:
            paper = place_result(self.source, path, self.source.title)
        except (backend.AssistantError, OSError) as exc:
            self.say('Помощник', str(exc), path)
            self.status.setText('Результат сохранён в файле')
            return
        if self.source.mask is not None:
            path = path.with_name('inpaint.png')
            self.last_result = {**result, 'path': str(path), 'raw_path': result['path']}
            if self.source.board.tool in MASK_TOOLS:
                self.source.board.set_tool('pencil')
        self.say('Помощник', 'Готово: «'+paper.name+'» на столе «'+self.source.board.desk_name+'».\nМожно продолжить рисовать на нём или убрать его через отмену. Для следующей доработки выберите нужный лист и напишите пожелания.', path)
        if result.get('provider') == 'higgsfield':
            job_path = Path(result['path']).with_name('higgsfield-job.json')
            try:
                record = json.loads(job_path.read_text(encoding='utf8'))
                record['placed'] = paper.id
                job_path.write_text(json.dumps(record), encoding='utf8')
            except (OSError, ValueError):
                pass
        self.status.setText('Новый лист добавлен над исходным')
        self.refresh_source()
