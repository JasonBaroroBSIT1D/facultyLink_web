@echo off
cd /d "%~dp0"
echo Starting FacultyLink...
set DATABASE_URL=postgresql://postgres:1234@localhost:5432/facultylink
python -m pip install -r requirements.txt -q
python app.py
pause
