import calendar
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import bleach
from flask import Blueprint, current_app, jsonify, render_template, request, session
from auth import current_user_id


BASE_DIR = Path(__file__).resolve().parent
STUDENT_DATABASE = Path(os.environ.get("STUDENT_DB", BASE_DIR / "student.db"))
STUDENT_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STUDENT_MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")
STUDENT_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
ASSIGNMENT_STATUSES = {"todo", "in_progress", "done"}
PRIORITIES = {"high", "medium", "low"}
EVENT_TYPES = {"exam", "assignment", "holiday", "other"}
WEEKDAYS = set(range(5))

student = Blueprint("student", __name__)


@contextmanager
def student_db():
    STUDENT_DATABASE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(STUDENT_DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def init_student_db():
    with student_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                email TEXT NOT NULL,
                password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subjects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
                color TEXT NOT NULL DEFAULT '#38a794',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subject TEXT NOT NULL CHECK(length(subject) BETWEEN 1 AND 80),
                subject_id INTEGER REFERENCES subjects(id) ON DELETE RESTRICT,
                title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 180),
                due_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo', 'in_progress', 'done')),
                priority TEXT NOT NULL DEFAULT 'medium' CHECK(priority IN ('high', 'medium', 'low')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_student_assignments_due ON assignments(due_date);
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 4),
                subject TEXT NOT NULL CHECK(length(subject) BETWEEN 1 AND 80),
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                room TEXT NOT NULL DEFAULT '' CHECK(length(room) <= 80)
            );
            CREATE INDEX IF NOT EXISTS idx_student_schedules_day ON schedules(weekday, start_time);
            CREATE TABLE IF NOT EXISTS subject_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
                subject TEXT NOT NULL CHECK(length(subject) BETWEEN 1 AND 80),
                title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 180),
                content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 20000),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS grades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subject TEXT NOT NULL CHECK(length(subject) BETWEEN 1 AND 80),
                assessment TEXT NOT NULL CHECK(length(assessment) BETWEEN 1 AND 160),
                score REAL NOT NULL CHECK(score >= 0),
                max_score REAL NOT NULL CHECK(max_score > 0),
                credits REAL NOT NULL DEFAULT 1 CHECK(credits > 0 AND credits <= 12),
                graded_on TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS exams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subject TEXT NOT NULL CHECK(length(subject) BETWEEN 1 AND 80),
                title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 160),
                exam_date TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '' CHECK(length(note) <= 1000)
            );
            CREATE TABLE IF NOT EXISTS student_calendar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 160),
                event_date TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'other' CHECK(event_type IN ('exam', 'assignment', 'holiday', 'other')),
                note TEXT NOT NULL DEFAULT '' CHECK(length(note) <= 1000)
            );
            CREATE INDEX IF NOT EXISTS idx_student_calendar_date ON student_calendar(event_date);
            CREATE TABLE IF NOT EXISTS student_settings (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL
            );
            """
        )
        for table in ("subjects", "assignments", "schedules", "subject_notes", "grades", "exams", "student_calendar", "student_settings"):
            columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
            if "user_id" not in columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS subjects_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
                color TEXT NOT NULL DEFAULT '#38a794',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, name)
            );
            """
        )
        subject_indexes = db.execute("PRAGMA index_list(subjects)").fetchall()
        has_global_name_unique = any(
            index["unique"] and [column["name"] for column in db.execute(f"PRAGMA index_info({index['name']})")] == ["name"]
            for index in subject_indexes
        )
        if has_global_name_unique:
            db.execute("PRAGMA foreign_keys = OFF")
            db.executescript(
                """
                INSERT INTO subjects_new (id, user_id, name, color, created_at)
                SELECT id, user_id, name, color, created_at FROM subjects;
                DROP TABLE subjects;
                ALTER TABLE subjects_new RENAME TO subjects;
                """
            )
            db.execute("PRAGMA foreign_keys = ON")
        else:
            db.execute("DROP TABLE subjects_new")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_subjects_user_name ON subjects(user_id, name)")
        settings_columns = {row["name"] for row in db.execute("PRAGMA table_info(student_settings)")}
        settings_pk = [row["name"] for row in sorted(db.execute("PRAGMA table_info(student_settings)"), key=lambda row: row["pk"]) if row["pk"]]
        if settings_pk != ["user_id", "setting_key"]:
            db.execute("PRAGMA foreign_keys = OFF")
            db.executescript(
                """
                CREATE TABLE student_settings_new (
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    setting_key TEXT NOT NULL,
                    setting_value TEXT NOT NULL,
                    PRIMARY KEY (user_id, setting_key)
                );
                INSERT INTO student_settings_new (user_id, setting_key, setting_value)
                SELECT user_id, setting_key, setting_value FROM student_settings;
                DROP TABLE student_settings;
                ALTER TABLE student_settings_new RENAME TO student_settings;
                """
            )
            db.execute("PRAGMA foreign_keys = ON")
        assignment_columns = {row["name"] for row in db.execute("PRAGMA table_info(assignments)")}
        if "subject_id" not in assignment_columns:
            db.execute("ALTER TABLE assignments ADD COLUMN subject_id INTEGER REFERENCES subjects(id) ON DELETE RESTRICT")
        legacy_subjects = db.execute(
            "SELECT DISTINCT user_id, CASE WHEN trim(subject) = '' THEN 'ไม่ระบุวิชา' "
            "ELSE trim(subject) END AS name FROM assignments WHERE subject_id IS NULL"
        ).fetchall()
        for legacy in legacy_subjects:
            db.execute("INSERT OR IGNORE INTO subjects (user_id, name) VALUES (?, ?)", (legacy["user_id"], legacy["name"]))
        db.execute(
            "UPDATE assignments SET subject_id = (SELECT id FROM subjects "
            "WHERE subjects.user_id IS assignments.user_id AND subjects.name = CASE WHEN trim(assignments.subject) = '' THEN 'ไม่ระบุวิชา' "
            "ELSE trim(assignments.subject) END) WHERE subject_id IS NULL"
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_student_assignments_subject ON assignments(subject_id)")
        for table in ("subjects", "assignments", "schedules", "subject_notes", "grades", "exams", "student_calendar", "student_settings"):
            db.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_user ON {table}(user_id)")
        db.commit()


def create_student_user(user_id, username, email, password_hash):
    with student_db() as db:
        db.execute(
            "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, username, email, password_hash),
        )
        db.commit()


def assign_legacy_data(user_id):
    with student_db() as db:
        counts = {}
        db.execute(
            "DELETE FROM student_settings WHERE user_id IS NULL AND rowid NOT IN "
            "(SELECT MIN(rowid) FROM student_settings WHERE user_id IS NULL GROUP BY setting_key)"
        )
        for table in ("subjects", "assignments", "schedules", "subject_notes", "grades", "exams", "student_calendar", "student_settings"):
            cursor = db.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (user_id,))
            counts[table] = cursor.rowcount
        db.commit()
    return counts


