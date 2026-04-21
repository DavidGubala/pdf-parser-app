import functools
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

from docling.datamodel.pipeline_options import PipelineOptions
from docling.document_converter import DocumentConverter

# Initialize Docling Converter as a singleton with GPU acceleration
pipeline_options = PipelineOptions(accelerator="cuda")
converter = DocumentConverter(pipeline_options=pipeline_options)

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

SYSTEM_PROMPT = """You are a professional Purchase Order (PO) extraction expert.
Your task is to extract structured data from the provided document content.
You will be provided with three versions of the document:
1. Unstructured Text: A flat reading of the document.
2. Markdown Text: A structured markdown representation.
3. Tables: Specific tables extracted as markdown.

Extract the following information into a strict JSON format:
- company_name: The name of the company that issued the PO (the buyer/customer).
- po_number: The Purchase Order number.
- po_date: The date of the PO in YYYY-MM-DD format.
- items: A list of line items, each containing:
    - item_name: The part number or primary identifier.
    - description: The full description of the item.
    - quantity: The ordered quantity.
    - unit_price: The price per unit.
    - due_date: The required delivery date in YYYY-MM-DD format.

If a value is not found, use null. Return ONLY the JSON object.
"""


def query_ollama(prompt, system_prompt=SYSTEM_PROMPT):
    """Query the local Ollama server for structured extraction."""
    url = (
        os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434") + "/api/chat"
    )
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": "json",
    }

    try:
        response = requests.post(url, json=payload, timeout=120)
        response.raise_for_status()
        return response.json()["message"]["content"]
    except Exception as e:
        logger.error("Ollama API error: %s", e)
        return None


def get_relevant_corrections(doc_id):
    """Retrieve recent corrections to use as few-shot examples."""
    db = get_db()
    # Get the last 5 corrections to provide a variety of 'lessons'
    corrections = db.execute(
        "SELECT field_name, original_value, corrected_value FROM po_corrections ORDER BY timestamp DESC LIMIT 5"
    ).fetchall()
    db.close()

    if not corrections:
        return ""

    lessons = [
        "The following mistakes were made in previous extractions. Please avoid them:"
    ]
    for c in corrections:
        lessons.append(
            f"- Field '{c['field_name']}': previously extracted as '{c['original_value']}', but corrected to '{c['corrected_value']}'"
        )

    return "\n".join(lessons)


def build_extraction_prompt(
    text_content, tables_json, unstructured_text, lessons_learned=""
):
    """Combine all available data sources and lessons into a single prompt for the LLM."""
    tables = json.loads(tables_json) if isinstance(tables_json, str) else tables_json
    tables_md = "\n\n".join([t.get("markdown", "") for t in tables])

    lessons_section = (
        f"\n### LESSONS FROM PREVIOUS EXTRACTIONS\n{lessons_learned}\n"
        if lessons_learned
        else ""
    )
    prompt = f"""Please extract the PO data from the following sources:
{lessons_section}
 ### UNSTRUCTURED TEXT
 {unstructured_text}

### MARKDOWN STRUCTURE
{text_content}

### EXTRACTED TABLES
{tables_md}
"""
    return prompt


