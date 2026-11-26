"""
CityOS - Single-file citizen complaint management application
-------------------------------------------------------------
Run:
    pip install flask
    python cityos.py

Then open http://127.0.0.1:5000

Optional environment variables:
    CITYOS_SECRET_KEY       Session secret (set a long random value in production)
    CITYOS_ADMIN_USERNAME  Admin username (default: admin)
    CITYOS_ADMIN_PASSWORD  Admin password (default: ChangeMe123!)
    CITYOS_DB              SQLite database path (default: cityos.db)

This is a compact, self-contained starter application. Before public deployment,
configure a strong secret/admin password, HTTPS, backups, rate limiting, and
production hosting. Do not expose Flask's development server to the internet.
"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from flask import (
    Flask, abort, flash, redirect, render_template_string, request,
    send_from_directory, session, url_for
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "cityos_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "CITYOS_SECRET_KEY", "change-this-development-secret-before-deployment"
)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB per upload
DB_PATH = os.environ.get("CITYOS_DB", str(BASE_DIR / "cityos.db"))
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
CATEGORIES = [
    "Garbage/Waste", "Road Damage", "Water Supply", "Streetlight",
    "Drainage", "Electricity", "Public Safety", "Other"
]
STATUSES = ["Reported", "In Progress", "Resolved", "Rejected"]

STYLE = """
<style>
:root{--green:#176b4d;--dark:#123d30;--bg:#f3f6f4;--line:#dce6df}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#1d2b24;
font:15px Arial,sans-serif}header{background:var(--dark);color:white;padding:18px 5%;
display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
header a{color:white;text-decoration:none;margin-left:15px}.brand{font-size:23px;font-weight:bold}
main{max-width:1050px;margin:28px auto;padding:0 16px}.card{background:white;
border:1px solid var(--line);border-radius:12px;padding:22px;margin-bottom:18px;
box-shadow:0 2px 8px #123d3010}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:15px}
h1,h2,h3{color:var(--dark)}label{display:block;font-weight:bold;margin:13px 0 6px}
input,select,textarea{width:100%;padding:11px;border:1px solid #cbd8cf;border-radius:7px;
font:inherit;background:white}textarea{min-height:100px;resize:vertical}
button,.btn{display:inline-block;background:var(--green);color:white;border:0;border-radius:7px;
padding:11px 16px;text-decoration:none;cursor:pointer;font-weight:bold}
button:hover,.btn:hover{background:var(--dark)}.muted{color:#65746b}.small{font-size:13px}
.flash{padding:12px;border-radius:7px;background:#e5f4eb;margin-bottom:12px}
table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:11px;
border-bottom:1px solid var(--line);vertical-align:top}th{background:#edf4ef}
.badge{display:inline-block;padding:5px 9px;border-radius:20px;background:#e7f1eb;font-size:12px}
img.preview{max-width:220px;max-height:160px;object-fit:cover;border-radius:8px}
.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.inline{display:inline}.inline select{width:auto;min-width:130px}
a{color:var(--green)}footer{text-align:center;padding:25px;color:#68766d}
@media(max-width:600px){header{align-items:flex-start}header nav a{margin:0 10px 0 0}
table{font-size:13px}th,td{padding:7px}}
</style>
"""

LAYOUT = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} - CityOS</title>""" + STYLE + """</head><body>
<header><a class="brand" href="{{ url_for('home') }}">CityOS</a><nav>
{% if session.get('user_id') %}
<a href="{{ url_for('dashboard') }}">Dashboard</a>
<a href="{{ url_for('new_report') }}">Report a Problem</a>
{% if session.get('role') == 'admin' %}<a href="{{ url_for('admin_dashboard') }}">Admin</a>{% endif %}
<a href="{{ url_for('logout') }}">Logout</a>
{% else %}<a href="{{ url_for('login') }}">Login</a><a href="{{ url_for('register') }}">Register</a>{% endif %}
</nav></header><main>
{% with messages = get_flashed_messages() %}{% for message in messages %}
<div class="flash">{{ message }}</div>{% endfor %}{% endwith %}
""" + "{{ content|safe }}" + """
</main><footer>CityOS · Citizen Services & Issue Reporting</footer></body></html>
"""

def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'citizen',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reports(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            location TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            photo TEXT,
            status TEXT NOT NULL DEFAULT 'Reported',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS report_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            note TEXT,
            changed_by INTEGER,
            changed_at TEXT NOT NULL,
            FOREIGN KEY(report_id) REFERENCES reports(id),
            FOREIGN KEY(changed_by) REFERENCES users(id)
        );
        """)
        admin_email = "admin@cityos.local"
        existing = con.execute("SELECT id FROM users WHERE email=?", (admin_email,)).fetchone()
        if not existing:
            con.execute(
                "INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                ("CityOS Administrator", admin_email,
                 generate_password_hash(os.environ.get("CITYOS_ADMIN_PASSWORD", "ChangeMe123!")),
                 "admin", now())
            )

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def page(title, content):
    return render_template_string(LAYOUT, title=title, content=content)

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to continue.")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            abort(403)
        return fn(*args, **kwargs)
    return wrapper

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app.errorhandler(413)
def too_large(_error):
    flash("Photo is too large. Maximum file size is 5 MB.")
    return redirect(request.referrer or url_for("new_report"))

@app.route("/")
def home():
    with db() as con:
        total = con.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        resolved = con.execute("SELECT COUNT(*) FROM reports WHERE status='Resolved'").fetchone()[0]
    content = f"""
    <section class="card"><h1>Make your city better with CityOS</h1>
    <p>Report local problems, follow their progress, and help improve essential city services.</p>
    <div class="actions"><a class="btn" href="{url_for('new_report') if session.get('user_id') else url_for('register')}">Report an Issue</a>
    <a class="btn" href="{url_for('dashboard')}">Track Reports</a></div></section>
    <div class="grid"><section class="card"><h2>{total}</h2><p class="muted">Reports submitted</p></section>
    <section class="card"><h2>{resolved}</h2><p class="muted">Issues resolved</p></section></div>
    <section class="card"><h3>How CityOS works</h3><p>1. Register or log in. 2. Submit an issue with its location and an optional photo.
    3. The city team reviews it and updates its status. 4. Track progress from your dashboard.</p></section>
    """
    return page("Home", content)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or not password:
            flash("Please complete all fields.")
        elif len(password) < 8:
            flash("Password must contain at least 8 characters.")
        else:
            try:
                with db() as con:
                    con.execute(
                        "INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                        (name, email, generate_password_hash(password), "citizen", now())
                    )
                flash("Account created. Please log in.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("An account with that email already exists.")
    content = """
    <section class="card"><h1>Create a citizen account</h1><form method="post">
    <label>Full name</label><input name="name" required maxlength="100">
    <label>Email</label><input type="email" name="email" required maxlength="150">
    <label>Password (minimum 8 characters)</label><input type="password" name="password" minlength="8" required>
    <p><button type="submit">Register</button></p></form></section>"""
    return page("Register", content)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["role"] = user["role"]
            return redirect(url_for("admin_dashboard" if user["role"] == "admin" else "dashboard"))
        flash("Invalid email or password.")
    content = """
    <section class="card"><h1>Login</h1><form method="post">
    <label>Email</label><input type="email" name="email" required>
    <label>Password</label><input type="password" name="password" required>
    <p><button type="submit">Login</button></p></form>
    <p class="small muted">Initial admin account: admin@cityos.local. Set CITYOS_ADMIN_PASSWORD before first run.</p></section>"""
    return page("Login", content)

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("home"))

@app.route("/dashboard")
@login_required
def dashboard():
    with db() as con:
        reports = con.execute(
            "SELECT * FROM reports WHERE user_id=? ORDER BY id DESC", (session["user_id"],)
        ).fetchall()
    rows = "".join(
        f"""<tr><td><a href="{url_for('report_detail', report_id=r['id'])}">#{r['id']} · {r['title']}</a></td>
        <td>{r['category']}</td><td><span class="badge">{r['status']}</span></td><td>{r['created_at']}</td></tr>"""
        for r in reports
    ) or '<tr><td colspan="4">You have not submitted any reports yet.</td></tr>'
    content = f"""<section class="card"><h1>Welcome, {session.get('name','Citizen')}</h1>
    <p>View your submitted issues and check their latest status.</p>
    <a class="btn" href="{url_for('new_report')}">+ Submit a report</a></section>
    <section class="card"><h2>Your reports</h2><table><tr><th>Report</th><th>Category</th><th>Status</th><th>Submitted</th></tr>{rows}</table></section>"""
    return page("Dashboard", content)

@app.route("/report/new", methods=["GET", "POST"])
@login_required
def new_report():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "")
        location = request.form.get("location", "").strip()
        lat = request.form.get("latitude", "").strip()
        lon = request.form.get("longitude", "").strip()
        photo_name = None
        photo = request.files.get("photo")
        if not title or not description or not location or category not in CATEGORIES:
            flash("Please fill in the title, description, category, and location.")
        elif (lat and not re.fullmatch(r"-?\\d+(\\.\\d+)?", lat)) or (lon and not re.fullmatch(r"-?\\d+(\\.\\d+)?", lon)):
            flash("Invalid GPS coordinates.")
        elif bool(lat) != bool(lon):
            flash("Both latitude and longitude are required together.")
        elif photo and photo.filename:
            if not allowed_file(photo.filename):
                flash("Photo must be PNG, JPG, JPEG, or WEBP.")
            else:
                photo_name = uuid.uuid4().hex + "_" + secure_filename(photo.filename)
                photo.save(UPLOAD_DIR / photo_name)
        if title and description and location and category in CATEGORIES and (not photo or not photo.filename or photo_name):
            timestamp = now()
            with db() as con:
                cur = con.execute("""INSERT INTO reports
                    (user_id,title,description,category,location,latitude,longitude,photo,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (session["user_id"], title, description, category, location,
                     float(lat) if lat else None, float(lon) if lon else None,
                     photo_name, "Reported", timestamp, timestamp))
                report_id = cur.lastrowid
                con.execute("""INSERT INTO report_history(report_id,status,note,changed_by,changed_at)
                    VALUES(?,?,?,?,?)""", (report_id, "Reported", "Initial report submitted", session["user_id"], timestamp))
            flash(f"Report #{report_id} submitted successfully.")
            return redirect(url_for("report_detail", report_id=report_id))
    options = "".join(f'<option>{c}</option>' for c in CATEGORIES)
    content = f"""
    <section class="card"><h1>Report a city problem</h1>
    <form method="post" enctype="multipart/form-data">
    <label>Issue title</label><input name="title" required maxlength="120" placeholder="Example: Broken streetlight near park">
    <label>Category</label><select name="category" required><option value="">Choose category</option>{options}</select>
    <label>Description</label><textarea name="description" required placeholder="Explain the problem and nearby landmarks"></textarea>
    <label>Location / address</label><input name="location" required maxlength="250" placeholder="Street, area, landmark">
    <div class="grid"><div><label>Latitude (optional)</label><input id="latitude" name="latitude" inputmode="decimal"></div>
    <div><label>Longitude (optional)</label><input id="longitude" name="longitude" inputmode="decimal"></div></div>
    <p><button type="button" class="btn" onclick="getLocation()">Use my current GPS location</button>
    <span id="gpsmsg" class="small muted">Your browser may ask for location permission.</span></p>
    <label>Photo (optional, max 5 MB)</label><input type="file" name="photo" accept=".png,.jpg,.jpeg,.webp">
    <p><button type="submit">Submit Report</button></p></form></section>
    <script>
    function getLocation(){const m=document.getElementById('gpsmsg');
      if(!navigator.geolocation){m.textContent='Geolocation is not supported by this browser.';return;}
      m.textContent='Getting location...';
      navigator.geolocation.getCurrentPosition(p=>{
        document.getElementById('latitude').value=p.coords.latitude;
        document.getElementById('longitude').value=p.coords.longitude;
        m.textContent='Location captured.';
      },()=>{m.textContent='Could not get location. Check browser permission and try again.';});
    }</script>"""
    return page("New Report", content)

@app.route("/report/<int:report_id>")
@login_required
def report_detail(report_id):
    with db() as con:
        report = con.execute("""SELECT r.*,u.name AS citizen FROM reports r
            JOIN users u ON u.id=r.user_id WHERE r.id=?""", (report_id,)).fetchone()
        if not report:
            abort(404)
        if report["user_id"] != session["user_id"] and session.get("role") != "admin":
            abort(403)
        history = con.execute("""SELECT h.*,u.name AS actor FROM report_history h
            LEFT JOIN users u ON u.id=h.changed_by WHERE h.report_id=? ORDER BY h.id DESC""",
            (report_id,)).fetchall()
    photo_html = f'<p><img class="preview" src="{url_for("uploaded_file", filename=report["photo"])}"></p>' if report["photo"] else ""
    gps_html = (f'<p><a target="_blank" rel="noopener" href="https://www.openstreetmap.org/?mlat={report["latitude"]}&mlon={report["longitude"]}#map=17/{report["latitude"]}/{report["longitude"]}">View GPS location on map</a></p>'
                if report["latitude"] is not None else "")
    history_rows = "".join(f"<tr><td>{h['changed_at']}</td><td>{h['status']}</td><td>{h['note'] or ''}</td><td>{h['actor'] or 'System'}</td></tr>" for h in history)
    admin_form = ""
    if session.get("role") == "admin":
        opts = "".join(f'<option {"selected" if s == report["status"] else ""}>{s}</option>' for s in STATUSES)
        admin_form = f"""<section class="card"><h2>Admin: update status</h2><form method="post" action="{url_for('update_report', report_id=report_id)}">
        <label>New status</label><select name="status">{opts}</select>
        <label>Update note</label><input name="note" maxlength="300" placeholder="Add an update for the citizen">
        <p><button type="submit">Save Update</button></p></form></section>"""
    content = f"""<section class="card"><h1>Report #{report['id']}: {report['title']}</h1>
    <p><span class="badge">{report['status']}</span> <span class="muted">Submitted {report['created_at']}</span></p>
    <p><b>Category:</b> {report['category']}</p><p><b>Location:</b> {report['location']}</p>
    <p><b>Description:</b><br>{report['description']}</p>{photo_html}{gps_html}</section>
    {admin_form}<section class="card"><h2>Status history</h2><table><tr><th>Date</th><th>Status</th><th>Note</th><th>Updated by</th></tr>{history_rows}</table></section>"""
    return page(f"Report {report_id}", content)

@app.route("/admin")
@admin_required
def admin_dashboard():
    with db() as con:
        reports = con.execute("""SELECT r.*,u.name AS citizen FROM reports r
            JOIN users u ON u.id=r.user_id ORDER BY r.id DESC""").fetchall()
        counts = {s: con.execute("SELECT COUNT(*) FROM reports WHERE status=?", (s,)).fetchone()[0] for s in STATUSES}
    summary = " · ".join(f"<b>{s}:</b> {counts[s]}" for s in STATUSES)
    rows = "".join(f"""<tr><td><a href="{url_for('report_detail',report_id=r['id'])}">#{r['id']} {r['title']}</a></td>
    <td>{r['citizen']}</td><td>{r['category']}</td><td>{r['location']}</td><td><span class="badge">{r['status']}</span></td><td>{r['created_at']}</td></tr>""" for r in reports)
    content = f"""<section class="card"><h1>CityOS Admin Dashboard</h1><p>{summary}</p></section>
    <section class="card"><h2>All citizen reports</h2><table><tr><th>Report</th><th>Citizen</th><th>Category</th><th>Location</th><th>Status</th><th>Date</th></tr>
    {rows or '<tr><td colspan="6">No reports yet.</td></tr>'}</table></section>"""
    return page("Admin", content)

@app.route("/admin/report/<int:report_id>/update", methods=["POST"])
@admin_required
def update_report(report_id):
    status = request.form.get("status", "")
    note = request.form.get("note", "").strip()
    if status not in STATUSES:
        abort(400)
    timestamp = now()
    with db() as con:
        report = con.execute("SELECT id FROM reports WHERE id=?", (report_id,)).fetchone()
        if not report:
            abort(404)
        con.execute("UPDATE reports SET status=?,updated_at=? WHERE id=?", (status, timestamp, report_id))
        con.execute("""INSERT INTO report_history(report_id,status,note,changed_by,changed_at)
            VALUES(?,?,?,?,?)""", (report_id, status, note, session["user_id"], timestamp))
    flash("Report status updated.")
    return redirect(url_for("report_detail", report_id=report_id))

@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    # Only serve files referenced by a report the user may view.
    with db() as con:
        row = con.execute("SELECT user_id FROM reports WHERE photo=?", (filename,)).fetchone()
    if not row or (row["user_id"] != session["user_id"] and session.get("role") != "admin"):
        abort(404)
    return send_from_directory(UPLOAD_DIR, filename)

@app.errorhandler(403)
def forbidden(_error):
    return page("Access denied", "<section class='card'><h1>Access denied</h1><p>You do not have permission to view this page.</p></section>"), 403

@app.errorhandler(404)
def not_found(_error):
    return page("Not found", "<section class='card'><h1>Page not found</h1><p>The requested page does not exist.</p></section>"), 404

init_db()

if __name__ == "__main__":
    print("\\nCityOS is starting...")
    print("Open: http://127.0.0.1:5000")
    print("Default admin: admin@cityos.local")
    print("Set CITYOS_ADMIN_PASSWORD before the first run for a custom admin password.\\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