def api_error(message, status=400):
    return jsonify({"error": message}), status


def valid_student_date(value):
    if not isinstance(value, str) or not STUDENT_DATE_PATTERN.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def valid_student_month(value):
    if not isinstance(value, str) or not STUDENT_MONTH_PATTERN.fullmatch(value):
        return False
    try:
        year, month = map(int, value.split("-"))
        calendar.monthrange(year, month)
    except (ValueError, calendar.IllegalMonthError):
        return False
    return True


def clean_text(payload, key, maximum, required=True, default=""):
    value = payload.get(key, default)
    if not isinstance(value, str):
        return None
    value = bleach.clean(value, tags=[], attributes={}, protocols=[], strip=True).strip()
    if (required and not value) or len(value) > maximum:
        return None
    return value


def grade_points(score, max_score):
    percent = score / max_score * 100
    if percent >= 80:
        return 4.0
    if percent >= 75:
        return 3.5
    if percent >= 70:
        return 3.0
    if percent >= 65:
        return 2.5
    if percent >= 60:
        return 2.0
    if percent >= 55:
        return 1.5
    if percent >= 50:
        return 1.0
    return 0.0


def validate_assignment(payload):
    subject_id = payload.get("subjectId")
    title = clean_text(payload, "title", 180)
    due_date = payload.get("dueDate")
    status = payload.get("status", "todo")
    priority = payload.get("priority", "medium")
    if type(subject_id) is not int or not title or not valid_student_date(due_date):
        return None, "กรุณากรอกวิชา งาน และกำหนดส่งให้ถูกต้อง"
    if status not in ASSIGNMENT_STATUSES or priority not in PRIORITIES:
        return None, "สถานะหรือลำดับความสำคัญไม่ถูกต้อง"
    return {"subject_id": subject_id, "title": title, "due_date": due_date, "status": status, "priority": priority}, None


def assignment_dict(row):
    item = dict(row)
    item["dueDate"] = item.pop("due_date")
    item["subjectId"] = item.pop("subject_id")
    return item


