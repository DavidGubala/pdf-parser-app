# Use the official PyTorch image which comes pre-installed with
# CUDA, cuDNN, and PyTorch. This eliminates the need to install
# these massive libraries from scratch, making builds significantly faster.
FROM pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime

# Prevent interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive

# Install only the essential system libraries needed for Docling and OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install dependencies.
# We use --ignore-installed for requirements to avoid the 'blinker' distutils error
# and we don't need to install torch separately because it's already in the base image.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --ignore-installed -r requirements.txt

# Copy application code and assets
COPY app.py seed_user.py ./
COPY templates/ templates/
COPY static/ static/

# Create necessary directories for uploads and logs
RUN mkdir -p uploads logs

EXPOSE 8000

# Use gunicorn to serve the Flask app
CMD ["gunicorn", "app:app", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "120"]
