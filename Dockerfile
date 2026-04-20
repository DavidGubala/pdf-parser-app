# 1. Use the official NVIDIA CUDA runtime as the base
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

# 2. Install Python 3.11 and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    curl \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y python3.11 python3.11-distutils \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

WORKDIR /app

# 3. INSTALL TORCH FIRST
# We install the CUDA 12.4 version of torch before anything else.
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# 4. INSTALL REQUIREMENTS
# We use --no-deps for the requirements install if we want to be extreme,
# but instead we'll just install them and then RE-INSTALL torch to be safe.
COPY requirements.txt .
RUN pip install --no-cache-dir --ignore-installed -r requirements.txt

# 5. THE FINAL LOCK: Force-reinstall the GPU version of torch
# This ensures that if 'docling' installed a CPU version of torch as a dependency,
# it is immediately overwritten by the GPU version before the image is finished.
RUN pip install --no-cache-dir --force-reinstall torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

COPY app.py seed_user.py ./
COPY templates/ templates/
COPY static/ static/
RUN mkdir -p uploads logs

EXPOSE 8000
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
