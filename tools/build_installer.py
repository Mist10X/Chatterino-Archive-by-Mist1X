"""Compile a per-user installer from the verified, clean update ZIP."""
import argparse,hashlib,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'source'))
from branding import VERSION
from update_core import stage,ASSET

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--compiler');args=parser.parse_args()
    candidates=[args.compiler,shutil.which('ISCC'),str(Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Inno Setup 6/ISCC.exe')]
    compiler=next((p for p in candidates if p and Path(p).is_file()),None)
    if not compiler:raise SystemExit('Install Inno Setup 6.3+ or pass --compiler PATH/ISCC.exe')
    releases=ROOT/'releases';work=releases/'installer-stage'
    if work.exists():raise SystemExit('Remove the old releases/installer-stage directory first')
    try:
        payload=stage(releases/ASSET,work,VERSION)
        subprocess.run([compiler,'/DAppVersion='+VERSION,'/DPayload='+str(payload),'/DOutput='+str(releases),str(ROOT/'installer/archive.iss')],check=True)
        setup=releases/f'ChatterinoArchive-Setup-{VERSION}-x64.exe'
        assert setup.is_file()
        paths=[releases/ASSET,setup]
        (releases/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in paths),encoding='utf-8')
        print(setup)
    finally:
        if work.exists():shutil.rmtree(work)
if __name__=='__main__':main()
