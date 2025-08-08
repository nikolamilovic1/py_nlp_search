import re
from typing import List, Tuple, Optional, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Pydantic models ----------
class Filters(BaseModel):
    categories: List[str] = []
    keywords: List[str] = []
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    rating_min: Optional[float] = None  # 0..5
    sort_by: Literal["relevance", "price_asc", "price_desc", "rating_desc"] = "relevance"

class SearchRequest(BaseModel):
    query: str

class Product(BaseModel):
    id: int
    title: str
    price: float
    description: str
    category: str
    image: str
    rating: Optional[dict] = None  # {"rate": float, "count": int} -> FakeStore API structure

class SearchResponse(BaseModel):
    filters: Filters
    count: int
    results: List[Product]

# ---------- Helpers ----------

CATEGORY_SYNONYMS = {
    "men": "men's clothing",
    "men's": "men's clothing",
    "women": "women's clothing",
    "women's": "women's clothing",
    "jewelry": "jewelery",  # fakestore spelling
    "jewels": "jewelery",
    "electronics": "electronics",
    "clothes": None,
    "shoes": None,  # fakestore has no shoe category
}

VALID_CATEGORIES = {"men's clothing", "women's clothing", "jewelery", "electronics"}
ALLOWED_SORT = {"relevance", "price_asc", "price_desc", "rating_desc"} # To prevent crash when try to sort invalid values

# To prevent crash when try to sort invalid values
def sanitize_sort_by(value: Optional[str]) -> str:
    if not value:
        return "relevance"
    v = value.lower().strip()
    if v in ALLOWED_SORT:
        return v
    # Map common phrases to allowed sorts
    if "good review" in v or "best rated" in v or "high rating" in v:
        return "rating_desc"
    if "cheap" in v:
        return "price_asc"
    if "expensive" in v:
        return "price_desc"
    return "relevance"


def normalize_categories(cats: List[str]) -> List[str]:
    out = set()
    for c in cats:
        lc = c.lower().strip()
        mapped = CATEGORY_SYNONYMS.get(lc, lc if lc in VALID_CATEGORIES else None)
        if mapped:
            out.add(mapped)
    return sorted(out) #["Men", "jewelry"] -> ["jewelery", "men's clothing"]

async def fetch_products() -> List[Product]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get("https://fakestoreapi.com/products")
        r.raise_for_status()
        data = r.json()
        return [Product(**p) for p in data]

def apply_filters(products: List[Product], f: Filters) -> List[Product]:
    out = products[:]

    # Price window
    if f.price_min is not None:
        out = [p for p in out if p.price >= f.price_min]
    if f.price_max is not None:
        out = [p for p in out if p.price <= f.price_max]

    # rating threshold 
    if f.rating_min is not None:
        out = [p for p in out if (p.rating or {}).get("rate", 0) >= f.rating_min]

    # Category filter
    if f.categories:
        cats = {c.lower() for c in f.categories}
        out = [p for p in out if p.category.lower() in cats]

    # Keywords, title, description, category
    if f.keywords:
        kws = [k.lower() for k in f.keywords]
        def haystack(p: Product):
            return f"{p.title} {p.description} {p.category}".lower()
        out = [p for p in out if all(k in haystack(p) for k in kws)]

    if f.sort_by == "price_asc":
        out.sort(key=lambda p: p.price)
    elif f.sort_by == "price_desc":
        out.sort(key=lambda p: -p.price)
    elif f.sort_by == "rating_desc":
        out.sort(key=lambda p: -float((p.rating or {}).get("rate", 0)))
    # relevance -> leave order as is

    return out

async def call_mistral(query: str) -> Filters:
#     SYSTEM = """You convert shopping queries into strict JSON filters for a product search engine.
# Return ONLY valid JSON with this schema and no extra text:

# {
#   "categories": string[],
#   "keywords": string[],
#   "price_min": number|null,
#   "price_max": number|null,
#   "rating_min": number|null,
#   "sort_by": "relevance" | "price_asc" | "price_desc" | "rating_desc"
# }

