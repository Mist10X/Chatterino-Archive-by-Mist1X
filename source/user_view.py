"""Any-user card with exact lookup, unified actions, messages and bans."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QLineEdit,QSplitter,QHeaderView,QSizePolicy
from theme import box,card,label,Button,combo,table,scroll,update_combo
from stable_tree import reconcile,set_texts
from query_state import QueryState
from storage import name
from emote_widgets import EmoteBrowser
from user_history import event_blocks,event_table_summary
from moderation_view import stamp
from responsive import WrappedCells

LABELS={'ban':'Бан','timeout':'Мут','delete':'Удаление','message':'Сообщение','unban':'Разбан','untimeout':'Снят мут','speech':'Снова пишет'}
class UserView:
    def __init__(self,app):
        self.app=app;self.user='';self.mode='history';self.offset=0;self.total=0;self.rows=[];self.query=QueryState(0)
        body,layout=box(margins=8)
        lookup,ll=box(False);self.nick=QLineEdit();self.nick.setPlaceholderText('Введи любой ник Twitch…');ll.addWidget(self.nick,1)
        ll.addWidget(Button('Открыть карточку',app.theme,self.open_typed,primary=True,icon='users'));layout.addWidget(lookup)
        heading,hl=card();self.title=label('Карточка пользователя',18,bold=True);hl.addWidget(self.title)
        self.counts=label('Введи ник: отслеживание пользователя не обязательно.',10,True);hl.addWidget(self.counts);layout.addWidget(heading)
        tabs,tl=box(False);self.buttons=[]
        for mode,text in [('history','История действий'),('messages','Сообщения'),('bans','Все баны')]:
            b=Button(text,app.theme,lambda mode=mode:self.select(mode));tl.addWidget(b);self.buttons.append((mode,b))
        layout.addWidget(tabs)
        filters,fl=box(False);self.channel=combo(['Все каналы']);fl.addWidget(self.channel)
        self.search=QLineEdit();self.search.setPlaceholderText('Поиск по тексту…');fl.addWidget(self.search,1);layout.addWidget(filters)
        actions,al=box(False)
        self.refresh=Button('Обновить баны Chatterino+',app.theme,lambda:self.fetch_shared(True),icon='shield');al.addWidget(self.refresh,1)
        from shared_bans_view import open_shared
        al.addWidget(Button('Подключение',app.theme,lambda:open_shared(app),icon='play'));layout.addWidget(actions)
        self.network_status=label('Сначала показаны сохранённые данные. Общие баны загружаются для открытого ника.',9,True);layout.addWidget(self.network_status)
        split=QSplitter(Qt.Vertical);split.setChildrenCollapsible(False);split.setHandleWidth(8)
        self.tree=table(['Время / канал','Событие','Подробности'],[170,100,330]);self.tree.setUniformRowHeights(False)
        self.tree.header().setStretchLastSection(False);self.tree.header().setSectionResizeMode(2,QHeaderView.Stretch)
        self.tree.setWordWrap(True);self.tree.setItemDelegate(WrappedCells(self.tree));self.tree.setMinimumWidth(0);self.tree.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Expanding)
        split.addWidget(self.tree);self.preview=EmoteBrowser(app.emotes);split.addWidget(self.preview);split.setSizes([320,240]);layout.addWidget(split,1)
        nav,nl=box(False);self.counter=label('',9,True);nl.addWidget(self.counter,1)
        self.prev=Button('Новее',app.theme,lambda:self.page(-1),icon='left');self.next=Button('Старее',app.theme,lambda:self.page(1),icon='right');nl.addWidget(self.prev);nl.addWidget(self.next);layout.addWidget(nav)
        layout.addWidget(label('Доступна история, которую сохранил архив или передал Chatterino+. Полнота данных сервиса не гарантирована.',9,True))
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
        self.user=user;self.nick.setText(user);self.title.setText('@'+user);self.offset=0
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
            kind=LABELS[row['kind']]
            if row['kind']=='timeout' and row.get('duration') is not None:kind+=f"\n{row['duration']} с"
            set_texts(item,[stamp(row['at_ms']).replace('  ','\n')+'\n#'+row['channel'],kind,(text[:400] or kind)+'\n'+row.get('origin','Наш архив')]);item.setData(0,Qt.UserRole,row)
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
