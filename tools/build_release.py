"""Build a release from fresh PyInstaller output, never from an installed profile."""
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'source'))
from branding import APP_ID,VERSION,EXE_STEM,APP_NAME,AUTHOR
from update_core import ASSET,stage

def main():
    built=ROOT/'dist'/EXE_STEM
    if not (built/(EXE_STEM+'.exe')).is_file():raise SystemExit('Run PyInstaller first.')
    output=ROOT/'releases';output.mkdir(exist_ok=True)
    work=output/'package'
    if work.exists():raise SystemExit('Remove the previous releases/package directory first.')
    app=work/'app';shutil.copytree(built,app)
    for name in ('licenses','plugin'):shutil.copytree(ROOT/name,app/name)
    for name in ('LICENSE','README.md'):shutil.copy2(ROOT/name,app/name)
    (app/'application.json').write_text(json.dumps({'id':APP_ID,'version':VERSION,'name':APP_NAME,'author':AUTHOR}),encoding='utf-8')
    files={p.relative_to(app).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in app.rglob('*') if p.is_file()}
    (app/'files.json').write_text(json.dumps(files,sort_keys=True),encoding='utf-8')
    archive=output/ASSET
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(app.rglob('*')):
            if path.is_file():z.write(path,path.relative_to(work).as_posix())
    verify=output/'verify';stage(archive,verify,VERSION)
    shutil.rmtree(verify);shutil.rmtree(work)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/'SHA256SUMS.txt').write_text(digest+'  '+ASSET+'\n',encoding='utf-8')
    print(archive);print(digest)

if __name__=='__main__':main()
