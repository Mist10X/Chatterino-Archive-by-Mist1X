"""Moderation directory and exact-user cards for the Qt panel."""
import datetime as dt
from PySide6.QtCore import Qt, QTimer, Signal, QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLineEdit, QTreeWidgetItem, QMenu, QWidgetAction, QCheckBox, QCompleter, QComboBox
from ui_controls import ChannelCombo, AnimatedCheck
from storage import name
from query_state import QueryState
from stable_tree import reconcile,set_texts
from message_text import reply_text
from emote_widgets import EmoteBrowser,message_blocks
from theme import (box,card,label,Button,Avatar,combo,table,preview,scroll,update_combo,RED,GREEN)

def stamp(ms):
    try:return dt.datetime.fromtimestamp(ms/1000).strftime('%d.%m.%Y  %H:%M:%S')
    except (ValueError,TypeError,OSError):return 'Время неизвестно'

class EventFilter(Button):
    changed=Signal()
    OPTIONS=[('Муты','timeout'),('Баны','ban'),('Удалённые сообщения','delete'),('Размуты','reward_unmute')]
    def __init__(self,theme):
        super().__init__('Все события',theme,self.open);self.menu=QMenu(self);self.checks=[];self.updating=False
        self.all=AnimatedCheck('Все события',theme);self.add_check(self.all);self.all.setChecked(True);self.all.stateChanged.connect(self.toggle_all)
        self.menu.addSeparator()
        for label_text,value in self.OPTIONS:
            check=AnimatedCheck(label_text,theme);check.setProperty('value',value);check.setChecked(True);check.stateChanged.connect(self.item_changed);self.add_check(check);self.checks.append(check)
    def add_check(self,check):
        action=QWidgetAction(self.menu);action.setDefaultWidget(check);self.menu.addAction(action)
    def open(self):self.menu.exec(self.mapToGlobal(QPoint(0,self.height())))
    def toggle_all(self,state):
        if self.updating:return
        self.updating=True
        for check in self.checks:check.setChecked(bool(state))
        self.updating=False;self.refresh();self.changed.emit()
    def item_changed(self):
        if self.updating:return
        self.updating=True;self.all.setChecked(all(c.isChecked() for c in self.checks));self.updating=False
        self.refresh();self.changed.emit()
    def refresh(self):
        selected=[c.text() for c in self.checks if c.isChecked()]
        self.setToolTip(', '.join(selected) if selected else 'Выбери хотя бы один тип события для просмотра.')
        caption=', '.join(selected)
        self.setText('Все события' if len(selected)==len(self.checks) else ('События не выбраны' if not selected else caption if len(caption)<=25 else 'Выбрано: '+str(len(selected))))
    def values(self):return tuple(c.property('value') for c in self.checks if c.isChecked())
    def currentText(self):return self.text()
    def count(self):return len(self.OPTIONS)+1
    def itemText(self,index):return (['Все события']+[x[0] for x in self.OPTIONS])[index]
    def setCurrentIndex(self,index):self.setCurrentText(self.itemText(index))
    def setCurrentText(self,text):
        labels=[x[0] for x in self.OPTIONS]
        if text!='Все события' and text not in labels:return
        self.updating=True
        for check in self.checks:check.setChecked(text=='Все события' or check.text()==text)
        self.all.setChecked(text=='Все события');self.updating=False;self.refresh();self.changed.emit()
    def set_values(self,values):
        values=set(values)
        self.updating=True
        for check in self.checks:check.setChecked(check.property('value') in values)
        self.all.setChecked(all(c.isChecked() for c in self.checks));self.updating=False;self.refresh();self.changed.emit()
    def reset(self):self.setCurrentText('Все события')

