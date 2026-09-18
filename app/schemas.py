from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field


class HourInput(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Hour of the day from 0 to 23")
    demand_kwh: float = Field(..., ge=0, description="Campus electricity demand in kWh")
    solar_kwh: float = Field(..., ge=0, description="Base solar generation available in kWh")
    tariff_bdt_per_kwh: float = Field(..., description="Grid electricity price in BDT per kWh")


class BatteryInput(BaseModel):
    capacity_kwh: float = Field(..., gt=0, description="Maximum energy battery can store")
    initial_energy_kwh: float = Field(..., ge=0, description="Energy in battery at start of hour 0")
    minimum_energy_kwh: float = Field(..., ge=0, description="Base minimum reserve energy level")
    max_charge_kwh_per_hour: float = Field(..., ge=0, description="Max charging rate in kWh/hour")
    max_discharge_kwh_per_hour: float = Field(..., ge=0, description="Max discharging rate in kWh/hour")


class ScenarioRequest(BaseModel):
    scenario_id: str = Field(..., description="Unique scenario identifier")
    operator_notes: List[str] = Field(..., min_length=1, max_length=3, description="1 to 3 operator notes")
    hours: List[HourInput] = Field(..., min_length=24, max_length=24, description="Hourly entries for 24 hours")
    battery: BatteryInput = Field(..., description="Battery specs")


class DirectiveInterpretation(BaseModel):
    note_index: int = Field(..., ge=0, description="Zero-based index of operator note")
    applies: bool = Field(..., description="True if directive applies, False if no_op")
    directive_type: str = Field(..., description="One of supported directive types or no_op")
    structured_adjustment: Optional[Dict[str, Any]] = Field(None, description="Structured adjustment parameter object or null")
    explanation: str = Field(..., description="Short explanation of interpretation")


class HourlyPlanEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Hour index 0 to 23")
    grid_kwh: float = Field(..., ge=0, description="Grid energy purchased in kWh")
    solar_used_kwh: float = Field(..., ge=0, description="Solar energy used in kWh")
    battery_action: str = Field(..., description="charge, discharge, or idle")
    battery_kwh: float = Field(..., ge=0, description="Magnitude of battery action in kWh")
    battery_energy_after_kwh: float = Field(..., ge=0, description="Battery energy state after this hour")


class OptimizationResponse(BaseModel):
    scenario_id: str = Field(..., description="Echo of request scenario_id")
    directive_interpretation: List[DirectiveInterpretation] = Field(..., description="Interpretations for each operator note")
    hourly_plan: List[HourlyPlanEntry] = Field(..., min_length=24, max_length=24, description="Optimal 24-hour schedule")
    total_grid_kwh: float = Field(..., ge=0, description="Sum of grid energy imported over 24 hours")
    total_cost_bdt: float = Field(..., ge=0, description="Total grid electricity cost in BDT")
    peak_grid_kwh: float = Field(..., ge=0, description="Maximum hourly grid import in kWh")
    plan_summary: str = Field(..., description="Human-readable summary of strategy")


class HealthResponse(BaseModel):
    status: str = "ok"
