"""Архив Chatterino by Mist1X — interface for the Mist1X archive project."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import html
import os
from pathlib import Path
import queue
import sys
import time
import traceback
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QShortcut, QKeySequence, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QLineEdit,QTreeWidgetItem,
    QStackedWidget,QDialog,QFileDialog,QListWidget,QCheckBox,QGridLayout,QSystemTrayIcon,QMenu)
from branding import APP_NAME,AUTHOR,VERSION,COPYRIGHT
from trim_dialog import TrimDialog
from twitch_core import settings_load,settings_save
from twitch_worker import TwitchWorker
from twitch_dialog import TwitchDialog
from replay_worker import ReplayWorker
from replay_view import ReplayView
from storage import Control,atomic_write,layout_channels,name
from message_text import reply_text
from panel_worker import Worker
from query_state import QueryState
from stable_tree import reconcile,set_texts
from moderation_view import ModerationView
from seven_tv import SevenTV
from emote_widgets import EmoteBrowser,EmoteDelegate,message_blocks
from theme import (Theme,install_style,Button,Avatar,label,box,card,combo,update_combo,table,
    preview,scroll,Dialog,ask_text,draw_icon,MUTED,GREEN,RED,ACCENT)

def local_time(value):
    try:return dt.datetime.fromisoformat(value.replace('Z','+00:00')).astimezone().strftime('%d.%m.%Y  %H:%M:%S')
    except (ValueError,AttributeError):return 'Время неизвестно'

def app_icon():
    icon=QIcon()
    for size in (16,24,32,48,64,128,256):
        pix=QPixmap(size,size);pix.fill(Qt.transparent);p=QPainter(pix);p.setRenderHint(QPainter.Antialiasing)
        p.scale(size/64,size/64);p.setPen(Qt.NoPen);p.setBrush(QColor('#362344'));p.drawRoundedRect(1,1,62,62,16,16)
        from PySide6.QtCore import QRectF
        draw_icon(p,'archive',QRectF(13,13,38,38),ACCENT);p.end();icon.addPixmap(pix)
    return icon

class App(QMainWindow):
    shared_result=Signal(str,object)
    archive_context_changed=Signal()
    def __init__(self,profile,demo=False,network=True):
        super().__init__();self.profile=Path(profile);self.data=self.profile/'Plugins'/'history-tools'/'data'
        self.data.mkdir(parents=True,exist_ok=True);self.control=Control(self.data);self.theme=Theme(self.data)
        self.network=network and not demo;self.exit_requested=False;self.tray=None;self.sent_twitch_users=None
        try:self.twitch_config=settings_load(self.data/'twitch')
        except (ValueError,OSError):self.twitch_config={'enabled':False,'channels':[],'tray':True}
        self.twitch=TwitchWorker(self.data,self.twitch_config)
        from moderation_events import ModerationEvents
        self.moderation_events=ModerationEvents(self.twitch)
        from shared_bans import SharedService
        self.shared=SharedService(self.data,token=lambda:(self.twitch.account or {}).get('access_token'),network=self.network,connection_state=self.twitch.snapshot)
        self.shared.start()
        self.replays=ReplayWorker(self.data,token=lambda:(self.twitch.account or {}).get("access_token"))
        self.twitch.replay=self.replays
        self.demo=demo;self.selected=None;self.offset=0;self.total=0;self.rows=[];self.counts={};self.query_token=0
        self.message_query=QueryState()
        self.emotes=SevenTV(self.data,self.theme.settings,online=network and not demo,parent=self)
        self.last_ack='';self.last_layout_sync=0.;self.last_moderation_query=0.;self.worker_error=None;self.closing=False
        self.setWindowTitle(APP_NAME+(' — тестовые данные' if demo else ''));self.setWindowIcon(app_icon())
        self.setMinimumSize(640,420);size=self.theme.settings.get('size',[1280,870])
        if not isinstance(size,list) or len(size)!=2 or not all(isinstance(x,int) for x in size):size=[1280,870]
        self.resize(max(640,min(1800,size[0])),max(420,min(1100,size[1])))
        self.worker=Worker(self.data)
        self.build()
        from responsive import Responsive
        self.responsive=Responsive(self)
        from connection_alerts import ConnectionAlerts
        self.connection_alerts=ConnectionAlerts(self);self.context_requested=set()
        from update_ui import Updates
        self.updates=Updates(self)
        self.import_channels()
        self.emotes.statusChanged.connect(lambda:self.emote_status.setText(self.emotes.status_text()))
        self.emotes.set_channels(self.control.status().get('channels',[]),self.control.channels)
        if not self.control.revision:self.control.save()
        self.worker.start();self.mod_view.request();self.refresh_users()
        if self.network:
            self.moderation_events.start()
            self.twitch.command('users',self.control.users);self.replays.start();self.twitch.start();self.setup_tray()
        if self.control.users:self.choose_user(sorted(self.control.users)[0])
        self.timer=QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self.poll);self.timer.start()
        self.search_timer=QTimer(self);self.search_timer.setSingleShot(True);self.search_timer.setInterval(400);self.search_timer.timeout.connect(self.reload)
        self.search.textChanged.connect(self.message_filter_edited);self.day.textChanged.connect(self.message_filter_edited)
        QShortcut(QKeySequence('Ctrl+F'),self,activated=self.responsive.focus_search)
        QShortcut(QKeySequence('F5'),self,activated=self.reload_all)
        QApplication.instance().focusChanged.connect(self.keep_focus_visible)
    def keep_focus_visible(self,old,current):
        if current is None or self.closing:return
        page=self.pages.currentWidget()
        if page and page.isAncestorOf(current):page.ensureWidgetVisible(current,18,18)
    def safe(self,fn):
        try:return fn()
        except (OSError,ValueError) as exc:self.notice('Не удалось выполнить действие',str(exc))
    def notice(self,title,text):
        d=Dialog(self,title,text);d.actions(self.theme,cancel=False);d.exec()
    def build(self):
        central,outer=box();outer.setSpacing(0);self.setCentralWidget(central)
        header,hl=box(margins=15);hl.setSpacing(8)
        top,tl=box(False);tl.addWidget(Avatar(logo=True));heading,ll=box();ll.setSpacing(3)
        self.app_heading=label(APP_NAME,21,bold=True);ll.addWidget(self.app_heading);ll.addWidget(label('Сообщения / Пользователи / История',9,True));tl.addWidget(heading,1)
        hl.addWidget(top)
        tools=QWidget();self.tools_layout=QGridLayout(tools);self.tools_layout.setContentsMargins(0,0,0,0);self.tools_layout.setSpacing(7)
        self.tool_buttons=[Button('Каналы',self.theme,lambda:self.safe(self.channels_dialog),icon='users'),
            Button('Папка',self.theme,lambda:self.safe(lambda:os.startfile(str(self.data))),icon='folder'),
            Button('Оформление',self.theme,self.appearance_dialog,icon='spark')]
        self.trim=Button('Очистить историю чатов…',self.theme,lambda:self.safe(self.trim_dialog),icon='archive',primary=True)
        self.tool_buttons.append(Button('Twitch',self.theme,self.twitch_dialog,icon='play'));self.tool_buttons.append(self.trim);self.tool_columns=0;self.arrange_tools();hl.addWidget(tools)
        outer.addWidget(header)
        tabs,tablayout=box(False,margins=12);tablayout.setContentsMargins(16,0,16,8)
        self.message_tab=Button('Сообщения',self.theme,lambda:self.select_page(0),icon='archive')
        self.moderation_tab=Button('Модерация',self.theme,lambda:self.select_page(1),icon='shield')
        self.replay_tab=Button('Повторы чата',self.theme,lambda:self.select_page(2),icon='play')
        self.user_tab=Button('Пользователи',self.theme,lambda:self.select_page(3),icon='users')
        tablayout.addWidget(self.message_tab);tablayout.addWidget(self.moderation_tab);tablayout.addWidget(self.replay_tab);tablayout.addWidget(self.user_tab);tablayout.addStretch();outer.addWidget(tabs)
        self.pages=QStackedWidget();self.pages.addWidget(self.message_page());self.mod_view=ModerationView(self);self.pages.addWidget(self.mod_view.frame);self.replay_view=ReplayView(self);self.pages.addWidget(self.replay_view.frame);outer.addWidget(self.pages,1)
        from user_view import UserView
        self.user_view=UserView(self);self.pages.addWidget(self.user_view.frame)
        footer,fl=box(margins=14);fl.setSpacing(4);fl.setContentsMargins(18,9,18,11)
        self.status=label('Подключение к плагину…',9,True);self.detail=label('',9,True);self.emote_status=label(self.emotes.status_text(),9,True)
        self.twitch_status=label('Twitch не подключён',9,True);fl.addWidget(self.status);fl.addWidget(self.twitch_status);fl.addWidget(self.emote_status);fl.addWidget(self.detail);outer.addWidget(footer)
        self.select_page(0)
    def arrange_tools(self):
        columns=5 if self.width()>=1100 else (3 if self.width()>=820 else 2)
        if columns==self.tool_columns:return
        for b in self.tool_buttons:self.tools_layout.removeWidget(b)
        for i in range(5):self.tools_layout.setColumnStretch(i,0)
        for i,b in enumerate(self.tool_buttons):self.tools_layout.addWidget(b,i//columns,i%columns)
        for i in range(columns):self.tools_layout.setColumnStretch(i,1)
        self.tool_columns=columns
    def resizeEvent(self,event):
        if hasattr(self,'tool_buttons'):self.arrange_tools()
        super().resizeEvent(event)
    def select_page(self,index):
        self.pages.setCurrentIndex(index)
        for i,b in enumerate((self.message_tab,self.moderation_tab,self.replay_tab,self.user_tab)):b.active=i==index;b.update()
        if index==1:self.mod_view.request()
        if index==3:self.user_view.request()
    def message_page(self):
        body,layout=box(False,margins=16);body.setMinimumSize(1000,620)
        sidebar,sl=card();sidebar.setFixedWidth(250);sl.addWidget(label('ОТСЛЕЖИВАЕМЫЕ',9,True,True))
        sl.addWidget(Button('Добавить пользователя',self.theme,lambda:self.safe(self.add_user),icon='plus',primary=True))
        self.users=table(['Пользователь']);self.users.setHeaderHidden(True);self.users.setAlternatingRowColors(False);sl.addWidget(self.users,1)
        sl.addWidget(label('Запись через Chatterino и подключение Twitch',9,True));layout.addWidget(sidebar)
        right,rl=box();layout.addWidget(right,1)
        heading,hl=card();identity,il=box(False);self.avatar=Avatar();il.addWidget(self.avatar)
        title,tl=box();tl.setSpacing(5);self.user_title=label('Сохранённые сообщения',18,bold=True)
        self.recording=label('Добавь пользователя, чтобы начать запись',9,True);tl.addWidget(self.user_title);tl.addWidget(self.recording);il.addWidget(title,1);hl.addWidget(identity)
        actions,al=box(False)
        self.toggle=Button('Начать запись',self.theme,lambda:self.safe(self.toggle_user),icon='play',primary=True)
        self.remove=Button('Убрать из списка',self.theme,lambda:self.safe(self.remove_user),icon='trash',danger=True)
        self.history_button=Button('История модерации',self.theme,self.open_moderation,icon='shield')
        for b in (self.toggle,self.remove,self.history_button):al.addWidget(b);b.setEnabled(False)
        al.addStretch();hl.addWidget(actions);rl.addWidget(heading)
        filters,fl=box(False);self.channel=combo(['Все каналы']);self.channel.setAccessibleName('Канал сохранённых сообщений')
        self.search=QLineEdit();self.search.setPlaceholderText('Поиск в сообщениях…');self.search.setAccessibleName('Поиск в сообщениях')
        self.day=QLineEdit();self.day.setPlaceholderText('ГГГГ-ММ-ДД');self.day.setFixedWidth(155);self.day.setAccessibleName('Дата сообщений')
        fl.addWidget(self.channel);fl.addWidget(self.search,1);fl.addWidget(self.day);rl.addWidget(filters)
        self.messages=table(['Время','Канал','Сообщение'],[180,140,390]);rl.addWidget(self.messages,3)
        self.messages.setItemDelegateForColumn(2,EmoteDelegate(self.emotes,self.messages))
        nav,nl=box(False);self.counter=label('Выбери пользователя слева',9,True);nl.addWidget(self.counter,1)
        self.loading=label('',9,True);nl.addWidget(self.loading)
        self.prev=Button('Новее',self.theme,lambda:self.page(-1),icon='left');self.next=Button('Старее',self.theme,lambda:self.page(1),icon='right')
        self.prev.setEnabled(False);self.next.setEnabled(False);nl.addWidget(self.prev);nl.addWidget(self.next);rl.addWidget(nav)
        caption,cl=box(False);cl.addWidget(label('ПРОСМОТР СООБЩЕНИЯ',9,True,True),1)
        self.copy=Button('Копировать',self.theme,self.copy_message,icon='copy');self.export_button=Button('Экспорт CSV',self.theme,lambda:self.safe(self.export),icon='export')
        cl.addWidget(self.copy);cl.addWidget(self.export_button);rl.addWidget(caption)
        self.preview=EmoteBrowser(self.emotes);self.preview.setPlainText('Выбери сообщение, чтобы прочитать его целиком. Здесь также сохраняется сообщение, на которое был дан ответ.');rl.addWidget(self.preview,2)
        self.users.itemSelectionChanged.connect(self.select_user);self.messages.itemSelectionChanged.connect(self.show_message)
        self.channel.currentTextChanged.connect(self.reload);return scroll(body)
    def refresh_users(self):
        def update(item,row):
            user,enabled=row
            suffix=f' · {self.counts[user]}' if user in self.counts else ''
            set_texts(item,[user+suffix]);item.setData(0,Qt.UserRole,user)
            pix=QPixmap(64,64);pix.fill(Qt.transparent);p=QPainter(pix);p.scale(4,4);p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen);p.setBrush(QColor(GREEN if enabled else MUTED));p.drawEllipse(5,5,6,6);p.end();item.setIcon(0,QIcon(pix))
            item.setForeground(0,QColor(MUTED if not enabled else '#eeedf5'))
        reconcile(self.users,sorted(self.control.users.items()),lambda row:row[0],update,self.selected)
    def choose_user(self,user):
        for i in range(self.users.topLevelItemCount()):
            item=self.users.topLevelItem(i)
            if item.data(0,Qt.UserRole)==user:self.users.setCurrentItem(item);return
    def select_user(self):
        items=self.users.selectedItems()
        if not items:return
        user=items[0].data(0,Qt.UserRole)
        if user!=self.selected:
            self.selected=user;self.user_title.setText(user);self.avatar.text=user[:2];self.avatar.update()
            for b in (self.toggle,self.remove,self.history_button):b.setEnabled(True)
            self.messages.clear();self.preview.setPlainText('Выбери сообщение, чтобы прочитать его целиком.');self.reload()
        self.update_recording()
    def update_recording(self):
        if not self.selected:return
        enabled=self.control.users.get(self.selected,False);self.toggle.setText('Приостановить' if enabled else 'Начать запись');self.toggle.kind='pause' if enabled else 'play';self.toggle.updateGeometry();self.toggle.update()
        state=self.control.status();suffix='' if state.get('fresh') and state.get('revision')==self.control.revision else ' · ожидает подтверждения плагина'
        
        if self.twitch.snapshot()['phase']=='connected':suffix=' · Twitch: '+str(len(self.twitch.snapshot()['joined']))+' каналов'
        self.recording.setText(('Запись на всех выбранных каналах' if enabled else 'На паузе · сохранённые сообщения остаются')+suffix)
    def add_user(self,value=None):
        if value is None:value=ask_text(self,'Добавить пользователя','Ник Twitch. Запись включится на всех выбранных каналах.')
        if value is None:return
        user=name(value)
        if len(self.control.users)>=500 and user not in self.control.users:raise ValueError('Поддерживается до 500 отслеживаемых пользователей.')
        old=dict(self.control.users);self.control.users[user]=True
        try:self.control.save()
        except Exception:self.control.users=old;raise
        self.refresh_users();self.choose_user(user);self.mod_view.request()
    def toggle_user(self):
        if not self.selected:return
        old=dict(self.control.users);self.control.users[self.selected]=not self.control.users.get(self.selected,False)
        try:self.control.save()
        except Exception:self.control.users=old;raise
        self.refresh_users();self.update_recording()
    def remove_user(self):
        if not self.selected:return
        removed=self.selected;self.control.remove_user(removed);self.selected=None;self.message_query.invalidate();self.query_token=self.message_query.token;self.rows=[];self.total=0;self.loading.clear()
        self.messages.clear();self.refresh_users()
        if self.control.users:self.choose_user(sorted(self.control.users)[0])
        else:
            self.user_title.setText('Сохранённые сообщения');self.avatar.text='';self.avatar.update()
            self.recording.setText('Добавь пользователя, чтобы начать запись');self.counter.setText('В списке пока нет пользователей')
            for b in (self.toggle,self.remove,self.history_button,self.prev,self.next):b.setEnabled(False)
            self.preview.setPlainText('Архив сохранён. Чтобы снова открыть его, добавь прежний ник.')
        self.detail.setText(f'{removed} убран из списка. Запись останавливается в подключённых источниках; архив сохранён.')
        self.mod_view.request()
    def request_ban_context(self,row):
        if row.get('kind')!='ban' or not self.network:return
        if row.get('shared_fetched_at') and row.get('context'):return
        key=row.get('key') or (row.get('user'),row.get('channel'),row.get('at'))
        if key in self.context_requested:return
        if self.shared.command('recover',row['user']):self.context_requested.add(key)
    def open_user_card(self,user):
        self.user_view.open_user(user);self.select_page(3)
    def open_moderation(self):
        if self.selected:self.mod_view.open_user(self.selected)
        self.select_page(1)
    def filters(self):
        day=self.day.text().strip()
        if day:
            try:
                if len(day)!=10:raise ValueError()
                dt.date.fromisoformat(day)
            except ValueError:raise ValueError('Дата: ГГГГ-ММ-ДД. Можно оставить поле пустым.') from None
        return self.selected,'' if self.channel.currentText()=='Все каналы' else self.channel.currentText(),self.search.text(),day
    def reload(self,*_):
        if hasattr(self,'search_timer'):self.search_timer.stop()
        self.offset=0;self.request_query()
    def reload_all(self):self.reload();self.mod_view.reload();self.user_view.request();self.safe(self.import_channels)
    def clear_messages(self, text):
        self.rows=[];self.total=0;self.messages.clear();self.counter.setText(text)
        self.prev.setEnabled(False);self.next.setEnabled(False)
        self.preview.setPlainText(text);self.loading.clear()
    def message_filter_edited(self):
        self.message_query.invalidate()
        self.clear_messages('Загружаем сообщения…')
        self.search_timer.start()
    def request_query(self):
        if not self.selected:return
        try:filters=self.filters();self.day.setToolTip('');self.day.setStyleSheet('')
        except ValueError as exc:
            self.message_query.invalidate();self.clear_messages(str(exc))
            self.day.setToolTip(str(exc));self.day.setStyleSheet('border-color:#efa0b2;');return
        if self.message_query.select((filters,self.offset)):
            self.clear_messages('Загружаем сообщения…')
            cached=self.message_query.cached()
            if cached is not None:self.render_rows(*cached)
        token=self.message_query.request()
        if token is None:return
        self.query_token=token
        self.loading.setText('Обновляем…' if self.rows else 'Загрузка…')
        self.worker.tasks.put(('query',(token,filters,self.offset)))
    def page(self,d):self.offset=max(0,self.offset+100*d);self.request_query()
    def render_rows(self,count,rows):
        self.total=count;self.rows=rows
        def update(item,row):
            set_texts(item,[local_time(row['time_utc']),'#'+row['channel'],row['text'].replace('\n',' ↵ ')])
            from responsive import format_message
            format_message(self,item,row)
            item.setData(0,Qt.UserRole,row);item.setToolTip(2,'<qt>'+html.escape(row['text']).replace('\n','<br>')+'</qt>');item.setForeground(0,QColor(MUTED));item.setForeground(1,QColor(ACCENT))
        reconcile(self.messages,rows,self.row_key,update)
        if hasattr(self,'responsive'):self.responsive.columns()
        self.counter.setText(f'{self.offset+1}–{self.offset+len(rows)} из {count}' if rows else 'Пока нет сообщений по этим условиям')
        self.prev.setEnabled(self.offset>0);self.next.setEnabled(self.offset+len(rows)<count)
        if self.messages.selectedItems():self.show_message()
        else:self.preview.setPlainText('Выбери сообщение, чтобы прочитать его целиком.' if rows else 'Сообщений пока нет. Включи запись и подключи канал через Twitch или Chatterino.')
    @staticmethod
    def row_key(row):return row['user'],row['channel'],row['id']
    def show_message(self):
        selected=self.messages.selectedItems()
        if not selected:return
        r=selected[0].data(0,Qt.UserRole);self.preview.set_blocks(message_blocks(r,local_time))
    def copy_message(self):
        selected=self.messages.selectedItems()
        if selected:
            r=selected[0].data(0,Qt.UserRole);QApplication.clipboard().setText(reply_text(r.get('reply'))+f"{local_time(r['time_utc'])} #{r['channel']} {r['display_name']}: {r['text']}")
            self.detail.setText('Сообщение скопировано.')
    def export(self):
        if not self.selected:return
        filters=self.filters();destination,_=QFileDialog.getSaveFileName(self,'Экспорт выбранных сообщений',f'{self.selected}-archive.csv','CSV (*.csv)')
        if destination:self.worker.tasks.put(('export',(destination,filters)));self.detail.setText('Экспортируется вся выборка, включая другие страницы…')
    def appearance_dialog(self):
        d=Dialog(self,'Оформление','Плавное свечение и лёгкое сжатие кнопок при наведении. Размер текста следует масштабу экрана Windows.')
        motion=QCheckBox('Включить анимации');motion.setChecked(self.theme.animations);d.layout.addWidget(motion)
        motion.toggled.connect(lambda value:self.safe(lambda:self.theme.set_motion(value)))
        d.layout.addWidget(Button('Попробуй навести курсор',self.theme,icon='spark',primary=True))
        d.layout.addWidget(Button('Эмоуты 7TV',self.theme,self.emotes_dialog,primary=True))
        d.layout.addWidget(Button('О программе',self.theme,self.about_dialog))
        d.layout.addWidget(Button('Обновления',self.theme,self.updates.dialog))
        d.actions(self.theme,cancel=False);d.exec()
    def about_dialog(self):
        d=Dialog(self,'О программе',APP_NAME)
        d.layout.addWidget(label('Автор проекта: '+AUTHOR,13,bold=True))
        d.layout.addWidget(label('Версия '+VERSION+' · '+COPYRIGHT,10,True))
        d.layout.addWidget(Button('Проверить обновления',self.theme,self.updates.dialog))
        d.layout.addWidget(label('Постоянный архив сообщений, ответов и истории наказаний с поддержкой 7TV.',11))
        d.layout.addWidget(label('Самостоятельное дополнение для Chatterino+. Сведения о компонентах и их авторах — в папке licenses рядом с приложением.',9,True))
        d.actions(self.theme,cancel=False);d.exec()
    def emotes_dialog(self):
        d=Dialog(self,'Эмоуты 7TV','Наборы каналов и персональные эмоуты авторов находятся автоматически. Изменения Personal Emotes отслеживаются в реальном времени и дополнительно перепроверяются. Загруженные изображения сохраняются в выбранной папке данных архива.')
        enabled=QCheckBox('Показывать эмоуты 7TV');enabled.setChecked(self.emotes.enabled)
        motion=QCheckBox('Проигрывать анимированные эмоуты');motion.setChecked(self.emotes.motion)
        d.layout.addWidget(enabled);d.layout.addWidget(motion)
        def save():
            self.emotes.configure(enabled.isChecked(),motion.isChecked());self.theme.save()
        enabled.toggled.connect(lambda:self.safe(save));motion.toggled.connect(lambda:self.safe(save))
        status=label(self.emotes.status_text(),9,True);d.layout.addWidget(status)
        timer=QTimer(d);timer.setInterval(500);timer.timeout.connect(lambda:status.setText(self.emotes.status_text()));timer.start()
        d.layout.addWidget(Button('Обновить наборы сейчас',self.theme,lambda:self.emotes.refresh(force=True),primary=True))
        d.layout.addWidget(label('Для старых сообщений используется набор, доступный при первом просмотре. Уже сопоставленные эмоуты закрепляются за сообщением. При недоступной картинке остаётся её название.',9,True))
        d.actions(self.theme,cancel=False);d.exec()
    def import_channels(self):
        previous=list(self.control.channels);state=self.control.status()
        live=[c['name'] for c in state.get('channels',[]) if state.get('fresh') and c.get('attached')]
        combined=sorted((set(previous)|set(layout_channels(self.profile))|set(live))-self.control.excluded)
        if combined!=previous:
            self.control.channels=combined
            try:self.control.save()
            except Exception:self.control.channels=previous;raise
        update_combo(self.channel,['Все каналы']+sorted(set(self.control.channels)|set(self.twitch_config['channels'])))
    def channels_dialog(self):
        d=Dialog(self,'Каналы для записи','Запись отслеживаемых пользователей. Удаление останавливает её через Chatterino и Twitch; прежние сообщения сохраняются. Полная запись эфиров настраивается отдельно во вкладке «Повторы чата».')
        listing=QListWidget();listing.setMinimumHeight(230);d.layout.addWidget(listing)
        def refresh():
            listing.clear();status=self.control.status();state={c['name']:c.get('attached') for c in status.get('channels',[])}
            for ch in self.control.channels:listing.addItem('#'+ch+'  ·  '+('подключён' if state.get(ch) and status.get('fresh') else 'ожидает подключения'))
        def add():
            value=ask_text(self,'Добавить канал','Название канала Twitch:')
            if value is None:return
            channel=name(value);excluded=set(self.control.excluded);self.control.excluded.discard(channel);old=list(self.control.channels);self.control.channels=sorted(set(old+[channel]))
            try:self.control.save()
            except Exception:self.control.channels=old;self.control.excluded=excluded;raise
            self.import_channels();refresh()
        def remove():
            item=listing.currentItem()
            if item is None:return
            channel=name(item.text().split('  ·  ')[0]);old=list(self.control.channels);excluded=set(self.control.excluded)
            self.control.channels=[c for c in old if c!=channel];self.control.excluded.add(channel)
            try:self.control.save()
            except Exception:self.control.channels=old;self.control.excluded=excluded;raise
            previous=dict(self.twitch_config)
            try:
                self.twitch_config=settings_save(self.data/'twitch',{**previous,'channels':[c for c in previous['channels'] if c!=channel]})
                self.twitch.command('config',self.twitch_config)
            except Exception:
                self.control.channels=old;self.control.excluded=excluded;self.control.save();raise
            refresh()
        controls,cl=box(False);cl.addWidget(Button('Убрать канал',self.theme,lambda:self.safe(remove),icon='trash'))
        cl.addWidget(Button('Добавить канал',self.theme,lambda:self.safe(add),icon='plus'))
        cl.addWidget(Button('Обновить',self.theme,lambda:self.safe(lambda:(self.import_channels(),refresh()))));d.layout.addWidget(controls)
        d.actions(self.theme,cancel=False);refresh();d.exec()
    def trim_dialog(self):
        if self.control.pending_request():self.notice('Очистка чата','Предыдущий запрос ещё ожидает ответа плагина.');return
        TrimDialog(self).exec()
    def poll(self):
        try:
            changed=False
            while True:
                try:task,payload=self.worker.results.get_nowait()
                except queue.Empty:break
                if task=='query':
                    token,count,rows,counts,invalid=payload
                    if self.message_query.finish(token,(count,rows)):
                        if self.counts!=counts:self.counts=counts;self.refresh_users()
                        self.loading.clear();self.render_rows(count,rows)
                        if self.detail.text().startswith('Ошибка чтения сообщений:'):self.detail.clear()
                        changed=changed or self.message_query.dirty
                        if invalid:self.detail.setText(f'Пропущено неполных/повреждённых строк: {invalid}. Исходные файлы сохранены.')
                elif task=='changed':changed=True
                elif task=='user_card':self.user_view.render(*payload)
                elif task=='user_card_error':self.user_view.failed(*payload)
                elif task=='moderation':self.mod_view.render(*payload)
                elif task=='moderation_changed':self.mod_view.request();self.archive_context_changed.emit()
                elif task=='query_error':
                    token,error=payload
                    if self.message_query.finish(token):
                        self.loading.setText('Не удалось обновить · F5 — повторить')
                        if not self.rows:self.counter.setText('Не удалось загрузить сообщения')
                        self.detail.setText('Ошибка чтения сообщений: '+error)
                elif task=='moderation_error':self.mod_view.failed(*payload)
                elif task=='sync_error':self.detail.setText('Чтение новых записей будет повторено: '+payload)
                elif task=='export':self.detail.setText(f'Экспортировано сообщений: {payload[1]}. Файл: {payload[0]}')
                elif task=='export_error':self.detail.setText('Экспорт не завершён: '+payload)
                elif task=='error':self.worker_error=payload;self.detail.setText('Ошибка чтения архива: '+payload)
            while True:
                try:kind,value=self.shared.results.get_nowait()
                except queue.Empty:break
                self.shared_result.emit(kind,value)
            if changed:self.request_query()
            if self.sent_twitch_users!=self.control.users:
                self.sent_twitch_users=dict(self.control.users);self.twitch.command('users',self.control.users)
            twitch_state=self.twitch.snapshot();self.twitch_status.setText(twitch_state['text']+' · сохранено за сеанс: '+str(twitch_state['saved'])+' · удалений: '+str(twitch_state.get('deleted',0)))
            self.twitch_status.setStyleSheet('color:'+(GREEN if twitch_state['phase']=='connected' else MUTED)+';')
            state=self.control.status();color=GREEN
            self.emotes.set_channels(state.get('channels',[]),sorted(set(self.control.channels)|set(self.twitch_config['channels'])))
            if self.worker_error:text='Ошибка просмотра архива. Перезапусти панель после устранения причины.';color=RED
            elif state.get('error') and state.get('fresh'):text='Плагин сообщил об ошибке: '+state['error'].strip()[:160];color=RED
            elif not state.get('fresh'):text='Chatterino не подключён · его история модерации сейчас не пополняется';color=MUTED
            elif state.get('revision')!=self.control.revision:text='Настройки сохранены · ожидается подтверждение Chatterino…';color=ACCENT
            elif state.get('moderation_unparsed',0):text=f"Часть событий модерации не распознана: {state['moderation_unparsed']}. Запись остальных продолжается.";color=RED
            else:text=f"Плагин подключён · каналов: {sum(bool(c.get('attached')) for c in state.get('channels',[]))}/{len(self.control.channels)} · архив обновляется автоматически"
            self.status.setText(text);self.status.setStyleSheet('color:'+color+';');self.update_recording()
            try:
                answer=json.loads((self.data/'panel-ack.json').read_text(encoding='utf-8'));key=answer['id']+answer['status']
                if key!=self.last_ack:
                    self.last_ack=key;detail=answer.get('detail','Очистка: '+answer['status'])
                    self.detail.setText(detail.split('\n')[0]);self.detail.setToolTip('<qt>'+html.escape(detail).replace('\n','<br>')+'</qt>')
            except (OSError,ValueError,KeyError):pass
            now=time.monotonic()
            if now-self.last_layout_sync>5:self.last_layout_sync=now;self.import_channels()
            if self.pages.currentIndex() in (1,3) and now-self.last_moderation_query>3:
                self.last_moderation_query=now
                if self.pages.currentIndex()==1:self.mod_view.request()
                else:self.user_view.request()
        except (OSError,ValueError) as exc:self.detail.setText('Не удалось обновить данные: '+str(exc))
    def twitch_dialog(self):
        TwitchDialog(self).exec()
    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():return
        self.tray=QSystemTrayIcon(self.windowIcon(),self);self.tray.setToolTip(APP_NAME)
        menu=QMenu(self);menu.addAction('Открыть архив',self.restore_window)
        menu.addAction('Подключение Twitch',lambda:(self.restore_window(),self.twitch_dialog()))
        menu.addSeparator();menu.addAction('Выйти из архива',self.exit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason:self.restore_window() if reason in (QSystemTrayIcon.Trigger,QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()
    def restore_window(self):
        self.showNormal();self.raise_();self.activateWindow()
    def exit_app(self):
        self.exit_requested=True;self.close()
    def closeEvent(self,event):
        if self.tray and self.twitch_config['tray'] and not self.exit_requested and not self.closing:
            event.ignore();self.hide()
            if not getattr(self,'tray_hint_shown',False):
                self.tray_hint_shown=True;self.tray.showMessage(APP_NAME,'Архив работает в фоне. Для остановки выбери «Выйти из архива» в меню значка.',QSystemTrayIcon.Information,4000)
            return
        if not getattr(self,"replays_closed",False):
            self.replays_closed=True;self.replay_view.close()
            self.moderation_events.stop()
            self.shared.stopped.set()
            if getattr(self,'shared_window',None):self.shared_window.close()
        self.twitch.stopped.set()
        if not self.twitch.is_alive():self.replays.stopped.set()
        self.emotes.close()
        if self.worker.is_alive() or self.twitch.is_alive() or self.replays.is_alive() or self.shared.is_alive():
            self.worker.stopped.set()
            if not self.closing:self.closing=True;self.timer.stop();self.status.setText('Завершаем работу с архивом…')
            event.ignore();QTimer.singleShot(80,self.close);return
        self.theme.settings['size']=[self.width(),self.height()]
        try:self.theme.save()
        except OSError:pass
        event.accept()
        if self.exit_requested:
            if self.tray:self.tray.hide()
            # Closing an already hidden tray window does not emit lastWindowClosed.
            QTimer.singleShot(0,QApplication.instance().quit)

def default_profile():
    executable=Path(sys.executable if getattr(sys,'frozen',False) else __file__).resolve()
    for folder in [executable.parent,*list(executable.parents)[:3]]:
        candidate=folder/'Profile'/'Chatterino by Rish'
        if (candidate/'Settings').is_dir():return candidate
    from first_run import saved_profile
    return saved_profile()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--profile',type=Path);parser.add_argument('--demo',action='store_true')
    parser.add_argument('--smoke-test',action='store_true');parser.add_argument('--configure-profile',action='store_true')
    parser.add_argument('--secondary-screen',action='store_true');parser.add_argument('--twitch-settings',action='store_true')
    parser.add_argument('--apply-update',type=Path);parser.add_argument('--update-health',type=Path);parser.add_argument('--update-nonce');args=parser.parse_args()
    if args.apply_update:
        from update_apply import apply_plan
        return apply_plan(args.apply_update)
    profile=args.profile or default_profile()
    if args.demo and not args.profile:parser.error('--demo requires an explicit disposable --profile')
    app=QApplication(sys.argv[:1]);app.setApplicationName(APP_NAME);app.setApplicationDisplayName(APP_NAME);app.setOrganizationName(AUTHOR);app.setApplicationVersion(VERSION);install_style(app)
    if profile is None:
        from first_run import choose_profile
        profile=choose_profile()
        if profile is None:return 0
    data=profile/'Plugins'/'history-tools'/'data'
    if args.configure_profile:
        try:
            path=profile/'Settings'/'settings.json';settings=json.loads(path.read_text(encoding='utf-8-sig'))
            settings.setdefault('misc',{}).setdefault('scrollback',{})['splitLimit']=100000
            atomic_write(path,json.dumps(settings,ensure_ascii=False,indent=4)+'\n');control=Control(data);control.keep=1000;control.save();return 0
        except Exception as exc:atomic_write(data/'configure-error.txt',str(exc));return 1
    lock=None;window=None
    def exception_hook(kind,value,tb):
        try:atomic_write(data/'panel-error.txt',''.join(traceback.format_exception(kind,value,tb)))
        except OSError:pass
        if window:window.detail.setText('Ошибка интерфейса: '+str(value))
    sys.excepthook=exception_hook
    try:
        if not (profile/'Settings').is_dir():raise ValueError('Профиль Chatterino не найден: '+str(profile))
        data.mkdir(parents=True,exist_ok=True);lock=(data/'panel.lock').open('a+b');lock.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise ValueError('Панель для этого архива уже открыта. Найди её на панели задач или в трее Windows (значки возле часов).') from None
        window=App(profile,args.demo,network=not args.smoke_test)
        if args.secondary_screen:
            screens=[screen for screen in app.screens() if screen!=app.primaryScreen()]
            if screens:
                area=screens[0].availableGeometry();window.resize(min(window.width(),area.width()-40),min(window.height(),area.height()-60))
                window.move(area.x()+20,area.y()+30)
        window.show()
        if args.update_health and args.update_nonce:
            from update_apply import confirm_health
            QTimer.singleShot(6000,lambda:confirm_health(data,args.update_health,args.update_nonce,window))
        if args.twitch_settings:QTimer.singleShot(300,window.twitch_dialog)
        if args.smoke_test:
            def finish():
                atomic_write(data/'smoke-result.json',json.dumps({'product':APP_NAME,'author':AUTHOR,'version':VERSION,'title':window.windowTitle(),'heading':window.app_heading.text(),'widgets':len(window.findChildren(QWidget)),'users':list(window.control.users),
                    'channels':window.control.channels,'replay_channels':window.replays.config['channels'],'replay_tab':window.replay_tab.text(),'user_tab':window.user_tab.text(),'page_count':window.pages.count(),'screen':window.screen().name(),'position':[window.x(),window.y()],'rows':len(window.rows),'error':window.worker_error,'font':app.font().family(),
                    'animations':window.theme.animations,'renderer':'Qt '+__import__('PySide6').__version__},ensure_ascii=False));window.close()
            QTimer.singleShot(3200,finish)
        return app.exec()
    except Exception as exc:
        # Startup errors must remain visible even before a profile window exists.
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None,APP_NAME,str(exc));return 1
    finally:
        if lock:lock.close()

if __name__=='__main__':raise SystemExit(main())
