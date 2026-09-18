import logging
from fastapi import FastAPI, HTTPException, status, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.schemas import ScenarioRequest, OptimizationResponse, HealthResponse
from app.llm_interpreter import interpret_operator_notes
from app.optimizer import solve_energy_optimization

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gridwise_api")

app = FastAPI(
    title="GridWise Smart Campus Energy Optimization Service",
    description="LLM-Assisted Operator Directive Interpretation and 24-Hour Battery Energy Scheduling API",
    version="2.0.0"
)


@app.get("/health", response_model=HealthResponse, status_code=status.HTTP_200_OK)
def health_check():
    """Readiness endpoint for the judging harness."""
    return HealthResponse(status="ok")


@app.post(
    "/optimize-energy",
    response_model=OptimizationResponse,
    status_code=status.HTTP_200_OK
)
def optimize_energy(request: ScenarioRequest):
    """
    Main LLM interpretation + 24-hour optimization endpoint.
    1. Interprets operator notes using LLM + Guardrails into machine-checkable directives.
    2. Formulates and solves 24-hour energy scheduling LP model.
    3. Returns optimal hourly plan and total cost BDT.
    """
    try:
        # Step 1: LLM Interpretation & Guardrail Validation
        interpretations = interpret_operator_notes(
            notes=request.operator_notes,
            battery=request.battery
        )

        # Step 2: Optimization Engine
        response = solve_energy_optimization(
            request=request,
            interpretations=interpretations
        )

        return response

    except Exception as e:
        logger.error(f"Error processing energy optimization request: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Controlled internal server error during energy optimization processing."
        )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle malformed or invalid request JSON schema with HTTP 400 as specified in contract."""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "detail": "Malformed JSON or structurally invalid request.",
            "errors": exc.errors()
        }
    )
