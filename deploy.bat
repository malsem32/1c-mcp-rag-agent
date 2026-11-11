@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title RAG-агент 1С - развертывание
echo ============================================================
echo  RAG-агент 1С: развертывание
echo ============================================================

rem --- 1. Python ---
set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE (where python >nul 2>&1 && set "PYEXE=python")
if not defined PYEXE (
  echo [ОШИБКА] Python 3.10+ не найден. Установите с python.org и отметьте "Add to PATH".
  pause & exit /b 1
)

rem --- 2. venv ---
if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Создаю виртуальное окружение .venv ...
  %PYEXE% -m venv .venv || ( echo [ОШИБКА] не удалось создать .venv & pause & exit /b 1 )
) else (
  echo [1/4] .venv уже есть
)

rem --- 3. зависимости ---
echo [2/4] Устанавливаю зависимости (первый раз 2-5 минут) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q || ( echo [ОШИБКА] pip install & pause & exit /b 1 )

rem --- 4. config.yaml и .env ---
if not exist "config.yaml" (
  copy /y "config.example.yaml" "config.yaml" >nul
  echo [3/4] Создан config.yaml из шаблона
) else (
  echo [3/4] config.yaml уже есть
)
if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo       Создан .env из шаблона - ключи задайте в панели
) else (
  echo       .env уже есть
)

rem --- 5. Qdrant ---
where docker >nul 2>&1
if %errorlevel%==0 (
  echo [4/4] Запускаю Qdrant в Docker ...
  docker compose up -d qdrant
  if errorlevel 1 echo [ПРЕДУПРЕЖДЕНИЕ] Docker не запустил Qdrant - в панели переключите Qdrant.mode на local
) else (
  echo [4/4] Docker не найден - в панели выберите Qdrant: режим "local" ^(встроенная папка^)
)

echo.
echo Готово. Открываю панель настроек: http://127.0.0.1:8765
echo Дальше: в панели откроется "Первый запуск" - пройдите 4 шага.
echo.
".venv\Scripts\python.exe" panel\server.py
endlocal
