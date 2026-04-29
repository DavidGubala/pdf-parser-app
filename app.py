import functools
import hashlib
import json
import logging
import logging.handlers
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = Path(__file__).parent / "uploads"
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB
# Use DATABASE_PATH env var if provided, otherwise default to /app/data/documents.db
# This ensures compatibility with the recommended volume mapping in docker-compose.dev.yml
db_path = os.getenv("DATABASE_PATH", "/app/data/documents.db")
app.config["DATABASE"] = Path(db_path)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me-in-production")
app.permanent_session_lifetime = timedelta(days=30)

# ---------------------------------------------------------------------------
# Logging — stdout + daily log file (logs/YYYY-MM-DD.log)
# ---------------------------------------------------------------------------


LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)

_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(logging.Formatter(LOG_FORMAT))
_root_logger.addHandler(_stream_handler)


class DailyFileHandler(logging.FileHandler):
    def __init__(self, log_dir):
        self.log_dir = log_dir
        self.current_date = None
        super().__init__(self._get_filename())
        self.current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _get_filename(self):
        return str(self.log_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d.log"))

    def emit(self, record):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.current_date:
            self.close()
            self.baseFilename = self._get_filename()
            self.stream = open(self.baseFilename, "a", encoding="utf-8")
            self.current_date = today
        super().emit(record)


_file_handler = DailyFileHandler(LOG_DIR)
_file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
_root_logger.addHandler(_file_handler)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Remote PDF Service Client (MacBook Compute Node)
# ---------------------------------------------------------------------------

PDF_SERVICE_URL = os.getenv("PDF_SERVICE_URL")
PDF_API_KEY = os.getenv("PDF_API_KEY")
OLLAMA_URL = os.getenv(
    "OLLAMA_URL", os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
)


def extract_pdf_remote(file_path: str) -> dict:
    """Send PDF to MacBook for Docling processing."""
    if not PDF_SERVICE_URL:
        raise RuntimeError("PDF_SERVICE_URL not configured")
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{PDF_SERVICE_URL}/process-pdf",
            files={"file": (os.path.basename(file_path), f, "application/pdf")},
            headers={"Authorization": f"Bearer {PDF_API_KEY}"},
            timeout=300,
        )
    resp.raise_for_status()
    return resp.json()


