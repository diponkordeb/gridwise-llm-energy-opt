import os
import re
import json
import logging
from typing import List, Dict, Any, Optional
from app.schemas import BatteryInput, DirectiveInterpretation

logger = logging.getLogger("llm_interpreter")

VALID_DIRECTIVE_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op"
}

LLM_SYSTEM_PROMPT = """
You are an expert energy management directive interpreter. Your task is to analyze campus operator natural language notes and translate each note into a structured energy directive for a 24-hour schedule (hours 0 through 23).

Supported directive types and their required structured_adjustment format:
1. solar_reduction: Reduce usable solar during specific hours.
   structured_adjustment: {"hours": [int, ...], "factor": float}
   NOTE: 'factor' is the usable fraction remaining. An 80% reduction means factor = 0.2. "roughly 25% of forecast" means factor = 0.25. "half of forecast" means factor = 0.5.

2. minimum_battery_reserve: Keep battery energy at or above a required level in kWh.
   structured_adjustment: {"hours": [int, ...], "minimum_energy_kwh": float}
   NOTE: If reserve is given as a percentage (e.g. 50% of capacity), convert to kWh by multiplying by battery capacity (e.g. 50% of 200 kWh = 100 kWh).

3. no_charge_window: Battery charging is unavailable during specific hours.
   structured_adjustment: {"hours": [int, ...]}

4. no_discharge_window: Battery discharging is unavailable during specific hours.
   structured_adjustment: {"hours": [int, ...]}

5. max_grid_window: Grid import may not exceed a stated kWh amount during specific hours.
   structured_adjustment: {"hours": [int, ...], "max_grid_kwh": float}

6. no_op: The note does NOT affect today's 24-hour energy schedule (e.g. registration deadline, cafeteria menu, library hours, book-return, sports office, seminar booking, student affairs notice).
   applies: false, directive_type: "no_op", structured_adjustment: null

TIME CONVENTION (Crucial):
- Time windows are whole-hour intervals, start-inclusive and end-exclusive.
- 12 PM (noon) until 2 PM -> hours [12, 13]
- 2 AM until 5 AM -> hours [2, 3, 4]
- 6 PM until 9 PM -> hours [18, 19, 20]
- 6 PM until 8 PM -> hours [18, 19]
- 6 PM until 10 PM -> hours [18, 19, 20, 21]
- 7 PM until 9 PM -> hours [19, 20]
- 7 PM until 10 PM -> hours [19, 20, 21]
- 10 AM until noon -> hours [10, 11]
- 2 PM until 4 PM -> hours [14, 15]
- 11 AM until 1 PM -> hours [11, 12]
- 5 PM until 7 PM -> hours [17, 18]
- between 11 AM and 2 PM -> hours [11, 12, 13]

OUTPUT FORMAT:
Return a JSON array of objects, one entry per note in exact note_index order:
[
  {
    "note_index": 0,
    "applies": true,
    "directive_type": "solar_reduction",
    "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
    "explanation": "Solar availability is reduced to 25% during panel cleaning."
  }
]
"""


def _parse_time_window(text: str) -> Optional[List[int]]:
    """Parse common English time window descriptions into start-inclusive, end-exclusive 0-23 hour arrays."""
    text_lower = text.lower()

    # Helpers to parse hour string to 24h int
    def to_24h(h_str: str, period: str = None) -> int:
        h_str = h_str.strip()
        if h_str == "noon" or h_str == "12 pm": return 12
        if h_str == "midnight" or h_str == "12 am": return 0
        val = int(re.search(r'\d+', h_str).group())
        if period:
            period = period.lower()
            if period == "pm" and val < 12: val += 12
            elif period == "am" and val == 12: val = 0
        return val

    # Match patterns like "from 12 PM until 2 PM", "from noon until 2 PM", "between 11 AM and 2 PM"
    p1 = re.search(r'(?:from|between)\s+(noon|midnight|\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+(?:until|to|and|-)\s+(noon|midnight|\d{1,2}(?::\d{2})?\s*(?:am|pm)?)', text_lower)
    if p1:
        start_raw, end_raw = p1.group(1), p1.group(2)
        
        # Determine AM/PM context if omitted in start
        end_period = "pm" if "pm" in end_raw or "noon" in end_raw else ("am" if "am" in end_raw or "midnight" in end_raw else None)
        start_period = "am" if "am" in start_raw else ("pm" if "pm" in start_raw else end_period)

        # Contextual adjustment: e.g. "from 1 PM until 3 PM", "from 10 AM until noon"
        if "noon" in end_raw:
            end_h = 12
            start_h = to_24h(start_raw, "am" if "am" in start_raw else ("pm" if to_24h(start_raw) < 7 else "am"))
        else:
            end_h = to_24h(end_raw, end_period)
        
        if "noon" in start_raw:
            start_h = 12
        else:
            start_h = to_24h(start_raw, start_period)

        # Fix relative PM vs AM if start_h > end_h
        if start_h >= end_h and start_h < 12 and end_h <= 12 and end_period == "pm":
            if start_h < 12 and start_period != "am":
                start_h += 12

        if 0 <= start_h < end_h <= 24:
            return list(range(start_h, end_h))

    # Match patterns like "1 PM to 3 PM", "1-3 PM"
    p2 = re.search(r'(\d{1,2})\s*-\s*(\d{1,2})\s*(pm|am)', text_lower)
    if p2:
        sh, eh, period = int(p2.group(1)), int(p2.group(2)), p2.group(3)
        if period == "pm":
            if sh < 12: sh += 12
            if eh < 12: eh += 12
        return list(range(sh, eh))

    return None


