import json
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from config import DATABASE

STANDARD_KRA_DEFINITIONS = [
    {
        "kra_slug": "instruction",
        "kra_name": "Instruction",
        "weight": 40,
        "min_score": 70,
        "description": "Teaching effectiveness, course delivery, and student outcomes per NBC 461.",
        "validation_rules": "Teaching load verification, student evaluation forms, syllabus and course portfolio",
        "criteria": [
            "Quality of teaching and learning materials",
            "Student feedback and evaluation ratings",
            "Compliance with prescribed teaching load",
        ],
        "indicators": [
            "Average student rating ≥ 4.0/5.0",
            "Complete syllabus and course outline on file",
            "Peer/classroom observation documented",
        ],
        "point_values": [
            {"label": "Excellent teaching portfolio", "points": 40},
            {"label": "Satisfactory student evaluations", "points": 25},
            {"label": "Updated instructional materials", "points": 15},
        ],
        "documentary_requirements": [
            "Signed teaching load certificate",
            "Student evaluation summary (CHED format)",
            "Course syllabus and IMs per term",
        ],
    },
    {
        "kra_slug": "research-innovation-and-creative-work",
        "kra_name": "Research, Innovation, and Creative Work",
        "weight": 30,
        "min_score": 75,
        "description": "Research outputs, innovation, and creative works aligned with CHED standards.",
        "validation_rules": "Peer-reviewed publications, citations, funded research, IP disclosures",
        "criteria": [
            "Published research in accredited outlets",
            "Innovation and technology transfer",
            "Creative works with documented impact",
        ],
        "indicators": [
            "Minimum one peer-reviewed publication (3 years)",
            "Research grant or funded project documentation",
            "Citation or impact metric report",
        ],
        "point_values": [
            {"label": "Indexed journal article", "points": 35},
            {"label": "Research grant / funded project", "points": 30},
            {"label": "Innovation / creative work exhibit", "points": 20},
        ],
        "documentary_requirements": [
            "Copies of published articles or DOI proof",
            "Grant contract or MOA",
            "CHED research monitoring form",
        ],
    },
    {
        "kra_slug": "extension-services",
        "kra_name": "Extension Services",
        "weight": 20,
        "min_score": 65,
        "description": "Community engagement, extension programs, and public service.",
        "validation_rules": "Extension program narrative, beneficiary data, partner MOAs",
        "criteria": [
            "Community outreach and extension activities",
            "Partnerships with LGU/industry",
            "Documented social impact",
        ],
        "indicators": [
            "At least one extension program per year",
            "Beneficiary count and activity reports",
            "MOA with partner agency",
        ],
        "point_values": [
            {"label": "Major extension program lead", "points": 30},
            {"label": "Participation in extension activity", "points": 15},
            {"label": "Extension report with impact data", "points": 20},
        ],
        "documentary_requirements": [
            "Extension program terminal report",
            "Photos / attendance sheets",
            "Partner MOA or endorsement letter",
        ],
    },
    {
        "kra_slug": "professional-development",
        "kra_name": "Professional Development",
        "weight": 10,
        "min_score": 60,
        "description": "Trainings, certifications, and continuing professional development.",
        "validation_rules": "Training certificates, CPD units, graduate studies documentation",
        "criteria": [
            "Relevant trainings and seminars",
            "Professional licenses and certifications",
            "Advanced degree or credential progress",
        ],
        "indicators": [
            "Minimum 40 CPD units (if applicable)",
            "Certificate of training completion",
            "License renewal proof",
        ],
        "point_values": [
            {"label": "National/international training", "points": 25},
            {"label": "Professional certification", "points": 20},
            {"label": "Graduate studies units completed", "points": 30},
        ],
        "documentary_requirements": [
            "Training certificates with hours/CPD",
            "PRC license (if applicable)",
            "Transcript or graduate study proof",
        ],
    },
]


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


