# Use the official NVIDIA CUDA 12.8 runtime image to match host drivers and support Blackwell GPUs
FROM nvidia/cuda:12.8.0-runtime-ubuntu22.04

# Prevent interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies and Python 3.11 in a single layer to reduce image size and build time
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    curl \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y python3.11 python3.11-distutils \
    && rm -rf /var/lib/apt/lists/*

# Install pip for Python 3.11
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11

# Set Python 3.11 as the default python
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

WORKDIR /app

COPY requirements.txt .

# Pre-install blinker via pip to bypass the distutils uninstall error
RUN pip install --no-cache-dir --ignore-installed blinker

# 1. Install application requirements first.
RUN pip install --no-cache-dir -r requirements.txt

# 2. FINAL LOCK: Force-install the correct CUDA 12.4 PyTorch version.
# This guarantees that even if requirements pulled a CPU or newer CUDA version (like 2.11.0+cu130),
# we overwrite it with the version compatible with our host driver (535.261.03).
RUN pip install --no-cache-dir --force-reinstall torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124

# Copy application code and assets
COPY app.py seed_user.py ./
COPY templates/ templates/
COPY static/ static/

# Create necessary directories
RUN mkdir -p uploads logs

EXPOSE 8000

# Use gunicorn to serve the Flask app
CMD ["gunicorn", "app:app", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "120"]
