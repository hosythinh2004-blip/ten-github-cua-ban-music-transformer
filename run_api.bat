@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python khong duoc tim thay trong PATH.
  echo Hay cai Python 3.12 roi chay lai.
  pause
  exit /b 1
)

echo Dang cai/cap nhat dependency...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Cai dependency that bai.
  pause
  exit /b 1
)

echo.
echo API dang chay tai: http://127.0.0.1:8000
start "" http://127.0.0.1:8000/docs
python -m uvicorn api_server:app --host 127.0.0.1 --port 8000

pause
