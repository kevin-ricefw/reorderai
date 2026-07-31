@echo off
cd /d "%~dp0"
set API_BASE=http://127.0.0.1:8001
echo Starting Streamlit demo (expects API at %API_BASE%)
echo Make sure uvicorn is running first, e.g.:
echo   python -m uvicorn api.main:app --host 0.0.0.0 --port 8001
echo.
python -m streamlit run demo_app/streamlit_app.py --server.port 8501
pause
