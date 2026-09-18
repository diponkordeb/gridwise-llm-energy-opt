import logging
from fastapi import FastAPI, HTTPException, status, Request
from fastapi.responses import JSONResponse, HTMLResponse
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

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GridWise — Smart Campus Energy Optimization Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; }
    </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen">
    <!-- Header -->
    <header class="border-b border-slate-800 bg-slate-900/50 backdrop-blur sticky top-0 z-50">
        <div class="max-w-7xl mx-auto px-4 py-4 flex flex-col md:flex-row justify-between items-center gap-4">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-emerald-500 to-teal-400 flex items-center justify-center text-slate-950 font-black text-xl shadow-lg shadow-emerald-500/20">
                    ⚡
                </div>
                <div>
                    <h1 class="text-xl font-bold bg-gradient-to-r from-emerald-400 to-teal-200 bg-clip-text text-transparent">GridWise Energy OS</h1>
                    <p class="text-xs text-slate-400">LLM Operator Directive & 24h Battery Optimizer Dashboard</p>
                </div>
            </div>
            <div class="flex items-center gap-3">
                <div class="flex items-center gap-2 bg-slate-800/80 px-3 py-1.5 rounded-full border border-slate-700 text-xs font-medium">
                    <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                    API Status: <span class="text-emerald-400 font-bold" id="health-status">Online (HTTP 200)</span>
                </div>
                <a href="/docs" target="_blank" class="text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 px-3 py-1.5 rounded-lg border border-slate-700 transition">Swagger API Docs ↗</a>
            </div>
        </div>
    </header>

    <!-- Main Container -->
    <main class="max-w-7xl mx-auto px-4 py-8">
        <div class="grid grid-cols-1 lg:grid-cols-12 gap-8">
            
            <!-- Left Panel: Controls & Input -->
            <div class="lg:col-span-4 space-y-6">
                <!-- Preset Selector -->
                <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl">
                    <label class="block text-sm font-semibold text-slate-300 mb-2">Select Scenario Preset</label>
                    <select id="sample-selector" onchange="loadPreset(this.value)" class="w-full bg-slate-950 border border-slate-700 rounded-xl px-4 py-3 text-sm text-slate-200 focus:outline-none focus:border-emerald-500 transition">
                        <option value="0">SAMPLE-01: Solar cleaning + Distractor note</option>
                        <option value="1">SAMPLE-02: Battery charging maintenance</option>
                        <option value="2">SAMPLE-03: Emergency reserve % (50% capacity)</option>
                        <option value="3">SAMPLE-04: No-discharge protection test</option>
                        <option value="4">SAMPLE-05: Temporary feeder grid cap</option>
                        <option value="5">SAMPLE-06: Multiple notes + distractor</option>
                        <option value="6">SAMPLE-07: Reserve + transformer cap</option>
                        <option value="7">SAMPLE-08: Separate charge/discharge outages</option>
                        <option value="8">SAMPLE-09: 80% Solar reduction norm</option>
                        <option value="9">SAMPLE-10: Multi-constraint evening operation</option>
                    </select>
                </div>

                <!-- Operator Notes Input -->
                <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
                    <div class="flex justify-between items-center">
                        <h2 class="font-bold text-slate-200">Natural Language Operator Notes</h2>
                        <span class="text-xs bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2 py-0.5 rounded">LLM Target</span>
                    </div>
                    <p class="text-xs text-slate-400">These human notes will be parsed by the LLM into structured constraints before optimization.</p>
                    <div id="notes-container" class="space-y-3">
                        <!-- Dynamic inputs -->
                    </div>
                    
                    <button onclick="runOptimization()" id="run-btn" class="w-full py-3.5 px-4 bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-400 hover:to-teal-400 text-slate-950 font-bold rounded-xl shadow-lg shadow-emerald-500/25 transition flex items-center justify-center gap-2 text-sm">
                        <span>⚡ Run LLM & Optimize Schedule</span>
                    </button>
                </div>

                <!-- Battery Spec Summary -->
                <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-3">
                    <h3 class="font-bold text-slate-300 text-sm">Battery Storage Specs</h3>
                    <div class="grid grid-cols-2 gap-3 text-xs">
                        <div class="bg-slate-950 p-3 rounded-xl border border-slate-800">
                            <span class="text-slate-400 block">Capacity</span>
                            <span class="text-base font-bold text-emerald-400" id="bat-cap">220 kWh</span>
                        </div>
                        <div class="bg-slate-950 p-3 rounded-xl border border-slate-800">
                            <span class="text-slate-400 block">Initial Energy</span>
                            <span class="text-base font-bold text-teal-400" id="bat-init">110 kWh</span>
                        </div>
                        <div class="bg-slate-950 p-3 rounded-xl border border-slate-800">
                            <span class="text-slate-400 block">Base Reserve</span>
                            <span class="text-base font-bold text-amber-400" id="bat-min">40 kWh</span>
                        </div>
                        <div class="bg-slate-950 p-3 rounded-xl border border-slate-800">
                            <span class="text-slate-400 block">Max Rate</span>
                            <span class="text-base font-bold text-blue-400" id="bat-rate">50 kW</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Right Panel: Results & Visualization -->
            <div class="lg:col-span-8 space-y-6">
                
                <!-- Metrics Summary Cards -->
                <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    <div class="bg-slate-900/90 border border-slate-800 rounded-2xl p-5 shadow-xl">
                        <span class="text-xs font-medium text-slate-400 block mb-1">Total Grid Electricity Cost</span>
                        <div class="text-2xl font-black text-emerald-400" id="res-cost">--- BDT</div>
                        <span class="text-[10px] text-slate-500">24-Hour Horizon Sum</span>
                    </div>
                    <div class="bg-slate-900/90 border border-slate-800 rounded-2xl p-5 shadow-xl">
                        <span class="text-xs font-medium text-slate-400 block mb-1">Total Grid Energy Imported</span>
                        <div class="text-2xl font-black text-teal-400" id="res-grid-kwh">--- kWh</div>
                        <span class="text-[10px] text-slate-500">Net Purchased Energy</span>
                    </div>
                    <div class="bg-slate-900/90 border border-slate-800 rounded-2xl p-5 shadow-xl">
                        <span class="text-xs font-medium text-slate-400 block mb-1">Peak Hourly Grid Demand</span>
                        <div class="text-2xl font-black text-amber-400" id="res-peak">--- kWh</div>
                        <span class="text-[10px] text-slate-500">Max Single Hour Import</span>
                    </div>
                </div>

                <!-- LLM Extracted Directives Section -->
                <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
                    <div class="flex justify-between items-center">
                        <h3 class="font-bold text-slate-200">LLM Interpreted Directives & Guardrails</h3>
                        <span class="text-xs text-slate-400">Validated Machine Structure</span>
                    </div>
                    <div id="directives-list" class="space-y-3">
                        <div class="text-slate-500 text-sm text-center py-4">Run optimization to view extracted directives.</div>
                    </div>
                </div>

                <!-- Plan Strategy Summary Card -->
                <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl">
                    <h3 class="font-bold text-slate-200 text-sm mb-2">Optimizer Strategy Summary</h3>
                    <p class="text-sm text-slate-300 leading-relaxed" id="plan-summary-text">
                        Select a scenario and click run to view strategy breakdown.
                    </p>
                </div>

                <!-- Chart Visualizer -->
                <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
                    <h3 class="font-bold text-slate-200">24-Hour Energy Balance & Battery Profile</h3>
                    <div class="h-64 relative">
                        <canvas id="energyChart"></canvas>
                    </div>
                </div>

                <!-- 24-Hour Hourly Schedule Table -->
                <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4 overflow-hidden">
                    <h3 class="font-bold text-slate-200">Detailed 24-Hour Schedule Breakdown</h3>
                    <div class="overflow-x-auto max-h-80">
                        <table class="w-full text-left text-xs text-slate-300">
                            <thead class="bg-slate-950 text-slate-400 sticky top-0">
                                <tr>
                                    <th class="p-2.5">Hour</th>
                                    <th class="p-2.5">Demand (kWh)</th>
                                    <th class="p-2.5">Solar Used (kWh)</th>
                                    <th class="p-2.5">Battery Action</th>
                                    <th class="p-2.5">Battery Energy (kWh)</th>
                                    <th class="p-2.5">Grid Import (kWh)</th>
                                    <th class="p-2.5">Tariff (BDT)</th>
                                </tr>
                            </thead>
                            <tbody id="schedule-table-body" class="divide-y divide-slate-800">
                                <tr><td colspan="7" class="text-center p-4 text-slate-500">No data available</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

            </div>

        </div>
    </main>

    <!-- JS Logic -->
    <script>
        let sampleCases = [];
        let currentScenario = null;
        let chartInstance = null;

        const PUBLIC_SAMPLES_URL = "https://raw.githubusercontent.com/diponkordeb/gridwise-llm-energy-opt/main/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json";

        async function init() {
            try {
                // Fetch sample cases JSON
                const res = await fetch('/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json');
                let data;
                if(res.ok) {
                    data = await res.json();
                } else {
                    const res2 = await fetch(PUBLIC_SAMPLES_URL);
                    data = await res2.json();
                }
                sampleCases = data.cases;
                loadPreset(0);
            } catch(e) {
                console.error(e);
            }
        }

        function loadPreset(index) {
            if (!sampleCases.length) return;
            currentScenario = sampleCases[index].input;
            
            // Populate Notes
            const container = document.getElementById('notes-container');
            container.innerHTML = '';
            currentScenario.operator_notes.forEach((note, idx) => {
                container.innerHTML += `
                    <div class="bg-slate-950 p-3 rounded-xl border border-slate-800 space-y-1">
                        <span class="text-[10px] text-slate-400 font-bold uppercase">Operator Note [${idx}]</span>
                        <input type="text" id="note-${idx}" value="${note.replace(/"/g, '&quot;')}" class="w-full bg-transparent text-xs text-slate-200 focus:outline-none border-b border-transparent focus:border-emerald-500">
                    </div>
                `;
            });

            // Populate Specs
            const bat = currentScenario.battery;
            document.getElementById('bat-cap').innerText = bat.capacity_kwh + ' kWh';
            document.getElementById('bat-init').innerText = bat.initial_energy_kwh + ' kWh';
            document.getElementById('bat-min').innerText = bat.minimum_energy_kwh + ' kWh';
            document.getElementById('bat-rate').innerText = bat.max_charge_kwh_per_hour + ' kW';

            runOptimization();
        }

        async function runOptimization() {
            const btn = document.getElementById('run-btn');
            btn.disabled = true;
            btn.innerHTML = '<span>⏳ Processing LLM & Optimizer...</span>';

            // Gather inputs
            const notes = [];
            document.querySelectorAll('#notes-container input').forEach(input => {
                notes.push(input.value);
            });

            const payload = {
                ...currentScenario,
                operator_notes: notes
            };

            try {
                const res = await fetch('/optimize-energy', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (!res.ok) throw new Error('Optimization failed');

                const data = await res.json();
                renderResults(data);
            } catch(e) {
                alert('Error running optimization: ' + e.message);
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<span>⚡ Run LLM & Optimize Schedule</span>';
            }
        }

        function renderResults(data) {
            // Render Cards
            document.getElementById('res-cost').innerText = data.total_cost_bdt.toLocaleString() + ' BDT';
            document.getElementById('res-grid-kwh').innerText = data.total_grid_kwh.toLocaleString() + ' kWh';
            document.getElementById('res-peak').innerText = data.peak_grid_kwh.toLocaleString() + ' kWh';
            document.getElementById('plan-summary-text').innerText = data.plan_summary;

            // Render Directives
            const dirContainer = document.getElementById('directives-list');
            dirContainer.innerHTML = '';
            data.directive_interpretation.forEach(d => {
                const badgeColor = d.applies ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' : 'bg-slate-800 text-slate-400 border-slate-700';
                const adjStr = d.structured_adjustment ? JSON.stringify(d.structured_adjustment) : 'null';
                dirContainer.innerHTML += `
                    <div class="bg-slate-950 p-4 rounded-xl border border-slate-800 space-y-2">
                        <div class="flex justify-between items-center">
                            <span class="font-bold text-xs text-slate-300">Note [${d.note_index}]: ${d.directive_type}</span>
                            <span class="text-[10px] px-2.5 py-0.5 rounded-full border ${badgeColor}">${d.applies ? 'APPLIES' : 'NO_OP'}</span>
                        </div>
                        <p class="text-xs text-slate-400">${d.explanation}</p>
                        <div class="text-[11px] font-mono text-emerald-400/90 bg-slate-900 px-3 py-1.5 rounded-lg border border-slate-800">
                            structured_adjustment: ${adjStr}
                        </div>
                    </div>
                `;
            });

            // Render Table
            const tbody = document.getElementById('schedule-table-body');
            tbody.innerHTML = '';
            data.hourly_plan.forEach(h => {
                let actionBadge = `<span class="px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-400">Idle</span>`;
                if (h.battery_action === 'charge') {
                    actionBadge = `<span class="px-2 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">Charge +${h.battery_kwh}</span>`;
                } else if (h.battery_action === 'discharge') {
                    actionBadge = `<span class="px-2 py-0.5 rounded text-[10px] bg-amber-500/20 text-amber-400 border border-amber-500/30">Discharge -${h.battery_kwh}</span>`;
                }

                const demand = currentScenario.hours[h.hour].demand_kwh;
                const tariff = currentScenario.hours[h.hour].tariff_bdt_per_kwh;

                tbody.innerHTML += `
                    <tr class="hover:bg-slate-900/50">
                        <td class="p-2.5 font-bold">${h.hour}:00</td>
                        <td class="p-2.5">${demand}</td>
                        <td class="p-2.5 text-amber-400 font-medium">${h.solar_used_kwh}</td>
                        <td class="p-2.5">${actionBadge}</td>
                        <td class="p-2.5 font-medium text-teal-300">${h.battery_energy_after_kwh}</td>
                        <td class="p-2.5 font-bold text-emerald-400">${h.grid_kwh}</td>
                        <td class="p-2.5 text-slate-400">${tariff} BDT</td>
                    </tr>
                `;
            });

            // Render Chart
            renderChart(data.hourly_plan);
        }

        function renderChart(plan) {
            const ctx = document.getElementById('energyChart').getContext('2d');
            const labels = plan.map(h => h.hour + ':00');
            const gridData = plan.map(h => h.grid_kwh);
            const solarData = plan.map(h => h.solar_used_kwh);
            const batData = plan.map(h => h.battery_energy_after_kwh);

            if (chartInstance) chartInstance.destroy();

            chartInstance = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: 'Grid Import (kWh)',
                            data: gridData,
                            borderColor: '#10b981',
                            backgroundColor: 'rgba(16, 185, 129, 0.1)',
                            fill: true,
                            tension: 0.3
                        },
                        {
                            label: 'Solar Used (kWh)',
                            data: solarData,
                            borderColor: '#f59e0b',
                            backgroundColor: 'transparent',
                            borderDash: [5, 5],
                            tension: 0.3
                        },
                        {
                            label: 'Battery Energy (kWh)',
                            data: batData,
                            borderColor: '#14b8a6',
                            backgroundColor: 'transparent',
                            tension: 0.3
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { labels: { color: '#94a3b8', font: { size: 11 } } }
                    },
                    scales: {
                        x: { grid: { color: '#1e293b' }, ticks: { color: '#64748b' } },
                        y: { grid: { color: '#1e293b' }, ticks: { color: '#64748b' } }
                    }
                }
            });
        }

        window.onload = init;
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def root_dashboard():
    """Interactive visual web dashboard."""
    return HTMLResponse(content=HTML_DASHBOARD, status_code=200)


@app.get("/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json")
def get_sample_cases():
    """Serve sample cases JSON directly for frontend dashboard presets."""
    try:
        import json
        with open("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json", "r", encoding="utf-8") as f:
            return JSONResponse(content=json.load(f))
    except Exception:
        raise HTTPException(status_code=404, detail="Sample cases file not found.")


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
