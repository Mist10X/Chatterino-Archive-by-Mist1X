"""Any-user card with exact lookup, unified actions, messages and bans."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLineEdit,QSplitter,QHeaderView,QSizePolicy
from ui_controls import ChannelCombo
from theme import Avatar,box,card,label,Button,combo,table,scroll,update_combo
from stable_tree import reconcile,set_texts
from query_state import QueryState
from storage import name
from emote_widgets import EmoteBrowser
from user_history import event_blocks,event_table_summary,event_kind_label
from moderation_view import stamp
from responsive import EventCells,event_color,summary_color

class UserView:
    def __init__(self,app):
        self.app=app;self.user='';self.mode='history';self.offset=0;self.total=0;self.rows=[];self.query=QueryState(0)
        body,layout=box(margins=8)
        lookup,ll=box(False);self.nick=QLineEdit();self.nick.setPlaceholderText('Введи любой ник Twitch…');ll.addWidget(self.nick,1)
        ll.addWidget(Button('Открыть карточку',app.theme,self.open_typed,primary=True,icon='users'));layout.addWidget(lookup)
        heading,hl=card();identity,il=box(False);self.avatar=Avatar();il.addWidget(self.avatar);self.title=label('Карточка пользователя',18,bold=True);il.addWidget(self.title,1);hl.addWidget(identity)
        self.counts=label('Введи ник: отслеживание пользователя не обязательно.',10,True);hl.addWidget(self.counts);layout.addWidget(heading)
        tabs,tl=box(False);self.buttons=[]
        for mode,text in [('history','История действий'),('messages','Сообщения'),('bans','Все баны')]:
            b=Button(text,app.theme,lambda mode=mode:self.select(mode));tl.addWidget(b);self.buttons.append((mode,b))
        layout.addWidget(tabs)
        filters,fl=box(False);self.channel=ChannelCombo(['Все каналы']);fl.addWidget(self.channel)
        self.search=QLineEdit();self.search.setPlaceholderText('Поиск по тексту…');fl.addWidget(self.search,1);layout.addWidget(filters)
        actions,al=box(False)
        self.refresh=Button('Обновить баны Chatterino+',app.theme,lambda:self.fetch_shared(True),icon='shield');al.addWidget(self.refresh,1)
        from shared_bans_view import open_shared
        al.addWidget(Button('Подключение',app.theme,lambda:open_shared(app),icon='play'));layout.addWidget(actions)
        self.network_status=label('Сначала показаны сохранённые данные. Общие баны загружаются для открытого ника.',9,True);layout.addWidget(self.network_status)
        split=QSplitter(Qt.Vertical);split.setChildrenCollapsible(False);split.setHandleWidth(8)
        self.tree=table(['Время / канал','Событие','Подробности'],[170,100,330]);self.tree.setUniformRowHeights(False)
        self.tree.header().setStretchLastSection(False);self.tree.header().setSectionResizeMode(2,QHeaderView.Stretch)
        self.tree.setWordWrap(True);self.tree.setItemDelegate(EventCells(self.tree,1,2,origin_line=True));self.tree.setMinimumWidth(0);self.tree.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Expanding)
        split.addWidget(self.tree);self.preview=EmoteBrowser(app.emotes);split.addWidget(self.preview);split.setSizes([320,240]);layout.addWidget(split,1)
        nav,nl=box(False);self.counter=label('',9,True);nl.addWidget(self.counter,1)
        self.prev=Button('Новее',app.theme,lambda:self.page(-1),icon='left');self.next=Button('Старее',app.theme,lambda:self.page(1),icon='right');nl.addWidget(self.prev);nl.addWidget(self.next);layout.addWidget(nav)
        self.network_status.setToolTip('Доступна история, которую сохранил архив или передал Chatterino+. Полнота данных сервиса не гарантирована.')
        self.frame=scroll(body);self.tree.itemSelectionChanged.connect(self.show)
        self.channel.currentTextChanged.connect(self.reload)
        self.search_timer=QTimer(app);self.search_timer.setSingleShot(True);self.search_timer.setInterval(350);self.search_timer.timeout.connect(self.reload)
        self.search.textChanged.connect(lambda:self.search_timer.start());self.nick.returnPressed.connect(self.open_typed)
        app.shared_result.connect(self.shared_event);self.select('history')
    def open_typed(self):
        try:self.open_user(self.nick.text())
        except ValueError:self.counter.setText('Введи корректный ник Twitch.')
    def open_user(self,user):
        user=name(user)
        if not user:raise ValueError('Empty user')
        self.user=user;self.nick.setText(user);self.title.setText('@'+self.app.profiles.display(user));self.avatar.set_profile(self.app.profiles,user);self.offset=0
        for widget in (self.channel,self.search):widget.blockSignals(True)
        self.channel.setCurrentIndex(0);self.search.clear()
        for widget in (self.channel,self.search):widget.blockSignals(False)
        self.request();self.fetch_shared(False)
    def select(self,mode):
        self.mode=mode;self.offset=0
        for key,button in self.buttons:button.active=mode==key;button.update()
        self.request()
    def reload(self,*_):self.offset=0;self.request()
    def page(self,d):self.offset=max(0,self.offset+d*100);self.request()
    def request(self):
        if not self.user:return
        filters=(self.user,self.mode,'' if self.channel.currentIndex()==0 else self.channel.currentText(),self.search.text())
        if self.query.select((filters,self.offset)):
            self.tree.clear();self.preview.setPlainText('Загружаем историю…');self.counter.setText('Загрузка…');self.counts.setText('Подсчитываем доступные записи…')
        token=self.query.request()
        if token is not None:self.app.worker.tasks.put(('user_card',(token,filters,self.offset)))
    def render(self,token,result):
        if not self.query.finish(token):return
        self.rows=result['rows'];self.total=result['total'];counts=result['counts']
        self.counts.setText(f"Сообщения: {result['messages']} · Баны: {counts['bans']} · Муты: {counts['timeouts']} · Удаления: {counts['deletions']}")
        def update(item,row):
            text=(row.get('message') or {}).get('text','') if row['kind']=='message' else event_table_summary(row,400)
            kind=event_kind_label(row).replace(' · ','\n',1) if row['kind']=='timeout' else event_kind_label(row)
            detail=text[:400] or kind
            set_texts(item,[stamp(row['at_ms']).replace('  ','\n')+'\n#'+row['channel'],kind,detail+'\n'+row.get('origin','Наш архив')]);item.setData(0,Qt.UserRole,row)
            item.setForeground(1,QColor(event_color(row)));item.setForeground(2,QColor(summary_color(row,detail)))
        reconcile(self.tree,self.rows,lambda row:row['key'],update,follow_top=self.offset==0)
        update_combo(self.channel,['Все каналы']+result['channels'])
        self.counter.setText((f'{self.offset+1}–{self.offset+len(self.rows)} из {self.total}' if self.rows else 'Нет доступных записей')+(' · '+result['warning'] if result['warning'] else ''))
        self.prev.setEnabled(self.offset>0);self.next.setEnabled(self.offset+len(self.rows)<self.total)
        if self.tree.currentItem():self.show()
        elif self.rows:self.tree.setCurrentItem(self.tree.topLevelItem(0))
        else:self.preview.setPlainText('По этим условиям сохранённых данных нет. Можно обновить баны из Chatterino+.')
        if self.query.dirty:self.request()
    def show(self):
        item=self.tree.currentItem()
        if item:
            row=item.data(0,Qt.UserRole);self.app.request_ban_context(row);self.preview.set_blocks(event_blocks(row))
    def failed(self,token,error):
        if self.query.finish(token):self.counter.setText('Не удалось обновить: '+error)
    def fetch_shared(self,force=False):
        if not self.user:self.counter.setText('Сначала открой карточку пользователя.');return
        if not self.app.network:self.network_status.setText('В этом режиме доступны сохранённые данные.');return
        if self.app.shared.command('fetch',(self.user,force)):self.network_status.setText('Получаем общие баны @'+self.user+'…')
        else:self.network_status.setText('Ещё выполняется предыдущий запрос. Нажми «Обновить баны» после его завершения.')
    def shared_event(self,kind,value):
        if kind=='card' and value['user']==self.user:
            self.network_status.setText('Карточка Chatterino+ получена. Записи объединяются с нашим архивом.');self.request()
        elif kind=='recovery_status':self.network_status.setText(value)
        elif kind in ('error','auth_required'):self.network_status.setText(value)
        elif kind=='account':self.network_status.setText('Chatterino+ подключён. Можно обновить баны.' if value else 'Для новых данных Chatterino+ открой «Подключение».')
