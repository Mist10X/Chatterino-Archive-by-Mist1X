import unittest
from user_history import event_message,event_summary,event_table_summary

def message(text,seconds,automod=False,state='available'):
    return {'text':text,'time_utc':f'2026-09-10T17:00:{seconds:02}.000Z','automod':automod,'state':state}

class EventSummaryTests(unittest.TestCase):
    def test_latest_message_for_ban_and_timeout(self):
        row={'kind':'ban','context':[message('раньше',1),message('последнее сообщение',2)]}
        self.assertEqual(event_summary(row),'последнее сообщение')
        row['kind']='timeout';self.assertEqual(event_summary(row),'последнее сообщение')
    def test_latest_automod_is_marked_and_equal_time_has_priority(self):
        row={'kind':'ban','context':[message('обычное',2),message('задержано',2,True)]}
        self.assertIs(event_message(row),row['context'][1])
        self.assertEqual(event_summary(row),'AutoMod · задержано')
        row['context'].append(message('позже опубликовано',3))
        self.assertEqual(event_summary(row),'позже опубликовано')
    def test_delete_uses_exact_deleted_message_before_context(self):
        row={'kind':'delete','message':message('удалено именно это',4),'context':[message('до удаления',3)]}
        self.assertEqual(event_summary(row),'удалено именно это')
        row['message']['state']='unavailable';self.assertEqual(event_summary(row),'до удаления')
    def test_missing_whitespace_and_truncation(self):
        self.assertEqual(event_summary({'kind':'ban','context':[]}),'Сообщение недоступно')
        self.assertEqual(event_summary({'kind':'timeout','context':[message('  одна\n строка  ',1)]}),'одна строка')
        value=event_summary({'kind':'ban','context':[message('x'*300,1)]},20)
        self.assertEqual(len(value),20);self.assertTrue(value.endswith('…'))
    def test_replaced_punishment_keeps_status_and_message(self):
        row={'kind':'timeout','status':'Заменён новым наказанием','context':[message('последнее',1)]}
        self.assertEqual(event_table_summary(row),'Заменён новым наказанием · последнее')
        row['status']='Мут действует';self.assertEqual(event_table_summary(row),'последнее')

if __name__=='__main__':unittest.main()
