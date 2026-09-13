"""Responsive archive pages; only presentation changes, no archive mutations."""
import html
from PySide6.QtCore import QObject,QEvent,QTimer,Qt,QSize,QRect,QRectF
from PySide6.QtWidgets import QDialog,QVBoxLayout,QSplitter,QHeaderView,QBoxLayout,QStyledItemDelegate,QStyleOptionViewItem,QSizePolicy,QStyle,QMenu
from PySide6.QtGui import QPalette,QColor,QTextDocument
from theme import box,Button,label,RED,GREEN,TEXT,MUTED,ACCENT

MUTE_COLOR='#e7c685';DELETE_COLOR='#e9ac7a';AUTOMOD_COLOR='#f3b45f';UNMUTE_COLOR='#72d7c2'

def event_color(row):
    kind=row.get('kind','')
    if kind in ('unban','untimeout','speech') or str(row.get('status','')).startswith('Снят:'):return GREEN
    if kind=='reward_unmute':return UNMUTE_COLOR
    if kind=='ban':return RED
    if kind=='timeout':return MUTE_COLOR
    if kind=='delete':return DELETE_COLOR
    if kind=='automod':return AUTOMOD_COLOR
    return TEXT

def stripe_color(row):
    return ACCENT if row.get('automod_ban') else event_color(row)

def summary_color(row,text):
    value=text.removeprefix('Заменён новым наказанием · ')
    if value.startswith('AutoMod ·'):return AUTOMOD_COLOR
    if row.get('kind')=='reward_unmute':return UNMUTE_COLOR
    if row.get('kind')=='timeout' and row.get('reward_attribution'):return ACCENT
    if value in ('Сообщение недоступно','') or value.endswith('недоступно'):return MUTED
    return TEXT

def _span(text,color):
    return '<span style="color:'+color+'">'+html.escape(text).replace('\n','<br>')+'</span>'

def event_markup(text,row):
    if row.get('automod_ban') and ' · AutoMod' in text:
        main,automod=text.rsplit(' · AutoMod',1)
        return _span(main,event_color(row))+_span(' · AutoMod'+automod,AUTOMOD_COLOR)
    return _span(text,event_color(row))

def summary_markup(text,row):
    prefix='Заменён новым наказанием · '
    if text.startswith(prefix):return _span(prefix,MUTED)+_span(text[len(prefix):],summary_color(row,text))
    return _span(text,summary_color(row,text))

class WrappedCells(QStyledItemDelegate):
    def paint(self,painter,option,index):
        opt=QStyleOptionViewItem(option);self.initStyleOption(opt,index);text=index.data(Qt.DisplayRole) or '';opt.text=''
        self.parent().style().drawControl(QStyle.CE_ItemViewItem,opt,painter,self.parent())
        painter.save();painter.setClipRect(option.rect);painter.setFont(opt.font)
        painter.setPen(opt.palette.color(QPalette.HighlightedText if opt.state & QStyle.State_Selected else QPalette.Text))
        padding=4 if self.parent().property('denseRows') else 7
        rect=option.rect.adjusted(10,padding,-10,-padding)
        profiles=getattr(self.parent().window(),'profiles',None) or getattr(self.parent().window().parentWidget(),'profiles',None)
        row=index.sibling(index.row(),0).data(Qt.UserRole)
        if profiles and isinstance(row,str) and index.column()==0 and profiles.valid(row):
            profiles.ensure(row);text=profiles.display(row)
        if profiles and isinstance(row,dict):
            login=row.get('user','');display=row.get('display_name')
            if login:
                profiles.ensure(login)
                lines=text.split('\n');text='\n'.join(profiles.display(login,display) if line==login else line for line in lines)
        channel_line=next((line for line in text.split('\n') if line.startswith('#') and profiles and profiles.valid(line[1:])),None)
        if channel_line:
            before=text.split(channel_line,1)[0].rstrip('\n');line_height=opt.fontMetrics.height()
            height=line_height*(len(before.split('\n')) if before else 0)+max(20,line_height)
            top=rect.top()+max(0,(rect.height()-height)/2)
            if before:painter.drawText(QRect(rect.left(),int(top),rect.width(),height-max(20,line_height)),Qt.AlignLeft|Qt.AlignTop,before)
            profiles.channel(painter,QRectF(rect.left(),top+height-max(20,line_height),rect.width(),max(20,line_height)),channel_line[1:],opt.font)
        else:painter.drawText(rect,Qt.AlignVCenter|Qt.AlignLeft|Qt.TextWordWrap,text)
        painter.restore()
    def sizeHint(self,option,index):
        opt=QStyleOptionViewItem(option);self.initStyleOption(opt,index)
        width=max(50,self.parent().columnWidth(index.column())-24)
        rect=opt.fontMetrics.boundingRect(QRect(0,0,width,3000),Qt.TextWordWrap,opt.text)
        dense=bool(self.parent().property('denseRows'))
        return QSize(width,max(36 if dense else 42,rect.height()+(10 if dense else 18)))