def ask_ollama_remote(model: str, prompt: str) -> str:
    """Send prompt to MacBook Ollama."""
    url = f"{OLLAMA_URL}/api/generate"
    resp = requests.post(
        url,
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["response"]


ALLOWED_EXTENSIONS = {"pdf"}

_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")


@app.errorhandler(HTTPException)
def handle_http_error(exc):
    return jsonify({"error": exc.description}), exc.code


@app.errorhandler(Exception)
def handle_generic_error(exc):
    logger.exception("Unhandled exception")
    return jsonify({"error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------


@app.before_request
def _start_timer():
    setattr(request, "_start_time", time.time())


@app.after_request
def _log_request(response):
    if request.path.startswith("/static/"):
        return response

    duration_ms = (time.time() - getattr(request, "_start_time", time.time())) * 1000
    user = session.get("username", "-")
    level = logging.WARNING if response.status_code >= 400 else logging.INFO
    logger.log(
        level,
        "user=%s %s %s -> %s (%.0fms)",
        user,
        request.method,
        request.path,
        response.status_code,
        duration_ms,
    )
    return response


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return wrapper


def current_user_id():
    return session.get("user_id", "")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if "user_id" in session:
            return redirect(url_for("index"))
        return render_template("login.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        return render_template("login.html", error="Please enter both fields.")

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    db.close()

    if user is None or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", error="Invalid username or password.")

    session.clear()
    session.permanent = bool(request.form.get("remember"))
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    logger.info("User %r logged in (remember=%s)", username, session.permanent)
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    username = session.get("username", "?")
    session.clear()
    logger.info("User %r logged out", username)
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def get_db():
    # timeout=30 tells SQLite to wait up to 30 seconds for a lock to clear
    db = sqlite3.connect(app.config["DATABASE"], timeout=30)
    db.row_factory = sqlite3.Row
    # WAL mode allows simultaneous reads and writes
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL,
            original_name TEXT NOT NULL,
            upload_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            page_count INTEGER,
            text_content TEXT,
            tables_json TEXT,
            unstructured_text TEXT,
            images_json TEXT,
            content_hash TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            company_name TEXT DEFAULT '',
            po_number TEXT DEFAULT '',
            po_date TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            verified_at TEXT,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS po_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            po_id TEXT NOT NULL,
            item_name TEXT DEFAULT '',
            description TEXT DEFAULT '',
            due_date TEXT DEFAULT '',
            quantity TEXT DEFAULT '',
            unit_price TEXT DEFAULT '',
            FOREIGN KEY (po_id) REFERENCES purchase_orders(id) ON DELETE CASCADE
        )
    """)
    # Migrate: add user_id to existing documents table if missing
    try:
        db.execute("ALTER TABLE documents ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
        logger.info("Migrated documents table: added user_id column")
    except sqlite3.OperationalError:
        pass  # column already exists

    db.execute("""
        CREATE TABLE IF NOT EXISTS po_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id TEXT NOT NULL,
            entity_type TEXT NOT NULL, -- 'PO' or 'ITEM'
            entity_id TEXT NOT NULL,   -- po_id or po_item_id
            field_name TEXT NOT NULL,
            original_value TEXT,
            corrected_value TEXT,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS llm_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id TEXT NOT NULL,
            prompt TEXT,
            response TEXT,
            latency REAL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        )
    """)

    try:
        db.execute("ALTER TABLE documents ADD COLUMN unstructured_text TEXT")
        logger.info("Migrated documents table: added unstructured_text column")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migrate: add verified_at to purchase_orders if missing
    try:
        db.execute("ALTER TABLE purchase_orders ADD COLUMN verified_at TEXT")
        logger.info("Migrated purchase_orders table: added verified_at column")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migrate: add content_hash to documents if missing
    try:
        db.execute("ALTER TABLE documents ADD COLUMN content_hash TEXT")
        logger.info("Migrated documents table: added content_hash column")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migrate: add markdown and unstructured_text to verified_examples if missing
    try:
        db.execute("ALTER TABLE verified_examples ADD COLUMN markdown TEXT")
        logger.info("Migrated verified_examples table: added markdown column")
    except sqlite3.OperationalError:
        pass
    try:
        db.execute("ALTER TABLE verified_examples ADD COLUMN unstructured_text TEXT")
        logger.info("Migrated verified_examples table: added unstructured_text column")
    except sqlite3.OperationalError:
        pass

    db.execute("""
        CREATE TABLE IF NOT EXISTS verified_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            markdown TEXT,
            unstructured_text TEXT,
            po_data TEXT NOT NULL,
            verified_at TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        )
    """)

    db.commit()
    db.close()


def _row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    d["tables"] = json.loads(d.pop("tables_json") or "[]")
    d["images"] = json.loads(d.pop("images_json") or "[]")
    return d


# ---------------------------------------------------------------------------
# PO extraction helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# LLM Extraction Logic (Ollama Integration)
# ---------------------------------------------------------------------------


def extract_po_remote(
    markdown: str, unstructured_text: str, examples: list | None = None
) -> dict | None:
    """Send extracted text to MacBook for PO extraction via Ollama."""
    if not PDF_SERVICE_URL:
        logger.error("PDF_SERVICE_URL not configured — cannot reach extraction service")
        return None

    url = f"{PDF_SERVICE_URL}/extract-po"
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

    payload: dict[str, object] = {
        "markdown": markdown,
        "unstructured_text": unstructured_text,
        "model": model,
    }
    if examples:
        payload["examples"] = examples

    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {PDF_API_KEY}"},
            timeout=120,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error("PO extraction service error: %s", e)
        return None


def get_verified_examples(user_id: str, limit: int = 3) -> list:
    """Fetch the most recent verified PO extractions for few-shot prompting."""
    db = get_db()
    rows = db.execute(
        """SELECT markdown, unstructured_text, po_data FROM verified_examples
           WHERE user_id = ?
           ORDER BY verified_at DESC
           LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    db.close()
    return [
        {
            "markdown": r["markdown"],
            "unstructured_text": r["unstructured_text"],
            "po_data": json.loads(r["po_data"]),
        }
        for r in rows
    ]


def persist_extracted_po(doc_id: str, data: dict) -> None:
    """Persist PO extraction results from the MacBook to the database."""
    if not data:
        return

    po_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()

    db = get_db()
    try:
        db.execute(
            """INSERT INTO purchase_orders (id, document_id, company_name, po_number, po_date, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                po_id,
                doc_id,
                data.get("company_name"),
                data.get("po_number"),
                data.get("po_date"),
                now,
            ),
        )

        for item in data.get("items", []):
            db.execute(
                """INSERT INTO po_items (po_id, item_name, description, due_date, quantity, unit_price)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    po_id,
                    item.get("item_name"),
                    item.get("description"),
                    item.get("due_date"),
                    item.get("quantity"),
                    item.get("unit_price"),
                ),
            )
        db.commit()
        logger.info("PO extraction persisted for %s", doc_id)
    except Exception as e:
        logger.error("Failed to persist PO data for %s: %s", doc_id, e)
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# PDF processing with Docling (runs in background thread)
# ---------------------------------------------------------------------------


