# MacBook Compute Node Services

## Overview

This directory contains the services that run on the MacBook compute node:

1. **PDF Processing Microservice** (FastAPI + Docling) — Handles PDF extraction on port 8000
2. **Ollama Proxy** — Forwards LLM inference requests from the Linux server to the local Ollama instance

The Linux server never talks directly to Ollama. All LLM requests go through the authenticated FastAPI service (`/process-ollama`), which proxies to `localhost:11434`.

## Prerequisites

- macOS with Apple Silicon (M1/M2/M3/M4)
- Tailscale installed and connected on both MacBook and Linux server
- Python 3.10+
- Ollama installed (`brew install ollama` or from https://ollama.com/download)

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
# This must match the PDF_API_KEY set on the Linux server
export PDF_API_KEY="a-strong-random-secret-key"
```

### 3. Pull the Ollama Model

```bash
ollama pull qwen2.5:7b
```

Verify it's available:

```bash
ollama list
```

### 4. Start Ollama (localhost only)

Ollama must be running before you start the PDF service if you want PO extraction / chat features.

**Do NOT set `OLLAMA_HOST`.** Just run:

```bash
ollama serve
```

This binds to `127.0.0.1:11434` (localhost only). The FastAPI proxy will forward remote requests to it. No network exposure needed.

### 5. Start the PDF Service

In a separate terminal:

```bash
chmod +x run.sh
./run.sh
```

This will:
- Detect your Tailscale IP and bind to it
- Check if Ollama is running
- Start the FastAPI service with daily log rotation

### 6. Verify Health

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
cd macbook_service
source venv/bin/activate
./run.sh
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
        <string>cd ~/Dev/pdf-parser-app/macbook_service && source venv/bin/activate && ./run.sh</string>
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

### Option 3: caffeinate (Keep MacBook awake while plugged in)

```bash
caffeinate -dims ./run.sh
```

## Testing

### Test PDF Processing

```bash
curl -X POST "http://$(tailscale ip -4):8000/process-pdf" \
  -H "Authorization: Bearer $PDF_API_KEY" \
  -F "file=@/path/to/sample.pdf"
```

### Test Ollama Proxy

```bash
curl -X POST "http://$(tailscale ip -4):8000/process-ollama" \
  -H "Authorization: Bearer $PDF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:7b",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "Hello!"}
    ],
    "stream": false,
    "format": "json"
  }'
```

If this returns `502 Ollama error`, Ollama is not running. Start it with `ollama serve`.

## Security Notes

- The **PDF service** binds to Tailscale IP (`100.x.x.x`), **not** `0.0.0.0` — unreachable from local Wi-Fi
- **Ollama** stays on `localhost:11434` — no network exposure at all
- The Linux server reaches Ollama **only** through the authenticated FastAPI proxy (`/process-ollama`)
- Strong `PDF_API_KEY` (Bearer token) is required for all PDF service requests
- Enable macOS Firewall + Stealth mode as defense-in-depth

## Troubleshooting

### Service won't start

```bash
# Check Tailscale IP
tailscale ip -4

# Check if port 8000 is in use
lsof -i :8000

# Check logs
cat logs/$(date +%Y-%m-%d).log
```

### Docling not loading

```bash
# Reinstall docling
pip install --upgrade docling

# Verify installation
python -c "from docling.document_converter import DocumentConverter; print('OK')"
```

### Ollama proxy returns 502

```bash
# Verify Ollama is running locally
curl http://127.0.0.1:11434/api/tags

# Check if model is pulled
ollama list

# Check Ollama logs (if running via terminal, check terminal output)
```

### Connection refused from Linux server

1. Verify Tailscale is running on both machines: `tailscale status`
2. Check MacBook firewall: System Settings → Network → Firewall
3. Verify PDF service is bound to Tailscale IP: `lsof -i :8000`
4. Test locally first: `curl http://127.0.0.1:8000/health`
5. Test from Linux: `curl http://$(tailscale ip -4):8000/health`

## Logs

Service logs are written to:

- `logs/YYYY-MM-DD.log` — Daily rotated logs (created automatically)
- `/tmp/pdfservice.log` — If running via launchd
- Console output if running via tmux/terminal