def event_dict(row):
    item = dict(row)
    item["eventDate"] = item.pop("event_date")
    item["eventType"] = item.pop("event_type")
    return item


@student.get("/student")
def student_dashboard():
    return render_template("student.html", csrf_token=session.get("csrf_token", ""))


@student.get("/api/student/dashboard")
def student_dashboard_data():
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    if not valid_student_month(month):
        return api_error("เดือนต้องอยู่ในรูปแบบ YYYY-MM ที่ถูกต้อง")
    year, month_number = map(int, month.split("-"))
    month_start = f"{month}-01"
    month_end = f"{month}-{calendar.monthrange(year, month_number)[1]:02d}"
    user_id = current_user_id()

    with student_db() as db:
        assignments = [assignment_dict(row) for row in db.execute("SELECT assignments.id, assignments.subject_id, subjects.name AS subject, assignments.title, assignments.due_date, assignments.status, assignments.priority, assignments.created_at FROM assignments LEFT JOIN subjects ON subjects.id = assignments.subject_id AND subjects.user_id = assignments.user_id WHERE assignments.user_id = ? ORDER BY assignments.due_date, assignments.id", (user_id,))]
        schedules = [dict(row) for row in db.execute("SELECT * FROM schedules WHERE user_id = ? ORDER BY weekday, start_time, id", (user_id,))]
        subjects = [dict(row) for row in db.execute("SELECT * FROM subjects WHERE user_id = ? ORDER BY name COLLATE NOCASE", (user_id,))]
        notes = [dict(row) for row in db.execute("SELECT id, subject_id AS subjectId, subject, title, content, updated_at AS updatedAt FROM subject_notes WHERE user_id = ? ORDER BY updated_at DESC, id DESC", (user_id,))]
        grades = [dict(row) for row in db.execute("SELECT * FROM grades WHERE user_id = ? ORDER BY graded_on DESC, id DESC", (user_id,))]
        exams = [dict(row) for row in db.execute("SELECT id, subject, title, exam_date AS examDate, note FROM exams WHERE user_id = ? ORDER BY exam_date, id", (user_id,))]
        events = [event_dict(row) for row in db.execute("SELECT * FROM student_calendar WHERE user_id = ? AND event_date BETWEEN ? AND ? ORDER BY event_date, id", (user_id, month_start, month_end))]
        target_row = db.execute("SELECT setting_value FROM student_settings WHERE user_id = ? AND setting_key = 'target_gpa'", (user_id,)).fetchone()

    completed_count = sum(item["status"] == "done" for item in assignments)
    assignment_count = len(assignments)
    weighted_points = 0.0
    total_credits = 0.0
    for grade in grades:
        points = grade_points(grade["score"], grade["max_score"])
        weighted_points += points * grade["credits"]
        total_credits += grade["credits"]
    gpa = round(weighted_points / total_credits, 2) if total_credits else 0.0
    target_gpa = float(target_row["setting_value"]) if target_row else 3.5

    return jsonify({
        "month": month,
        "today": date.today().isoformat(),
        "daysInMonth": calendar.monthrange(year, month_number)[1],
        "assignments": assignments,
        "assignmentSummary": {"total": assignment_count, "completed": completed_count, "progress": round(completed_count / assignment_count * 100) if assignment_count else 0},
        "schedules": schedules,
        "subjects": subjects,
        "notes": notes,
        "grades": grades,
        "gradeSummary": {"gpa": gpa, "targetGpa": target_gpa, "progress": min(100, round(gpa / target_gpa * 100)) if target_gpa else 0, "credits": total_credits},
        "exams": exams,
        "events": events,
    })


@student.post("/api/student/assignments")
def create_assignment():
    payload = request.get_json(silent=True) or {}
    values, problem = validate_assignment(payload)
    if problem:
        return api_error(problem)
    try:
        with student_db() as db:
            user_id = current_user_id()
            subject = db.execute("SELECT name FROM subjects WHERE id = ? AND user_id = ?", (values["subject_id"], user_id)).fetchone()
            if subject is None:
                return api_error("กรุณาเลือกรายวิชาที่มีอยู่แล้ว", 404)
            cursor = db.execute("INSERT INTO assignments (user_id, subject_id, subject, title, due_date, status, priority) VALUES (:user_id, :subject_id, :subject, :title, :due_date, :status, :priority)", {**values, "user_id": user_id, "subject": subject["name"]})
            row = db.execute("SELECT assignments.id, assignments.subject_id, subjects.name AS subject, assignments.title, assignments.due_date, assignments.status, assignments.priority, assignments.created_at FROM assignments LEFT JOIN subjects ON subjects.id = assignments.subject_id AND subjects.user_id = assignments.user_id WHERE assignments.id = ? AND assignments.user_id = ?", (cursor.lastrowid, user_id)).fetchone()
            db.commit()
    except sqlite3.Error as exc:
        current_app.logger.exception("Failed to create student assignment")
        return jsonify({"error": "บันทึกงานไม่สำเร็จ กรุณาตรวจสอบฐานข้อมูล", "detail": str(exc)}), 500
    return jsonify(assignment_dict(row)), 201


