# Local LLM Inference Research: Ollama vs. llama.cpp vs. llama-swap

## 1. Overview
This document evaluates the viability of replacing the current Ollama-based inference setup with `llama.cpp` or `llama-swap` to reduce overhead ("bloat") and increase functionality.

### Current State
- **Hardware:** NVIDIA GeForce GTX 1060 3GB (Compute Capability 6.1).
- **Model:** `qwen2.5:1.5b` (running on CPU due to VRAM/Compute limitations).
- **Integration:** Flask backend communicating via OpenAI-compatible API.

---

## 2. Technology Comparison

| Feature | Ollama | llama.cpp | llama-swap |
| :--- | :--- | :--- | :--- |
| **Nature** | Model Manager & Runtime | Core Inference Engine (C++) | Model Switching Proxy (Go) |
| **Dependencies** | Bundled runtime | Minimal (Plain C/C++) | Zero (Single Binary) |
| **Model Format** | Ollama Library (GGUF based) | GGUF | Agnostic (Proxies others) |
| **API** | OpenAI Compatible | OpenAI Compatible (`llama-server`) | OpenAI/Anthropic Compatible |
| **Setup** | One-click Installer | Build from source / Binaries | Single Binary + Config |
| **Resource Usage** | Moderate (Management overhead) | Extremely Low | Negligible (Proxy layer) |
| **Model Mgmt** | `ollama pull/run` | Manual GGUF downloads | Config-based swapping |
| **GPU Support** | Automated | Highly Granular / Manual | Dependent on upstream server |

---

## 3. Deep Dive

### llama.cpp (The Engine)
`llama.cpp` is the foundational project that Ollama is built upon. It provides the most direct access to the hardware.

- **Pros:**
    - **Maximum Efficiency:** No wrapper layers; direct control over threads, batch size, and GPU offloading.
    - **Flexibility:** Supports a vast array of backends (CUDA, Vulkan, Metal, etc.).
    - **GGUF Standard:** Uses the industry-standard GGUF format, allowing you to download any quantized model directly from Hugging Face.
    - **`llama-server`:** Provides a lightweight HTTP server that is a drop-in replacement for the Ollama API.
- **Cons:**
    - **Manual Workflow:** You must manually manage `.gguf` files and CLI arguments.
    - **Configuration:** Requires more technical knowledge to optimize (e.g., setting `-ngl` for GPU layers).

### llama-swap (The Orchestrator)
`llama-swap` is not an inference engine but a **transparent proxy**. It sits between the application and one or more inference servers (like `llama-server`).

- **Pros:**
    - **Dynamic Swapping:** Can automatically start/stop different models based on the `model` parameter in the API request.
    - **VRAM Optimization:** Ideal for machines with low VRAM (like the GTX 1060) where you cannot fit multiple models simultaneously.
    - **Multi-Backend:** Can proxy to `llama.cpp`, `vLLM`, or even remote APIs.
- **Cons:**
    - **Additional Layer:** Adds a small amount of network latency (negligible for most use cases).
    - **Management:** Requires maintaining a `config.yaml` to map model IDs to shell commands.

### Ollama (The Wrapper)
Ollama simplifies the `llama.cpp` experience by bundling model management and the server into one package.

- **Pros:** Great UX, easy model updates.
- **Cons:** "Bloat" comes from the background daemon and the abstraction layer that hides the underlying `llama.cpp` flags.

---

## 4. Viability Analysis for Current Project

### Is it "Less Bloated"?
Yes. Moving to `llama.cpp` (`llama-server`) removes the Ollama daemon and management layer. The memory footprint of the runtime itself will be lower.

### Does it allow "More Functionality"?
Yes. 
- **`llama.cpp`** allows for precise control over GBNF grammars (constrained output), which is critical for reliable JSON extraction in PO parsing.
- **`llama-swap`** allows the system to switch between a small, fast model for simple tasks and a larger, more capable model for complex POs without manual intervention.

### Hardware Impact (GTX 1060 3GB)
Since the current bottleneck is the Pascal architecture and 3GB VRAM:
1. **`llama.cpp`** will allow us to try more aggressive quantization (e.g., 2-bit or 3-bit) or specific CUDA kernels that might be more efficient than Ollama's defaults.
2. **`llama-swap`** would be highly beneficial if we decide to use different models for different stages of the pipeline (e.g., one for classification, one for extraction).

---

## 5. Recommendation

**Recommendation: Migrate to `llama.cpp` (`llama-server`) $\rightarrow$ Optional `llama-swap` layer.**

1. **Phase 1: Replace Ollama with `llama-server`.** 
   - Download the `qwen2.5-1.5b` GGUF file.
   - Run `llama-server` with optimized CPU/GPU flags.
   - Update the Flask app's API URL.
   - **Result:** Reduced overhead, identical API.

2. **Phase 2: Introduce `llama-swap` if multi-model support is needed.**
   - Configure `llama-swap` to manage `llama-server` instances.
   - **Result:** Ability to hot-swap models based on request.

### Summary of Transition
`Ollama` $\rightarrow$ `llama-server` (Efficiency) $\rightarrow$ `llama-swap` (Flexibility).