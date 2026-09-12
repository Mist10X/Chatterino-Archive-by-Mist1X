"""Qt update controls; networking and extraction never block the GUI thread."""
import json
from pathlib import Path
import queue
import shutil
import sys
import threading
import uuid
from PySide6.QtCore import QObject,QTimer
from theme import Button,Dialog,box,label
from update_core import bundled_repository,latest,download,stage,VERSION
from update_apply import prepare_helper

class Updates(QObject):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.results=queue.Queue();self.busy=False;self.release=None
        self.text='Текущая версия: '+VERSION;self.manual=False
        self.banner,layout=box(False,margins=10);self.caption=label('',10,bold=True)
        layout.addWidget(self.caption,1);self.button=Button('Обновление',app.theme,self.dialog,primary=True);layout.addWidget(self.button)
        app.centralWidget().layout().insertWidget(0,self.banner);self.banner.hide()
        self.poller=QTimer(self);self.poller.setInterval(200);self.poller.timeout.connect(self.poll);self.poller.start()
        self.periodic=QTimer(self);self.periodic.setInterval(4*3600*1000);self.periodic.timeout.connect(self.check)
        if app.network:self.periodic.start();QTimer.singleShot(15000,self.check)
        self.cleaner=QTimer(self);self.cleaner.setInterval(30000);self.cleaner.timeout.connect(self.cleanup);self.cleaner.start()
    def cleanup(self):
        if self.busy:return
        from update_apply import completed_runs
        self.task(lambda:completed_runs(self.app.data),'cleanup')
    def task(self,fn,kind):
        if self.busy:return
        self.busy=True
        def run():
            try:self.results.put((kind,fn(),None))
            except Exception as exc:self.results.put((kind,None,str(exc)))
        threading.Thread(target=run,daemon=True).start()
    def check(self,manual=False):
        if self.busy:return
        self.manual=manual;self.text='Проверяем обновления…'
        def fetch():
            repo=bundled_repository()
            if not repo:raise ValueError('Источник обновлений ещё не настроен.')
            try:return latest(repo)
            except __import__('urllib.error',fromlist=['HTTPError']).HTTPError as exc:
                if exc.code==404:raise ValueError('Первый выпуск ещё не опубликован на GitHub.') from None
                raise
        self.task(fetch,'check')
    def install(self):
        if self.busy or not self.release:return
        if not getattr(sys,'frozen',False):
            self.text='Установка обновлений доступна в готовой сборке EXE.';return
        release=dict(self.release);self.text='Скачиваем и проверяем обновление…';self.banner.show()
        def fetch():
            root=self.app.data/'updates';root.mkdir(exist_ok=True)
            run=root/('run-'+uuid.uuid4().hex);run.mkdir()
            try:
                download(release,run/'release.zip');stage(run/'release.zip',run/'staged',release['version'])
                prepare_helper(Path(sys.executable).parent,self.app.profile,run/'staged')
            except Exception:
                shutil.rmtree(run,ignore_errors=True);raise
            return str(run)
        self.task(fetch,'install')
    def poll(self):
        if self.app.closing:return
        try:kind,value,error=self.results.get_nowait()
        except queue.Empty:return
        self.busy=False
        if kind=='cleanup':
            if value:
                self.text='Обновление отменено, предыдущая версия сохранена: '+value[0]
                self.caption.setText(self.text);self.banner.show()
            return
        if error:self.text='Не удалось обновить: '+error
        elif kind=='check':
            self.release=value
            self.text=('Доступна версия '+value['version']) if value else 'Установлена актуальная версия '+VERSION
        else:
            self.text='Сохраняем данные и перезапускаем архив…';self.app.exit_app();return
        self.caption.setText(self.text)
        self.banner.setVisible(bool(self.release) or (bool(error) and (self.manual or kind=='install')))
    def dialog(self):
        dialog=Dialog(self.app,'Обновления','Источник: github.com/'+bundled_repository())
        status=label(self.text,11);dialog.layout.addWidget(status)
        notes=label(self.release['notes'] if self.release else '',10,True);dialog.layout.addWidget(notes)
        check=Button('Проверить сейчас',self.app.theme,lambda:self.check(True));dialog.layout.addWidget(check)
        install=Button('Скачать и установить',self.app.theme,lambda:(dialog.accept(),self.install()),primary=True);dialog.layout.addWidget(install)
        def refresh():
            status.setText(self.text);check.setEnabled(not self.busy);install.setEnabled(bool(self.release) and not self.busy)
            notes.setText(self.release['notes'] if self.release else '')
        refresh();timer=QTimer(dialog);timer.setInterval(200);timer.timeout.connect(refresh);timer.start()
        dialog.actions(self.app.theme,cancel=False);dialog.exec()