class ModerationView:
    def __init__(self,app):
        self.app=app;self.user='';self.token=0;self.offset=0;self.rows=[];self.total=0
        self.query=QueryState(cache_size=0)
        self.people_limit=200;self.people_total=0;self.people_search='';self.card_key=None
        self.timer=QTimer(app);self.timer.setSingleShot(True);self.timer.setInterval(350);self.timer.timeout.connect(self.reload)
        body,layout=box(False,margins=16);body.setMinimumSize(1000,620)
        directory,dl=card();directory.setFixedWidth(340)
        dl.addWidget(label('ПОЛЬЗОВАТЕЛИ',9,True,True))
        self.search=QLineEdit();self.search.setPlaceholderText('Поиск по нику…');self.search.setAccessibleName('Поиск пользователя в журнале');dl.addWidget(self.search)
        dl.addWidget(Button('Открыть карточку',app.theme,lambda:app.safe(lambda:self.open_user(self.search.text())),icon='users',primary=True))
        self.people=table(['Ник','Баны','Муты','Удал.'],[125,49,49,55]);self.people.setColumnWidth(0,125);dl.addWidget(self.people,1)
        self.people_status=label('',9,True);dl.addWidget(self.people_status)
        self.more_people=Button('Показать ещё',app.theme,self.load_more_people);self.more_people.hide();dl.addWidget(self.more_people)
        self.people.verticalScrollBar().valueChanged.connect(self.people_scrolled)
        dl.addWidget(Button('Все пользователи',app.theme,self.overview,icon='archive'))
        layout.addWidget(directory)
        right,rl=box();layout.addWidget(right,1)
        heading,hl=card();self.heading=heading;identity,il=box(False);self.heading_identity=identity;self.avatar=Avatar();il.addWidget(self.avatar)
        title,tl=box();self.title=label('Модерация',18,bold=True);self.subtitle=label('История модерации',9,True);tl.addWidget(self.title);tl.addWidget(self.subtitle);il.addWidget(title,1)
        self.total_caption=label('Всего в архиве:',15,bold=True)
        self.total_caption.setAlignment(Qt.AlignLeft|Qt.AlignTop);il.addWidget(self.total_caption,0,Qt.AlignTop)
        self.ban_count=label('0',19,bold=True);self.ban_count.setStyleSheet('color:#efa0b2;')
        self.mute_count=label('0',19,bold=True);self.mute_count.setStyleSheet('color:#e7c685;')
        self.delete_count=label('0',19,bold=True);self.delete_count.setStyleSheet('color:#e9ac7a;')
        for title,value in [('БАНЫ',self.ban_count),('МУТЫ',self.mute_count),('УДАЛЕНИЯ',self.delete_count)]:
            w,l=box();l.addWidget(value);l.addWidget(label(title,8,True,True));il.addWidget(w)
        hl.addWidget(identity);rl.addWidget(heading)
        from shared_bans_view import open_shared
        self.shared_button=Button('Общие баны Chatterino+',app.theme,lambda:open_shared(app,self.user or self.search.text().strip()),icon='shield')
        links,links_layout=box(False);self.links=links;links_layout.addWidget(self.shared_button)
        self.full_card_button=Button('Полная карточка',app.theme,lambda:app.safe(lambda:app.open_user_card(self.user or self.search.text())),icon='users')
        links_layout.addWidget(self.full_card_button);hl.addWidget(links)
        self.scope=label('Все пользователи · наш архив и общие баны',9,True);rl.addWidget(self.scope)
        fw,fl=box(False);self.channel=ChannelCombo(['Все каналы']);self.kind=EventFilter(app.theme)
        self.channel.setAccessibleName('Канал событий модерации');self.kind.setAccessibleName('Тип события модерации')
        self.reset_button=Button('Сбросить',app.theme,self.reset)
        fl.addWidget(self.channel,1);fl.addWidget(self.kind,1);fl.addWidget(self.reset_button);rl.addWidget(fw)
        self.tree=table(['Время','Пользователь','Канал','Событие','Последнее сообщение'],[175,140,135,135,300]);rl.addWidget(self.tree,3)
        nav,nl=box(False);self.page_label=label('',9,True);nl.addWidget(self.page_label,1)
        self.prev=Button('Новее',app.theme,lambda:self.page(-1),icon='left');self.next=Button('Старее',app.theme,lambda:self.page(1),icon='right')
        nl.addWidget(self.prev);nl.addWidget(self.next);rl.addWidget(nav)
        self.preview_caption=label('СОБЫТИЕ И СООБЩЕНИЯ ПЕРЕД НИМ',9,True,True);rl.addWidget(self.preview_caption)
        self.preview=EmoteBrowser(app.emotes);rl.addWidget(self.preview,2)
        self.summary=label('',9,True);self.global_counts=label('',9,True);rl.addWidget(self.summary);rl.addWidget(self.global_counts)
        self.frame=scroll(body)
        self.search.textChanged.connect(self.search_edited);self.search.returnPressed.connect(lambda:app.safe(lambda:self.open_user(self.search.text())))
        self.channel.currentIndexChanged.connect(self.reload);self.kind.changed.connect(self.reload)
        self.people.itemSelectionChanged.connect(self.select_person);self.tree.itemSelectionChanged.connect(self.show)
        self.tree.itemDoubleClicked.connect(lambda item,col:self.open_user(item.data(0,Qt.UserRole)['user']) if item.data(0,Qt.UserRole)['user'] else None)
        self.preview.setPlainText('Выбери событие, чтобы увидеть историю наказания и предшествующие сообщения.')

    def selected_channel(self):
        text=self.channel.currentText()
        return '' if text=='Все каналы' or self.channel.findText(text,Qt.MatchFixedString)<0 else text
    def filters(self):return self.user,self.selected_channel(),self.kind.values()
    def search_edited(self):
        self.people_limit=200
        self.query.invalidate();self.timer.start()
    def people_scrolled(self,value):
        bar=self.people.verticalScrollBar()
        if self.people.isVisible() and bar.maximum()>0 and value>=bar.maximum()-40:
            self.load_more_people()
    def load_more_people(self):
        if self.people_limit>=self.people_total or self.people_limit>self.people.topLevelItemCount():return
        self.people_limit=min(self.people_total,self.people_limit+200);self.request()
    def request(self):
        filters=self.filters();search=self.search.text();extra=tuple(sorted(self.app.control.users))
        if search!=self.people_search:
            self.people_search=search;self.people_limit=200;self.people.clear()
        self.query.select((filters,self.offset,search,extra,self.people_limit))
        if self.card_key!=(filters,self.offset):
            self.card_key=(filters,self.offset)
            self.rows=[];self.total=0;self.tree.clear();self.page_label.setText('Загружаем историю…')
            self.prev.setEnabled(False);self.next.setEnabled(False)
            self.title.setText('@'+self.app.profiles.display(self.user) if self.user else 'Модерация');self.avatar.set_profile(self.app.profiles,self.user)
            self.total_caption.setText('У пользователя:' if self.user else 'Всего в архиве:')
            for counter in (self.ban_count,self.mute_count,self.delete_count):counter.setText('…')
            self.preview.setPlainText('Загружаем историю…')
        token=self.query.request()
        if token is None:return
        self.token=token;self.app.worker.tasks.put(('moderation',(token,filters,self.offset,search,extra,self.people_limit)))
    def reload(self,*_):
        self.timer.stop();self.offset=0
        deleted=self.kind.values()==('delete',)
        self.tree.setMaximumHeight(210 if deleted else 16777215)
        self.preview_caption.setText('УДАЛЁННОЕ СООБЩЕНИЕ' if deleted else 'СОБЫТИЕ И СООБЩЕНИЯ ПЕРЕД НИМ')
        if hasattr(self.app,'responsive'):self.app.responsive.refresh()
        self.request()
    def reset(self):
        self.channel.blockSignals(True);self.channel.setCurrentIndex(0);self.channel.blockSignals(False);self.kind.reset()
        self.reload()
    def overview(self):self.user='';self.search.blockSignals(True);self.search.clear();self.search.blockSignals(False);self.reset()
    def open_user(self,user):
        user=name(user)
        if user!=self.user:self.preview.setPlainText('Загружаем историю пользователя…');self.tree.clear()
        self.user=user;self.reload()
    def select_person(self):
        items=self.people.selectedItems()
        if items and items[0].data(0,Qt.UserRole)!=self.user:self.open_user(items[0].data(0,Qt.UserRole))
    def page(self,d):self.offset=max(0,self.offset+d*100);self.request()
    def failed(self,token,error):
        if self.query.finish(token):
            self.page_label.setText('Не удалось обновить историю · F5 — повторить')
            self.summary.setText(error)
    def render(self,token,result):
        if not self.query.finish(token):return
        self.rows=result['rows'];self.total=result['total']
        def update_event(item,row):
            from user_history import event_summary,event_table_summary,event_kind_label
            kind=event_kind_label(row)
            summary=event_table_summary(row);row['summary']=summary
            set_texts(item,[stamp(row['at_ms']),row['user'] or 'Автор неизвестен','#'+row['channel'],kind,summary]);item.setData(0,Qt.UserRole,row)
            item.setToolTip(4,event_table_summary(row,2000))
            from responsive import format_moderation
            format_moderation(self,item,row,kind)
            from responsive import event_color,summary_color
            item.setForeground(3,QColor(event_color(row)));item.setForeground(4,QColor(summary_color(row,summary)))
        reconcile(self.tree,self.rows,lambda row:row['key'],update_event,follow_top=self.offset==0)
        self.tree.setColumnHidden(1,bool(self.user))
        self.title.setText('@'+self.app.profiles.display(self.user) if self.user else 'Модерация');self.avatar.set_profile(self.app.profiles,self.user);self.avatar.text=self.user[:2] if self.user else 'ВС';self.avatar.update()
        self.total_caption.setText('У пользователя:' if self.user else 'Всего в архиве:')
        personal=result['personal_counts'];overall=result['global_counts']
        self.ban_count.setText(str(personal['bans']));self.mute_count.setText(str(personal['timeouts']));self.delete_count.setText(str(personal.get('deletions',0)))
        self.scope.setText('Личные счётчики: наш архив и загруженные общие баны' if self.user else 'Все пользователи · наш архив и общие баны')
        self.global_counts.setText(f"Наш архив + Chatterino+ · баны: {overall['bans']} · муты: {overall['timeouts']} · удаления: {overall.get('deletions',0)}")
        def update_person(item,person):
            set_texts(item,[person['user'],str(person['bans']),str(person['timeouts']),str(person.get('deletions',0))]);item.setData(0,Qt.UserRole,person['user'])
        reconcile(self.people,result['people']['rows'],lambda p:p['user'],update_person,self.user)
        total=result['people']['total'];self.people_total=total;shown=len(result['people']['rows'])
        self.people_status.setText(f'Пользователей: {total}' if shown>=total else f'Показано {shown} из {total}')
        self.more_people.setVisible(shown<total)
        self.page_label.setText(f'{self.offset+1}–{self.offset+len(self.rows)} из {self.total}' if self.rows else 'Нет событий по этим условиям')
        self.prev.setEnabled(self.offset>0);self.next.setEnabled(self.offset+len(self.rows)<self.total)
        update_combo(self.channel,['Все каналы']+sorted(set(result['channels'])|set(self.app.control.channels)))
        if hasattr(self.app,'responsive'):self.app.responsive.columns()
        note=f"По выбранным условиям: баны — {result['bans']}, муты — {result['timeouts']}, удаления — {result.get('deletions',0)}, баны после AutoMod — {result.get('automod',0)}, размуты — {result.get('unmutes',0)}."
        hint=result.get('hint')
        if hint:
            date=dt.datetime.fromtimestamp(hint['at_seconds']).strftime('%d.%m.%Y %H:%M')
            note+=f"  Сводка Chatterino+: {hint['n']} отметок о банах ({date}); каналы в ней не указаны."
        if result.get('invalid'):note+=f"  Пропущено повреждённых записей: {result['invalid']}."
        self.summary.setText(note)
        if self.tree.selectedItems():self.show()
        else:self.preview.setPlainText('Выбери событие, чтобы увидеть сообщения перед наказанием.' if self.rows else 'Событий по этим условиям пока нет. Удаления поступают из подключения Twitch, баны и муты — из Chatterino.')
        if not self.kind.values():self.preview.setPlainText('Выбери типы событий в фильтре над списком.');self.page_label.setText('Типы событий не выбраны')
        if self.query.dirty:self.request()
    def show(self):
        selected=self.tree.selectedItems()
        if not selected:return
        row=selected[0].data(0,Qt.UserRole)
        if row['kind']=='ban':
            self.app.request_ban_context(row)
            from user_history import event_blocks
            self.preview.set_blocks(event_blocks(row));return
        if row['kind'] in ('automod','reward_unmute'):
            from user_history import event_blocks
            self.preview.set_blocks(event_blocks(row));return
        if row['kind']=='delete':
            from panel import local_time
            message=row['message'];blocks=[{'text':'УДАЛЕНО СООБЩЕНИЕ','style':'deletion'},
                {'text':f"{row['user'] or 'Автор неизвестен'} · #{row['channel']} · удалено {stamp(row['at_ms'])}",'style':'meta'},
                {'text':row['status'],'style':'meta'}]
            if row['basis']!='server':blocks.append({'text':'Время удаления — момент получения события приложением.','style':'meta'})
            if message and message.get('state')=='available':blocks.extend(message_blocks(message,local_time))
            else:blocks.append({'text':'Сообщение удалено, исходный текст недоступен.','style':'body'})
            blocks.append({'text':'Сопоставление с наказанием проверяется в пределах следующей минуты по тому же пользователю и каналу. Это не доказывает причину наказания.','style':'meta'})
            from moderation_evidence import attribution_blocks
            self.preview.set_blocks(blocks+attribution_blocks(row));return
        kind='БАН' if row['kind']=='ban' else 'МУТ';need=10 if row['kind']=='ban' else 5
        blocks=[{'text':f"{kind} · {row['user']} · #{row['channel']} · {stamp(row['at_ms'])}",'style':'title'},
            {'text':row['status'],'style':'meta'}]
        if row['duration'] is not None:blocks.append({'text':f"Длительность: {row['duration']} с",'style':'meta'})
        if row.get('repeat_count',1)>1:
            blocks.append({'text':f"Повторные уведомления Chatterino (×{row['repeat_count']}) объединены в одно наказание.",'style':'meta'})
        blocks.append({'text':f"Сообщений перед наказанием: {len(row['context'])} из {need}",'style':'meta'})
        for message in row['context']:
            from panel import local_time
            blocks.extend(message_blocks(message,local_time))
        if len(row['context'])<need:blocks.append({'text':'Остальные сообщения не были доступны в полученной истории.','style':'meta'})
        blocks.append({'text':'Системное сообщение Chatterino:\n'+row['raw'],'style':'meta'})
        from moderation_evidence import attribution_blocks
        self.preview.set_blocks(blocks+attribution_blocks(row))
