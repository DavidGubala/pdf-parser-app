"""
MacBook PDF Processing Microservice

FastAPI service that runs on the MacBook and handles PDF extraction
via Docling. Bound to Tailscale IP for security.

Usage:
    export PDF_API_KEY="your-secret-key"
    uvicorn main:app --host "$(tailscale ip -4)" --port 8000
"""

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


@app.get("/config")
async def get_config():
    """Return current service configuration (sanitized)."""
    return {
        "pdf_service_url": PDF_SERVICE_URL,
        "api_key_set": bool(API_KEY and API_KEY != "change-me-in-production"),
        "docling_available": converter is not None,
        "python_version": __import__("sys").version,
    }
