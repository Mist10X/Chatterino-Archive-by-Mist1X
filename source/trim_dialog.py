"""Explicitly preview a snapshot of open channels before trimming their histories."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget,QListWidgetItem,QTextBrowser,QCheckBox
from storage import name
from theme import Dialog,Button,box,label

def open_channels(state):
    if not state.get('fresh'):raise ValueError('Запусти Chatterino и дождись подключения плагина.')
    if not state.get('trim_except') or state.get('discovery')!='open_tabs':
        raise ValueError('Для этой очистки нужен обновлённый плагин с доступом к открытым вкладкам.')
    return sorted({name(c['name']) for c in state.get('channels',[]) if c.get('open')})

class TrimDialog(Dialog):
    def __init__(self,app):
        super().__init__(app,'Очистить историю чатов',
            'Отметь каналы, где нужно сохранять всю историю Chatterino. Остальные открытые каналы можно очищать вручную или автоматически раз в час. Постоянный архив на диске не очищается.')
        self.app=app;self.channels=open_channels(app.control.status());self.setMinimumWidth(570)
        self.saved=set()
        for value in app.control.trim_preserved:
            try:self.saved.add(name(value))
            except (ValueError,AttributeError):pass
        self.auto=QCheckBox('Каждый час оставлять в остальных каналах только 1000 последних сообщений')
        self.auto.setChecked(app.control.auto_trim);self.layout.addWidget(self.auto)
        self.layout.addWidget(label('Галочка = сохранить историю полностью',11,bold=True))
        self.choices=QListWidget();self.choices.setAccessibleName('Каналы, которые не нужно очищать');self.choices.setMinimumHeight(130);self.choices.setMaximumHeight(240)
        for channel in sorted(set(self.channels)|self.saved):
            item=QListWidgetItem('#'+channel+(' · сейчас не открыт' if channel not in self.channels else ''))
            item.setData(Qt.UserRole,channel);item.setFlags(item.flags()|Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if channel in self.saved else Qt.Unchecked);self.choices.addItem(item)
        self.layout.addWidget(self.choices)
        self.summary=QTextBrowser();self.summary.setAccessibleName('Предварительный список очистки');self.summary.setMinimumHeight(100);self.summary.setMaximumHeight(150);self.layout.addWidget(self.summary)
        actions,layout=box(False)
        layout.addWidget(Button('Отмена',app.theme,self.reject))
        layout.addWidget(Button('Сохранить исключения',app.theme,lambda:app.safe(self.save_only)))
        self.confirm=Button('Очистить остальные',app.theme,lambda:app.safe(self.submit),primary=True);layout.addWidget(self.confirm)
        self.layout.addWidget(self.error);self.layout.addWidget(actions)
        self.choices.itemChanged.connect(self.refresh);self.auto.toggled.connect(self.refresh);self.refresh()
    def excluded(self):
        return {self.choices.item(i).data(Qt.UserRole) for i in range(self.choices.count()) if self.choices.item(i).checkState()==Qt.Checked}
    def refresh(self):
        preserved=set(self.channels)&self.excluded();targets=set(self.channels)-preserved
        automatic='включена · раз в час' if self.auto.isChecked() else 'выключена'
        self.summary.setPlainText('Сохранить всю историю: '+(', '.join('#'+x for x in sorted(self.excluded())) or 'нет')+
            '\n\nОставить до 1000 сообщений: '+(', '.join('#'+x for x in sorted(targets)) or 'нет — все открытые каналы исключены')+
            '\n\nАвтоматическая очистка: '+automatic)
        self.confirm.setEnabled(bool(targets));self.confirm.setText(f'Очистить остальные ({len(targets)})')
    def save(self):
        previous=(self.app.control.auto_trim,set(self.app.control.trim_preserved))
        self.app.control.auto_trim=self.auto.isChecked();self.app.control.trim_preserved=set(self.excluded())
        try:self.app.control.save()
        except Exception:
            self.app.control.auto_trim,self.app.control.trim_preserved=previous;raise
    def save_only(self):
        self.save();self.app.detail.setText('Настройки автоматической очистки сохранены. История чатов сейчас не очищалась.');self.accept()
    def submit(self):
        current=open_channels(self.app.control.status())
        if current!=self.channels:
            raise ValueError('Список открытых каналов изменился. Закрой это окно и открой очистку заново.')
        self.save()
        self.app.control.request_trim_except(self.channels,set(self.channels)&self.excluded())
        self.app.detail.setText('Очистка отправлена в Chatterino. Ожидается результат…');self.accept()