@student.patch("/api/student/assignments/<int:item_id>")
@student.put("/api/student/assignments/<int:item_id>")
def update_assignment(item_id):
    payload = request.get_json(silent=True) or {}
    try:
        with student_db() as db:
            user_id = current_user_id()
            row = db.execute("SELECT * FROM assignments WHERE id = ? AND user_id = ?", (item_id, user_id)).fetchone()
            if row is None:
                return api_error("ไม่พบงานนี้", 404)
            values = assignment_dict(row)
            merged = {"subjectId": payload.get("subjectId", values["subjectId"]), "title": payload.get("title", values["title"]), "dueDate": payload.get("dueDate", values["dueDate"]), "status": payload.get("status", values["status"]), "priority": payload.get("priority", values["priority"])}
            clean_values, problem = validate_assignment(merged)
            if problem:
                return api_error(problem)
            subject = db.execute("SELECT name FROM subjects WHERE id = ? AND user_id = ?", (clean_values["subject_id"], user_id)).fetchone()
            if subject is None:
                return api_error("กรุณาเลือกรายวิชาที่มีอยู่แล้ว", 404)
            db.execute("UPDATE assignments SET subject_id = :subject_id, subject = :subject, title = :title, due_date = :due_date, status = :status, priority = :priority WHERE id = :id AND user_id = :user_id", {**clean_values, "subject": subject["name"], "id": item_id, "user_id": user_id})
            db.commit()
            result = db.execute("SELECT assignments.id, assignments.subject_id, subjects.name AS subject, assignments.title, assignments.due_date, assignments.status, assignments.priority, assignments.created_at FROM assignments LEFT JOIN subjects ON subjects.id = assignments.subject_id AND subjects.user_id = assignments.user_id WHERE assignments.id = ? AND assignments.user_id = ?", (item_id, user_id)).fetchone()
    except sqlite3.Error as exc:
        current_app.logger.exception("Failed to update student assignment %s", item_id)
        return jsonify({"error": "แก้ไขงานไม่สำเร็จ กรุณาตรวจสอบฐานข้อมูล", "detail": str(exc)}), 500
    return jsonify(assignment_dict(result))


@student.delete("/api/student/assignments/<int:item_id>")
def delete_assignment(item_id):
    with student_db() as db:
        cursor = db.execute("DELETE FROM assignments WHERE id = ? AND user_id = ?", (item_id, current_user_id()))
        db.commit()
    if not cursor.rowcount:
        return api_error("ไม่พบงานนี้", 404)
    return jsonify({"deleted": True})


@student.post("/api/student/schedules")
def create_schedule():
    payload = request.get_json(silent=True) or {}
    weekday = payload.get("weekday")
    subject_name = clean_text(payload, "subject", 80)
    start_time = payload.get("startTime")
    end_time = payload.get("endTime")
    room = clean_text(payload, "room", 80, required=False)
    if type(weekday) is not int or weekday not in WEEKDAYS or not subject_name or not isinstance(start_time, str) or not re.fullmatch(r"\d{2}:\d{2}", start_time) or not isinstance(end_time, str) or not re.fullmatch(r"\d{2}:\d{2}", end_time) or start_time >= end_time or room is None:
        return api_error("กรุณากรอกวัน วิชา และช่วงเวลาให้ถูกต้อง")
    with student_db() as db:
        user_id = current_user_id()
        cursor = db.execute("INSERT INTO schedules (user_id, weekday, subject, start_time, end_time, room) VALUES (?, ?, ?, ?, ?, ?)", (user_id, weekday, subject_name, start_time, end_time, room))
        row = db.execute("SELECT * FROM schedules WHERE id = ? AND user_id = ?", (cursor.lastrowid, user_id)).fetchone()
        db.commit()
    return jsonify(dict(row)), 201


