import calendar
import json
import math
import os
import re
import secrets
import sqlite3
import socket
from datetime import date
from pathlib import Path

import click
import bleach
from flask import Flask, abort, flash, g, get_flashed_messages, jsonify, make_response, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from auth import current_user_id, login_required
from student_mode import STUDENT_DATABASE, register_student_mode


BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("HABIT_DB", BASE_DIR / "habits.db"))
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
BACKUP_VERSION = 1
BACKUP_MAX_BYTES = 5 * 1024 * 1024
BACKUP_MAX_ROWS = 10000
BACKUP_DIR = BASE_DIR / "backups"
SECRET_KEY_PATH = BASE_DIR / "instance" / "secret_key"


def persistent_secret_key():
    SECRET_KEY_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(SECRET_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        key = SECRET_KEY_PATH.read_text(encoding="utf-8").strip()
        if not key:
            raise RuntimeError("Flask secret key file is empty")
        return key
    key = secrets.token_hex(32)
    with os.fdopen(descriptor, "w", encoding="utf-8") as key_file:
        key_file.write(key)
    return key

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or persistent_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
    MAX_CONTENT_LENGTH=BACKUP_MAX_BYTES,
)
app.config["JSON_SORT_KEYS"] = False
register_student_mode(app)


@app.get("/service-worker.js")
def service_worker():
    response = send_from_directory(BASE_DIR / "static", "sw.js", mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.context_processor
def inject_current_user():
    return {"current_user": getattr(g, "current_user", None)}


def get_db():
    if "db" not in g:
        DATABASE.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK(length(username) BETWEEN 3 AND 40),
            email TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 60),
            color TEXT NOT NULL DEFAULT '#75d6b1',
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS completions (
            goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            completed_on TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 1 CHECK(done IN (0, 1)),
            PRIMARY KEY (goal_id, completed_on)
        );
        CREATE INDEX IF NOT EXISTS idx_completions_date ON completions(completed_on);
        CREATE TABLE IF NOT EXISTS monthly_goal_progress (
            goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            month TEXT NOT NULL CHECK(length(month) = 7),
            percent INTEGER NOT NULL CHECK(percent BETWEEN 0 AND 100),
            note TEXT NOT NULL DEFAULT '' CHECK(length(note) <= 2000),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (goal_id, month)
        );
        CREATE TABLE IF NOT EXISTS daily_metrics (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            metric_date TEXT PRIMARY KEY,
            mood INTEGER CHECK(mood BETWEEN 1 AND 5),
            energy INTEGER CHECK(energy BETWEEN 1 AND 5),
            note TEXT NOT NULL DEFAULT '' CHECK(length(note) <= 2000)
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
            amount REAL NOT NULL CHECK(amount > 0),
            category TEXT NOT NULL CHECK(length(category) BETWEEN 1 AND 60),
            note TEXT NOT NULL DEFAULT '' CHECK(length(note) <= 500),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS recurring_incomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 100),
            amount REAL NOT NULL CHECK(amount > 0),
            day_of_month INTEGER NOT NULL CHECK(day_of_month BETWEEN 1 AND 31),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS expected_incomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 100),
            amount REAL NOT NULL CHECK(amount > 0),
            expected_date TEXT NOT NULL CHECK(length(expected_date) = 10),
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'received')),
            received_transaction_id INTEGER UNIQUE REFERENCES transactions(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS recurring_income_occurrences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            recurring_income_id INTEGER REFERENCES recurring_incomes(id) ON DELETE SET NULL,
            month_year TEXT NOT NULL CHECK(length(month_year) = 7),
            transaction_id INTEGER UNIQUE REFERENCES transactions(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(recurring_income_id, month_year)
        );
        CREATE TABLE IF NOT EXISTS category_budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            category_name TEXT NOT NULL CHECK(length(category_name) BETWEEN 1 AND 60),
            monthly_limit REAL NOT NULL CHECK(monthly_limit >= 0),
            month_year TEXT NOT NULL CHECK(length(month_year) = 7),
            UNIQUE(user_id, category_name, month_year)
        );
        CREATE TABLE IF NOT EXISTS category_budget_settlements (
            budget_id INTEGER PRIMARY KEY REFERENCES category_budgets(id) ON DELETE CASCADE,
            amount REAL NOT NULL DEFAULT 0 CHECK(amount >= 0),
            transaction_id INTEGER UNIQUE REFERENCES transactions(id) ON DELETE SET NULL,
            settled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS savings_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 100),
            target_amount REAL NOT NULL CHECK(target_amount > 0),
            current_amount REAL NOT NULL DEFAULT 0 CHECK(current_amount >= 0),
            deadline TEXT,
            goal_name TEXT,
            current_allocated REAL NOT NULL DEFAULT 0 CHECK(current_allocated >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS monthly_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            month_year TEXT NOT NULL CHECK(length(month_year) = 7),
            total_income REAL NOT NULL DEFAULT 0,
            total_expenses REAL NOT NULL DEFAULT 0,
            net_savings REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, month_year)
        );
        CREATE TABLE IF NOT EXISTS savings_allocation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            goal_id INTEGER REFERENCES savings_goals(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL CHECK(event_type IN ('allocate', 'return', 'spend')),
            amount REAL NOT NULL CHECK(amount > 0),
            note TEXT NOT NULL DEFAULT '' CHECK(length(note) <= 500),
            transaction_id INTEGER UNIQUE REFERENCES transactions(id) ON DELETE SET NULL,
            snapshot_included INTEGER NOT NULL DEFAULT 0 CHECK(snapshot_included IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS daily_budget_settings (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            daily_limit REAL NOT NULL CHECK(daily_limit >= 0)
        );
        CREATE TABLE IF NOT EXISTS daily_budget_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            daily_amount REAL NOT NULL CHECK(daily_amount >= 0),
            effective_date TEXT NOT NULL,
            UNIQUE(user_id, effective_date)
        );
        CREATE TABLE IF NOT EXISTS daily_budget_expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expense_date TEXT NOT NULL,
            amount REAL NOT NULL CHECK(amount > 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    goal_columns = {row["name"] for row in db.execute("PRAGMA table_info(goals)")}
    if "is_active" not in goal_columns:
        db.execute("ALTER TABLE goals ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1))")
    savings_goal_columns = {row["name"] for row in db.execute("PRAGMA table_info(savings_goals)")}
    if "goal_name" not in savings_goal_columns:
        db.execute("ALTER TABLE savings_goals ADD COLUMN goal_name TEXT")
    if "current_allocated" not in savings_goal_columns:
        db.execute("ALTER TABLE savings_goals ADD COLUMN current_allocated REAL NOT NULL DEFAULT 0")
    if "created_at" not in savings_goal_columns:
        db.execute("ALTER TABLE savings_goals ADD COLUMN created_at TEXT")
    db.execute(
        "UPDATE savings_goals SET goal_name = title, current_allocated = current_amount, "
        "created_at = COALESCE(created_at, CURRENT_TIMESTAMP)"
    )
    savings_event_columns = {row["name"] for row in db.execute("PRAGMA table_info(savings_allocation_events)")}
    if "transaction_id" not in savings_event_columns:
        db.execute("ALTER TABLE savings_allocation_events ADD COLUMN transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL")
    if "snapshot_included" not in savings_event_columns:
        db.execute("ALTER TABLE savings_allocation_events ADD COLUMN snapshot_included INTEGER NOT NULL DEFAULT 0 CHECK(snapshot_included IN (0, 1))")
    for table in ("goals", "completions", "monthly_goal_progress", "daily_metrics", "transactions", "savings_goals"):
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if "user_id" not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
    metric_info = {row["name"]: row["pk"] for row in db.execute("PRAGMA table_info(daily_metrics)")}
    if metric_info.get("user_id") != 1 or metric_info.get("metric_date") != 2:
        db.executescript(
            """
            CREATE TABLE daily_metrics_new (
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                metric_date TEXT NOT NULL,
                mood INTEGER CHECK(mood BETWEEN 1 AND 5),
                energy INTEGER CHECK(energy BETWEEN 1 AND 5),
                note TEXT NOT NULL DEFAULT '' CHECK(length(note) <= 2000),
                PRIMARY KEY (user_id, metric_date)
            );
            INSERT INTO daily_metrics_new (user_id, metric_date, mood, energy, note)
            SELECT user_id, metric_date, mood, energy, note FROM daily_metrics;
            DROP TABLE daily_metrics;
            ALTER TABLE daily_metrics_new RENAME TO daily_metrics;
            """
        )
    db.execute("CREATE INDEX IF NOT EXISTS idx_goals_user ON goals(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_completions_user_date ON completions(user_id, completed_on)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_progress_user_month ON monthly_goal_progress(user_id, month)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_metrics_user_date ON daily_metrics(user_id, metric_date)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user_created ON transactions(user_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_recurring_incomes_user_active ON recurring_incomes(user_id, is_active)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_expected_incomes_user_status_date ON expected_incomes(user_id, status, expected_date)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_recurring_occurrences_user_month ON recurring_income_occurrences(user_id, month_year)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_category_budgets_user_month ON category_budgets(user_id, month_year)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_savings_goals_user ON savings_goals(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_monthly_snapshots_user_month ON monthly_snapshots(user_id, month_year)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_savings_events_user_date ON savings_allocation_events(user_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_savings_events_goal ON savings_allocation_events(goal_id, id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_daily_budget_history_user_date ON daily_budget_history(user_id, effective_date)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_daily_budget_expenses_user_date ON daily_budget_expenses(user_id, expense_date)")
    db.execute(
        "INSERT OR IGNORE INTO daily_budget_history (user_id, daily_amount, effective_date) "
        "SELECT user_id, daily_limit, '0001-01-01' FROM daily_budget_settings"
    )
    db.commit()


def error(message, status=400):
    return jsonify({"error": message}), status


def valid_date(value):
    if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def valid_score(value):
    return value is None or (type(value) is int and 1 <= value <= 5)


def valid_month(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        return False
    try:
        year, month_number = map(int, value.split("-"))
        calendar.monthrange(year, month_number)
    except (ValueError, calendar.IllegalMonthError):
        return False
    return True


@app.before_request
def require_login():
    if request.path.lower().endswith((".db", ".db-wal", ".db-shm")) or request.path.startswith("/backups/"):
        abort(404)
    if "user_id" in session:
        user = get_db().execute(
            "SELECT id, username, email FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()
        if user is None:
            session.clear()
        else:
            g.current_user = dict(user)
    public_endpoints = {"login", "authenticate", "register", "create_account", "static", "service_worker"}
    if request.endpoint and request.endpoint not in public_endpoints and "user_id" not in session:
        if request.path.startswith("/api/"):
            return jsonify({"error": "กรุณาเข้าสู่ระบบก่อน", "loginUrl": url_for("login")}), 401
        return redirect(url_for("login", next=request.full_path if request.query_string else request.path))


@app.before_request
def validate_csrf():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected = session.get("csrf_token")
        supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not expected or not secrets.compare_digest(expected, supplied or ""):
            abort(400, "CSRF token ไม่ถูกต้องหรือหมดอายุ")


@app.get("/register")
def register():
    if "user_id" in session:
        return redirect(url_for("index"))
    return render_template("register.html", csrf_token=csrf_token())


@app.post("/register")
def create_account():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not 3 <= len(username) <= 40 or any(char.isspace() or not char.isprintable() for char in username):
        return render_template("register.html", error="ชื่อผู้ใช้ต้องมีความยาว 3-40 ตัวอักษร และไม่มีช่องว่างหรืออักขระควบคุม", csrf_token=csrf_token()), 400
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
        return render_template("register.html", error="กรุณากรอกอีเมลให้ถูกต้อง", csrf_token=csrf_token()), 400
    if len(password) < 10 or len(password) > 128:
        return render_template("register.html", error="รหัสผ่านต้องมีความยาว 10-128 ตัวอักษร", csrf_token=csrf_token()), 400

    db = get_db()
    try:
        cursor = db.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
            (username, email, generate_password_hash(password)),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return render_template("register.html", error="ชื่อผู้ใช้หรืออีเมลนี้ถูกใช้งานแล้ว", csrf_token=csrf_token()), 409

    from student_mode import create_student_user

    password_hash = db.execute("SELECT password_hash FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()[0]
    try:
        create_student_user(cursor.lastrowid, username, email, password_hash)
    except sqlite3.Error:
        db.execute("DELETE FROM users WHERE id = ?", (cursor.lastrowid,))
        db.commit()
        app.logger.exception("Could not create matching student account")
        return render_template("register.html", error="สร้างบัญชีไม่สำเร็จ กรุณาลองใหม่", csrf_token=csrf_token()), 500
    session.clear()
    session["user_id"] = cursor.lastrowid
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    return redirect(url_for("index"))


@app.get("/login")
def login():
    if "user_id" in session:
        return redirect(url_for("index"))
    return render_template("login.html", csrf_token=csrf_token())


@app.post("/login")
def authenticate():
    identifier = request.form.get("identifier", "").strip()
    password = request.form.get("password", "")
    user = get_db().execute(
        "SELECT id, password_hash FROM users WHERE username = ? OR email = ?",
        (identifier, identifier.lower()),
    ).fetchone()
    if user is None or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", error="ชื่อผู้ใช้/อีเมลหรือรหัสผ่านไม่ถูกต้อง", csrf_token=csrf_token()), 401

    session.clear()
    session["user_id"] = user["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    target = request.args.get("next", "")
    return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("index"))


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def sanitize_text(value):
    if not isinstance(value, str):
        return ""
    return bleach.clean(value, tags=[], attributes={}, protocols=[], strip=True).strip()


def _user_rows(db, table, columns, user_id, schema="main"):
    fields = ", ".join(columns)
    return [dict(row) for row in db.execute(
        f"SELECT {fields} FROM {schema}.{table} WHERE user_id = ? ORDER BY rowid", (user_id,)
    )]


def create_user_backup(user_id):
    db = get_db()
    db.execute("ATTACH DATABASE ? AS student_export", (str(STUDENT_DATABASE),))
    try:
        habits = {
            "goals": _user_rows(db, "goals", ("id", "name", "color", "is_active", "created_at"), user_id),
            "completions": _user_rows(db, "completions", ("goal_id", "completed_on", "done"), user_id),
            "monthly_goal_progress": _user_rows(db, "monthly_goal_progress", ("goal_id", "month", "percent", "note", "updated_at"), user_id),
            "daily_metrics": _user_rows(db, "daily_metrics", ("metric_date", "mood", "energy", "note"), user_id),
        }
        student = {
            "subjects": _user_rows(db, "subjects", ("id", "name", "color", "created_at"), user_id, "student_export"),
            "assignments": _user_rows(db, "assignments", ("subject_id", "title", "due_date", "status", "priority", "created_at"), user_id, "student_export"),
            "schedules": _user_rows(db, "schedules", ("weekday", "subject", "start_time", "end_time", "room"), user_id, "student_export"),
            "subject_notes": _user_rows(db, "subject_notes", ("subject_id", "subject", "title", "content", "updated_at"), user_id, "student_export"),
            "grades": _user_rows(db, "grades", ("subject", "assessment", "score", "max_score", "credits", "graded_on"), user_id, "student_export"),
            "exams": _user_rows(db, "exams", ("subject", "title", "exam_date", "note"), user_id, "student_export"),
            "student_calendar": _user_rows(db, "student_calendar", ("title", "event_date", "event_type", "note"), user_id, "student_export"),
            "student_settings": _user_rows(db, "student_settings", ("setting_key", "setting_value"), user_id, "student_export"),
        }
    finally:
        db.execute("DETACH DATABASE student_export")
    return {"format": "daymark-backup", "version": BACKUP_VERSION, "habits": habits, "student": student}


def _valid_backup(payload):
    if not isinstance(payload, dict) or set(payload) != {"format", "version", "habits", "student"}:
        return False
    if payload.get("format") != "daymark-backup" or payload.get("version") != BACKUP_VERSION:
        return False
    schemas = {
        "habits": {
            "goals": {"id", "name", "color", "is_active", "created_at"},
            "completions": {"goal_id", "completed_on", "done"},
            "monthly_goal_progress": {"goal_id", "month", "percent", "note", "updated_at"},
            "daily_metrics": {"metric_date", "mood", "energy", "note"},
        },
        "student": {
            "subjects": {"id", "name", "color", "created_at"},
            "assignments": {"subject_id", "title", "due_date", "status", "priority", "created_at"},
            "schedules": {"weekday", "subject", "start_time", "end_time", "room"},
            "subject_notes": {"subject_id", "subject", "title", "content", "updated_at"},
            "grades": {"subject", "assessment", "score", "max_score", "credits", "graded_on"},
            "exams": {"subject", "title", "exam_date", "note"},
            "student_calendar": {"title", "event_date", "event_type", "note"},
            "student_settings": {"setting_key", "setting_value"},
        },
    }
    total = 0
    for group, tables in schemas.items():
        section = payload.get(group)
        if not isinstance(section, dict) or set(section) != set(tables):
            return False
        for table, fields in tables.items():
            rows = section[table]
            if not isinstance(rows, list):
                return False
            total += len(rows)
            if total > BACKUP_MAX_ROWS:
                return False
            for row in rows:
                if not isinstance(row, dict) or set(row) != fields:
                    return False
    try:
        habits = payload["habits"]
        student = payload["student"]
        goal_ids = [row["id"] for row in habits["goals"]]
        subject_ids = [row["id"] for row in student["subjects"]]
        if any(type(value) is not int or value < 1 for value in goal_ids + subject_ids):
            return False
        if len(set(goal_ids)) != len(goal_ids) or len(set(subject_ids)) != len(subject_ids):
            return False
        if any(type(row["goal_id"]) is not int or row["goal_id"] not in goal_ids for table in ("completions", "monthly_goal_progress") for row in habits[table]):
            return False
        if any(type(row["subject_id"]) is not int or row["subject_id"] not in subject_ids for row in student["assignments"]):
            return False
        if any(row["subject_id"] is not None and (type(row["subject_id"]) is not int or row["subject_id"] not in subject_ids) for row in student["subject_notes"]):
            return False
        for row in habits["goals"]:
            if not isinstance(row["name"], str) or not 1 <= len(sanitize_text(row["name"])) <= 60 or not COLOR_PATTERN.fullmatch(row["color"]):
                return False
            if type(row["is_active"]) is not int or row["is_active"] not in (0, 1):
                return False
            if not isinstance(row["created_at"], str) or len(row["created_at"]) > 40:
                return False
        for row in habits["completions"]:
            if type(row["goal_id"]) is not int or not valid_date(row["completed_on"]) or type(row["done"]) is not int or row["done"] not in (0, 1):
                return False
        for row in habits["monthly_goal_progress"]:
            if type(row["goal_id"]) is not int or not valid_month(row["month"]):
                return False
            if type(row["percent"]) is not int or not 0 <= row["percent"] <= 100 or not isinstance(row["note"], str) or len(row["note"]) > 2000:
                return False
            if not isinstance(row["updated_at"], str) or len(row["updated_at"]) > 40:
                return False
        for row in habits["daily_metrics"]:
            if not valid_date(row["metric_date"]) or not valid_score(row["mood"]) or not valid_score(row["energy"]) or not isinstance(row["note"], str) or len(row["note"]) > 2000:
                return False
        for row in student["subjects"]:
            if not isinstance(row["name"], str) or not 1 <= len(sanitize_text(row["name"])) <= 80 or not re.fullmatch(r"#[0-9a-fA-F]{6}", row["color"]):
                return False
            if not isinstance(row["created_at"], str) or len(row["created_at"]) > 40:
                return False
        for row in student["assignments"]:
            if not isinstance(row["title"], str) or not 1 <= len(sanitize_text(row["title"])) <= 180 or not valid_date(row["due_date"]):
                return False
            if row["status"] not in {"todo", "in_progress", "done"} or row["priority"] not in {"high", "medium", "low"}:
                return False
            if not isinstance(row["created_at"], str) or len(row["created_at"]) > 40:
                return False
        for row in student["schedules"]:
            if type(row["weekday"]) is not int or row["weekday"] not in range(5) or not isinstance(row["start_time"], str) or not isinstance(row["end_time"], str):
                return False
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", row["start_time"]) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", row["end_time"]):
                return False
            if row["start_time"] >= row["end_time"] or not all(isinstance(row[key], str) and len(sanitize_text(row[key])) <= limit for key, limit in (("subject", 80), ("room", 80))):
                return False
        for row in student["subject_notes"]:
            if not all(isinstance(row[key], str) and 1 <= len(sanitize_text(row[key])) <= maximum for key, maximum in (("subject", 80), ("title", 180), ("content", 20000))):
                return False
            if not isinstance(row["updated_at"], str) or len(row["updated_at"]) > 40:
                return False
        for row in student["grades"]:
            if not all(isinstance(row[key], str) and 1 <= len(sanitize_text(row[key])) <= maximum for key, maximum in (("subject", 80), ("assessment", 160))):
                return False
            if type(row["score"]) not in (int, float) or type(row["max_score"]) not in (int, float) or type(row["credits"]) not in (int, float):
                return False
            if not all(math.isfinite(value) for value in (row["score"], row["max_score"], row["credits"])) or not 0 <= row["score"] <= row["max_score"] or row["max_score"] <= 0 or not 0 < row["credits"] <= 12 or not valid_date(row["graded_on"]):
                return False
        for row in student["exams"]:
            if not all(isinstance(row[key], str) and 1 <= len(sanitize_text(row[key])) <= maximum for key, maximum in (("subject", 80), ("title", 160))) or not valid_date(row["exam_date"]) or not isinstance(row["note"], str) or len(row["note"]) > 1000:
                return False
        for row in student["student_calendar"]:
            if not isinstance(row["title"], str) or not 1 <= len(sanitize_text(row["title"])) <= 160 or not valid_date(row["event_date"]):
                return False
            if row["event_type"] not in {"exam", "assignment", "holiday", "other"} or not isinstance(row["note"], str) or len(row["note"]) > 1000:
                return False
        if any(row["setting_key"] != "target_gpa" or not isinstance(row["setting_value"], str) or not math.isfinite(float(row["setting_value"])) or not 0.1 <= float(row["setting_value"]) <= 4 for row in student["student_settings"]):
            return False
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    return True


def restore_user_backup(payload, user_id):
    db = get_db()
    db.execute("ATTACH DATABASE ? AS student_restore", (str(STUDENT_DATABASE),))
    try:
        db.execute("BEGIN IMMEDIATE")
        for table in ("completions", "monthly_goal_progress", "daily_metrics", "goals"):
            db.execute(f"DELETE FROM main.{table} WHERE user_id = ?", (user_id,))
        for table in ("assignments", "subject_notes", "schedules", "grades", "exams", "student_calendar", "student_settings", "subjects"):
            db.execute(f"DELETE FROM student_restore.{table} WHERE user_id = ?", (user_id,))

        goal_ids = {}
        for row in payload["habits"]["goals"]:
            cursor = db.execute("INSERT INTO main.goals (user_id, name, color, is_active, created_at) VALUES (?, ?, ?, ?, ?)", (user_id, sanitize_text(row["name"]), row["color"], row["is_active"], row["created_at"]))
            goal_ids[row["id"]] = cursor.lastrowid
        for row in payload["habits"]["completions"]:
            db.execute("INSERT INTO main.completions (goal_id, user_id, completed_on, done) VALUES (?, ?, ?, ?)", (goal_ids[row["goal_id"]], user_id, row["completed_on"], row["done"]))
        for row in payload["habits"]["monthly_goal_progress"]:
            db.execute("INSERT INTO main.monthly_goal_progress (goal_id, user_id, month, percent, note, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (goal_ids[row["goal_id"]], user_id, row["month"], row["percent"], sanitize_text(row["note"]), row["updated_at"]))
        for row in payload["habits"]["daily_metrics"]:
            db.execute("INSERT INTO main.daily_metrics (user_id, metric_date, mood, energy, note) VALUES (?, ?, ?, ?, ?)", (user_id, row["metric_date"], row["mood"], row["energy"], sanitize_text(row["note"])))

        subject_ids = {}
        for row in payload["student"]["subjects"]:
            cursor = db.execute("INSERT INTO student_restore.subjects (user_id, name, color, created_at) VALUES (?, ?, ?, ?)", (user_id, sanitize_text(row["name"]), row["color"], row["created_at"]))
            subject_ids[row["id"]] = cursor.lastrowid
        for row in payload["student"]["assignments"]:
            subject_id = subject_ids[row["subject_id"]]
            subject_name = db.execute("SELECT name FROM student_restore.subjects WHERE id = ? AND user_id = ?", (subject_id, user_id)).fetchone()[0]
            db.execute("INSERT INTO student_restore.assignments (user_id, subject_id, subject, title, due_date, status, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (user_id, subject_id, subject_name, sanitize_text(row["title"]), row["due_date"], row["status"], row["priority"], row["created_at"]))
        for row in payload["student"]["schedules"]:
            db.execute("INSERT INTO student_restore.schedules (user_id, weekday, subject, start_time, end_time, room) VALUES (?, ?, ?, ?, ?, ?)", (user_id, row["weekday"], sanitize_text(row["subject"]), row["start_time"], row["end_time"], sanitize_text(row["room"])))
        for row in payload["student"]["subject_notes"]:
            subject_id = subject_ids.get(row["subject_id"])
            subject = row["subject"]
            if subject_id is not None:
                subject = db.execute("SELECT name FROM student_restore.subjects WHERE id = ?", (subject_id,)).fetchone()[0]
            db.execute("INSERT INTO student_restore.subject_notes (user_id, subject_id, subject, title, content, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (user_id, subject_id, sanitize_text(subject), sanitize_text(row["title"]), sanitize_text(row["content"]), row["updated_at"]))
        for row in payload["student"]["grades"]:
            db.execute("INSERT INTO student_restore.grades (user_id, subject, assessment, score, max_score, credits, graded_on) VALUES (?, ?, ?, ?, ?, ?, ?)", (user_id, sanitize_text(row["subject"]), sanitize_text(row["assessment"]), row["score"], row["max_score"], row["credits"], row["graded_on"]))
        for row in payload["student"]["exams"]:
            db.execute("INSERT INTO student_restore.exams (user_id, subject, title, exam_date, note) VALUES (?, ?, ?, ?, ?)", (user_id, sanitize_text(row["subject"]), sanitize_text(row["title"]), row["exam_date"], sanitize_text(row["note"])))
        for row in payload["student"]["student_calendar"]:
            db.execute("INSERT INTO student_restore.student_calendar (user_id, title, event_date, event_type, note) VALUES (?, ?, ?, ?, ?)", (user_id, sanitize_text(row["title"]), row["event_date"], row["event_type"], sanitize_text(row["note"])))
        for row in payload["student"]["student_settings"]:
            db.execute("INSERT INTO student_restore.student_settings (user_id, setting_key, setting_value) VALUES (?, ?, ?)", (user_id, row["setting_key"], row["setting_value"]))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute("DETACH DATABASE student_restore")


@app.get("/backup/export")
@login_required
def export_backup():
    data = json.dumps(create_user_backup(current_user_id()), ensure_ascii=False, indent=2)
    response = make_response(data)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    response.headers["Content-Disposition"] = 'attachment; filename="daymark-backup.json"'
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/backup/import")
@login_required
def import_backup():
    uploaded = request.files.get("backup_file")
    if uploaded is None or not uploaded.filename or not uploaded.filename.lower().endswith(".json"):
        flash("กรุณาเลือกไฟล์สำรอง .json", "error")
        return redirect(url_for("index"))
    raw = uploaded.stream.read(BACKUP_MAX_BYTES + 1)
    if len(raw) > BACKUP_MAX_BYTES:
        flash("ไฟล์สำรองมีขนาดใหญ่เกินกำหนด", "error")
        return redirect(url_for("index"))
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        flash("ไฟล์ JSON ไม่ถูกต้อง", "error")
        return redirect(url_for("index"))
    if not _valid_backup(payload):
        flash("โครงสร้างหรือข้อมูลในไฟล์สำรองไม่ถูกต้อง", "error")
        return redirect(url_for("index"))
    try:
        restore_user_backup(payload, current_user_id())
    except (sqlite3.Error, KeyError, ValueError):
        app.logger.exception("Could not restore user backup")
        flash("คืนค่าข้อมูลไม่สำเร็จ ไม่มีการเปลี่ยนแปลงข้อมูล", "error")
        return redirect(url_for("index"))
    flash("คืนค่าข้อมูลสำเร็จ", "success")
    return redirect(url_for("index"))


def backup_database(source, destination):
    if not source.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination.parent, 0o700)
    with sqlite3.connect(source) as origin, sqlite3.connect(destination) as target:
        origin.backup(target)
    os.chmod(destination, 0o600)


def backup_databases():
    backup_database(DATABASE, BACKUP_DIR / "database_backup.db")
    backup_database(STUDENT_DATABASE, BACKUP_DIR / "student_database_backup.db")


@app.cli.command("assign-legacy-data")
@click.argument("username")
def assign_legacy_data_command(username):
    """Assign unowned pre-authentication data to an existing account."""
    user = get_db().execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if user is None:
        raise click.ClickException(f"ไม่พบบัญชี {username}")
    user_id = user["id"]
    counts = {}
    db = get_db()
    for table in ("goals", "completions", "monthly_goal_progress", "daily_metrics"):
        cursor = db.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (user_id,))
        counts[table] = cursor.rowcount
    db.commit()

    from student_mode import assign_legacy_data

    counts.update(assign_legacy_data(user_id))
    for table, count in counts.items():
        click.echo(f"{table}: {count}")


@app.get("/")
@login_required
def index():
    return render_template("index.html", csrf_token=csrf_token())


def finance_form_error(message):
    flash(message, "error")
    return finance_form_redirect()


def finance_form_redirect():
    day_filter = request.form.get("day_filter", "")
    if valid_date(day_filter):
        return redirect(url_for("finance", month=day_filter[:7], day=day_filter))
    return redirect(url_for("finance", month=request.form.get("month_filter") or date.today().strftime("%Y-%m")))


def parse_finance_amount(value):
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if math.isfinite(amount) and amount > 0 else None


def parse_chat_transaction(message):
    digit_translation = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
    normalized = message.translate(digit_translation)
    amount_match = re.search(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?(?!\d)", normalized)
    if amount_match is None:
        return None

    amount = parse_finance_amount(amount_match.group(0).replace(",", ""))
    if amount is None or amount > 1_000_000_000:
        return None

    income_hint = re.search(r"ได้รับ|รับเงิน|พ่อโอนให้|แม่โอนให้|โอนเงินมา|เงินเดือน|รายรับ|ได้เงิน|ขายได้|โบนัส|ดอกเบี้ยเข้า", normalized)
    expense_hint = re.search(r"กิน|ซื้อ|จ่าย|ค่าอาหาร|ค่าเดินทาง|ค่าเรียน|เติมน้ำมัน|ชำระ|รายจ่าย|เสียเงิน|กาแฟ|ข้าว", normalized)
    if bool(income_hint) == bool(expense_hint):
        return None

    transaction_type = "income" if income_hint else "expense"
    if transaction_type == "income":
        category = "รายรับอื่น ๆ"
        if re.search(r"เงินเดือน", normalized):
            category = "เงินเดือน"
        elif re.search(r"พ่อ|แม่|โอนเงินมา|ได้รับโอน", normalized):
            category = "เงินจากครอบครัว"
        elif re.search(r"ขายได้", normalized):
            category = "รายได้จากการขาย"
    else:
        category = "รายจ่ายทั่วไป"
        if re.search(r"กิน|อาหาร|ข้าว|กาแฟ", normalized):
            category = "อาหาร"
        elif re.search(r"เดินทาง|รถ|น้ำมัน|แท็กซี่|รถเมล์", normalized):
            category = "เดินทาง"
        elif re.search(r"เรียน|หนังสือ|การบ้าน", normalized):
            category = "การศึกษา"
        elif re.search(r"ซื้อ|ช้อป", normalized):
            category = "ช้อปปิ้ง"
    return {"type": transaction_type, "amount": amount, "category": category}


def savings_balances(db, user_id):
    snapshots = db.execute(
        "SELECT COALESCE(SUM(net_savings), 0) FROM monthly_snapshots WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]
    spent = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM savings_allocation_events "
        "WHERE user_id = ? AND event_type = 'spend' AND snapshot_included = 0",
        (user_id,),
    ).fetchone()[0]
    allocated = db.execute(
        "SELECT COALESCE(SUM(current_allocated), 0) FROM savings_goals WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]
    lifetime_total = round(snapshots - spent, 2)
    allocated_total = round(allocated, 2)
    unallocated = round(max(lifetime_total - allocated_total, 0), 2)
    return lifetime_total, allocated_total, unallocated


def daily_budget_allocation(db, user_id, start_date, end_date):
    if end_date < start_date:
        return 0.0, 0
    history = db.execute(
        "SELECT daily_amount, effective_date FROM daily_budget_history "
        "WHERE user_id = ? AND effective_date <= ? ORDER BY effective_date",
        (user_id, end_date.isoformat()),
    ).fetchall()
    rate = 150.0
    history_index = 0
    while history_index < len(history) and history[history_index]["effective_date"] <= start_date.isoformat():
        rate = history[history_index]["daily_amount"]
        history_index += 1

    total = 0.0
    day_count = (end_date - start_date).days + 1
    for day_offset in range(day_count):
        current_date = date.fromordinal(start_date.toordinal() + day_offset).isoformat()
        while history_index < len(history) and history[history_index]["effective_date"] <= current_date:
            rate = history[history_index]["daily_amount"]
            history_index += 1
        total += rate
    return total, day_count


def daily_budget_returns(db, user_id, start_date, end_date):
    if end_date < start_date:
        return []
    history = db.execute(
        "SELECT daily_amount, effective_date FROM daily_budget_history "
        "WHERE user_id = ? AND effective_date <= ? ORDER BY effective_date",
        (user_id, end_date.isoformat()),
    ).fetchall()
    expenses = db.execute(
        "SELECT expense_date, SUM(amount) AS total FROM daily_budget_expenses "
        "WHERE user_id = ? AND expense_date >= ? AND expense_date <= ? "
        "GROUP BY expense_date",
        (user_id, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    expenses_by_date = {row["expense_date"]: row["total"] for row in expenses}
    rate = 150.0
    history_index = 0
    while history_index < len(history) and history[history_index]["effective_date"] <= start_date.isoformat():
        rate = history[history_index]["daily_amount"]
        history_index += 1

    returns = []
    day_count = (end_date - start_date).days + 1
    for day_offset in range(day_count):
        current_date = date.fromordinal(start_date.toordinal() + day_offset).isoformat()
        while history_index < len(history) and history[history_index]["effective_date"] <= current_date:
            rate = history[history_index]["daily_amount"]
            history_index += 1
        spent = expenses_by_date.get(current_date, 0)
        remaining = max(rate - spent, 0)
        if remaining > 0:
            returns.append({
                "date": current_date,
                "amount": remaining,
                "budget": rate,
                "spent": spent,
            })
    return returns


def daily_budget_overages(db, user_id, start_date, end_date):
    if end_date < start_date:
        return 0.0
    history = db.execute(
        "SELECT daily_amount, effective_date FROM daily_budget_history "
        "WHERE user_id = ? AND effective_date <= ? ORDER BY effective_date",
        (user_id, end_date.isoformat()),
    ).fetchall()
    expenses = db.execute(
        "SELECT expense_date, SUM(amount) AS total FROM daily_budget_expenses "
        "WHERE user_id = ? AND expense_date >= ? AND expense_date <= ? GROUP BY expense_date",
        (user_id, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    expenses_by_date = {row["expense_date"]: row["total"] for row in expenses}
    rate = 150.0
    history_index = 0
    while history_index < len(history) and history[history_index]["effective_date"] <= start_date.isoformat():
        rate = history[history_index]["daily_amount"]
        history_index += 1

    overage_total = 0.0
    day_count = (end_date - start_date).days + 1
    for day_offset in range(day_count):
        current_date = date.fromordinal(start_date.toordinal() + day_offset).isoformat()
        while history_index < len(history) and history[history_index]["effective_date"] <= current_date:
            rate = history[history_index]["daily_amount"]
            history_index += 1
        overage_total += max(expenses_by_date.get(current_date, 0) - rate, 0)
    return overage_total


def sync_category_budget_settlements(db, user_id, current_date=None):
    current_date = current_date or date.today()
    current_month = current_date.strftime("%Y-%m")
    budgets = db.execute(
        "SELECT b.id, b.category_name, b.monthly_limit, b.month_year, "
        "s.amount AS settled_amount, s.transaction_id "
        "FROM category_budgets b LEFT JOIN category_budget_settlements s ON s.budget_id = b.id "
        "WHERE b.user_id = ? AND (b.month_year < ? OR s.budget_id IS NOT NULL) "
        "ORDER BY b.month_year, b.id",
        (user_id, current_month),
    ).fetchall()
    for budget in budgets:
        db.execute(
            "INSERT OR IGNORE INTO category_budget_settlements (budget_id) VALUES (?)",
            (budget["id"],),
        )
        if budget["month_year"] >= current_month:
            if budget["transaction_id"] is not None:
                db.execute(
                    "DELETE FROM transactions WHERE id = ? AND user_id = ?",
                    (budget["transaction_id"], user_id),
                )
            db.execute("DELETE FROM category_budget_settlements WHERE budget_id = ?", (budget["id"],))
            continue
        year, month_number = map(int, budget["month_year"].split("-"))
        next_month = date(year + (month_number == 12), month_number % 12 + 1, 1)
        month_start = f"{budget['month_year']}-01"
        month_end = next_month.isoformat()
        spent = db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions "
            "WHERE user_id = ? AND type = 'expense' AND category = ? "
            "AND date(created_at) >= ? AND date(created_at) < ?",
            (user_id, budget["category_name"], month_start, month_end),
        ).fetchone()[0]
        remainder = round(max(budget["monthly_limit"] - spent, 0), 2)
        settlement = db.execute(
            "SELECT transaction_id FROM category_budget_settlements WHERE budget_id = ?",
            (budget["id"],),
        ).fetchone()
        transaction_id = settlement["transaction_id"]
        if remainder > 0:
            note = f"เงินเหลือจากงบ{budget['category_name']}ประจำเดือน: +{remainder:,.2f} บาท"
            timestamp = f"{month_end} 00:00:00"
            if transaction_id is None:
                cursor = db.execute(
                    "INSERT INTO transactions (user_id, type, amount, category, note, created_at) "
                    "VALUES (?, 'income', ?, 'เงินเหลือจากงบหมวดหมู่', ?, ?)",
                    (user_id, remainder, note, timestamp),
                )
                transaction_id = cursor.lastrowid
            else:
                db.execute(
                    "UPDATE transactions SET amount = ?, note = ?, created_at = ? "
                    "WHERE id = ? AND user_id = ?",
                    (remainder, note, timestamp, transaction_id, user_id),
                )
        elif transaction_id is not None:
            db.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id))
            transaction_id = None
        db.execute(
            "UPDATE category_budget_settlements SET amount = ?, transaction_id = ? WHERE budget_id = ?",
            (remainder, transaction_id, budget["id"]),
        )


def apply_recurring_incomes(db, user_id, month, current_date=None):
    current_date = current_date or date.today()
    if month != current_date.strftime("%Y-%m"):
        return
    year, month_number = map(int, month.split("-"))
    last_day = calendar.monthrange(year, month_number)[1]
    incomes = db.execute(
        "SELECT id, title, amount, day_of_month FROM recurring_incomes "
        "WHERE user_id = ? AND is_active = 1 ORDER BY id",
        (user_id,),
    ).fetchall()
    for income in incomes:
        due_day = min(income["day_of_month"], last_day)
        if due_day > current_date.day:
            continue
        cursor = db.execute(
            "INSERT OR IGNORE INTO recurring_income_occurrences "
            "(user_id, recurring_income_id, month_year) VALUES (?, ?, ?)",
            (user_id, income["id"], month),
        )
        if cursor.rowcount == 0:
            continue
        transaction = db.execute(
            "INSERT INTO transactions (user_id, type, amount, category, note, created_at) "
            "VALUES (?, 'income', ?, 'รายรับประจำ', ?, ?)",
            (
                user_id,
                income["amount"],
                f"รายรับประจำ: {income['title']}",
                f"{month}-{due_day:02d} 12:00:00",
            ),
        )
        db.execute(
            "UPDATE recurring_income_occurrences SET transaction_id = ? WHERE id = ?",
            (transaction.lastrowid, cursor.lastrowid),
        )


def close_monthly_snapshots(db, user_id, current_month):
    first_month = db.execute(
        "SELECT MIN(month_value) FROM ("
        "SELECT substr(created_at, 1, 7) AS month_value FROM transactions WHERE user_id = ? "
        "UNION ALL SELECT substr(expense_date, 1, 7) FROM daily_budget_expenses WHERE user_id = ? "
        "UNION ALL SELECT month_year FROM category_budgets WHERE user_id = ? "
        "UNION ALL SELECT substr(effective_date, 1, 7) FROM daily_budget_history "
        "WHERE user_id = ? AND effective_date > '0001-01-01')",
        (user_id, user_id, user_id, user_id),
    ).fetchone()[0]
    if first_month is None or first_month >= current_month:
        return

    year, month_number = map(int, first_month.split("-"))
    current_year, current_month_number = map(int, current_month.split("-"))
    stop_ordinal = current_year * 12 + current_month_number - 1
    while year * 12 + month_number - 1 < stop_ordinal:
        month = f"{year:04d}-{month_number:02d}"
        month_start = f"{month}-01"
        next_month = date(year + (month_number == 12), month_number % 12 + 1, 1)
        next_month_start = next_month.isoformat()
        month_end = next_month - date.resolution
        income = db.execute(
            "SELECT COALESCE(SUM(t.amount), 0) FROM transactions t "
            "WHERE t.user_id = ? AND t.type = 'income' AND date(t.created_at) >= ? "
            "AND date(t.created_at) < ? AND NOT EXISTS ("
            "SELECT 1 FROM category_budget_settlements s WHERE s.transaction_id = t.id) "
            "AND NOT EXISTS (SELECT 1 FROM savings_allocation_events e "
            "WHERE e.transaction_id = t.id AND e.event_type = 'spend')",
            (user_id, month_start, next_month_start),
        ).fetchone()[0]
        transaction_expenses = db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE user_id = ? "
            "AND type = 'expense' AND date(created_at) >= ? AND date(created_at) < ?",
            (user_id, month_start, next_month_start),
        ).fetchone()[0]
        daily_expenses = db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM daily_budget_expenses "
            "WHERE user_id = ? AND expense_date >= ? AND expense_date < ?",
            (user_id, month_start, next_month_start),
        ).fetchone()[0]
        daily_allocation, _ = daily_budget_allocation(
            db, user_id, date.fromisoformat(month_start), month_end
        )
        daily_remaining = sum(
            item["amount"] for item in daily_budget_returns(
                db, user_id, date.fromisoformat(month_start), month_end
            )
        )
        daily_overage = daily_budget_overages(
            db, user_id, date.fromisoformat(month_start), month_end
        )
        budgets = db.execute(
            "SELECT b.category_name, b.monthly_limit, COALESCE(SUM(t.amount), 0) AS spent "
            "FROM category_budgets b LEFT JOIN transactions t ON t.user_id = b.user_id "
            "AND t.category = b.category_name AND t.type = 'expense' "
            "AND date(t.created_at) >= ? AND date(t.created_at) < ? "
            "WHERE b.user_id = ? AND b.month_year = ? GROUP BY b.id",
            (month_start, next_month_start, user_id, month),
        ).fetchall()
        category_allocation = sum(item["monthly_limit"] for item in budgets)
        category_remaining = sum(max(item["monthly_limit"] - item["spent"], 0) for item in budgets)
        category_overage = sum(max(item["spent"] - item["monthly_limit"], 0) for item in budgets)
        budgeted_spent = sum(item["spent"] for item in budgets)
        unbudgeted_expenses = max(transaction_expenses - budgeted_spent, 0)
        transferred_savings = db.execute(
            "SELECT COALESCE(SUM(e.amount), 0) FROM savings_allocation_events e "
            "JOIN transactions t ON t.id = e.transaction_id "
            "WHERE e.user_id = ? AND e.event_type = 'spend' AND t.type = 'income' "
            "AND date(e.created_at) >= ? AND date(e.created_at) < ?",
            (user_id, month_start, next_month_start),
        ).fetchone()[0]
        net_savings = round(
            income - unbudgeted_expenses - daily_allocation - category_allocation
            + daily_remaining + category_remaining - daily_overage - category_overage
            - transferred_savings,
            2,
        )
        total_expenses = transaction_expenses + daily_expenses
        snapshot_cursor = db.execute(
            "INSERT INTO monthly_snapshots "
            "(user_id, month_year, total_income, total_expenses, net_savings) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, month_year) DO NOTHING",
            (user_id, month, income, total_expenses, net_savings),
        )
        if snapshot_cursor.rowcount:
            db.execute(
                "UPDATE savings_allocation_events SET snapshot_included = 1 "
                "WHERE user_id = ? AND event_type = 'spend' AND substr(created_at, 1, 7) = ?",
                (user_id, month),
            )
        month_number += 1
        if month_number == 13:
            year += 1
            month_number = 1


@app.get("/finance")
@login_required
def finance():
    user_id = current_user_id()
    selected_month = request.args.get("month", date.today().strftime("%Y-%m"))
    selected_date = request.args.get("day", "")
    try:
        projection_months = int(request.args.get("projection_months", "3"))
    except (TypeError, ValueError):
        projection_months = 3
    projection_months = max(1, min(projection_months, 120))
    if not valid_month(selected_month):
        selected_month = date.today().strftime("%Y-%m")
    if selected_date and not valid_date(selected_date):
        selected_date = ""

    db = get_db()
    sync_category_budget_settlements(db, user_id)
    current_month = date.today().strftime("%Y-%m")
    apply_recurring_incomes(db, user_id, selected_month)
    current_year, current_month_number = map(int, current_month.split("-"))
    next_current_month = date(
        current_year + (current_month_number == 12), current_month_number % 12 + 1, 1
    ).strftime("%Y-%m")
    rollover_cutoff = max(current_month, min(selected_month, next_current_month))
    close_monthly_snapshots(db, user_id, rollover_cutoff)
    db.commit()
    finance_day = date.fromisoformat(selected_date) if selected_date else date.today()
    finance_day_string = finance_day.isoformat()
    daily_budget_row = db.execute(
        "SELECT daily_amount AS daily_limit FROM daily_budget_history "
        "WHERE user_id = ? AND effective_date <= ? ORDER BY effective_date DESC LIMIT 1",
        (user_id, finance_day_string),
    ).fetchone()
    if daily_budget_row is None:
        daily_budget_row = db.execute(
            "SELECT daily_limit FROM daily_budget_settings WHERE user_id = ?", (user_id,)
        ).fetchone()
    daily_budget_limit = daily_budget_row["daily_limit"] if daily_budget_row else 150.0
    daily_spent_today = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM daily_budget_expenses "
        "WHERE user_id = ? AND expense_date = ?",
        (user_id, finance_day_string),
    ).fetchone()[0]
    daily_budget_remaining = max(daily_budget_limit - daily_spent_today, 0)
    daily_budget_overage = max(daily_spent_today - daily_budget_limit, 0)
    daily_budget_percent = max(0, min(daily_spent_today / daily_budget_limit * 100, 100)) if daily_budget_limit else (100 if daily_spent_today else 0)
    daily_budget_warning = daily_budget_remaining <= 0 or (
        daily_budget_limit > 0 and daily_budget_remaining <= daily_budget_limit * 0.2
    )
    year, month_number = map(int, selected_month.split("-"))
    month_day_count = calendar.monthrange(year, month_number)[1]
    month_start = date(year, month_number, 1).isoformat()
    month_end = date(year, month_number, month_day_count).isoformat()
    next_month_start = (date(year + (month_number == 12), month_number % 12 + 1, 1)).isoformat()
    period_start = date.fromisoformat(month_start)
    period_end = date.fromisoformat(month_end)
    date_clause = "date(created_at) >= ? AND date(created_at) < ?"
    date_params = (month_start, next_month_start)

    budget_period_end = min(finance_day, period_end)
    budget_allocation, budget_day_count = daily_budget_allocation(
        db, user_id, max(finance_day + date.resolution, period_start), period_end
    )
    daily_savings_returns = daily_budget_returns(
        db, user_id, period_start, min(finance_day - date.resolution, period_end)
    )
    realized_daily_expense_total = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM daily_budget_expenses "
        "WHERE user_id = ? AND expense_date >= ? AND expense_date <= ?",
        (user_id, month_start, finance_day_string),
    ).fetchone()[0]
    daily_budget_expenses = db.execute(
        "SELECT id, expense_date, amount FROM daily_budget_expenses "
        "WHERE user_id = ? AND expense_date = ? ORDER BY id DESC",
        (user_id, finance_day_string),
    ).fetchall()
    transaction_rows = db.execute(
        f"SELECT t.id, t.type, t.amount, t.category, t.note, t.created_at, "
        f"EXISTS (SELECT 1 FROM category_budget_settlements s WHERE s.transaction_id = t.id) "
        f"AS is_category_budget_return, EXISTS (SELECT 1 FROM savings_allocation_events e "
        f"WHERE e.transaction_id = t.id) AS is_savings_withdrawal FROM transactions t "
        f"WHERE t.user_id = ? AND {date_clause.replace('created_at', 't.created_at')} "
        f"ORDER BY t.created_at DESC, t.id DESC",
        (user_id, *date_params),
    ).fetchall()
    transactions = [dict(row) for row in transaction_rows]
    totals = db.execute(
        f"SELECT type, COALESCE(SUM(amount), 0) AS total FROM transactions "
        f"WHERE user_id = ? AND {date_clause} GROUP BY type",
        (user_id, *date_params),
    ).fetchall()
    totals_by_type = {row["type"]: row["total"] for row in totals}
    for savings_return in daily_savings_returns:
        transactions.append({
            "id": None,
            "type": "income",
            "amount": savings_return["amount"],
            "category": "เงินเหลือจากงบรายวัน (Daily Savings Return)",
            "note": (
                f"บันทึกอัตโนมัติ · งบ ฿{ savings_return['budget']:,.2f} "
                f"− ใช้จริง ฿{ savings_return['spent']:,.2f}"
            ),
            "created_at": f"{savings_return['date']} 23:59:58",
            "is_daily_savings_return": True,
        })
    transactions.sort(key=lambda item: (item["created_at"], item["id"] or 0), reverse=True)
    expenses_by_category = db.execute(
        f"SELECT category, SUM(amount) AS total FROM transactions "
        f"WHERE user_id = ? AND type = 'expense' AND {date_clause} "
        f"GROUP BY category ORDER BY total DESC",
        (user_id, *date_params),
    ).fetchall()
    expense_total = totals_by_type.get("expense", 0)
    categories = [
        {"category": row["category"], "total": row["total"], "percent": row["total"] / expense_total * 100}
        for row in expenses_by_category
    ]
    category_budgets = db.execute(
        "SELECT b.id, b.category_name, b.monthly_limit, "
        "COALESCE(SUM(t.amount), 0) AS spent "
        "FROM category_budgets b LEFT JOIN transactions t ON t.user_id = b.user_id "
        "AND t.category = b.category_name AND t.type = 'expense' "
        "AND date(t.created_at) >= ? AND date(t.created_at) < ? "
        "WHERE b.user_id = ? AND b.month_year = ? "
        "GROUP BY b.id ORDER BY b.category_name COLLATE NOCASE",
        (month_start, next_month_start, user_id, selected_month),
    ).fetchall()
    category_budgets = [
        {
            "id": row["id"],
            "category_name": row["category_name"],
            "monthly_limit": row["monthly_limit"],
            "spent": row["spent"],
            "remaining": max(row["monthly_limit"] - row["spent"], 0),
            "percent": min(row["spent"] / row["monthly_limit"] * 100, 100)
            if row["monthly_limit"] else (100 if row["spent"] else 0),
        }
        for row in category_budgets
    ]
    category_budget_reserved = sum(budget["monthly_limit"] for budget in category_budgets)
    budgeted_category_spending = sum(
        min(budget["spent"], budget["monthly_limit"]) for budget in category_budgets
    )
    savings_goals = db.execute(
        "SELECT id, goal_name AS title, target_amount, current_allocated AS current_amount, "
        "deadline, created_at FROM savings_goals "
        "WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    recurring_incomes = db.execute(
        "SELECT id, title, amount, day_of_month, is_active FROM recurring_incomes "
        "WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    expected_incomes = db.execute(
        "SELECT id, title, amount, expected_date, status FROM expected_incomes "
        "WHERE user_id = ? AND status = 'pending' ORDER BY expected_date, id DESC",
        (user_id,),
    ).fetchall()
    current_month = date.today().strftime("%Y-%m")
    current_month_start = f"{current_month}-01"
    next_month_date = date.today().replace(day=28) + date.resolution * 4
    next_month_start = next_month_date.replace(day=1).isoformat()
    lifetime_income_this_month = db.execute(
        "SELECT COALESCE(SUM(t.amount), 0) FROM transactions t "
        "WHERE t.user_id = ? AND t.type = 'income' AND date(t.created_at) >= ? "
        "AND date(t.created_at) < ? AND NOT EXISTS ("
        "SELECT 1 FROM category_budget_settlements s WHERE s.transaction_id = t.id)",
        (user_id, current_month_start, next_month_start),
    ).fetchone()[0]
    lifetime_expenses_this_month = db.execute(
        "SELECT (SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE user_id = ? "
        "AND type = 'expense' AND date(created_at) >= ? AND date(created_at) < ?) + "
        "(SELECT COALESCE(SUM(amount), 0) FROM daily_budget_expenses WHERE user_id = ? "
        "AND expense_date >= ? AND expense_date < ?)",
        (user_id, current_month_start, next_month_start, user_id, current_month_start, next_month_start),
    ).fetchone()[0]
    current_year, current_month_number = map(int, current_month.split("-"))
    current_month_end = date(current_year, current_month_number, calendar.monthrange(current_year, current_month_number)[1])
    current_daily_allocation, _ = daily_budget_allocation(
        db, user_id, date.fromisoformat(current_month_start), current_month_end
    )
    current_daily_overage = daily_budget_overages(
        db, user_id, date.fromisoformat(current_month_start), date.today()
    )
    current_category_budgets = db.execute(
        "SELECT b.monthly_limit, COALESCE(SUM(t.amount), 0) AS spent "
        "FROM category_budgets b LEFT JOIN transactions t ON t.user_id = b.user_id "
        "AND t.category = b.category_name AND t.type = 'expense' "
        "AND date(t.created_at) >= ? AND date(t.created_at) < ? "
        "WHERE b.user_id = ? AND b.month_year = ? GROUP BY b.id",
        (current_month_start, next_month_start, user_id, current_month),
    ).fetchall()
    current_category_allocation = sum(row["monthly_limit"] for row in current_category_budgets)
    current_category_spend_covered = sum(
        min(row["spent"], row["monthly_limit"]) for row in current_category_budgets
    )
    current_transaction_expenses = lifetime_expenses_this_month - db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM daily_budget_expenses "
        "WHERE user_id = ? AND expense_date >= ? AND expense_date < ?",
        (user_id, current_month_start, next_month_start),
    ).fetchone()[0]
    current_out_of_budget_expenses = max(
        current_transaction_expenses - current_category_spend_covered, 0
    )
    snapshot_totals = db.execute(
        "SELECT COALESCE(SUM(net_savings), 0) AS savings, "
        "COALESCE(SUM(total_income), 0) AS income, COUNT(*) AS month_count "
        "FROM monthly_snapshots WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    lifetime_savings, allocated_savings, unallocated_savings = savings_balances(db, user_id)
    estimated_month_end_savings = (
        round(
            lifetime_income_this_month - current_daily_allocation - current_category_allocation
            - current_out_of_budget_expenses - current_daily_overage,
            2,
        )
        if lifetime_income_this_month else 0
    )
    lifetime_income = snapshot_totals["income"]
    has_current_month_activity = lifetime_income_this_month > 0 or lifetime_expenses_this_month > 0
    if snapshot_totals["month_count"]:
        average_monthly_savings = lifetime_savings / snapshot_totals["month_count"]
        average_savings_rate = lifetime_savings / lifetime_income * 100 if lifetime_income else 0
        average_savings_basis = "เฉลี่ยจากเดือนที่ปิดงบแล้ว"
        savings_rate_basis = "เทียบรายรับจากเดือนที่ปิดงบแล้ว"
    else:
        average_monthly_savings = estimated_month_end_savings if has_current_month_activity else 0
        average_savings_rate = (
            average_monthly_savings / lifetime_income_this_month * 100
            if lifetime_income_this_month else 0
        )
        average_savings_basis = "ประเมินจากงบประมาณคงเหลือสิ้นเดือนสุทธิ"
        savings_rate_basis = "เทียบรายรับเดือนปัจจุบันที่บันทึกแล้ว"
    tracked_months = snapshot_totals["month_count"]
    savings_projection = lifetime_savings + average_monthly_savings * projection_months
    month_income = totals_by_type.get("income", 0)
    has_month_spending = expense_total > 0 or realized_daily_expense_total > 0
    if month_income == 0 and not has_month_spending:
        balance_after_budget = 0
    else:
        balance_after_budget = (
            month_income - budget_allocation - expense_total
            - realized_daily_expense_total + budgeted_category_spending - category_budget_reserved
        )
    current_year = date.today().year
    year_start = f"{current_year:04d}-01"
    prior_year_savings = db.execute(
        "SELECT COALESCE(SUM(net_savings), 0) FROM monthly_snapshots "
        "WHERE user_id = ? AND month_year < ?",
        (user_id, year_start),
    ).fetchone()[0]
    year_snapshots = {
        row["month_year"]: row["net_savings"]
        for row in db.execute(
            "SELECT month_year, net_savings FROM monthly_snapshots "
            "WHERE user_id = ? AND month_year >= ? AND month_year <= ?",
            (user_id, year_start, current_month),
        )
    }
    growth_labels = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
    growth_values = []
    cumulative_savings = prior_year_savings
    for month_number in range(1, 13):
        month_key = f"{current_year:04d}-{month_number:02d}"
        cumulative_savings += year_snapshots.get(month_key, 0)
        growth_values.append(round(cumulative_savings, 2))
    return render_template(
        "finance.html",
        csrf_token=csrf_token(),
        transactions=transactions,
        savings_goals=savings_goals,
        recurring_incomes=recurring_incomes,
        expected_incomes=expected_incomes,
        income_total=totals_by_type.get("income", 0),
        expense_total=expense_total,
        balance=totals_by_type.get("income", 0) - expense_total,
        budget_allocation=budget_allocation,
        budget_day_count=budget_day_count,
        realized_daily_expense_total=realized_daily_expense_total,
        balance_after_budget=balance_after_budget,
        category_budget_reserved=category_budget_reserved,
        categories=categories,
        category_budgets=category_budgets,
        selected_month=selected_month,
        selected_date=selected_date,
        finance_day=finance_day_string,
        today_date=date.today().isoformat(),
        tomorrow_date=(date.today() + date.resolution).isoformat(),
        next_finance_day=(finance_day + date.resolution if finance_day < date.max else finance_day).isoformat(),
        daily_budget_limit=daily_budget_limit,
        daily_spent_today=daily_spent_today,
        daily_budget_remaining=daily_budget_remaining,
        daily_budget_overage=daily_budget_overage,
        daily_budget_percent=daily_budget_percent,
        daily_budget_warning=daily_budget_warning,
        daily_budget_expenses=daily_budget_expenses,
        lifetime_savings=lifetime_savings,
        allocated_savings=allocated_savings,
        unallocated_savings=unallocated_savings,
        average_savings_rate=average_savings_rate,
        average_monthly_savings=average_monthly_savings,
        average_savings_basis=average_savings_basis,
        savings_rate_basis=savings_rate_basis,
        projection_months=projection_months,
        savings_projection=savings_projection,
        tracked_months=tracked_months,
        growth_labels=growth_labels,
        growth_values=growth_values,
        growth_year=current_year,
        messages=list(get_flashed_messages(with_categories=True)),
    )


@app.post("/finance/force-month-end-snapshot")
@login_required
def force_month_end_snapshot():
    selected_month = request.form.get("month_filter", "")
    if not valid_month(selected_month):
        selected_month = date.today().strftime("%Y-%m")
    selected_year, selected_month_number = map(int, selected_month.split("-"))
    selected_next_month = date(
        selected_year + (selected_month_number == 12), selected_month_number % 12 + 1, 1
    ).strftime("%Y-%m")
    current_year, current_month_number = date.today().year, date.today().month
    next_current_month = date(
        current_year + (current_month_number == 12), current_month_number % 12 + 1, 1
    ).strftime("%Y-%m")
    snapshot_cutoff = min(selected_next_month, next_current_month)
    db = get_db()
    close_monthly_snapshots(db, current_user_id(), snapshot_cutoff)
    db.commit()
    flash(f"ปิดงบเดือนก่อนหน้าถึง {selected_month} และบันทึก Snapshot แล้ว", "success")
    return redirect(url_for("finance", month=selected_month))


@app.post("/finance/daily-budget")
@login_required
def update_daily_budget():
    raw_limit = request.form.get("daily_limit", "")
    try:
        daily_limit = float(raw_limit)
    except (TypeError, ValueError):
        daily_limit = math.nan
    if not math.isfinite(daily_limit) or daily_limit < 0 or daily_limit > 1_000_000_000:
        return finance_form_error("กรุณาระบุงบรายวันเป็นจำนวนเงินตั้งแต่ 0 บาทขึ้นไป")
    effective_date = request.form.get("day_filter", "")
    if not valid_date(effective_date):
        effective_date = date.today().isoformat()
    db = get_db()
    user_id = current_user_id()
    db.execute(
        "INSERT OR IGNORE INTO daily_budget_history (user_id, daily_amount, effective_date) "
        "VALUES (?, 150, '0001-01-01')",
        (user_id,),
    )
    db.execute(
        "INSERT INTO daily_budget_history (user_id, daily_amount, effective_date) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, effective_date) DO UPDATE SET daily_amount = excluded.daily_amount",
        (user_id, daily_limit, effective_date),
    )
    db.execute(
        "INSERT INTO daily_budget_settings (user_id, daily_limit) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET daily_limit = excluded.daily_limit",
        (user_id, daily_limit),
    )
    db.commit()
    flash("บันทึกงบประมาณรายวันแล้ว", "success")
    return finance_form_redirect()


@app.post("/finance/daily-budget/expenses")
@login_required
def create_daily_budget_expense():
    amount = parse_finance_amount(request.form.get("expense_amount"))
    if amount is None or amount > 1_000_000_000:
        return finance_form_error("กรุณาระบุค่าใช้จ่ายเป็นจำนวนเงินมากกว่า 0 บาท")
    expense_date = request.form.get("day_filter", "")
    if not valid_date(expense_date):
        expense_date = date.today().isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO daily_budget_expenses (user_id, expense_date, amount) VALUES (?, ?, ?)",
        (current_user_id(), expense_date, amount),
    )
    db.commit()
    flash("บันทึกค่าใช้จ่ายจากงบรายวันแล้ว", "success")
    return finance_form_redirect()


@app.post("/finance/daily-budget/expenses/<int:expense_id>/update")
@login_required
def update_daily_budget_expense(expense_id):
    amount = parse_finance_amount(request.form.get("amount"))
    if amount is None or amount > 1_000_000_000:
        return finance_form_error("กรุณาระบุค่าใช้จ่ายเป็นจำนวนเงินมากกว่า 0 บาท")
    db = get_db()
    cursor = db.execute(
        "UPDATE daily_budget_expenses SET amount = ? WHERE id = ? AND user_id = ?",
        (amount, expense_id, current_user_id()),
    )
    if cursor.rowcount == 0:
        abort(404)
    db.commit()
    flash("แก้ไขค่าใช้จ่ายจากงบรายวันแล้ว", "success")
    return finance_form_redirect()


@app.post("/finance/daily-budget/expenses/<int:expense_id>/delete")
@login_required
def delete_daily_budget_expense(expense_id):
    db = get_db()
    cursor = db.execute(
        "DELETE FROM daily_budget_expenses WHERE id = ? AND user_id = ?",
        (expense_id, current_user_id()),
    )
    if cursor.rowcount == 0:
        abort(404)
    db.commit()
    flash("ลบค่าใช้จ่ายจากงบรายวันแล้ว", "success")
    return finance_form_redirect()


@app.post("/finance/recurring-incomes")
@login_required
def create_recurring_income():
    title = sanitize_text(request.form.get("title", "")).strip()
    amount = parse_finance_amount(request.form.get("amount"))
    try:
        day_of_month = int(request.form.get("day_of_month", ""))
    except (TypeError, ValueError):
        day_of_month = 0
    if not title or len(title) > 100 or amount is None or amount > 1_000_000_000:
        return finance_form_error("กรุณาระบุชื่อและจำนวนเงินรายรับประจำให้ถูกต้อง")
    if not 1 <= day_of_month <= 31:
        return finance_form_error("วันที่รับเงินต้องอยู่ระหว่าง 1 ถึง 31")
    db = get_db()
    db.execute(
        "INSERT INTO recurring_incomes (user_id, title, amount, day_of_month) VALUES (?, ?, ?, ?)",
        (current_user_id(), title, amount, day_of_month),
    )
    db.commit()
    flash("เพิ่มรายรับประจำแล้ว", "success")
    return redirect(url_for("finance", month=request.form.get("month_filter") or date.today().strftime("%Y-%m")))


@app.post("/finance/expected-incomes")
@login_required
def create_expected_income():
    title = sanitize_text(request.form.get("title", "")).strip()
    amount = parse_finance_amount(request.form.get("amount"))
    expected_date = request.form.get("expected_date", "")
    if not title or len(title) > 100 or amount is None or amount > 1_000_000_000:
        return finance_form_error("กรุณาระบุชื่อและจำนวนเงินที่คาดว่าจะได้รับให้ถูกต้อง")
    if not valid_date(expected_date):
        return finance_form_error("วันที่คาดว่าจะได้รับไม่ถูกต้อง")
    db = get_db()
    db.execute(
        "INSERT INTO expected_incomes (user_id, title, amount, expected_date) VALUES (?, ?, ?, ?)",
        (current_user_id(), title, amount, expected_date),
    )
    db.commit()
    flash("เพิ่มรายการเงินคาดว่าจะได้รับแล้ว โดยยังไม่นับเป็นรายรับจริง", "success")
    return finance_form_redirect()


@app.post("/finance/expected-incomes/<int:income_id>/received")
@login_required
def receive_expected_income(income_id):
    db = get_db()
    user_id = current_user_id()
    db.execute("BEGIN IMMEDIATE")
    expected_income = db.execute(
        "SELECT title, amount, status FROM expected_incomes WHERE id = ? AND user_id = ?",
        (income_id, user_id),
    ).fetchone()
    if expected_income is None:
        abort(404)
    if expected_income["status"] == "received":
        db.commit()
        flash("รายการนี้บันทึกว่าได้รับเงินแล้ว", "success")
        return redirect(url_for("finance", month=date.today().strftime("%Y-%m")))
    received_date = date.today().isoformat()
    cursor = db.execute(
        "INSERT INTO transactions (user_id, type, amount, category, note, created_at) "
        "VALUES (?, 'income', ?, 'เงินที่ได้รับ', ?, ?)",
        (user_id, expected_income["amount"], f"เงินคาดว่าจะได้รับ: {expected_income['title']}", f"{received_date} 12:00:00"),
    )
    db.execute(
        "UPDATE expected_incomes SET status = 'received', received_transaction_id = ? "
        "WHERE id = ? AND user_id = ? AND status = 'pending'",
        (cursor.lastrowid, income_id, user_id),
    )
    db.commit()
    flash("บันทึกรายรับเงินจริงแล้ว", "success")
    return redirect(url_for("finance", month=received_date[:7]))


@app.post("/finance/expected-incomes/<int:income_id>/pending")
@login_required
def keep_expected_income_pending(income_id):
    db = get_db()
    expected_income = db.execute(
        "SELECT status FROM expected_incomes WHERE id = ? AND user_id = ?",
        (income_id, current_user_id()),
    ).fetchone()
    if expected_income is None:
        abort(404)
    if expected_income["status"] != "pending":
        return finance_form_error("รายการที่ได้รับเงินแล้วไม่สามารถกลับเป็นรายการรอได้")
    flash("คงรายการนี้ไว้ในสถานะรอรับแล้ว", "success")
    return finance_form_redirect()


@app.post("/finance/expected-incomes/<int:income_id>/postpone")
@login_required
def postpone_expected_income(income_id):
    expected_date = request.form.get("expected_date", "")
    if not valid_date(expected_date):
        return finance_form_error("วันที่คาดว่าจะได้รับไม่ถูกต้อง")
    db = get_db()
    cursor = db.execute(
        "UPDATE expected_incomes SET expected_date = ? "
        "WHERE id = ? AND user_id = ? AND status = 'pending'",
        (expected_date, income_id, current_user_id()),
    )
    if cursor.rowcount == 0:
        abort(404)
    db.commit()
    flash("เลื่อนวันที่คาดว่าจะได้รับแล้ว", "success")
    return finance_form_redirect()


@app.post("/finance/recurring-incomes/<int:income_id>/update")
@login_required
def update_recurring_income(income_id):
    title = sanitize_text(request.form.get("title", "")).strip()
    amount = parse_finance_amount(request.form.get("amount"))
    try:
        day_of_month = int(request.form.get("day_of_month", ""))
    except (TypeError, ValueError):
        day_of_month = 0
    is_active = 1 if request.form.get("is_active") == "1" else 0
    if not title or len(title) > 100 or amount is None or amount > 1_000_000_000:
        return finance_form_error("กรุณาระบุชื่อและจำนวนเงินรายรับประจำให้ถูกต้อง")
    if not 1 <= day_of_month <= 31:
        return finance_form_error("วันที่รับเงินต้องอยู่ระหว่าง 1 ถึง 31")
    db = get_db()
    cursor = db.execute(
        "UPDATE recurring_incomes SET title = ?, amount = ?, day_of_month = ?, is_active = ? "
        "WHERE id = ? AND user_id = ?",
        (title, amount, day_of_month, is_active, income_id, current_user_id()),
    )
    if cursor.rowcount == 0:
        abort(404)
    db.commit()
    flash("แก้ไขรายรับประจำแล้ว", "success")
    return redirect(url_for("finance", month=request.form.get("month_filter") or date.today().strftime("%Y-%m")))


@app.post("/finance/recurring-incomes/<int:income_id>/delete")
@login_required
def delete_recurring_income(income_id):
    db = get_db()
    cursor = db.execute(
        "DELETE FROM recurring_incomes WHERE id = ? AND user_id = ?",
        (income_id, current_user_id()),
    )
    if cursor.rowcount == 0:
        abort(404)
    db.commit()
    flash("ลบรายการรายรับประจำแล้ว รายการที่บันทึกไปก่อนหน้ายังคงอยู่", "success")
    return redirect(url_for("finance", month=request.form.get("month_filter") or date.today().strftime("%Y-%m")))


@app.post("/finance/category-budgets")
@login_required
def create_category_budget():
    category = sanitize_text(request.form.get("category_name", "")).strip()
    month = request.form.get("month_filter", "")
    try:
        monthly_limit = float(request.form.get("monthly_limit", ""))
    except (TypeError, ValueError):
        monthly_limit = math.nan
    if not category or len(category) > 60 or not valid_month(month):
        return finance_form_error("กรุณาระบุหมวดหมู่และเดือนที่ถูกต้อง")
    if not math.isfinite(monthly_limit) or not 0 <= monthly_limit <= 1_000_000_000:
        return finance_form_error("วงเงินต้องเป็นจำนวนเงินตั้งแต่ 0 บาทขึ้นไป")
    db = get_db()
    user_id = current_user_id()
    db.execute(
        "INSERT INTO category_budgets (user_id, category_name, monthly_limit, month_year) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(user_id, category_name, month_year) "
        "DO UPDATE SET monthly_limit = excluded.monthly_limit",
        (user_id, category, monthly_limit, month),
    )
    sync_category_budget_settlements(db, user_id)
    db.commit()
    flash("บันทึกงบประมาณรายหมวดหมู่แล้ว", "success")
    return redirect(url_for("finance", month=month))


@app.post("/finance/category-budgets/<int:budget_id>/update")
@login_required
def update_category_budget(budget_id):
    category = sanitize_text(request.form.get("category_name", "")).strip()
    month = request.form.get("month_filter", "")
    try:
        monthly_limit = float(request.form.get("monthly_limit", ""))
    except (TypeError, ValueError):
        monthly_limit = math.nan
    if not category or len(category) > 60 or not valid_month(month):
        return finance_form_error("กรุณาระบุหมวดหมู่และเดือนที่ถูกต้อง")
    if not math.isfinite(monthly_limit) or not 0 <= monthly_limit <= 1_000_000_000:
        return finance_form_error("วงเงินต้องเป็นจำนวนเงินตั้งแต่ 0 บาทขึ้นไป")
    db = get_db()
    user_id = current_user_id()
    duplicate = db.execute(
        "SELECT 1 FROM category_budgets WHERE user_id = ? AND category_name = ? "
        "AND month_year = ? AND id != ?",
        (user_id, category, month, budget_id),
    ).fetchone()
    if duplicate:
        flash("มีงบหมวดหมู่นี้ในเดือนดังกล่าวแล้ว", "error")
        return redirect(url_for("finance", month=month))
    cursor = db.execute(
        "UPDATE category_budgets SET category_name = ?, monthly_limit = ?, month_year = ? "
        "WHERE id = ? AND user_id = ?",
        (category, monthly_limit, month, budget_id, user_id),
    )
    if cursor.rowcount == 0:
        abort(404)
    sync_category_budget_settlements(db, user_id)
    db.commit()
    flash("แก้ไขงบประมาณรายหมวดหมู่แล้ว", "success")
    return redirect(url_for("finance", month=month))


@app.post("/finance/category-budgets/<int:budget_id>/delete")
@login_required
def delete_category_budget(budget_id):
    db = get_db()
    user_id = current_user_id()
    budget = db.execute(
        "SELECT month_year FROM category_budgets WHERE id = ? AND user_id = ?",
        (budget_id, user_id),
    ).fetchone()
    if budget is None:
        abort(404)
    db.execute(
        "DELETE FROM transactions WHERE id = (SELECT transaction_id FROM category_budget_settlements "
        "WHERE budget_id = ?) AND user_id = ?",
        (budget_id, user_id),
    )
    db.execute("DELETE FROM category_budgets WHERE id = ? AND user_id = ?", (budget_id, user_id))
    db.commit()
    flash("ลบงบประมาณรายหมวดหมู่แล้ว", "success")
    return redirect(url_for("finance", month=budget["month_year"]))
    
@app.post("/finance/category-budgets/<int:budget_id>/expenses")
@login_required
def create_category_budget_expense(budget_id):
    user_id = current_user_id()
    amount = parse_finance_amount(request.form.get("amount"))
    expense_date = request.form.get("expense_date", "")
    note = sanitize_text(request.form.get("note", "")).strip()
    if amount is None or amount > 1_000_000_000:
        return finance_form_error("กรุณาระบุค่าใช้จ่ายเป็นจำนวนเงินมากกว่า 0 บาท")
    if not valid_date(expense_date):
        return finance_form_error("วันที่รายการไม่ถูกต้อง")
    if len(note) > 500:
        return finance_form_error("บันทึกต้องไม่เกิน 500 ตัวอักษร")
    db = get_db()
    budget = db.execute(
        "SELECT category_name FROM category_budgets WHERE id = ? AND user_id = ?",
        (budget_id, user_id),
    ).fetchone()
    if budget is None:
        abort(404)
    db.execute(
        "INSERT INTO transactions (user_id, type, amount, category, note, created_at) "
        "VALUES (?, 'expense', ?, ?, ?, ?)",
        (user_id, amount, budget["category_name"], note, f"{expense_date} 12:00:00"),
    )
    sync_category_budget_settlements(db, user_id)
    db.commit()
    flash(f"บันทึกรายจ่ายหมวด{budget['category_name']}แล้ว", "success")
    return redirect(url_for("finance", month=expense_date[:7]))


@app.post("/finance/reset/<dataset>")
@login_required
def reset_finance_data(dataset):
    reset_actions = {
        "transactions": ("DELETE FROM transactions WHERE user_id = ?", "รายการรายรับ-รายจ่าย"),
        "daily-expenses": ("DELETE FROM daily_budget_expenses WHERE user_id = ?", "ประวัติใช้จ่ายงบรายวัน"),
        "daily-budget": (None, "การตั้งค่างบรายวัน"),
        "savings-goals": ("DELETE FROM savings_goals WHERE user_id = ?", "เป้าหมายการออม"),
    }
    action = reset_actions.get(dataset)
    if action is None:
        abort(404)

    db = get_db()
    user_id = current_user_id()
    statement, label = action
    if dataset == "daily-budget":
        db.execute("DELETE FROM daily_budget_history WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM daily_budget_settings WHERE user_id = ?", (user_id,))
    else:
        db.execute(statement, (user_id,))
    db.commit()
    flash(f"รีเซ็ต{label}แล้ว", "success")
    return redirect(url_for("finance", month=request.form.get("month_filter") or date.today().strftime("%Y-%m")))


@app.post("/finance/transactions")
@login_required
def create_finance_transaction():
    user_id = current_user_id()
    transaction_type = request.form.get("type", "")
    amount = parse_finance_amount(request.form.get("amount"))
    category = sanitize_text(request.form.get("category", "")).strip()
    note = sanitize_text(request.form.get("note", "")).strip()
    transaction_date = request.form.get("transaction_date", "")
    if transaction_type not in {"income", "expense"} or amount is None:
        return finance_form_error("กรุณาระบุประเภทรายการและจำนวนเงินที่ถูกต้อง")
    if not category or len(category) > 60 or len(note) > 500:
        return finance_form_error("หมวดหมู่ต้องมี 1-60 ตัวอักษร และบันทึกต้องไม่เกิน 500 ตัวอักษร")
    if not valid_date(transaction_date):
        return finance_form_error("วันที่รายการไม่ถูกต้อง")
    transaction_timestamp = f"{transaction_date} 12:00:00"
    db = get_db()
    db.execute(
        "INSERT INTO transactions (user_id, type, amount, category, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, transaction_type, amount, category, note, transaction_timestamp),
    )
    sync_category_budget_settlements(db, user_id)
    db.commit()
    flash("บันทึกรายการแล้ว", "success")
    return redirect(url_for("finance", month=transaction_date[:7]))


@app.post("/api/chat-assistant")
@login_required
def chat_assistant():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "รูปแบบข้อความไม่ถูกต้อง"}), 400
    message = sanitize_text(payload.get("message", "")).strip()
    if not message or len(message) > 500:
        return jsonify({"error": "พิมพ์ข้อความไม่เกิน 500 ตัวอักษร"}), 400

    parsed = parse_chat_transaction(message)
    if parsed is None:
        return jsonify({
            "logged": False,
            "reply": "ยังไม่แน่ใจว่าเป็นรายรับหรือรายจ่าย ลองพิมพ์ให้ชัดขึ้น เช่น ‘กินข้าว 40 บาท’ หรือ ‘พ่อโอนให้ 300 บาท’",
        })

    user_id = current_user_id()
    transaction_date = date.today().isoformat()
    db = get_db()
    cursor = db.execute(
        "INSERT INTO transactions (user_id, type, amount, category, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, parsed["type"], parsed["amount"], parsed["category"], message[:500], f"{transaction_date} 12:00:00"),
    )
    sync_category_budget_settlements(db, user_id)
    db.commit()
    kind = "รายรับ" if parsed["type"] == "income" else "รายจ่าย"
    reply = f"บันทึก{kind} {parsed['amount']:,.2f} บาท หมวด{parsed['category']} เรียบร้อยแล้ว"
    return jsonify({"logged": True, "reply": reply, "transactionId": cursor.lastrowid, **parsed})


@app.post("/finance/transactions/<int:transaction_id>/update")
@login_required
def update_finance_transaction(transaction_id):
    user_id = current_user_id()
    db = get_db()
    transaction = db.execute(
        "SELECT type, amount, category, note, date(created_at) AS transaction_date, "
        "EXISTS (SELECT 1 FROM category_budget_settlements s WHERE s.transaction_id = transactions.id) "
        "AS is_category_budget_return, EXISTS (SELECT 1 FROM savings_allocation_events e "
        "WHERE e.transaction_id = transactions.id) AS is_savings_withdrawal "
        "FROM transactions WHERE id = ? AND user_id = ?",
        (transaction_id, user_id),
    ).fetchone()
    if transaction is None:
        abort(404)
    if transaction["is_savings_withdrawal"]:
        return finance_form_error("รายการถอนเงินออมแก้ไขไม่ได้ เพื่อรักษาประวัติยอดเงินออม")
    if transaction["is_category_budget_return"]:
        return finance_form_error("รายการเงินคืนอัตโนมัติแก้ไขไม่ได้")
    transaction_type = request.form.get("type", "")
    amount = parse_finance_amount(request.form.get("amount"))
    category = sanitize_text(request.form.get("category", "")).strip()
    note = sanitize_text(request.form.get("note", "")).strip()
    transaction_date = request.form.get("transaction_date", "")
    if transaction_type not in {"income", "expense"} or amount is None or not valid_date(transaction_date):
        return finance_form_error("ข้อมูลรายการไม่ถูกต้อง")
    if not category or len(category) > 60 or len(note) > 500:
        return finance_form_error("หมวดหมู่ต้องมี 1-60 ตัวอักษร และบันทึกต้องไม่เกิน 500 ตัวอักษร")
    transaction_timestamp = f"{transaction_date} 12:00:00"
    db.execute(
        "UPDATE transactions SET type = ?, amount = ?, category = ?, note = ?, created_at = ? "
        "WHERE id = ? AND user_id = ?",
        (transaction_type, amount, category, note, transaction_timestamp, transaction_id, user_id),
    )
    sync_category_budget_settlements(db, user_id)
    db.commit()
    flash("แก้ไขรายการแล้ว", "success")
    return redirect(url_for("finance", month=transaction_date[:7]))


@app.post("/finance/transactions/<int:transaction_id>/delete")
@login_required
def delete_finance_transaction(transaction_id):
    db = get_db()
    user_id = current_user_id()
    is_budget_return = db.execute(
        "SELECT 1 FROM category_budget_settlements s JOIN transactions t ON t.id = s.transaction_id "
        "WHERE t.id = ? AND t.user_id = ?",
        (transaction_id, user_id),
    ).fetchone()
    if is_budget_return:
        abort(404)
    is_savings_withdrawal = db.execute(
        "SELECT 1 FROM savings_allocation_events e JOIN transactions t ON t.id = e.transaction_id "
        "WHERE t.id = ? AND t.user_id = ?",
        (transaction_id, user_id),
    ).fetchone()
    if is_savings_withdrawal:
        abort(404)
    cursor = db.execute(
        "DELETE FROM transactions WHERE id = ? AND user_id = ?",
        (transaction_id, user_id),
    )
    sync_category_budget_settlements(db, user_id)
    db.commit()
    if cursor.rowcount == 0:
        abort(404)
    flash("ลบรายการแล้ว", "success")
    return redirect(url_for("finance", month=request.form.get("month_filter") or date.today().strftime("%Y-%m")))


@app.post("/finance/savings-goals")
@login_required
def create_savings_goal():
    user_id = current_user_id()
    title = sanitize_text(request.form.get("title", "")).strip()
    target_amount = parse_finance_amount(request.form.get("target_amount"))
    deadline = request.form.get("deadline", "")
    if not title or len(title) > 100 or target_amount is None:
        return finance_form_error("กรุณาตรวจสอบชื่อเป้าหมายและจำนวนเงิน")
    if deadline and not valid_date(deadline):
        return finance_form_error("กำหนดวันเป้าหมายไม่ถูกต้อง")
    db = get_db()
    db.execute(
        "INSERT INTO savings_goals "
        "(user_id, title, target_amount, current_amount, deadline, goal_name, current_allocated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, title, target_amount, 0, deadline or None, title, 0),
    )
    db.commit()
    flash("เพิ่มเป้าหมายการออมแล้ว", "success")
    return redirect(url_for("finance"))


@app.post("/finance/savings-goals/<int:goal_id>/update")
@login_required
def update_savings_goal(goal_id):
    user_id = current_user_id()
    title = sanitize_text(request.form.get("title", "")).strip()
    target_amount = parse_finance_amount(request.form.get("target_amount"))
    deadline = request.form.get("deadline", "")
    if not title or len(title) > 100 or target_amount is None:
        return finance_form_error("กรุณาตรวจสอบชื่อเป้าหมายและจำนวนเงิน")
    if deadline and not valid_date(deadline):
        return finance_form_error("กำหนดวันเป้าหมายไม่ถูกต้อง")
    db = get_db()
    cursor = db.execute(
        "UPDATE savings_goals SET title = ?, target_amount = ?, deadline = ?, goal_name = ? "
        "WHERE id = ? AND user_id = ?",
        (title, target_amount, deadline or None, title, goal_id, user_id),
    )
    db.commit()
    if cursor.rowcount == 0:
        abort(404)
    flash("แก้ไขเป้าหมายการออมแล้ว", "success")
    return redirect(url_for("finance"))


@app.post("/finance/savings-goals/<int:goal_id>/delete")
@login_required
def delete_savings_goal(goal_id):
    db = get_db()
    cursor = db.execute(
        "DELETE FROM savings_goals WHERE id = ? AND user_id = ?",
        (goal_id, current_user_id()),
    )
    db.commit()
    if cursor.rowcount == 0:
        abort(404)
    flash("ลบเป้าหมายการออมแล้ว", "success")
    return redirect(url_for("finance"))


@app.post("/finance/savings-goals/<int:goal_id>/allocate")
@login_required
def allocate_savings_to_goal(goal_id):
    user_id = current_user_id()
    amount = parse_finance_amount(request.form.get("amount"))
    if amount is None or amount > 1_000_000_000:
        return finance_form_error("กรุณาระบุจำนวนเงินจัดสรรที่มากกว่า 0 บาท")
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    goal = db.execute(
        "SELECT id FROM savings_goals WHERE id = ? AND user_id = ?", (goal_id, user_id)
    ).fetchone()
    if goal is None:
        db.rollback()
        abort(404)
    _, _, unallocated = savings_balances(db, user_id)
    if amount > unallocated:
        db.rollback()
        return finance_form_error("จำนวนเงินเกินยอดเงินออมสะสมที่ยังไม่จัดสรร")
    db.execute(
        "UPDATE savings_goals SET current_amount = current_amount + ?, "
        "current_allocated = current_allocated + ? WHERE id = ? AND user_id = ?",
        (amount, amount, goal_id, user_id),
    )
    db.execute(
        "INSERT INTO savings_allocation_events (user_id, goal_id, event_type, amount, note) "
        "VALUES (?, ?, 'allocate', ?, 'จัดสรรเงินออมเข้าเป้าหมาย')",
        (user_id, goal_id, amount),
    )
    db.commit()
    flash("จัดสรรเงินออมเข้าเป้าหมายแล้ว", "success")
    return redirect(url_for("finance", month=request.form.get("month_filter") or date.today().strftime("%Y-%m")))


@app.post("/finance/savings-goals/<int:goal_id>/withdraw")
@login_required
def withdraw_savings_from_goal(goal_id):
    user_id = current_user_id()
    amount = parse_finance_amount(request.form.get("amount"))
    withdrawal_type = request.form.get("withdrawal_type", "")
    if amount is None or amount > 1_000_000_000 or withdrawal_type not in {"return", "spend"}:
        return finance_form_error("กรุณาตรวจสอบจำนวนเงินและประเภทการถอน")
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    goal = db.execute(
        "SELECT goal_name, current_allocated FROM savings_goals WHERE id = ? AND user_id = ?",
        (goal_id, user_id),
    ).fetchone()
    if goal is None:
        db.rollback()
        abort(404)
    if amount > goal["current_allocated"]:
        db.rollback()
        return finance_form_error("จำนวนเงินถอนเกินยอดที่จัดสรรไว้ในเป้าหมายนี้")
    lifetime_total, _, _ = savings_balances(db, user_id)
    if withdrawal_type == "spend" and amount > lifetime_total:
        db.rollback()
        return finance_form_error("ยอดเงินออมสะสมรวมไม่เพียงพอสำหรับการถอนใช้จ่าย")
    db.execute(
        "UPDATE savings_goals SET current_amount = current_amount - ?, "
        "current_allocated = current_allocated - ? WHERE id = ? AND user_id = ?",
        (amount, amount, goal_id, user_id),
    )
    transaction_id = None
    if withdrawal_type == "spend":
        note = f"ถอนจากเป้าหมาย: {goal['goal_name']}"
        cursor = db.execute(
            "INSERT INTO transactions (user_id, type, amount, category, note, created_at) "
            "VALUES (?, 'expense', ?, 'ถอนเงินออม', ?, ?)",
            (user_id, amount, note, f"{date.today().isoformat()} 12:00:00"),
        )
        transaction_id = cursor.lastrowid
    db.execute(
        "INSERT INTO savings_allocation_events "
        "(user_id, goal_id, event_type, amount, note, transaction_id) VALUES (?, ?, ?, ?, ?, ?)",
        (
            user_id, goal_id, withdrawal_type, amount,
            "ถอนกลับเข้าคลังเงินออม" if withdrawal_type == "return" else "ถอนเงินออมไปใช้จ่าย",
            transaction_id,
        ),
    )
    db.commit()
    flash("คืนเงินเข้าคลังเงินออมแล้ว" if withdrawal_type == "return" else "บันทึกการถอนเงินออมเป็นรายจ่ายแล้ว", "success")
    return redirect(url_for("finance", month=date.today().strftime("%Y-%m")))


@app.post("/finance/savings/withdraw")
@login_required
def withdraw_lifetime_savings():
    user_id = current_user_id()
    amount = parse_finance_amount(request.form.get("amount"))
    withdrawal_type = request.form.get("withdrawal_type", "")
    note = sanitize_text(request.form.get("note", "")).strip()
    if amount is None or amount > 1_000_000_000:
        return finance_form_error("กรุณาระบุจำนวนเงินถอนที่มากกว่า 0 บาท")
    if withdrawal_type not in {"expense", "transfer_income"}:
        return finance_form_error("กรุณาเลือกรูปแบบการถอนเงิน")
    if len(note) > 500:
        return finance_form_error("เหตุผลการถอนต้องไม่เกิน 500 ตัวอักษร")

    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    _, _, unallocated = savings_balances(db, user_id)
    if amount > unallocated:
        db.rollback()
        return finance_form_error("จำนวนเงินถอนเกินยอดเงินออมสะสมที่พร้อมจัดสรร")

    is_expense = withdrawal_type == "expense"
    transaction_type = "expense" if is_expense else "income"
    category = "ถอนเงินออมสะสม" if is_expense else "ดึงจากเงินออมสะสม"
    transaction_note = note or ("ถอนเงินออมสะสมไปใช้จ่าย" if is_expense else "โอนเงินออมสะสมกลับมาใช้หมุนเวียน")
    cursor = db.execute(
        "INSERT INTO transactions (user_id, type, amount, category, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, transaction_type, amount, category, transaction_note, f"{date.today().isoformat()} 12:00:00"),
    )
    db.execute(
        "INSERT INTO savings_allocation_events "
        "(user_id, event_type, amount, note, transaction_id) VALUES (?, 'spend', ?, ?, ?)",
        (user_id, amount, transaction_note, cursor.lastrowid),
    )
    db.commit()
    flash("บันทึกการถอนเงินสะสมแล้ว", "success")
    return redirect(url_for("finance", month=date.today().strftime("%Y-%m")))


@app.get("/api/dashboard")
@login_required
def dashboard():
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        return error("เดือนต้องอยู่ในรูปแบบ YYYY-MM")
    try:
        year, month_number = map(int, month.split("-"))
        days_in_month = calendar.monthrange(year, month_number)[1]
    except (ValueError, calendar.IllegalMonthError):
        return error("เดือนที่ระบุไม่ถูกต้อง")
    today = date.today()
    current_month = today.strftime("%Y-%m")
    days_elapsed = days_in_month if month < current_month else today.day if month == current_month else 0

    db = get_db()
    user_id = current_user_id()
    goals = [dict(row) for row in db.execute("SELECT id, name, color FROM goals WHERE user_id = ? AND is_active = 1 ORDER BY id", (user_id,))]
    month_start = f"{month}-01"
    month_end = f"{month}-{days_in_month:02d}"
    completions = db.execute(
        "SELECT goal_id, completed_on, done FROM completions "
        "WHERE user_id = ? AND completed_on BETWEEN ? AND ?",
        (user_id, month_start, month_end),
    ).fetchall()
    completion_map = {(row["completed_on"], row["goal_id"]): bool(row["done"]) for row in completions}
    metrics = {
        row["metric_date"]: {"mood": row["mood"], "energy": row["energy"], "note": row["note"]}
        for row in db.execute(
            "SELECT metric_date, mood, energy, note FROM daily_metrics WHERE user_id = ? AND metric_date BETWEEN ? AND ?",
            (user_id, month_start, month_end),
        )
    }
    monthly_progress = {
        str(row["goal_id"]): {"percent": row["percent"], "note": row["note"]}
        for row in db.execute(
            "SELECT goal_id, percent, note FROM monthly_goal_progress WHERE user_id = ? AND month = ?",
            (user_id, month),
        )
    }

    days = []
    for day_number in range(1, days_in_month + 1):
        day = date(year, month_number, day_number)
        day_string = day.isoformat()
        done_count = sum(completion_map.get((day_string, goal["id"]), False) for goal in goals)
        days.append(
            {
                "date": day_string,
                "day": day_number,
                "weekday": day.strftime("%a"),
                "done": {str(goal["id"]): completion_map.get((day_string, goal["id"]), False) for goal in goals},
                "completed": done_count,
                "rate": round(done_count / len(goals) * 100) if goals else 0,
                "metrics": metrics.get(day_string),
            }
        )

    goal_stats = []
    for goal in goals:
        total_done = sum(completion_map.get((day["date"], goal["id"]), False) for day in days[:days_elapsed])
        rate = round(total_done / days_elapsed * 100) if days_elapsed else 0
        goal_stats.append({**goal, "completed": total_done, "rate": rate})

    return jsonify(
        {
            "month": month,
            "today": today.isoformat(),
            "daysInMonth": days_in_month,
            "daysElapsed": days_elapsed,
            "goals": goals,
            "goalStats": goal_stats,
            "days": days,
            "metrics": metrics,
            "monthlyProgress": monthly_progress,
        }
    )


@app.post("/api/goals")
@login_required
def create_goal():
    payload = request.get_json(silent=True) or {}
    name = sanitize_text(payload.get("name", ""))
    color = payload.get("color", "#75d6b1")
    if not name or len(name) > 60:
        return error("ชื่อเป้าหมายต้องมีความยาว 1-60 ตัวอักษร")
    if not isinstance(color, str) or not COLOR_PATTERN.fullmatch(color):
        return error("รูปแบบสีไม่ถูกต้อง")
    db = get_db()
    cursor = db.execute("INSERT INTO goals (user_id, name, color) VALUES (?, ?, ?)", (current_user_id(), name, color))
    db.commit()
    return jsonify({"id": cursor.lastrowid, "name": name, "color": color}), 201


@app.patch("/api/goals/<int:goal_id>")
@app.put("/api/goals/<int:goal_id>")
@login_required
def update_goal(goal_id):
    payload = request.get_json(silent=True) or {}
    db = get_db()
    goal = db.execute("SELECT id, name, color FROM goals WHERE id = ? AND user_id = ? AND is_active = 1", (goal_id, current_user_id())).fetchone()
    if goal is None:
        return error("ไม่พบเป้าหมายนี้", 404)
    name = sanitize_text(payload.get("name", goal["name"]))
    color = payload.get("color", goal["color"])
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
        return error("ชื่อเป้าหมายต้องมีความยาว 1-60 ตัวอักษร")
    if not isinstance(color, str) or not COLOR_PATTERN.fullmatch(color):
        return error("รูปแบบสีไม่ถูกต้อง")
    db.execute("UPDATE goals SET name = ?, color = ? WHERE id = ? AND user_id = ?", (name.strip(), color, goal_id, current_user_id()))
    db.commit()
    return jsonify({"id": goal_id, "name": name.strip(), "color": color})


@app.put("/api/goals/<int:goal_id>/monthly-progress")
@login_required
def set_monthly_goal_progress(goal_id):
    payload = request.get_json(silent=True) or {}
    month = payload.get("month")
    percent = payload.get("percent")
    note = payload.get("note", "")
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
        return error("เดือนต้องอยู่ในรูปแบบ YYYY-MM")
    try:
        year, month_number = map(int, month.split("-"))
        calendar.monthrange(year, month_number)
    except (ValueError, calendar.IllegalMonthError):
        return error("เดือนที่ระบุไม่ถูกต้อง")
    if type(percent) is not int or not 0 <= percent <= 100:
        return error("ความคืบหน้าต้องเป็นจำนวนเต็มตั้งแต่ 0 ถึง 100")
    if not isinstance(note, str) or len(note) > 2000:
        return error("บันทึกต้องมีความยาวไม่เกิน 2,000 ตัวอักษร")

    db = get_db()
    user_id = current_user_id()
    if db.execute("SELECT 1 FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id)).fetchone() is None:
        return error("ไม่พบเป้าหมายนี้", 404)
    clean_note = sanitize_text(note)
    db.execute(
        "INSERT INTO monthly_goal_progress (goal_id, user_id, month, percent, note) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(goal_id, month) DO UPDATE SET percent = excluded.percent, "
        "note = excluded.note, updated_at = CURRENT_TIMESTAMP",
        (goal_id, user_id, month, percent, clean_note),
    )
    db.commit()
    return jsonify({"goalId": goal_id, "month": month, "percent": percent, "note": clean_note})


@app.delete("/api/goals/<int:goal_id>")
@login_required
def delete_goal(goal_id):
    db = get_db()
    cursor = db.execute("UPDATE goals SET is_active = 0 WHERE id = ? AND user_id = ? AND is_active = 1", (goal_id, current_user_id()))
    db.commit()
    if cursor.rowcount == 0:
        return error("ไม่พบเป้าหมายนี้", 404)
    return jsonify({"deleted": True, "historyPreserved": True})


@app.put("/api/completions")
@login_required
def set_completion():
    payload = request.get_json(silent=True) or {}
    goal_id = payload.get("goalId")
    completed_on = payload.get("date")
    done = payload.get("done")
    if type(goal_id) is not int or not valid_date(completed_on) or type(done) is not bool:
        return error("ข้อมูลเช็กลิสต์ไม่ถูกต้อง")
    db = get_db()
    user_id = current_user_id()
    if db.execute("SELECT 1 FROM goals WHERE id = ? AND user_id = ? AND is_active = 1", (goal_id, user_id)).fetchone() is None:
        return error("ไม่พบเป้าหมายนี้", 404)
    db.execute(
        "INSERT INTO completions (goal_id, user_id, completed_on, done) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(goal_id, completed_on) DO UPDATE SET done = excluded.done, user_id = excluded.user_id",
        (goal_id, user_id, completed_on, int(done)),
    )
    db.commit()
    return jsonify({"goalId": goal_id, "date": completed_on, "done": done})


@app.put("/api/metrics")
@login_required
def set_metrics():
    payload = request.get_json(silent=True) or {}
    metric_date = payload.get("date")
    mood = payload.get("mood")
    energy = payload.get("energy")
    note = payload.get("note", "")
    if not valid_date(metric_date) or not valid_score(mood) or not valid_score(energy):
        return error("วันที่และคะแนนต้องถูกต้อง (คะแนน 1-5)")
    if not isinstance(note, str) or len(note) > 2000:
        return error("บันทึกต้องมีความยาวไม่เกิน 2,000 ตัวอักษร")
    db = get_db()
    db.execute(
        "INSERT INTO daily_metrics (user_id, metric_date, mood, energy, note) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, metric_date) DO UPDATE SET mood = excluded.mood, "
        "energy = excluded.energy, note = excluded.note",
        (current_user_id(), metric_date, mood, energy, sanitize_text(note)),
    )
    db.commit()
    return jsonify({"date": metric_date, "mood": mood, "energy": energy, "note": sanitize_text(note)})


with app.app_context():
    init_db()
    backup_databases()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as address_probe:
            address_probe.connect(("192.0.2.1", 80))
            lan_ip = address_probe.getsockname()[0]
    except OSError:
        lan_ip = "127.0.0.1"

    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        print(f"LAN access: http://{lan_ip}:{port}/finance")
    debug_mode = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)

    