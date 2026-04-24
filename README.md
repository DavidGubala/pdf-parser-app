# PDF Parse — Split-Tier Architecture

A lightweight web dashboard for uploading PDF files, extracting their content automatically, and exploring the results. PDF processing (Docling) and LLM inference (Ollama) are offloaded to a private MacBook compute node over Tailscale.

## Architecture

```
User
  │
  ▼
Internet
  │
  ▼
┌─────────────────────────────┐
│   Linux Server (pdf-parse)  │  Public-facing Flask app
│   - NGINX / Gunicorn        │  Stores files & metadata in SQLite
│   - SQLite database         │  Proxies compute work to MacBook
│   - No GPU needed           │
└───────────┬─────────────────┘
            │
            ▼
    Tailscale Network (WireGuard)
    100.x.x.x virtual IPs
    No open firewall ports
            │
            ▼
┌─────────────────────────────┐
│   MacBook (Apple Silicon)   │  Private compute node
│   - FastAPI + Docling       │  PDF extraction
│   - Ollama                  │  LLM inference
│   - No inbound ports        │
└─────────────────────────────┘
```

## Why Split-Tier?

| Problem | Before (Linux Monolith) | After (Split-Tier) |
|---|---|---|
| **cuDNN crash** | Fatal on GTX 1060 Pascal | Gone — MacBook runs Docling on MPS/CPU |
| **VRAM starvation** | 3GB insufficient | MacBook has unified memory |
| **Docker build time** | ~3,000s downloading CUDA wheels | ~60s with slim CPU image |
| **Linux GPU needed** | Required | Optional — can run on cheapest VPS |
| **MacBook exposure** | N/A | Zero public open ports |

## Prerequisites

- **Linux server**: Docker, Docker Compose
- **MacBook**: Python 3.10+, Tailscale, Ollama
- **Both machines**: Tailscale installed and connected to the same network

## Quick Start

### 1. MacBook Compute Node

```bash
cd macbook_service

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Pull the LLM model
ollama pull qwen2.5:7b

# Start Ollama (localhost only — remote access goes through FastAPI proxy)
ollama serve

# In another terminal, start the PDF service
./run.sh
```

Verify health:
```bash
curl http://$(tailscale ip -4):8000/health
```

### 2. Linux Web Server

Create `.env` in the project root:

```bash
# Required
PDF_SERVICE_URL=http://100.x.x.x:8000      # MacBook Tailscale IP
PDF_API_KEY=your-shared-secret-key
SECRET_KEY=your-flask-secret-key

# Optional
OLLAMA_URL=http://100.x.x.x:11434          # Not used for LLM anymore (goes through proxy)
OLLAMA_MODEL=qwen2.5:7b
DATABASE_PATH=/app/data/documents.db
```

Deploy with Docker:

```bash
docker-compose up -d --build
```

Or for development:
```bash
docker-compose -f docker-compose.dev.yml up -d --build
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `PDF_SERVICE_URL` | Yes | MacBook FastAPI service URL (Tailscale IP:8000) |
| `PDF_API_KEY` | Yes | Shared Bearer token — must match MacBook's `PDF_API_KEY` |
| `SECRET_KEY` | Yes | Flask session encryption key |
| `OLLAMA_URL` | No | Direct Ollama URL (legacy, now proxied through FastAPI) |
| `OLLAMA_MODEL` | No | Model name (default: `qwen2.5:7b`) |
| `DATABASE_PATH` | No | SQLite path (default: `/app/data/documents.db`) |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/upload` | Upload a PDF file |
| `GET` | `/api/documents` | List all documents |
| `GET` | `/api/documents/<id>` | Get document details and extracted data |
| `DELETE` | `/api/documents/<id>` | Delete a document |
| `GET` | `/api/purchase-orders` | List extracted purchase orders |
| `GET` | `/api/schedule` | Get scheduling dashboard data |
| `POST` | `/api/correct-po` | Submit a PO correction |

## Project Structure

```
pdf-parse/
├── app.py                  # Flask backend (remote PDF client)
├── requirements.txt        # Python dependencies (no Docling/torch)
├── Dockerfile              # Slim CPU image (no CUDA)
├── docker-compose.yml      # Production compose
├── docker-compose.dev.yml  # Development compose
├── .env.example            # Environment variable template
├── seed_user.py            # User account creation
├── macbook_service/        # MacBook compute node
│   ├── main.py             # FastAPI + Docling microservice
│   ├── requirements.txt    # FastAPI, Docling, uvicorn
│   ├── run.sh              # Startup script with Tailscale binding
│   └── README.md           # MacBook setup docs
├── templates/              # HTML templates
├── static/                 # CSS, JS assets
├── uploads/                # Stored PDF files
└── data/                   # SQLite database + logs
```

## Security

- **Network**: MacBook joins Tailscale only. No open firewall ports.
- **Binding**: FastAPI binds to `100.x.x.x`, not `0.0.0.0`. Blocks local Wi-Fi access.
- **Auth**: Strong `PDF_API_KEY` (Bearer token) shared only between Linux and MacBook.
- **Ollama**: Stays on `localhost:11434` — unreachable from network. Accessed only via authenticated FastAPI proxy.
- **Encryption**: Tailscale encrypts all traffic with WireGuard.

## Troubleshooting

### Linux can't reach MacBook

```bash
# From Linux server
ping $(tailscale ip -4 macbook-hostname)
curl http://100.x.x.x:8000/health
```

### Docling not loading on MacBook

```bash
# Reinstall docling
pip install --upgrade docling
python -c "from docling.document_converter import DocumentConverter; print('OK')"
```

### Ollama proxy returns 502

Ollama is not running or not reachable from the FastAPI service. On MacBook:
```bash
curl http://127.0.0.1:11434/api/tags
# Should return list of models. If not:
ollama serve
```

## Tech Stack

- **Backend**: Python, Flask, Gunicorn
- **PDF Parsing**: Docling (on MacBook via FastAPI)
- **LLM**: Ollama (on MacBook, proxied through FastAPI)
- **Frontend**: Vanilla HTML, CSS, JavaScript
- **Database**: SQLite
- **Network**: Tailscale (WireGuard VPN)