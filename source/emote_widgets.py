"""Inline static/animated emotes in Qt text documents and message table cells."""
from PySide6.QtCore import QObject,Qt,QSize,QSizeF,QRectF,QPointF,QEvent
from PySide6.QtGui import (QPyTextObject,QTextFormat,QTextCharFormat,QTextBlockFormat,
    QTextCursor,QFont,QFontMetricsF,QColor,QKeySequence,QPalette,QPainter)
from PySide6.QtWidgets import QTextBrowser,QStyledItemDelegate,QStyleOptionViewItem,QStyle,QMenu,QToolTip
from seven_tv_store import groups

OBJECT=int(QTextFormat.UserObject)+17
GROUP=int(QTextFormat.UserProperty)+17
HEIGHT=GROUP+1

def tooltip_text(text):
    import html
    return '<qt>'+html.escape(text).replace('\n','<br>')+'</qt>'

def measured(group,manager,font,height,active=False):
    getter=manager.image if active else getattr(manager,'measure_image',manager.image)
    pictures=[getter(ref) for ref in group.get('emotes',[])]
    if pictures and all(pic is not None and not pic.isNull() for pic in pictures):
        base=pictures[0];width=height*base.width()/max(1,base.height())
        return min(width,height*6)+(4 if group.get('badge') else 0),height,pictures
    if group.get('badge'):return 0,0,None
    metrics=QFontMetricsF(font)
    return metrics.horizontalAdvance(group['text']),metrics.height(),None

def paint_group(painter,rect,group,manager,font,height,color):
    width,h,pictures=measured(group,manager,font,height,active=True)
    if pictures:
        painter.setRenderHint(QPainter.SmoothPixmapTransform,True)
        painter.save()
        if group.get('dimmed'):painter.setOpacity(painter.opacity()*.4)
        for pic in pictures:
            w=min(height*pic.width()/max(1,pic.height()),height*6)
            image_rect=QRectF(rect.x()+(width-w-(4 if group.get('badge') else 0))/2,rect.y()+(rect.height()-height)/2,w,height)
            painter.drawImage(image_rect,pic)
        painter.restore()
    else:
        painter.setFont(font);painter.setPen(QColor(color));painter.drawText(rect,Qt.AlignLeft|Qt.AlignVCenter,group['text'])
    return width

class EmoteObject(QPyTextObject):
    def __init__(self,manager,parent=None):super().__init__(parent);self.manager=manager
    def intrinsicSize(self,document,pos,format):
        group=format.property(GROUP) or {'text':''};height=float(format.property(HEIGHT) or 28)
        font=format.toCharFormat().font();font=font.resolve(document.defaultFont())
        w,h,_=measured(group,self.manager,font,height);return QSizeF(w,h+3)
    def drawObject(self,painter,rect,document,pos,format):
        char=format.toCharFormat();font=char.font().resolve(document.defaultFont());color=char.foreground().color()
        paint_group(painter,rect,format.property(GROUP) or {'text':''},self.manager,font,float(format.property(HEIGHT) or 28),color)

