import os
import re
import sqlite3
import tempfile
import threading
import time
import json
from functools import wraps
from importlib import import_module

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

try:
    psycopg2 = import_module("psycopg2")
    RealDictCursor = import_module("psycopg2.extras").RealDictCursor
except Exception:  # pragma: no cover - optional dependency for postgres deployment
    psycopg2 = None
    RealDictCursor = None

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")
database_url = os.environ.get("DATABASE_URL", "").strip()
if database_url.startswith("postgres://"):
    database_url = "postgresql://" + database_url[len("postgres://") :]
app.config["DATABASE_URL"] = database_url
app.config["USE_POSTGRES"] = bool(database_url)


def _resolve_database_path():
    explicit_path = os.environ.get("DATABASE_PATH", "").strip()
    if explicit_path:
        return explicit_path

    # Cloudtype Volume path (default mount) fallback.
    volume_path = os.environ.get("VOLUME_PATH", "/data").strip() or "/data"
    if os.path.isdir(volume_path):
        return os.path.join(volume_path, "portfolio.sqlite3")

    return os.path.join(app.root_path, "portfolio.sqlite3")


app.config["DATABASE"] = _resolve_database_path() if not app.config["USE_POSTGRES"] else ""
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["CORS_ALLOW_ORIGIN"] = os.environ.get("CORS_ALLOW_ORIGIN", "*")
app.jinja_env.auto_reload = True


def _compute_asset_version():
    fixed_version = os.environ.get("ASSET_VERSION")
    if fixed_version:
        return fixed_version

    static_candidates = (
        os.path.join(app.root_path, "static", "css", "styles.css"),
        os.path.join(app.root_path, "static", "js", "main.js"),
    )
    mtimes = []
    for path in static_candidates:
        try:
            mtimes.append(int(os.path.getmtime(path)))
        except OSError:
            continue
    return str(max(mtimes) if mtimes else 1)


ASSET_VERSION = _compute_asset_version()
_db_init_lock = threading.Lock()
_db_initialized = False

LICENSE_MENUS = [
    ("qna", "Q&A"),
    ("assignments", "Assignments"),
    ("scores", "Scores"),
    ("notices", "Notices"),
    ("student_accounts", "Student Accounts"),
]

TRACKED_MENUS = {"qna", "assignments", "scores", "notices", "student_accounts"}
TRACKED_MENU_KEYS = tuple(sorted(TRACKED_MENUS))

PORTFOLIO_DEFAULT_PROFILE = {
    "name": "Jeong Seo-bin",
    "age": 18,
    "education": "Cheon-cheon Highschool",
    "certificates": "Engineer Information Processing, SQLD",
    "email": "portfolio@example.com",
    "phone": "010-1234-5678",
    "intro": "I build practical web products with HTML, CSS, JavaScript, and Python.",
    "location": "Seoul, KR",
    "github": "github.com/your-id",
}

PORTFOLIO_DEFAULT_SKILLS = [
    "HTML5 / CSS3 / JavaScript",
    "Python (Flask, Pandas)",
    "SQLite / MySQL",
    "Git / GitHub / Notion",
]

PORTFOLIO_DEFAULT_PROJECTS = [
    {
        "title": "Tutoring Management Platform",
        "summary": "Integrated question, assignment, score, and notice management service.",
    },
    {
        "title": "Automated Data Reporting",
        "summary": "Collect and clean data with Python, then generate visual reports.",
    },
    {
        "title": "Personal Portfolio Site",
        "summary": "Responsive UI with project archive and contact hub.",
    },
]

PORTFOLIO_DEFAULT_ABOUT_NOTE = "실무 문제를 구조화하고, 빠르게 동작하는 결과물로 전환하는 데 집중합니다."
PORTFOLIO_DEFAULT_SKILLS_NOTE = "주요 언어는 HTML, CSS, JavaScript, Python이며 필요 시 다른 언어/도구를 활용합니다."
PORTFOLIO_DEFAULT_CONTACT_NOTE = "협업, 과외, 프로젝트 제안은 아래로 연락 주세요."

