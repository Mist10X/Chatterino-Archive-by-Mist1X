from ui_controls import ChannelCombo
"""Chat-only replay window and a separate recordings library."""
import datetime as dt
import hashlib
import queue
import time
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QColor,QFont,QTextCursor,QTextCharFormat,QTextBlockFormat,QShortcut,QKeySequence
from PySide6.QtWidgets import QMainWindow,QLineEdit,QSlider,QTreeWidgetItem,QCheckBox,QMessageBox,QDialog,QHeaderView
from theme import box,card,scroll,label,Button,combo,table,ask_text,Dialog,update_combo,MUTED
from stable_tree import reconcile,set_texts
from emote_widgets import EmoteBrowser,OBJECT,GROUP,HEIGHT,tooltip_text
from seven_tv_store import groups
from replay_store import Reader,sessions,session_path,utc
from replay_media import make_view_media
from storage import name

def timing(milliseconds):
    seconds=max(0,int(milliseconds/1000));return f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}'

def date_text(at):return dt.datetime.fromtimestamp(at/1000).strftime('%d.%m.%Y %H:%M')

def event_text(row):
    user=row.get('user') or 'Пользователь'
    if row['kind']=='timeout':return f"{user} получил мут на {row.get('duration',0)} с."
    if row['kind']=='ban':return user+' забанен.'
    if row['kind']=='delete':return 'Удалено сообщение '+user+'.'
    if row['kind']=='clear':return 'Чат очищен модерацией.'
    return row.get('text','')

