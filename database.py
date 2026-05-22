import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from config import DATABASE


def _new_connection():
    conn = sqlite3.connect(DATABASE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_db():
    """Return one SQLite connection per Flask request (avoids 'database is locked')."""
    try:
        from flask import g, has_app_context
    except ImportError:
        has_app_context = lambda: False  # noqa: E731

    if has_app_context():
        if "db" not in g:
            g.db = _new_connection()
        return g.db
    return _new_connection()


def close_db(_exc=None):
    from flask import g

    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = _new_connection()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'reviewer')),
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            faculty_name TEXT NOT NULL,
            faculty_email TEXT,
            document_title TEXT NOT NULL,
            kra_type TEXT,
            status TEXT DEFAULT 'pending',
            compliance_status TEXT DEFAULT 'pending',
            ocr_confidence REAL DEFAULT 0,
            ocr_text TEXT,
            reviewer_id INTEGER,
            reviewer_comments TEXT,
            submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (reviewer_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            message TEXT,
            type TEXT DEFAULT 'info',
            read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS kra_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kra_name TEXT NOT NULL,
            weight REAL DEFAULT 0,
            min_score REAL DEFAULT 0,
            validation_rules TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()
    seed_data(conn)
    conn.close()


def seed_data(conn):
    c = conn.cursor()
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        return

    users = [
        ("admin@university.edu", "admin123", "Dr. Aris Thorne", "admin"),
        ("reviewer@university.edu", "reviewer123", "Prof. Maria Santos", "reviewer"),
        ("reviewer2@university.edu", "reviewer123", "Dr. James Chen", "reviewer"),
    ]
    for email, pwd, name, role in users:
        c.execute(
            "INSERT INTO users (email, password_hash, full_name, role) VALUES (?, ?, ?, ?)",
            (email, generate_password_hash(pwd), name, role),
        )

    submissions = [
        ("Dr. Elena Reyes", "elena.reyes@university.edu", "Research Publication Portfolio 2025", "Research", "pending", "under_review", 87.5),
        ("Prof. Michael Tan", "m.tan@university.edu", "Extension Service Report Q2", "Extension", "approved", "compliant", 94.2),
        ("Dr. Sofia Lim", "s.lim@university.edu", "Instructional Materials Portfolio", "Instruction", "pending", "non_compliant", 62.1),
        ("Dr. Carlos Vega", "c.vega@university.edu", "Professional Development Certificates", "Prof Dev", "rejected", "non_compliant", 45.0),
        ("Prof. Anna Cruz", "a.cruz@university.edu", "CHED-DBM Compliance Documents", "Research", "pending", "pending", 78.3),
    ]
    ocr_samples = [
        "FACULTY RESEARCH PORTFOLIO\nName: Dr. Elena Reyes\nRank: Associate Professor\nPublications: 12 peer-reviewed articles (2023-2025)\nKRA: Research - 85%",
        "EXTENSION SERVICE REPORT\nCommunity outreach programs: 4\nBeneficiaries: 2,400\nKRA: Extension - 92%",
        "INSTRUCTIONAL PORTFOLIO\nCourses taught: 6\nStudent evaluation avg: 4.2/5.0\nMissing: CHED form appendix B",
        "PROF DEV CERTIFICATES\nWorkshops: 3\nMissing: official transcript verification",
        "DBM-CHED JOINT CIRCULAR COMPLIANCE\nDocument type: Rank upgrade application\nAll required fields present.",
    ]
    for i, (name, email, title, kra, status, compliance, conf) in enumerate(submissions):
        c.execute(
            """INSERT INTO submissions
               (faculty_name, faculty_email, document_title, kra_type, status,
                compliance_status, ocr_confidence, ocr_text, reviewer_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, email, title, kra, status, compliance, conf, ocr_samples[i], 2 if i % 2 == 0 else None),
        )

    notifications = [
        ("System Overload Warning", "Document validation queue exceeded threshold.", "urgent"),
        ("New Reviewer Assigned", "Dr. James Chen assigned to Research submissions.", "info"),
        ("Monthly Report Ready", "April 2026 compliance report is available.", "success"),
        ("Pending Review Reminder", "12 submissions awaiting validation.", "warning"),
        ("Validation Update", "Prof. Michael Tan submission approved.", "success"),
    ]
    for title, msg, ntype in notifications:
        c.execute(
            "INSERT INTO notifications (title, message, type) VALUES (?, ?, ?)",
            (title, msg, ntype),
        )

    kra_rules = [
        ("Instruction", 25, 70, "Teaching load, student eval, syllabus documentation"),
        ("Research", 30, 75, "Publications, citations, research grants"),
        ("Extension", 25, 65, "Community service, outreach documentation"),
        ("Prof Dev", 20, 60, "Trainings, certifications, workshops"),
    ]
    for name, weight, min_score, rules in kra_rules:
        c.execute(
            "INSERT INTO kra_rules (kra_name, weight, min_score, validation_rules) VALUES (?, ?, ?, ?)",
            (name, weight, min_score, rules),
        )

    logs = [
        (1, "Document Validated", "Research portfolio approved for Dr. Elena Reyes"),
        (1, "New Faculty Registered", "Prof. Anna Cruz added to archive"),
        (2, "Submission Reviewed", "Extension report validated"),
        (1, "Archive Sync Complete", "Academic archive v2.4 synchronized"),
        (1, "Rule Updated", "KRA Research weight adjusted to 30%"),
    ]
    for uid, action, details in logs:
        c.execute(
            "INSERT INTO audit_logs (user_id, action, details) VALUES (?, ?, ?)",
            (uid, action, details),
        )

    conn.commit()


def verify_user(email, password):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ? AND active = 1", (email,)).fetchone()
    if user and check_password_hash(user["password_hash"], password):
        return dict(user)
    return None


def log_action(user_id, action, details=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO audit_logs (user_id, action, details) VALUES (?, ?, ?)",
        (user_id, action, details),
    )
    conn.commit()
