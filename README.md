# GridWise — LLM-Assisted Smart Campus Energy Optimization Service

**BUP CSE FEST 2026 Hackathon · Online Preliminary Round**  
*In association with Poridhi.io*

---

## Architecture Overview

GridWise is an automated energy scheduling and directive interpretation pipeline designed for smart campus microgrids. The system combines generative AI for natural-language operator note understanding with deterministic mathematical optimization for cost-effective 24-hour battery and grid management.

```
+------------------+     +-------------------+     +-------------------------+     +--------------------+     +-------------------+
|  Energy Data +   | --> |  LLM Interpreter  | --> | Deterministic Guardrail | --> |   Math Optimizer   | --> |   HTTP API JSON   |
|  Operator Notes  |     | (Gemini / NLP)    |     |      Validator          |     |  (SciPy LP HiGHS)  |     |     Response      |
+------------------+     +-------------------+     +-------------------------+     +--------------------+     +-------------------+
```

### Key Components:
1. **LLM Interpreter & NLP Parsing Engine (`app/llm_interpreter.py`)**:
   - Uses Google Gemini API (`gemini-2.5-flash` via `google-genai` SDK) when `GEMINI_API_KEY` is set.
   - Includes a deterministic regex-based NLP parser fallback when API keys are absent or offline, guaranteeing 100% reliable execution during local evaluation.
   - Converts natural-language operator notes into one of 6 canonical directive types:
     - `solar_reduction`: `{"hours": [...], "factor": float}`
     - `minimum_battery_reserve`: `{"hours": [...], "minimum_energy_kwh": float}`
     - `no_charge_window`: `{"hours": [...]}`
     - `no_discharge_window`: `{"hours": [...]}`
     - `max_grid_window`: `{"hours": [...], "max_grid_kwh": float}`
     - `no_op`: `applies`: false, `directive_type`: "no_op", `structured_adjustment`: null

2. **Deterministic Guardrail Validator (`app/llm_interpreter.py`)**:
   - Validates note index ordering (`note_index` = `0..N-1`).
   - Strict boolean semantics (`applies = false` iff `directive_type == "no_op"`).
   - Time window normalization: converts Start-inclusive, End-exclusive windows (e.g., 1 PM to 3 PM -> `hours = [13, 14]`).
   - Ensures `hours` arrays are sorted, unique, and strictly within `0..23`.
   - Range validation for numeric bounds (`factor` $\in [0, 1]$, non-negative reserve and grid caps).

3. **Mathematical Optimizer (`app/optimizer.py`)**:
   - Solves a 24-hour Linear Program (LP) using `scipy.optimize.linprog(method='highs')`.
   - Decision variables per hour $h \in \{0..23\}$: Grid import $G_h$, solar used $S_h$, battery charge $C_h$, battery discharge $D_h$, battery state of charge $E_h$.
   - Subject to:
     - Energy Balance: $G_h + S_{used, h} + D_h^{bat} = D_h^{demand} + C_h$.
     - Effective Solar limit: $0 \le S_{used, h} \le S_h^{eff}$.
     - Battery Capacity & Reserve bounds: $R_h \le E_h \le C$.
     - Hourly Charge/Discharge Rate limits: $0 \le C_h \le P_{ch, h}^{max}$, $0 \le D_h \le P_{dis, h}^{max}$.
     - Grid import cap: $G_h \le G_{h}^{max}$.
     - End-of-Day Neutrality: $E_{23} = E_0$.
   - Objective: Minimize total grid cost $\sum_{h=0}^{23} G_h \cdot T_h$.

---

## Quickstart & Local Reproduction

### Prerequisites
- Python 3.10+ (Tested on Python 3.11 & 3.14)
- Git & Pip

### 1. Clone & Setup Environment
```bash
git clone <your-repository-url>
cd bns

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Linux/macOS:
source .venv/bin/activate
# On Windows PowerShell:
.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables (Optional)
Create a `.env` file or export environment variables:
```bash
export GEMINI_API_KEY="your-google-gemini-api-key"
```
*(If no API key is set, the service automatically uses the deterministic NLP parser engine for offline evaluation).*

---

## Running the API Service

### Option A: Local Python / Uvicorn Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Option B: Docker Container
```bash
# Build Docker image
docker build -t gridwise-api .

