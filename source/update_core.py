"""Public GitHub releases; bounded downloads and verified application-only staging."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import urllib.request
import zipfile
from branding import APP_ID, EXE_STEM, VERSION

ASSET = 'ChatterinoArchive-Windows-x64.zip'
MAX_DOWNLOAD = 300 * 1024 * 1024
MAX_UNPACKED = 700 * 1024 * 1024

def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'v?\d{1,4}\.\d{1,4}\.\d{1,4}',value):
        raise ValueError('Неверный номер версии.')
    return tuple(map(int,value.lstrip('v').split('.')))

def repository(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}',value):
        raise ValueError('Укажи репозиторий GitHub в формате автор/название.')
    if value.split('/')[1] in ('.','..'):raise ValueError('Неверный репозиторий.')
    return value

def bundled_repository():
    path=Path(__file__).parent/'release_config.json'
    value=json.loads(path.read_text(encoding='utf-8')).get('repository','')
    return repository(value) if value else ''

class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        from urllib.parse import urlsplit
        p=urlsplit(newurl)
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or p.hostname not in ('github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'):
            raise ValueError('Неожиданный адрес загрузки обновления.')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def open_url(url):
    return urllib.request.build_opener(GitHubRedirect()).open(urllib.request.Request(url,headers={
        'User-Agent':EXE_STEM+'/'+VERSION,'Accept':'application/vnd.github+json'}),timeout=20)

def latest(repo,current=VERSION,opener=open_url):
    repo=repository(repo)
    with opener('https://api.github.com/repos/'+repo+'/releases/latest') as response:
        raw=response.read(2*1024*1024+1)
    if len(raw)>2*1024*1024:raise ValueError('Слишком большой ответ GitHub.')
    doc=json.loads(raw)
    if doc.get('draft') or doc.get('prerelease'):return None
    tag=doc.get('tag_name','')
    if version(tag)<=version(current):return None
    prefix='https://github.com/'+repo+'/releases/download/'+tag+'/'
    assets={a['name']:a for a in doc.get('assets',[]) if isinstance(a,dict) and 'name' in a}
    asset=assets.get(ASSET,{})
    digest=asset.get('digest','')
    if not re.fullmatch(r'sha256:[a-fA-F0-9]{64}',digest or ''):
        raise ValueError('У выпуска нет контрольной суммы GitHub. Обновление не устанавливается.')
    if asset.get('browser_download_url')!=prefix+ASSET or not 0<asset.get('size',0)<=MAX_DOWNLOAD:
        raise ValueError('Неподходящий пакет обновления.')
    return {'version':tag.lstrip('v'),'url':prefix+ASSET,'sha256':digest[7:].lower(),
        'size':asset['size'],'notes':str(doc.get('body') or '')[:12000]}

def download(release,target,opener=open_url):
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    partial=target.with_suffix('.part');hasher=hashlib.sha256();size=0
    try:
        with opener(release['url']) as response,partial.open('wb') as out:
            while True:
                chunk=response.read(256*1024)
                if not chunk:break
                size+=len(chunk)
                if size>MAX_DOWNLOAD or size>release['size']:raise ValueError('Размер обновления не совпадает.')
                hasher.update(chunk);out.write(chunk)
        if size!=release['size'] or hasher.hexdigest()!=release['sha256']:raise ValueError('Проверка целостности обновления не пройдена.')
        os.replace(partial,target)
    finally:partial.unlink(missing_ok=True)

def safe_member(value):
    path=PurePosixPath(value)
    if '\\' in value or ':' in value or path.is_absolute() or any(p in ('..','.') or p.endswith((' ','.')) for p in value.split('/')):
        raise ValueError('Небезопасный путь в обновлении.')
    if any(re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',p,re.I) for p in path.parts):
        raise ValueError('Недопустимое имя файла.')
    if not path.parts or path.parts[0]!='app':raise ValueError('Обновление содержит посторонние данные.')
    return path

def stage(archive,destination,expected_version):
    """Validate the entire archive before extracting anything."""
    destination=Path(destination)
    if destination.exists():raise ValueError('Папка подготовки уже существует.')
    with zipfile.ZipFile(archive) as z:
        entries=[i for i in z.infolist() if not i.is_dir()]
        if len(entries)>5000 or sum(i.file_size for i in entries)>MAX_UNPACKED:raise ValueError('Пакет слишком большой.')
        seen=set()
        for item in entries:
            safe_member(item.filename)
            key=item.filename.casefold()
            if key in seen or stat.S_ISLNK(item.external_attr>>16):raise ValueError('Повторный путь или ссылка в пакете.')
            seen.add(key)
        manifest=json.loads(z.read('app/application.json'))
        if manifest.get('id')!=APP_ID or manifest.get('version')!=expected_version:
            raise ValueError('Пакет относится к другой программе или версии.')
        if ('app/'+EXE_STEM+'.exe').casefold() not in seen:raise ValueError('В пакете нет приложения.')
        hashes=json.loads(z.read('app/files.json'))
        actual={i.filename[4:] for i in entries if i.filename!='app/files.json'}
        if set(hashes)!=actual:raise ValueError('Список файлов пакета не совпадает.')
        for item in entries:
            if item.filename=='app/files.json':continue
            if hashlib.sha256(z.read(item)).hexdigest()!=hashes[item.filename[4:]]:raise ValueError('Повреждён файл обновления.')
        destination.mkdir(parents=True)
        try:
            for item in entries:
                target=destination.joinpath(*PurePosixPath(item.filename).parts[1:])
                target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(item) as src,target.open('wb') as out:shutil.copyfileobj(src,out)
        except Exception:
            shutil.rmtree(destination);raise
    return destination
