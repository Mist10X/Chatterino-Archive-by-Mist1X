"""Connection warnings above every main page and application-owned dialog."""
from PySide6.QtCore import QObject,QEvent,QTimer
from PySide6.QtWidgets import QApplication,QMainWindow,QDialog,QFrame,QHBoxLayout
from shiboken6 import isValid
from theme import label,Button

def connection_warning(state,config,wanted):
    if not config.get('enabled'):return ('off','Twitch: запись отключена вручную.')
    phase=state.get('phase','off')
    if phase=='connected':
        missing=set(wanted)-set(state.get('joined',[]))
        if missing:
            names=', '.join('#'+ch for ch in sorted(missing)[:3])
            if len(missing)>3:names+=f' и ещё {len(missing)-3}'
            return ('warning',f'Twitch: подключено {len(set(wanted)-missing)} из {len(set(wanted))}. Ожидаем: {names}. Подключаем автоматически.')
        return None
    if phase in ('auth','off','auth_required'):return ('error',state.get('text') or 'Twitch: требуется вход. Новые сообщения напрямую не записываются.')
    if phase=='paused':return ('warning','Twitch: запись приостановлена. Проверь выбранные каналы.')
    if phase=='connecting':return ('warning','Twitch: подключаемся. Получение новых сообщений ещё не началось.')
    return ('error','Twitch: соединение потеряно. '+state.get('text','Проверь подключение.'))

class ConnectionAlerts(QObject):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.banners=[]
        QApplication.instance().installEventFilter(self);self.attach(app)
        self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.refresh);self.timer.start()
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Show and isinstance(obj,(QDialog,QMainWindow)) and obj is not self.app:
            parent=obj.parentWidget()
            while parent and parent is not self.app:parent=parent.parentWidget()
            if parent is self.app:self.attach(obj)
        return False
    def attach(self,window):
        if getattr(window,'connection_banner',None) is not None:return
        container=window.centralWidget() if isinstance(window,QMainWindow) else window
        layout=getattr(container,'layout',None) if container else None
        if callable(layout):layout=layout()
        if layout is None or not hasattr(layout,'insertWidget'):return
        banner=QFrame();bl=QHBoxLayout(banner);bl.setContentsMargins(12,7,12,7)
        text=label('',10,bold=True);bl.addWidget(text,1)
        retry=Button('Повторить сейчас',self.app.theme,self.retry_now);bl.addWidget(retry)
        button=Button('Подключение Twitch',self.app.theme,self.open_settings);bl.addWidget(button)
        layout.insertWidget(0,banner);window.connection_banner=banner;window.connection_retry=retry;self.banners.append((banner,text,retry));self.refresh()
    def retry_now(self):self.app.twitch.command('retry')
    def open_settings(self):
        from twitch_dialog import TwitchDialog
        for window in self.app.findChildren(TwitchDialog):
            if window.isVisible():window.raise_();window.activateWindow();return
        self.app.twitch_dialog()
    def refresh(self):
        self.banners=[pair for pair in self.banners if isValid(pair[0])]
        state=self.app.twitch.snapshot();warning=None if self.app.demo else connection_warning(state,self.app.twitch_config,self.app.twitch.wanted_channels())
        for banner,text,retry in self.banners:
            banner.setVisible(warning is not None)
            retry.setVisible(warning is not None and state.get('phase') in ('reconnecting','error','auth_required'))
            if warning:
                kind,value=warning
                if text.text()==value and banner.property('connectionKind')==kind:continue
                text.setText(value);banner.setProperty('connectionKind',kind)
                color={'off':'#33313e','warning':'#654d24','error':'#773344'}[kind]
                banner.setStyleSheet('QFrame {background:'+color+';border-radius:8px;}')