class ChatBrowser(EmoteBrowser):
    def __init__(self,manager):
        super().__init__(manager);self.rows=[];self.dimmed=set();self.start=0;self.stream_time=True
        self.highlight_id=None
        self.document().setUndoRedoEnabled(False)
        self.setStyleSheet('QTextBrowser {background:#111116;border:0;border-radius:0;padding:2px;color:#eeedf5;}')
        f=QFont(self.font());f.setPointSizeF(11);f.setWeight(QFont.DemiBold);self.setFont(f)
        self.frame_pending=False;self.frame_timer=QTimer(self);self.frame_timer.setInterval(33)
        self.frame_timer.timeout.connect(self.paint_frame);self.frame_timer.start()
    def frame_changed(self,key):
        if key in self.asset_keys and self.isVisible():self.frame_pending=True
    def paint_frame(self):
        if self.frame_pending:
            self.frame_pending=False
            if self.isVisible():self.viewport().update()
    def refresh_emotes(self):
        self.document().markContentsDirty(0,self.document().characterCount());self.viewport().update()
    def show_rows(self,rows,dimmed,start,stream_time):
        # Playback keeps a rolling window. Avoid rebuilding unchanged QTextBlocks.
        rows=list(rows);dimmed=set(dimmed)
        config=(start,stream_time,self.highlight_id)
        if rows==self.rows and dimmed==self.dimmed and config==getattr(self,'render_config',None):return
        old_rows=self.rows;old_dim=self.dimmed
        overlap=0;drop=0
        if rows and old_rows and config==getattr(self,'render_config',None):
            old_ids=[r['seq'] for r in old_rows];new_ids=[r['seq'] for r in rows]
            if new_ids[0] in old_ids:
                drop=old_ids.index(new_ids[0]);overlap=min(len(old_ids)-drop,len(new_ids))
                if old_ids[drop:drop+overlap]!=new_ids[:overlap]:overlap=0
                if drop+overlap!=len(old_ids):overlap=0
        doc=self.document();cursor=QTextCursor(doc)
        bar=self.verticalScrollBar();follow=not old_rows or bar.value()>=bar.maximum()-3
        old_scroll=bar.value();removed_height=0
        if overlap and drop:
            removed_height=doc.documentLayout().blockBoundingRect(doc.findBlockByNumber(drop)).top()
        self.asset_keys=set();self.setUpdatesEnabled(False);doc.setLayoutEnabled(False)
        try:
            cursor.beginEditBlock()
            if not overlap:
                doc.clear();cursor=QTextCursor(doc)
                for i,row in enumerate(rows):
                    if i:cursor.insertBlock()
                    self.insert_row(cursor,row,i,dimmed,start,stream_time)
            else:
                if drop:
                    cursor.setPosition(0);cursor.setPosition(doc.findBlockByNumber(drop).position(),QTextCursor.KeepAnchor);cursor.removeSelectedText()
                for i in range(overlap):
                    row=rows[i];old=old_rows[drop+i]
                    if row!=old or (row['seq'] in dimmed)!=(row['seq'] in old_dim):
                        block=doc.findBlockByNumber(i);cursor.setPosition(block.position());cursor.movePosition(QTextCursor.EndOfBlock,QTextCursor.KeepAnchor);cursor.removeSelectedText()
                        self.insert_row(cursor,row,i,dimmed,start,stream_time)
                cursor.movePosition(QTextCursor.End)
                for i in range(overlap,len(rows)):
                    cursor.insertBlock();self.insert_row(cursor,rows[i],i,dimmed,start,stream_time)
            cursor.endEditBlock()
            # Collect retained refs too, without opening files or activating animations.
            for row in rows:
                refs=[*row.get('emote_refs',{}).values(),*row.get('badge_refs',[]),*(row.get('reply') or {}).get('emote_refs',{}).values()]
                self.asset_keys.update(ref['key'] for ref in refs)
            self.rows=rows;self.dimmed=dimmed;self.start=start;self.stream_time=stream_time;self.render_config=config
        finally:
            doc.setLayoutEnabled(True);self.setUpdatesEnabled(True)
        if follow or not overlap:bar.setValue(bar.maximum())
        else:bar.setValue(max(0,int(old_scroll-removed_height)))
        if self.highlight_id is not None:
            for i,row in enumerate(rows):
                if row.get('seq')==self.highlight_id:
                    cursor=QTextCursor(doc.findBlockByNumber(i));self.setTextCursor(cursor);self.ensureCursorVisible();break
    def insert_row(self,cursor,row,i,dimmed,start,stream_time):
        bf=QTextBlockFormat();bf.setTopMargin(2);bf.setBottomMargin(3)
        bf.setBackground(QColor('#15151d' if row.get('seq',i)%2==0 else '#111116'));cursor.setBlockFormat(bf)
        if row.get('seq')==self.highlight_id:bf.setBackground(QColor('#382446'));cursor.setBlockFormat(bf)
        dim=row.get('seq') in dimmed;normal='#85818e' if dim else '#eeedf5'
        def text(value,color=normal,bold=False,small=False):
            fmt=QTextCharFormat();f=QFont(self.font());f.setWeight(QFont.DemiBold if bold or not small else QFont.Normal)
            if small:f.setPointSizeF(10)
            fmt.setFont(f);fmt.setForeground(QColor(color));cursor.insertText(value,fmt)
        def parts(value,refs,height=27):
            for part in groups(value,refs):
                if 'emotes' not in part:text(part['text']);continue
                fmt=QTextCharFormat();fmt.setObjectType(OBJECT);fmt.setProperty(GROUP,dict(part,dimmed=dim));fmt.setProperty(HEIGHT,height)
                fmt.setFont(self.font());fmt.setForeground(QColor(normal));fmt.setToolTip(tooltip_text(part['text']))
                cursor.insertText('\ufffc',fmt);self.asset_keys.update(x['key'] for x in part['emotes'])
        if row['kind']=='message':
            reply=row.get('reply')
            if reply:
                text('Ответ для @'+(reply.get('display_name') or reply.get('user') or '?')+': ','#a49cb9',small=True)
                if reply.get('state')=='available':parts(reply.get('text',''),reply.get('emote_refs',{}),21)
                else:text('исходный текст недоступен','#a49cb9',small=True)
                cursor.insertText('\u2028')
        stamp=timing(row['at']-start) if stream_time else dt.datetime.fromtimestamp(row['at']/1000).strftime('%H:%M:%S')
        text(stamp+'  ','#817a96',small=True)
        if row['kind']!='message':
            text(event_text(row),'#a195b9')
            for message in row.get('ban_context',[]):
                cursor.insertText('\u2028')
                body=message['text'].replace('\r\n','\n').replace('\r','\n').replace('\n','\u2028').replace('\u2029','\u2028')
                text(('AutoMod · задержано: ' if message.get('automod') else 'Перед баном: ')+body,'#d8ba83',small=True)
            return
        for badge in row.get('badge_refs',[]):
            # Badges are indivisible images, never tokenized names or tooltips.
            fmt=QTextCharFormat();fmt.setObjectType(OBJECT)
            fmt.setProperty(GROUP,{'text':'','emotes':[badge],'badge':True,'dimmed':dim});fmt.setProperty(HEIGHT,18)
            fmt.setFont(self.font());cursor.insertText('\ufffc',fmt);self.asset_keys.add(badge['key'])
        color=row.get('color','')
        if not QColor(color).isValid():color=['#df91dc','#65c9eb','#94d376','#efa86e'][int(hashlib.sha256(row.get('user','').encode()).hexdigest()[:2],16)%4]
        if dim:color='#77717e'
        text((row.get('display_name') or row.get('user',''))+': ',color,bold=True)
        parts(row.get('text',''),row.get('emote_refs',{}))
    def wheelEvent(self,event):
        bar=self.verticalScrollBar();delta=event.angleDelta().y()
        if getattr(self,'navigate',None) and ((delta>0 and bar.value()==bar.minimum()) or (delta<0 and bar.value()==bar.maximum())):
            self.navigate(-1 if delta>0 else 1);event.accept();return
        super().wheelEvent(event)

