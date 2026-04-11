# Codebase Review Findings

## 1. General Architectural Review

### Current State
The application follows a **monolithic architecture** where the web server (Flask), database management (SQLite), business logic (PO extraction), and background processing (threading) are all tightly coupled within `app.py`.

### Observations
- **Tight Coupling:** Extraction logic, database schema definitions, and route handlers are all in one file. This makes the codebase harder to test in isolation and more difficult to maintain as features grow.
- **Background Processing:** Using `threading.Thread` for PDF processing works for low-concurrency but lacks robustness. If the server restarts, pending tasks are lost.
- **Database Access:** Using raw SQL queries increases the risk of errors and makes schema migrations more manual and error-prone.

### Recommendations
- **Modularization:** Break `app.py` into a structured package (e.g., `models/`, `services/`, `routes/`, `utils/`).
- **Task Queue:** For production, consider a dedicated worker pattern using **Redis** and **Celery** to provide persistence and retries.
- **ORM Integration:** Transitioning to **SQLAlchemy** would provide better abstraction, easier migrations (via Alembic), and improved security.

## 2. Code Quality and Readability

### Current State
The code is generally well-written, follows standard Python conventions, and includes helpful logging.

### Observations
- **Logging:** Excellent implementation of a dual-handler logging system.
- **Error Handling:** Good global error handlers for both HTTP and generic exceptions.
- **Regex Complexity:** PO extraction relies heavily on complex regular expressions, which can be brittle if PDF layouts change.
- **Inconsistent Type Hinting:** Type hints are used sporadically.

### Recommendations
- **Standardize Type Hinting:** Implement consistent type hinting across all functions.
- **Robust Parsing:** Consider a more "schema-aware" approach (e.g., using Pydantic) to validate extracted data.
- **Docstring Consistency:** Ensure all public-facing functions have descriptive docstrings.

## 3. Performance Optimization

### Current State
The most computationally expensive part is the Docling PDF conversion, which is correctly moved to a background thread.

### Observations
- **Converter Instantiation:** `DocumentConverter()` is instantiated inside `process_pdf`, causing the converter and its models to reload on every upload.
- **Database Connections:** Frequent opening and closing of database connections.
- **Memory Management:** Large PDFs could lead to high memory usage as text and table data are stored as large blobs.

### Recommendations
- **Singleton Pattern for Converter:** Move the `DocumentConverter` instantiation outside the `process_pdf` function to reuse it.
- **Connection Pooling:** Use a connection pool (e.g., via SQLAlchemy) to reduce overhead.
- **Data Chunking:** For extremely large documents, consider processing and storing data in chunks.