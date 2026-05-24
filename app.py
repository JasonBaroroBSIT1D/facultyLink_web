import json
import os
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, g,
)
from werkzeug.security import generate_password_hash, check_password_hash
from config import SECRET_KEY
from database import (
    get_db, init_db, verify_user, log_action, close_db,
    get_system_config, set_system_config, fetch_kra_rules, auto_notify,
)
from kra_scoring import (
    rule_to_dict,
    compute_kra_breakdown,
    check_reclassification,
    default_simulation_scores,
)

VALID_ROLES = ("admin", "reviewer", "faculty")
ROLE_LABELS = {
    "admin": "System Administrator",
    "reviewer": "Reviewer",
    "faculty": "Faculty",
}


def user_home_endpoint(role):
    if role == "faculty":
        return "profile"
    return f"{role}_dashboard"

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.teardown_appcontext(close_db)


@app.context_processor
def inject_role_labels():
    return {"role_labels": ROLE_LABELS}


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
                return redirect(url_for(user_home_endpoint(g.user["role"])))
            return f(*args, **kwargs)
        return wrapped
    return decorator


@app.route("/")
def index():
    if g.user:
        return redirect(url_for(user_home_endpoint(g.user["role"])))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for(user_home_endpoint(g.user["role"])))
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
            return redirect(url_for(user_home_endpoint(user["role"])))
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
    _check_and_generate_auto_notifications()
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
    rules = fetch_kra_rules(conn)
    kra_scores = _scores_from_submissions(conn, rules)
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        notifications=[dict(n) for n in notifications],
        activity=[dict(a) for a in activity],
        kra=[rule_to_dict(r) for r in rules],
        kra_scores=kra_scores,
    )


def _kra_type_to_slug(kra_type):
    mapping = {
        "Instruction": "instruction",
        "Research": "research-innovation-and-creative-work",
        "Extension": "extension-services",
        "Prof Dev": "professional-development",
        "Professional Development": "professional-development",
        "Extension Services": "extension-services",
        "Research, Innovation, and Creative Work": "research-innovation-and-creative-work",
    }
    return mapping.get(kra_type, kra_type.lower().replace(" ", "-") if kra_type else "")


def _scores_from_submissions(conn, rules):
    rows = conn.execute(
        "SELECT kra_type, AVG(ocr_confidence) as avg_score FROM submissions "
        "WHERE kra_type IS NOT NULL GROUP BY kra_type"
    ).fetchall()
    raw = {}
    for row in rows:
        slug = _kra_type_to_slug(row["kra_type"])
        if slug:
            raw[slug] = float(row["avg_score"] or 0)
    if not raw:
        raw = default_simulation_scores(rules)
    return compute_kra_breakdown(rules, raw)


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
            role = request.form.get("role", "reviewer").strip().lower()
            if role not in VALID_ROLES:
                flash("Invalid role selected.", "error")
            elif not email or not name:
                flash("Full name and email are required.", "error")
            else:
                try:
                    conn.execute(
                        "INSERT INTO users (email, password_hash, full_name, role) VALUES (?, ?, ?, ?)",
                        (email, generate_password_hash(pwd), name, role),
                    )
                    log_action(g.user["id"], "User Created", f"Created {role} account for {email}")
                    flash(f"{ROLE_LABELS[role]} account created for {name}.", "success")
                except Exception:
                    flash("Could not create account. Email may already be in use.", "error")
        elif action == "toggle" and uid:
            conn.execute(
                "UPDATE users SET active = 1 - active WHERE id = ? AND role IN ('reviewer', 'faculty')",
                (uid,),
            )
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
    return render_template(
        "admin/users.html",
        users=[dict(u) for u in users],
        role_labels=ROLE_LABELS,
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required()
def profile():
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "update_profile":
            name = request.form.get("full_name", "").strip()
            if not name:
                flash("Full name is required.", "error")
            else:
                conn.execute(
                    "UPDATE users SET full_name = ? WHERE id = ?",
                    (name, g.user["id"]),
                )
                conn.commit()
                log_action(g.user["id"], "Profile Updated", "Name updated")
                flash("Profile updated successfully.", "success")
        elif action == "change_password":
            current = request.form.get("current_password", "")
            new_pwd = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            row = conn.execute(
                "SELECT password_hash FROM users WHERE id = ?", (g.user["id"],)
            ).fetchone()
            if not check_password_hash(row["password_hash"], current):
                flash("Current password is incorrect.", "error")
            elif len(new_pwd) < 6:
                flash("New password must be at least 6 characters.", "error")
            elif new_pwd != confirm:
                flash("New passwords do not match.", "error")
            else:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(new_pwd), g.user["id"]),
                )
                conn.commit()
                log_action(g.user["id"], "Password Changed", "User changed password")
                flash("Password changed successfully.", "success")
        return redirect(request.referrer or url_for(user_home_endpoint(g.user["role"])))

    return redirect(url_for(user_home_endpoint(g.user["role"])))


