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

# 1. Install PyTorch with CUDA 12.4 support first.
# We use cu124 as it is the current stable target for PyTorch 2.x and is fully compatible
# with CUDA 12.8 drivers. This avoids the massive build times caused by searching
# for non-standard indices or force-reinstalling.
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# 2. Install application requirements.
# Since torch is already installed, pip will satisfy the torch dependency of 'docling'
# and 'unstructured' using the existing GPU version.
COPY requirements.txt .
RUN pip install --no-cache-dir --ignore-installed -r requirements.txt

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
