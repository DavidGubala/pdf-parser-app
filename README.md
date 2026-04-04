# PDF Parse

A lightweight web dashboard for uploading PDF files, extracting their content automatically, and exploring the results — powered by [Docling](https://www.docling.ai/) and Flask.

## Features

- **Drag-and-drop upload** — drop a PDF onto the dashboard or browse for a file
- **Automatic extraction** — text, tables, and metadata are parsed in the background via Docling
- **Interactive viewer** — switch between extracted text and table views
- **Simple API** — RESTful endpoints for uploading, listing, viewing, and deleting documents

## Quick Start

```bash
# 1. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the application
python app.py
```

Then open **http://localhost:5000** in your browser.

## Project Structure

```
pdf-parse/
├── app.py                  # Flask backend + Docling integration
├── requirements.txt        # Python dependencies
├── templates/
│   └── index.html          # Dashboard page
├── static/
│   ├── css/style.css       # Styles
│   └── js/app.js           # Frontend logic (vanilla JS)
├── uploads/                # Stored PDF files (created at runtime)
└── documents.db            # SQLite database (created at runtime)
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/upload` | Upload a PDF file |
| `GET` | `/api/documents` | List all documents |
| `GET` | `/api/documents/<id>` | Get document details and extracted data |
| `DELETE` | `/api/documents/<id>` | Delete a document |

## Tech Stack

- **Backend**: Python, Flask
- **PDF Parsing**: [Docling](https://www.docling.ai/)
- **Frontend**: Vanilla HTML, CSS, JavaScript
- **Database**: SQLite