class EmoteBrowser(QTextBrowser):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;self.blocks=None;self.signature=None;self.asset_keys=set()
        self.handler=EmoteObject(manager,self);self.document().documentLayout().registerHandler(OBJECT,self.handler)
        self.setOpenExternalLinks(False);self.setOpenLinks(False);self.setMinimumHeight(115)
        self.viewport().setMouseTracking(True)
        self.manager.changed.connect(self.refresh_emotes);self.manager.frame.connect(self.frame_changed)
    def setPlainText(self,text):
        self.blocks=None;self.signature=None;self.asset_keys.clear();super().setPlainText(text)
    def set_blocks(self,blocks):
        if self.blocks==blocks:return
        self.blocks=blocks;self.signature=None;self.refresh_emotes()
    def refresh_emotes(self):
        if self.blocks is None:return
        resolved=[];self.asset_keys=set()
        for block in self.blocks:
            row=block.get('row')
            refs=self.manager.bind(row,block.get('slot','body')) if row is not None else {}
            resolved.append((block,groups(block.get('text',''),refs)))
            self.asset_keys.update(r['key'] for r in refs.values())
        # Asset arrival changes object sizes, while original text and selection stay stable.
        signature=str(resolved)
        if signature==self.signature:
            self.document().markContentsDirty(0,self.document().characterCount());self.viewport().update();return
        self.signature=signature;cursor_before=self.textCursor();selection=(cursor_before.anchor(),cursor_before.position());scroll=self.verticalScrollBar().value()
        self.document().clear();cursor=QTextCursor(self.document())
        for i,(block,parts) in enumerate(resolved):
            if i:cursor.insertBlock()
            style=block.get('style','body');bf=QTextBlockFormat();bf.setTopMargin(4);bf.setBottomMargin(8)
            if style=='quote':bf.setLeftMargin(12);bf.setRightMargin(6);bf.setBackground(QColor('#262030'))
            cursor.setBlockFormat(bf)
            fmt=QTextCharFormat();font=QFont(self.font());font.setPointSizeF(10 if style in ('meta','quote') else 11)
            if style in ('title','deletion'):font.setWeight(QFont.DemiBold)
            fmt.setFont(font);fmt.setForeground(QColor('#b7a9c9' if style in ('meta','quote') else '#eeedf5'))
            if style=='deletion':fmt.setForeground(QColor('#e9ac7a'))
            for part in parts:
                if 'emotes' in part:
                    object_format=QTextCharFormat(fmt);object_format.setObjectType(OBJECT);object_format.setProperty(GROUP,part);object_format.setProperty(HEIGHT,25 if style=='quote' else 30)
                    import html
                    object_format.setToolTip('<qt>'+html.escape(part['text'])+'</qt>');cursor.insertText('\ufffc',object_format)
                else:cursor.insertText(part['text'],fmt)
        maximum=self.document().characterCount()-1
        cursor.setPosition(min(selection[0],maximum));cursor.setPosition(min(selection[1],maximum),QTextCursor.KeepAnchor);self.setTextCursor(cursor);self.verticalScrollBar().setValue(scroll)
    def frame_changed(self,key):
        if key in self.asset_keys and self.isVisible():self.viewport().update()
    def emote_at(self,point):
        position=self.cursorForPosition(point).position()
        for pos in (position,position-1):
            if pos<0 or pos>=self.document().characterCount()-1:continue
            cursor=QTextCursor(self.document());cursor.setPosition(pos);start=self.cursorRect(cursor)
            cursor.setPosition(pos+1,QTextCursor.KeepAnchor);fmt=cursor.charFormat()
            group=fmt.property(GROUP)
            if cursor.selectedText()!='\ufffc' or not group:continue
            width,height,_=measured(group,self.manager,fmt.font().resolve(self.document().defaultFont()),float(fmt.property(HEIGHT) or 28))
            if QRectF(start.x(),start.y(),width,start.height()).contains(QPointF(point)):return None if group.get('badge') else group['text']
        return None
    def viewportEvent(self,event):
        if event.type()==QEvent.ToolTip:
            text=self.emote_at(event.pos())
            if text is not None:QToolTip.showText(event.globalPos(),tooltip_text(text),self.viewport());event.accept();return True
            QToolTip.hideText();event.ignore();return True
        return super().viewportEvent(event)
    def plain_range(self,start,end):
        cursor=QTextCursor(self.document());result=[]
        for pos in range(start,end):
            cursor.setPosition(pos);cursor.setPosition(pos+1,QTextCursor.KeepAnchor)
            text=cursor.selectedText()
            if text=='\ufffc':text=(cursor.charFormat().property(GROUP) or {}).get('text','')
            result.append(text.replace('\u2029','\n').replace('\u2028','\n'))
        return ''.join(result)
    def toPlainText(self):return self.plain_range(0,self.document().characterCount()-1)
    def copy(self):
        cursor=self.textCursor()
        if cursor.hasSelection():
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(self.plain_range(cursor.selectionStart(),cursor.selectionEnd()))
    def keyPressEvent(self,event):
        if event.matches(QKeySequence.Copy):self.copy();event.accept()
        else:super().keyPressEvent(event)
    def contextMenuEvent(self,event):
        menu=QMenu(self);copy=menu.addAction('Копировать',self.copy);copy.setEnabled(self.textCursor().hasSelection())
        menu.addAction('Выделить всё',self.selectAll);menu.exec(event.globalPos())