def run_po_extraction(
    doc_id: str, text_content: str, unstructured_text: str, user_id: str
):
    """Run LLM PO extraction and persist results."""
    db = get_db()
    db.execute(
        "UPDATE documents SET status='analyzing' WHERE id=?",
        (doc_id,),
    )
    db.commit()
    db.close()
    logger.info("Starting PO extraction (analyzing) for %s", doc_id)

    examples = get_verified_examples(user_id)

    start_time = time.time()
    po_data = extract_po_remote(text_content, unstructured_text, examples)
    latency = time.time() - start_time

    db = get_db()
    if po_data:
        logger.info("PO extraction completed for %s in %.2fs", doc_id, latency)
        persist_extracted_po(doc_id, po_data)
        db.execute(
            "UPDATE documents SET status='completed' WHERE id=?",
            (doc_id,),
        )
    else:
        logger.error("PO extraction failed for %s", doc_id)
        db.execute(
            "UPDATE documents SET status='error', error=? WHERE id=?",
            ("PO extraction failed — no data returned from LLM", doc_id),
        )
    db.commit()
    db.close()


def process_pdf(doc_id: str, filepath: str, user_id: str = ""):
    """Send PDF to MacBook for remote Docling processing and store results."""
    logger.info("Starting remote PDF processing for %s (%s)", doc_id, filepath)
    overall_start = time.time()
    try:
        # --- Remote Processing Phase ---
        remote_start = time.time()
        logger.info("Sending PDF to remote service for %s...", doc_id)

        result = extract_pdf_remote(filepath)
        remote_latency = time.time() - remote_start
        logger.info(
            "Remote processing completed for %s in %.2fs", doc_id, remote_latency
        )

        if not result:
            raise RuntimeError("Remote PDF extraction returned no data")

        text_content = result.get("markdown", "")
        page_count = result.get("page_count", 0)

        # Skip separate table extraction — let the LLM infer from markdown
        tables = []

        # Use unstructured text from the remote service (Unstructured library output)
        unstructured_text = result.get("unstructured_text", result.get("text", ""))

        db = get_db()
        db.execute(
            """UPDATE documents
               SET text_content=?, tables_json=?, unstructured_text=?, page_count=?
               WHERE id=?""",
            (text_content, json.dumps(tables), unstructured_text, page_count, doc_id),
        )
        db.commit()
        db.close()

        # --- PO Extraction Phase ---
        run_po_extraction(doc_id, text_content, unstructured_text, user_id)

        overall_latency = time.time() - overall_start
        logger.info("Processing completed for %s in %.2fs", doc_id, overall_latency)

    except Exception as exc:
        logger.exception("Remote PDF processing failed for %s", doc_id)
        db = get_db()
        db.execute(
            "UPDATE documents SET status='error', error=? WHERE id=?",
            (str(exc), doc_id),
        )
        db.commit()
        db.close()


