# Use the official NVIDIA CUDA 12.1 runtime image for compatibility with host driver 535.x
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04

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

# 2. FINAL LOCK: Force-install the correct CUDA 12.1 PyTorch version.
# --no-deps guarantees pip does not try to resolve dependencies and override our pinned version.
# Using 2.5.1 as it is the latest stable release available for cu121.
RUN pip install --no-cache-dir --force-reinstall --no-deps torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121

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