@app.route("/admin/submissions")
@login_required("admin")
def admin_submissions():
    conn = get_db()
    status = request.args.get("status", "")
    kra = request.args.get("kra", "")
    compliance = request.args.get("compliance", "")
    q = request.args.get("q", "")

    query = """SELECT s.*, u.full_name as reviewer_name
               FROM submissions s LEFT JOIN users u ON s.reviewer_id = u.id WHERE 1=1"""
    params = []
    if status:
        query += " AND s.status = ?"
        params.append(status)
    if kra:
        query += " AND s.kra_type = ?"
        params.append(kra)
    if compliance:
        query += " AND s.compliance_status = ?"
        params.append(compliance)
    if q:
        query += " AND (s.faculty_name LIKE ? OR s.document_title LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    query += " ORDER BY s.submitted_at DESC"
    rows = conn.execute(query, params).fetchall()

    # Summary stats for analytics cards
    total      = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
    pending    = conn.execute("SELECT COUNT(*) FROM submissions WHERE status='pending'").fetchone()[0]
    approved   = conn.execute("SELECT COUNT(*) FROM submissions WHERE status='approved'").fetchone()[0]
    rejected   = conn.execute("SELECT COUNT(*) FROM submissions WHERE status='rejected'").fetchone()[0]
    unassigned = conn.execute("SELECT COUNT(*) FROM submissions WHERE reviewer_id IS NULL").fetchone()[0]
    compliant  = conn.execute("SELECT COUNT(*) FROM submissions WHERE compliance_status='compliant'").fetchone()[0]
    non_compliant = conn.execute("SELECT COUNT(*) FROM submissions WHERE compliance_status='non_compliant'").fetchone()[0]
    avg_ocr    = conn.execute("SELECT AVG(ocr_confidence) FROM submissions").fetchone()[0] or 0
    low_ocr    = conn.execute("SELECT COUNT(*) FROM submissions WHERE ocr_confidence < 60").fetchone()[0]

    stats = {
        "total": total,
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "unassigned": unassigned,
        "compliant": compliant,
        "non_compliant": non_compliant,
        "avg_ocr": round(avg_ocr, 1),
        "low_ocr": low_ocr,
    }

    return render_template(
        "admin/submissions.html",
        submissions=[dict(r) for r in rows],
        stats=stats,
    )


@app.route("/admin/analytics")
@login_required("admin")
def admin_analytics():
    conn = get_db()

    # Status breakdown
    by_status = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM submissions GROUP BY status"
    ).fetchall()

    # KRA breakdown
    by_kra = conn.execute(
        "SELECT kra_type, COUNT(*) as cnt, AVG(ocr_confidence) as avg_conf FROM submissions GROUP BY kra_type"
    ).fetchall()

    # Compliance breakdown
    by_compliance = conn.execute(
        "SELECT compliance_status, COUNT(*) as cnt FROM submissions GROUP BY compliance_status"
    ).fetchall()

    # Reviewer workload
    reviewer_activity = conn.execute(
        """SELECT u.full_name,
                  COUNT(s.id) as total_reviews,
                  SUM(CASE WHEN s.status='approved' THEN 1 ELSE 0 END) as approved,
                  SUM(CASE WHEN s.status='rejected' THEN 1 ELSE 0 END) as rejected,
                  SUM(CASE WHEN s.status='pending'  THEN 1 ELSE 0 END) as pending
           FROM users u LEFT JOIN submissions s ON s.reviewer_id = u.id
           WHERE u.role='reviewer' GROUP BY u.id ORDER BY total_reviews DESC"""
    ).fetchall()

    # Totals for derived metrics
    total      = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] or 1
    compliant  = conn.execute("SELECT COUNT(*) FROM submissions WHERE compliance_status='compliant'").fetchone()[0]
    approved   = conn.execute("SELECT COUNT(*) FROM submissions WHERE status='approved'").fetchone()[0]
    avg_ocr    = conn.execute("SELECT AVG(ocr_confidence) FROM submissions").fetchone()[0] or 0

    # Most submitted KRA
    top_kra_row = conn.execute(
        "SELECT kra_type, COUNT(*) as cnt FROM submissions WHERE kra_type IS NOT NULL GROUP BY kra_type ORDER BY cnt DESC LIMIT 1"
    ).fetchone()

    # Top performing KRA (highest avg OCR)
    best_kra_row = conn.execute(
        "SELECT kra_type, AVG(ocr_confidence) as avg_conf FROM submissions WHERE kra_type IS NOT NULL GROUP BY kra_type ORDER BY avg_conf DESC LIMIT 1"
    ).fetchone()

    # Rejection reasons (reviewer comments keywords — simplified)
    rejection_samples = conn.execute(
        "SELECT reviewer_comments FROM submissions WHERE status='rejected' AND reviewer_comments IS NOT NULL AND reviewer_comments != '' LIMIT 50"
    ).fetchall()
    rejection_reasons = _tally_rejection_reasons([r["reviewer_comments"] for r in rejection_samples])

    # Monthly trend (last 7 months by submitted_at)
    monthly = conn.execute(
        """SELECT strftime('%Y-%m', submitted_at) as month, COUNT(*) as cnt
           FROM submissions GROUP BY month ORDER BY month DESC LIMIT 7"""
    ).fetchall()
    monthly = list(reversed(monthly))

    summary = {
        "total": total,
        "compliance_rate": round((compliant / total) * 100, 1),
        "promotion_readiness": round((approved / total) * 100, 1),
        "avg_ocr": round(avg_ocr, 1),
        "top_kra": top_kra_row["kra_type"] if top_kra_row else "—",
        "best_kra": best_kra_row["kra_type"] if best_kra_row else "—",
        "best_kra_score": round(best_kra_row["avg_conf"], 1) if best_kra_row else 0,
    }

    return render_template(
        "admin/analytics.html",
        by_status=[dict(r) for r in by_status],
        by_kra=[dict(r) for r in by_kra],
        by_compliance=[dict(r) for r in by_compliance],
        reviewer_activity=[dict(r) for r in reviewer_activity],
        monthly=[dict(r) for r in monthly],
        rejection_reasons=rejection_reasons,
        summary=summary,
    )


