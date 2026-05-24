# FacultyLink

Faculty evaluation and document validation system (Admin and Reviewer roles). Runs fully on your PC  no internet needed after setup.

## Quick start (Windows)

**Option A  double-click**

1. Double-click `run.bat`
2. Open **http://127.0.0.1:5000** in your browser

**Option B  terminal**

```powershell
cd "c:\Users\gumat\OneDrive\Desktop\FacultyLink_webs"
python -m pip install -r requirements.txt
python app.py
```

Keep the terminal open while you use the app. Press `Ctrl+C` to stop the server.

## Demo accounts

| Role     | Email                    | Password      |
|----------|--------------------------|---------------|
| Admin    | admin@university.edu     | admin123      |
| Reviewer | reviewer@university.edu  | reviewer123   |
| Reviewer | reviewer2@university.edu | reviewer123   |

## Offline use

- Works without internet after Flask is installed once.
- Charts, CSS, JS, and the logo are served from the `static/` folder (no CDN).

## PostgreSQL database

By default, FacultyLink still uses the local `facultylink.db` SQLite file.
To use PostgreSQL instead, set `DATABASE_URL` before starting the app:

```powershell
$env:DATABASE_URL="postgresql://postgres:your_password@localhost:5432/facultylink"
python -m pip install -r requirements.txt
python app.py
```

Create the PostgreSQL database first, for example:

```sql
CREATE DATABASE facultylink;
```

On first run, the app creates the tables and demo accounts in PostgreSQL.

## Troubleshooting

**This site cant be reached** the server is not running. Run `run.bat` or `python app.py` first.

Use **http://** (not https) and **127.0.0.1:5000**.

Do not open HTML files directly from File Explorer; always use the URL above.