def parse_note_fallback(note: str, battery: BatteryInput) -> Dict[str, Any]:
    """Deterministic NLP parser fallback when LLM API is unavailable."""
    n_lower = note.lower()

    # Check for distractors/irrelevant notes first
    distractor_keywords = [
        "registration", "menu", "cafeteria", "sports office", "book-return", "library",
        "seminar room", "booking", "club notices", "student affairs", "next week", "next month"
    ]
    if any(k in n_lower for k in distractor_keywords) and not any(k in n_lower for k in ["solar", "battery", "grid", "charger", "reserve"]):
        return {
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "This note does not affect today's 24-hour energy schedule."
        }

    hours = _parse_time_window(note)
    if not hours:
        # Fallback time extraction heuristics
        if "noon until 2 pm" in n_lower or "12 pm until 2 pm" in n_lower: hours = [12, 13]
        elif "2 am until 5 am" in n_lower: hours = [2, 3, 4]
        elif "6 pm until 9 pm" in n_lower: hours = [18, 19, 20]
        elif "6 pm until 8 pm" in n_lower: hours = [18, 19]
        elif "6 pm until 10 pm" in n_lower: hours = [18, 19, 20, 21]
        elif "7 pm until 9 pm" in n_lower: hours = [19, 20]
        elif "7 pm until 10 pm" in n_lower: hours = [19, 20, 21]
        elif "10 am until noon" in n_lower: hours = [10, 11]
        elif "2 pm until 4 pm" in n_lower: hours = [14, 15]
        elif "11 am until 1 pm" in n_lower: hours = [11, 12]
        elif "5 pm until 7 pm" in n_lower: hours = [17, 18]
        elif "11 am and 2 pm" in n_lower: hours = [11, 12, 13]

    # Directive 1: Solar Reduction
    if any(k in n_lower for k in ["solar", "panels", "pv production", "rooftop"]):
        factor = 1.0
        if "25%" in n_lower or "one-fourth" in n_lower or "quarter" in n_lower:
            factor = 0.25
        elif "80% reduction" in n_lower or "reduced by 80%" in n_lower:
            factor = 0.2
        elif "50%" in n_lower or "half" in n_lower or "one-half" in n_lower or "50 percent" in n_lower:
            factor = 0.5
        elif "20%" in n_lower or "one-fifth" in n_lower:
            factor = 0.2
        
        m_red = re.search(r'(\d+)%\s+reduction', n_lower)
        if m_red:
            red_pct = float(m_red.group(1))
            factor = round((100.0 - red_pct) / 100.0, 2)

        m_remain = re.search(r'(?:to|roughly|leave)\s+(\d+)%', n_lower)
        if m_remain and "reduction" not in n_lower:
            factor = round(float(m_remain.group(1)) / 100.0, 2)

        return {
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": hours or [], "factor": factor},
            "explanation": f"Usable solar reduced to {int(factor*100)}% during window."
        }

    # Directive 2: Minimum Battery Reserve
    if "reserve" in n_lower or "remain in the battery" in n_lower or "stored in the battery" in n_lower or "keep at least" in n_lower:
        req_min = battery.minimum_energy_kwh
        # Check percentage
        m_pct = re.search(r'(\d+)%\s+of\s+(?:the\s+)?battery\s+capacity', n_lower)
        if m_pct:
            pct = float(m_pct.group(1))
            req_min = round(battery.capacity_kwh * (pct / 100.0), 2)
        else:
            m_kwh = re.search(r'(\d+(?:\.\d+)?)\s*kwh', n_lower)
            if m_kwh:
                req_min = float(m_kwh.group(1))

        return {
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": hours or [], "minimum_energy_kwh": req_min},
            "explanation": f"Minimum battery reserve of {req_min} kWh enforced."
        }

    # Directive 3: No Charge Window
    if ("charge" in n_lower or "charger" in n_lower or "charging" in n_lower) and ("not charge" in n_lower or "isolated" in n_lower or "unavailable" in n_lower or "disabled" in n_lower or "outage" in n_lower):
        return {
            "applies": True,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": hours or []},
            "explanation": "Battery charging disabled during maintenance/window."
        }

    # Directive 4: No Discharge Window
    if ("discharge" in n_lower or "discharging" in n_lower) and ("not discharge" in n_lower or "disabled" in n_lower or "protection" in n_lower or "testing" in n_lower):
        return {
            "applies": True,
            "directive_type": "no_discharge_window",
            "structured_adjustment": {"hours": hours or []},
            "explanation": "Battery discharging disabled during window."
        }

    # Directive 5: Max Grid Window
    if "grid" in n_lower or "import" in n_lower or "feeder" in n_lower or "transformer" in n_lower or "substation" in n_lower or "intake" in n_lower:
        max_grid = 0.0
        m_grid = re.search(r'(\d+(?:\.\d+)?)\s*kwh', n_lower)
        if m_grid:
            max_grid = float(m_grid.group(1))

        return {
            "applies": True,
            "directive_type": "max_grid_window",
            "structured_adjustment": {"hours": hours or [], "max_grid_kwh": max_grid},
            "explanation": f"Grid import capped at {max_grid} kWh during window."
        }

    # Fallback to no_op if unrecognized
    return {
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": "This note does not affect today's 24-hour energy schedule."
    }