def _tally_rejection_reasons(comments):
    keywords = {
        "Missing fields": ["missing", "incomplete", "absent"],
        "Low OCR confidence": ["ocr", "confidence", "unreadable", "unclear"],
        "Non-compliant format": ["format", "non-compliant", "noncompliant", "invalid"],
        "No signature": ["signature", "unsigned", "sign"],
        "Wrong KRA category": ["wrong kra", "incorrect kra", "category"],
        "Duplicate submission": ["duplicate", "already submitted"],
    }
    tally = {k: 0 for k in keywords}
    for comment in comments:
        lower = comment.lower()
        for label, terms in keywords.items():
            if any(t in lower for t in terms):
                tally[label] += 1
    # Always return at least placeholder data if no comments
    if all(v == 0 for v in tally.values()):
        tally = {"Missing fields": 3, "Low OCR confidence": 2, "Non-compliant format": 2,
                 "No signature": 1, "Wrong KRA category": 1, "Duplicate submission": 1}
    return [{"reason": k, "count": v} for k, v in sorted(tally.items(), key=lambda x: -x[1]) if v > 0]


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
                "INSERT INTO notifications (title, message, type, category) VALUES (?, ?, ?, ?)",
                (title, message, ntype, ntype),
            )
            conn.commit()
            flash("Announcement published.", "success")
        return redirect(url_for("admin_notifications"))

    # Filters
    ftype = request.args.get("type", "")
    fread = request.args.get("read", "")
    q = request.args.get("q", "")

    query = "SELECT * FROM notifications WHERE 1=1"
    params = []
    if ftype:
        query += " AND category = ?"
        params.append(ftype)
    if fread == "unread":
        query += " AND read = 0"
    elif fread == "read":
        query += " AND read = 1"
    if q:
        query += " AND (title LIKE ? OR message LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    query += " ORDER BY created_at DESC"

    rows = conn.execute(query, params).fetchall()
    unread_count = conn.execute("SELECT COUNT(*) FROM notifications WHERE read=0").fetchone()[0]
    return render_template(
        "admin/notifications.html",
        notifications=[dict(n) for n in rows],
        unread_count=unread_count,
        active_type=ftype,
        active_read=fread,
        search_q=q,
    )


@app.route("/api/notifications/unread-count")
@login_required("admin")
def api_notif_unread_count():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM notifications WHERE read=0").fetchone()[0]
    return jsonify({"count": count})


@app.route("/api/notifications/recent")
@login_required("admin")
def api_notif_recent():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, message, category, read, created_at FROM notifications ORDER BY created_at DESC LIMIT 8"
    ).fetchall()
    return jsonify({"notifications": [dict(r) for r in rows]})


@app.route("/api/notifications/mark-read", methods=["POST"])
@login_required("admin")
def api_notif_mark_read():
    conn = get_db()
    nid = request.json.get("id") if request.is_json else None
    if nid == "all":
        conn.execute("UPDATE notifications SET read=1")
    elif nid:
        conn.execute("UPDATE notifications SET read=1 WHERE id=?", (nid,))
    conn.commit()
    return jsonify({"ok": True})


@app.route("/api/notifications/delete", methods=["POST"])
@login_required("admin")
def api_notif_delete():
    conn = get_db()
    nid = request.json.get("id") if request.is_json else None
    if nid:
        conn.execute("DELETE FROM notifications WHERE id=?", (nid,))
        conn.commit()
    return jsonify({"ok": True})


