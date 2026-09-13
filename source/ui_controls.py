"""Keyboard-friendly channels and full-row animated event switches."""
from PySide6.QtCore import Qt,QEvent,QVariantAnimation,QEasingCurve,QRectF,QSize
from PySide6.QtGui import QPainter,QColor,QPen,QIcon
from PySide6.QtWidgets import QComboBox,QCompleter,QCheckBox,QApplication

class AnimatedCheck(QCheckBox):
    def __init__(self,text,theme):
        super().__init__(text);self.theme=theme;self.progress=0.;self.setMinimumHeight(38);self.setMinimumWidth(255)
        self.setCursor(Qt.PointingHandCursor)
        self.motion=QVariantAnimation(self);self.motion.setDuration(130);self.motion.setEasingCurve(QEasingCurve.OutCubic)
        self.motion.valueChanged.connect(self.frame);self.toggled.connect(self.animate)
    def sizeHint(self):return QSize(max(255,self.fontMetrics().horizontalAdvance(self.text())+58),38)
    def hitButton(self,pos):return self.rect().contains(pos)
    def frame(self,value):self.progress=float(value);self.update()
    def animate(self,checked):
        self.motion.stop()
        if not self.theme.animations:self.frame(float(checked));return
        self.motion.setStartValue(self.progress);self.motion.setEndValue(float(checked));self.motion.start()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHints(QPainter.Antialiasing|QPainter.TextAntialiasing)
        if self.underMouse() or self.hasFocus():p.fillRect(self.rect(),QColor('#30283e'))
        r=QRectF(12,(self.height()-18)/2,18,18);p.setPen(QPen(QColor('#c2a7f5' if self.isChecked() else '#82768f'),1.4));p.setBrush(QColor('#655080' if self.isChecked() else '#202029'));p.drawRoundedRect(r,4,4)
        p.save();p.setOpacity(self.progress);p.setPen(QPen(QColor('#f6efff'),2,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin));p.drawLine(r.left()+4,r.top()+9,r.left()+8,r.top()+13);p.drawLine(r.left()+8,r.top()+13,r.left()+15,r.top()+5);p.restore()
        p.setFont(self.font());p.setPen(QColor('#eeedf5'));p.drawText(self.rect().adjusted(42,0,-10,0),Qt.AlignVCenter|Qt.AlignLeft,self.text());p.end()

class ChannelCombo(QComboBox):
    def __init__(self,values):
        super().__init__();self.addItems(values);self.setEditable(True);self.setInsertPolicy(QComboBox.NoInsert)
        self.setMinimumWidth(120);self.setMaxVisibleItems(10);self.profiles=None;self.matches=[];self.cycle=-1
        self.completer().setCaseSensitivity(Qt.CaseInsensitive);self.completer().setFilterMode(Qt.MatchContains);self.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.completer().popup().setProperty('completionPopup',True);self.completer().popup().setFont(QApplication.font());self.completer().popup().setMinimumWidth(260)
        self.lineEdit().setPlaceholderText('Поиск канала…');self.lineEdit().installEventFilter(self)
        self.completer().popup().installEventFilter(self);self.lineEdit().textEdited.connect(self.edited)
    def edited(self,text):self.matches=[self.itemText(i) for i in range(self.count()) if text.casefold() in self.itemText(i).casefold()];self.cycle=-1
    def bind_profiles(self,profiles):self.profiles=profiles;profiles.changed.connect(self.refresh_icons);self.refresh_icons()
    def refresh_icons(self,*_):
        if not self.profiles:return
        for i in range(self.count()):
            login=self.itemText(i)
            if self.profiles.valid(login):
                self.profiles.ensure(login);pix=self.profiles.icon(login)
                if pix:self.setItemIcon(i,QIcon(pix))
        self.setIconSize(QSize(22,22))
    def showPopup(self):self.refresh_icons();super().showPopup()
    def eventFilter(self,obj,event):
        if event.type()==QEvent.KeyPress:
            key=event.key()
            if key in (Qt.Key_Tab,Qt.Key_Backtab):
                if not self.matches:self.edited(self.lineEdit().text())
                if self.matches:
                    step=-1 if key==Qt.Key_Backtab or event.modifiers()&Qt.ShiftModifier else 1
                    self.cycle=(self.cycle+step)%len(self.matches);text=self.matches[self.cycle]
                    self.lineEdit().setText(text);self.completer().setCompletionPrefix(text);self.completer().complete();return True
            if key in (Qt.Key_Return,Qt.Key_Enter):
                popup=self.completer().popup();index=popup.currentIndex()
                text=self.matches[self.cycle] if self.matches and self.cycle>=0 else (self.matches[0] if len(self.matches)==1 else (index.data() if popup.isVisible() and index.isValid() else self.lineEdit().text()))
                i=self.findText(text or '',Qt.MatchFixedString)
                if i>=0:
                    self.setCurrentIndex(i);self.lineEdit().setText(self.itemText(i));popup.hide();self.matches=[];self.cycle=-1;return True
        return super().eventFilter(obj,event)