# ---------------------------------------------------------------------------
# Routes — Pages
# ---------------------------------------------------------------------------


@app.route("/")
@login_required
def index():
    return render_template("index.html", username=session.get("username", ""))


# ---------------------------------------------------------------------------
# Routes — API (Documents)
# ---------------------------------------------------------------------------


@app.route("/api/upload", methods=["POST"])
@login_required
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    filename = file.filename
    if not filename:
        return jsonify({"error": "Empty filename"}), 400

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"File type '.{ext}' not allowed. Upload a PDF."}), 400

    # Compute content hash for duplicate detection
    file_content = file.read()
    file.seek(0)
    content_hash = hashlib.md5(file_content).hexdigest()

    uid = current_user_id()
    db = get_db()

    # Check for duplicate by same user
    existing = db.execute(
        "SELECT id, status FROM documents WHERE user_id=? AND content_hash=? ORDER BY upload_time DESC LIMIT 1",
        (uid, content_hash),
    ).fetchone()

    if existing:
        # Check if the existing document's PO is verified
        verified_po = db.execute(
            """SELECT 1 FROM purchase_orders po
               JOIN documents d ON po.document_id = d.id
               WHERE d.id = ? AND po.verified_at IS NOT NULL""",
            (existing["id"],),
        ).fetchone()

        db.close()
        logger.info(
            "Duplicate upload detected: %s -> existing %s (verified: %s)",
            filename,
            existing["id"],
            bool(verified_po),
        )
        return jsonify(
            {
                "existing": True,
                "document_id": existing["id"],
                "status": existing["status"],
                "filename": filename,
                "is_verified": bool(verified_po),
            }
        ), 200

    doc_id = uuid.uuid4().hex[:12]
    safe_name = secure_filename(filename)
    stored_name = f"{doc_id}_{safe_name}"
    filepath = app.config["UPLOAD_FOLDER"] / stored_name
    file.save(str(filepath))

    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """INSERT INTO documents (id, user_id, filename, original_name, upload_time, status, content_hash)
           VALUES (?, ?, ?, ?, ?, 'processing', ?)""",
        (doc_id, uid, stored_name, file.filename, now, content_hash),
    )
    db.commit()
    db.close()

    thread = threading.Thread(
        target=process_pdf, args=(doc_id, str(filepath), uid), daemon=True
    )
    thread.start()

    logger.info("Uploaded %s as %s — processing started", file.filename, doc_id)
    return jsonify(
        {"id": doc_id, "filename": file.filename, "status": "processing"}
    ), 201


@app.route("/api/documents/<doc_id>/reextract", methods=["POST"])
@login_required
def reextract_document(doc_id):
    """Re-run PO extraction on an existing document."""
    uid = current_user_id()
    db = get_db()
    doc = db.execute(
        "SELECT text_content, unstructured_text, status FROM documents WHERE id=? AND user_id=?",
        (doc_id, uid),
    ).fetchone()

    if not doc:
        db.close()
        return jsonify({"error": "Document not found"}), 404

    text_content = doc["text_content"] or ""
    unstructured_text = doc["unstructured_text"] or ""

    if not text_content:
        db.close()
        return jsonify({"error": "Document has no extracted text. Re-upload?"}), 400

    # Delete existing PO data for this document
    po_rows = db.execute(
        "SELECT id FROM purchase_orders WHERE document_id=?", (doc_id,)
    ).fetchall()
    for po in po_rows:
        db.execute("DELETE FROM po_items WHERE po_id=?", (po["id"],))
    db.execute("DELETE FROM po_corrections WHERE document_id=?", (doc_id,))
    db.execute("DELETE FROM purchase_orders WHERE document_id=?", (doc_id,))
    db.execute(
        "UPDATE documents SET status='analyzing', error=NULL WHERE id=?",
        (doc_id,),
    )
    db.commit()
    db.close()

    uid = current_user_id()
    thread = threading.Thread(
        target=run_po_extraction,
        args=(doc_id, text_content, unstructured_text, uid),
        daemon=True,
    )
    thread.start()

    logger.info("Re-extraction started for %s", doc_id)
    return jsonify({"status": "analyzing", "document_id": doc_id})


