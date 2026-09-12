"""Separate source-labelled shared-ban card; local punishment counts stay intact."""
import datetime as dt,queue
from PySide6.QtCore import Qt,QTimer,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLineEdit,QSplitter,QTextBrowser,QHeaderView
from theme import box,label,Button,combo,table,update_combo
from stable_tree import reconcile,set_texts
from storage import name

def stamp(at):
    try:return dt.datetime.fromtimestamp(at).strftime('%d.%m.%Y %H:%M:%S')
    except (ValueError,OSError,TypeError):return 'Время неизвестно'

class SharedBansDialog(QDialog):
    def __init__(self,app,user=''):
        super().__init__(app);self.app=app;self.service=app.shared;self.card=None;self.requested='';self.auth_url=''
        self.setAttribute(Qt.WA_DeleteOnClose);self.setWindowTitle('Общие баны Chatterino+');self.resize(850,720);self.setMinimumSize(620,520)
        area=app.screen().availableGeometry();self.move(area.x()+max(0,(area.width()-self.width())//2),area.y()+30)
        layout=QVBoxLayout(self);layout.setContentsMargins(16,16,16,16);layout.setSpacing(10)
        layout.addWidget(label('Общие баны Chatterino+',18,bold=True))
        layout.addWidget(label('Ответ сервиса Chatterino+. Загруженные баны также учитываются в общей модерации и карточке пользователя.',9,True))
        connection,cl=box(False);self.account=label('Вход не выполнен',9,True);cl.addWidget(self.account,1)
        self.connect=Button('Подключить',app.theme,self.connect_service);cl.addWidget(self.connect)
        self.disconnect=Button('Отключить',app.theme,lambda:self.service.command('disconnect'));cl.addWidget(self.disconnect);layout.addWidget(connection)
        search,sl=box(False);self.user=QLineEdit();self.user.setPlaceholderText('Ник пользователя Twitch');self.user.setText(user);sl.addWidget(self.user,1)
        self.find=Button('Показать баны',app.theme,lambda:self.fetch(False),primary=True);sl.addWidget(self.find)
        self.refresh=Button('Обновить',app.theme,lambda:self.fetch(True));sl.addWidget(self.refresh);layout.addWidget(search)
        self.channel=combo(['Все каналы']);layout.addWidget(self.channel)
        split=QSplitter(Qt.Vertical);split.setChildrenCollapsible(False)
        self.tree=table(['Канал','Дата бана','Сообщений'],[200,220,120]);split.addWidget(self.tree)
        self.preview=QTextBrowser();self.preview.setOpenLinks(False);self.preview.setOpenExternalLinks(False);self.preview.setMinimumHeight(140);split.addWidget(self.preview)
        split.setSizes([280,240]);layout.addWidget(split,1)
        self.updated=label('',9,True);layout.addWidget(self.updated)
        self.status=label('Выбери пользователя для запроса. Сохранённые карточки доступны без подключения.',9,True);layout.addWidget(self.status)
        layout.addWidget(Button('Закрыть',app.theme,self.close))
        self.user.returnPressed.connect(lambda:self.fetch(False));self.tree.itemSelectionChanged.connect(self.show_row)
        self.channel.currentTextChanged.connect(self.render)
        app.shared_result.connect(self.handle_result)
        self.timer=QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self.poll);self.timer.start()
        if self.service.account:self.account.setText('Подключён @'+self.service.account.get('login',''))
        if user:QTimer.singleShot(0,lambda:self.fetch(False))
    def connect_service(self):
        if self.service.command('auth'):
            self.status.setText('Открываем вход Twitch для сервиса Chatterino+…');self.connect.setEnabled(False)
    def fetch(self,force):
        try:user=name(self.user.text())
        except ValueError:self.status.setText('Введи корректный ник Twitch.');return
        if not user:self.status.setText('Введи ник Twitch.');return
        if self.service.command('fetch',(user,force)):
            if self.requested!=user:self.card=None;self.tree.clear();self.preview.clear();self.updated.clear()
            self.requested=user;self.status.setText('Получаем общие баны @'+user+'…')
    def poll(self):
        self.find.setEnabled(not self.service.pending);self.refresh.setEnabled(not self.service.pending)
    def handle_result(self,kind,value):
        if kind=='card':
            if value['user']==self.requested:
                self.card=value;update_combo(self.channel,['Все каналы']+sorted({r['channel'] for r in value['items']}));self.render()
                self.status.setText(value.get('note') or 'Карточка сохранена на компьютере. Сервис может учитывать не все баны и разбаны.')
        elif kind=='account':
            self.connect.setEnabled(True);self.auth_url='';self.account.setText('Подключён @'+value if value else 'Вход не выполнен')
            self.status.setText('Вход сохранён. Нажми «Показать баны».' if value else 'Вход отключён; сохранённые карточки остались.')
        elif kind=='auth_url':
            self.auth_url=value
            opened=QDesktopServices.openUrl(QUrl(value))
            self.status.setText('Подтверди вход на официальной странице Twitch.' if opened else 'Не удалось открыть браузер. Попробуй подключиться снова.')
        elif kind in ('error','auth_required','status'):
            self.status.setText(value);self.connect.setEnabled(True)
    def render(self,*_):
        if not self.card:return
        rows=[r for r in self.card['items'] if self.channel.currentIndex()==0 or r['channel']==self.channel.currentText()]
        def update(item,row):
            am=sum(m['automod'] for m in row['messages'])
            set_texts(item,['#'+row['channel'],stamp(row['since']),str(len(row['messages']))+(f' · AutoMod: {am}' if am else '')]);item.setData(0,Qt.UserRole,row)
        reconcile(self.tree,rows,lambda r:(r['channel_id'],r['since']),update)
        self.updated.setText(f"@{self.card['user']} · В ответе сервиса: {len(self.card['items'])} · Получено: {stamp(self.card['fetched_at'])}")
        if not rows:self.preview.setPlainText('В полученной карточке нет записей по этим условиям. Это не доказывает отсутствие банов на Twitch.')
        elif self.tree.currentItem():self.show_row()
        else:self.tree.setCurrentItem(self.tree.topLevelItem(0))
    def show_row(self):
        item=self.tree.currentItem()
        if not item:return
        row=item.data(0,Qt.UserRole)
        lines=[f"БАН · @{row['user']} · #{row['channel']}",stamp(row['since']),'Источник: общие баны Chatterino+','']
        for message in row['messages']:
            lines.extend([('AutoMod · ' if message['automod'] else '')+stamp(message['at']),message['text'],''])
        if not row['messages']:lines.append('Сервис не передал текст сообщений для этого бана.')
        self.preview.setPlainText('\n'.join(lines))
    def closeEvent(self,event):self.timer.stop();super().closeEvent(event)

def open_shared(app,user=''):
    current=getattr(app,'shared_window',None)
    if current is not None:
        current.show();current.raise_();current.activateWindow()
        if user:current.user.setText(user);current.fetch(False)
        return
    window=SharedBansDialog(app,user);app.shared_window=window
    window.destroyed.connect(lambda:setattr(app,'shared_window',None));window.show()
