@echo off
cd /d %~dp0
if not exist .env (
  copy .env.example .env >nul
  echo.
  echo [VEO AUTO VIDEO STUDIO] Da tao file .env.
  echo Mo .env va dien GEMINI_API_KEY, sau do chay lai start.bat.
  pause
  exit /b 0
)
if not exist node_modules (
  echo Dang cai thu vien...
  call npm install
  if errorlevel 1 pause & exit /b 1
)
start "" http://localhost:3188
npm start
pause