@student.patch("/api/student/schedules/<int:item_id>")
@student.put("/api/student/schedules/<int:item_id>")
def update_schedule(item_id):
    payload = request.get_json(silent=True) or {}
    with student_db() as db:
        user_id = current_user_id()
        row = db.execute("SELECT * FROM schedules WHERE id = ? AND user_id = ?", (item_id, user_id)).fetchone()
        if row is None:
            return api_error("ไม่พบคาบเรียนนี้", 404)
        current = dict(row)
        merged = {"weekday": payload.get("weekday", current["weekday"]), "subject": payload.get("subject", current["subject"]), "startTime": payload.get("startTime", current["start_time"]), "endTime": payload.get("endTime", current["end_time"]), "room": payload.get("room", current["room"])}
        subject_name = clean_text(merged, "subject", 80)
        room = clean_text(merged, "room", 80, required=False)
        if type(merged["weekday"]) is not int or merged["weekday"] not in WEEKDAYS or not subject_name or not isinstance(merged["startTime"], str) or not re.fullmatch(r"\d{2}:\d{2}", merged["startTime"]) or not isinstance(merged["endTime"], str) or not re.fullmatch(r"\d{2}:\d{2}", merged["endTime"]) or merged["startTime"] >= merged["endTime"] or room is None:
            return api_error("ข้อมูลตารางเรียนไม่ถูกต้อง")
        db.execute("UPDATE schedules SET weekday=?, subject=?, start_time=?, end_time=?, room=? WHERE id=? AND user_id=?", (merged["weekday"], subject_name, merged["startTime"], merged["endTime"], room, item_id, user_id))
        db.commit()
        updated = dict(db.execute("SELECT * FROM schedules WHERE id = ? AND user_id = ?", (item_id, user_id)).fetchone())
    return jsonify(updated)


@student.delete("/api/student/schedules/<int:item_id>")
def delete_schedule(item_id):
    with student_db() as db:
        cursor = db.execute("DELETE FROM schedules WHERE id = ? AND user_id = ?", (item_id, current_user_id()))
        db.commit()
    if not cursor.rowcount:
        return api_error("ไม่พบคาบเรียนนี้", 404)
    return jsonify({"deleted": True})


@student.post("/api/student/subjects")
def create_subject():
    payload = request.get_json(silent=True) or {}
    name = clean_text(payload, "name", 80)
    color = payload.get("color", "#38a794")
    if not name:
        return api_error("ชื่อวิชาต้องมีความยาว 1-80 ตัวอักษร")
    if not isinstance(color, str) or not STUDENT_COLOR_PATTERN.fullmatch(color):
        return api_error("สีวิชาไม่ถูกต้อง")
    try:
        with student_db() as db:
            user_id = current_user_id()
            cursor = db.execute("INSERT INTO subjects (user_id, name, color) VALUES (?, ?, ?)", (user_id, name, color))
            db.commit()
            row = dict(db.execute("SELECT * FROM subjects WHERE id = ? AND user_id = ?", (cursor.lastrowid, user_id)).fetchone())
    except sqlite3.IntegrityError:
        return api_error("มีวิชานี้อยู่แล้ว")
    return jsonify(row), 201


@student.patch("/api/student/subjects/<int:subject_id>")
@student.put("/api/student/subjects/<int:subject_id>")
def update_subject(subject_id):
    payload = request.get_json(silent=True) or {}
    with student_db() as db:
        user_id = current_user_id()
        current = db.execute("SELECT * FROM subjects WHERE id = ? AND user_id = ?", (subject_id, user_id)).fetchone()
        if current is None:
            return api_error("ไม่พบวิชานี้", 404)
        name = clean_text(payload, "name", 80, default=current["name"])
        color = payload.get("color", current["color"])
        if not name or not isinstance(color, str) or not STUDENT_COLOR_PATTERN.fullmatch(color):
            return api_error("ชื่อหรือสีวิชาไม่ถูกต้อง")
        try:
            db.execute("UPDATE subjects SET name=?, color=? WHERE id=? AND user_id=?", (name, color, subject_id, user_id))
        except sqlite3.IntegrityError:
            return api_error("มีวิชานี้อยู่แล้ว")
        db.execute("UPDATE subject_notes SET subject=? WHERE subject_id=? AND user_id=?", (name, subject_id, user_id))
        db.execute("UPDATE assignments SET subject=? WHERE subject_id=? AND user_id=?", (name, subject_id, user_id))
        db.execute("UPDATE schedules SET subject=? WHERE subject=? AND user_id=?", (name, current["name"], user_id))
        db.execute("UPDATE grades SET subject=? WHERE subject=? AND user_id=?", (name, current["name"], user_id))
        db.execute("UPDATE exams SET subject=? WHERE subject=? AND user_id=?", (name, current["name"], user_id))
        db.commit()
        row = dict(db.execute("SELECT * FROM subjects WHERE id=? AND user_id=?", (subject_id, user_id)).fetchone())
    return jsonify(row)