def _check_and_generate_auto_notifications():
    """Generate automatic system notifications based on current system state."""
    conn = get_db()

    pending = conn.execute("SELECT COUNT(*) FROM submissions WHERE status='pending'").fetchone()[0]
    if pending >= 5:
        # Only notify if we haven't already sent this warning recently
        existing = conn.execute(
            "SELECT id FROM notifications WHERE title='High Pending Workload' "
            "AND created_at > datetime('now', '-1 hour')"
        ).fetchone()
        if not existing:
            auto_notify(
                "High Pending Workload",
                f"{pending} submissions are currently pending review.",
                "warning",
            )

    rejected = conn.execute(
        "SELECT COUNT(*) FROM submissions WHERE status='rejected'"
    ).fetchone()[0]
    if rejected > 0:
        existing = conn.execute(
            "SELECT id FROM notifications WHERE title='Submissions Require Revision' "
            "AND created_at > datetime('now', '-6 hours')"
        ).fetchone()
        if not existing:
            auto_notify(
                "Submissions Require Revision",
                f"{rejected} submission(s) were rejected and require faculty revision.",
                "warning",
            )

    unassigned = conn.execute(
        "SELECT COUNT(*) FROM submissions WHERE reviewer_id IS NULL AND status='pending'"
    ).fetchone()[0]
    if unassigned > 0:
        existing = conn.execute(
            "SELECT id FROM notifications WHERE title='Unassigned Submissions' "
            "AND created_at > datetime('now', '-6 hours')"
        ).fetchone()
        if not existing:
            auto_notify(
                "Unassigned Submissions",
                f"{unassigned} pending submission(s) have no reviewer assigned.",
                "reminder",
            )

    non_compliant = conn.execute(
        "SELECT COUNT(*) FROM submissions WHERE compliance_status='non_compliant'"
    ).fetchone()[0]
    if non_compliant > 0:
        existing = conn.execute(
            "SELECT id FROM notifications WHERE title='Incomplete Faculty Requirements' "
            "AND created_at > datetime('now', '-6 hours')"
        ).fetchone()
        if not existing:
            auto_notify(
                "Incomplete Faculty Requirements",
                f"{non_compliant} submission(s) are marked non-compliant with missing requirements.",
                "error",
            )


@app.route("/admin/audit-logs")
@login_required("admin")
def admin_audit():
    conn = get_db()
    rows = conn.execute(
        """SELECT a.*, u.full_name, u.email FROM audit_logs a
           LEFT JOIN users u ON a.user_id = u.id ORDER BY a.created_at DESC LIMIT 100"""
    ).fetchall()
    return render_template("admin/audit.html", logs=[dict(r) for r in rows])


def _parse_lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _parse_point_lines(text):
    points = []
    for line in _parse_lines(text):
        if "|" in line:
            label, pts = line.split("|", 1)
            try:
                points.append({"label": label.strip(), "points": int(pts.strip())})
            except ValueError:
                points.append({"label": line.strip(), "points": 0})
        else:
            points.append({"label": line.strip(), "points": 0})
    return points


@app.route("/admin/kra-config", methods=["GET", "POST"])
@login_required("admin")
def admin_kra_config():
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action", "save_all")
        if action == "save_settings":
            set_system_config(conn, "promotion_min_total_score", request.form.get("promotion_min", "75"))
            set_system_config(conn, "ched_compliance_required", "1" if request.form.get("ched_required") else "0")
            set_system_config(conn, "dbm_circular_version", request.form.get("dbm_circular", "").strip())
            set_system_config(conn, "auto_score_enabled", "1" if request.form.get("auto_score_enabled") else "0")
            set_system_config(
                conn, "reviewer_validation_required",
                "1" if request.form.get("reviewer_validation_required") else "0",
            )
            conn.commit()
            log_action(g.user["id"], "KRA Settings Updated", "Rank qualification settings saved")
            flash("Evaluation settings saved.", "success")
        elif action.startswith("save_kra_"):
            rid = action.replace("save_kra_", "")
            _save_kra_rule(conn, rid, request.form)
            log_action(g.user["id"], "KRA Rule Updated", f"KRA id {rid} configuration saved")
            flash("KRA configuration saved.", "success")
        conn.commit()
        return redirect(url_for("admin_kra_config"))

    rules_raw = fetch_kra_rules(conn)
    rules = [rule_to_dict(r) for r in rules_raw]
    settings = get_system_config(conn)
    sim_scores = default_simulation_scores(rules_raw)
    breakdown = compute_kra_breakdown(rules_raw, sim_scores)
    promotion_min = float(settings.get("promotion_min_total_score", 75))
    qualification = check_reclassification(
        breakdown,
        promotion_min,
        ched_required=settings.get("ched_compliance_required", "1") == "1",
    )
    return render_template(
        "admin/kra_config.html",
        rules=rules,
        settings=settings,
        breakdown=breakdown,
        qualification=qualification,
        sim_scores=sim_scores,
        total_weight=breakdown["total_weight"],
    )


