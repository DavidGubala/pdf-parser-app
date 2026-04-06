import os
import re
import uuid
import json
import sqlite3
import logging
import threading
import functools
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import (
    Flask, render_template, request, jsonify,
    send_from_directory, session, redirect, url_for,
)
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = Path(__file__).parent / "uploads"
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB
app.config["DATABASE"] = Path(__file__).parent / "documents.db"
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me-in-production")
app.permanent_session_lifetime = timedelta(days=30)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
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
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
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

def _parse_markdown_table(md_text):
    """Parse a markdown table into (headers, rows) where rows are dicts."""
    lines = [l for l in md_text.strip().split("\n") if l.strip()]
    if len(lines) < 3:
        return [], []

    def split_row(line):
        parts = line.split("|")
        if parts and not parts[0].strip():
            parts = parts[1:]
        if parts and not parts[-1].strip():
            parts = parts[:-1]
        return [p.strip() for p in parts]

    headers = split_row(lines[0])
    rows = []
    for line in lines[2:]:
        cleaned = line.replace("|", "").replace("-", "").replace(":", "").strip()
        if not cleaned:
            continue
        cells = split_row(line)
        if cells:
            row_dict = {}
            for i, h in enumerate(headers):
                row_dict[h] = cells[i] if i < len(cells) else ""
            rows.append(row_dict)

    return headers, rows


