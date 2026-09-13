"""Qt vector controls. Animation never changes the layout or the hit target."""
from pathlib import Path
import json
import weakref
from PySide6.QtCore import Qt, QRectF, QSize, QVariantAnimation, QEasingCurve, QEvent
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (QAbstractButton, QApplication, QLabel, QFrame, QVBoxLayout,
    QHBoxLayout, QLineEdit, QComboBox, QTreeWidget, QAbstractItemView, QHeaderView,
    QTextBrowser, QScrollArea, QWidget, QDialog, QSizePolicy)
from storage import atomic_write

BG='#0e0e11'; SURFACE='#17171e'; CARD='#1d1d27'; TEXT='#eeedf5'
MUTED='#a09bab'; ACCENT='#c2a7f5'; GREEN='#86d6b0'; RED='#efa0b2'; BORDER='#34323f'

def mix(a,b,t):
    a,b=QColor(a),QColor(b)
    return QColor.fromRgbF(*(a.getRgbF()[i]*(1-t)+b.getRgbF()[i]*t for i in range(4)))

class Theme:
    def __init__(self,data):
        self.path=Path(data)/'panel-ui.json'; self.settings={}; self.buttons=weakref.WeakSet()
        try:self.settings=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        self.animations=self.settings.get('animations',True) is not False
    def set_motion(self,value):
        self.animations=bool(value);self.settings['animations']=self.animations
        for b in list(self.buttons):b.animate(b.underMouse() and b.isEnabled())
        self.save()
    def save(self):atomic_write(self.path,json.dumps(self.settings,ensure_ascii=False,indent=2))

def install_style(app):
    for path in Path(__file__).with_name('fonts').glob('*.ttf'):
        QFontDatabase.addApplicationFont(str(path))
    font=QFontDatabase.font('Archive Montserrat','SemiBold',10);font.setPointSizeF(10.5)
    font.setWeight(QFont.DemiBold)
    font.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font);app.setStyle('Fusion')
    palette=QPalette()
    for role,color in [(QPalette.Window,BG),(QPalette.WindowText,TEXT),(QPalette.Base,SURFACE),
            (QPalette.AlternateBase,'#1b1b23'),(QPalette.Text,TEXT),(QPalette.Button,CARD),
            (QPalette.ButtonText,TEXT),(QPalette.Highlight,'#423153'),(QPalette.HighlightedText,TEXT),
            (QPalette.ToolTipBase,CARD),(QPalette.ToolTipText,TEXT),(QPalette.PlaceholderText,MUTED)]:
        palette.setColor(role,QColor(color))
    app.setPalette(palette)
    app.setStyleSheet('''
        QWidget {color:#eeedf5;font-family:"Archive Montserrat";font-weight:600;} QMainWindow,QDialog {background:#0e0e11;}
        QLabel {background:transparent;} QLabel[muted="true"] {color:#a09bab;}
        QFrame[card="true"] {background:#17171e;border:1px solid #34323f;border-radius:14px;}
        QLineEdit,QComboBox {background:#202029;border:1px solid #3b3848;border-radius:9px;padding:9px 11px;selection-background-color:#57406e;min-height:20px;}
        QLineEdit:hover,QComboBox:hover {border-color:#87719e;}
        QLineEdit:focus,QComboBox:focus {border:1px solid #c2a7f5;}
        QComboBox::drop-down {width:24px;border:0;}
        QComboBox QAbstractItemView {background:#23212c;selection-background-color:#423153;border:1px solid #55465f;padding:6px;}
        QTreeWidget,QListWidget {font-family:"Archive Montserrat";font-size:11pt;font-weight:600;background:#17171e;alternate-background-color:#1b1b23;border:1px solid #34323f;border-radius:11px;outline:0;padding:5px;selection-background-color:#3d2c50;}
        QListWidget::item {padding:7px 6px;}
        QTreeWidget::item {padding:7px 6px;border:0;}
        QTreeWidget::item:hover {background:#2d263b;}
        QTreeWidget::item:selected {background:#423153;color:#f6efff;}
        QHeaderView::section {background:#202029;color:#b9b1c9;padding:10px 8px;border:0;border-bottom:1px solid #34323f;font-weight:600;}
        QTextBrowser {background:#17171e;border:1px solid #34323f;border-radius:12px;padding:12px;selection-background-color:#57406e;}
        QScrollArea {border:0;background:transparent;}
        QScrollBar:vertical {background:#131319;width:11px;margin:2px;}
        QScrollBar::handle:vertical {background:#51465f;min-height:28px;border-radius:4px;}
        QScrollBar:horizontal {background:#131319;height:11px;margin:2px;}
        QScrollBar::handle:horizontal {background:#51465f;min-width:28px;border-radius:4px;}
        QScrollBar::handle:hover {background:#9074ad;}
        QScrollBar::add-line,QScrollBar::sub-line {width:0;height:0;}
        QScrollBar::add-page,QScrollBar::sub-page {background:transparent;}
        QMenu {font-size:10.5pt;background:#202029;border:1px solid #55465f;padding:6px;} QMenu::item {padding:9px 16px;} QMenu::item:selected {background:#423153;} QMenu::separator {height:1px;background:#34323f;margin:5px;}
        QAbstractItemView[completionPopup="true"] {font-size:10.5pt;background:#202029;border:1px solid #55465f;selection-background-color:#423153;padding:4px;} QAbstractItemView[completionPopup="true"]::item {padding:8px 10px;min-height:24px;}
        QToolTip {background:#26212f;color:#eeedf5;border:1px solid #766087;padding:7px;border-radius:6px;}
    ''')