def validate_and_sanitize_directives(
    raw_list: List[Dict[str, Any]],
    num_notes: int,
    battery: BatteryInput
) -> List[DirectiveInterpretation]:
    """Deterministic Guardrail Validator enforcing all schema and semantic rules."""
    sanitized: List[DirectiveInterpretation] = []

    for idx in range(num_notes):
        raw = raw_list[idx] if idx < len(raw_list) else {}
        note_idx = idx
        directive_type = str(raw.get("directive_type", "no_op"))
        if directive_type not in VALID_DIRECTIVE_TYPES:
            directive_type = "no_op"

        applies = bool(raw.get("applies", False))

        # Enforce applies <-> directive_type rule
        if directive_type == "no_op":
            applies = False
            adj = None
        else:
            applies = True
            adj = raw.get("structured_adjustment")
            if not isinstance(adj, dict):
                adj = {}

            # Validate and clean hours array
            raw_hours = adj.get("hours", [])
            if not isinstance(raw_hours, list):
                raw_hours = []
            clean_hours = sorted(list(set(int(h) for h in raw_hours if isinstance(h, (int, float)) and 0 <= int(h) <= 23)))
            adj["hours"] = clean_hours

            # Validate type-specific numeric fields
            if directive_type == "solar_reduction":
                factor = float(adj.get("factor", 1.0))
                factor = max(0.0, min(1.0, round(factor, 4)))
                adj["factor"] = factor
            elif directive_type == "minimum_battery_reserve":
                min_energy = float(adj.get("minimum_energy_kwh", battery.minimum_energy_kwh))
                min_energy = max(0.0, min(float(battery.capacity_kwh), round(min_energy, 4)))
                adj["minimum_energy_kwh"] = min_energy
            elif directive_type == "max_grid_window":
                max_grid = float(adj.get("max_grid_kwh", 0.0))
                max_grid = max(0.0, round(max_grid, 4))
                adj["max_grid_kwh"] = max_grid

        explanation = str(raw.get("explanation", "Interpreted directive from note."))

        sanitized.append(
            DirectiveInterpretation(
                note_index=note_idx,
                applies=applies,
                directive_type=directive_type,
                structured_adjustment=adj,
                explanation=explanation
            )
        )

    return sanitized


def interpret_operator_notes(
    notes: List[str],
    battery: BatteryInput
) -> List[DirectiveInterpretation]:
    """
    Main entry point for interpreting operator notes into structured directives.
    First attempts Gemini LLM API if key is present, otherwise falls back to deterministic NLP parser.
    Always runs through Deterministic Guardrail Validator.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    raw_results = []

    if api_key:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            prompt = (
                f"{LLM_SYSTEM_PROMPT}\n\n"
                f"Battery specs for scenario: capacity_kwh={battery.capacity_kwh}, initial_energy_kwh={battery.initial_energy_kwh}, minimum_energy_kwh={battery.minimum_energy_kwh}.\n"
                f"Operator notes to interpret:\n"
            )
            for i, note in enumerate(notes):
                prompt += f"Note {i}: \"{note}\"\n"

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            parsed = json.loads(response.text)
            if isinstance(parsed, list):
                raw_results = parsed
        except Exception as e:
            logger.warning(f"LLM API call failed ({e}), falling back to NLP parser.")

    # Fallback to deterministic NLP parser if API key absent or failed
    if not raw_results:
        raw_results = [parse_note_fallback(note, battery) for note in notes]

    return validate_and_sanitize_directives(raw_results, len(notes), battery)
