"""Responsive archive pages; only presentation changes, no archive mutations."""
from PySide6.QtCore import QObject,QEvent,QTimer,Qt,QSize,QRect
from PySide6.QtWidgets import QDialog,QVBoxLayout,QSplitter,QHeaderView,QBoxLayout,QStyledItemDelegate,QStyleOptionViewItem,QSizePolicy,QStyle
from PySide6.QtGui import QPalette
from theme import box,Button,label

class WrappedCells(QStyledItemDelegate):
    def paint(self,painter,option,index):
        opt=QStyleOptionViewItem(option);self.initStyleOption(opt,index);text=opt.text;opt.text=''
        self.parent().style().drawControl(QStyle.CE_ItemViewItem,opt,painter,self.parent())
        painter.save();painter.setClipRect(option.rect);painter.setFont(opt.font)
        painter.setPen(opt.palette.color(QPalette.HighlightedText if opt.state & QStyle.State_Selected else QPalette.Text))
        painter.drawText(option.rect.adjusted(10,7,-10,-7),Qt.AlignVCenter|Qt.AlignLeft|Qt.TextWordWrap,text);painter.restore()
    def sizeHint(self,option,index):
        opt=QStyleOptionViewItem(option);self.initStyleOption(opt,index)
        width=max(50,self.parent().columnWidth(index.column())-24)
        rect=opt.fontMetrics.boundingRect(QRect(0,0,width,3000),Qt.TextWordWrap,opt.text)
        return QSize(width,max(42,rect.height()+18))

def format_moderation(view,item,row,kind):
    compact=getattr(view.app,'responsive',None)
    compact=compact is not None and compact.compact
    from moderation_view import stamp
    item.setText(0,stamp(row['at_ms']).replace('  ','\n') if compact else stamp(row['at_ms']))
    person=row['user'] or 'Автор неизвестен'
    item.setText(1,('#'+row['channel'] if view.user else person+'\n#'+row['channel']) if compact and view.channel.currentText()=='Все каналы' else person)
    item.setText(3,kind+('\n'+row.get('summary',row['status']) if compact else ''))

def format_message(app,item,row):
    from panel import local_time
    compact=getattr(app,'responsive',None)
    compact=compact is not None and compact.compact
    text=local_time(row['time_utc'])
    if compact:text=text.replace('  ','\n')+('\n#'+row['channel'] if app.channel.currentText()=='Все каналы' else '')
    item.setText(0,text)

def format_replay(view,item,meta):
    compact=getattr(view.app,'responsive',None)
    compact=compact is not None and compact.compact
    from replay_view import date_text
    item.setText(0,date_text(meta['started_at']).replace(' ','\n') if compact else date_text(meta['started_at']))
    item.setText(2,(('#'+meta['channel']+'\n') if compact else '')+meta['title'])
    item.setText(3,str(meta.get('messages',0))+(f"\n{meta['bytes']/1048576:.1f} МБ" if compact else ''))

