# Сборка Windows x64

Нужны Python 3.12 x64, Git и Inno Setup 6.3+ для установщика. Из корня репозитория:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r source/requirements-build.txt
.venv\Scripts\python -m unittest discover -s source -p 'test_*.py'
.venv\Scripts\python -m PyInstaller --noconfirm --clean --distpath dist --workpath build source/ChatterinoArchive-by-Mist1X.spec
.venv\Scripts\python tools/build_release.py
.venv\Scripts\python tools/build_installer.py
```

Результат: releases/ChatterinoArchive-Windows-x64.zip , ChatterinoArchive-Setup-VERSION-x64.exe и SHA256SUMS.txt. Сборщик читает только свежую папку dist и файлы проекта. Профили и установленные приложения источниками сборки не являются.

Перед следующим выпуском измените VERSION и VERSION_TUPLE в source/branding.py и RELEASE-NOTES.md. Отправьте коммит и соответствующий тег vX.Y.Z. Workflow создаёт черновик GitHub Release; публикация вручную включает уведомления об обновлении.

Qt-тесты используют временные профили и подменённую сеть. Windows DPAPI-тесты запускаются в обычном контексте пользователя. Дополнительные Lua-тесты запускаются Lua 5.4 с путём plugin и отдельным временным каталогом: lua source/test_plugin.lua plugin TEMP_DIR (аналогично test_moderation.lua и test_replies.lua).

Обновлятор проверяет SHA-256 из GitHub Release API и внутренний манифест. Приложение не подписано сертификатом издателя. Динамические Qt-библиотеки можно заменить совместимыми версиями. Исходники и лицензии сторонних библиотек перечислены в licenses.

Если компилятор находится в другой папке: `python tools/build_installer.py --compiler "путь/ISCC.exe"`. Установщик собирается только из проверенного чистого ZIP. Папка данных не входит в него и не удаляется при деинсталляции.
