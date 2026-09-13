"""Explicit per-channel contracts for bot confirmations; no guessed bot attribution."""
import json,sqlite3
from contextlib import closing
from PySide6.QtWidgets import QLineEdit,QPlainTextEdit,QCheckBox,QListWidget,QSpinBox,QWidget,QVBoxLayout,QScrollArea
from theme import Dialog,Button,label,combo
from moderation_evidence import rules_load,rules_save

class RewardDialog(Dialog):
    def __init__(self,app):
        super().__init__(app,'Награды: мут и анмут','Покупка сопоставляется с фактическим событием. Без подтверждения бота связь отмечается как сопоставление. Неоднозначные совпадения остаются без подписи покупателя.')
        self.app=app;self.rules=rules_load(app.data);self.resize(660,760)
        self.saved=QListWidget();self.saved.setMaximumHeight(100);self.layout.addWidget(self.saved)
        self.saved.currentRowChanged.connect(self.select)
        self.channel=combo(sorted(set(app.twitch_config['channels'])));self.layout.addWidget(label('Канал'));self.layout.addWidget(self.channel)
        self.reward=combo([]);self.layout.addWidget(label('Полученная награда'));self.layout.addWidget(self.reward)
        self.action=combo(['Мут','Снятие мута']);self.layout.addWidget(self.action)
        self.mode=combo(['Подтверждение бота','Сопоставление без бота']);self.layout.addWidget(self.mode)
        self.duration=QSpinBox();self.duration.setRange(1,1209600);self.duration.setValue(600);self.duration.setSuffix(' с');self.layout.addWidget(label('Длительность покупаемого мута'));self.layout.addWidget(self.duration)
        self.bot=QLineEdit();self.bot.setPlaceholderText('Ник бота, подтверждающего исполнение');self.layout.addWidget(self.bot)
        self.template=QLineEdit();self.template.setPlaceholderText('Например: {buyer} замутил {target}');self.layout.addWidget(label('Точный текст подтверждения; ники заменить на {buyer} и {target}'));self.layout.addWidget(self.template)
        self.self_mute=QCheckBox('При запрещённой цели бот мутит самого покупателя');self.layout.addWidget(self.self_mute)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.layout.addWidget(Button('Сохранить правило',app.theme,self.save,primary=True));self.layout.addWidget(Button('Удалить выбранное правило',app.theme,self.remove))
        self.examples=QPlainTextEdit();self.examples.setReadOnly(True);self.examples.setMaximumHeight(160);self.layout.addWidget(label('Недавние доступные награды и ответы ботов'));self.layout.addWidget(self.examples)
        self.channel.currentTextChanged.connect(self.refresh_examples);self.refresh();self.refresh_examples()
        content=QWidget();form=QVBoxLayout(content);form.setContentsMargins(0,0,8,0);form.setSpacing(10)
        while self.layout.count()>2:
            item=self.layout.takeAt(2);form.addWidget(item.widget())
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QScrollArea.NoFrame);scroll.setWidget(content);self.layout.addWidget(scroll,1)
        self.actions(app.theme,cancel=False)
    def mode_changed(self,i):
        for widget in (self.bot,self.template,self.self_mute):widget.setEnabled(i==0)
        if i:self.self_mute.setChecked(False)
    def refresh(self):
        self.saved.clear()
        for r in self.rules:self.saved.addItem('#'+r['channel']+' · '+('Мут' if r['action']=='timeout' else 'Анмут')+' · '+(r['bot'] if r.get('mode')!='correlation' else 'Сопоставление'))
    def select(self,i):
        if i<0 or i>=len(self.rules):return
        r=self.rules[i];self.channel.setCurrentText(r['channel'])
        pos=self.reward.findData(r['reward'])
        if pos<0:self.reward.addItem('Сохранённая награда',r['reward']);pos=self.reward.count()-1
        self.reward.setCurrentIndex(pos);self.bot.setText(r['bot']);self.template.setText(r['template']);self.action.setCurrentIndex(r['action']=='untimeout');self.self_mute.setChecked(r.get('self',False))
        self.mode.setCurrentIndex(r.get('mode')=='correlation');self.duration.setValue(r.get('duration') or 600)
    def save(self):
        def work():
            r={'channel':self.channel.currentText(),'reward':self.reward.currentData(),'bot':self.bot.text().strip(),'template':self.template.text(),'action':('timeout','untimeout')[self.action.currentIndex()],'self':self.self_mute.isChecked()}
            r.update(mode=('bot','correlation')[self.mode.currentIndex()],duration=self.duration.value(),title=self.reward.currentText())
            if not r['reward'] or (r['mode']=='bot' and not r['bot']):raise ValueError('Выбери полученную награду и укажи ник бота для подтверждения ботом.')
            rules=[old for old in self.rules if (old['channel'],old['reward'])!=(r['channel'],r['reward'])]+[r]
            rules_save(self.app.data,rules);self.rules=rules;self.refresh();self.app.mod_view.request()
        self.app.safe(work)
    def remove(self):
        i=self.saved.currentRow()
        if i<0:return
        rules=self.rules[:i]+self.rules[i+1:]
        self.app.safe(lambda:rules_save(self.app.data,rules));self.rules=rules;self.refresh()
    def refresh_examples(self):
        path=self.app.data/'panel-cache/moderation.sqlite3';lines=[];previous=self.reward.currentData();self.reward.clear();seen=set()
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)) as db:
                for payload, in db.execute("SELECT payload FROM evidence WHERE channel=? AND kind IN ('reward','bot_result') ORDER BY at_ms DESC LIMIT 30",(self.channel.currentText(),)):
                    e=json.loads(payload)
                    if e['kind']=='reward':
                        title=e.get('title') or 'Награда с текстом «'+e['input'][:70]+'»'
                        if e['reward'] not in seen:self.reward.addItem(title,e['reward']);seen.add(e['reward'])
                        lines.append(title+' · покупатель @'+e['buyer'])
                    else:lines.append(e['bot']+': '+e['text'])
        except (OSError,sqlite3.Error,ValueError):pass
        self.examples.setPlainText('\n'.join(lines) or 'Сведения ещё не получены. Покупать награду для настройки не обязательно — можно дождаться чужой покупки.')
        if previous and self.reward.findData(previous)>=0:self.reward.setCurrentIndex(self.reward.findData(previous))