@student.delete("/api/student/subjects/<int:subject_id>")
def delete_subject(subject_id):
    try:
        with student_db() as db:
            cursor = db.execute("DELETE FROM subjects WHERE id = ? AND user_id = ?", (subject_id, current_user_id()))
            db.commit()
    except sqlite3.IntegrityError:
        return api_error("ยังลบวิชานี้ไม่ได้ เพราะมีงานที่ผูกกับวิชาอยู่ กรุณาย้ายหรือลบงานก่อน", 409)
    if not cursor.rowcount:
        return api_error("ไม่พบวิชานี้", 404)
    return jsonify({"deleted": True})


@student.post("/api/student/notes")
def create_note():
    payload = request.get_json(silent=True) or {}
    subject = clean_text(payload, "subject", 80)
    title = clean_text(payload, "title", 180)
    content = clean_text(payload, "content", 20000)
    subject_id = payload.get("subjectId")
    if not subject or not title or not content or (subject_id is not None and type(subject_id) is not int):
        return api_error("กรุณาเลือกวิชาและกรอกหัวข้อกับเนื้อหา")
    with student_db() as db:
        user_id = current_user_id()
        if subject_id is not None and db.execute("SELECT 1 FROM subjects WHERE id = ? AND user_id = ?", (subject_id, user_id)).fetchone() is None:
            return api_error("ไม่พบวิชาที่เลือก", 404)
        cursor = db.execute("INSERT INTO subject_notes (user_id, subject_id, subject, title, content) VALUES (?, ?, ?, ?, ?)", (user_id, subject_id, subject, title, content))
        db.commit()
        row = dict(db.execute("SELECT id, subject_id AS subjectId, subject, title, content, updated_at AS updatedAt FROM subject_notes WHERE id = ? AND user_id = ?", (cursor.lastrowid, user_id)).fetchone())
    return jsonify(row), 201


@student.patch("/api/student/notes/<int:note_id>")
@student.put("/api/student/notes/<int:note_id>")
def update_note(note_id):
    payload = request.get_json(silent=True) or {}
    with student_db() as db:
        user_id = current_user_id()
        current = db.execute("SELECT * FROM subject_notes WHERE id=? AND user_id=?", (note_id, user_id)).fetchone()
        if current is None:
            return api_error("ไม่พบบันทึกนี้", 404)
        title = clean_text(payload, "title", 180, default=current["title"])
        content = clean_text(payload, "content", 20000, default=current["content"])
        subject_id = payload.get("subjectId", current["subject_id"])
        if type(subject_id) is not int or db.execute("SELECT 1 FROM subjects WHERE id=? AND user_id=?", (subject_id, user_id)).fetchone() is None:
            return api_error("กรุณาเลือกวิชาที่มีอยู่")
        if not title or not content:
            return api_error("หัวข้อและเนื้อหาต้องไม่ว่าง")
        subject = db.execute("SELECT name FROM subjects WHERE id=? AND user_id=?", (subject_id, user_id)).fetchone()["name"]
        cursor = db.execute("UPDATE subject_notes SET subject_id=?, subject=?, title=?, content=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?", (subject_id, subject, title, content, note_id, user_id))
        db.commit()
        row = db.execute("SELECT id, subject_id AS subjectId, subject, title, content, updated_at AS updatedAt FROM subject_notes WHERE id=? AND user_id=?", (note_id, user_id)).fetchone()
    if not cursor.rowcount:
        return api_error("ไม่พบบันทึกนี้", 404)
    return jsonify(dict(row))


@student.delete("/api/student/notes/<int:note_id>")
def delete_note(note_id):
    with student_db() as db:
        cursor = db.execute("DELETE FROM subject_notes WHERE id=? AND user_id=?", (note_id, current_user_id()))
        db.commit()
    if not cursor.rowcount:
        return api_error("ไม่พบบันทึกนี้", 404)
    return jsonify({"deleted": True})


