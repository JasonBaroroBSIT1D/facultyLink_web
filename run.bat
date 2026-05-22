@echo off
cd /d "%~dp0"
echo Starting FacultyLink...
python -m pip install -r requirements.txt -q
python app.py
pause
