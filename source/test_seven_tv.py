import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from PySide6.QtCore import Qt,QSizeF,QPoint,QEvent
from PySide6.QtGui import QImage,QColor,QTextCursor,QPainter,QPixmap,QHelpEvent
from PySide6.QtWidgets import QApplication,QStyleOptionViewItem,QTreeWidgetItem
from PySide6.QtTest import QTest
from seven_tv_store import parse_catalog,parse_personal_user,fetch_personal_users,permitted_url,EmoteStore,groups,SafeRedirect
from seven_tv import SevenTV,NetworkPool,personal_event
from emote_widgets import EmoteBrowser,EmoteDelegate,message_blocks,OBJECT
from theme import install_style

qt=QApplication.instance() or QApplication([]);install_style(qt)

def catalog(channel,alias='Smile',eid='01FCY771D800007PQ2DF3GDTN6',flags=0):
    emote={'id':eid,'name':alias,'flags':flags,'data':{'id':eid,'name':'OriginalName','animated':False,'flags':0,
        'host':{'url':'//cdn.7tv.app/emote/'+eid,'files':[{'name':'3x.webp','format':'WEBP','width':96,'height':96,'size':1000}]}}}
    result={'id':'set-test','emotes':[emote]}
    return parse_catalog(result if channel=='global' else {'emote_set':result},channel,'123',now=100)

def personal(uid='456',alias='Smile',eid='01F6ME9FRG0005TFYTWP1H8R42',now=100):
    active={'id':eid,'name':alias,'flags':0,'data':{'id':eid,'name':alias,'state':['PERSONAL'],'animated':False,'flags':0,
        'host':{'url':'//cdn.7tv.app/emote/'+eid,'files':[{'name':'3x.webp','format':'WEBP','width':96,'height':96,'size':1000}]}}}
    return parse_personal_user({'emote_sets':[{'id':'01KEAJ6HE8YS6BM2M2K1DDV4KP','name':'Personal','emotes':[active]}]},uid,now)