def extract_po_with_llm(doc_id, text_content, tables_json, unstructured_text):
    """Orchestrate the LLM extraction and persist results to the database."""
    logger.info("Starting LLM extraction for document %s", doc_id)

    # Retrieve lessons learned from the correction log
    lessons = get_relevant_corrections(doc_id)

    prompt = build_extraction_prompt(
        text_content, tables_json, unstructured_text, lessons
    )

    start_time = time.time()
    raw_response = query_ollama(prompt)
    latency = time.time() - start_time

    # Log the raw interaction for debugging and auditing
    db = get_db()
    db.execute(
        """INSERT INTO llm_logs (document_id, prompt, response, latency, timestamp)
           VALUES (?, ?, ?, ?, ?)""",
        (doc_id, prompt, raw_response, latency, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    db.close()

    if not raw_response:
        logger.error("No response from LLM for document %s", doc_id)
        return

    try:
        data = json.loads(raw_response)

        po_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()

        db = get_db()
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
        db.close()
        logger.info("LLM extraction successful for %s", doc_id)

    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Failed to parse LLM JSON for %s: %s", doc_id, e)


# ---------------------------------------------------------------------------
# PDF processing with Docling (runs in background thread)
# ---------------------------------------------------------------------------


def process_pdf(doc_id: str, filepath: str):
    """Parse a PDF via Docling and store results in the database."""
    logger.info("Starting PDF processing for %s (%s)", doc_id, filepath)
    overall_start = time.time()
    try:
        # --- Docling Phase ---
        docling_start = time.time()
        logger.info("Running Docling conversion for %s...", doc_id)

        result = converter.convert(filepath)
        doc = result.document
        docling_latency = time.time() - docling_start
        logger.info(
            "Docling conversion completed for %s in %.2fs", doc_id, docling_latency
        )

        text_content = doc.export_to_markdown()

        tables = []
        for i, table in enumerate(doc.tables):
            table_data = {
                "index": i,
                "markdown": table.export_to_markdown(),
            }
            try:
                df = table.export_to_dataframe()
                table_data["html"] = df.to_html(classes="data-table", index=False)
                table_data["rows"] = len(df)
                table_data["cols"] = len(df.columns)
            except Exception:
                table_data["html"] = f"<pre>{table.export_to_markdown()}</pre>"
            tables.append(table_data)

        page_count = doc.num_pages() if callable(getattr(doc, "num_pages", None)) else 0

        # Get flat text reading using Unstructured
        unstructured_text = ""
        try:
            unstructured_start = time.time()
            from unstructured.partition.pdf import partition_pdf

            elements = partition_pdf(filename=filepath)
            unstructured_text = "\n\n".join([str(el) for el in elements])
            unstructured_latency = time.time() - unstructured_start
            logger.info(
                "Unstructured processing completed for %s in %.2fs",
                doc_id,
                unstructured_latency,
            )
        except Exception as ue:
            logger.warning("Unstructured processing failed for %s: %s", doc_id, ue)

        db = get_db()
        db.execute(
            """UPDATE documents
               SET status='completed', text_content=?, tables_json=?, unstructured_text=?, page_count=?
               WHERE id=?""",
            (text_content, json.dumps(tables), unstructured_text, page_count, doc_id),
        )
        db.commit()
        db.close()
        overall_latency = time.time() - overall_start
        logger.info(
            "Processing completed for %s in %.2fs — %d table(s) found",
            doc_id,
            overall_latency,
            len(tables),
        )

        # Trigger LLM extraction now that data is prepared
        extract_po_with_llm(doc_id, text_content, json.dumps(tables), unstructured_text)

    except Exception as exc:
        logger.exception("Docling processing failed for %s", doc_id)
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

    doc_id = uuid.uuid4().hex[:12]
    safe_name = secure_filename(filename)
    stored_name = f"{doc_id}_{safe_name}"
    filepath = app.config["UPLOAD_FOLDER"] / stored_name
    file.save(str(filepath))

    now = datetime.now(timezone.utc).isoformat()
    uid = current_user_id()
    db = get_db()
    db.execute(
        """INSERT INTO documents (id, user_id, filename, original_name, upload_time, status)
           VALUES (?, ?, ?, ?, ?, 'processing')""",
        (doc_id, uid, stored_name, file.filename, now),
    )
    db.commit()
    db.close()

    thread = threading.Thread(
        target=process_pdf, args=(doc_id, str(filepath)), daemon=True
    )
    thread.start()

    logger.info("Uploaded %s as %s — processing started", file.filename, doc_id)
    return jsonify(
        {"id": doc_id, "filename": file.filename, "status": "processing"}
    ), 201


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
            field = corr.get("field_name")
            new_val = corr.get("new_value")

            if entity_type == "PO":
                if field not in {"company_name", "po_number", "po_date"}:
                    continue
                # Get original value
                row = db.execute(
                    f"SELECT {field} FROM purchase_orders WHERE id = ?", (entity_id,)
                ).fetchone()
                original_val = row[0] if row else None

                # Update
                db.execute(
                    f"UPDATE purchase_orders SET {field} = ? WHERE id = ?",
                    (new_val, entity_id),
                )
            elif entity_type == "ITEM":
                if field not in {
                    "item_name",
                    "description",
                    "due_date",
                    "quantity",
                    "unit_price",
                }:
                    continue
                # Get original value
                row = db.execute(
                    f"SELECT {field} FROM po_items WHERE id = ?", (entity_id,)
                ).fetchone()
                original_val = row[0] if row else None

                # Update
                db.execute(
                    f"UPDATE po_items SET {field} = ? WHERE id = ?",
                    (new_val, entity_id),
                )
            else:
                continue

            # Log correction
            db.execute(
                """INSERT INTO po_corrections
                   (document_id, entity_type, entity_id, field_name, original_value, corrected_value, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, entity_type, entity_id, field, original_val, new_val, now),
            )

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


# ---------------------------------------------------------------------------
# Startup — always initialize DB and directories (works with gunicorn too)
# ---------------------------------------------------------------------------

app.config["UPLOAD_FOLDER"].mkdir(exist_ok=True)
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development") == "development"
    app.run(debug=debug, host="0.0.0.0", port=port)