class AsyncQueries:
    def __init__(self):self.pool=ThreadPoolExecutor(max_workers=1);self.results=queue.Queue();self.closed=False
    def submit(self,key,fn):
        if self.closed:return
        future=self.pool.submit(fn)
        def done(f):
            try:self.results.put((key,f.result(),None))
            except Exception as exc:self.results.put((key,None,str(exc)))
        future.add_done_callback(done)
    def close(self):self.closed=True;self.pool.shutdown(wait=True,cancel_futures=True)

class ReplayWindow(QMainWindow):
    def __init__(self,app,meta):
        super().__init__(app,Qt.Window);self.app=app;self.theme=app.theme;self.meta=meta;self.data=app.data;self.key=meta['key']
        self.setAttribute(Qt.WA_DeleteOnClose);self.setWindowTitle('#'+meta['channel']+' — '+date_text(meta['started_at'])+' · Повтор чата')
        self.setWindowIcon(app.windowIcon());self.resize(820,900);self.setMinimumSize(560,420)
        screen=app.screen();geo=screen.availableGeometry();self.move(geo.x()+max(0,(geo.width()-820)//2),geo.y()+20)
        self.media=make_view_media(self.data,session_path(self.data,self.key),self.theme.settings,self)
        self.query=AsyncQueries();self.token=0;self.rows=[];self.dim=set();self.playing=False;self.busy=False;self.first=meta['first_recorded'];self.last=meta['last_recorded'];self.current=self.first
        root,layout=box();layout.setSpacing(0);self.setCentralWidget(root)
        self.controls,cl=box(margins=8)
        line,ll=box(False);self.play=Button('Воспроизвести',self.theme,self.toggle_play,icon='play');ll.addWidget(self.play)
        self.speed=combo(['×1','×2','×4']);self.speed.setMinimumWidth(65);ll.addWidget(self.speed)
        self.clock=combo(['Тайминг стрима','Время на часах']);ll.addWidget(self.clock)
        self.shade=QCheckBox('Показывать модерацию');self.shade.setChecked(True);cl.addWidget(line);cl.addWidget(self.shade)
        nav,nl=box(False);nl.addWidget(Button('Раньше',self.theme,lambda:self.page(-1),icon='left'))
        self.position=QLineEdit();self.position.setPlaceholderText('ЧЧ:ММ:СС');self.position.setMinimumWidth(90);nl.addWidget(self.position)
        nl.addWidget(Button('Перейти',self.theme,self.jump));nl.addWidget(Button('Позже',self.theme,lambda:self.page(1),icon='right'));cl.addWidget(nav)
        self.slider=QSlider(Qt.Horizontal);cl.addWidget(self.slider)
        find,fl=box(False);self.nick=QLineEdit();self.nick.setPlaceholderText('Ник');self.nick.setMaximumWidth(180)
        self.search=QLineEdit();self.search.setPlaceholderText('Найти текст в записи…');fl.addWidget(self.nick);fl.addWidget(self.search,1);fl.addWidget(Button('Найти',self.theme,self.find));cl.addWidget(find)
        self.hint=label('',9,True);cl.addWidget(self.hint);layout.addWidget(self.controls)
        top,tl=box(False);tl.setContentsMargins(8,0,8,0);tl.addWidget(label('#'+meta['channel'],10,bold=True),1)
        tl.addWidget(Button('Управление',self.theme,lambda:self.controls.setVisible(not self.controls.isVisible())));layout.addWidget(top)
        self.chat=ChatBrowser(self.media);layout.addWidget(self.chat,1)
        self.chat.navigate=lambda direction:self.page(direction) if not self.busy else None
        self.clock.currentIndexChanged.connect(self.redraw);self.shade.toggled.connect(self.redraw)
        self.slider.sliderReleased.connect(lambda:self.seek(meta['started_at']+self.slider.value()*1000))
        self.position.returnPressed.connect(self.jump);self.search.returnPressed.connect(self.find);self.nick.returnPressed.connect(self.find)
        self.timer=QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self.tick);self.timer.start();self.last_tick=time.monotonic();self.last_query=0
        QShortcut(QKeySequence('Ctrl+F'),self,activated=lambda:(self.controls.show(),self.search.setFocus()))
        QShortcut(QKeySequence('Escape'),self,activated=lambda:self.controls.show())
        self.context_pending=False;self.context_dirty=False;self.app.shared_result.connect(self.shared_update)
        self.app.archive_context_changed.connect(self.index_updated)
        self.request('initial',self.first)
    def request(self,mode,at,seq=0):
        self.token+=1;token=self.token;self.busy=True
        self.chat.highlight_id=seq if mode=='context' else None
        playing=self.playing
        def read():
            reader=Reader(self.data,self.key)
            try:
                first,last,count=reader.bounds()
                if mode in ('prev','next'):rows=reader.adjacent(at,seq,-1 if mode=='prev' else 1)
                elif mode=='context':
                    prior=reader.adjacent(at,seq+1,-1,100);after=reader.adjacent(at,seq,1,150);rows=prior+after
                elif mode=='initial':rows=reader.page(first,'after')
                else:rows=reader.page(at,'before')
                cutoff=at if playing or mode=='seek' else last
                from replay_context import enrich_bans
                rows=enrich_bans(self.data,rows)
                dim=reader.dimmed(rows,cutoff)
                return rows,dim,first,last,count,at,mode
            finally:reader.close()
        self.query.submit(token,read)
    def redraw(self):
        self.chat.show_rows([r for r in self.rows if self.shade.isChecked() or r['kind']=='message'],self.dim if self.shade.isChecked() else set(),self.meta['started_at'],self.clock.currentIndex()==0)
    def seek(self,at):
        self.current=max(self.first,min(self.last,at));self.request('seek',self.current)
    def jump(self):
        try:
            parts=[int(x) for x in self.position.text().strip().split(':')]
            if len(parts)!=3 or min(parts)<0 or parts[1]>59 or parts[2]>59:raise ValueError()
            self.playing=False;self.play.setText('Воспроизвести');self.seek(self.meta['started_at']+(parts[0]*3600+parts[1]*60+parts[2])*1000)
        except ValueError:self.hint.setText('Введи тайминг от начала стрима: ЧЧ:ММ:СС.')
    def toggle_play(self):
        self.playing=not self.playing;self.play.setText('Пауза' if self.playing else 'Воспроизвести');self.last_tick=time.monotonic()
        if self.playing:
            if self.current>=self.last:self.current=self.first
            self.seek(self.current)
    def page(self,direction):
        self.playing=False;self.play.setText('Воспроизвести')
        if self.rows:
            r=self.rows[0 if direction<0 else -1];self.request('prev' if direction<0 else 'next',r['at'],r['seq'])
    def find(self,after=0):
        try:user=name(self.nick.text()) if self.nick.text().strip() else ''
        except ValueError:self.hint.setText('Проверь написание ника.');return
        text=self.search.text()
        if not text and not user:self.hint.setText('Введи ник или часть сообщения.');return
        def read():
            reader=Reader(self.data,self.key)
            try:return reader.search(text,user,after)
            finally:reader.close()
        self.query.submit('search',read)
    def show_search(self,rows):
        d=Dialog(self,'Найденные сообщения','Выбери сообщение, чтобы открыть его в полной ленте.');d.resize(850,600)
        listing=table(['Тайминг','Ник','Сообщение'],[110,150]);d.layout.addWidget(listing)
        for row in rows:
            item=QTreeWidgetItem([timing(row['at']-self.meta['started_at']),row.get('display_name') or row['user'],row['text']]);item.setData(0,Qt.UserRole,row);listing.addTopLevelItem(item)
        def open_row():
            item=listing.currentItem()
            if item:
                r=item.data(0,Qt.UserRole);self.playing=False;self.play.setText('Воспроизвести');self.request('context',r['at'],r['seq']);d.accept()
        listing.itemDoubleClicked.connect(lambda *_:open_row())
        if len(rows)==100:d.layout.addWidget(Button('Следующие результаты',self.theme,lambda:(d.accept(),self.find(rows[-1]['seq']))))
        d.actions(self.theme,accept='Открыть в чате',callback=open_row);d.exec()
    def shared_update(self,kind,value):
        if kind=='card' and any(r.get('kind')=='ban' and r.get('user')==value['user'] for r in self.rows):self.context_dirty=True
    def index_updated(self):
        if any(r.get('kind')=='ban' and not r.get('ban_context') for r in self.rows):self.context_dirty=True
    def tick(self):
        while True:
            try:key,result,error=self.query.results.get_nowait()
            except queue.Empty:break
            if isinstance(key,tuple) and key[0]=='context_update':
                self.context_pending=False
                if not error and key[1]==self.token:self.rows=result;self.redraw()
                continue
            if key!='search' and key!=self.token:continue
            if error:self.busy=False;self.hint.setText('Не удалось прочитать запись: '+error[:100]);continue
            if key=='search':self.show_search(result);continue
            if key!=self.token:continue
            self.busy=False;rows,self.dim,self.first,self.last,count,at,mode=result
            self.rows=rows
            if mode in ('prev','next') and rows:self.current=rows[-1]['at']
            elif mode=='context':self.current=at
            self.redraw();self.slider.setRange(0,max(1,(self.last-self.meta['started_at'])//1000));self.slider.setValue(max(0,int((self.current-self.meta['started_at'])//1000)))
            if mode=='next':self.chat.verticalScrollBar().setValue(0)
            if not self.position.hasFocus():self.position.setText(timing(self.current-self.meta['started_at']))
            self.hint.setText('' if rows else 'В этой части записи сообщений нет.')
        if self.context_dirty and not self.context_pending and not self.busy:
            self.context_dirty=False;self.context_pending=True
            from replay_context import enrich_bans
            rows=list(self.rows);self.query.submit(('context_update',self.token),lambda:enrich_bans(self.data,rows))
        for row in self.rows:
            if row.get('kind')=='ban' and not row.get('ban_context'):self.app.request_ban_context(row)
        now=time.monotonic();elapsed=now-self.last_tick;self.last_tick=now
        if self.playing and not self.slider.isSliderDown():
            self.current=min(self.last,self.current+elapsed*1000*[1,2,4][self.speed.currentIndex()])
            if not self.busy and now-self.last_query>.25:self.last_query=now;self.request('seek',int(self.current))
            if self.current>=self.last:self.playing=False;self.play.setText('Воспроизвести')
    def closeEvent(self,event):
        self.timer.stop();self.query.close();self.media.close();event.accept()

class ReplayView:
    def __init__(self,app):
        self.app=app;self.service=app.replays;self.windows={};self.query=AsyncQueries();self.busy=False;self.metas={}
        body,layout=box(margins=16);body.setMinimumWidth(760)
        header,hl=card();hl.addWidget(label('Повторы чата',20,bold=True))
        hl.addWidget(label('Выбери каналы для полной записи во время эфиров. Сохранённый чат можно листать или воспроизводить.',10,True))
        row,rl=box(False);self.channel=ChannelCombo(self.service.config['channels']);self.channel.bind_profiles(app.profiles);rl.addWidget(self.channel,1)
        rl.addWidget(Button('Добавить канал',app.theme,self.add,icon='plus'));rl.addWidget(Button('Убрать канал',app.theme,self.remove,icon='trash'));hl.addWidget(row)
        self.enabled=QCheckBox('Записывать чат во время стримов');self.enabled.setChecked(self.service.config['enabled']);hl.addWidget(self.enabled)
        self.enabled.toggled.connect(self.configure);self.state=label('',10,True);hl.addWidget(self.state);layout.addWidget(header)
        self.filter=ChannelCombo(['Все записанные каналы']);self.filter.bind_profiles(app.profiles);layout.addWidget(self.filter);self.filter.currentIndexChanged.connect(self.refresh)
        self.list=table(['Дата','Канал','Стрим','Сообщений','Размер'],[180,145,310,110,100]);self.list.header().setStretchLastSection(False);self.list.header().setSectionResizeMode(2,QHeaderView.Stretch);layout.addWidget(self.list,1)
        actions,al=box(False);al.addWidget(Button('Открыть повтор',app.theme,self.open,icon='play',primary=True));al.addWidget(Button('Удалить запись',app.theme,self.delete,icon='trash',danger=True));layout.addWidget(actions)
        self.list.itemDoubleClicked.connect(lambda *_:self.open());self.frame=scroll(body)
        self.timer=QTimer(app);self.timer.setInterval(1000);self.timer.timeout.connect(self.poll);self.timer.start();self.next_refresh=0
    def configure(self,*_):
        value={**self.service.config,'enabled':self.enabled.isChecked()};self.app.safe(lambda:self.service.configure(value));self.app.twitch.command('replay_changed')
    def add(self):
        value=ask_text(self.app,'Добавить канал в повторы','Название канала Twitch. Будет записываться весь его чат во время эфира.')
        if value is None:return
        def update():
            ch=name(value);config={**self.service.config,'channels':sorted(set(self.service.config['channels']+[ch]))};self.service.configure(config)
            update_combo(self.channel,config['channels']);self.channel.setCurrentText(ch);self.app.twitch.command('replay_changed')
        self.app.safe(update)
    def remove(self):
        ch=self.channel.currentText()
        if not ch:return
        def update():
            config={**self.service.config,'channels':[c for c in self.service.config['channels'] if c!=ch]};self.service.configure(config)
            update_combo(self.channel,config['channels']);self.app.twitch.command('replay_changed')
        self.app.safe(update)
    def refresh(self,*_):
        if self.busy:return
        self.busy=True;self.query.submit('sessions',lambda:sessions(self.app.data));self.next_refresh=time.monotonic()+5
    def poll(self):
        state=self.service.snapshot();self.state.setText(state['text']+(' · запись выключена' if not self.service.config['enabled'] else ''))
        if not self.app.twitch_config['enabled']:self.state.setText('Для записи повторов включи прямое получение сообщений в окне Twitch.')
        if state.get('dropped'):self.state.setText(self.state.text()+f" · пропущено при перегрузке: {state['dropped']}")
        while True:
            try:key,result,error=self.query.results.get_nowait()
            except queue.Empty:break
            self.busy=False
            if error:self.state.setText('Не удалось прочитать список записей.');continue
            self.metas={x['key']:x for x in result};update_combo(self.filter,['Все записанные каналы']+sorted({x['channel'] for x in result}))
            def update(item,m):
                set_texts(item,[date_text(m['started_at']),'#'+m['channel'],m['title'],str(m.get('messages',0)),f"{m['bytes']/1048576:.1f} МБ"])
                item.setData(0,Qt.UserRole,m['key']);item.setToolTip(2,m['title'])
                from responsive import format_replay
                format_replay(self,item,m)
            reconcile(self.list,[m for m in result if self.filter.currentIndex()==0 or m['channel']==self.filter.currentText()],lambda m:m['key'],update)
        while True:
            try:kind,value=self.service.results.get_nowait()
            except queue.Empty:break
            if kind=='error':self.state.setText(value)
            else:self.refresh()
        if time.monotonic()>=self.next_refresh:self.refresh()
    def open(self):
        item=self.list.currentItem()
        if not item:return
        key=item.data(0,Qt.UserRole)
        if key in self.windows:self.windows[key].show();self.windows[key].raise_();return
        def create():
            w=ReplayWindow(self.app,self.metas[key]);self.windows[key]=w;w.destroyed.connect(lambda:self.windows.pop(key,None));w.show()
        self.app.safe(create)
    def delete(self):
        item=self.list.currentItem()
        if not item:return
        key=item.data(0,Qt.UserRole);m=self.metas[key]
        if QMessageBox.question(self.app,'Удалить запись?',f"Удалить чат #{m['channel']} за {date_text(m['started_at'])} и его сохранённые смайлы?\nЕсли эфир ещё идёт, его запись не возобновится.",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        if key in self.windows:self.windows[key].close()
        self.service.commands.put(('delete',key))
    def close(self):
        self.timer.stop()
        for window in list(self.windows.values()):window.close()
        self.query.close()
