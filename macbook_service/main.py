"""
MacBook PDF Processing Microservice

FastAPI service that runs on the MacBook and handles PDF extraction
via Docling and Unstructured. Bound to Tailscale IP for security.

Usage:
    export PDF_API_KEY="your-secret-key"
    uvicorn main:app --host "$(tailscale ip -4)" --port 8000
"""

import json
import logging
import logging.handlers
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

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

# --------------- Configuration ---------------

API_KEY = os.getenv("PDF_API_KEY", "change-me-in-production")
PDF_SERVICE_URL = os.getenv("PDF_SERVICE_URL", "http://127.0.0.1:8000")

# --------------- Docling Setup ---------------

try:
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    logger.info("Docling converter initialized successfully")
except ImportError as e:
    logger.error("Failed to import Docling: %s", e)
    converter = None


# --------------- FastAPI App ---------------


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000
        client = request.client.host if request.client else "-"
        logger.info(
            "client=%s %s %s -> %s (%.0fms)",
            client,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response


app = FastAPI(
    title="PDF Processing Node",
    description="Remote PDF extraction microservice for MacBook compute node",
    version="1.0.0",
)

app.add_middleware(RequestLoggingMiddleware)

security = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify Bearer token authentication."""
    if credentials.credentials != API_KEY:
        logger.warning(
            "Unauthorized access attempt from %s",
            credentials.credentials[:8] if credentials.credentials else "None",
        )
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return credentials


# --------------- Pydantic Models ---------------


class ProcessingResult(BaseModel):
    """Response model for PDF processing results."""

    markdown: str
    text: str
    unstructured_text: str
    page_count: int
    table_count: int
    processing_time_ms: float


class StatusResponse(BaseModel):
    """Health check response model."""

    status: str
    docling_available: bool
    api_key_set: bool


# --------------- Endpoints ---------------


@app.get("/health")
async def health_check() -> StatusResponse:
    """Health check endpoint."""
    return StatusResponse(
        status="healthy",
        docling_available=converter is not None,
        api_key_set=bool(API_KEY and API_KEY != "change-me-in-production"),
    )


@app.post(
    "/process-pdf",
    response_model=ProcessingResult,
    dependencies=[Depends(verify_token)],
)
async def process_pdf(file: UploadFile = File(...)) -> ProcessingResult:
    """
    Process a PDF file using Docling and return extracted content.

    Args:
        file: PDF file to process

    Returns:
        ProcessingResult with markdown, text, page count, table count, and timing
    """
    if converter is None:
        raise HTTPException(
            status_code=503,
            detail="Docling not available. Install docling package on MacBook.",
        )

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    processing_start = time.time()

    # Write file to temp location for Docling
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".pdf", dir=str(Path(__file__).parent)
        ) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
            file_size_kb = len(content) / 1024
            logger.info("Processing PDF: %s (%.1f KB)", file.filename, file_size_kb)

        # Run Docling conversion
        result = converter.convert(tmp_path)
        doc = result.document

        # Export content
        markdown_content = doc.export_to_markdown()
        text_content = doc.export_to_text()

        # Extract unstructured text using Unstructured
        unstructured_text = ""
        try:
            unstructured_start = time.time()
            from unstructured.partition.pdf import partition_pdf

            elements = partition_pdf(filename=tmp_path)
            unstructured_text = "\n\n".join([str(el) for el in elements])
            unstructured_latency = time.time() - unstructured_start
            logger.info(
                "Unstructured processing completed for %s in %.2fs",
                file.filename,
                unstructured_latency,
            )
        except Exception as ue:
            logger.warning(
                "Unstructured processing failed for %s: %s", file.filename, ue
            )
            # Fall back to docling plain text
            unstructured_text = text_content

        # Extract page count (handle method vs property)
        if callable(getattr(doc, "num_pages", None)):
            page_count = doc.num_pages()
        else:
            page_count = 0

        # Count tables
        table_count = 0
        if hasattr(doc, "tables") and doc.tables:
            table_count = (
                len(doc.tables) if isinstance(doc.tables, (list, tuple)) else 1
            )

        processing_time_ms = (time.time() - processing_start) * 1000

        # Cleanup temp file before building response
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
                tmp_path = None
            except OSError:
                pass

        logger.info(
            "Completed PDF: %s | pages=%d | tables=%d | %.1fms",
            file.filename,
            page_count,
            table_count,
            processing_time_ms,
        )

        return ProcessingResult(
            markdown=markdown_content,
            text=text_content,
            unstructured_text=unstructured_text,
            page_count=page_count,
            table_count=table_count,
            processing_time_ms=processing_time_ms,
        )

    except Exception as e:
        logger.exception("Error processing PDF: %s", file.filename)
        raise HTTPException(
            status_code=500,
            detail=f"PDF processing failed: {str(e)}",
        )
    finally:
        # Cleanup temp file
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class ChatRequest(BaseModel):
    model: str
    messages: list
    stream: bool = False
    format: str | None = None


class ExtractPORequest(BaseModel):
    markdown: str
    unstructured_text: str
    model: str = "qwen2.5:7b"


SYSTEM_PROMPT = """You are a professional Purchase Order (PO) extraction expert.
Your task is to extract structured data from the provided document content.
You will be provided with two versions of the document:
1. Unstructured Text: A flat reading of the document.
2. Markdown Text: A structured markdown representation.

Extract the following information into a strict JSON format:
- company_name: The name of the company that issued the PO (the buyer/customer).
- po_number: The Purchase Order number.
- po_date: The date of the PO in YYYY-MM-DD format.
- items: A list of line items, each containing:
    - item_name: The part number or primary identifier. This MUST be a short string of letters and numbers (e.g., "ABC-123", "PN45678"). Do NOT include descriptive sentences or full words here.
    - description: The full description of the item. This is where actual descriptive words belong (e.g., "Stainless steel bracket, 4-inch").
    - quantity: The ordered quantity.
    - unit_price: The price per unit.
    - due_date: The required delivery date in YYYY-MM-DD format.

DUE DATE EXTRACTION GUIDANCE:
- Look for due dates in multiple places: column headers labeled "Required Delivery Date", "Ship Date", "Due Date", or similar.
- Due dates may appear per-item in a table row, OR as a single date for the entire PO (often near the top or in a header section).
- If every item shares the same due date, repeat that date for each item.
- If no due date is found for an item, use null.
- Common date formats in POs: MM/DD/YYYY, MMM DD YYYY, DD-MMM-YYYY. Convert all to YYYY-MM-DD.
- Watch for relative dates like "ASAP" or "TBD" — use null for these.

If a value is not found, use null. Return ONLY the JSON object.
"""


@app.post("/process-ollama")
async def process_ollama(
    chat_req: ChatRequest,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
) -> dict:
    """Proxy Ollama /api/chat requests from the Linux server."""
    try:
        import requests as req

        ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
        payload = {
            "model": chat_req.model,
            "messages": chat_req.messages,
            "stream": chat_req.stream,
        }
        if chat_req.format:
            payload["format"] = chat_req.format

        logger.info(
            "Proxying Ollama chat request: model=%s messages=%d",
            chat_req.model,
            len(chat_req.messages),
        )
        resp = req.post(
            f"{ollama_url}/api/chat",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.exception("Ollama proxy error")
        raise HTTPException(status_code=502, detail=f"Ollama error: {str(e)}")


@app.post("/extract-po")
async def extract_po(
    req: ExtractPORequest,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
) -> dict:
    """Extract Purchase Order data from text using local Ollama."""
    try:
        import requests

        ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")

        prompt = f"""Please extract the PO data from the following sources:

### UNSTRUCTURED TEXT
{req.unstructured_text}

### MARKDOWN STRUCTURE
{req.markdown}

Extract into strict JSON with: company_name, po_number, po_date, items (each with item_name, description, quantity, unit_price, due_date).

CRITICAL:
- item_name must be ONLY a short part number / SKU (letters, numbers, dashes). Example: "ABC-123", "PN45678". Do NOT put full sentences or descriptive text here.
- description is where the actual descriptive words belong (e.g., "Stainless steel bracket, 4-inch").
- due_date: Check per-item table rows for delivery dates. If the PO has a single global due date (e.g., in a header section like "Required Delivery Date: 05/15/2026"), apply that same date to every item. Convert all dates to YYYY-MM-DD format.

Return ONLY the JSON object."""

        payload = {
            "model": req.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "json",
        }

        logger.info("Extracting PO via Ollama: model=%s", req.model)
        resp = requests.post(
            f"{ollama_url}/api/chat",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_content = data["message"]["content"]

        # Parse and validate JSON
        po_data = json.loads(raw_content)
        logger.info(
            "PO extraction successful: company=%s items=%d",
            po_data.get("company_name", "N/A"),
            len(po_data.get("items", [])),
        )
        return po_data

    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM JSON response: %s", e)
        raise HTTPException(status_code=502, detail=f"Invalid JSON from LLM: {str(e)}")
    except Exception as e:
        logger.exception("PO extraction failed")
        raise HTTPException(status_code=502, detail=f"PO extraction failed: {str(e)}")


@app.get("/config")
async def get_config():
    """Return current service configuration (sanitized)."""
    return {
        "pdf_service_url": PDF_SERVICE_URL,
        "api_key_set": bool(API_KEY and API_KEY != "change-me-in-production"),
        "docling_available": converter is not None,
        "python_version": __import__("sys").version,
    }