class StoreTests(unittest.TestCase):
    def test_personal_emote_has_priority_and_message_keeps_frozen_meaning(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=EmoteStore(tmp);store.install(catalog('global'));store.install(catalog('one',eid='01FCY771D800007PQ2DF3GDTN6'))
            first=personal(now=time.time());store.install_personal(first)
            row={'channel':'one','user':'u','user_id':'456','id':'m','time_utc':'2026','text':'Smile'}
            self.assertTrue(store.bind(row)['Smile']['personal']);self.assertEqual(store.bind(row)['Smile']['id'],first['emotes']['Smile']['id'])
            newer=personal(eid='01FCY771D800007PQ2DF3GDTN6',now=time.time());store.install_personal(newer)
            self.assertEqual(store.bind(row)['Smile']['id'],first['emotes']['Smile']['id'])
            self.assertEqual(store.bind(dict(row,id='new'))['Smile']['id'],newer['emotes']['Smile']['id']);store.close()
    def test_unknown_personal_catalog_keeps_alias_raw_until_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=EmoteStore(tmp);store.install(catalog('global'));store.install(catalog('one'))
            row={'channel':'one','user':'u','user_id':'999','id':'m','time_utc':'2026','text':'Smile'}
            self.assertEqual(store.bind(row),{})
            store.install_personal({'version':1,'twitch_id':'999','set_ids':[],'observed_at':time.time(),'emotes':{}})
            self.assertIn('Smile',store.bind(row));store.close()
    def test_personal_parser_filters_ineligible_and_batches_graphql(self):
        value=personal();bad={'id':'01FCY771D800007PQ2DF3GDTN6','name':'Nope','data':{'id':'01FCY771D800007PQ2DF3GDTN6','state':['LISTED'],'host':{'url':'//cdn.7tv.app/emote/01FCY771D800007PQ2DF3GDTN6','files':[{'name':'3x.webp','format':'WEBP','width':96,'height':96,'size':10}]}}}
        parsed=parse_personal_user({'emote_sets':[{'id':'01KEAJ6HE8YS6BM2M2K1DDV4KP','emotes':[bad]}]},'456');self.assertEqual(parsed['emotes'],{})
        def fake(url,body,maximum):
            request=json.loads(body);self.assertEqual(url,'https://7tv.io/v3/gql');self.assertIn('userByConnection',request['query'])
            return json.dumps({'data':{'u0':{'emote_sets':[]},'u1':{'emote_sets':[]}}}).encode()
        result=fetch_personal_users(['456','789'],fake);self.assertEqual(set(result),{'456','789'})
        self.assertTrue(permitted_url('https://7tv.io/v3/gql'))
    def test_eventapi_refreshes_only_personal_users_and_sets(self):
        entitlement={'op':0,'d':{'type':'entitlement.create','body':{'object':{'kind':'EMOTE_SET','ref_id':'01KEAJ6HE8YS6BM2M2K1DDV4KP','user':{'connections':[{'platform':'TWITCH','id':'456'},{'platform':'KICK','id':'9'}]}}}}}
        self.assertEqual(personal_event(entitlement),[('user','456','01KEAJ6HE8YS6BM2M2K1DDV4KP')])
        update={'op':0,'d':{'type':'emote_set.update','body':{'id':'01KEAJ6HE8YS6BM2M2K1DDV4KP'}}}
        self.assertEqual(personal_event(update),[('set','01KEAJ6HE8YS6BM2M2K1DDV4KP','')])
        entitlement['d']['body']['object']['kind']='PAINT';self.assertEqual(personal_event(entitlement),[])
    def test_alias_ulid_overlay_quality_and_channel_priority(self):
        c=catalog('one',flags=1);ref=c['emotes']['Smile'];self.assertTrue(ref['zero_width']);self.assertTrue(ref['url'].endswith('/3x.webp'))
        self.assertNotIn('OriginalName',c['emotes'])
        with tempfile.TemporaryDirectory() as tmp:
            s=EmoteStore(tmp);s.install(catalog('global',eid='01F6ME9FRG0005TFYTWP1H8R42'));s.install(c)
            refs=s.bind({'channel':'one','user':'u','id':'id','text':'Smile Smile, <b>Smile</b>'})
            self.assertEqual(refs['Smile']['id'],ref['id']);self.assertNotIn('Smile,',refs);s.close()
    def test_frozen_meanings_survive_catalog_changes_restart_and_empty_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=EmoteStore(tmp);s.install(catalog('global'));s.install(catalog('one',alias='Local'))
            row={'channel':'one','user':'u','id':'m','time_utc':'2026','text':'Local'}
            old=s.bind(row)['Local']['id'];s.bind(dict(row,id='literal',text='Future'))
            s.install(catalog('one',alias='Local',eid='01F6ME9FRG0005TFYTWP1H8R42'));self.assertEqual(s.bind(row)['Local']['id'],old)
            s.install(catalog('one',alias='Future'));self.assertEqual(s.bind(dict(row,id='literal',text='Future')),{});s.close()
            s=EmoteStore(tmp);self.assertEqual(s.bind(row)['Local']['id'],old);s.close()
    def test_unresolved_channel_does_not_freeze_global_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=EmoteStore(tmp);s.install(catalog('global'));row={'channel':'one','user':'u','id':'m','text':'Smile'}
            first=s.bind(row)['Smile']['id'];s.install(catalog('one',eid='01F6ME9FRG0005TFYTWP1H8R42'))
            self.assertNotEqual(s.bind(row)['Smile']['id'],first);s.close()
    def test_network_and_cache_paths_cannot_escape_7tv(self):
        for url in ['http://cdn.7tv.app/emote/abc/3x.webp','https://cdn.7tv.app.evil.test/emote/abc/3x.webp','https://127.0.0.1/emote/abc/3x.webp','https://7tv.io:bad/v3/emote-sets/global','https://cdn.7tv.app/emote/abc/../../x','file:///C:/secret','https://user@7tv.io/v3/emote-sets/global']:
            self.assertFalse(permitted_url(url),url)
        self.assertTrue(permitted_url('https://7tv.io/v3/users/twitch/123'))
        with tempfile.TemporaryDirectory() as tmp:
            m=SevenTV(tmp,{},online=False);self.assertIsNone(m.path({'key':'a'*64,'file':'../../secret'}));m.close()
    def test_overlay_composes_only_with_adjacent_base(self):
        base=catalog('one')['emotes']['Smile'];overlay=dict(base,name='Rain',zero_width=True)
        parsed=groups('Smile Rain  hello Rain',{'Smile':base,'Rain':overlay})
        self.assertEqual(len(parsed[0]['emotes']),2);self.assertEqual(parsed[0]['text'],'Smile Rain')
        self.assertEqual(''.join(x['text'] for x in parsed),'Smile Rain  hello Rain')
        self.assertEqual(len(groups('Smile\nRain',{'Smile':base,'Rain':overlay})),3)
    def test_two_background_requests_and_errors_return_without_hanging(self):
        def fake(url,maximum):
            if url.endswith('404'):raise ValueError('offline')
            return b'image bytes'
        pool=NetworkPool(fake);pool.submit('one','https://example.invalid/image');pool.submit('two','https://example.invalid/404')
        first=pool.results.get(timeout=2);second=pool.results.get(timeout=2);pool.stop.set()
        result={r[0]:r for r in [first,second]};self.assertEqual(result['one'][1],b'image bytes');self.assertEqual(result['two'][2],'offline')

class EmoteWidgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.manager=SevenTV(self.tmp.name,{},online=False)
        self.manager.store.install(catalog('global'));self.manager.store.install(catalog('one',alias='Local'))
        self.ref=self.manager.store.catalogs['one']['emotes']['Local']
        # Create our own lossless image fixture, so tests need no network or external emote art.
        picture=QImage(96,96,QImage.Format_ARGB32);picture.fill(QColor('#c2a7f5'));self.assertTrue(picture.save(str(self.manager.path(self.ref)),'WEBP'))
        self.browser=EmoteBrowser(self.manager);self.browser.resize(520,270);self.browser.show()
    def tearDown(self):
        self.browser.close();self.manager.close();self.browser.deleteLater();self.manager.deleteLater();qt.processEvents();self.tmp.cleanup()
    def row(self):return {'id':'test','time_utc':'2026-09-08T12:00:00Z','user':'someone','display_name':'Someone','channel':'one','text':'Local <b>literal</b>','reply':{'state':'available','channel':'one','user':'other','display_name':'Other','text':'Local ответ'}}
    def test_inline_objects_body_quote_copy_and_plain_html(self):
        row=self.row();self.browser.set_blocks(message_blocks(row,lambda x:'12:00'));qt.processEvents()
        self.assertIn('Local <b>literal</b>',self.browser.toPlainText());self.assertIn('Local ответ',self.browser.toPlainText())
        raw=self.browser.document().toPlainText();self.assertEqual(raw.count('\ufffc'),2)
        self.browser.selectAll();self.browser.copy();self.assertIn('Local ответ',qt.clipboard().text());self.assertNotIn('\ufffc',qt.clipboard().text())
        self.assertIsNotNone(self.manager.image(self.ref))
    def test_offline_missing_asset_keeps_name_and_disable_restores_plain_text(self):
        self.manager.path(self.ref).unlink();self.browser.set_blocks(message_blocks(self.row(),lambda x:'12:00'));qt.processEvents()
        self.assertIn('Local',self.browser.toPlainText());self.assertIsNone(self.manager.image(self.ref))
        self.manager.configure(enabled=False);qt.processEvents();self.assertNotIn('\ufffc',self.browser.document().toPlainText());self.assertIn('Local',self.browser.toPlainText())
    def test_painting_does_not_replace_selection_on_asset_refresh(self):
        self.browser.set_blocks(message_blocks(self.row(),lambda x:'12:00'));qt.processEvents()
        cur=self.browser.textCursor();cur.setPosition(1);cur.setPosition(4,QTextCursor.KeepAnchor);self.browser.setTextCursor(cur)
        self.manager.changed.emit();qt.processEvents();self.assertEqual(self.browser.textCursor().selectionStart(),1);self.assertEqual(self.browser.textCursor().selectionEnd(),4)
    def test_custom_objects_are_actually_painted_in_body_and_quote(self):
        self.browser.set_blocks(message_blocks(self.row(),lambda x:'12:00'));qt.processEvents()
        # Verify Qt calls the Python object's size/paint overrides, not merely
        # that U+FFFC placeholders were inserted in the document.
        rendered=self.browser.grab().toImage();count=0
        for y in range(rendered.height()):
            for x in range(rendered.width()):
                color=rendered.pixelColor(x,y)
                if abs(color.red()-194)<=2 and abs(color.green()-167)<=2 and abs(color.blue()-245)<=2:count+=1
        self.assertGreater(count,1000,'Both solid-color emote fixtures must be visible in the rendered text document')
    def test_actual_hover_event_shows_original_alias_in_body_and_reply(self):
        from unittest.mock import patch
        self.browser.set_blocks(message_blocks(self.row(),lambda x:'12:00'));qt.processEvents()
        raw=self.browser.document().toPlainText()
        for pos,char in enumerate(raw):
            if char!='\ufffc':continue
            cursor=QTextCursor(self.browser.document());cursor.setPosition(pos)
            rect=self.browser.cursorRect(cursor)
            for dx in (3,24):
                point=QPoint(rect.x()+dx,rect.center().y())
                self.assertEqual(self.browser.emote_at(point),'Local')
                event=QHelpEvent(QEvent.ToolTip,point,self.browser.viewport().mapToGlobal(point))
                with patch('emote_widgets.QToolTip.showText') as show:
                    QApplication.sendEvent(self.browser.viewport(),event)
                    self.assertEqual(show.call_args.args[1],'<qt>Local</qt>')
        self.manager.store.install(catalog('one',alias='Renamed'));self.manager.changed.emit();qt.processEvents()
        self.assertIn('Local',self.browser.toPlainText())
        self.assertIsNone(self.browser.emote_at(QPoint(490,250)))
    def test_table_emote_hit_test_preserves_text_and_alias(self):
        from theme import table
        from unittest.mock import patch
        tree=table(['Message']);tree.resize(480,130);row=self.row();row['text']='Local words'
        item=QTreeWidgetItem(['Local words']);item.setData(0,Qt.UserRole,row);tree.addTopLevelItem(item)
        delegate=EmoteDelegate(self.manager,tree);tree.setItemDelegate(delegate);tree.show();qt.processEvents()
        index=tree.indexFromItem(item);option=QStyleOptionViewItem();option.initFrom(tree);option.rect=tree.visualRect(index)
        hit=None
        for x in range(option.rect.left(),option.rect.right()):
            point=QPoint(x,option.rect.center().y())
            if delegate.emote_at(point,option,index)=='Local':hit=point;break
        self.assertIsNotNone(hit)
        with patch('emote_widgets.QToolTip.showText') as show:
            delegate.helpEvent(QHelpEvent(QEvent.ToolTip,hit,tree.viewport().mapToGlobal(hit)),tree,option,index)
            self.assertEqual(show.call_args.args[1],'<qt>Local</qt>')
        self.assertIsNone(delegate.emote_at(QPoint(460,option.rect.center().y()),option,index))
        tree.close();tree.deleteLater();qt.processEvents()

if __name__=='__main__':unittest.main()