SCHEMA_SQL_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('student', 'admin', 'super_admin')),
    full_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    age INTEGER,
    education TEXT,
    certificates TEXT,
    bio TEXT,
    approved INTEGER NOT NULL DEFAULT 1 CHECK (approved IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin_licenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL,
    menu_key TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 0 CHECK (is_enabled IN (0, 1)),
    UNIQUE (admin_id, menu_key),
    FOREIGN KEY (admin_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    is_public INTEGER NOT NULL DEFAULT 1 CHECK (is_public IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS question_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    admin_id INTEGER,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
    FOREIGN KEY (admin_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    due_date TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS assignment_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    content TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'in-progress',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (assignment_id, student_id),
    FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE,
    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    test_name TEXT NOT NULL,
    score REAL NOT NULL,
    max_score REAL NOT NULL,
    analysis TEXT,
    announced_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (announced_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    is_pinned INTEGER NOT NULL DEFAULT 0 CHECK (is_pinned IN (0, 1)),
    pinned_at TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_admin_id INTEGER,
    menu_key TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (actor_admin_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS portfolio_content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    about_note TEXT,
    skills_note TEXT,
    contact_note TEXT,
    github TEXT,
    location TEXT,
    skills_json TEXT NOT NULL,
    projects_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_role_approved ON users (role, approved);
CREATE INDEX IF NOT EXISTS idx_users_role_created_at ON users (role, created_at);
CREATE INDEX IF NOT EXISTS idx_questions_student_created_at ON questions (student_id, created_at);
CREATE INDEX IF NOT EXISTS idx_questions_public_created_at ON questions (is_public, created_at);
CREATE INDEX IF NOT EXISTS idx_question_answers_question_created_at ON question_answers (question_id, created_at);
CREATE INDEX IF NOT EXISTS idx_assignments_created_at ON assignments (created_at);
CREATE INDEX IF NOT EXISTS idx_assignment_submissions_student_updated_at ON assignment_submissions (student_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_assignment_submissions_assignment_updated_at ON assignment_submissions (assignment_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_scores_student_created_at ON scores (student_id, created_at);
CREATE INDEX IF NOT EXISTS idx_notices_pinned_created_at ON notices (is_pinned, pinned_at, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_menu_created_at ON audit_logs (menu_key, created_at);
CREATE INDEX IF NOT EXISTS idx_portfolio_content_updated_at ON portfolio_content (updated_at);
"""


def _sqlite_schema_to_postgres(schema_sql):
    schema = schema_sql
    schema = schema.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    schema = schema.replace("created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP", "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP")
    schema = schema.replace("updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP", "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP")
    schema = schema.replace("pinned_at TEXT,", "pinned_at TIMESTAMP,")
    return schema


SCHEMA_SQL_POSTGRES = _sqlite_schema_to_postgres(SCHEMA_SQL_SQLITE)
SCHEMA_SQL = SCHEMA_SQL_POSTGRES if app.config["USE_POSTGRES"] else SCHEMA_SQL_SQLITE


def _split_sql_script(sql_script):
    return [statement.strip() for statement in sql_script.split(";") if statement.strip()]


def _is_postgres_insert(query):
    return bool(re.match(r"^\s*INSERT\s+INTO\s+", query, flags=re.IGNORECASE))


def _adapt_query_for_postgres(query):
    translated = query
    used_insert_ignore = bool(re.match(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+", translated, flags=re.IGNORECASE))
    if used_insert_ignore:
        translated = re.sub(
            r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+",
            "INSERT INTO ",
            translated,
            flags=re.IGNORECASE,
        )
    translated = translated.replace("?", "%s")
    if used_insert_ignore:
        translated = translated.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    needs_returning_id = _is_postgres_insert(translated) and "RETURNING" not in translated.upper()
    if needs_returning_id:
        translated = translated.rstrip().rstrip(";") + " RETURNING id"

    return translated, needs_returning_id


class CursorResult:
    def __init__(self, cursor=None, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchall(self):
        if self._cursor is None:
            return []
        return self._cursor.fetchall()

    def fetchone(self):
        if self._cursor is None:
            return None
        return self._cursor.fetchone()

    def close(self):
        if self._cursor is not None:
            self._cursor.close()
            self._cursor = None


class PostgresConnectionWrapper:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, args=()):
        sql, needs_returning_id = _adapt_query_for_postgres(query)
        cursor = self.connection.cursor()
        cursor.execute(sql, args)
        if not needs_returning_id:
            return CursorResult(cursor=cursor)

        row = cursor.fetchone()
        lastrowid = None
        if row is not None:
            if isinstance(row, dict):
                lastrowid = row.get("id")
            elif isinstance(row, (list, tuple)) and row:
                lastrowid = row[0]
            elif hasattr(row, "get"):
                lastrowid = row.get("id")
        cursor.close()
        return CursorResult(lastrowid=lastrowid)

    def executescript(self, sql_script):
        cursor = self.connection.cursor()
        try:
            for statement in _split_sql_script(sql_script):
                cursor.execute(statement)
        finally:
            cursor.close()

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()


_db_operational_errors = [sqlite3.OperationalError]
if psycopg2 is not None:
    _db_operational_errors.append(psycopg2.OperationalError)
DB_OPERATIONAL_ERRORS = tuple(_db_operational_errors)


def get_db():
    if "db" not in g:
        if app.config["USE_POSTGRES"]:
            if psycopg2 is None:
                raise RuntimeError("DATABASE_URL is set but psycopg2 is not installed.")
            connection = psycopg2.connect(app.config["DATABASE_URL"], cursor_factory=RealDictCursor)
            connection.autocommit = False
            g.db = PostgresConnectionWrapper(connection)
        else:
            db_path = app.config["DATABASE"]
            try:
                parent_dir = os.path.dirname(os.path.abspath(db_path))
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                connection = sqlite3.connect(db_path, timeout=10)
            except (sqlite3.OperationalError, OSError):
                # Fallback for environments where project path may be read-only.
                fallback = os.path.join(tempfile.gettempdir(), "portfolio.sqlite3")
                app.config["DATABASE"] = fallback
                connection = sqlite3.connect(fallback, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            # Sandbox/Cloud-sync environments can fail SQLite journaling writes.
            try:
                connection.execute("PRAGMA journal_mode = OFF")
                connection.execute("PRAGMA synchronous = OFF")
            except sqlite3.OperationalError:
                pass
            connection.execute("PRAGMA foreign_keys = ON")
            g.db = connection
    return g.db


def query_db(query, args=(), one=False):
    cursor = get_db().execute(query, args)
    rows = cursor.fetchall()
    cursor.close()
    if one:
        return rows[0] if rows else None
    return rows


@app.teardown_appcontext
def close_db(_error):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA_SQL)
    db.commit()
    seed_defaults()


def seed_defaults():
    db = get_db()

    super_admin = query_db(
        "SELECT id FROM users WHERE role = 'super_admin' LIMIT 1",
        one=True,
    )
    if super_admin is None:
        db.execute(
            """
            INSERT INTO users (username, password_hash, role, full_name, email, phone, approved)
            VALUES (?, ?, 'super_admin', ?, ?, ?, 1)
            """,
            (
                "masteradmin",
                generate_password_hash("Master123!"),
                "Master Admin",
                "master@example.com",
                "010-0000-0000",
            ),
        )

    student = query_db(
        "SELECT id FROM users WHERE username = ? AND role = 'student'",
        ("student1",),
        one=True,
    )
    if student is None:
        db.execute(
            """
            INSERT INTO users (
                username, password_hash, role, full_name, email, phone, age, education, certificates, bio, approved
            )
            VALUES (?, ?, 'student', ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                "student1",
                generate_password_hash("Student123!"),
                "Student One",
                "student1@example.com",
                "010-1111-1111",
                20,
                "Computer Science",
                "Information Processing Engineer",
                "Focused on backend development and data analysis.",
            ),
        )

    notice_count = query_db("SELECT COUNT(*) AS cnt FROM notices", one=True)
    if notice_count["cnt"] == 0:
        db.execute(
            """
            INSERT INTO notices (title, content, is_pinned, pinned_at, created_by)
            VALUES (?, ?, 1, CURRENT_TIMESTAMP, (SELECT id FROM users WHERE role = 'super_admin' LIMIT 1))
            """,
            (
                "[Pinned] Class Guide",
                "Tutoring sessions are held twice a week. Please use the Q&A menu for questions.",
            ),
        )
        db.execute(
            """
            INSERT INTO notices (title, content, is_pinned, created_by)
            VALUES (?, ?, 0, (SELECT id FROM users WHERE role = 'super_admin' LIMIT 1))
            """,
            (
                "Sample Notice",
                "This project currently includes sample data.",
            ),
        )

    db.commit()

def ensure_db_initialized():
    global _db_initialized
    if _db_initialized:
        return

    with _db_init_lock:
        if _db_initialized:
            return

        last_error = None
        for _ in range(8):
            try:
                init_db()
                _db_initialized = True
                return
            except DB_OPERATIONAL_ERRORS as exc:
                lowered = str(exc).lower()
                if "locked" in lowered or "deadlock" in lowered or "serialize" in lowered:
                    last_error = exc
                    time.sleep(0.25)
                    continue
                raise

        if last_error is not None:
            raise last_error


def as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def ensure_admin_license_rows(admin_id):
    db = get_db()
    for menu_key, _label in LICENSE_MENUS:
        db.execute(
            """
            INSERT OR IGNORE INTO admin_licenses (admin_id, menu_key, is_enabled)
            VALUES (?, ?, 0)
            """,
            (admin_id, menu_key),
        )


def get_admin_license_map(admin_id):
    cache_key = f"admin_license_map_{admin_id}"
    cached = g.get(cache_key)
    if cached is not None:
        return cached

    rows = query_db(
        """
        SELECT menu_key, is_enabled
        FROM admin_licenses
        WHERE admin_id = ?
        """,
        (admin_id,),
    )
    license_map = {row["menu_key"]: row["is_enabled"] for row in rows}
    g[cache_key] = license_map
    return license_map


def has_license(admin_id, menu_key):
    license_map = get_admin_license_map(admin_id)
    return bool(license_map.get(menu_key, 0) == 1)


def get_student_user():
    if "student_user" in g:
        return g.student_user

    student_id = session.get("student_id")
    if student_id is None:
        g.student_user = None
        return None

    g.student_user = query_db(
        "SELECT * FROM users WHERE id = ? AND role = 'student'",
        (student_id,),
        one=True,
    )
    return g.student_user


def get_admin_user():
    if "admin_user" in g:
        return g.admin_user

    admin_id = session.get("admin_id")
    if admin_id is None:
        g.admin_user = None
        return None

    g.admin_user = query_db(
        "SELECT * FROM users WHERE id = ? AND role IN ('admin', 'super_admin')",
        (admin_id,),
        one=True,
    )
    return g.admin_user


def admin_can_access(menu_key):
    admin = get_admin_user()
    if admin is None:
        return False

    if admin["role"] == "super_admin":
        return True

    if admin["approved"] != 1:
        return False

    return has_license(admin["id"], menu_key)


def student_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if get_student_user() is None:
            flash("Student login is required.", "warning")
            return redirect(url_for("tutoring_login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(menu_key=None, super_admin_only=False):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            admin = get_admin_user()
            if admin is None:
                flash("Admin login is required.", "warning")
                return redirect(url_for("admin_login"))

            if super_admin_only and admin["role"] != "super_admin":
                flash("Super admin privileges are required.", "danger")
                return redirect(url_for("admin_home"))

            if menu_key is not None and admin["role"] != "super_admin":
                if admin["approved"] != 1:
                    flash("This account is pending approval by the master admin.", "warning")
                    return redirect(url_for("admin_home"))
                if not has_license(admin["id"], menu_key):
                    flash("You do not have license access for this menu.", "danger")
                    return redirect(url_for("admin_home"))

            return view(*args, **kwargs)

        return wrapped

    return decorator


def log_admin_action(menu_key, action_type, target_type, target_id, detail):
    admin = get_admin_user()
    if admin is None:
        return

    db = get_db()
    db.execute(
        """
        INSERT INTO audit_logs (actor_admin_id, menu_key, action_type, target_type, target_id, detail)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (admin["id"], menu_key, action_type, target_type, target_id, detail),
    )
    db.commit()


def fetch_questions_for_student(student_id):
    if student_id is None:
        questions = query_db(
            """
            SELECT q.*, u.full_name AS student_name, u.username AS student_username
            FROM questions q
            JOIN users u ON u.id = q.student_id
            WHERE q.is_public = 1
            ORDER BY q.created_at DESC
            """
        )
    else:
        questions = query_db(
            """
            SELECT q.*, u.full_name AS student_name, u.username AS student_username
            FROM questions q
            JOIN users u ON u.id = q.student_id
            WHERE q.is_public = 1 OR q.student_id = ?
            ORDER BY q.created_at DESC
            """,
            (student_id,),
        )

    question_ids = [question["id"] for question in questions]
    answers_by_question = {}

    if question_ids:
        placeholders = ",".join(["?"] * len(question_ids))
        answers = query_db(
            f"""
            SELECT a.*, u.full_name AS admin_name, u.username AS admin_username
            FROM question_answers a
            LEFT JOIN users u ON u.id = a.admin_id
            WHERE a.question_id IN ({placeholders})
            ORDER BY a.created_at ASC
            """,
            tuple(question_ids),
        )
        for answer in answers:
            answers_by_question.setdefault(answer["question_id"], []).append(answer)

    return questions, answers_by_question


def fetch_notices():
    pinned = query_db(
        """
        SELECT n.*, u.full_name AS admin_name
        FROM notices n
        LEFT JOIN users u ON u.id = n.created_by
        WHERE n.is_pinned = 1
        ORDER BY COALESCE(n.pinned_at, n.created_at) DESC
        """
    )

    regular = query_db(
        """
        SELECT n.*, u.full_name AS admin_name
        FROM notices n
        LEFT JOIN users u ON u.id = n.created_by
        WHERE n.is_pinned = 0
        ORDER BY n.created_at DESC
        """
    )

    return pinned, regular


def _normalize_string_list(items):
    normalized = []
    for item in items:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_projects(items):
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        summary = str(item.get("summary", "")).strip()
        if title and summary:
            normalized.append({"title": title, "summary": summary})
    return normalized


def _parse_portfolio_json(raw_json, fallback, normalize_fn):
    if not raw_json:
        return fallback

    try:
        parsed = json.loads(raw_json)
    except (TypeError, ValueError):
        return fallback

    if not isinstance(parsed, list):
        return fallback

    normalized = normalize_fn(parsed)
    return normalized if normalized else fallback


def ensure_portfolio_content_row():
    row = query_db(
        "SELECT * FROM portfolio_content ORDER BY id ASC LIMIT 1",
        one=True,
    )
    if row is not None:
        return row

    db = get_db()
    db.execute(
        """
        INSERT INTO portfolio_content (
            about_note,
            skills_note,
            contact_note,
            github,
            location,
            skills_json,
            projects_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            PORTFOLIO_DEFAULT_ABOUT_NOTE,
            PORTFOLIO_DEFAULT_SKILLS_NOTE,
            PORTFOLIO_DEFAULT_CONTACT_NOTE,
            PORTFOLIO_DEFAULT_PROFILE["github"],
            PORTFOLIO_DEFAULT_PROFILE["location"],
            json.dumps(PORTFOLIO_DEFAULT_SKILLS, ensure_ascii=False),
            json.dumps(PORTFOLIO_DEFAULT_PROJECTS, ensure_ascii=False),
        ),
    )
    db.commit()
    return query_db(
        "SELECT * FROM portfolio_content ORDER BY id ASC LIMIT 1",
        one=True,
    )


def build_portfolio_payload():
    profile = dict(PORTFOLIO_DEFAULT_PROFILE)
    about_note = PORTFOLIO_DEFAULT_ABOUT_NOTE
    skills_note = PORTFOLIO_DEFAULT_SKILLS_NOTE
    contact_note = PORTFOLIO_DEFAULT_CONTACT_NOTE
    skills = list(PORTFOLIO_DEFAULT_SKILLS)
    projects = [dict(project) for project in PORTFOLIO_DEFAULT_PROJECTS]

    content_row = ensure_portfolio_content_row()
    if content_row is not None:
        about_note_text = (content_row["about_note"] or "").strip()
        if about_note_text:
            about_note = about_note_text

        skills_note_text = (content_row["skills_note"] or "").strip()
        if skills_note_text:
            skills_note = skills_note_text

        contact_note_text = (content_row["contact_note"] or "").strip()
        if contact_note_text:
            contact_note = contact_note_text

        github = (content_row["github"] or "").strip()
        if github:
            profile["github"] = github

        location = (content_row["location"] or "").strip()
        if location:
            profile["location"] = location

        skills = _parse_portfolio_json(
            content_row["skills_json"],
            list(PORTFOLIO_DEFAULT_SKILLS),
            _normalize_string_list,
        )
        projects = _parse_portfolio_json(
            content_row["projects_json"],
            [dict(project) for project in PORTFOLIO_DEFAULT_PROJECTS],
            _normalize_projects,
        )

    owner = query_db(
        """
        SELECT full_name, age, education, certificates, email, phone, bio
        FROM users
        WHERE role = 'super_admin'
        ORDER BY id ASC
        LIMIT 1
        """,
        one=True,
    )

    if owner is not None:
        full_name = (owner["full_name"] or "").strip()
        if full_name:
            profile["name"] = full_name

        if owner["age"] is not None:
            profile["age"] = owner["age"]

        education = (owner["education"] or "").strip()
        if education:
            profile["education"] = education

        certificates = (owner["certificates"] or "").strip()
        if certificates:
            profile["certificates"] = certificates

        email = (owner["email"] or "").strip()
        if email:
            profile["email"] = email

        phone = (owner["phone"] or "").strip()
        if phone:
            profile["phone"] = phone

        intro = (owner["bio"] or "").strip()
        if intro:
            profile["intro"] = intro

    return {
        "profile": profile,
        "skills": skills,
        "projects": projects,
        "about_note": about_note,
        "skills_note": skills_note,
        "contact_note": contact_note,
    }


@app.context_processor
def inject_users():
    return {
        "student_user": get_student_user(),
        "admin_user": get_admin_user(),
        "license_menus": LICENSE_MENUS,
        "admin_can_access": admin_can_access,
        "asset_version": ASSET_VERSION,
    }


@app.before_request
def boot_db_once():
    ensure_db_initialized()


@app.after_request
def disable_static_cache(response):
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    if request.path.startswith("/api/"):
        allow_origin = app.config.get("CORS_ALLOW_ORIGIN", "*")
        response.headers["Access-Control-Allow-Origin"] = allow_origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


@app.route("/")
def index():
    return redirect(url_for("portfolio"))


@app.get("/healthz")
def healthz():
    return {"status": "ok"}, 200


@app.route("/api/<path:_path>", methods=["OPTIONS"])
def api_preflight(_path):
    return "", 204


@app.get("/api/healthz")
def api_healthz():
    return {
        "status": "ok",
        "service": "portfolio-backend",
        "timestamp": int(time.time()),
    }, 200


@app.get("/api/portfolio")
def api_portfolio():
    return build_portfolio_payload(), 200


def _serialize_notice_row(notice):
    return {
        "id": notice["id"],
        "title": notice["title"],
        "content": notice["content"],
        "is_pinned": bool(notice["is_pinned"]),
        "created_at": notice["created_at"],
        "updated_at": notice["updated_at"],
        "admin_name": notice["admin_name"] if notice["admin_name"] else "",
    }


@app.get("/api/notices/public")
def api_notices_public():
    pinned_notices, regular_notices = fetch_notices()
    return {
        "pinned": [_serialize_notice_row(n) for n in pinned_notices],
        "regular": [_serialize_notice_row(n) for n in regular_notices],
    }, 200


@app.route("/portfolio")
def portfolio():
    payload = build_portfolio_payload()
    return render_template(
        "portfolio.html",
        profile=payload["profile"],
        skills=payload["skills"],
        projects=payload["projects"],
        about_note=payload["about_note"],
        skills_note=payload["skills_note"],
        contact_note=payload["contact_note"],
    )


@app.route("/tutoring")
def tutoring_home():
    student = get_student_user()
    pinned_notices, regular_notices = fetch_notices()

    assignment_summary = None
    score_summary = None
    if student is not None:
        assignment_summary = query_db(
            """
            SELECT COUNT(*) AS total, COALESCE(AVG(progress), 0) AS avg_progress
            FROM assignment_submissions
            WHERE student_id = ?
            """,
            (student["id"],),
            one=True,
        )
        score_summary = query_db(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(AVG((score * 100.0) / NULLIF(max_score, 0)), 0) AS avg_rate
            FROM scores
            WHERE student_id = ?
            """,
            (student["id"],),
            one=True,
        )

    return render_template(
        "tutoring/home.html",
        pinned_notices=pinned_notices,
        regular_notices=regular_notices[:5],
        assignment_summary=assignment_summary,
        score_summary=score_summary,
    )


@app.route("/tutoring/login", methods=["GET", "POST"])
def tutoring_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        student = query_db(
            "SELECT * FROM users WHERE username = ? AND role = 'student'",
            (username,),
            one=True,
        )

        if student and check_password_hash(student["password_hash"], password):
            session["student_id"] = student["id"]
            flash("??덇문 ?④쑴???곗쨮 嚥≪뮄??紐낅뻥??щ빍??", "success")
            return redirect(url_for("tutoring_home"))

        flash("?袁⑹뵠???癒?뮉 ??쑬?甕곕뜇?뉐첎? ??而?몴?? ??녿뮸??덈뼄.", "danger")

    return render_template("tutoring/login.html")


@app.post("/tutoring/logout")
def tutoring_logout():
    session.pop("student_id", None)
    flash("Logged out.", "success")
    return redirect(url_for("tutoring_home"))


@app.route("/tutoring/profile", methods=["GET", "POST"])
@student_required
def tutoring_profile():
    student = get_student_user()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        age = as_int(request.form.get("age"), 0)
        education = request.form.get("education", "").strip()
        certificates = request.form.get("certificates", "").strip()
        bio = request.form.get("bio", "").strip()
        new_password = request.form.get("new_password", "")

        if not full_name:
            flash("Name is required.", "danger")
            return redirect(url_for("tutoring_profile"))

        db = get_db()
        db.execute(
            """
            UPDATE users
            SET full_name = ?, email = ?, phone = ?, age = ?, education = ?, certificates = ?, bio = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (full_name, email, phone, age, education, certificates, bio, student["id"]),
        )

        if new_password:
            db.execute(
                "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (generate_password_hash(new_password), student["id"]),
            )

        db.commit()
        flash("Profile has been updated.", "success")
        return redirect(url_for("tutoring_profile"))

    student = query_db("SELECT * FROM users WHERE id = ?", (student["id"],), one=True)
    g.student_user = student
    return render_template("tutoring/profile.html", student=student)


@app.route("/tutoring/qna", methods=["GET", "POST"])
def tutoring_qna():
    student = get_student_user()

    if request.method == "POST":
        if student is None:
            flash("Login is required to post a question.", "warning")
            return redirect(url_for("tutoring_login"))

        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        is_public = 1 if request.form.get("is_public") == "on" else 0

        if not title or not content:
            flash("Title and content are required.", "danger")
            return redirect(url_for("tutoring_qna"))

        db = get_db()
        db.execute(
            """
            INSERT INTO questions (student_id, title, content, is_public)
            VALUES (?, ?, ?, ?)
            """,
            (student["id"], title, content, is_public),
        )
        db.commit()
        flash("Question has been created.", "success")
        return redirect(url_for("tutoring_qna"))

    questions, answers_by_question = fetch_questions_for_student(student["id"] if student else None)
    return render_template(
        "tutoring/qna.html",
        questions=questions,
        answers_by_question=answers_by_question,
    )


@app.post("/tutoring/qna/<int:question_id>/edit")
@student_required
def tutoring_edit_question(question_id):
    student = get_student_user()
    question = query_db(
        "SELECT * FROM questions WHERE id = ?",
        (question_id,),
        one=True,
    )

    if question is None:
        abort(404)
    if question["student_id"] != student["id"]:
        abort(403)

    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    is_public = 1 if request.form.get("is_public") == "on" else 0

    if not title or not content:
        flash("??뺛걠????곸뒠??筌뤴뫀紐???낆젾??雅뚯눘苑??", "danger")
        return redirect(url_for("tutoring_qna"))

    db = get_db()
    db.execute(
        """
        UPDATE questions
        SET title = ?, content = ?, is_public = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (title, content, is_public, question_id),
    )
    db.commit()
    flash("筌욌뜄揆????륁젟??뤿???щ빍??", "success")
    return redirect(url_for("tutoring_qna"))


@app.post("/tutoring/qna/<int:question_id>/delete")
@student_required
def tutoring_delete_question(question_id):
    student = get_student_user()
    question = query_db(
        "SELECT * FROM questions WHERE id = ?",
        (question_id,),
        one=True,
    )

    if question is None:
        abort(404)
    if question["student_id"] != student["id"]:
        abort(403)

    db = get_db()
    db.execute("DELETE FROM questions WHERE id = ?", (question_id,))
    db.commit()
    flash("筌욌뜄揆???????뤿???щ빍??", "success")
    return redirect(url_for("tutoring_qna"))

@app.route("/tutoring/assignments", methods=["GET", "POST"])
@student_required
def tutoring_assignments():
    student = get_student_user()

    if request.method == "POST":
        assignment_id = as_int(request.form.get("assignment_id"), 0)
        content = request.form.get("content", "").strip()
        progress = max(0, min(100, as_int(request.form.get("progress"), 0)))
        status = "completed" if progress >= 100 else "in-progress"

        assignment = query_db(
            "SELECT id FROM assignments WHERE id = ?",
            (assignment_id,),
            one=True,
        )
        if assignment is None:
            flash("鈺곕똻???? ??낅뮉 ?⑥눘???낅빍??", "danger")
            return redirect(url_for("tutoring_assignments"))

        db = get_db()
        db.execute(
            """
            INSERT INTO assignment_submissions (assignment_id, student_id, content, progress, status, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(assignment_id, student_id)
            DO UPDATE SET
                content = excluded.content,
                progress = excluded.progress,
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP
            """,
            (assignment_id, student["id"], content, progress, status),
        )
        db.commit()
        flash("?⑥눘????뽱뀱/??륁젟???袁⑥┷??뤿???щ빍??", "success")
        return redirect(url_for("tutoring_assignments"))

    assignments = query_db(
        """
        SELECT
            a.*,
            s.id AS submission_id,
            s.content AS submission_content,
            s.progress AS submission_progress,
            s.status AS submission_status,
            s.updated_at AS submission_updated_at
        FROM assignments a
        LEFT JOIN assignment_submissions s
            ON s.assignment_id = a.id AND s.student_id = ?
        ORDER BY a.created_at DESC
        """,
        (student["id"],),
    )

    return render_template("tutoring/assignments.html", assignments=assignments)


@app.route("/tutoring/scores")
@student_required
def tutoring_scores():
    student = get_student_user()
    scores = query_db(
        """
        SELECT s.*, a.full_name AS admin_name
        FROM scores s
        LEFT JOIN users a ON a.id = s.announced_by
        WHERE s.student_id = ?
        ORDER BY s.created_at DESC
        """,
        (student["id"],),
    )

    summary = query_db(
        """
        SELECT
            COUNT(*) AS total,
            COALESCE(AVG((score * 100.0) / NULLIF(max_score, 0)), 0) AS avg_rate,
            COALESCE(MAX((score * 100.0) / NULLIF(max_score, 0)), 0) AS best_rate
        FROM scores
        WHERE student_id = ?
        """,
        (student["id"],),
        one=True,
    )

    return render_template("tutoring/scores.html", scores=scores, summary=summary)


@app.route("/tutoring/notices")
def tutoring_notices():
    pinned_notices, regular_notices = fetch_notices()
    return render_template(
        "tutoring/notices.html",
        pinned_notices=pinned_notices,
        regular_notices=regular_notices,
    )


@app.route("/admin", methods=["GET"])
@admin_required()
def admin_home():
    admin = get_admin_user()

    pending_admins = []
    managed_admins = []
    license_map = {}

    if admin["role"] == "super_admin":
        pending_admins = query_db(
            """
            SELECT *
            FROM users
            WHERE role = 'admin' AND approved = 0
            ORDER BY created_at ASC
            """
        )

        managed_admins = query_db(
            """
            SELECT *
            FROM users
            WHERE role = 'admin'
            ORDER BY created_at DESC
            """
        )

        licenses = query_db(
            """
            SELECT admin_id, menu_key, is_enabled
            FROM admin_licenses
            """
        )
        for license_row in licenses:
            license_map.setdefault(license_row["admin_id"], {})[license_row["menu_key"]] = license_row[
                "is_enabled"
            ]

    tracked_menu_placeholders = ",".join(["?"] * len(TRACKED_MENU_KEYS))
    activity_logs = query_db(
        f"""
        SELECT l.*, u.username AS actor_username, u.full_name AS actor_name
        FROM audit_logs l
        LEFT JOIN users u ON u.id = l.actor_admin_id
        WHERE l.menu_key IN ({tracked_menu_placeholders})
        ORDER BY l.created_at DESC
        LIMIT 80
        """,
        TRACKED_MENU_KEYS,
    )

    count_row = query_db(
        """
        SELECT
            (SELECT COUNT(*) FROM users WHERE role = 'student') AS students,
            (SELECT COUNT(*) FROM questions) AS questions,
            (SELECT COUNT(*) FROM assignments) AS assignments,
            (SELECT COUNT(*) FROM notices) AS notices
        """,
        one=True,
    )
    counts = dict(count_row)

    return render_template(
        "admin/home.html",
        pending_admins=pending_admins,
        managed_admins=managed_admins,
        license_map=license_map,
        activity_logs=activity_logs,
        counts=counts,
    )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        admin = query_db(
            "SELECT * FROM users WHERE username = ? AND role IN ('admin', 'super_admin')",
            (username,),
            one=True,
        )

        if admin and check_password_hash(admin["password_hash"], password):
            session["admin_id"] = admin["id"]
            if admin["role"] == "admin" and admin["approved"] != 1:
                flash("Account created, but waiting for master admin approval.", "warning")
            else:
                flash("Admin login successful.", "success")
            return redirect(url_for("admin_home"))

        flash("Invalid username or password.", "danger")

    return render_template("admin/login.html")


@app.post("/admin/register")
def admin_register():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()

    if not username or not password or not full_name:
        flash("Username, password, and name are required.", "danger")
        return redirect(url_for("admin_login"))

    existing = query_db("SELECT id FROM users WHERE username = ?", (username,), one=True)
    if existing is not None:
        flash("This username is already in use.", "danger")
        return redirect(url_for("admin_login"))

    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO users (username, password_hash, role, full_name, email, phone, approved)
        VALUES (?, ?, 'admin', ?, ?, ?, 0)
        """,
        (username, generate_password_hash(password), full_name, email, phone),
    )
    new_admin_id = cursor.lastrowid
    ensure_admin_license_rows(new_admin_id)
    db.commit()

    flash("Admin account created. Access is available after master admin approval and license assignment.", "success")
    return redirect(url_for("admin_login"))


@app.post("/admin/logout")
def admin_logout():
    session.pop("admin_id", None)
    flash("Admin logged out.", "success")
    return redirect(url_for("admin_login"))


@app.route("/admin/profile", methods=["GET", "POST"])
@admin_required()
def admin_profile():
    admin = get_admin_user()

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        age_raw = request.form.get("age", "").strip()
        age = as_int(age_raw, 0) if age_raw else None
        education = request.form.get("education", "").strip()
        certificates = request.form.get("certificates", "").strip()
        bio = request.form.get("bio", "").strip()
        new_password = request.form.get("new_password", "")

        if not full_name:
            flash("Name is required.", "danger")
            return redirect(url_for("admin_profile"))

        next_username = admin["username"]
        if admin["role"] == "super_admin":
            if not username:
                flash("Master admin username is required.", "danger")
                return redirect(url_for("admin_profile"))

            duplicate = query_db(
                "SELECT id FROM users WHERE username = ? AND id <> ?",
                (username, admin["id"]),
                one=True,
            )
            if duplicate is not None:
                flash("This username is already in use.", "danger")
                return redirect(url_for("admin_profile"))
            next_username = username

        if age is not None and age < 0:
            age = 0

        db = get_db()
        db.execute(
            """
            UPDATE users
            SET username = ?, full_name = ?, email = ?, phone = ?, age = ?, education = ?, certificates = ?, bio = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (next_username, full_name, email, phone, age, education, certificates, bio, admin["id"]),
        )

        if new_password:
            db.execute(
                "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (generate_password_hash(new_password), admin["id"]),
            )

        db.commit()
        flash("Admin profile has been updated.", "success")
        return redirect(url_for("admin_profile"))

    admin = query_db("SELECT * FROM users WHERE id = ?", (admin["id"],), one=True)
    g.admin_user = admin
    return render_template("admin/profile.html", admin=admin)


@app.route("/admin/portfolio", methods=["GET", "POST"])
@admin_required(super_admin_only=True)
def admin_portfolio():
    admin = get_admin_user()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        age_raw = request.form.get("age", "").strip()
        age = as_int(age_raw, 0) if age_raw else None
        education = request.form.get("education", "").strip()
        certificates = request.form.get("certificates", "").strip()
        intro = request.form.get("intro", "").strip()

        about_note = request.form.get("about_note", "").strip()
        skills_note = request.form.get("skills_note", "").strip()
        contact_note = request.form.get("contact_note", "").strip()
        github = request.form.get("github", "").strip()
        location = request.form.get("location", "").strip()

        skills_lines = request.form.get("skills", "")
        skills = _normalize_string_list(skills_lines.splitlines())

        project_titles = request.form.getlist("project_title")
        project_summaries = request.form.getlist("project_summary")
        projects = []
        for idx, title in enumerate(project_titles):
            summary = project_summaries[idx] if idx < len(project_summaries) else ""
            title_text = title.strip()
            summary_text = summary.strip()
            if title_text and summary_text:
                projects.append({"title": title_text, "summary": summary_text})

        if not full_name:
            flash("Name is required.", "danger")
            return redirect(url_for("admin_portfolio"))

        if age is not None and age < 0:
            age = 0

        if not skills:
            skills = list(PORTFOLIO_DEFAULT_SKILLS)
        if not projects:
            projects = [dict(project) for project in PORTFOLIO_DEFAULT_PROJECTS]

        db = get_db()
        db.execute(
            """
            UPDATE users
            SET
                full_name = ?,
                email = ?,
                phone = ?,
                age = ?,
                education = ?,
                certificates = ?,
                bio = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (full_name, email, phone, age, education, certificates, intro, admin["id"]),
        )

        current_content = query_db(
            "SELECT id FROM portfolio_content ORDER BY id ASC LIMIT 1",
            one=True,
        )

        if current_content is None:
            db.execute(
                """
                INSERT INTO portfolio_content (
                    about_note,
                    skills_note,
                    contact_note,
                    github,
                    location,
                    skills_json,
                    projects_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    about_note,
                    skills_note,
                    contact_note,
                    github,
                    location,
                    json.dumps(skills, ensure_ascii=False),
                    json.dumps(projects, ensure_ascii=False),
                ),
            )
        else:
            db.execute(
                """
                UPDATE portfolio_content
                SET
                    about_note = ?,
                    skills_note = ?,
                    contact_note = ?,
                    github = ?,
                    location = ?,
                    skills_json = ?,
                    projects_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    about_note,
                    skills_note,
                    contact_note,
                    github,
                    location,
                    json.dumps(skills, ensure_ascii=False),
                    json.dumps(projects, ensure_ascii=False),
                    current_content["id"],
                ),
            )

        db.commit()
        flash("Portfolio content has been updated.", "success")
        return redirect(url_for("admin_portfolio"))

    payload = build_portfolio_payload()
    profile = payload["profile"]
    projects = payload["projects"][:]
    while len(projects) < 3:
        projects.append({"title": "", "summary": ""})
    projects = projects[:3]

    return render_template(
        "admin/portfolio.html",
        profile=profile,
        about_note=payload["about_note"],
        skills_note=payload["skills_note"],
        contact_note=payload["contact_note"],
        skills_text="\n".join(payload["skills"]),
        projects=projects,
    )


@app.post("/admin/accounts/<int:target_admin_id>/update")
@admin_required(super_admin_only=True)
def admin_update_account(target_admin_id):
    target = query_db(
        "SELECT * FROM users WHERE id = ? AND role = 'admin'",
        (target_admin_id,),
        one=True,
    )
    if target is None:
        flash("Admin account not found.", "danger")
        return redirect(url_for("admin_home"))

    approved = 1 if request.form.get("approved") == "on" else 0

    db = get_db()
    db.execute(
        "UPDATE users SET approved = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (approved, target_admin_id),
    )

    ensure_admin_license_rows(target_admin_id)

    for menu_key, _label in LICENSE_MENUS:
        enabled = 1 if request.form.get(f"license_{menu_key}") == "on" else 0
        db.execute(
            """
            UPDATE admin_licenses
            SET is_enabled = ?
            WHERE admin_id = ? AND menu_key = ?
            """,
            (enabled, target_admin_id, menu_key),
        )

    db.commit()
    g.pop(f"admin_license_map_{target_admin_id}", None)

    state_text = "approved" if approved == 1 else "pending"
    log_admin_action(
        "student_accounts",
        "license_update",
        "admin_account",
        target_admin_id,
        f"{target['username']} state:{state_text}",
    )
    flash("Admin license/approval state has been updated.", "success")
    return redirect(url_for("admin_home"))
@app.post("/admin/accounts/<int:target_admin_id>/delete")
@admin_required(super_admin_only=True)
def admin_delete_account(target_admin_id):
    admin = get_admin_user()
    if admin["id"] == target_admin_id:
        flash("You cannot delete the currently logged-in admin account.", "danger")
        return redirect(url_for("admin_home"))

    target = query_db(
        "SELECT * FROM users WHERE id = ? AND role = 'admin'",
        (target_admin_id,),
        one=True,
    )
    if target is None:
        flash("Admin account to delete was not found.", "danger")
        return redirect(url_for("admin_home"))

    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (target_admin_id,))
    db.commit()

    log_admin_action(
        "student_accounts",
        "delete_admin",
        "admin_account",
        target_admin_id,
        f"{target['username']} deleted",
    )
    flash("Admin account has been deleted.", "success")
    return redirect(url_for("admin_home"))
@app.route("/admin/qna", methods=["GET", "POST"])
@admin_required(menu_key="qna")
def admin_qna():
    if request.method == "POST":
        action = request.form.get("action")
        db = get_db()

        if action == "answer":
            question_id = as_int(request.form.get("question_id"), 0)
            content = request.form.get("content", "").strip()
            question = query_db("SELECT * FROM questions WHERE id = ?", (question_id,), one=True)

            if question is None or not content:
                flash("Question not found or answer content is empty.", "danger")
                return redirect(url_for("admin_qna"))

            admin = get_admin_user()
            db.execute(
                """
                INSERT INTO question_answers (question_id, admin_id, content)
                VALUES (?, ?, ?)
                """,
                (question_id, admin["id"], content),
            )
            db.commit()
            log_admin_action("qna", "answer", "question", question_id, "answer_created")
            flash("Answer has been added.", "success")

        elif action == "delete_question":
            question_id = as_int(request.form.get("question_id"), 0)
            question = query_db("SELECT id FROM questions WHERE id = ?", (question_id,), one=True)
            if question is None:
                flash("Question not found.", "danger")
            else:
                db.execute("DELETE FROM questions WHERE id = ?", (question_id,))
                db.commit()
                log_admin_action("qna", "delete_question", "question", question_id, "question_deleted")
                flash("Question has been deleted.", "success")

        elif action == "delete_answer":
            answer_id = as_int(request.form.get("answer_id"), 0)
            answer = query_db("SELECT * FROM question_answers WHERE id = ?", (answer_id,), one=True)
            if answer is None:
                flash("Answer not found.", "danger")
            else:
                db.execute("DELETE FROM question_answers WHERE id = ?", (answer_id,))
                db.commit()
                log_admin_action("qna", "delete_answer", "answer", answer_id, "answer_deleted")
                flash("Answer has been deleted.", "success")

        return redirect(url_for("admin_qna"))

    questions = query_db(
        """
        SELECT q.*, u.full_name AS student_name, u.username AS student_username
        FROM questions q
        JOIN users u ON u.id = q.student_id
        ORDER BY q.created_at DESC
        """
    )

    question_ids = [question["id"] for question in questions]
    answers_by_question = {}
    if question_ids:
        placeholders = ",".join(["?"] * len(question_ids))
        answers = query_db(
            f"""
            SELECT a.*, u.full_name AS admin_name, u.username AS admin_username
            FROM question_answers a
            LEFT JOIN users u ON u.id = a.admin_id
            WHERE a.question_id IN ({placeholders})
            ORDER BY a.created_at ASC
            """,
            tuple(question_ids),
        )
        for answer in answers:
            answers_by_question.setdefault(answer["question_id"], []).append(answer)

    return render_template(
        "admin/qna.html",
        questions=questions,
        answers_by_question=answers_by_question,
    )
@app.route("/admin/assignments", methods=["GET", "POST"])
@admin_required(menu_key="assignments")
def admin_assignments():
    if request.method == "POST":
        action = request.form.get("action")
        db = get_db()

        if action == "create_assignment":
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            due_date = request.form.get("due_date", "").strip() or None

            if not title or not description:
                flash("?⑥눘????뺛걠????살구????낆젾??雅뚯눘苑??", "danger")
                return redirect(url_for("admin_assignments"))

            admin = get_admin_user()
            cursor = db.execute(
                """
                INSERT INTO assignments (title, description, due_date, created_by)
                VALUES (?, ?, ?, ?)
                """,
                (title, description, due_date, admin["id"]),
            )
            db.commit()
            log_admin_action("assignments", "create", "assignment", cursor.lastrowid, title)
            flash("?⑥눘?ｅ첎? ?源낆쨯??뤿???щ빍??", "success")

        elif action == "update_submission":
            submission_id = as_int(request.form.get("submission_id"), 0)
            progress = max(0, min(100, as_int(request.form.get("progress"), 0)))
            status = request.form.get("status", "").strip() or "in-progress"

            submission = query_db(
                "SELECT * FROM assignment_submissions WHERE id = ?",
                (submission_id,),
                one=True,
            )
            if submission is None:
                flash("??뽱뀱 ?類ｋ궖??筌≪뼚??????곷뮸??덈뼄.", "danger")
            else:
                db.execute(
                    """
                    UPDATE assignment_submissions
                    SET progress = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (progress, status, submission_id),
                )
                db.commit()
                log_admin_action(
                    "assignments",
                    "update_submission",
                    "assignment_submission",
                    submission_id,
                    f"progress={progress}",
                )
                flash("??덇문 ?⑥눘???源녿옱?袁? ??낅쑓??꾨뱜??뤿???щ빍??", "success")

        elif action == "delete_assignment":
            assignment_id = as_int(request.form.get("assignment_id"), 0)
            assignment = query_db("SELECT * FROM assignments WHERE id = ?", (assignment_id,), one=True)
            if assignment is None:
                flash("?⑥눘?ｇ몴?筌≪뼚??????곷뮸??덈뼄.", "danger")
            else:
                db.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
                db.commit()
                log_admin_action(
                    "assignments",
                    "delete",
                    "assignment",
                    assignment_id,
                    assignment["title"],
                )
                flash("?⑥눘?ｅ첎? ?????뤿???щ빍??", "success")

        return redirect(url_for("admin_assignments"))

    assignments = query_db(
        """
        SELECT
            a.*,
            u.full_name AS creator_name,
            COUNT(s.id) AS submission_count
        FROM assignments a
        LEFT JOIN users u ON u.id = a.created_by
        LEFT JOIN assignment_submissions s ON s.assignment_id = a.id
        GROUP BY a.id
        ORDER BY a.created_at DESC
        """
    )

    submissions = query_db(
        """
        SELECT
            s.*,
            a.title AS assignment_title,
            st.full_name AS student_name,
            st.username AS student_username
        FROM assignment_submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN users st ON st.id = s.student_id
        ORDER BY s.updated_at DESC
        """
    )

    return render_template(
        "admin/assignments.html",
        assignments=assignments,
        submissions=submissions,
    )


@app.route("/admin/scores", methods=["GET", "POST"])
@admin_required(menu_key="scores")
def admin_scores():
    if request.method == "POST":
        action = request.form.get("action")
        db = get_db()

        if action == "add_score":
            student_id = as_int(request.form.get("student_id"), 0)
            test_name = request.form.get("test_name", "").strip()
            score = as_float(request.form.get("score"), 0)
            max_score = as_float(request.form.get("max_score"), 100)
            analysis = request.form.get("analysis", "").strip()

            student = query_db(
                "SELECT id FROM users WHERE id = ? AND role = 'student'",
                (student_id,),
                one=True,
            )
            if student is None or not test_name or max_score <= 0:
                flash("??낆젾揶쏅????類ㅼ뵥??雅뚯눘苑??", "danger")
                return redirect(url_for("admin_scores"))

            admin = get_admin_user()
            cursor = db.execute(
                """
                INSERT INTO scores (student_id, test_name, score, max_score, analysis, announced_by)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (student_id, test_name, score, max_score, analysis, admin["id"]),
            )
            db.commit()
            log_admin_action("scores", "announce", "score", cursor.lastrowid, test_name)
            flash("?源놁읅???源낆쨯??뤿???щ빍??", "success")

        elif action == "delete_score":
            score_id = as_int(request.form.get("score_id"), 0)
            score = query_db("SELECT * FROM scores WHERE id = ?", (score_id,), one=True)
            if score is None:
                flash("?源놁읅 ?怨쀬뵠?怨? 筌≪뼚??????곷뮸??덈뼄.", "danger")
            else:
                db.execute("DELETE FROM scores WHERE id = ?", (score_id,))
                db.commit()
                log_admin_action("scores", "delete", "score", score_id, score["test_name"])
                flash("?源놁읅 ?怨쀬뵠?怨? ?????뤿???щ빍??", "success")

        return redirect(url_for("admin_scores"))

    students = query_db(
        "SELECT id, username, full_name FROM users WHERE role = 'student' ORDER BY full_name ASC"
    )

    scores = query_db(
        """
        SELECT s.*, st.full_name AS student_name, st.username AS student_username, a.full_name AS admin_name
        FROM scores s
        JOIN users st ON st.id = s.student_id
        LEFT JOIN users a ON a.id = s.announced_by
        ORDER BY s.created_at DESC
        """
    )

    return render_template("admin/scores.html", students=students, scores=scores)


@app.route("/admin/notices", methods=["GET", "POST"])
@admin_required(menu_key="notices")
def admin_notices():
    if request.method == "POST":
        action = request.form.get("action")
        db = get_db()

        if action == "create_notice":
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            is_pinned = 1 if request.form.get("is_pinned") == "on" else 0

            if not title or not content:
                flash("Title and content are required.", "danger")
                return redirect(url_for("admin_notices"))

            admin = get_admin_user()
            cursor = db.execute(
                """
                INSERT INTO notices (title, content, is_pinned, pinned_at, created_by)
                VALUES (?, ?, ?, CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END, ?)
                """,
                (title, content, is_pinned, is_pinned, admin["id"]),
            )
            db.commit()
            log_admin_action("notices", "create", "notice", cursor.lastrowid, title)
            flash("Notice has been created.", "success")

        elif action == "toggle_pin":
            notice_id = as_int(request.form.get("notice_id"), 0)
            notice = query_db("SELECT * FROM notices WHERE id = ?", (notice_id,), one=True)
            if notice is None:
                flash("Notice not found.", "danger")
            else:
                next_state = 0 if notice["is_pinned"] == 1 else 1
                db.execute(
                    """
                    UPDATE notices
                    SET
                        is_pinned = ?,
                        pinned_at = CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (next_state, next_state, notice_id),
                )
                db.commit()
                action_text = "pin" if next_state == 1 else "unpin"
                log_admin_action("notices", action_text, "notice", notice_id, notice["title"])
                flash("Pinned state has been changed.", "success")

        elif action == "delete_notice":
            notice_id = as_int(request.form.get("notice_id"), 0)
            notice = query_db("SELECT * FROM notices WHERE id = ?", (notice_id,), one=True)
            if notice is None:
                flash("Notice not found.", "danger")
            else:
                db.execute("DELETE FROM notices WHERE id = ?", (notice_id,))
                db.commit()
                log_admin_action("notices", "delete", "notice", notice_id, notice["title"])
                flash("Notice has been deleted.", "success")

        return redirect(url_for("admin_notices"))

    pinned_notices, regular_notices = fetch_notices()
    return render_template(
        "admin/notices.html",
        pinned_notices=pinned_notices,
        regular_notices=regular_notices,
    )


@app.route("/admin/students", methods=["GET", "POST"])
@admin_required(menu_key="student_accounts")
def admin_students():
    if request.method == "POST":
        action = request.form.get("action")
        db = get_db()

        if action == "create_student":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()
            age = as_int(request.form.get("age"), 0)
            education = request.form.get("education", "").strip()

            if not username or not password or not full_name:
                flash("Username, password, and name are required.", "danger")
                return redirect(url_for("admin_students"))

            duplicate = query_db("SELECT id FROM users WHERE username = ?", (username,), one=True)
            if duplicate is not None:
                flash("This student username is already in use.", "danger")
                return redirect(url_for("admin_students"))

            cursor = db.execute(
                """
                INSERT INTO users (username, password_hash, role, full_name, email, phone, age, education, approved)
                VALUES (?, ?, 'student', ?, ?, ?, ?, ?, 1)
                """,
                (username, generate_password_hash(password), full_name, email, phone, age, education),
            )
            db.commit()
            log_admin_action("student_accounts", "create", "student", cursor.lastrowid, username)
            flash("Student account has been created.", "success")

        elif action == "update_student":
            student_id = as_int(request.form.get("student_id"), 0)
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()
            age = as_int(request.form.get("age"), 0)
            education = request.form.get("education", "").strip()
            certificates = request.form.get("certificates", "").strip()
            bio = request.form.get("bio", "").strip()
            new_password = request.form.get("new_password", "")

            target = query_db(
                "SELECT * FROM users WHERE id = ? AND role = 'student'",
                (student_id,),
                one=True,
            )
            if target is None:
                flash("Student account not found.", "danger")
                return redirect(url_for("admin_students"))

            db.execute(
                """
                UPDATE users
                SET full_name = ?, email = ?, phone = ?, age = ?, education = ?, certificates = ?, bio = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (full_name, email, phone, age, education, certificates, bio, student_id),
            )
            if new_password:
                db.execute(
                    "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (generate_password_hash(new_password), student_id),
                )
            db.commit()
            log_admin_action("student_accounts", "update", "student", student_id, target["username"])
            flash("Student profile has been updated.", "success")

        elif action == "delete_student":
            student_id = as_int(request.form.get("student_id"), 0)
            target = query_db(
                "SELECT * FROM users WHERE id = ? AND role = 'student'",
                (student_id,),
                one=True,
            )
            if target is None:
                flash("Student account not found.", "danger")
            else:
                db.execute("DELETE FROM users WHERE id = ?", (student_id,))
                db.commit()
                log_admin_action("student_accounts", "delete", "student", student_id, target["username"])
                flash("Student account has been deleted.", "success")

        return redirect(url_for("admin_students"))

    students = query_db(
        """
        SELECT *
        FROM users
        WHERE role = 'student'
        ORDER BY created_at DESC
        """
    )

    return render_template("admin/students.html", students=students)


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", title="403 Forbidden", message="You do not have permission to access this page."), 403


@app.errorhandler(404)
def page_not_found(_error):
    return render_template("error.html", title="404 Not Found", message="The requested page could not be found."), 404


if __name__ == "__main__":
    raw_port = os.environ.get("PORT", "5000")
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        port = 5000
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)