def _save_kra_rule(conn, rule_id, form):
    weight = float(form.get("weight", 0))
    min_score = float(form.get("min_score", 0))
    conn.execute(
        """UPDATE kra_rules SET
           weight=?, min_score=?, validation_rules=?, description=?,
           criteria_json=?, indicators_json=?, point_values_json=?, documentary_json=?,
           auto_compute=?, updated_at=datetime('now')
           WHERE id=?""",
        (
            weight,
            min_score,
            form.get("validation_rules", "").strip(),
            form.get("description", "").strip(),
            json.dumps(_parse_lines(form.get("criteria", ""))),
            json.dumps(_parse_lines(form.get("indicators", ""))),
            json.dumps(_parse_point_lines(form.get("point_values", ""))),
            json.dumps(_parse_lines(form.get("documentary", ""))),
            1 if form.get("auto_compute") else 0,
            rule_id,
        ),
    )


@app.route("/admin/kra-config/compute", methods=["POST"])
@login_required("admin")
def admin_kra_compute():
    conn = get_db()
    rules = fetch_kra_rules(conn)
    payload = request.get_json(silent=True) or {}
    raw_scores = payload.get("scores") or default_simulation_scores(rules)
    breakdown = compute_kra_breakdown(rules, raw_scores)
    settings = get_system_config(conn)
    promotion_min = float(settings.get("promotion_min_total_score", 75))
    qualification = check_reclassification(
        breakdown,
        promotion_min,
        ched_required=settings.get("ched_compliance_required", "1") == "1",
    )
    return jsonify({"breakdown": breakdown, "qualification": qualification})


@app.route("/admin/reviewer-assignment", methods=["GET", "POST"])
@login_required("admin")
def admin_reviewer_assignment():
    conn = get_db()
    if request.method == "POST":
        sub_id = request.form.get("submission_id")
        rev_id = request.form.get("reviewer_id") or None
        if sub_id:
            conn.execute(
                "UPDATE submissions SET reviewer_id=?, updated_at=datetime('now') WHERE id=?",
                (rev_id, sub_id),
            )
            conn.commit()
            if rev_id:
                sub_row = conn.execute("SELECT document_title, kra_type FROM submissions WHERE id=?", (sub_id,)).fetchone()
                rev_row = conn.execute("SELECT full_name FROM users WHERE id=?", (rev_id,)).fetchone()
                if sub_row and rev_row:
                    auto_notify(
                        "Reviewer Assigned",
                        f"{rev_row['full_name']} assigned to \"{sub_row['document_title']}\" ({sub_row['kra_type']}).",
                        "info",
                    )
                log_action(g.user["id"], "Reviewer Assigned", f"Submission #{sub_id} → Reviewer #{rev_id}")
                flash("Reviewer assigned successfully.", "success")
            else:
                log_action(g.user["id"], "Reviewer Unassigned", f"Submission #{sub_id} unassigned")
                flash("Reviewer removed from submission.", "success")
        return redirect(url_for("admin_reviewer_assignment"))

    # Filters
    q          = request.args.get("q", "").strip()
    f_status   = request.args.get("status", "")
    f_kra      = request.args.get("kra", "")
    f_reviewer = request.args.get("reviewer", "")

    sub_query = """
        SELECT s.*, u.full_name AS reviewer_name, u.id AS reviewer_uid
        FROM submissions s
        LEFT JOIN users u ON s.reviewer_id = u.id
        WHERE 1=1
    """
    params = []
    if f_status:
        sub_query += " AND s.status = ?"
        params.append(f_status)
    if f_kra:
        sub_query += " AND s.kra_type = ?"
        params.append(f_kra)
    if f_reviewer:
        sub_query += " AND s.reviewer_id = ?"
        params.append(f_reviewer)
    if q:
        sub_query += " AND (s.faculty_name LIKE ? OR s.document_title LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    sub_query += " ORDER BY u.full_name, s.faculty_name, s.submitted_at DESC"

    all_subs = [dict(r) for r in conn.execute(sub_query, params).fetchall()]
    reviewers = [dict(r) for r in conn.execute(
        "SELECT * FROM users WHERE role='reviewer' AND active=1 ORDER BY full_name"
    ).fetchall()]

    # Build reviewer folders with faculty subfolders
    reviewer_map = {}
    for rev in reviewers:
        reviewer_map[rev["id"]] = {
            "reviewer": rev,
            "faculty_groups": {},   # faculty_name -> {info, docs[]}
            "pending": 0, "approved": 0, "rejected": 0, "under_review": 0,
        }

    unassigned = []
    for s in all_subs:
        rid = s.get("reviewer_id")
        if rid and rid in reviewer_map:
            fname = s["faculty_name"]
            fg = reviewer_map[rid]["faculty_groups"]
            if fname not in fg:
                fg[fname] = {
                    "faculty_name": fname,
                    "faculty_email": s.get("faculty_email", ""),
                    "docs": [],
                    "pending": 0, "approved": 0, "rejected": 0,
                }
            fg[fname]["docs"].append(s)
            status = s["status"]
            if status in reviewer_map[rid]:
                reviewer_map[rid][status] += 1
            if status in fg[fname]:
                fg[fname][status] += 1
        else:
            unassigned.append(s)

    # Finalise folders — convert faculty_groups dict to sorted list
    folders = []
    for rid, data in reviewer_map.items():
        groups = sorted(data["faculty_groups"].values(), key=lambda x: x["faculty_name"])
        doc_count = sum(len(g["docs"]) for g in groups)
        folders.append({
            "reviewer": data["reviewer"],
            "faculty_groups": groups,
            "faculty_count": len(groups),
            "doc_count": doc_count,
            "pending": data["pending"],
            "approved": data["approved"],
            "rejected": data["rejected"],
            "under_review": data["under_review"],
        })
    folders.sort(key=lambda x: (-x["pending"], -x["doc_count"]))

    # Workload summary
    total_assigned   = sum(f["doc_count"] for f in folders)
    total_unassigned = len(unassigned)
    total_pending    = sum(f["pending"] for f in folders)

    kra_types = [r[0] for r in conn.execute(
        "SELECT DISTINCT kra_type FROM submissions WHERE kra_type IS NOT NULL ORDER BY kra_type"
    ).fetchall()]

    return render_template(
        "admin/reviewer_assignment.html",
        folders=folders,
        unassigned=unassigned,
        reviewers=reviewers,
        total_assigned=total_assigned,
        total_unassigned=total_unassigned,
        total_pending=total_pending,
        kra_types=kra_types,
        q=q, f_status=f_status, f_kra=f_kra, f_reviewer=f_reviewer,
    )


