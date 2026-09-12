"""A portable installation stores its data choice outside the application folder."""
import json
from pathlib import Path
import sys
from storage import atomic_write

def pointer_path():
    executable=Path(sys.executable if getattr(sys,'frozen',False) else __file__).resolve()
    return executable.parent.parent/'archive-profile.json'

def saved_profile():
    try:
        value=Path(json.loads(pointer_path().read_text(encoding='utf-8'))['profile'])
        return value if value.is_absolute() and (value/'Settings').is_dir() else None
    except (OSError,ValueError,KeyError,TypeError):return None

def remember_profile(profile):
    profile=Path(profile).resolve()
    executable=Path(sys.executable if getattr(sys,'frozen',False) else __file__).resolve()
    if profile==executable.parent or profile.is_relative_to(executable.parent):
        raise ValueError('Выбери папку данных вне папки app, чтобы обновления не затрагивали записи.')
    (profile/'Settings').mkdir(parents=True,exist_ok=True)
    atomic_write(pointer_path(),json.dumps({'profile':str(profile)},ensure_ascii=False))
    return profile

def choose_profile():
    from PySide6.QtWidgets import QFileDialog,QMessageBox
    from branding import APP_NAME
    QMessageBox.information(None,APP_NAME,'Выбери отдельную папку для данных архива. Для подключения существующего плагина можно выбрать профиль Chatterino, содержащий папку Settings. Записи и настройки останутся в выбранной папке при обновлениях.')
    while True:
        folder=QFileDialog.getExistingDirectory(None,'Папка для данных архива')
        if not folder:return None
        try:return remember_profile(folder)
        except (OSError,ValueError) as exc:QMessageBox.warning(None,APP_NAME,str(exc))