def migrate_faculty_role(conn):
    """Allow faculty role on existing databases created before the role was added."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if not row or not row[0] or "'faculty'" in row[0]:
        return
    conn.executescript("""
        CREATE TABLE users_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'reviewer', 'faculty')),
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO users_new SELECT * FROM users;
        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
    """)
    conn.commit()


def migrate_kra_rules_extended(conn):
    """Add NBC 461 configuration columns and ensure standard KRAs exist."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(kra_rules)").fetchall()}
    new_cols = [
        ("kra_slug", "TEXT"),
        ("description", "TEXT"),
        ("criteria_json", "TEXT"),
        ("indicators_json", "TEXT"),
        ("point_values_json", "TEXT"),
        ("documentary_json", "TEXT"),
        ("auto_compute", "INTEGER DEFAULT 1"),
    ]
    for name, col_type in new_cols:
        if name not in cols:
            conn.execute(f"ALTER TABLE kra_rules ADD COLUMN {name} {col_type}")

    legacy_names = {
        "Research": "research-innovation-and-creative-work",
        "Extension": "extension-services",
        "Prof Dev": "professional-development",
        "Instruction": "instruction",
    }
    for old_name, slug in legacy_names.items():
        conn.execute(
            "UPDATE kra_rules SET kra_slug = ? WHERE kra_name = ? AND (kra_slug IS NULL OR kra_slug = '')",
            (slug, old_name),
        )

    for kra in STANDARD_KRA_DEFINITIONS:
        row = conn.execute(
            "SELECT id FROM kra_rules WHERE kra_slug = ?", (kra["kra_slug"],)
        ).fetchone()
        payload = (
            kra["kra_name"],
            kra["weight"],
            kra["min_score"],
            kra["validation_rules"],
            kra["description"],
            json.dumps(kra["criteria"]),
            json.dumps(kra["indicators"]),
            json.dumps(kra["point_values"]),
            json.dumps(kra["documentary_requirements"]),
            1,
            kra["kra_slug"],
        )
        if row:
            conn.execute(
                """UPDATE kra_rules SET
                   kra_name=?, weight=?, min_score=?, validation_rules=?, description=?,
                   criteria_json=?, indicators_json=?, point_values_json=?, documentary_json=?,
                   auto_compute=?, kra_slug=?, updated_at=datetime('now')
                   WHERE id=?""",
                (*payload, row[0]),
            )
        else:
            conn.execute(
                """INSERT INTO kra_rules
                   (kra_name, weight, min_score, validation_rules, description,
                    criteria_json, indicators_json, point_values_json, documentary_json,
                    auto_compute, kra_slug)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                payload,
            )

    defaults = {
        "promotion_min_total_score": "75",
        "ched_compliance_required": "1",
        "dbm_circular_version": "DBM-CHED Joint Circular No. 2022-1 (NBC No. 461)",
        "auto_score_enabled": "1",
        "reviewer_validation_required": "1",
    }
    for key, value in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO system_config (key, value) VALUES (?, ?)",
            (key, value),
        )
    conn.commit()


def init_db():
    conn = _new_connection()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'reviewer', 'faculty')),
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
    migrate_faculty_role(conn)
    migrate_kra_rules_extended(conn)
    seed_data(conn)
    conn.close()


def get_system_config(conn):
    rows = conn.execute("SELECT key, value FROM system_config").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_system_config(conn, key, value):
    conn.execute(
        "INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)",
        (key, str(value)),
    )


def fetch_kra_rules(conn):
    rows = conn.execute("SELECT * FROM kra_rules ORDER BY id").fetchall()
    return [dict(r) for r in rows]


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

    if c.execute("SELECT COUNT(*) FROM kra_rules").fetchone()[0] == 0:
        for kra in STANDARD_KRA_DEFINITIONS:
            c.execute(
                """INSERT INTO kra_rules
                   (kra_name, weight, min_score, validation_rules, description,
                    criteria_json, indicators_json, point_values_json, documentary_json,
                    auto_compute, kra_slug)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    kra["kra_name"],
                    kra["weight"],
                    kra["min_score"],
                    kra["validation_rules"],
                    kra["description"],
                    json.dumps(kra["criteria"]),
                    json.dumps(kra["indicators"]),
                    json.dumps(kra["point_values"]),
                    json.dumps(kra["documentary_requirements"]),
                    1,
                    kra["kra_slug"],
                ),
            )
    for key, value in [
        ("promotion_min_total_score", "75"),
        ("ched_compliance_required", "1"),
        ("dbm_circular_version", "DBM-CHED Joint Circular No. 2022-1 (NBC No. 461)"),
        ("auto_score_enabled", "1"),
        ("reviewer_validation_required", "1"),
    ]:
        c.execute(
            "INSERT INTO system_config (key, value) VALUES (?, ?)",
            (key, value),
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
