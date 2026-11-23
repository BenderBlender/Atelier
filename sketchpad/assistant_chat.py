"""Chat presentation, bounded agent loop, reviewable destructive actions and image settings."""
import html
import json
from pathlib import Path
import uuid
from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QTextDocument, QImage
from PySide6.QtWidgets import QPlainTextEdit, QComboBox, QHBoxLayout, QVBoxLayout, QWidget, QCheckBox, QMenu
from .widgets import button, label, tool_icon
from . import assistant_backend as backend
from . import agent_backend, agent_tools, higgsfield_backend as higgs
from .model import Paper, composite_paper, png_bytes


class ChatComposer(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            event.accept()
            self.submitted.emit()
        else:
            super().keyPressEvent(event)


class ChatMixin:
    def setup_chat(self, main, options):
        self.resize(460, 820)
        self.setMinimumWidth(380)
        self.setMaximumWidth(900)
        self.chat.setMinimumHeight(220)
        self.chat.setStyleSheet('QTextBrowser { background:#1b211f; border:0; padding:10px; selection-background-color:#53664f; }')
        self.composer.setFixedHeight(92)
        self.composer.submitted.connect(self.send_message)
        self.channel = QComboBox()
        self.channel.addItem('Чат · помощник Atelier', 'chat')
        self.channel.addItem('Изображение · генерация', 'image')
        self.channel.setToolTip('Чат отвечает и управляет Atelier. Изображение отправляет ваш промпт напрямую генератору.')
        row = QHBoxLayout()
        row.addWidget(self.channel, 1)
        self.settings_toggle = button('Настройки', self.toggle_options, 'Модель, стиль, выделение и подключение', True)
        row.addWidget(self.settings_toggle)
        menu = button('', self.conversation_menu, 'История и новый чат')
        menu.setIcon(tool_icon('more'))
        menu.setFixedWidth(34)
        row.addWidget(menu)
        main.insertLayout(2, row)
        self.suggestions = QWidget()
        quick = QHBoxLayout(self.suggestions)
        quick.setContentsMargins(0, 0, 0, 0)
        for title, prompt in [('Инструменты', 'Что ты умеешь делать в Atelier?'),
                              ('Разложить', 'Разложи выбранные листы аккуратными рядами.'),
                              ('Разбор скетча', 'Посмотри мой скетч и предложи, что улучшить. Пока ничего не меняй.')]:
            quick.addWidget(button(title, lambda checked=False, text=prompt: self.draft(text), 'Подставить запрос в поле ввода'))
        main.insertWidget(4, self.suggestions)
        self.attach_view = QCheckBox('Показывать выбранный скетч помощнику')
        self.attach_view.setChecked(True)
        self.attach_view.setToolTip('Для обсуждения отправляется уменьшенный снимок выбранного листа. Генерация всегда использует полный исходник.')
        options.addWidget(self.attach_view)
        self.pending_actions = None
        self.after_worker = None
        self.agent_observations = []
        self.agent_round = 0
        self.agent_active = False
        self.action_review = QWidget()
        review = QHBoxLayout(self.action_review)
        review.setContentsMargins(0, 0, 0, 0)
        review.addWidget(button('Применить команды', self.apply_pending))
        review.addWidget(button('Отменить', self.discard_pending))
        main.insertWidget(main.count()-1, self.action_review)
        self.action_review.hide()
        self.channel.currentIndexChanged.connect(self.channel_changed)
        self.send.clicked.disconnect()
        self.send.clicked.connect(self.send_message)
        self.channel_changed()

    def draft(self, text):
        self.channel.setCurrentIndex(0)
        self.composer.setPlainText(text)
        self.composer.setFocus()

    def toggle_options(self, checked):
        self.options_scroll.setVisible(checked)

    def channel_changed(self):
        image = self.channel.currentData() == 'image'
        self.enhance.setVisible(image)
        self.prompt_hint.setVisible(image)
        self.suggestions.setVisible(not image)
        self.options_scroll.setVisible(image)
        self.options_scroll.setMinimumHeight(180 if image else 100)
        self.settings_toggle.setChecked(image)
        self.composer.setPlaceholderText('Что изменить в изображении? Enter — отправить, Shift+Enter — новая строка.' if image else
                                        'Спросите об инструменте, разберите скетч или поручите задачу…')
        self.update_send_label()

    def update_send_label(self):
        if hasattr(self, 'channel') and self.channel.currentData() == 'chat':
            self.send.setText('↑  Отправить')
        else:
            self.send.setText('✦  Перерисовать область' if self.mode.currentData() == 'inpaint' else '✦  Создать изображение')

    def render_message(self, who, text, result=None):
        user = who == 'Вы'
        color, background = ('#eceee8', '#303a32') if user else ('#dce3db', '#1b211f')
        body = html.escape(text).replace('\n', '<br>')
        if result:
            path = Path(result).resolve()
            roots = (self.data.resolve(), backend.ROOT/'qa', backend.ROOT/'data')
            if any(path.is_relative_to(root) for root in roots) and path.is_file():
                image = QImage(str(path))
                url = QUrl.fromLocalFile(str(path))
                if not image.isNull():
                    thumb = image.scaled(340, 240, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    self.chat.document().addResource(QTextDocument.ResourceType.ImageResource, url, thumb)
                    body += f'<br><br><a href="{html.escape(url.toString(), quote=True)}"><img src="{html.escape(url.toString(), quote=True)}" width="{thumb.width()}" height="{thumb.height()}"></a>'
                body += f'<br><a style="color:#dbc69b" href="{html.escape(url.toString(), quote=True)}">Открыть изображение</a>'
        align = 'right' if user else 'left'
        self.chat.append(f'<table width="100%" cellspacing="0" cellpadding="10" bgcolor="{background}"><tr><td>'
                         f'<p align="{align}" style="color:#bda678; font-size:11px"><b>{html.escape(who)}</b></p>'
                         f'<p style="color:{color}; line-height:135%">{body}</p></td></tr></table><p style="font-size:5px"> </p>')
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    def conversation_menu(self):
        menu = QMenu(self)
        menu.addAction('Новый чат', self.new_conversation).setEnabled(not self.busy and not self.pending_actions)
        history = menu.addMenu('История чатов')
        for path in sorted((self.data/'conversations').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
            try:
                messages = json.loads(path.read_text(encoding='utf8'))
                title = next((m['text'].split('\n')[0][:55] for m in messages if m.get('who') == 'Вы'), 'Чат')
                action = history.addAction(title, lambda checked=False, p=path: self.load_conversation(p))
                action.setEnabled(not self.busy and not self.pending_actions)
            except (ValueError, OSError, KeyError):
                pass
        menu.exec(self.mapToGlobal(self.rect().topRight()))

    def archive_conversation(self):
        if not self.messages:
            return
        directory = self.data/'conversations'
        directory.mkdir(parents=True, exist_ok=True)
        (directory/(uuid.uuid4().hex+'.json')).write_text(json.dumps(self.messages, ensure_ascii=False), encoding='utf8')

    def new_conversation(self):
        self.archive_conversation()
        self.messages = []
        self.chat.clear()
        self.composer.clear()
        self.persist_chat()
        self.say('Помощник', 'Я могу объяснить инструменты, обсудить рисунок или помочь организовать столы и листы. Что сделаем?')

    def load_conversation(self, path):
        try:
            messages = json.loads(path.read_text(encoding='utf8'))
            if not isinstance(messages, list): return
            self.archive_conversation()
            self.messages = messages
            self.chat.clear()
            for record in messages: self.append_message(record['who'], record['text'], record.get('result'))
            self.persist_chat()
        except (ValueError, KeyError, OSError):
            self.say('Помощник', 'Не удалось прочитать этот чат.')

    def send_message(self):
        if self.busy or self.pending_actions:
            return
        if self.channel.currentData() == 'image':
            self.start_generation()
            return
        text = self.composer.toPlainText()
        if not text.strip(): return
        if len(text) > 8000:
            self.say('Помощник', 'Сократите сообщение до 8000 символов.')
            return
        self.agent_directory = (self.data/'assistant-chat'/uuid.uuid4().hex).resolve()
        self.agent_directory.mkdir(parents=True)
        self.agent_image = None
        paper = self.window.board.selected
        if self.attach_view.isChecked() and isinstance(paper, Paper):
            image = composite_paper(self.window.board.doc, paper)
            image = image.scaled(1280, 1280, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.agent_image = self.agent_directory/'view.png'
            self.agent_image.write_bytes(png_bytes(image))
        self.say('Вы', text, self.agent_image)
        self.agent_user_text = text
        self.agent_observations, self.agent_round = [], 0
        self.agent_active = True
        self.agent_step()

    def agent_step(self):
        self.agent_stamp = agent_tools.state_stamp(self.window)
        if self.agent_round and self.attach_view.isChecked() and isinstance(self.window.board.selected, Paper):
            image = composite_paper(self.window.board.doc, self.window.board.selected)
            image = image.scaled(1280, 1280, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.agent_image = self.agent_directory/('view-'+str(self.agent_round)+'.png')
            self.agent_image.write_bytes(png_bytes(image))
        payload = {'state': agent_tools.snapshot(self.window),
                   'conversation': [{'role': 'user' if m['who'] == 'Вы' else 'assistant', 'text': m['text']} for m in self.messages[-40:]],
                   'observations': self.agent_observations,
                   'manual': (backend.ROOT/'README.md').read_text(encoding='utf8')[-24000:]}
        provider = 'api' if self.provider.currentData() == 'api' else 'subscription'
        key = self.api_key.text().strip() or __import__('os').environ.get('OPENAI_API_KEY', '')
        model, codex_path = self.text_model.text().strip(), self.codex.text().strip()
        directory = self.agent_directory/str(self.agent_round)
        image = self.agent_image
        self.status.setText('Помощник обдумывает запрос…')
        self.start_worker(lambda cancel, progress: agent_backend.ask(directory, payload, cancel, provider, key, model, codex_path, image), self.agent_result)

    def agent_result(self, response):
        actions = response['actions']
        unknown = [a['tool'] for a in actions if a['tool'] not in agent_tools.TOOLS]
        if unknown or any(a['tool'] == 'assistant.generate' for a in actions[:-1]):
            self.agent_active = False
            self.say('Помощник', 'Получена неподдерживаемая последовательность команд. Изменения не применены.')
            return
        if response['reply']:
            self.say('Помощник', response['reply'])
        if not actions:
            self.agent_active = False
            self.status.setText('Готово')
            return
        changed = self.agent_stamp != agent_tools.state_stamp(self.window)
        destructive = any(a['tool'] in agent_tools.DESTRUCTIVE for a in actions)
        if changed or destructive:
            self.pending_actions = actions
            self.pending_stamp = agent_tools.state_stamp(self.window)
            self.agent_active = False
            self.action_review.show()
            self.send.setEnabled(False)
            self.say('Помощник', ('Состояние стола изменилось, пока я отвечал. ' if changed else '')+
                     'Проверьте команды перед применением:\n'+self.describe_actions(actions))
            return
        self.run_actions(actions)

    def describe_actions(self, actions):
        titles = {'desk.new': 'Создать стол', 'desk.select': 'Переключить стол', 'desk.rename': 'Переименовать стол',
            'desk.reference': 'Открыть плавающее окно референсов', 'desk.close': 'Закрыть стол',
            'sheet.add': 'Добавить лист', 'sheet.select': 'Выбрать листы', 'sheet.update': 'Изменить свойства листа',
            'sheet.arrange': 'Разложить выбранные листы', 'sheet.duplicate': 'Создать копии листов',
            'sheet.copy': 'Скопировать листы', 'sheet.paste': 'Вставить из буфера', 'sheet.tracing': 'Добавить кальку',
            'sheet.delete': 'Удалить выбранные листы', 'sheet.clear': 'Очистить выбранный лист',
            'sheet.order': 'Изменить порядок листов', 'sheet.import': 'Выбрать изображения для импорта',
            'folder.create': 'Создать папку', 'folder.move': 'Переместить в папку', 'folder.rename': 'Переименовать папку',
            'folder.ungroup': 'Раскрыть содержимое папки', 'brush.set': 'Настроить кисть', 'brush.reset': 'Сбросить кисть',
            'tool.select': 'Переключить инструмент', 'view.set': 'Настроить вид', 'grid.set': 'Настроить объёмную сетку',
            'guide.add': 'Добавить Alt-слой', 'guide.select': 'Выбрать Alt-слой', 'guide.update': 'Настроить Alt-слой',
            'guide.delete': 'Удалить Alt-слой', 'pin.add': 'Добавить пин', 'pin.move': 'Переместить пин',
            'pin.delete': 'Удалить пин с его резинками', 'pin.connect': 'Соединить пины', 'band.delete': 'Удалить резинку',
            'guides.set': 'Настроить направляющие', 'inpaint.set': 'Настроить выделение', 'inpaint.clear': 'Убрать выделение',
            'history.undo': 'Отменить последнее изменение', 'history.redo': 'Вернуть изменение', 'file.save': 'Сохранить стол',
            'file.open': 'Открыть проект', 'file.export': 'Экспортировать PNG', 'assistant.generate': 'Создать изображение',
            'ui.panel': 'Открыть панель инструментов'}
        titles.update({'inpaint.rectangle': 'Выделить область для правки', 'draw.stroke': 'Нарисовать штрих'})
        lines = []
        for action in actions:
            title, args = titles.get(action['tool'], 'Команда'), action['arguments']
            if action['tool'] == 'sheet.clear' and args.get('guide'):
                title = 'Очистить только текущий Alt-слой'
            detail = args.get('name') or args.get('preset') or args.get('prompt') or ''
            if action['tool'] in ('sheet.delete', 'sheet.clear', 'guide.delete'):
                detail = ', '.join(p.name for p in self.window.board.selected_papers())
            lines.append('• '+title+(' — '+str(detail)[:180] if detail else ''))
        return '\n'.join(lines)

    def apply_pending(self):
        if self.busy or not self.pending_actions: return
        if self.pending_stamp != agent_tools.state_stamp(self.window):
            self.discard_pending()
            self.say('Помощник', 'Выбор или содержимое стола снова изменились. Отправьте запрос заново, чтобы команды относились к актуальным листам.')
            return
        actions, self.pending_actions = self.pending_actions, None
        self.action_review.hide()
        self.run_actions(actions, True)
        if self.after_worker:
            callback, self.after_worker = self.after_worker, None
            QTimer.singleShot(0, callback)

    def discard_pending(self):
        self.pending_actions = None
        self.action_review.hide()
        self.send.setEnabled(not self.busy)
        self.status.setText('Готов к новому сообщению')
        self.say('Помощник', 'Команды отменены, изменения не применены.')

    def run_actions(self, actions, confirmed=False):
        generation = None
        results = []
        for action in actions:
            try:
                result = agent_tools.execute(self.window, action['tool'], action['arguments'], confirmed)
                results.append({'tool': action['tool'], 'result': result})
                if 'generation' in result:
                    generation = result['generation']
            except Exception as exc:
                results.append({'tool': action['tool'], 'error': str(exc)})
                self.say('Помощник', 'Не удалось выполнить: '+self.describe_actions([action])[2:]+'. '+str(exc)+
                         '\nПредыдущие выполненные изменения можно отменить через Ctrl+Z.')
                self.agent_active = False
                return
        self.agent_observations.extend(results)
        self.say('Действия Atelier', self.describe_actions(actions))
        if generation:
            self.agent_active = False
            if not self.enhance.isChecked() and hasattr(self, 'agent_user_text'):
                generation['prompt'] = self.agent_user_text
            def start_image():
                self.channel.setCurrentIndex(1)
                self.composer.setPlainText(generation['prompt'])
                self.mode.setCurrentIndex(self.mode.findData(generation['mode']))
                self.start_generation()
            self.after_worker = start_image
        elif self.agent_round < 3:
            self.agent_round += 1
            self.after_worker = self.agent_step
        else:
            self.agent_active = False
            self.say('Помощник', 'Команды выполнены. Можно продолжить следующим сообщением.')

    def setup_higgsfield(self, options):
        self.higgs_controls = QWidget()
        layout = QVBoxLayout(self.higgs_controls)
        layout.setContentsMargins(0, 0, 0, 0)
        self.higgs_model = QComboBox()
        for model in higgs.catalog(): self.higgs_model.addItem(model['name'], model['id'])
        self.higgs_aspect = QComboBox()
        self.higgs_params = QWidget()
        self.higgs_params_layout = QVBoxLayout(self.higgs_params)
        self.higgs_params_layout.setContentsMargins(0, 0, 0, 0)
        self.higgs_param_widgets = {}
        layout.addWidget(label('Модель Higgsfield', 'muted'))
        layout.addWidget(self.higgs_model)
        layout.addWidget(label('Пропорции', 'muted'))
        layout.addWidget(self.higgs_aspect)
        layout.addWidget(self.higgs_params)
        self.higgs_auto_hint = label('', 'muted')
        self.higgs_auto_hint.setWordWrap(True)
        layout.addWidget(self.higgs_auto_hint)
        options.addWidget(self.higgs_controls)
        self.higgs_connection = QWidget()
        row = QVBoxLayout(self.higgs_connection)
        row.setContentsMargins(0, 0, 0, 0)
        buttons = QHBoxLayout()
        buttons.addWidget(button('Войти в Higgsfield', lambda: self.connect_higgsfield(True)))
        buttons.addWidget(button('Проверить', lambda: self.connect_higgsfield(False)))
        row.addLayout(buttons)
        self.higgs_retrieve = button('Получить результат последнего задания', self.retrieve_higgsfield,
                                     'Проверить уже отправленное задание. Новая генерация не запускается.')
        row.addWidget(self.higgs_retrieve)
        self.higgs_workspace = QComboBox()
        self.higgs_workspace.addItem('Выберите пространство после входа', '')
        row.addWidget(self.higgs_workspace)
        note = label('Вход через официальный Higgsfield. Подключение MCP в чате не передаёт вход в Atelier. Для переписки используется ChatGPT / OpenAI.', 'muted')
        note.setWordWrap(True)
        row.addWidget(note)
        self.connection.content.addWidget(self.higgs_connection)
        self.higgs_model.currentIndexChanged.connect(self.higgs_model_changed)
        self.higgs_aspect.currentIndexChanged.connect(self.higgs_save_settings)
        self.higgs_workspace.currentIndexChanged.connect(self.higgs_save_settings)
        self.higgs_model_changed()
        try:
            saved = json.loads((self.data/'higgsfield-settings.json').read_text(encoding='utf8'))
            index = self.higgs_model.findData(saved.get('model'))
            if index >= 0: self.higgs_model.setCurrentIndex(index)
            index = self.higgs_aspect.findData(saved.get('aspect'))
            if index >= 0: self.higgs_aspect.setCurrentIndex(index)
            for key, value in saved.get('parameters', {}).items():
                widget = self.higgs_param_widgets.get(key)
                if widget and widget.findData(value) >= 0: widget.setCurrentIndex(widget.findData(value))
            if saved.get('workspace'):
                self.higgs_workspace.addItem(saved.get('workspace_name', 'Выбранное пространство'), saved['workspace'])
                self.higgs_workspace.setCurrentIndex(1)
        except (ValueError, OSError, TypeError):
            pass

    def higgs_model_changed(self):
        model = higgs.model_info(self.higgs_model.currentData())
        self.higgs_aspect.blockSignals(True)
        self.higgs_aspect.clear()
        self.higgs_aspect.addItem('Auto · как исходный лист', 'auto')
        for ratio in model['aspect_ratios']:
            if ratio != 'auto': self.higgs_aspect.addItem(ratio, ratio)
        self.higgs_aspect.blockSignals(False)
        while self.higgs_params_layout.count():
            item = self.higgs_params_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.higgs_param_widgets = {}
        labels = {'resolution': 'Разрешение', 'quality': 'Качество', 'background': 'Фон', 'variant': 'Версия модели', 'thinking_level': 'Проработка'}
        for parameter in model['parameters']:
            key, values = parameter['name'], parameter.get('options', [])
            if key not in labels or not values: continue
            self.higgs_params_layout.addWidget(label(labels[key], 'muted'))
            widget = QComboBox()
            for val in values: widget.addItem(str(val).upper() if key == 'resolution' else str(val), val)
            index = widget.findData(parameter.get('default'))
            if index >= 0: widget.setCurrentIndex(index)
            self.higgs_params_layout.addWidget(widget)
            self.higgs_param_widgets[key] = widget
            widget.currentIndexChanged.connect(self.higgs_save_settings)
        self.higgs_auto_hint.setText('Auto передаёт исходные пропорции по изображению.' if 'auto' in model['aspect_ratios'] else
                                    'У этой модели нет Auto: будет выбран ближайший поддерживаемый формат. Изображение впишется в лист без растяжения.')

    def higgs_settings(self):
        return {'model': self.higgs_model.currentData(), 'aspect': self.higgs_aspect.currentData(),
                'parameters': {key: widget.currentData() for key, widget in self.higgs_param_widgets.items()},
                'workspace': self.higgs_workspace.currentData(), 'workspace_name': self.higgs_workspace.currentText()}

    def higgs_save_settings(self):
        if not hasattr(self, 'higgs_workspace'): return
        try:
            self.data.mkdir(parents=True, exist_ok=True)
            (self.data/'higgsfield-settings.json').write_text(json.dumps(self.higgs_settings(), ensure_ascii=False), encoding='utf8')
        except OSError:
            pass

    def connect_higgsfield(self, login):
        if self.busy: return
        self.status.setText('Завершите вход Higgsfield в браузере…' if login else 'Проверяю Higgsfield…')
        self.start_worker(lambda cancel, progress: higgs.workspaces(cancel, login), self.higgs_connected)

    def higgs_connected(self, result):
        self.higgs_workspace.blockSignals(True)
        current = self.higgs_workspace.currentData()
        self.higgs_workspace.clear()
        for item in result['workspaces']: self.higgs_workspace.addItem(item['name'], item['id'])
        index = self.higgs_workspace.findData(current)
        if index >= 0: self.higgs_workspace.setCurrentIndex(index)
        self.higgs_workspace.blockSignals(False)
        self.higgs_save_settings()
        self.say('Помощник', result['message'])

    def retrieve_higgsfield(self):
        if self.busy: return
        try:
            from .assistant_panel import Source
            directories = sorted((self.data/'assistant').glob('*/higgsfield-job.json'), key=lambda p: p.stat().st_mtime, reverse=True)
            if not directories:
                raise ValueError('Нет сохранённых заданий Higgsfield в этом Atelier.')
            path = directories[0]
            job = json.loads(path.read_text(encoding='utf8'))
            if job.get('placed'):
                raise ValueError('Последний результат уже добавлен на лист. Новая генерация не запускалась.')
            metadata = json.loads(path.with_name('source.json').read_text(encoding='utf8'))
            board = next((b for b in self.window.boards if any(p.id == metadata['source_id'] for p in b.doc.papers)), None)
            if not board:
                raise ValueError('Откройте исходный стол, с которого запускалось это задание, и нажмите ещё раз.')
            mask = QImage(str(path.with_name('selection.png'))) if metadata.get('mode') == 'inpaint' else None
            if mask is not None and mask.isNull():
                raise ValueError('Не найдена сохранённая область Inpaint.')
            self.source = Source(board, board.doc, metadata['source_id'], metadata['source_name'], 'Higgsfield',
                                 mask, metadata.get('feather', 40), metadata.get('harmonize', True), metadata.get('crop'))
            self.start_worker(lambda cancel, progress: higgs.retrieve(path.parent, cancel, progress), self.generation_result)
        except (ValueError, OSError, KeyError) as exc:
            self.say('Помощник', str(exc))