@student.post("/api/student/grades")
def create_grade():
    payload = request.get_json(silent=True) or {}
    subject = clean_text(payload, "subject", 80)
    assessment = clean_text(payload, "assessment", 160)
    score = payload.get("score")
    max_score = payload.get("maxScore")
    credits = payload.get("credits", 1)
    graded_on = payload.get("gradedOn")
    if not subject or not assessment or type(score) not in (int, float) or type(max_score) not in (int, float) or type(credits) not in (int, float) or score < 0 or max_score <= 0 or score > max_score or not 0 < credits <= 12 or not valid_student_date(graded_on):
        return api_error("กรุณาตรวจคะแนน คะแนนเต็ม หน่วยกิต และวันที่")
    with student_db() as db:
        user_id = current_user_id()
        cursor = db.execute("INSERT INTO grades (user_id, subject, assessment, score, max_score, credits, graded_on) VALUES (?, ?, ?, ?, ?, ?, ?)", (user_id, subject, assessment, score, max_score, credits, graded_on))
        row = dict(db.execute("SELECT * FROM grades WHERE id=? AND user_id=?", (cursor.lastrowid, user_id)).fetchone())
        db.commit()
    row["gradePoint"] = grade_points(row["score"], row["max_score"])
    return jsonify(row), 201


@student.patch("/api/student/grades/<int:grade_id>")
@student.put("/api/student/grades/<int:grade_id>")
def update_grade(grade_id):
    payload = request.get_json(silent=True) or {}
    with student_db() as db:
        user_id = current_user_id()
        current = db.execute("SELECT * FROM grades WHERE id=? AND user_id=?", (grade_id, user_id)).fetchone()
        if current is None:
            return api_error("ไม่พบคะแนนนี้", 404)
        subject = clean_text(payload, "subject", 80, default=current["subject"])
        assessment = clean_text(payload, "assessment", 160, default=current["assessment"])
        score = payload.get("score", current["score"])
        max_score = payload.get("maxScore", current["max_score"])
        credits = payload.get("credits", current["credits"])
        graded_on = payload.get("gradedOn", current["graded_on"])
        if not subject or not assessment or type(score) not in (int, float) or type(max_score) not in (int, float) or type(credits) not in (int, float) or score < 0 or max_score <= 0 or score > max_score or not 0 < credits <= 12 or not valid_student_date(graded_on):
            return api_error("กรุณาตรวจคะแนน คะแนนเต็ม หน่วยกิต และวันที่")
        db.execute("UPDATE grades SET subject=?, assessment=?, score=?, max_score=?, credits=?, graded_on=? WHERE id=? AND user_id=?", (subject, assessment, score, max_score, credits, graded_on, grade_id, user_id))
        db.commit()
        row = dict(db.execute("SELECT * FROM grades WHERE id=? AND user_id=?", (grade_id, user_id)).fetchone())
    row["gradePoint"] = grade_points(row["score"], row["max_score"])
    return jsonify(row)


@student.delete("/api/student/grades/<int:grade_id>")
def delete_grade(grade_id):
    with student_db() as db:
        cursor = db.execute("DELETE FROM grades WHERE id=? AND user_id=?", (grade_id, current_user_id()))
        db.commit()
    if not cursor.rowcount:
        return api_error("ไม่พบคะแนนนี้", 404)
    return jsonify({"deleted": True})


@student.put("/api/student/settings/target-gpa")
def set_target_gpa():
    payload = request.get_json(silent=True) or {}
    target = payload.get("targetGpa")
    if type(target) not in (int, float) or not 0.1 <= target <= 4.0:
        return api_error("เกรดเป้าหมายต้องอยู่ระหว่าง 0.1 ถึง 4.0")
    with student_db() as db:
        db.execute("INSERT INTO student_settings (user_id, setting_key, setting_value) VALUES (?, 'target_gpa', ?) ON CONFLICT(user_id, setting_key) DO UPDATE SET setting_value=excluded.setting_value", (current_user_id(), str(target)))
        db.commit()
    return jsonify({"targetGpa": target})


@student.post("/api/student/exams")
def create_exam():
    payload = request.get_json(silent=True) or {}
    subject = clean_text(payload, "subject", 80)
    title = clean_text(payload, "title", 160)
    exam_date = payload.get("examDate")
    note = clean_text(payload, "note", 1000, required=False)
    if not subject or not title or not valid_student_date(exam_date) or note is None:
        return api_error("กรุณากรอกวิชา ชื่อสอบ วันที่ และโน้ตให้ถูกต้อง")
    with student_db() as db:
        user_id = current_user_id()
        cursor = db.execute("INSERT INTO exams (user_id, subject, title, exam_date, note) VALUES (?, ?, ?, ?, ?)", (user_id, subject, title, exam_date, note))
        row = dict(db.execute("SELECT id, subject, title, exam_date AS examDate, note FROM exams WHERE id=? AND user_id=?", (cursor.lastrowid, user_id)).fetchone())
        db.commit()
    return jsonify(row), 201