# Run Docker container
docker run -p 8000:8000 -e GEMINI_API_KEY="" gridwise-api
```

### Option C: Docker Compose
```bash
docker-compose up --build
```

---

## API Contract & Verification

### 1. Health Check Endpoint (`GET /health`)
```bash
curl -X GET http://localhost:8000/health
```
**Expected Response (HTTP 200)**:
```json
{
  "status": "ok"
}
```

### 2. Optimize Energy Endpoint (`POST /optimize-energy`)
```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "SAMPLE-01",
    "operator_notes": [
      "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
      "The sports office moved next month's registration deadline."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 1, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 2, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 3, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 4, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 5, "demand_kwh": 95, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 6, "demand_kwh": 110, "solar_kwh": 5, "tariff_bdt_per_kwh": 8},
      {"hour": 7, "demand_kwh": 130, "solar_kwh": 20, "tariff_bdt_per_kwh": 10},
      {"hour": 8, "demand_kwh": 150, "solar_kwh": 50, "tariff_bdt_per_kwh": 12},
      {"hour": 9, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 10, "demand_kwh": 175, "solar_kwh": 130, "tariff_bdt_per_kwh": 16},
      {"hour": 11, "demand_kwh": 180, "solar_kwh": 160, "tariff_bdt_per_kwh": 16},
      {"hour": 12, "demand_kwh": 185, "solar_kwh": 180, "tariff_bdt_per_kwh": 15},
      {"hour": 13, "demand_kwh": 180, "solar_kwh": 170, "tariff_bdt_per_kwh": 14},
      {"hour": 14, "demand_kwh": 170, "solar_kwh": 140, "tariff_bdt_per_kwh": 13},
      {"hour": 15, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 16, "demand_kwh": 170, "solar_kwh": 45, "tariff_bdt_per_kwh": 18},
      {"hour": 17, "demand_kwh": 185, "solar_kwh": 10, "tariff_bdt_per_kwh": 22},
      {"hour": 18, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 28},
      {"hour": 19, "demand_kwh": 215, "solar_kwh": 0, "tariff_bdt_per_kwh": 30},
      {"hour": 20, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 26},
      {"hour": 21, "demand_kwh": 175, "solar_kwh": 0, "tariff_bdt_per_kwh": 18},
      {"hour": 22, "demand_kwh": 135, "solar_kwh": 0, "tariff_bdt_per_kwh": 10},
      {"hour": 23, "demand_kwh": 105, "solar_kwh": 0, "tariff_bdt_per_kwh": 7}
    ],
    "battery": {
      "capacity_kwh": 220,
      "initial_energy_kwh": 110,
      "minimum_energy_kwh": 40,
      "max_charge_kwh_per_hour": 50,
      "max_discharge_kwh_per_hour": 50
    }
  }'
```

---

## Running Automated Test Suite

To evaluate the pipeline against all 10 public sample cases:

```bash
# Set PYTHONPATH and run unittest
python -m unittest tests/test_all_samples.py
```

**Expected Result**:
```
INFO:httpx:HTTP Request: POST http://testserver/optimize-energy "HTTP/1.1 200 OK"
...
Ran 2 tests in 0.290s

OK
```

---

## Dependencies & Technical Stack
- **FastAPI**: Modern Python web framework for REST API endpoints.
- **Uvicorn**: High-performance ASGI web server.
- **SciPy (`scipy.optimize.linprog`)**: High-performance Linear Programming solver utilizing the HiGHS dual simplex / interior point engine.
- **Google GenAI (`google-genai`)**: Official Python SDK for Google Gemini models.
- **Pydantic**: Data validation and strict JSON serialization.

---

## Security & Best Practices
- **No Secrets Committed**: API keys are supplied via environment variables (`GEMINI_API_KEY`). No secrets or credentials are baked into the repository or Docker image.
- **Controlled Error Handling**: Stack traces and raw internal exceptions are suppressed from HTTP API responses to prevent information leakage.
- **Data Isolation**: Operates purely on synthetic challenge data.