def draw_icon(p,kind,rect,color):
    p.save();p.translate(rect.x(),rect.y());p.scale(rect.width()/24,rect.height()/24)
    p.setPen(QPen(QColor(color),1.65,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin));p.setBrush(Qt.NoBrush)
    if kind=='plus':p.drawLine(12,5,12,19);p.drawLine(5,12,19,12)
    elif kind=='pause':p.drawLine(8,6,8,18);p.drawLine(16,6,16,18)
    elif kind=='play':p.drawLine(8,5,18,12);p.drawLine(18,12,8,19);p.drawLine(8,19,8,5)
    elif kind=='trash':
        p.drawRoundedRect(QRectF(6,7,12,14),2,2);p.drawLine(4,6,20,6);p.drawLine(9,3,15,3);p.drawLine(10,10,10,17);p.drawLine(14,10,14,17)
    elif kind=='folder':
        from PySide6.QtGui import QPainterPath
        path=QPainterPath();path.moveTo(3,8);path.lineTo(3,5);path.lineTo(10,5);path.lineTo(12,8);path.lineTo(21,8);path.lineTo(21,20);path.lineTo(3,20);path.closeSubpath();p.drawPath(path)
    elif kind=='copy':p.drawRoundedRect(QRectF(8,8,12,13),2,2);p.drawLine(5,16,3,16);p.drawLine(3,16,3,3);p.drawLine(3,3,16,3);p.drawLine(16,3,16,5)
    elif kind=='export':p.drawLine(12,3,12,15);p.drawLine(7,10,12,15);p.drawLine(12,15,17,10);p.drawLine(4,16,4,21);p.drawLine(4,21,20,21);p.drawLine(20,21,20,16)
    elif kind in ('left','right'):
        sign=1 if kind=='right' else -1;p.drawLine(12-sign*7,12,12+sign*7,12);p.drawLine(12+sign*2,7,12+sign*7,12);p.drawLine(12+sign*7,12,12+sign*2,17)
    elif kind=='users':p.drawEllipse(QRectF(8,3,8,8));p.drawArc(QRectF(4,13,16,15),0,180*16)
    elif kind=='shield':
        from PySide6.QtGui import QPainterPath
        path=QPainterPath();path.moveTo(12,2);path.lineTo(21,6);path.cubicTo(21,15,18,19,12,22);path.cubicTo(6,19,3,15,3,6);path.closeSubpath();p.drawPath(path);p.drawLine(12,7,12,13);p.drawPoint(12,17)
    elif kind=='spark':
        for a,b,c,d in [(12,3,12,7),(12,17,12,21),(3,12,7,12),(17,12,21,12),(6,6,8,8),(16,16,18,18),(6,18,8,16),(16,8,18,6)]:p.drawLine(a,b,c,d)
        p.drawEllipse(QRectF(9,9,6,6))
    else:
        p.drawRoundedRect(QRectF(3,4,18,16),3,3);p.drawLine(7,9,17,9);p.drawLine(7,13,17,13);p.drawLine(7,17,12,17)
    p.restore()

