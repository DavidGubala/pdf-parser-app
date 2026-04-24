#!/bin/bash
# MacBook PDF Processing Service Startup Script
#
# This script starts the FastAPI PDF microservice on the MacBook.
# It binds to the Tailscale IP for security and handles common
# MacBook-specific concerns like preventing sleep.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --------------------------------------------
# Configuration (override via environment)
# --------------------------------------------
export PDF_API_KEY="${PDF_API_KEY:-apikey}"
export PDF_SERVICE_URL="${PDF_SERVICE_URL:-http://127.0.0.1:8000}"
export OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"

# Detect Tailscale IP (if available)
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "")
if [ -z "$TAILSCALE_IP" ]; then
    echo "WARNING: Tailscale not detected. Binding to 127.0.0.1 (localhost only)."
    BIND_IP="127.0.0.1"
else
    echo "Using Tailscale IP: $TAILSCALE_IP"
    BIND_IP="$TAILSCALE_IP"
fi

# --------------------------------------------
# Virtual environment setup
# --------------------------------------------
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# Install/upgrade dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt

# --------------------------------------------
# Check Ollama status
# --------------------------------------------
if curl -s "$OLLAMA_URL" > /dev/null 2>&1; then
    echo "Ollama detected at $OLLAMA_URL"
else
    echo ""
    echo "WARNING: Ollama not detected at $OLLAMA_URL"
    echo "  LLM features (PO extraction, chat) will be unavailable."
    echo "  To start Ollama with remote access:"
    echo ""
    echo "    OLLAMA_HOST=0.0.0.0:11434 ollama serve"
    echo ""
    echo "  (Use 0.0.0.0 so local apps still work; macOS Firewall blocks WAN)"
    echo ""
fi

# --------------------------------------------
# Prevent MacBook from sleeping
# --------------------------------------------
echo "Starting PDF processing service..."
echo "  API Key set: $([ "$PDF_API_KEY" = "change-me-in-production" ] && echo 'NO - Change this in production!' || echo 'YES')"
echo "  Binding to: $BIND_IP:8000"
echo "  Health check: http://$BIND_IP:8000/health"
echo ""
echo "Press Ctrl+C to stop the service."
echo ""

# Use caffeinate to prevent sleep while plugged in
# -d: prevent display sleep
# -i: prevent idle sleep
# -m: prevent disk sleep
# -s: prevent system sleep
if command -v caffeinate &> /dev/null; then
    caffeinate -dims uvicorn main:app \
        --host "$BIND_IP" \
        --port 8000 \
        --log-level info
else
    # Fallback if caffeinate is not available
    uvicorn main:app \
        --host "$BIND_IP" \
        --port 8000 \
        --log-level info
fi
