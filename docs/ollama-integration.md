# Ollama Integration Guide

This document provides instructions for setting up an Ollama server locally and integrating it with the `pdf-parse` Dockerized application for local LLM-powered PO extraction.

## 1. Local Ollama Setup

### Installation
1. **Download**: Visit [ollama.com](https://ollama.com) and download the installer for your operating system (Windows, macOS, or Linux).
2. **Install**: Run the installer and follow the on-screen instructions.
3. **Verify**: Open a terminal and run:
   ```bash
   ollama --version
   ```

### Pulling Models
To use the models recommended for PO extraction, pull them via the CLI:

**For Qwen 2.5 (Strong structured extraction):**
```bash
ollama pull qwen2.5:7b
# or for a lighter version
ollama pull qwen2.5:3b
```

**For Gemma 4 (Strong reasoning and context):**
```bash
ollama pull gemma4:9b
# or for a lighter version
ollama pull gemma4:4b
```

---

## 2. Server Configuration (Crucial for Docker)

By default, Ollama binds to `127.0.0.1`, which means it only accepts connections from the local machine. Since our Flask app runs inside a Docker container, it is treated as a separate network entity and cannot reach `127.0.0.1` of the host.

### Windows
1. Close Ollama from the system tray.
2. Open **System Environment Variables**.
3. Add a new User Variable:
   - **Variable**: `OLLAMA_HOST`
   - **Value**: `0.0.0.0`
4. Restart Ollama.

### macOS/Linux
Run the server with the environment variable set:
```bash
OLLAMA_HOST=0.0.0.0 ollama serve
```

---

## 3. Docker Integration

### Networking
To allow the Flask container to communicate with the Ollama server running on the host machine, use the special DNS name `host.docker.internal`.

### Environment Configuration
Add the following environment variable to your `.env` file or your `docker-compose.yml`:

```env
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen2.5:7b
```

### Docker Compose Update
If using `docker-compose.yml`, ensure the `extra_hosts` parameter is set (primarily for Linux users) to resolve `host.docker.internal`:

```yaml
services:
  web:
    build: .
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
      - OLLAMA_MODEL=qwen2.5:7b
```

---

## 4. Model Recommendations for PO Extraction

| Model | Use Case | Why? |
| :--- | :--- | :--- |
| **Qwen 2.5 (7B/3B)** | **Primary Extraction** | Exceptional at following JSON schemas and extracting tabular data into structured formats. |
| **Gemma 4 (9B/4B)** | **Complex Reasoning** | Better at interpreting ambiguous text or "cleaning" messy OCR data before extraction. |

**Strategy**: We recommend using **Qwen 2.5** for the final JSON extraction phase due to its superior instruction following for structured outputs.

---

## 5. Implementation Example (Python)

When we implement the LLM logic in `app.py`, we will use the following pattern to send the Docling and Unstructured data:

```python
import requests
import os

def query_ollama(prompt, system_prompt="You are a PO extraction expert."):
    url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434") + "/api/chat"
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "stream": False,
        "format": "json" # Ensures the model returns valid JSON
    }
    
    response = requests.post(url, json=payload)
    return response.json()['message']['content']
```

### Prompting Strategy
To maximize accuracy, the prompt will combine:
1. **Unstructured Text**: For a global "flat" view of the document.
2. **Docling Markdown**: For structural context.
3. **Docling Tables**: Specifically passed as markdown tables to preserve row/column relationships.

---

## 6. Continuous Improvement: Human-in-the-Loop (HITL)

To reach production-grade accuracy, the system will implement a feedback loop that treats user corrections as training data.

### The Feedback Cycle
1. **Extraction**: LLM extracts PO data $\rightarrow$ Saved to DB.
2. **Correction**: User identifies an error in the dashboard and corrects the value.
3. **Logging**: The system logs the `document_id`, the `original_value`, and the `corrected_value`.

### Utilizing Corrections
We can leverage this "gold dataset" of edge cases in three stages:

1. **Few-Shot Prompting (Immediate)**: Inject 2-3 examples of previous failures and their correct versions into the prompt for similar documents.
2. **Dynamic Retrieval (Intermediate)**: Use a similarity search to find the most relevant past corrections and provide them as context to the LLM.
3. **Fine-Tuning (Advanced)**: Once a sufficient dataset is collected, use PEFT/LoRA to fine-tune a small model (like Qwen 2.5 3B) into a specialized PO extraction expert.