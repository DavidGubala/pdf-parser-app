# Split-Tier Architecture: Linux Web Host + MacBook Compute Node

## Problem Statement

The production Linux server has a **NVIDIA GeForce GTX 1060 3GB** (Pascal architecture). Extensive debugging revealed a fundamental incompatibility:

- **Docling / Transformers** requires **PyTorch >= 2.4**.
- **PyTorch 2.4+ bundles cuDNN 9.x**.
- **Pascal GPUs + cuDNN 9.x** suffer from a known bug causing `CUDNN_STATUS_NOT_INITIALIZED` or `Error 804: forward compatibility was attempted on non supported HW`.
- The card also has only **~2.3GB free VRAM**, which is insufficient for Docling's RT-DETR layout model.

Meanwhile, a new MacBook (Apple Silicon) is available and can run Docling efficiently on CPU/MPS, as well as Ollama via the Neural Engine / Metal GPU.

**Goal:** Keep the public website hosted on the always-on Linux machine, but offload PDF processing (Docling) and LLM inference (Ollama) to the MacBook **without exposing the MacBook to the public internet**.

---

## High-Level Architecture

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

---

## Network Layer: Tailscale (Recommended)

**Why Tailscale instead of SSH tunnels or port forwarding?**

| Requirement | Tailscale | Reverse SSH | Port Forward |
|---|---|---|---|
| No open inbound ports | ✅ | ✅ | ❌ |
| Survives IP changes / roaming | ✅ | ⚠️ | ❌ |
| Automatic NAT traversal | ✅ | ✅ | ❌ |
| Persistent (no babysitting) | ✅ | ❌ | ✅ |
| Encrypted (WireGuard) | ✅ | ✅ | ❌ |
| Free for personal use | ✅ | ✅ | ✅ |

Tailscale assigns each machine a stable `100.x.x.x` address. The Linux server can always reach the MacBook at this IP, even if the MacBook moves between home Wi-Fi and a coffee shop.

### Setup Steps

1. **Install Tailscale** on both machines:
   ```bash
   # Linux
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up

   # macOS
   brew install --cask tailscale
   # Or download from https://tailscale.com/download
   ```
2. **Note the MacBook's Tailscale IP**:
   ```bash
   tailscale ip -4
   # e.g., 100.78.123.45
   ```
3. **(Optional) ACL Lockdown** in Tailscale admin console:
   ```json
   {
     "acls": [
       {
         "action": "accept",
         "src": ["tag:web-server"],
         "dst": ["tag:macbook:8000,11434"]
       }
     ]
   }
   ```

---

## Compute Layer: MacBook Services

The MacBook runs two background services, bound to its Tailscale IP so they are unreachable from the local LAN / coffee shop Wi-Fi.

### A. PDF Processing Microservice (FastAPI + Docling)

Create `~/pdf_service/main.py` on the MacBook:

```python
import os
import tempfile
from fastapi import FastAPI, File, UploadFile, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from docling.document_converter import DocumentConverter

API_KEY = os.getenv("PDF_API_KEY", "change-me-in-production")

app = FastAPI(title="PDF Processing Node")
converter = DocumentConverter()
security = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid token")

@app.post("/process-pdf")
async def process_pdf(
    file: UploadFile = File(...),
    _: HTTPAuthorizationCredentials = Depends(verify_token)
):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = converter.convert(tmp_path)
        return {
            "markdown": result.document.export_to_markdown(),
            "text": result.document.export_to_text(),
        }
    finally:
        os.unlink(tmp_path)
```

**Run it:**

```bash
cd ~/pdf_service
python -m venv venv
source venv/bin/activate
pip install fastapi uvicorn docling

# Bind to Tailscale IP only
export PDF_API_KEY="a-strong-random-secret"
uvicorn main:app --host "$(tailscale ip -4)" --port 8000
```

**Keep it awake (MacBook plugged in):**

```bash
caffeinate -dims uvicorn main:app --host "$(tailscale ip -4)" --port 8000
```

Or use `launchd` / `tmux` / `screen` for persistence.

---

### B. Ollama

Quit the Ollama desktop app (if running) and start the server bound to Tailscale:

```bash
export OLLAMA_HOST="$(tailscale ip -4):11434"
ollama serve
```

If you want Ollama to persist across reboots, create a `launchd` plist or use `tmux`.

---

## Web Layer: Linux Server (pdf-parse)

Your Flask app stops importing Docling locally and instead makes HTTP requests to the MacBook.

### Environment Variables

Add to your `.env` or systemd service definition:

