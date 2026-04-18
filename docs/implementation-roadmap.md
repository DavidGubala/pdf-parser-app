# LLM Integration Implementation Roadmap

This document outlines the phased approach to integrating a local LLM (via Ollama) into the `pdf-parse` application to replace hardcoded extraction logic with a flexible, AI-driven system.

## Phase 1: The "Brain" (Core LLM Integration)
**Goal**: Establish a stable connection between the Dockerized application and the local Ollama server to perform basic structured extraction.

### 1.1 Infrastructure & Configuration
- [ ] **Environment Variables**: Add `OLLAMA_BASE_URL` and `OLLAMA_MODEL` to the `.env` file.
- [ ] **Docker Networking**: Update `docker-compose.yml` with `extra_hosts: ["host.docker.internal:host-gateway"]` to allow the container to communicate with the host machine.
- [ ] **Ollama Setup**: (User Task) Install Ollama, pull recommended models (Qwen 2.5 or Gemma 4), and set `OLLAMA_HOST=0.0.0.0`.

### 1.2 Backend Implementation
- [ ] **LLM Utility**: Implement `query_ollama()` in `app.py` to handle API requests, set the `format: "json"` flag, and manage timeouts.
- [ ] **Prompt Engineering**: 
    - Define a `SYSTEM_PROMPT` specifying the required JSON schema (Company, PO#, Date, Items).
    - Create a prompt builder that aggregates **Unstructured Text**, **Docling Markdown**, and **Docling Tables**.
- [ ] **Workflow Integration**: 
    - Update `process_pdf` to trigger the LLM extraction immediately after data preparation.
    - Implement JSON parsing logic to map LLM output to the `purchase_orders` and `po_items` database tables.

---

## Phase 2: The "Eyes" (User Verification & Correction)
**Goal**: Implement a Human-in-the-Loop (HITL) interface to allow users to verify and correct AI-extracted data.

### 2.1 Data Persistence
- [ ] **Feedback Schema**: Create a `po_corrections` table to log:
    - `document_id`
    - `field_name` (e.g., "due_date", "quantity")
    - `original_value`
    - `corrected_value`
    - `timestamp`

### 2.2 Frontend Enhancements
- [ ] **Edit Mode**: Update the PO Data tab in the document detail view to allow inline editing of extracted fields.
- [ ] **Correction UI**: Add a "Save Corrections" action that visually confirms the update.

### 2.3 API Development
- [ ] **Correction Endpoint**: Create a `/api/purchase-orders/correct` endpoint that:
    1. Updates the primary PO/Item records.
    2. Logs the change in the `po_corrections` table for future training.

---

## Phase 3: The "Memory" (Continuous Improvement)
**Goal**: Use the collected correction data to automatically improve the model's accuracy over time.

### 3.1 Prompt Optimization
- [ ] **Few-Shot Injection**: Modify the prompt builder to query the `po_corrections` table and inject 2-3 relevant "Mistake $\rightarrow$ Correction" examples into the prompt.
- [ ] **Dynamic Retrieval**: Implement a basic similarity search to ensure the examples provided to the LLM are relevant to the current document's format.

### 3.2 Evaluation & Monitoring
- [ ] **Accuracy Tracking**: Create a simple internal metric to track the "Correction Rate" (percentage of fields changed by users) to quantify model improvement.
- [ ] **Dataset Export**: Implement a utility to export the `po_corrections` table into a JSONL format suitable for PEFT/LoRA fine-tuning.

---

## Summary of Technical Requirements

| Component | Requirement |
| :--- | :--- |
| **Runtime** | Ollama Server (Host) $\leftrightarrow$ Flask App (Docker) |
| **Model** | Qwen 2.5 (Extraction) or Gemma 4 (Reasoning) |
| **Data Flow** | PDF $\rightarrow$ Docling/Unstructured $\rightarrow$ LLM $\rightarrow$ SQLite $\rightarrow$ User $\rightarrow$ Feedback Loop |
| **Key API** | Ollama `/api/chat` with `format: "json"` |