def _try_parse_date(text):
    """Best-effort parse of a date string into YYYY-MM-DD."""
    if not text or not text.strip():
        return ""
    text = text.strip()

    formats = [
        "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y",
        "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
        "%m/%d/%y", "%Y/%m/%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    try:
        from dateutil import parser as dateparser
        dt = dateparser.parse(text, dayfirst=False)
        if dt:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    return text


def _is_line_item_table(headers):
    """Return True if headers look like a PO line-item table."""
    hl = [h.lower().strip() for h in headers]
    return any("line" in h for h in hl) and any("part" in h or "description" in h for h in hl)


def _extract_item_from_table(headers, rows):
    """Extract one item from a multi-row PO line-item table.

    Expected row layout (nVenia-style):
      Row 0  : Line#, Part Number (+ optional desc), Qty, Unit Price, Ext Price
      Row 1+ : Description fragments, shipping labels, due dates scattered
    """
    if not rows:
        return None

    col = {}
    for h in headers:
        hl = h.lower().strip()
        if "part" in hl or "description" in hl:
            col["part"] = h
        elif "order qty" in hl or hl == "qty":
            col["qty"] = h
        elif "unit price" in hl:
            col["price"] = h
        elif hl == "line":
            col["line"] = h

    first = rows[0]

    # --- Part number + possible inline description ---
    part_text = first.get(col.get("part", ""), "").strip()
    item_name = part_text
    description = ""

    pm = re.match(r"^([A-Z0-9][-A-Z0-9]*)\s+(.+)$", part_text, re.IGNORECASE)
    if pm:
        item_name = pm.group(1)
        description = pm.group(2).strip()

    # --- Description from later rows (Line column often has it) ---
    if not description and len(rows) > 1 and "line" in col:
        raw = rows[1].get(col["line"], "").strip()
        desc = re.sub(r"\s*-\s*Shipping.*$", "", raw, flags=re.IGNORECASE).strip()
        desc = re.sub(r"^-\s*", "", desc).strip()
        desc = re.sub(r"\s*-\s*$", "", desc).strip()
        if desc and not _DATE_RE.match(desc) and desc.lower() not in ("quantity", "tax", ""):
            description = desc

    # --- Quantity: numeric part of "EA 1.00" / "EA Each 2.00" ---
    qty_raw = first.get(col.get("qty", ""), "").strip()
    qm = re.search(r"(\d+\.?\d*)", qty_raw)
    quantity = qm.group(1) if qm else qty_raw

    # --- Unit price: number from "42.00000/1" ---
    price_raw = first.get(col.get("price", ""), "").strip()
    upm = re.search(r"([\d,]+\.?\d*)", price_raw)
    unit_price = ""
    if upm:
        try:
            unit_price = f"{float(upm.group(1).replace(',', '')):.2f}"
        except ValueError:
            unit_price = upm.group(1)

    # --- Due date: scan every cell in rows after the first for a date ---
    due_date = ""
    for row in rows[1:]:
        for val in row.values():
            dm = _DATE_RE.search(str(val))
            if dm:
                due_date = _try_parse_date(dm.group(1))
                break
        if due_date:
            break

    if not item_name:
        return None

    return {
        "item_name": item_name,
        "description": description,
        "due_date": due_date,
        "quantity": quantity,
        "unit_price": unit_price,
    }


def extract_po_data(doc_id, text_content, tables_data):
    """Extract Purchase Order metadata + line items and persist to DB."""
    logger.info("Extracting PO data for document %s", doc_id)

    company_name = ""
    po_number = ""
    po_date = ""
    items = []

    # --- metadata from the full markdown text ---
    if text_content:
        # PO Number — "PO Number: | 378201" or inline "PO Number: 378201"
        m = re.search(r"PO\s+Number\s*[:\s|]+\s*(\d+)", text_content, re.IGNORECASE)
        if m:
            po_number = m.group(1)

        # Customer/buyer — the company that issued the PO (Ship To)
        m = re.search(r"Ship\s*To:\s*([A-Za-z][\w]+)", text_content, re.IGNORECASE)
        if m:
            company_name = m.group(1)

        if not company_name:
            m = re.search(r"Bill\s*To:\s*([A-Za-z][\w]+)", text_content, re.IGNORECASE)
            if m:
                company_name = m.group(1)

        # Order Date
        m = re.search(r"Order\s*Date:\s*(\d{1,2}/\d{1,2}/\d{2,4})", text_content, re.IGNORECASE)
        if m:
            po_date = _try_parse_date(m.group(1))

    # --- line items: each item is its own table with multi-row layout ---
    if tables_data:
        for table_info in tables_data:
            md = table_info.get("markdown", "")
            if not md:
                continue
            headers, rows = _parse_markdown_table(md)
            if not headers or not rows:
                continue
            if not _is_line_item_table(headers):
                continue

            item = _extract_item_from_table(headers, rows)
            if item:
                items.append(item)

    # --- persist ---
    po_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()

    db = get_db()
    db.execute(
        """INSERT INTO purchase_orders (id, document_id, company_name, po_number, po_date, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (po_id, doc_id, company_name, po_number, po_date, now),
    )
    for item in items:
        db.execute(
            """INSERT INTO po_items (po_id, item_name, description, due_date, quantity, unit_price)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (po_id, item["item_name"], item["description"], item["due_date"],
             item["quantity"], item["unit_price"]),
        )
    db.commit()
    db.close()

    logger.info(
        "PO extracted for %s: company=%r, po_number=%r, %d item(s)",
        doc_id, company_name, po_number, len(items),
    )


# ---------------------------------------------------------------------------
# PDF processing with Docling (runs in background thread)
# ---------------------------------------------------------------------------

def process_pdf(doc_id: str, filepath: str):
    """Parse a PDF via Docling and store results in the database."""
    logger.info("Starting Docling processing for %s (%s)", doc_id, filepath)
    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(filepath)
        doc = result.document

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

        db = get_db()
        db.execute(
            """UPDATE documents
               SET status='completed', text_content=?, tables_json=?, page_count=?
               WHERE id=?""",
            (text_content, json.dumps(tables), page_count, doc_id),
        )
        db.commit()
        db.close()
        logger.info("Docling processing completed for %s — %d table(s) found", doc_id, len(tables))

        extract_po_data(doc_id, text_content, tables)

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
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"File type '.{ext}' not allowed. Upload a PDF."}), 400

    doc_id = uuid.uuid4().hex[:12]
    safe_name = secure_filename(file.filename)
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

    thread = threading.Thread(target=process_pdf, args=(doc_id, str(filepath)), daemon=True)
    thread.start()

    logger.info("Uploaded %s as %s — processing started", file.filename, doc_id)
    return jsonify({"id": doc_id, "filename": file.filename, "status": "processing"}), 201


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
    row = db.execute("SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, uid)).fetchone()
    db.close()
    if row is None:
        return jsonify({"error": "Document not found"}), 404
    return jsonify(_row_to_dict(row))


@app.route("/api/documents/<doc_id>/pdf", methods=["GET"])
@login_required
def get_document_pdf(doc_id):
    uid = current_user_id()
    db = get_db()
    row = db.execute("SELECT filename FROM documents WHERE id=? AND user_id=?", (doc_id, uid)).fetchone()
    db.close()
    if row is None:
        return jsonify({"error": "Document not found"}), 404
    return send_from_directory(app.config["UPLOAD_FOLDER"], row["filename"], mimetype="application/pdf")


@app.route("/api/documents/<doc_id>", methods=["DELETE"])
@login_required
def delete_document(doc_id):
    uid = current_user_id()
    db = get_db()
    row = db.execute("SELECT filename FROM documents WHERE id=? AND user_id=?", (doc_id, uid)).fetchone()
    if row is None:
        db.close()
        return jsonify({"error": "Document not found"}), 404

    filepath = app.config["UPLOAD_FOLDER"] / row["filename"]
    if filepath.exists():
        filepath.unlink()

    po_rows = db.execute("SELECT id FROM purchase_orders WHERE document_id=?", (doc_id,)).fetchall()
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
    pos = db.execute("""
        SELECT po.*, d.original_name AS document_name
        FROM purchase_orders po
        JOIN documents d ON po.document_id = d.id
        WHERE d.user_id = ?
        ORDER BY po.created_at DESC
    """, (uid,)).fetchall()

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
    po = db.execute("""
        SELECT po.*, d.original_name AS document_name
        FROM purchase_orders po
        JOIN documents d ON po.document_id = d.id
        WHERE po.id = ? AND d.user_id = ?
    """, (po_id, uid)).fetchone()

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

    items = db.execute("""
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
    """, (uid,)).fetchall()

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

    return jsonify({
        "summary": {
            "total_pos": total_pos,
            "total_items": total_items,
            "overdue": overdue,
            "due_this_week": due_this_week,
            "upcoming": upcoming,
        },
        "items": items_list,
    })


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.config["UPLOAD_FOLDER"].mkdir(exist_ok=True)
    init_db()
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development") == "development"
    app.run(debug=debug, host="0.0.0.0", port=port)
