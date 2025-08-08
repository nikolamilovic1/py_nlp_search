# NLP Product Search API

FastAPI backend that turns natural-language shopping queries into structured filters, fetches products from FakeStore API, and returns matches. Uses Mistral via Ollama to parse queries into JSON.

## How to run

1) Clone

```bash
   git clone <your-repo-url>
   cd <your-repo-folder>
```

2) Create virtual environment (Python 3.10)
Linux/macOS: (Tested on Linux)

```bash
   python3 -m venv .venv
   source .venv/bin/activate
```

3) Install dependencies
```bash
    pip install -r requirements.txt
```

4) Install Ollama and pull the model
```bash
    curl -fsSL https://ollama.com/install.sh | sh
    ollama pull mistral
```

5) Run the API
```bash
    uvicorn main:app --reload --port 8000
```

6) Try it
```bash
    curl -X POST http://localhost:8000/nlp-search -H "Content-Type: application/json" -d '{"query":"Show me electronics under $100"}'
```

## Which AI feature are used
- LLM (Mistral 7B via Ollama) to convert free-form text into a strict JSON filter:
  categories, keywords, price_min, price_max, rating_min, sort_by.
- Low temperature (0.1) for deterministic, schema-friendly output.
- Post-processing (sanitize_sort_by, normalize_categories) to keep filters valid.

## Tools / libraries

- FastAPI (web framework)
- Pydantic (data models / validation)
- httpx (async HTTP client)
- CORSMiddleware (frontend access)
- Ollama (local LLM runtime) + Mistral 7B model
- FakeStore API (demo product data)

## Notable assumptions

- Available categories (FakeStore): "men's clothing", "women's clothing", "jewelery", "electronics".
  Terms like "shoes" map to no category (may yield zero results).
- Keywords are matched case-insensitively across title/description/category and require ALL tokens to match (AND logic).
- "good reviews" doesn’t set a numeric rating_min; data is sorted by rating_desc instead.
- Literal price parsing (e.g., "under 100") overrides the model if there’s any conflict.
- sort_by is sanitized to one of: relevance | price_asc | price_desc | rating_desc to prevent validation errors.

## System Specs (Tested Environment)

- **OS**: Ubuntu 24.04.2 LTS
- **CPU**: Intel Core i7-14650HX (16 cores / 24 threads, up to 5.2 GHz)
- **RAM**: 32 GB DDR5
- **GPU**: NVIDIA GeForce RTX 4070 Laptop GPU (8 GB VRAM)  
  - Driver: 560.35.03  
  - CUDA Runtime (driver): 12.6  
  - CUDA Toolkit (nvcc): 12.0  
- **Storage**: NVMe SSD 500 GB
- **Python**: 3.10.12
- **Ollama**: 0.1.32
- **Mistral Model**: mistral:latest (7.25B params, quantized, ~4.4 GB on disk)

