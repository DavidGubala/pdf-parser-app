# Simplified Dockerfile for Linux Web Server (no GPU needed)
# PDF processing is offloaded to the MacBook compute node via Tailscale

FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies (minimal set)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and assets
COPY app.py seed_user.py ./
COPY templates/ templates/
COPY static/ static/

# Create necessary directories
RUN mkdir -p uploads logs data

EXPOSE 8000

# Use gunicorn to serve the Flask app
CMD ["gunicorn", "app:app", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "120"]
