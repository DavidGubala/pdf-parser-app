# MacBook Compute Node Services

## Overview

This directory contains the services that run on the MacBook compute node:

1. **PDF Processing Microservice** (FastAPI + Docling) - Handles PDF extraction
2. **Ollama Proxy** (optional) - Proxies LLM inference requests to local Ollama instance

## Prerequisites

- macOS with Apple Silicon (M1/M2/M3/M4)
- Tailscale installed and connected
- Python 3.10+

## Quick Start

### 1. Install Dependencies

```bash
cd macbook_service
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables

```bash
# API key for authentication between Linux server and MacBook
export PDF_API_KEY="a-strong-random-secret-key"

# Ollama URL (optional, only needed for LLM proxy)
export OLLAMA_URL="http://127.0.0.1:11434"
```

### 3. Start Ollama (Optional — only if you need LLM features)

Ollama must be running **before** you start the PDF service if you want PO extraction / chat features.

**Option A: Bind to all interfaces** (recommended — works with both local apps and remote Linux)

```bash
OLLAMA_HOST="0.0.0.0:11434" ollama serve
```

This lets your MacBook's Ollama desktop app, local scripts, and the remote Linux server all connect. Your LAN can't reach it if macOS Firewall is on.

**Option B: Bind to Tailscale IP only** (more restrictive — breaks local apps)

```bash
OLLAMA_HOST="$(tailscale ip -4):11434" ollama serve
```

Use this if you want **zero** LAN exposure. Local Ollama desktop app and scripts will need to use `http://100.x.x.x:11434` explicitly.

> ⚠️ **Security:** If using Option A on public Wi-Fi, enable macOS Firewall (System Settings → Network → Firewall). `0.0.0.0` makes Ollama reachable from your LAN — the Firewall blocks the WAN.

### 4. Start the PDF Service

```bash
# Bind to Tailscale IP for security
uvicorn main:app --host "$(tailscale ip -4)" --port 8000
```

Or use the startup script:

```bash
chmod +x run.sh
./run.sh
```

### 5. Verify Health

```bash
curl http://$(tailscale ip -4):8000/health
```

Expected response:

```json
{
  "status": "healthy",
  "docling_available": true,
  "api_key_set": true
}
```

## Keeping the Service Running

### Option 1: tmux (Recommended for testing)

```bash
tmux new -s pdf-service
source venv/bin/activate
uvicorn main:app --host "$(tailscale ip -4)" --port 8000
# Ctrl+B, D to detach
```

### Option 2: launchd (Recommended for production)

Create `~/Library/LaunchAgents/com.pdfservice.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.pdfservice</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>cd ~/Dev/pdf-parser-app/macbook_service && source venv/bin/activate && uvicorn main:app --host "$(tailscale ip -4)" --port 8000</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/pdfservice.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/pdfservice.err</string>
</dict>
</plist>
```

Load the service:

```bash
launchctl load ~/Library/LaunchAgents/com.pdfservice.plist
```

### Option 3: caffeinate (Keep MacBook awake)

```bash
caffeinate -dims uvicorn main:app --host "$(tailscale ip -4)" --port 8000
```

## Testing

### Test PDF Processing

```bash
curl -X POST http://$(tailscale ip -4):8000/process-pdf \
  -H "Authorization: Bearer $PDF_API_KEY" \
  -F "file=@/path/to/sample.pdf" \
  http://100.x.x.x:8000/process-pdf \
  -H "Authorization: Bearer $PDF_API_KEY" \
  -F "file=@sample.pdf"
```

### Test Ollama Proxy (if Ollama is running)

```bash
curl -X POST http://$(tailscale ip -4):8000/process-ollama \
  -H "Authorization: Bearer $PDF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen2.5", "prompt": "Hello, how are you?"}'
```

If this returns `502 Ollama error`, Ollama is not running or not reachable. Check `OLLAMA_URL` in your env.

## Security Notes

- The **PDF service** binds to Tailscale IP (`100.x.x.x`), **not** `0.0.0.0` — unreachable from local Wi-Fi
- **Ollama** binds to `0.0.0.0` (recommended) so local apps still work; macOS Firewall blocks WAN access
- Strong `PDF_API_KEY` is required for all PDF service requests
- Ollama has **no built-in auth** — if you need auth, put a reverse proxy in front of it
- Enable macOS Firewall + Stealth mode as defense-in-depth

## Troubleshooting

### Service won't start

```bash
# Check Tailscale IP
tailscale ip -4

# Check if port is in use
lsof -i :8000

# Check logs
cat pdf_service.log
```

### Docling not loading

```bash
# Reinstall docling
pip install --upgrade docling

# Verify installation
python -c "from docling.document_converter import DocumentConverter; print('OK')"
```

### Connection refused from Linux server

1. Verify Tailscale is running: `tailscale status`
2. Check MacBook firewall: System Settings → Network → Firewall
3. Verify service is bound to Tailscale IP: `lsof -i :8000`
4. Test locally first: `curl http://127.0.0.1:8000/health`

## Logs

Service logs are written to:

- `pdf_service.log` - Current service log file
- `/tmp/pdfservice.log` - If running via launchd
- Console output if running via tmux/terminal