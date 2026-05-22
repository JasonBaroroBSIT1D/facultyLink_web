import os
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, g,
)
from werkzeug.security import generate_password_hash
from config import SECRET_KEY
from database import get_db, init_db, verify_user, log_action, close_db

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.teardown_appcontext(close_db)


@app.before_request
def load_user():
    g.user = None
    if "user_id" in session:
        row = get_db().execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        if row:
            g.user = dict(row)


def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not g.user:
                return redirect(url_for("login"))
            if role and g.user["role"] != role:
                flash("You do not have permission to access that page.", "error")
                return redirect(url_for(f"{g.user['role']}_dashboard"))
            return f(*args, **kwargs)
        return wrapped
    return decorator


@app.route("/")
def index():
    if g.user:
        return redirect(url_for(f"{g.user['role']}_dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for(f"{g.user['role']}_dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember")
        user = verify_user(email, password)
        if user:
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session.permanent = bool(remember)
            log_action(user["id"], "User Login", f"{user['email']} logged in")
            return redirect(url_for(f"{user['role']}_dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    if g.user:
        log_action(g.user["id"], "User Logout", f"{g.user['email']} logged out")
    session.clear()
    return redirect(url_for("login"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        flash("Password reset instructions sent to your institution email.", "success")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")


# ─── Admin routes ───────────────────────────────────────────────

@app.route("/admin/dashboard")
@login_required("admin")
def admin_dashboard():
    conn = get_db()
    stats = {
        "faculty": conn.execute("SELECT COUNT(DISTINCT faculty_email) FROM submissions").fetchone()[0],
        "reviewers": conn.execute("SELECT COUNT(*) FROM users WHERE role='reviewer' AND active=1").fetchone()[0],
        "documents": conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0],
        "pending": conn.execute("SELECT COUNT(*) FROM submissions WHERE status='pending'").fetchone()[0],
    }
    notifications = conn.execute(
        "SELECT * FROM notifications ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    activity = conn.execute(
        """SELECT a.*, u.full_name FROM audit_logs a
           LEFT JOIN users u ON a.user_id = u.id
           ORDER BY a.created_at DESC LIMIT 6"""
    ).fetchall()
    kra = conn.execute("SELECT * FROM kra_rules").fetchall()
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        notifications=[dict(n) for n in notifications],
        activity=[dict(a) for a in activity],
        kra=[dict(k) for k in kra],
    )


@app.route("/admin/users", methods=["GET", "POST"])
@login_required("admin")
def admin_users():
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        uid = request.form.get("user_id")
        if action == "create":
            email = request.form.get("email", "").strip().lower()
            name = request.form.get("full_name", "").strip()
            pwd = request.form.get("password", "reviewer123")
            conn.execute(
                "INSERT INTO users (email, password_hash, full_name, role) VALUES (?, ?, ?, 'reviewer')",
                (email, generate_password_hash(pwd), name),
            )
            log_action(g.user["id"], "Reviewer Created", f"Created account for {email}")
            flash(f"Reviewer account created for {name}.", "success")
        elif action == "toggle" and uid:
            conn.execute("UPDATE users SET active = 1 - active WHERE id = ? AND role='reviewer'", (uid,))
            log_action(g.user["id"], "Account Toggled", f"User ID {uid}")
            flash("Account status updated.", "success")
        elif action == "reset" and uid:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash("reviewer123"), uid),
            )
            flash("Password reset to default: reviewer123", "success")
        conn.commit()
    users = conn.execute(
        "SELECT u.*, (SELECT COUNT(*) FROM submissions s WHERE s.reviewer_id = u.id) as review_count "
        "FROM users u ORDER BY role, full_name"
    ).fetchall()
    return render_template("admin/users.html", users=[dict(u) for u in users])


@app.route("/admin/submissions")
@login_required("admin")
def admin_submissions():
    conn = get_db()
    status = request.args.get("status", "")
    kra = request.args.get("kra", "")
    q = request.args.get("q", "")
    query = "SELECT s.*, u.full_name as reviewer_name FROM submissions s LEFT JOIN users u ON s.reviewer_id = u.id WHERE 1=1"
    params = []
    if status:
        query += " AND s.status = ?"
        params.append(status)
    if kra:
        query += " AND s.kra_type = ?"
        params.append(kra)
    if q:
        query += " AND (s.faculty_name LIKE ? OR s.document_title LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    query += " ORDER BY s.submitted_at DESC"
    rows = conn.execute(query, params).fetchall()
    return render_template("admin/submissions.html", submissions=[dict(r) for r in rows])


@app.route("/admin/analytics")
@login_required("admin")
def admin_analytics():
    conn = get_db()
    by_status = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM submissions GROUP BY status"
    ).fetchall()
    by_kra = conn.execute(
        "SELECT kra_type, COUNT(*) as cnt, AVG(ocr_confidence) as avg_conf FROM submissions GROUP BY kra_type"
    ).fetchall()
    by_compliance = conn.execute(
        "SELECT compliance_status, COUNT(*) as cnt FROM submissions GROUP BY compliance_status"
    ).fetchall()
    reviewer_activity = conn.execute(
        """SELECT u.full_name, COUNT(s.id) as reviews
           FROM users u LEFT JOIN submissions s ON s.reviewer_id = u.id
           WHERE u.role='reviewer' GROUP BY u.id"""
    ).fetchall()
    return render_template(
        "admin/analytics.html",
        by_status=[dict(r) for r in by_status],
        by_kra=[dict(r) for r in by_kra],
        by_compliance=[dict(r) for r in by_compliance],
        reviewer_activity=[dict(r) for r in reviewer_activity],
    )


@app.route("/admin/notifications", methods=["GET", "POST"])
@login_required("admin")
def admin_notifications():
    conn = get_db()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        message = request.form.get("message", "").strip()
        ntype = request.form.get("type", "info")
        if title:
            conn.execute(
                "INSERT INTO notifications (title, message, type) VALUES (?, ?, ?)",
                (title, message, ntype),
            )
            conn.commit()
            flash("Announcement published.", "success")
    rows = conn.execute("SELECT * FROM notifications ORDER BY created_at DESC").fetchall()
    return render_template("admin/notifications.html", notifications=[dict(n) for n in rows])


@app.route("/admin/audit-logs")
@login_required("admin")
def admin_audit():
    conn = get_db()
    rows = conn.execute(
        """SELECT a.*, u.full_name, u.email FROM audit_logs a
           LEFT JOIN users u ON a.user_id = u.id ORDER BY a.created_at DESC LIMIT 100"""
    ).fetchall()
    return render_template("admin/audit.html", logs=[dict(r) for r in rows])


@app.route("/admin/kra-config", methods=["GET", "POST"])
@login_required("admin")
def admin_kra_config():
    conn = get_db()
    if request.method == "POST":
        for key in request.form:
            if key.startswith("weight_"):
                rid = key.replace("weight_", "")
                weight = float(request.form.get(key, 0))
                min_score = float(request.form.get(f"min_{rid}", 0))
                rules = request.form.get(f"rules_{rid}", "")
                conn.execute(
                    "UPDATE kra_rules SET weight=?, min_score=?, validation_rules=?, updated_at=datetime('now') WHERE id=?",
                    (weight, min_score, rules, rid),
                )
        conn.commit()
        log_action(g.user["id"], "KRA Rules Updated", "DBM-CHED configuration saved")
        flash("KRA scoring rules saved successfully.", "success")
    rules = conn.execute("SELECT * FROM kra_rules ORDER BY id").fetchall()
    return render_template("admin/kra_config.html", rules=[dict(r) for r in rules])


@app.route("/admin/reviewer-assignment", methods=["GET", "POST"])
@login_required("admin")
def admin_reviewer_assignment():
    conn = get_db()
    if request.method == "POST":
        sub_id = request.form.get("submission_id")
        rev_id = request.form.get("reviewer_id")
        if sub_id and rev_id:
            conn.execute(
                "UPDATE submissions SET reviewer_id=?, updated_at=datetime('now') WHERE id=?",
                (rev_id, sub_id),
            )
            conn.commit()
            flash("Reviewer assigned successfully.", "success")
    submissions = conn.execute(
        "SELECT s.*, u.full_name as reviewer_name FROM submissions s "
        "LEFT JOIN users u ON s.reviewer_id = u.id ORDER BY s.status, s.submitted_at DESC"
    ).fetchall()
    reviewers = conn.execute(
        "SELECT * FROM users WHERE role='reviewer' AND active=1"
    ).fetchall()
    return render_template(
        "admin/reviewer_assignment.html",
        submissions=[dict(s) for s in submissions],
        reviewers=[dict(r) for r in reviewers],
    )


# ─── Reviewer routes ────────────────────────────────────────────

@app.route("/reviewer/dashboard")
@login_required("reviewer")
def reviewer_dashboard():
    conn = get_db()
    uid = g.user["id"]
    stats = {
        "assigned": conn.execute(
            "SELECT COUNT(*) FROM submissions WHERE reviewer_id=? OR reviewer_id IS NULL",
            (uid,),
        ).fetchone()[0],
        "pending": conn.execute(
            "SELECT COUNT(*) FROM submissions WHERE status='pending' AND (reviewer_id=? OR reviewer_id IS NULL)",
            (uid,),
        ).fetchone()[0],
        "approved": conn.execute(
            "SELECT COUNT(*) FROM submissions WHERE status='approved' AND reviewer_id=?",
            (uid,),
        ).fetchone()[0],
        "rejected": conn.execute(
            "SELECT COUNT(*) FROM submissions WHERE status='rejected' AND reviewer_id=?",
            (uid,),
        ).fetchone()[0],
    }
    pending = conn.execute(
        """SELECT * FROM submissions WHERE status='pending'
           AND (reviewer_id=? OR reviewer_id IS NULL) ORDER BY submitted_at DESC LIMIT 5""",
        (uid,),
    ).fetchall()
    notifications = conn.execute(
        "SELECT * FROM notifications ORDER BY created_at DESC LIMIT 4"
    ).fetchall()
    return render_template(
        "reviewer/dashboard.html",
        stats=stats,
        pending=[dict(p) for p in pending],
        notifications=[dict(n) for n in notifications],
    )


@app.route("/reviewer/submissions")
@login_required("reviewer")
def reviewer_submissions():
    conn = get_db()
    status = request.args.get("status", "")
    query = "SELECT * FROM submissions WHERE reviewer_id=? OR reviewer_id IS NULL"
    params = [g.user["id"]]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY submitted_at DESC"
    rows = conn.execute(query, params).fetchall()
    return render_template("reviewer/submissions.html", submissions=[dict(r) for r in rows])


@app.route("/reviewer/review/<int:sub_id>", methods=["GET", "POST"])
@login_required("reviewer")
def reviewer_review(sub_id):
    conn = get_db()
    sub = conn.execute("SELECT * FROM submissions WHERE id=?", (sub_id,)).fetchone()
    if not sub:
        flash("Submission not found.", "error")
        return redirect(url_for("reviewer_submissions"))
    if request.method == "POST":
        action = request.form.get("action")
        comments = request.form.get("comments", "")
        status = "approved" if action == "approve" else "rejected"
        compliance = request.form.get("compliance_status", sub["compliance_status"])
        conn.execute(
            """UPDATE submissions SET status=?, compliance_status=?, reviewer_comments=?,
               reviewer_id=?, updated_at=datetime('now') WHERE id=?""",
            (status, compliance, comments, g.user["id"], sub_id),
        )
        conn.commit()
        log_action(g.user["id"], f"Submission {status.title()}", f"Submission #{sub_id}: {comments[:80]}")
        flash(f"Submission {status}.", "success")
        return redirect(url_for("reviewer_submissions"))
    return render_template("reviewer/review.html", submission=dict(sub))


@app.route("/reviewer/ocr/<int:sub_id>")
@login_required("reviewer")
def reviewer_ocr(sub_id):
    conn = get_db()
    sub = conn.execute("SELECT * FROM submissions WHERE id=?", (sub_id,)).fetchone()
    if not sub:
        flash("Submission not found.", "error")
        return redirect(url_for("reviewer_submissions"))
    missing = ["CHED Form Appendix B", "Official Signature Block"] if sub["ocr_confidence"] < 80 else []
    duplicate = sub["ocr_confidence"] < 50
    return render_template(
        "reviewer/ocr.html",
        submission=dict(sub),
        missing_fields=missing,
        duplicate_detected=duplicate,
    )


@app.route("/api/chart-data")
@login_required()
def chart_data():
    conn = get_db()
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul"]
    counts = [120, 145, 132, 198, 167, 189, 210]
    return jsonify({"labels": months, "data": counts})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