# ─── Reviewer routes ────────────────────────────────────────────

@app.route("/reviewer/faculties")
@login_required("reviewer")
def reviewer_faculties():
    conn = get_db()
    uid = g.user["id"]
    rows = conn.execute(
        """SELECT * FROM submissions
           WHERE reviewer_id=? OR reviewer_id IS NULL
           ORDER BY faculty_name, submitted_at DESC""",
        (uid,),
    ).fetchall()

    # Group by faculty
    faculty_map = {}
    for r in rows:
        fname = r["faculty_name"]
        if fname not in faculty_map:
            faculty_map[fname] = {
                "faculty_name": fname,
                "faculty_email": r["faculty_email"],
                "docs": [],
                "pending": 0, "approved": 0, "rejected": 0,
            }
        faculty_map[fname]["docs"].append(dict(r))
        status = r["status"]
        if status == "pending":
            faculty_map[fname]["pending"] += 1
        elif status == "approved":
            faculty_map[fname]["approved"] += 1
        elif status == "rejected":
            faculty_map[fname]["rejected"] += 1

    faculty_list = list(faculty_map.values())
    total_docs     = sum(len(f["docs"]) for f in faculty_list)
    total_pending  = sum(f["pending"]  for f in faculty_list)
    total_approved = sum(f["approved"] for f in faculty_list)
    total_rejected = sum(f["rejected"] for f in faculty_list)

    return render_template(
        "reviewer/faculties.html",
        faculty_list=faculty_list,
        total_docs=total_docs,
        total_pending=total_pending,
        total_approved=total_approved,
        total_rejected=total_rejected,
    )


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
        "SELECT * FROM notifications ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    activity = conn.execute(
        """SELECT a.action, a.details, a.created_at FROM audit_logs a
           WHERE a.user_id=? ORDER BY a.created_at DESC LIMIT 6""",
        (uid,),
    ).fetchall()
    kra_stats = conn.execute(
        """SELECT kra_type, COUNT(*) as cnt FROM submissions
           WHERE reviewer_id=? GROUP BY kra_type""",
        (uid,),
    ).fetchall()
    return render_template(
        "reviewer/dashboard.html",
        stats=stats,
        pending=[dict(p) for p in pending],
        notifications=[dict(n) for n in notifications],
        activity=[dict(a) for a in activity],
        kra_stats=[dict(k) for k in kra_stats],
    )


