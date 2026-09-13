"""Public Twitch identities; bounded async requests and reusable local thumbnails."""
import json,re,time,hashlib
from pathlib import Path
from urllib.parse import urlencode,urlparse
from PySide6.QtCore import QObject,Signal,QTimer,QUrl,QRectF,QPointF,Qt
from PySide6.QtGui import QPixmap,QImage,QPainter,QPainterPath,QColor
from PySide6.QtNetwork import QNetworkAccessManager,QNetworkRequest,QNetworkReply
from storage import atomic_write
from twitch_core import CLIENT_ID

class Profiles(QObject):
    changed=Signal(str)
    def __init__(self,data,token,online=True,parent=None):
        super().__init__(parent);self.root=Path(data)/'profiles';self.root.mkdir(exist_ok=True);self.token=token;self.online=online
        try:self.entries=json.loads((self.root/'identities.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):self.entries={}
        self.pending=set();self.inflight=set();self.failed={};self.pictures={};self.rounded={};self.images=set();self.replies=set();self.closed=False;self.image_failed={}
        self.net=QNetworkAccessManager(self);self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.flush)
        if online:self.timer.start()
    @staticmethod
    def valid(login):return isinstance(login,str) and re.fullmatch(r'[a-zA-Z0-9_]{1,25}',login) is not None
    def display(self,login,fallback=None):return self.entries.get(str(login).lower(),{}).get('display_name') or fallback or login
    def ensure(self,login):
        if not self.online or not self.valid(login) or self.closed:return
        login=login.lower();entry=self.entries.get(login,{})
        if time.time()-entry.get('checked',0)<21600:
            if entry.get('url') and (not entry.get('image') or not (self.root/entry['image']).exists()):self.download(login,entry.get('url',''))
            return
        if time.time()-self.failed.get(login,0)<120:return
        if login not in self.inflight:self.pending.add(login)
    def get(self,url,callback,token=None,limit=2_000_000):
        request=QNetworkRequest(QUrl(url));request.setTransferTimeout(8000)
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute,QNetworkRequest.ManualRedirectPolicy)
        if token:
            request.setRawHeader(b'Client-Id',CLIENT_ID.encode());request.setRawHeader(b'Authorization',('Bearer '+token).encode())
        reply=self.net.get(request);self.replies.add(reply)
        reply.downloadProgress.connect(lambda received,total:reply.abort() if received>limit or total>limit else None)
        def done():
            self.replies.discard(reply)
            raw=bytes(reply.readAll()) if reply.error()==QNetworkReply.NoError else b''
            reply.deleteLater()
            if not self.closed:callback(raw if len(raw)<=limit else b'')
        reply.finished.connect(done)
    def flush(self):
        token=self.token()
        if not token or not self.pending or self.inflight:return
        batch=sorted(self.pending)[:100];self.pending.difference_update(batch);self.inflight.update(batch)
        def ready(raw):
            self.inflight.difference_update(batch)
            try:rows=json.loads(raw)['data']
            except (ValueError,KeyError,TypeError):
                for login in batch:self.failed[login]=time.time()
                return
            found=set()
            for row in rows:
                login=str(row.get('login','')).lower()
                if login not in batch:continue
                found.add(login);url=row.get('profile_image_url','');old=self.entries.get(login,{})
                self.entries[login]={'display_name':row.get('display_name') or login,'id':row.get('id',''),'url':url,'image':old.get('image',''),'checked':time.time()}
                if url!=old.get('url') or not old.get('image') or not (self.root/old['image']).exists():self.download(login,url)
                self.changed.emit(login)
            for login in set(batch)-found:self.entries[login]={'checked':time.time()}
            self.save()
        self.get('https://api.twitch.tv/helix/users?'+urlencode([('login',login) for login in batch]),ready,token)
    def save(self):
        try:atomic_write(self.root/'identities.json',json.dumps(self.entries,ensure_ascii=False))
        except OSError:pass
    def download(self,login,url):
        parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.hostname!='static-cdn.jtvnw.net' or login in self.images or self.closed or time.time()-self.image_failed.get(login,0)<120:return
        self.images.add(login)
        def ready(raw):
            self.images.discard(login)
            image=QImage.fromData(raw)
            if image.isNull() or image.width()>4096 or image.height()>4096:
                self.image_failed[login]=time.time();return
            filename=login+'.png';image=image.scaled(96,96,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)
            image=image.copy((image.width()-96)//2,(image.height()-96)//2,96,96)
            if image.save(str(self.root/filename)):
                self.entries[login]['image']=filename;self.pictures.pop(login,None);self.save();self.changed.emit(login)
        self.get(url,ready)
    def pixmap(self,login):
        login=str(login).lower()
        if login in self.pictures:return self.pictures[login]
        filename=self.entries.get(login,{}).get('image','')
        if not filename or Path(filename).name!=filename:return None
        pix=QPixmap(str(self.root/filename))
        if pix.isNull():return None
        self.pictures[login]=pix
        if len(self.pictures)>256:self.pictures.pop(next(iter(self.pictures)))
        return pix
    def rounded_pixmap(self,login,diameter,dpr=1.):
        source=self.pixmap(login)
        if source is None:return None
        pixels=max(1,round(diameter*dpr));key=(login,source.cacheKey(),pixels,dpr)
        if key in self.rounded:return self.rounded[key]
        # Raster clip edges are jagged at tiny sizes. Mask at 3x physical size,
        # then downsample once; keep the source and destination strictly square.
        size=pixels*3;large=QPixmap(size,size);large.fill(Qt.transparent)
        painter=QPainter(large);painter.setRenderHints(QPainter.Antialiasing|QPainter.SmoothPixmapTransform)
        path=QPainterPath();path.addEllipse(QRectF(0,0,size,size));painter.setClipPath(path)
        side=min(source.width(),source.height());crop=QRectF((source.width()-side)/2,(source.height()-side)/2,side,side)
        painter.drawPixmap(QRectF(0,0,size,size),source,crop);painter.end()
        result=large.scaled(pixels,pixels,Qt.KeepAspectRatio,Qt.SmoothTransformation);result.setDevicePixelRatio(dpr)
        self.rounded[key]=result
        if len(self.rounded)>256:self.rounded.pop(next(iter(self.rounded)))
        return result
    def icon(self,login):return self.rounded_pixmap(login,32)
    def paint(self,painter,rect,login):
        dpr=painter.device().devicePixelRatioF();diameter=min(rect.width(),rect.height())
        pix=self.rounded_pixmap(login,diameter,dpr)
        if pix is None:return False
        x=round((rect.center().x()-diameter/2)*dpr)/dpr;y=round((rect.center().y()-diameter/2)*dpr)/dpr
        painter.save();painter.setRenderHints(QPainter.Antialiasing|QPainter.SmoothPixmapTransform)
        painter.drawPixmap(QPointF(x,y),pix);painter.restore();return True
    def channel(self,painter,rect,login,font):
        self.ensure(login);size=20;icon=QRectF(rect.left(),rect.center().y()-size/2,size,size)
        if not self.paint(painter,icon,login):
            painter.save();painter.setPen(Qt.NoPen);painter.setBrush(QColor('#544262'));painter.drawEllipse(icon);painter.setPen(QColor('#eeedf5'));painter.setFont(font);painter.drawText(icon,Qt.AlignCenter,login[:1].upper());painter.restore()
        painter.save();painter.setPen(QColor('#655870'));x=icon.right()+6;painter.drawLine(int(x),int(icon.top()+3),int(x),int(icon.bottom()-3));painter.setFont(font);painter.setPen(QColor('#c2b5e4'));text=self.display(login);metrics=painter.fontMetrics();painter.drawText(rect.adjusted(size+13,0,0,0),Qt.AlignVCenter|Qt.AlignLeft,metrics.elidedText(text,Qt.ElideRight,int(max(0,rect.width()-size-13))));painter.restore()
    def close(self):
        self.closed=True;self.timer.stop()
        for reply in list(self.replies):reply.abort()
