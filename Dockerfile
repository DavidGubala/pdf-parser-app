# Use plain CUDA 11.8 runtime without system cuDNN to prevent library conflicts.
# PyTorch 2.4+cu118 bundles its own cuDNN which will be used instead.
FROM nvidia/cuda:11.8.0-runtime-ubuntu22.04

# Prevent interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive

# Install CUPTI for PyTorch profiling support
RUN apt-get update && apt-get install -y --no-install-recommends cuda-cupti-11-8 && rm -rf /var/lib/apt/lists/*
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.11/dist-packages/torch/lib:/usr/local/cuda-11.8/lib64:/usr/local/cuda-11.8/extras/CUPTI/lib64:$LD_LIBRARY_PATH

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

# 2. FINAL LOCK: Force-install PyTorch 2.4 with CUDA 11.8.
# This gives us PyTorch >= 2.4 (required by transformers) while using cuDNN 8
# which is compatible with Pascal GPUs like the GTX 1060.
RUN pip install --no-cache-dir --force-reinstall --no-deps torch==2.4.0 torchvision==0.19.0 torchaudio==2.4.0 --index-url https://download.pytorch.org/whl/cu118

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
     "--workers", "1", \
     "--timeout", "120"]
