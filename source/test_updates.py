import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from branding import APP_ID,EXE_STEM
from update_core import stage,download,latest,ASSET
from update_apply import swap_and_launch,validate_install
from replay_store import settings_load

def package(path,extra=None):
    files={EXE_STEM+'.exe':b'new', 'application.json':json.dumps({'id':APP_ID,'version':'0.17.1'}).encode()}
    files.update(extra or {})
    files['files.json']=json.dumps({k:hashlib.sha256(v).hexdigest() for k,v in files.items()}).encode()
    with zipfile.ZipFile(path,'w') as z:
        for k,v in files.items():z.writestr('app/'+k,v)

class UpdatesTests(unittest.TestCase):
    def test_fresh_archive_has_no_recorded_channels(self):
        with tempfile.TemporaryDirectory() as tmp:self.assertEqual(settings_load(Path(tmp))['channels'],[])
    def test_stage_and_reject_traversal_tampering_wrong_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'release.zip';package(p);stage(p,root/'ok','0.17.1')
            self.assertEqual((root/'ok'/(EXE_STEM+'.exe')).read_bytes(),b'new')
            with self.assertRaises(ValueError):stage(p,root/'wrong','0.17.2')
            package(p,{'../../escape':b'bad'})
            with self.assertRaises(ValueError):stage(p,root/'escape','0.17.1')
            package(p)
            with zipfile.ZipFile(p,'a') as z:z.writestr('app/extra',b'bad')
            with self.assertRaises(ValueError):stage(p,root/'tamper','0.17.1')
            self.assertFalse((root/'escape').exists());self.assertFalse((root/'tamper').exists())
    def test_digest_and_download_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'a.zip';meta={'url':'unused','size':3,'sha256':hashlib.sha256(b'abc').hexdigest()}
            download(meta,target,lambda _:io.BytesIO(b'abc'));self.assertEqual(target.read_bytes(),b'abc')
            for body in (b'abd',b'abcd',b'ab'):
                with self.assertRaises(ValueError):download(meta,target,lambda _:io.BytesIO(body))
            self.assertFalse(target.with_suffix('.part').exists())
    def test_release_identity_and_version(self):
        doc={'tag_name':'v0.17.1','assets':[{'name':ASSET,'size':3,'digest':'sha256:'+'a'*64,'browser_download_url':'https://github.com/Mist10X/Chatterino-Archive-by-Mist1X/releases/download/v0.17.1/'+ASSET}]}
        fetch=lambda _:io.BytesIO(json.dumps(doc).encode())
        self.assertEqual(latest('Mist10X/Chatterino-Archive-by-Mist1X','0.17.0',fetch)['version'],'0.17.1')
        self.assertIsNone(latest('Mist10X/Chatterino-Archive-by-Mist1X','0.17.1',fetch))
        doc['assets'][0]['browser_download_url']='https://example.com/other'
        with self.assertRaises(ValueError):latest('Mist10X/Chatterino-Archive-by-Mist1X','0.17.0',fetch)
    def test_transaction_success_and_rollback_preserve_data(self):
        for success in (True,False):
            with self.subTest(success=success),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);install=root/'app';install.mkdir();exe=install/(EXE_STEM+'.exe');exe.write_bytes(b'old')
                profile=root/'profile';profile.mkdir();data=profile/'records';data.write_bytes(b'private records')
                p=root/'r.zip';package(p);staged=stage(p,root/'staged','0.17.1');health=root/'healthy';calls=[]
                class Child:
                    def poll(self):return None if success else 1
                def launch(args):
                    calls.append(args)
                    if success:health.write_text('nonce')
                    return Child()
                if success:swap_and_launch(install,profile,staged,health,'nonce',launcher=launch)
                else:
                    with self.assertRaises(RuntimeError):swap_and_launch(install,profile,staged,health,'nonce',launcher=launch)
                    self.assertEqual(len(calls),2)
                self.assertEqual(exe.read_bytes(),b'new' if success else b'old');self.assertEqual(data.read_bytes(),b'private records')
                self.assertEqual(len(list((profile/'Plugins/history-tools/data/updates/backups').glob('*.zip'))),1)
    def test_data_inside_install_refuses_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/(EXE_STEM+'.exe')).write_bytes(b'old')
            with self.assertRaises(ValueError):validate_install(root,root/'Profile')

if __name__=='__main__':unittest.main()