# Rules:
# - If user says "under/below", set price_max; "above/over" -> price_min.
# - "good reviews" => rating_min = 4; "excellent/4.5+" => rating_min = 4.5.
# - Extract concrete product-type words as keywords (e.g., "running shoes" -> ["running","shoes"]).
# - Use sort_by if the user implies it: "cheapest" -> price_asc, "best rated" -> rating_desc.
# - If unknown, leave null/empty but output valid JSON only.
# """


    SYSTEM = """You convert shopping queries into strict JSON filters for a product search engine.
Return ONLY JSON with this exact schema:

{
  "categories": string[],
  "keywords": string[],
  "price_min": number|null,
  "price_max": number|null,
  "rating_min": number|null,
  "sort_by": "relevance" | "price_asc" | "price_desc" | "rating_desc"
}

Rules about price:
- "under", "below", "less than", "<= $X", "< $X", "max $X" => price_max = X
- "over", "above", "more than", ">= $X", "> $X", "min $X", "at least $X" => price_min = X
- "between $A and $B" or "$A-$B" => price_min = A and price_max = B
- Never set both price_min and price_max to the same X unless the user asked for exactly that price.
- Do not invert these rules.

Other rules:
- Only set rating_min if a numeric threshold is given (e.g., "4+ stars").
- "good reviews" alone => leave rating_min null and set sort_by = "rating_desc".
- If user implies "cheapest", set sort_by="price_asc"; "most expensive" => "price_desc".
- If unknown, leave fields null/empty, but always return valid JSON.

Examples:
Q: "electronics under $100"
A: {"categories":["electronics"],"keywords":[],"price_min":null,"price_max":100,"rating_min":null,"sort_by":"relevance"}

Q: "electronics over $100"
A: {"categories":["electronics"],"keywords":[],"price_min":100,"price_max":null,"rating_min":null,"sort_by":"relevance"}

Q: "women's clothing between $20 and $50 with 4+ stars"
A: {"categories":["women's clothing"],"keywords":[],"price_min":20,"price_max":50,"rating_min":4,"sort_by":"relevance"}
"""

    PROMPT = f'User query: """{query}"""\nReturn JSON now:'

    body = {
        "model": "mistral",
        "prompt": f"{SYSTEM}\n\n{PROMPT}",
        "stream": False,
        "options": {"temperature": 0.1}, # more deterministic
        "format": "json"
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post("http://localhost:11434/api/generate", json=body)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Ollama error: {r.text}")

        data = r.json()  # {"response": "...", ...}
        text = data.get("response", "")

    # Parse JSON from model output
    try:
        import json
        parsed = json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise HTTPException(status_code=500, detail="Model returned non-JSON.")
        import json
        parsed = json.loads(m.group(0))

    # Validation & normalization
    parsed["sort_by"] = sanitize_sort_by(parsed.get("sort_by")) # so Pydantic won’t explode
    f = Filters(**parsed)
    f.categories = normalize_categories(f.categories)
    return f


def extract_price_constraints(q: str) -> Tuple[Optional[float], Optional[float]]:
    text = q.lower().replace(",", "")
    # between $A and $B / $A-$B
    m = re.search(rf"(?:between|from)\s*\$?\s*(\d+(?:\.\d+)?)\s*(?:and|to|-)\s*\$?\s*(\d+(?:\.\d+)?)", text)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        lo, hi = (a, b) if a <= b else (b, a)
        return lo, hi

    # under/below/less than/max/<=
    m = re.search(rf"(?:under|below|less than|max(?:imum)?|<=|<)\s*\$?\s*(\d+(?:\.\d+)?)", text)
    if m:
        return None, float(m.group(1))

    # over/above/more than/min/>=/>
    m = re.search(rf"(?:over|above|more than|min(?:imum)?|at least|>=|>)\s*\$?\s*(\d+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1)), None

    return None, None


# ---------- API ----------
@app.post("/nlp-search", response_model=SearchResponse)
async def nlp_search(req: SearchRequest):
    q = (req.query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Missing query")

    filters = await call_mistral(q)

    # Hardening price logic based on the literal query
    pmin, pmax = extract_price_constraints(q)
    if pmin is not None:
        filters.price_min = pmin
        # If model set price_max equal to pmin due to confusion, clearing it
        if filters.price_max == pmin:
            filters.price_max = None
    if pmax is not None:
        filters.price_max = pmax
        if filters.price_min == pmax:
            filters.price_min = None

    # Handling inverted range
    if (filters.price_min is not None and filters.price_max is not None and filters.price_min > filters.price_max):
        filters.price_min, filters.price_max = filters.price_max, filters.price_min

    products = await fetch_products()
    results = apply_filters(products, filters)
    return SearchResponse(filters=filters, count=len(results), results=results)