class Button(QAbstractButton):
    def __init__(self,text,theme,callback=None,icon='',primary=False,danger=False,parent=None):
        super().__init__(parent);self.setText(text);self.theme=theme;theme.buttons.add(self)
        self.kind=icon;self.primary=primary;self.danger=danger;self.hover=0.;self.active=False;self.focus_ring=False
        self.setCursor(Qt.PointingHandCursor);self.setFocusPolicy(Qt.StrongFocus);self.setMinimumHeight(44)
        self.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Fixed)
        self.setAccessibleName(text);self.anim=QVariantAnimation(self);self.anim.setDuration(160)
        self.anim.setEasingCurve(QEasingCurve.OutCubic);self.anim.valueChanged.connect(self._value)
        if callback:self.clicked.connect(lambda checked=False:callback())
        self.pressed.connect(self.update);self.released.connect(self.update)
    def sizeHint(self):
        from PySide6.QtGui import QFontMetrics
        font=QFont(self.font());font.setWeight(QFont.DemiBold)
        return QSize(QFontMetrics(font).horizontalAdvance(self.text())+38+(25 if self.kind else 0),44)
    def minimumSizeHint(self):return self.sizeHint()
    def _value(self,value):self.hover=float(value);self.update()
    def animate(self,on):
        self.anim.stop();target=1. if on else 0.
        if self.theme.animations:
            self.anim.setStartValue(self.hover);self.anim.setEndValue(target);self.anim.start()
        else:self._value(target)
    def enterEvent(self,event):self.animate(self.isEnabled());super().enterEvent(event)
    def leaveEvent(self,event):self.animate(False);super().leaveEvent(event)
    def focusInEvent(self,event):
        self.focus_ring=event.reason() in (Qt.TabFocusReason,Qt.BacktabFocusReason,Qt.ShortcutFocusReason)
        super().focusInEvent(event);self.update()
    def changeEvent(self,event):
        if event.type()==QEvent.EnabledChange and not self.isEnabled():self.animate(False)
        super().changeEvent(event)
    def visual_rect(self):
        r=QRectF(self.rect()).adjusted(3,3,-3,-3)
        shrink=(.03*self.hover + (.025 if self.isDown() else 0)) if self.theme.animations else 0
        return r.adjusted(r.width()*shrink/2,r.height()*shrink/2,-r.width()*shrink/2,-r.height()*shrink/2)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHints(QPainter.Antialiasing|QPainter.TextAntialiasing)
        r=self.visual_rect();h=self.hover if self.isEnabled() else 0
        if not self.isEnabled():p.setOpacity(.4)
        accent=RED if self.danger else ACCENT
        base='#33253f' if self.primary or self.active else '#22212a'
        if h:
            glow=QColor(accent);glow.setAlphaF(.08*h)
            for pad in (1,2,3):p.setPen(Qt.NoPen);p.setBrush(glow);p.drawRoundedRect(r.adjusted(-pad,-pad,pad,pad),12+pad,12+pad)
        p.setBrush(mix(base,'#533b69' if not self.danger else '#563040',h*.7))
        p.setPen(QPen(mix('#665175' if self.active else BORDER,accent,h*.8),1))
        p.drawRoundedRect(r,10,10)
        if self.hasFocus() and self.focus_ring:p.setPen(QPen(QColor(accent),1.4,Qt.DotLine));p.setBrush(Qt.NoBrush);p.drawRoundedRect(r.adjusted(2,2,-2,-2),8,8)
        font=QFont(self.font());font.setWeight(QFont.DemiBold)
        if self.theme.animations:font.setPointSizeF(font.pointSizeF()*(1-.03*h-(.025 if self.isDown() else 0)))
        p.setFont(font)
        width=p.fontMetrics().horizontalAdvance(self.text())+(25 if self.kind else 0)
        x=r.center().x()-width/2
        if self.kind:draw_icon(p,self.kind,QRectF(x,r.center().y()-9,18,18),accent if self.primary or self.danger else TEXT);x+=25
        p.setPen(QColor(accent if self.primary or self.danger or self.active else TEXT))
        p.drawText(QRectF(x,r.top(),r.right()-x,r.height()),Qt.AlignVCenter|Qt.AlignLeft,self.text());p.end()

