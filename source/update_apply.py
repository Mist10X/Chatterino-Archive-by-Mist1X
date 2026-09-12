"""Application-only update transaction, run by a disposable copy of the old EXE."""
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from branding import EXE_STEM, APP_ID
from storage import atomic_write

def no_links(folder):
    folder=Path(folder)
    for path in [folder,*folder.rglob('*')]:
        if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):
            raise ValueError('Папка приложения содержит ссылки. Используй ручную установку.')

def validate_install(install,profile):
    install=Path(install).resolve();profile=Path(profile).resolve()
    if not (install/(EXE_STEM+'.exe')).is_file() or install==install.parent:
        raise ValueError('Не найдена папка установленного архива.')
    if profile==install or profile.is_relative_to(install):
        raise ValueError('Перед обновлением перенеси данные за пределы папки приложения.')
    if (install/'chatterino.exe').exists():raise ValueError('Архив должен находиться в отдельной папке.')
    no_links(install)
    if any(p.name.lower() in ('data','profile','archivedata','backups','settings') or
           (p.is_file() and (p.suffix in ('.jsonl','.sqlite3') or 'credentials' in p.name.lower()))
           for p in install.rglob('*')):
        raise ValueError('В папке программы обнаружены пользовательские данные. Используй отдельную установку.')
    return install,profile

def prepare_helper(install,profile,staged):
    install,profile=validate_install(install,profile)
    staged=Path(staged).resolve();no_links(staged)
    run=staged.parent
    helper=run/'helper'
    shutil.copytree(install,helper)
    plan={'id':APP_ID,'install':str(install),'profile':str(profile),'staged':str(staged),
          'parent_pid':os.getpid(),'health':str(run/'healthy'),'nonce':uuid.uuid4().hex}
    planpath=run/'plan.json';atomic_write(planpath,json.dumps(plan))
    subprocess.Popen([str(helper/(EXE_STEM+'.exe')),'--apply-update',str(planpath)],
        cwd=str(helper),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

def wait_pid(pid,seconds=120):
    if os.name!='nt':raise RuntimeError('Обновление поддерживается в Windows.')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x100000,False,int(pid))
    if not handle:
        if ctypes.get_last_error()==87:return
        raise OSError('Не удалось дождаться завершения архива.')
    try:
        if kernel.WaitForSingleObject(handle,int(seconds*1000))!=0:
            raise TimeoutError('Архив ещё завершает запись. Файлы не заменены.')
    finally:kernel.CloseHandle(handle)

def swap_and_launch(install,profile,staged,health,nonce,timeout=60,launcher=None):
    """The previous directory is retained until the new GUI reports healthy."""
    install,profile=validate_install(install,profile)
    staged=Path(staged).resolve();health=Path(health);no_links(staged)
    previous=install.with_name(install.name+'.previous-'+uuid.uuid4().hex)
    incoming=install.with_name(install.name+'.incoming-'+uuid.uuid4().hex)
    backups=profile/'Plugins/history-tools/data/updates/backups';backups.mkdir(parents=True,exist_ok=True)
    backup=backups/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.zip')
    with zipfile.ZipFile(backup,'w',zipfile.ZIP_DEFLATED) as z:
        for path in install.rglob('*'):
            if path.is_file():z.write(path,path.relative_to(install))
    with zipfile.ZipFile(backup) as z:
        if z.testzip() is not None:raise ValueError('Резервная копия не прошла проверку.')
    shutil.copytree(staged,incoming)
    # Preserve installation metadata, but update its release identity.
    if (install/'installation.json').exists():
        info=json.loads((install/'installation.json').read_text(encoding='utf-8-sig'))
        info['version']=json.loads((incoming/'application.json').read_text(encoding='utf-8'))['version']
        info['backup']=str(backup)
        atomic_write(incoming/'installation.json',json.dumps(info,ensure_ascii=False))
    launcher=launcher or (lambda args:subprocess.Popen(args,cwd=str(install)))
    install.rename(previous);child=None
    try:
        incoming.rename(install)
        child=launcher([str(install/(EXE_STEM+'.exe')),'--profile',str(profile),
            '--update-health',str(health),'--update-nonce',nonce,'--secondary-screen'])
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if child.poll() is not None:raise RuntimeError('Новая версия завершилась при запуске.')
            if health.exists() and health.read_text(encoding='utf-8')==nonce:break
            time.sleep(.2)
        else:raise TimeoutError('Новая версия не подтвердила успешный запуск.')
    except Exception:
        if child and child.poll() is None:child.terminate();child.wait(timeout=15)
        if install.exists():shutil.rmtree(install)
        previous.rename(install)
        launcher([str(install/(EXE_STEM+'.exe')),'--profile',str(profile),'--secondary-screen'])
        raise
    else:
        shutil.rmtree(previous)
        # Keep one verified application rollback ZIP; archive data is untouched.
        for old in backups.glob('*.zip'):
            if old!=backup:old.unlink()

def apply_plan(path):
    path=Path(path).resolve()
    try:
        plan=json.loads(path.read_text(encoding='utf-8'))
        if plan.get('id')!=APP_ID or Path(plan['staged']).resolve()!=path.parent/'staged':raise ValueError('Неверный план обновления.')
        if Path(plan['health']).resolve()!=path.parent/'healthy':raise ValueError('Неверный путь проверки запуска.')
        wait_pid(plan['parent_pid'])
        swap_and_launch(plan['install'],plan['profile'],plan['staged'],plan['health'],plan['nonce'])
        atomic_write(path.parent/'result.json',json.dumps({'ok':True}))
        return 0
    except Exception as exc:
        atomic_write(path.parent/'result.json',json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False))
        return 1

def confirm_health(data,path,nonce,window):
    data=Path(data).resolve();path=Path(path).resolve()
    if path.parent.parent!=data/'updates' or not path.parent.name.startswith('run-') or path.name!='healthy':return
    if window.worker.is_alive() and not window.worker_error and not window.closing:
        atomic_write(path,nonce)

def completed_runs(data):
    """Remove disposable executable copies only after the helper wrote its result."""
    root=Path(data).resolve()/'updates';errors=[]
    for folder in root.glob('run-*'):
        if folder.is_symlink() or folder.resolve().parent!=root:continue
        result=folder/'result.json'
        try:
            if not result.exists() or time.time()-result.stat().st_mtime<10:continue
            doc=json.loads(result.read_text(encoding='utf-8'))
            no_links(folder)
            if not doc.get('ok'):errors.append(str(doc.get('error','Обновление не установлено.')))
            shutil.rmtree(folder)
        except (OSError,ValueError):continue
    return errors
