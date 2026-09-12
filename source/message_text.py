def reply_text(reply):
    if not reply:return ''
    if reply.get('state')!='available':return '↳ Ответ: Chatterino не передал исходный текст.\n'
    author=reply.get('display_name') or reply.get('user') or 'Автор не указан'
    return f"↳ Исходное сообщение · {author}:\n{reply.get('text','')}\n"