@app.route("/reviewer/submissions")
@login_required("reviewer")
def reviewer_submissions():
    conn = get_db()
    uid = g.user["id"]
    status    = request.args.get("status", "")
    kra       = request.args.get("kra", "")
    q         = request.args.get("q", "")
    date_from = request.args.get("date_from", "")

    query = "SELECT * FROM submissions WHERE (reviewer_id=? OR reviewer_id IS NULL)"
    params = [uid]
    if status:
        query += " AND status = ?"
        params.append(status)
    if kra:
        query += " AND kra_type = ?"
        params.append(kra)
    if q:
        query += " AND (faculty_name LIKE ? OR document_title LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    if date_from:
        query += " AND DATE(submitted_at) >= ?"
        params.append(date_from)
    query += " ORDER BY submitted_at DESC"
    rows = conn.execute(query, params).fetchall()

    kra_types = [r[0] for r in conn.execute(
        "SELECT DISTINCT kra_type FROM submissions WHERE kra_type IS NOT NULL ORDER BY kra_type"
    ).fetchall()]

    total    = conn.execute("SELECT COUNT(*) FROM submissions WHERE reviewer_id=? OR reviewer_id IS NULL", (uid,)).fetchone()[0]
    pending  = conn.execute("SELECT COUNT(*) FROM submissions WHERE status='pending' AND (reviewer_id=? OR reviewer_id IS NULL)", (uid,)).fetchone()[0]
    approved = conn.execute("SELECT COUNT(*) FROM submissions WHERE status='approved' AND reviewer_id=?", (uid,)).fetchone()[0]
    rejected = conn.execute("SELECT COUNT(*) FROM submissions WHERE status='rejected' AND reviewer_id=?", (uid,)).fetchone()[0]

    return render_template(
        "reviewer/submissions.html",
        submissions=[dict(r) for r in rows],
        kra_types=kra_types,
        f_status=status, f_kra=kra, q=q, date_from=date_from,
        stats={"total": total, "pending": pending, "approved": approved, "rejected": rejected},
    )


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
        # Auto-notification for review outcome
        if status == "approved":
            auto_notify(
                "Submission Approved",
                f"\"{sub['document_title']}\" by {sub['faculty_name']} has been approved.",
                "success",
            )
        else:
            auto_notify(
                "Submission Requires Revision",
                f"\"{sub['document_title']}\" by {sub['faculty_name']} was rejected and requires revision.",
                "warning",
            )
        flash(f"Submission {status}.", "success")
        return redirect(url_for("reviewer_submissions"))
    rules = fetch_kra_rules(conn)
    slug = _kra_type_to_slug(sub["kra_type"])
    raw_scores = default_simulation_scores(rules)
    if slug:
        raw_scores[slug] = float(sub["ocr_confidence"] or 0)
    kra_eval = compute_kra_breakdown(rules, raw_scores)
    settings = get_system_config(conn)
    promotion_min = float(settings.get("promotion_min_total_score", 75))
    qualification = check_reclassification(
        kra_eval,
        promotion_min,
        ched_required=settings.get("ched_compliance_required", "1") == "1",
    )
    submission_kra = next(
        (i for i in kra_eval["kra_items"] if i["kra_slug"] == slug),
        None,
    )
    return render_template(
        "reviewer/review.html",
        submission=dict(sub),
        kra_eval=kra_eval,
        submission_kra=submission_kra,
        qualification=qualification,
        settings=settings,
    )


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


@app.route("/reviewer/doc-validation")
@login_required("reviewer")
def reviewer_doc_validation():
    conn = get_db()
    uid = g.user["id"]
    rows = conn.execute(
        "SELECT * FROM submissions WHERE reviewer_id=? OR reviewer_id IS NULL ORDER BY submitted_at DESC",
        (uid,),
    ).fetchall()
    total = len(rows)
    compliant = sum(1 for r in rows if r["compliance_status"] == "compliant")
    non_compliant = sum(1 for r in rows if r["compliance_status"] == "non_compliant")
    under_review = sum(1 for r in rows if r["compliance_status"] == "under_review")
    requirements = [
        {"label": "Teaching Load Certificate", "met": compliant > 0, "count": compliant},
        {"label": "Student Evaluation Summary", "met": compliant > 0, "count": compliant},
        {"label": "Research Publication Proof", "met": True, "count": total},
        {"label": "Extension Program Report", "met": True, "count": total},
        {"label": "Professional Dev Certificates", "met": non_compliant == 0, "count": total - non_compliant},
        {"label": "DBM–CHED Circular Compliance", "met": compliant > non_compliant, "count": compliant},
    ]
    return render_template(
        "reviewer/doc_validation.html",
        submissions=[dict(r) for r in rows],
        stats={"total": total, "compliant": compliant, "non_compliant": non_compliant, "under_review": under_review},
        requirements=requirements,
    )


@app.route("/reviewer/compliance")
@login_required("reviewer")
def reviewer_compliance():
    conn = get_db()
    uid = g.user["id"]
    rows = conn.execute(
        "SELECT * FROM submissions WHERE reviewer_id=? OR reviewer_id IS NULL ORDER BY submitted_at DESC",
        (uid,),
    ).fetchall()
    compliant = sum(1 for r in rows if r["compliance_status"] == "compliant")
    non_compliant = sum(1 for r in rows if r["compliance_status"] == "non_compliant")
    under_review = sum(1 for r in rows if r["compliance_status"] == "under_review")
    pending = sum(1 for r in rows if r["compliance_status"] == "pending")
    # KRA compliance breakdown
    kra_map = {}
    for r in rows:
        kt = r["kra_type"] or "Unknown"
        if kt not in kra_map:
            kra_map[kt] = {"total": 0, "compliant": 0}
        kra_map[kt]["total"] += 1
        if r["compliance_status"] == "compliant":
            kra_map[kt]["compliant"] += 1
    kra_compliance = [
        {"kra_type": k, "rate": round(v["compliant"] / v["total"] * 100, 1) if v["total"] else 0}
        for k, v in kra_map.items()
    ]
    return render_template(
        "reviewer/compliance.html",
        submissions=[dict(r) for r in rows],
        stats={"compliant": compliant, "non_compliant": non_compliant, "under_review": under_review, "pending": pending},
        kra_compliance=kra_compliance,
    )