class EmoteDelegate(QStyledItemDelegate):
    def __init__(self,manager,tree):
        super().__init__(tree);self.manager=manager;self.tree=tree
        manager.changed.connect(tree.viewport().update);manager.frame.connect(lambda key:tree.viewport().update() if tree.isVisible() else None)
    def sizeHint(self,option,index):
        size=super().sizeHint(option,index);size.setHeight(max(42,size.height()));return size
    def emote_at(self,point,option,index):
        row=index.siblingAtColumn(0).data(Qt.UserRole)
        if not self.manager.enabled or not isinstance(row,dict) or 'text' not in row:return None
        opt=QStyleOptionViewItem(option);self.initStyleOption(opt,index);opt.text=''
        rect=QRectF(self.tree.style().subElementRect(QStyle.SE_ItemViewItemText,opt,self.tree)).adjusted(3,0,-3,0)
        if not rect.contains(QPointF(point)):return None
        x=rect.x()
        for part in groups(row['text'].replace('\n',' ↵ '),self.manager.bind(row)):
            width,_,_=measured(part,self.manager,opt.font,28)
            if QRectF(x,rect.y(),width,rect.height()).contains(QPointF(point)):
                return part['text'] if 'emotes' in part else None
            x+=width
        return None
    def helpEvent(self,event,view,option,index):
        if event.type()==QEvent.ToolTip:
            text=self.emote_at(event.pos(),option,index)
            if text is not None:QToolTip.showText(event.globalPos(),tooltip_text(text),view);return True
        return super().helpEvent(event,view,option,index)
    def paint(self,painter,option,index):
        if not self.manager.enabled:super().paint(painter,option,index);return
        row=index.siblingAtColumn(0).data(Qt.UserRole)
        if not isinstance(row,dict) or 'text' not in row:super().paint(painter,option,index);return
        opt=QStyleOptionViewItem(option);self.initStyleOption(opt,index);opt.text=''
        self.tree.style().drawControl(QStyle.CE_ItemViewItem,opt,painter,self.tree)
        rect=QRectF(self.tree.style().subElementRect(QStyle.SE_ItemViewItemText,opt,self.tree)).adjusted(3,0,-3,0)
        color=opt.palette.color(QPalette.HighlightedText if opt.state&QStyle.State_Selected else QPalette.Text)
        font=opt.font;refs=self.manager.bind(row);parts=groups(row['text'].replace('\n',' ↵ '),refs)
        painter.save();painter.setClipRect(rect);painter.setRenderHints(QPainter.Antialiasing|QPainter.TextAntialiasing|QPainter.SmoothPixmapTransform)
        x=rect.x()
        for part in parts:
            w,h,_=measured(part,self.manager,font,28)
            if x>=rect.right():break
            target=QRectF(x,rect.y(),w,rect.height());paint_group(painter,target,part,self.manager,font,28,color);x+=w
        painter.restore()

def message_blocks(row,local_time):
    result=[{'text':f"{local_time(row['time_utc'])}  ·  #{row['channel']}  ·  {row.get('display_name') or row.get('user','')}",'style':'meta'}]
    reply=row.get('reply')
    if reply:
        author=reply.get('display_name') or reply.get('user') or 'неизвестный автор'
        if reply.get('state')=='available':
            quoted=dict(reply);quoted['id']=row.get('id','');quoted['time_utc']=row.get('time_utc','');quoted['channel']=row['channel']
            result.append({'text':'↳ Исходное сообщение · '+author,'style':'meta'})
            result.append({'text':reply.get('text',''),'style':'quote','row':quoted,'slot':'reply'})
        else:result.append({'text':'↳ Исходное сообщение недоступно','style':'quote'})
    result.append({'text':row['text'],'row':row});return result