class EventCells(WrappedCells):
    """Paint event labels, details and row markers without losing colours on selection."""
    def __init__(self,parent,event_column,summary_column,joined=False,origin_line=False):
        super().__init__(parent);self.event_column=event_column;self.summary_column=summary_column
        self.joined=joined;self.origin_line=origin_line
    def _document(self,markup,font,width):
        doc=QTextDocument();doc.setDocumentMargin(0);doc.setDefaultFont(font);doc.setTextWidth(width)
        doc.setDefaultStyleSheet('div {margin:0;padding:0;}');doc.setHtml(markup);return doc
    def _paint_markup(self,painter,option,index,markup):
        opt=QStyleOptionViewItem(option);self.initStyleOption(opt,index);opt.text=''
        self.parent().style().drawControl(QStyle.CE_ItemViewItem,opt,painter,self.parent())
        padding=4 if self.parent().property('denseRows') else 7
        rect=option.rect.adjusted(10,padding,-10,-padding);doc=self._document(markup,opt.font,max(1,rect.width()))
        painter.save();painter.setClipRect(option.rect)
        painter.translate(rect.left(),rect.top()+max(0,(rect.height()-doc.size().height())/2))
        doc.drawContents(painter,QRectF(0,0,rect.width(),max(rect.height(),doc.size().height())));painter.restore()
    def paint(self,painter,option,index):
        row=index.sibling(index.row(),0).data(Qt.UserRole)
        if not isinstance(row,dict):return super().paint(painter,option,index)
        column=index.column();text=index.data(Qt.DisplayRole) or ''
        if column==self.event_column:
            if self.joined and '\n' in text:
                event,detail=text.split('\n',1)
                markup='<div>'+event_markup(event,row)+'</div><div>'+summary_markup(detail,row)+'</div>'
            else:markup='<div>'+event_markup(text,row)+'</div>'
            self._paint_markup(painter,option,index,markup)
        elif column==self.summary_column:
            if self.origin_line and '\n' in text:
                detail,origin=text.rsplit('\n',1)
                markup='<div>'+summary_markup(detail,row)+'</div><div>'+_span(origin,MUTED)+'</div>'
            else:markup='<div>'+summary_markup(text,row)+'</div>'
            self._paint_markup(painter,option,index,markup)
        else:super().paint(painter,option,index)
        if column==0:
            painter.save();painter.setPen(Qt.NoPen);painter.setBrush(QColor(stripe_color(row)))
            painter.drawRoundedRect(QRectF(option.rect.left()+2,option.rect.top()+4,4,max(4,option.rect.height()-8)),2,2);painter.restore()

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
        super().__init__(app);self.app=app;self.compact=False;self.narrow=False;self.initial=True;self.dialog=None;self.page=-1
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
        app.users.setItemDelegate(WrappedCells(app.users));app.mod_view.people.setItemDelegate(WrappedCells(app.mod_view.people))
        app.users.parentWidget().setMinimumHeight(320);app.mod_view.people.parentWidget().setMinimumHeight(400)
        self.mod_users=self.sidebars[1][2]
        self.mod_users.parentWidget().layout().removeWidget(self.mod_users)
        app.mod_view.channel.parentWidget().layout().insertWidget(0,self.mod_users)
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
        app.mod_view.tree.setItemDelegate(EventCells(app.mod_view.tree,3,4,joined=True))
        app.messages.setUniformRowHeights(False)
        app.messages.setItemDelegateForColumn(0,WrappedCells(app.messages));app.messages.setItemDelegateForColumn(1,WrappedCells(app.messages))
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
        self.header=app.header;self.tabs=app.tabs_bar
        self.nav_more=Button('Ещё',app.theme,self.open_moderation_menu)
        self.tabs.layout().insertWidget(4,self.nav_more);self.nav_more.hide()
        self.heading_container=app.app_heading.parentWidget().parentWidget()
        self.subtitle=app.app_heading.parentWidget().layout().itemAt(1).widget()
        self.footer=app.status.parentWidget();footer_layout=self.footer.parentWidget().layout()
        self.short_footer,fl=box(False,margins=8);self.short_status=label('',9,True)
        self.short_status.setTextFormat(Qt.RichText)
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
        self.app.open_tools_menu(self.more)
    def open_moderation_menu(self):
        a=self.app;m=a.mod_view;menu=QMenu(a)
        if a.width()<740:
            menu.addAction('Сообщения',lambda:a.select_page(0))
            menu.addAction('Повторы чата',lambda:a.select_page(2))
            menu.addAction('Пользователи',lambda:a.select_page(3))
            menu.addSeparator();menu.addAction('Список пользователей модерации',self.mod_users.click)
            menu.addSeparator()
        menu.addAction('Общие баны Chatterino+',m.shared_button.click)
        menu.addAction('Полная карточка пользователя',m.full_card_button.click)
        menu.addSeparator();menu.addAction('Каналы',a.tool_buttons[0].click);menu.addAction('Подключение Twitch',a.tool_buttons[1].click)
        menu.addAction('Очистить историю чатов…',a.trim.click)
        menu.addSeparator();menu.addAction('Открыть папку архива',a.folder_tool.click);menu.addAction('Оформление и 7TV',a.appearance_tool.click)
        menu.addAction('Проверить обновления',a.updates.dialog);menu.addAction('О программе',a.about_dialog)
        menu.exec(self.nav_more.mapToGlobal(self.nav_more.rect().bottomLeft()))
    def show_status(self):
        a=self.app;a.notice('Подключения и состояние','\n\n'.join(w.text() for w in (a.status,a.twitch_status,a.emote_status,a.detail) if w.text()))
    def update_status(self):
        a=self.app;state=a.twitch.snapshot();plugin=a.control.status()
        chat_ok=bool(plugin.get('fresh'));twitch_ok=state['phase']=='connected'
        chat='<span style="color:#86d6b0">•</span> Chatterino: подключён' if chat_ok else '<span style="color:#efa0b2">•</span> Chatterino: нет связи'
        twitch=('<span style="color:#86d6b0">•</span> Twitch: '+str(len(state['joined']))+' каналов') if twitch_ok else '<span style="color:#efa0b2">•</span> Twitch: нет связи'
        self.short_status.setText(chat+' &nbsp;·&nbsp; '+twitch)
        self.short_status.setToolTip(a.status.text()+'\n'+a.twitch_status.text()+'\n'+a.detail.text())
    def refresh(self):
        a=self.app;compact=a.width()<1180;narrow=a.width()<740;page=a.pages.currentIndex();compact_changed=self.initial or compact!=self.compact
        mode_changed=self.initial or compact_changed or narrow!=self.narrow or page!=self.page;moderation_focus=compact and page==1
        self.initial=False;self.compact=compact;self.narrow=narrow;self.page=page
        if mode_changed:
            if self.dialog:self.close_users()
            for side,layout,button,width in self.sidebars:side.setVisible(not compact);button.setVisible(compact)
            self.mod_users.setVisible(compact and not(moderation_focus and narrow))
            self.header.setVisible(not moderation_focus)
            self.tools.setVisible(not compact);self.compact_tools.setVisible(compact and not moderation_focus);self.subtitle.setVisible(not compact)
            self.nav_more.setVisible(moderation_focus)
            for tab in (a.message_tab,a.replay_tab,a.user_tab):tab.setVisible(not(moderation_focus and narrow))
            a.moderation_tab.setVisible(True)
            self.footer.hide();self.short_footer.show()
            f=a.app_heading.font();f.setPointSizeF(16 if compact else 21);a.app_heading.setFont(f);a.app_heading.setStyleSheet(f"font-size:{16 if compact else 21}pt;")
            self.splitters[0].setSizes([330,210]);self.splitters[1].setSizes([560,150] if moderation_focus else [330,210])
            m=a.mod_view;m.links.setVisible(not compact);m.subtitle.setVisible(not compact);m.avatar.setVisible(not compact)
            m.heading.layout().setContentsMargins(*(12,8,12,8) if compact else (16,14,16,14));m.heading.layout().setSpacing(4 if compact else 8)
            m.heading.setMaximumHeight(86 if compact else 16777215)
            title_font=m.title.font();title_font.setPointSizeF(16 if compact else 18);m.title.setFont(title_font);m.title.setStyleSheet(f"font-size:{16 if compact else 18}pt;")
            caption_font=m.total_caption.font();caption_font.setPointSizeF(11 if compact else 15);m.total_caption.setFont(caption_font);m.total_caption.setStyleSheet(f"font-size:{11 if compact else 15}pt;")
            m.total_caption.setAlignment((Qt.AlignLeft|Qt.AlignVCenter) if compact else (Qt.AlignLeft|Qt.AlignTop))
            m.tree.setProperty('denseRows',moderation_focus);m.tree.header().setFixedHeight(34 if moderation_focus else 40)
            for button in (m.prev,m.next):button.setFixedHeight(38 if moderation_focus else 44)
            m.tree.doItemsLayout();m.tree.viewport().update()
        deleted=a.mod_view.kind.values()==('delete',)
        a.mod_view.preview_caption.setText('УДАЛЁННОЕ СООБЩЕНИЕ' if deleted else ('ДЕТАЛИ СОБЫТИЯ' if moderation_focus else 'СОБЫТИЕ И СООБЩЕНИЯ ПЕРЕД НИМ'))
        for layout in (self.message_actions,self.message_filters,self.replay_controls):
            layout.setDirection(QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight)
        a.channel.setMinimumWidth(130);a.day.setMaximumWidth(16777215 if narrow else 150)
        a.mod_view.channel.setMinimumWidth(120);a.mod_view.kind.setMinimumWidth(150)
        # Detailed explanations remain available in the event preview and status
        # dialog instead of permanently taking vertical space below the tables.
        a.mod_view.scope.setVisible(not compact);a.mod_view.summary.hide();a.mod_view.global_counts.hide()
        self.columns()
        if compact_changed:self.reformat()
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
            from user_history import event_kind_label
            kind=event_kind_label(row)
            format_moderation(m,item,row,kind)
        for i in range(a.messages.topLevelItemCount()):
            item=a.messages.topLevelItem(i);format_message(a,item,item.data(0,Qt.UserRole))
        for i in range(a.replay_view.list.topLevelItemCount()):
            item=a.replay_view.list.topLevelItem(i);meta=a.replay_view.metas.get(item.data(0,Qt.UserRole))
            if meta:format_replay(a.replay_view,item,meta)
