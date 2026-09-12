"""Twitch connection and independent channel selection, in the archive's theme."""
from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QPlainTextEdit
from storage import name, layout_channels
from twitch_core import settings_save
from theme import Dialog, Button, label, box, scroll


class TwitchDialog(Dialog):
    def __init__(self, app):
        super().__init__(app, 'Подключение Twitch', 'Запись сообщений напрямую, даже когда Chatterino закрыт. Личные сообщения — по списку отслеживания. Отдельные удаления — у всех пользователей выбранных каналов.')
        self.app = app;self.open_requested = False;self.last_opened = '';self.resize(590, 710)
        outer = self.layout
        content, self.layout = box()
        self.body = scroll(content);outer.addWidget(self.body, 1)
        self.live = label('', 10, True);self.layout.addWidget(self.live)
        row, buttons = box(False)
        self.connect_button = Button('Подключить Twitch', app.theme, self.connect_account, primary=True)
        self.disconnect_button = Button('Отключить аккаунт', app.theme, lambda: app.twitch.command('disconnect'), danger=True)
        buttons.addWidget(self.connect_button);buttons.addWidget(self.disconnect_button);self.layout.addWidget(row)
        self.code = label('', 13, bold=True);self.code.setTextInteractionFlags(Qt.TextSelectableByMouse);self.layout.addWidget(self.code)
        row, buttons = box(False)
        self.browser = Button('Открыть страницу входа', app.theme, self.open_browser)
        self.cancel_auth = Button('Отменить вход', app.theme, lambda: app.twitch.command('cancel_auth'))
        buttons.addWidget(self.browser);buttons.addWidget(self.cancel_auth);self.layout.addWidget(row)
        self.enabled = QCheckBox('Записывать сообщения напрямую из Twitch');self.enabled.setChecked(app.twitch_config['enabled']);self.layout.addWidget(self.enabled)
        self.layout.addWidget(label('Каналы — по одному в строке. Закрытие вкладки Chatterino не убирает канал из этого списка.', 10, True))
        self.channels = QPlainTextEdit();self.channels.setPlaceholderText('morphe_ya\ndangerlyoha');self.channels.setPlainText('\n'.join(app.twitch_config['channels']))
        self.channels.setMinimumHeight(150);self.layout.addWidget(self.channels, 1)
        self.layout.addWidget(Button('Добавить каналы из Chatterino', app.theme, lambda: app.safe(self.import_channels)))
        self.tray = QCheckBox('При закрытии окна оставлять архив в трее');self.tray.setChecked(app.twitch_config['tray']);self.layout.addWidget(self.tray)
        self.layout.addWidget(label('Запись работает, пока запущен архив и компьютер не спит. Пропуски за время отключения автоматически не восстанавливаются. История банов и мутов пока поступает через плагин Chatterino.', 9, True))
        self.layout = outer
        self.actions(app.theme, accept='Сохранить', callback=self.save)
        self.timer = QTimer(self);self.timer.setInterval(300);self.timer.timeout.connect(self.refresh);self.timer.start();self.refresh()
        self.finished.connect(lambda: self.timer.stop())

    def connect_account(self):
        self.open_requested = True;self.app.twitch.command('auth')

    def open_browser(self):
        url = self.app.twitch.snapshot().get('auth_url')
        if url:QDesktopServices.openUrl(QUrl(url))

    def refresh(self):
        state = self.app.twitch.snapshot();self.live.setText(state['text'])
        pending = state['phase'] == 'auth';has_code = bool(state['auth_url'])
        self.connect_button.setEnabled(not pending);self.disconnect_button.setEnabled(bool(state['login']))
        self.browser.setVisible(has_code);self.cancel_auth.setVisible(pending);self.code.setVisible(has_code)
        self.code.setText('Код входа: ' + state['auth_code'])
        if has_code and self.open_requested and self.last_opened != state['auth_code']:
            self.last_opened = state['auth_code'];self.open_requested = False;self.open_browser()

    def import_channels(self):
        current = {name(ch) for ch in self.channels.toPlainText().splitlines() if ch.strip()}
        current.update(layout_channels(self.app.profile))
        status = self.app.control.status()
        if status.get('fresh'):current.update(c['name'] for c in status.get('channels', []) if c.get('open'))
        self.channels.setPlainText('\n'.join(sorted(current)))

    def save(self):
        try:
            value = settings_save(self.app.data / 'twitch', {'enabled': self.enabled.isChecked(), 'tray': self.tray.isChecked(),
                'channels': [ch for ch in self.channels.toPlainText().splitlines() if ch.strip()]})
            self.app.twitch_config = value;self.app.twitch.command('config', value);self.app.import_channels();self.accept()
        except (ValueError, OSError) as exc:self.error.setText(str(exc))