@app.route("/api/documents", methods=["GET"])
@login_required
def list_documents():
    uid = current_user_id()
    db = get_db()
    rows = db.execute(
        "SELECT id, original_name, upload_time, status, error, page_count FROM documents WHERE user_id=? ORDER BY upload_time DESC",
        (uid,),
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/documents/<doc_id>", methods=["GET"])
@login_required
def get_document(doc_id):
    uid = current_user_id()
    db = get_db()
    row = db.execute(
        "SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, uid)
    ).fetchone()
    db.close()
    if row is None:
        return jsonify({"error": "Document not found"}), 404
    return jsonify(_row_to_dict(row))


@app.route("/api/documents/<doc_id>/pdf", methods=["GET"])
@login_required
def get_document_pdf(doc_id):
    uid = current_user_id()
    db = get_db()
    row = db.execute(
        "SELECT filename FROM documents WHERE id=? AND user_id=?", (doc_id, uid)
    ).fetchone()
    db.close()
    if row is None:
        return jsonify({"error": "Document not found"}), 404
    return send_from_directory(
        app.config["UPLOAD_FOLDER"], row["filename"], mimetype="application/pdf"
    )


@app.route("/api/documents/<doc_id>", methods=["DELETE"])
@login_required
def delete_document(doc_id):
    uid = current_user_id()
    db = get_db()
    row = db.execute(
        "SELECT filename FROM documents WHERE id=? AND user_id=?", (doc_id, uid)
    ).fetchone()
    if row is None:
        db.close()
        return jsonify({"error": "Document not found"}), 404

    filepath = app.config["UPLOAD_FOLDER"] / row["filename"]
    if filepath.exists():
        filepath.unlink()

    po_rows = db.execute(
        "SELECT id FROM purchase_orders WHERE document_id=?", (doc_id,)
    ).fetchall()
    for po in po_rows:
        db.execute("DELETE FROM po_items WHERE po_id=?", (po["id"],))
    db.execute("DELETE FROM purchase_orders WHERE document_id=?", (doc_id,))
    db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    db.commit()
    db.close()
    logger.info("Deleted document %s and associated PO data", doc_id)
    return jsonify({"deleted": doc_id})


# ---------------------------------------------------------------------------
# Routes — API (Purchase Orders / Schedule)
# ---------------------------------------------------------------------------


@app.route("/api/purchase-orders", methods=["GET"])
@login_required
def list_purchase_orders():
    uid = current_user_id()
    db = get_db()
    pos = db.execute(
        """
        SELECT po.*, d.original_name AS document_name
        FROM purchase_orders po
        JOIN documents d ON po.document_id = d.id
        WHERE d.user_id = ?
        ORDER BY po.created_at DESC
    """,
        (uid,),
    ).fetchall()

    result = []
    for po in pos:
        po_dict = dict(po)
        items = db.execute(
            "SELECT * FROM po_items WHERE po_id = ? ORDER BY due_date ASC, id ASC",
            (po_dict["id"],),
        ).fetchall()
        po_dict["items"] = [dict(item) for item in items]
        result.append(po_dict)

    db.close()
    return jsonify(result)


@app.route("/api/purchase-orders/<po_id>", methods=["GET"])
@login_required
def get_purchase_order(po_id):
    uid = current_user_id()
    db = get_db()
    po = db.execute(
        """
        SELECT po.*, d.original_name AS document_name
        FROM purchase_orders po
        JOIN documents d ON po.document_id = d.id
        WHERE po.id = ? AND d.user_id = ?
    """,
        (po_id, uid),
    ).fetchone()

    if po is None:
        db.close()
        return jsonify({"error": "Purchase order not found"}), 404

    po_dict = dict(po)
    items = db.execute(
        "SELECT * FROM po_items WHERE po_id = ? ORDER BY due_date ASC, id ASC",
        (po_dict["id"],),
    ).fetchall()
    po_dict["items"] = [dict(item) for item in items]

    db.close()
    return jsonify(po_dict)


@app.route("/api/schedule", methods=["GET"])
@login_required
def get_schedule():
    uid = current_user_id()
    db = get_db()

    items = db.execute(
        """
        SELECT
            pi.id, pi.item_name, pi.description, pi.due_date,
            pi.quantity, pi.unit_price,
            po.company_name, po.po_number, po.po_date, po.document_id,
            d.original_name AS document_name
        FROM po_items pi
        JOIN purchase_orders po ON pi.po_id = po.id
        JOIN documents d ON po.document_id = d.id
        WHERE d.user_id = ?
        ORDER BY
            CASE WHEN pi.due_date = '' OR pi.due_date IS NULL THEN 1 ELSE 0 END,
            pi.due_date ASC,
            po.company_name ASC
    """,
        (uid,),
    ).fetchall()

    total_pos = db.execute(
        "SELECT COUNT(*) AS c FROM purchase_orders po JOIN documents d ON po.document_id = d.id WHERE d.user_id = ?",
        (uid,),
    ).fetchone()["c"]
    total_items = len(items)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    week_end = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")

    overdue = 0
    due_this_week = 0
    upcoming = 0

    items_list = []
    for item in items:
        d = dict(item)
        due = d.get("due_date", "")
        if due:
            if due < today:
                d["urgency"] = "overdue"
                overdue += 1
            elif due <= week_end:
                d["urgency"] = "due_soon"
                due_this_week += 1
            else:
                d["urgency"] = "upcoming"
                upcoming += 1
        else:
            d["urgency"] = "no_date"
        items_list.append(d)

    db.close()
    return jsonify(
        {
            "summary": {
                "total_pos": total_pos,
                "total_items": total_items,
                "overdue": overdue,
                "due_this_week": due_this_week,
                "upcoming": upcoming,
            },
            "items": items_list,
        }
    )


@app.route("/api/purchase-orders/correct", methods=["POST"])
@login_required
def correct_purchase_order():
    data = request.get_json()
    doc_id = data.get("document_id")
    corrections = data.get("corrections", [])

    if not doc_id:
        return jsonify({"error": "Missing document_id"}), 400

    db = get_db()
    # Verify ownership
    doc = db.execute("SELECT user_id FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc or doc["user_id"] != current_user_id():
        db.close()
        return jsonify({"error": "Unauthorized"}), 403

    now = datetime.now(timezone.utc).isoformat()

    try:
        for corr in corrections:
            entity_type = corr.get("entity_type")
            entity_id = corr.get("entity_id")

            if entity_type == "PO":
                field = corr.get("field_name")
                new_val = corr.get("new_value")
                if field not in {"company_name", "po_number", "po_date"}:
                    continue
                row = db.execute(
                    f"SELECT {field} FROM purchase_orders WHERE id = ?", (entity_id,)
                ).fetchone()
                original_val = row[0] if row else None
                db.execute(
                    f"UPDATE purchase_orders SET {field} = ? WHERE id = ?",
                    (new_val, entity_id),
                )
                db.execute(
                    """INSERT INTO po_corrections
                       (document_id, entity_type, entity_id, field_name, original_value, corrected_value, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (doc_id, entity_type, entity_id, field, original_val, new_val, now),
                )

            elif entity_type == "ITEM":
                field = corr.get("field_name")
                new_val = corr.get("new_value")
                if field not in {
                    "item_name",
                    "description",
                    "due_date",
                    "quantity",
                    "unit_price",
                }:
                    continue
                row = db.execute(
                    f"SELECT {field} FROM po_items WHERE id = ?", (entity_id,)
                ).fetchone()
                original_val = row[0] if row else None
                db.execute(
                    f"UPDATE po_items SET {field} = ? WHERE id = ?",
                    (new_val, entity_id),
                )
                db.execute(
                    """INSERT INTO po_corrections
                       (document_id, entity_type, entity_id, field_name, original_value, corrected_value, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (doc_id, entity_type, entity_id, field, original_val, new_val, now),
                )

            elif entity_type == "DELETE_ITEM":
                db.execute("DELETE FROM po_items WHERE id = ?", (entity_id,))

            elif entity_type == "ADD_ITEM":
                po_id = corr.get("po_id")
                new_id = uuid.uuid4().hex[:12]
                db.execute(
                    """INSERT INTO po_items (id, po_id, item_name, description, due_date, quantity, unit_price)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        new_id,
                        po_id,
                        corr.get("item_name", ""),
                        corr.get("description", ""),
                        corr.get("due_date", ""),
                        corr.get("quantity", ""),
                        corr.get("unit_price", ""),
                    ),
                )

            else:
                continue

        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.exception("Correction failed: %s", e)
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/corrections/export")
@login_required
def export_corrections():
    db = get_db()
    corrections = db.execute(
        "SELECT * FROM po_corrections ORDER BY timestamp DESC"
    ).fetchall()
    db.close()

    return jsonify([dict(c) for c in corrections])


@app.route("/api/purchase-orders/<po_id>/verify", methods=["POST"])
@login_required
def verify_purchase_order(po_id):
    """Mark a Purchase Order as manually verified by the user."""
    uid = current_user_id()
    db = get_db()

    logger.info("Verifying PO %s for user %s", po_id, uid)
    # Verify ownership via document
    po = db.execute(
        """
        SELECT po.id, po.company_name, po.po_number, po.po_date, po.document_id
        FROM purchase_orders po
        JOIN documents d ON po.document_id = d.id
        WHERE po.id = ? AND d.user_id = ?
    """,
        (po_id, uid),
    ).fetchone()

    if not po:
        logger.warning("PO %s not found or not owned by user %s", po_id, uid)
        db.close()
        return jsonify({"error": "Purchase order not found"}), 404

    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE purchase_orders SET verified_at = ? WHERE id = ?",
        (now, po_id),
    )

    # Snapshot verified PO data and original text for few-shot learning
    doc_text = db.execute(
        "SELECT text_content, unstructured_text FROM documents WHERE id = ?",
        (po["document_id"],),
    ).fetchone()

    items = db.execute(
        "SELECT item_name, description, due_date, quantity, unit_price FROM po_items WHERE po_id = ?",
        (po_id,),
    ).fetchall()
    po_data = {
        "company_name": po["company_name"],
        "po_number": po["po_number"],
        "po_date": po["po_date"],
        "items": [dict(item) for item in items],
    }
    db.execute(
        """INSERT INTO verified_examples (user_id, document_id, markdown, unstructured_text, po_data, verified_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            uid,
            po["document_id"],
            doc_text["text_content"],
            doc_text["unstructured_text"],
            json.dumps(po_data),
            now,
        ),
    )

    db.commit()
    db.close()
    logger.info("PO %s verified by user %s", po_id, uid)
    return jsonify({"status": "verified", "verified_at": now})


@app.route("/api/purchase-orders/<po_id>/unverify", methods=["POST"])
@login_required
def unverify_purchase_order(po_id):
    """Mark a Purchase Order as unverified and remove from few-shot examples."""
    uid = current_user_id()
    db = get_db()

    # Verify ownership via document
    po = db.execute(
        """
        SELECT po.document_id FROM purchase_orders po
        JOIN documents d ON po.document_id = d.id
        WHERE po.id = ? AND d.user_id = ?
    """,
        (po_id, uid),
    ).fetchone()

    if not po:
        db.close()
        return jsonify({"error": "Purchase order not found"}), 404

    doc_id = po["document_id"]

    # 1. Clear verified_at timestamp
    db.execute("UPDATE purchase_orders SET verified_at = NULL WHERE id = ?", (po_id,))

    # 2. Remove from verified_examples table
    db.execute(
        "DELETE FROM verified_examples WHERE document_id = ? AND user_id = ?",
        (doc_id, uid),
    )

    db.commit()
    db.close()
    logger.info("PO %s unverified by user %s", po_id, uid)
    return jsonify({"status": "unverified"})


# ---------------------------------------------------------------------------
# Startup — always initialize DB and directories (works with gunicorn too)
# ---------------------------------------------------------------------------

app.config["UPLOAD_FOLDER"].mkdir(exist_ok=True)
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development") == "development"
    app.run(debug=debug, host="0.0.0.0", port=port)