class Responsive(QObject):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.compact=False;self.initial=True;self.dialog=None
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.refresh)
        app.installEventFilter(self)
        self.sidebars=[]
        for page,tree in ((app.pages.widget(0),app.users),(app.mod_view.frame,app.mod_view.people)):
            body=page.widget();body.setMinimumSize(0,0);body.layout().setContentsMargins(8,8,8,8)
            side=tree.parentWidget();right=body.layout().itemAt(1).widget()
            button=Button('Пользователи',app.theme,lambda side=side:self.open_users(side),icon='users')
            right.layout().insertWidget(0,button);button.hide()
            self.sidebars.append((side,body.layout(),button,side.width()))
            tree.itemClicked.connect(lambda *_:self.close_users())
            for action in side.findChildren(Button):
                if action.text() in ('Открыть карточку','Все пользователи'):
                    action.clicked.connect(lambda *_:self.close_users())
        app.users.parentWidget().setMinimumHeight(320);app.mod_view.people.parentWidget().setMinimumHeight(400)
        self.splitters=[]
        self.split(app.messages,app.prev.parentWidget(),app.copy.parentWidget(),app.preview)
        self.split(app.mod_view.tree,app.mod_view.prev.parentWidget(),app.mod_view.preview_caption,app.mod_view.preview)
        self.message_actions=app.toggle.parentWidget().layout()
        self.message_filters=app.channel.parentWidget().layout()
        app.day.setMinimumWidth(0);app.day.setMaximumWidth(16777215)
        self.replay_controls=app.replay_view.channel.parentWidget().layout()
        app.replay_view.frame.widget().setMinimumWidth(0)
        for tree in (app.mod_view.tree,app.replay_view.list):
            tree.setUniformRowHeights(False);tree.setWordWrap(True);tree.setItemDelegate(WrappedCells(tree))
        app.messages.setUniformRowHeights(False)
        app.messages.setItemDelegateForColumn(0,WrappedCells(app.messages))
        for tree in (app.messages,app.mod_view.tree,app.replay_view.list):
            tree.setMinimumWidth(0);tree.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Expanding)
            tree.viewport().installEventFilter(self)
        # Replace the tall toolbar only in compact mode; keep original buttons callable.
        tools=app.tool_buttons[0].parentWidget();parent=tools.parentWidget().layout()
        self.compact_tools,cl=box(False)
        self.quick_channels=Button('Каналы',app.theme,lambda:app.safe(app.channels_dialog),icon='users')
        self.quick_twitch=Button('Twitch',app.theme,app.twitch_dialog,icon='play')
        self.quick_trim=Button('Очистить чаты…',app.theme,lambda:app.safe(app.trim_dialog),icon='archive',primary=True)
        self.more=Button('Ещё',app.theme,self.open_menu)
        for b in (self.quick_channels,self.quick_twitch,self.quick_trim,self.more):cl.addWidget(b)
        parent.addWidget(self.compact_tools);self.compact_tools.hide();self.tools=tools
        self.heading_container=app.app_heading.parentWidget().parentWidget()
        self.subtitle=app.app_heading.parentWidget().layout().itemAt(1).widget()
        self.footer=app.status.parentWidget();footer_layout=self.footer.parentWidget().layout()
        self.short_footer,fl=box(False,margins=8);self.short_status=label('',9,True)
        fl.addWidget(self.short_status,1);fl.addWidget(Button('Подробнее',app.theme,self.show_status))
        footer_layout.addWidget(self.short_footer);self.short_footer.hide()
        self.status_timer=QTimer(self);self.status_timer.setInterval(1000);self.status_timer.timeout.connect(self.update_status);self.status_timer.start()
        self.refresh()
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Resize:self.timer.start(0)
        return False
    def split(self,tree,nav,caption,preview):
        layout=tree.parentWidget().layout();pos=layout.indexOf(tree)
        upper,ul=box();lower,ll=box()
        for widget,target in ((tree,ul),(nav,ul),(caption,ll),(preview,ll)):
            layout.removeWidget(widget);target.addWidget(widget,1 if widget in (tree,preview) else 0)
        splitter=QSplitter(Qt.Vertical);splitter.setHandleWidth(8);splitter.setChildrenCollapsible(False)
        splitter.setStyleSheet('QSplitter::handle {background:#34323f;border-radius:3px;} QSplitter::handle:hover {background:#a487c9;}')
        splitter.addWidget(upper);splitter.addWidget(lower);splitter.setStretchFactor(0,3);splitter.setStretchFactor(1,2)
        splitter.setSizes([330,230]);layout.insertWidget(pos,splitter,1);self.splitters.append(splitter)
    def open_users(self,side):
        if self.dialog:return
        dialog=QDialog(self.app);dialog.setWindowTitle('Пользователи');dialog.resize(390,min(760,self.app.height()-40))
        layout=QVBoxLayout(dialog);layout.addWidget(side);side.show()
        done=Button('Готово',self.app.theme,dialog.accept);layout.addWidget(done);self.dialog=dialog
        def restore():
            for widget,home,button,width in self.sidebars:
                if widget is side:home.insertWidget(0,side);side.setVisible(not self.compact)
            self.dialog=None;dialog.deleteLater()
        dialog.finished.connect(restore);dialog.open()
    def close_users(self):
        if self.dialog:self.dialog.accept()
    def focus_search(self):
        a=self.app;index=a.pages.currentIndex()
        if index==1 and self.compact:self.open_users(self.sidebars[1][0])
        (a.search if index==0 else a.mod_view.search if index==1 else a.user_view.nick if index==3 else a.replay_view.channel).setFocus()
    def open_menu(self):
        from PySide6.QtWidgets import QMenu
        menu=QMenu(self.app)
        for button in self.app.tool_buttons[1:3]:menu.addAction(button.text(),button.click)
        menu.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))
    def show_status(self):
        a=self.app;a.notice('Подключения и состояние','\n\n'.join(w.text() for w in (a.status,a.twitch_status,a.emote_status,a.detail) if w.text()))
    def update_status(self):
        a=self.app;state=a.twitch.snapshot();plugin=a.control.status()
        chat='Chatterino: '+('подключён' if plugin.get('fresh') else 'нет связи')
        twitch='Twitch: '+(str(len(state['joined']))+' каналов' if state['phase']=='connected' else 'нет связи')
        self.short_status.setText(chat+' · '+twitch)
        self.short_status.setToolTip(a.status.text()+'\n'+a.twitch_status.text()+'\n'+a.detail.text())
    def refresh(self):
        a=self.app;compact=a.width()<1180;changed=self.initial or compact!=self.compact
        self.initial=False;self.compact=compact
        if changed:
            if self.dialog:self.close_users()
            for side,layout,button,width in self.sidebars:side.setVisible(not compact);button.setVisible(compact)
            self.tools.setVisible(not compact);self.compact_tools.setVisible(compact);self.subtitle.setVisible(not compact)
            self.footer.setVisible(not compact);self.short_footer.setVisible(compact)
            f=a.app_heading.font();f.setPointSizeF(16 if compact else 21);a.app_heading.setFont(f)
            for splitter in self.splitters:splitter.setSizes([330,210])
        narrow=a.width()<740
        for layout in (self.message_actions,self.message_filters,self.replay_controls):
            layout.setDirection(QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight)
        a.channel.setMinimumWidth(130);a.day.setMaximumWidth(16777215 if narrow else 150)
        a.mod_view.channel.setMinimumWidth(120);a.mod_view.kind.setMinimumWidth(150)
        # Keep general totals accessible without consuming the compact page height.
        a.mod_view.scope.setVisible(not compact);a.mod_view.summary.setVisible(not compact);a.mod_view.global_counts.setVisible(not compact)
        self.columns()
        if changed:self.reformat()
        self.update_status()
    def columns(self):
        a=self.app;m=a.mod_view;c=self.compact
        m.tree.setColumnHidden(1,bool(m.user) and not(c and m.channel.currentText()=='Все каналы'));m.tree.setColumnHidden(2,c or m.channel.currentText()!='Все каналы');m.tree.setColumnHidden(4,c)
        m.tree.headerItem().setText(1,'Канал' if c and m.user else 'Пользователь')
        m.tree.header().setStretchLastSection(False)
        for i in range(5):m.tree.header().setSectionResizeMode(i,QHeaderView.Interactive)
        m.tree.header().setSectionResizeMode(3 if c else 4,QHeaderView.Stretch)
        for i,w in enumerate(([140,160,135,135,230] if c else [175,140,135,135,230])):m.tree.setColumnWidth(i,w)
        a.messages.setColumnHidden(1,c or a.channel.currentText()!='Все каналы')
        a.messages.setColumnWidth(0,180);a.messages.setColumnWidth(1,140)
        r=a.replay_view;r.list.setColumnHidden(1,c);r.list.setColumnHidden(4,c)
        r.list.setColumnWidth(0,145 if c else 180);r.list.setColumnWidth(1,145);r.list.setColumnWidth(3,110);r.list.setColumnWidth(4,100)
        for tree in (m.tree,a.messages,r.list):tree.doItemsLayout()
    def reformat(self):
        a=self.app;m=a.mod_view
        for i in range(m.tree.topLevelItemCount()):
            item=m.tree.topLevelItem(i);row=item.data(0,Qt.UserRole)
            kind={'ban':'Бан','timeout':'Мут','delete':'Удаление'}[row['kind']]
            if row['kind']=='timeout' and row['duration'] is not None:kind+=f" · {row['duration']} с"
            format_moderation(m,item,row,kind)
        for i in range(a.messages.topLevelItemCount()):
            item=a.messages.topLevelItem(i);format_message(a,item,item.data(0,Qt.UserRole))
        for i in range(a.replay_view.list.topLevelItemCount()):
            item=a.replay_view.list.topLevelItem(i);meta=a.replay_view.metas.get(item.data(0,Qt.UserRole))
            if meta:format_replay(a.replay_view,item,meta)