@student.patch("/api/student/exams/<int:exam_id>")
@student.put("/api/student/exams/<int:exam_id>")
def update_exam(exam_id):
    payload = request.get_json(silent=True) or {}
    with student_db() as db:
        user_id = current_user_id()
        current = db.execute("SELECT * FROM exams WHERE id=? AND user_id=?", (exam_id, user_id)).fetchone()
        if current is None:
            return api_error("ไม่พบวันสอบนี้", 404)
        subject = clean_text(payload, "subject", 80, default=current["subject"])
        title = clean_text(payload, "title", 160, default=current["title"])
        exam_date = payload.get("examDate", current["exam_date"])
        note = clean_text(payload, "note", 1000, required=False, default=current["note"])
        if not subject or not title or not valid_student_date(exam_date) or note is None:
            return api_error("กรุณากรอกวิชา ชื่อสอบ วันที่ และโน้ตให้ถูกต้อง")
        db.execute("UPDATE exams SET subject=?, title=?, exam_date=?, note=? WHERE id=? AND user_id=?", (subject, title, exam_date, note, exam_id, user_id))
        db.commit()
        row = dict(db.execute("SELECT id, subject, title, exam_date AS examDate, note FROM exams WHERE id=? AND user_id=?", (exam_id, user_id)).fetchone())
    return jsonify(row)


@student.delete("/api/student/exams/<int:exam_id>")
def delete_exam(exam_id):
    with student_db() as db:
        cursor = db.execute("DELETE FROM exams WHERE id=? AND user_id=?", (exam_id, current_user_id()))
        db.commit()
    if not cursor.rowcount:
        return api_error("ไม่พบวันสอบนี้", 404)
    return jsonify({"deleted": True})


@student.post("/api/student/calendar")
def create_calendar_event():
    payload = request.get_json(silent=True) or {}
    title = clean_text(payload, "title", 160)
    event_date = payload.get("eventDate")
    event_type = payload.get("eventType", "other")
    note = clean_text(payload, "note", 1000, required=False)
    if not title or not valid_student_date(event_date) or event_type not in EVENT_TYPES or note is None:
        return api_error("กรุณากรอกชื่อ วันที่ และประเภทกิจกรรมให้ถูกต้อง")
    with student_db() as db:
        user_id = current_user_id()
        cursor = db.execute("INSERT INTO student_calendar (user_id, title, event_date, event_type, note) VALUES (?, ?, ?, ?, ?)", (user_id, title, event_date, event_type, note))
        row = event_dict(db.execute("SELECT * FROM student_calendar WHERE id=? AND user_id=?", (cursor.lastrowid, user_id)).fetchone())
        db.commit()
    return jsonify(row), 201


@student.patch("/api/student/calendar/<int:event_id>")
@student.put("/api/student/calendar/<int:event_id>")
def update_calendar_event(event_id):
    payload = request.get_json(silent=True) or {}
    with student_db() as db:
        user_id = current_user_id()
        current = db.execute("SELECT * FROM student_calendar WHERE id=? AND user_id=?", (event_id, user_id)).fetchone()
        if current is None:
            return api_error("ไม่พบกิจกรรมนี้", 404)
        title = clean_text(payload, "title", 160, default=current["title"])
        event_date = payload.get("eventDate", current["event_date"])
        event_type = payload.get("eventType", current["event_type"])
        note = clean_text(payload, "note", 1000, required=False, default=current["note"])
        if not title or not valid_student_date(event_date) or event_type not in EVENT_TYPES or note is None:
            return api_error("กรุณากรอกชื่อ วันที่ และประเภทกิจกรรมให้ถูกต้อง")
        db.execute("UPDATE student_calendar SET title=?, event_date=?, event_type=?, note=? WHERE id=? AND user_id=?", (title, event_date, event_type, note, event_id, user_id))
        db.commit()
        row = event_dict(db.execute("SELECT * FROM student_calendar WHERE id=? AND user_id=?", (event_id, user_id)).fetchone())
    return jsonify(row)


@student.delete("/api/student/calendar/<int:event_id>")
def delete_calendar_event(event_id):
    with student_db() as db:
        cursor = db.execute("DELETE FROM student_calendar WHERE id=? AND user_id=?", (event_id, current_user_id()))
        db.commit()
    if not cursor.rowcount:
        return api_error("ไม่พบกิจกรรมนี้", 404)
    return jsonify({"deleted": True})


def register_student_mode(app):
    init_student_db()
    app.register_blueprint(student)
