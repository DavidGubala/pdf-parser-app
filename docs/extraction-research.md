# Research: Document Extraction & Data Organization for PO Organizer

> Date: 2025-01-XX
> Purpose: Evaluate alternatives to our current regex-based PO extraction and explore LLM-assisted data organization.

---

## 1. Current Architecture

### How We Extract Data Today

Our pipeline in `app.py` follows this flow:

```
PDF → Docling (markdown + tables) → Regex + Heuristics → SQLite
```

**Key functions:**
- `_parse_markdown_table()` — parses Docling's markdown tables into (headers, rows)
- `_is_line_item_table()` — checks if headers contain "line" + "part/description" keywords
- `_extract_item_from_table()` — assumes nVenia-style 3-row-per-item layout
- `extract_po_data()` — regex on full markdown text for metadata (Company Name, PO#, Date)

**Problem:** The extraction logic is tightly coupled to the nVenia PO format. Different vendors use different table layouts, header names, and metadata placements. Our keyword matching and row assumptions break on unfamiliar formats.

---

## 2. Document Extraction Tool Comparison

### 2.1 Docling (Current)

| Attribute | Detail |
|---|---|
| **GitHub** | [docling-project/docling](https://github.com/docling-project/docling) |
| **Stars** | 58k ⭐ |
| **License** | MIT |
| **Language** | Python |
| **Table extraction** | ✅ Excellent — TableFormer model for structure recognition |
| **Layout analysis** | ✅ Excellent — DocLayNet object detection model |
| **OCR** | ✅ Built-in |
| **Output formats** | Markdown, JSON (DoclingDocument), HTML, DocTags |
| **Multi-format** | PDF, DOCX, PPTX, XLSX, HTML, images, LaTeX, WebVTT |
| **GPU support** | ✅ Yes |
| **Local execution** | ✅ Full, air-gapped capable |
| **Install weight** | Heavy (~2-4 GB for ML models) |

**Verdict:** Best-in-class for our use case. TableFormer is specifically designed for complex tables like PO line items. **Keep as primary parser.**

### 2.2 Unstructured

| Attribute | Detail |
|---|---|
| **GitHub** | [Unstructured-IO/unstructured](https://github.com/Unstructured-IO/unstructured) |
| **Stars** | 14.5k ⭐ |
| **License** | Apache 2.0 |
| **Language** | Python |
| **Table extraction** | ✅ Good — via Donut model or Hi-Res strategy |
| **Layout analysis** | ✅ Good — YOLOX layout model |
| **OCR** | ✅ Via Tesseract |
| **Output formats** | Structured element list (Title, NarrativeText, Table, Image, etc.) |
| **Multi-format** | 35+ formats (PDF, DOCX, PPTX, HTML, EML, MSG, EPUB, etc.) |
| **GPU support** | ✅ Yes |
| **Local execution** | ✅ Full |
| **Install weight** | Heavy (many optional dependency groups) |

**Verdict:** Strong alternative if we need to support many more document formats (emails, HTML, EPUBs). However, table extraction quality is not as good as Docling's for purchase orders. The element-based output is more LLM-friendly than raw markdown, but we'd lose TableFormer's table structure accuracy.

### 2.3 LiteParse

| Attribute | Detail |
|---|---|
| **GitHub** | [run-llama/liteparse](https://github.com/run-llama/liteparse) |
| **Stars** | 4.4k ⭐ |
| **License** | Apache 2.0 |
| **Language** | TypeScript (primary), Python bindings |
| **Table extraction** | ❌ No — text + bounding boxes only |
| **Layout analysis** | ❌ Basic — spatial text ordering |
| **OCR** | ✅ Tesseract.js (built-in) + HTTP servers (EasyOCR, PaddleOCR) |
| **Output formats** | JSON (with bboxes), Text |
| **Multi-format** | PDF + Office (via LibreOffice) + images (via ImageMagick) |
| **GPU support** | ❌ No |
| **Local execution** | ✅ Full |
| **Install weight** | Light (~50 MB, uses PDF.js) |

**Verdict:** Too lightweight for our needs. It gives text with bounding boxes but no table structure understanding, which is critical for PO line items. It's also TypeScript-first, making Python integration awkward (requires npm/Node.js or subprocess calls). Best suited as a preprocessing step for LLM vision models, not as a standalone extractor.

### 2.4 Summary Comparison

| Feature | Docling | Unstructured | LiteParse |
|---|---|---|---|
| Table structure | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐ |
| Layout analysis | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| Format coverage | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| Python native | ✅ | ✅ | ⚠️ (subprocess) |
| Speed | Medium | Medium | Fast |
| LLM-ready output | Markdown | Element list | Text + bboxes |
| Recommendation | **Keep** | Alternative | Not suitable |

---

## 3. Data Organization — Current vs. Proposed

### 3.1 Current Approach: Regex + Heuristics

```python
# Current: brittle regex patterns
company_match = re.search(r"(?:Company|Customer|Bill To)[:\s|]+(.+)", text, re.IGNORECASE)
po_match = re.search(r"(?:PO\s*#?|Purchase\s*Order)[:\s|]+([\w-]+)", text, re.IGNORECASE)

# Current: hardcoded table assumptions
def _is_line_item_table(headers):
    hl = [h.lower().strip() for h in headers]
    return any("line" in h for h in hl) and any("part" in h or "description" in h for h in hl)
```

**Problems:**
- Regex patterns are format-specific (work for nVenia, break on others)
- Table detection requires specific keywords in headers
- Row layout assumptions (3 rows per item) don't generalize
- Adding support for a new PO format means writing new regex/heuristics

### 3.2 Proposed: LLM-Assisted Extraction

```
PDF → Docling (markdown + tables) → LLM (structured JSON) → SQLite
```

The LLM receives Docling's markdown output and returns a structured JSON object matching our schema. This is format-agnostic — the LLM understands semantic meaning regardless of layout.

---

## 4. Lightweight LLM Options

### 4.1 Model Comparison

| Model | Size | Quality | CPU Speed | RAM (Q4) | Notes |
|---|---|---|---|---|---|
| **Qwen2.5-3B-Instruct** | 3B | Good | ~5 tok/s | ~2 GB | Best small model for structured output |
| **Qwen2.5-7B-Instruct** | 7B | Very Good | ~2 tok/s | ~4 GB | Sweet spot for quality/speed |
| **Phi-3.5-mini-instruct** | 3.8B | Good | ~4 tok/s | ~2.5 GB | Microsoft, strong reasoning |
| **Llama-3.2-3B-Instruct** | 3B | Good | ~5 tok/s | ~2 GB | Meta, good tool use |
| **GPT-4o-mini** (API) | N/A | Very Good | N/A | N/A | ~$0.15/M input tokens, ~$0.60/M output |
| **Granite-Docling** (VLM) | Varies | Excellent | GPU only | N/A | IBM's doc understanding model |

### 4.2 Integration Options

#### Option A: Ollama (Local, Recommended)
```bash
ollama pull qwen2.5:3b
```
```python
import ollama
response = ollama.chat(
    model="qwen2.5:3b",
    messages=[{"role": "user", "content": prompt}],
    format={"type": "object", "properties": {...}}  # JSON schema
)
```
- **Pros:** Free, local, no API keys, supports structured output
- **Cons:** Requires Ollama server running, ~2-4 GB RAM

#### Option B: llama-cpp-python (Local, No Server)
```python
from llama_cpp import Llama
llm = Llama(model_path="qwen2.5-3b-instruct.Q4_K_M.gguf", n_ctx=4096)
result = llm.create_chat_completion(messages=[...], response_format={"type": "json_object"})
```
- **Pros:** Pure Python, no external server, GGUF models are compact
- **Cons:** Slower than Ollama, manual model management

#### Option C: Existing LLM API (Cloud)
```python
# Using our existing tools/llm_api.py
from tools.llm_api import query_llm
result = query_llm(prompt, provider="openai", model="gpt-4o-mini")
```
- **Pros:** No local setup, highest quality, already integrated
- **Cons:** Requires API key, per-token cost, needs internet

### 4.3 Cost Estimate (Cloud API)

For a typical PO (~5 pages, ~3KB markdown):
- **Input tokens:** ~1,000 tokens
- **Output tokens:** ~200 tokens (JSON)
- **GPT-4o-mini cost:** ~$0.00027 per PO
- **At 100 POs/month:** ~$0.027/month — negligible

---

## 5. Can We Train a Lightweight LLM?

### 5.1 Fine-Tuning Options

| Method | Data Needed | Hardware | Time | Notes |
|---|---|---|---|---|
| **LoRA/QLoRA** | 100-500 labeled examples | 1x GPU (8GB VRAM) | 1-4 hours | Most practical for our scale |
| **Full fine-tuning** | 1,000+ examples | Multiple GPUs | Days | Overkill |
| **Distillation** | Teacher model outputs | 1x GPU | Hours | Complex setup |

### 5.2 Few-Shot Prompting (No Training)

Instead of fine-tuning, provide 2-3 example POs in the prompt:

```
Extract purchase order data from this document. Return JSON with:
company_name, po_number, po_date, items[]

Examples:
---
Document: [example PO 1 markdown]
Output: {"company_name": "Acme Corp", "po_number": "PO-12345", ...}
---
Document: [example PO 2 markdown]
Output: {"company_name": "Beta Inc", "po_number": "PO-67890", ...}
---
Document: [actual PO markdown]
Output:
```

**Recommendation:** Start with few-shot prompting. It's immediate, requires no training data, and achieves 90%+ accuracy with a 7B model or GPT-4o-mini.

### 5.3 When to Consider Fine-Tuning

- We have 100+ diverse PO formats and few-shot prompts get too long
- Latency requirements demand a smaller model (< 3B)
- We need deterministic output guarantees (fine-tuned models are more consistent)
- API costs become significant at scale (> 10,000 POs/month)

---

## 6. Recommended Architecture

### 6.1 Proposed Pipeline

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐     ┌──────────┐
│   PDF Upload│────▶│   Docling    │────▶│  LLM Extractor  │────▶│  SQLite  │
│             │     │  (markdown)  │     │  (Qwen2.5-3B    │     │  (POs,   │
│             │     │  + tables    │     │   or GPT-4o-mini)│     │  items)  │
└─────────────┘     └──────────────┘     └─────────────────┘     └──────────┘
```

### 6.2 JSON Schema for PO Output

```json
{
  "company_name": "string — the customer/buyer (Ship To)",
  "po_number": "string — purchase order identifier",
  "po_date": "string — ISO date (YYYY-MM-DD)",
  "vendor": "string — optional vendor/supplier name",
  "items": [
    {
      "line_number": "integer",
      "part_number": "string",
      "name": "string — item name/short description",
      "description": "string — full item description",
      "quantity": "number",
      "unit_price": "number",
      "extended_price": "number",
      "due_date": "string — ISO date (YYYY-MM-DD)",
      "category": "string — optional classification"
    }
  ],
  "total_amount": "number — optional",
  "currency": "string — optional, e.g. USD",
  "confidence": "number — 0.0 to 1.0, extraction confidence"
}
```

### 6.3 Phased Implementation Plan

#### Phase 1: LLM Few-Shot Extraction (Quick Win — 1-2 days)
- [ ] Define JSON schema for PO output (Pydantic model)
- [ ] Build `extract_po_with_llm()` function using existing `llm_api.py`
- [ ] Create 2-3 example POs for few-shot prompting
- [ ] Add fallback: if LLM fails, fall back to current regex extraction
- [ ] Test against multiple PO formats
- [ ] Add `confidence` score to output

#### Phase 2: Local LLM Integration (Offline Mode — 2-3 days)
- [ ] Add Ollama integration (`pip install ollama`)
- [ ] Support `EXTRACTION_PROVIDER=ollama|openai|anthropic` env var
- [ ] Default to local Qwen2.5-3B if Ollama is available
- [ ] Add model selection UI in settings
- [ ] Benchmark accuracy vs. cloud models

#### Phase 3: Architecture Improvements (2-3 days)
- [ ] Extract extraction logic from `app.py` into `services/extraction.py`
- [ ] Move `DocumentConverter` to singleton pattern
- [ ] Add Pydantic validation for all extracted data
- [ ] Add extraction logging for debugging/improvement

#### Phase 4: Fine-Tuning (If Needed — 1-2 weeks)
- [ ] Collect 100+ labeled PO examples from real usage
- [ ] Fine-tune Qwen2.5-3B with QLoRA
- [ ] Deploy GGUF quantized model with llama-cpp-python
- [ ] Compare accuracy vs. few-shot prompting

---

## 7. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM hallucinates incorrect data | Low | High | Add confidence scores, validate with Pydantic, keep regex fallback |
| Local LLM too slow on CPU | Medium | Medium | Use 3B model, set timeout, fall back to cloud API |
| API costs grow unexpectedly | Low | Low | Current cost is ~$0.0003/PO; even 10K POs = $3/month |
| PO format too complex for LLM | Low | Medium | Add human review UI for low-confidence extractions |
| Ollama setup too complex for users | Medium | Low | Make it optional; cloud API is default |

---

## 8. Key Takeaways

1. **Keep Docling** — its table extraction (TableFormer) is best-in-class for POs. No replacement needed.
2. **Replace regex with LLM** — few-shot prompting with a 3-7B model or GPT-4o-mini will handle diverse PO formats without hardcoded heuristics.
3. **Start simple** — Phase 1 (few-shot via existing `llm_api.py`) can be done in 1-2 days with immediate benefit.
4. **Local is optional** — Ollama + Qwen2.5-3B gives free, offline extraction, but cloud API costs are negligible for our scale.
5. **Don't fine-tune yet** — few-shot prompting will cover 90%+ of cases. Fine-tune only if we hit scale or accuracy limits.
6. **LiteParse is not suitable** — lacks table structure understanding, which is critical for PO line items.