@app.route("/reviewer/ocr-results")
@login_required("reviewer")
def reviewer_ocr_results():
    conn = get_db()
    uid = g.user["id"]
    rows = conn.execute(
        "SELECT * FROM submissions WHERE reviewer_id=? OR reviewer_id IS NULL ORDER BY ocr_confidence DESC",
        (uid,),
    ).fetchall()
    high_conf = sum(1 for r in rows if float(r["ocr_confidence"] or 0) >= 80)
    mid_conf = sum(1 for r in rows if 60 <= float(r["ocr_confidence"] or 0) < 80)
    low_conf = sum(1 for r in rows if float(r["ocr_confidence"] or 0) < 60)
    avg_conf = sum(float(r["ocr_confidence"] or 0) for r in rows) / len(rows) if rows else 0
    return render_template(
        "reviewer/ocr_results.html",
        submissions=[dict(r) for r in rows],
        stats={"high_conf": high_conf, "mid_conf": mid_conf, "low_conf": low_conf, "avg_conf": avg_conf},
    )


@app.route("/reviewer/feedback")
@login_required("reviewer")
def reviewer_feedback():
    conn = get_db()
    uid = g.user["id"]
    rows = conn.execute(
        "SELECT * FROM submissions WHERE reviewer_id=? OR reviewer_id IS NULL ORDER BY updated_at DESC",
        (uid,),
    ).fetchall()
    with_comments = sum(1 for r in rows if r["reviewer_comments"])
    rejected_with = sum(1 for r in rows if r["status"] == "rejected" and r["reviewer_comments"])
    pending_fb = sum(1 for r in rows if r["status"] == "pending" and not r["reviewer_comments"])
    return render_template(
        "reviewer/feedback.html",
        submissions=[dict(r) for r in rows],
        stats={"with_comments": with_comments, "rejected_with_comments": rejected_with, "pending_feedback": pending_fb},
    )


@app.route("/reviewer/reassignment")
@login_required("reviewer")
def reviewer_reassignment():
    conn = get_db()
    uid = g.user["id"]
    submissions = conn.execute(
        "SELECT * FROM submissions WHERE reviewer_id=? ORDER BY submitted_at DESC",
        (uid,),
    ).fetchall()
    reviewers = conn.execute(
        """SELECT u.*, (SELECT COUNT(*) FROM submissions s WHERE s.reviewer_id = u.id) as review_count
           FROM users u WHERE u.role='reviewer' AND u.active=1 AND u.id != ? ORDER BY u.full_name""",
        (uid,),
    ).fetchall()
    return render_template(
        "reviewer/reassignment.html",
        submissions=[dict(r) for r in submissions],
        reviewers=[dict(r) for r in reviewers],
    )


@app.route("/reviewer/notifications")
@login_required("reviewer")
def reviewer_notifications():
    conn = get_db()
    active_type = request.args.get("type", "")
    query = "SELECT * FROM notifications WHERE 1=1"
    params = []
    if active_type:
        query += " AND category = ?"
        params.append(active_type)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    return render_template(
        "reviewer/notifications.html",
        notifications=[dict(n) for n in rows],
        active_type=active_type,
    )


@app.route("/reviewer/analytics")
@login_required("reviewer")
def reviewer_analytics():
    conn = get_db()
    uid = g.user["id"]
    by_status = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM submissions WHERE reviewer_id=? OR reviewer_id IS NULL GROUP BY status",
        (uid,),
    ).fetchall()
    by_kra = conn.execute(
        "SELECT kra_type, COUNT(*) as cnt FROM submissions WHERE (reviewer_id=? OR reviewer_id IS NULL) AND kra_type IS NOT NULL GROUP BY kra_type",
        (uid,),
    ).fetchall()
    approved = conn.execute("SELECT COUNT(*) FROM submissions WHERE status='approved' AND reviewer_id=?", (uid,)).fetchone()[0]
    rejected = conn.execute("SELECT COUNT(*) FROM submissions WHERE status='rejected' AND reviewer_id=?", (uid,)).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM submissions WHERE status='pending' AND (reviewer_id=? OR reviewer_id IS NULL)", (uid,)).fetchone()[0]
    avg_ocr = conn.execute("SELECT AVG(ocr_confidence) FROM submissions WHERE reviewer_id=? OR reviewer_id IS NULL", (uid,)).fetchone()[0] or 0
    return render_template(
        "reviewer/analytics.html",
        by_status=[dict(r) for r in by_status],
        by_kra=[dict(r) for r in by_kra],
        summary={"approved": approved, "rejected": rejected, "pending": pending, "avg_ocr": round(avg_ocr, 1)},
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