```bash
PDF_SERVICE_URL=http://100.78.123.45:8000   # MacBook Tailscale IP
OLLAMA_URL=http://100.78.123.45:11434
PDF_API_KEY=a-strong-random-secret
```

### Flask Client Helpers

Add to `app.py` (or a new `pdf_client.py`):

```python
import os
import requests

PDF_SERVICE_URL = os.getenv("PDF_SERVICE_URL")
OLLAMA_URL = os.getenv("OLLAMA_URL")
PDF_API_KEY = os.getenv("PDF_API_KEY")

def extract_pdf_remote(file_path: str) -> dict:
    """Send PDF to MacBook for Docling processing."""
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{PDF_SERVICE_URL}/process-pdf",
            files={"file": (os.path.basename(file_path), f, "application/pdf")},
            headers={"Authorization": f"Bearer {PDF_API_KEY}"},
            timeout=300,  # Docling can be slow on large PDFs
        )
        resp.raise_for_status()
        return resp.json()

def ask_ollama_remote(model: str, prompt: str) -> str:
    """Send prompt to MacBook Ollama."""
    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["response"]
```

### Request Flow

1. User uploads PDF to Linux Flask app.
2. Flask stores the file on disk / SQLite metadata.
3. Flask calls `extract_pdf_remote()` → MacBook processes via Docling → returns JSON.
4. (Optional) Flask calls `ask_ollama_remote()` to summarize or classify extracted text.
5. Flask renders the UI with the final data.

---

## Docker Simplification (Linux Server)

Since the Linux server no longer runs Docling or Ollama locally, you can strip all CUDA/GPU complexity from the Dockerfile:

```dockerfile
FROM python:3.11-slim-bookworm

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 5000
CMD ["gunicorn", "-b", "0.0.0.0:5000", "-w", "2", "app:app"]
```

**Benefits:**
- No more multi-gigabyte PyTorch + CUDA downloads.
- Build time drops from ~3,000s to under 60s.
- Image size shrinks dramatically.
- No `nvidia-container-toolkit` or `--gpus` flags needed.

---

## Security Checklist

| Layer | Control |
|---|---|
| **Network** | MacBook joins Tailscale only. Router firewall stays completely closed. |
| **Binding** | MacBook services bind to `100.x.x.x`, **not** `0.0.0.0`. This blocks access from the local coffee shop Wi-Fi. |
| **Auth** | Strong `PDF_API_KEY` (Bearer token) shared only between Linux and MacBook. |
| **Ollama** | Ollama has no built-in auth. If needed, place a tiny FastAPI reverse-proxy in front of it on the MacBook to enforce tokens. |
| **Firewall** | Enable macOS Firewall + Stealth mode as a defense-in-depth backstop. |
| **Encryption** | Tailscale encrypts all traffic in transit with WireGuard. |
| **TLS** | Optional: Tailscale provides HTTPS certificates (`*.ts.net`) for end-to-end TLS inside the overlay network. |

---

## Alternative: Reverse SSH Tunnel

If you prefer **no third-party services**, use `autossh` from the MacBook to the Linux server:

```bash
# On MacBook
autossh -M 0 -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" \
  -R 9001:localhost:8000 \
  -R 9002:localhost:11434 \
  user@linux-server-ip
```

Then Linux points to `http://localhost:9001` and `http://localhost:9002`.

**Caveat:** SSH tunnels are less reliable than Tailscale for roaming laptops. They drop when networks change and require `autossh` to restart.

---

## Why This Architecture Wins

| Problem | Before (Linux Monolith) | After (Split-Tier) |
|---|---|---|
| **cuDNN crash** | Fatal on GTX 1060 Pascal | Gone — MacBook runs Docling on MPS/CPU |
| **VRAM starvation** | 3GB insufficient | MacBook has unified memory (8GB–128GB) |
| **Docker build time** | ~3,000s downloading CUDA wheels | ~60s with slim CPU image |
| **Linux GPU needed** | Required | Optional — can run on cheapest VPS |
| **MacBook exposure** | N/A | Zero public open ports |
| **Ollama performance** | CPU-only on Linux | Apple Silicon Neural Engine / Metal |

---

## Next Steps

1. Install Tailscale on both machines and verify `ping` works both ways.
2. Deploy the FastAPI PDF microservice on the MacBook and test with `curl` from Linux.
3. Update the Linux Flask app to call remote endpoints instead of importing Docling.
4. Strip CUDA/GPU dependencies from `requirements.txt` and `Dockerfile` on Linux.
5. Monitor MacBook power settings so it does not sleep while plugged in.