class Avatar(QWidget):
    def __init__(self,text='',logo=False):
        super().__init__();self.text=text;self.logo=logo;self.profiles=None;self.login='';self.setFixedSize(48,48)
    def set_profile(self,profiles,login):
        self.profiles=profiles;self.login=login or '';self.text=self.login[:2];profiles.ensure(self.login);self.update()
    def paintEvent(self,e):
        p=QPainter(self);p.setRenderHints(QPainter.Antialiasing|QPainter.TextAntialiasing)
        p.setPen(QPen(QColor('#806398'),1));p.setBrush(QColor('#34243f'));p.drawEllipse(QRectF(3,3,42,42))
        if self.profiles and self.login and self.profiles.paint(p,QRectF(3,3,42,42),self.login):
            p.end();return
        if self.logo:draw_icon(p,'archive',QRectF(13,13,22,22),ACCENT)
        else:
            f=QFont(self.font());f.setPointSizeF(13);f.setWeight(QFont.DemiBold);p.setFont(f);p.setPen(QColor(ACCENT));p.drawText(self.rect(),Qt.AlignCenter,self.text[:2].upper())
        p.end()

def label(text='',size=None,muted=False,bold=False):
    w=QLabel(text);w.setTextFormat(Qt.PlainText);w.setProperty('muted',muted);w.setWordWrap(True)
    f=QFont(w.font())
    if size:
        size=max(10,size);f.setPointSizeF(size)
        w.setStyleSheet(f"font-size:{size}pt;")
    if bold:f.setWeight(QFont.DemiBold)
    w.setFont(f);return w

def box(vertical=True,parent=None,margins=0):
    w=QWidget(parent);layout=(QVBoxLayout if vertical else QHBoxLayout)(w)
    layout.setContentsMargins(margins,margins,margins,margins);layout.setSpacing(10);return w,layout

def card():
    w=QFrame();w.setProperty('card',True);l=QVBoxLayout(w);l.setContentsMargins(16,14,16,14);l.setSpacing(8);return w,l

def combo(values):
    w=QComboBox();w.addItems(values);w.setMinimumWidth(170);return w

def update_combo(w,values):
    current=w.currentText()
    if [w.itemText(i) for i in range(w.count())]==values:return
    w.blockSignals(True);w.clear();w.addItems(values)
    if current in values:w.setCurrentText(current)
    w.blockSignals(False)
    if hasattr(w,'refresh_icons'):w.refresh_icons()

def table(headers,widths=None):
    from stable_tree import StableTree
    w=StableTree();w.setHeaderLabels(headers);w.setRootIsDecorated(False);w.setUniformRowHeights(True)
    w.setAlternatingRowColors(True);w.setSelectionMode(QAbstractItemView.SingleSelection)
    w.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel);w.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    w.setMinimumHeight(135);w.header().setStretchLastSection(True)
    for i,width in enumerate(widths or []):w.setColumnWidth(i,width)
    return w

def preview():
    w=QTextBrowser();w.setOpenExternalLinks(False);w.setOpenLinks(False);w.setMinimumHeight(115)
    w.setLineWrapMode(QTextBrowser.WidgetWidth);return w

def scroll(content):
    w=QScrollArea();w.setWidgetResizable(True);w.setWidget(content);return w

class Dialog(QDialog):
    def __init__(self,parent,title,description=''):
        super().__init__(parent);self.setWindowTitle(title);self.setMinimumWidth(470)
        self.layout=QVBoxLayout(self);self.layout.setContentsMargins(22,22,22,20);self.layout.setSpacing(14)
        self.layout.addWidget(label(title,16,bold=True))
        if description:self.layout.addWidget(label(description,muted=True))
        self.error=label('',muted=True)
    def actions(self,theme,accept='Готово',callback=None,cancel=True):
        self.layout.addWidget(self.error);w,l=box(False)
        if cancel:l.addWidget(Button('Отмена',theme,self.reject))
        l.addStretch();l.addWidget(Button(accept,theme,callback or self.accept,primary=True));self.layout.addWidget(w)

def ask_text(parent,title,description):
    d=Dialog(parent,title,description);entry=QLineEdit();d.layout.addWidget(entry)
    d.actions(parent.theme,accept='Добавить');entry.returnPressed.connect(d.accept);entry.setFocus()
    return entry.text() if d.exec()==QDialog.Accepted else None
