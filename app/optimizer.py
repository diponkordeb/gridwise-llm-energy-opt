import numpy as np
from scipy.optimize import linprog
from typing import List, Dict, Any
from app.schemas import ScenarioRequest, DirectiveInterpretation, OptimizationResponse, HourlyPlanEntry


def solve_energy_optimization(
    request: ScenarioRequest,
    interpretations: List[DirectiveInterpretation]
) -> OptimizationResponse:
    N = 24
    hours_input = request.hours
    battery = request.battery

    demand = np.array([h.demand_kwh for h in hours_input], dtype=float)
    base_solar = np.array([h.solar_kwh for h in hours_input], dtype=float)
    tariff = np.array([h.tariff_bdt_per_kwh for h in hours_input], dtype=float)

    eff_solar = base_solar.copy()
    min_reserve = np.full(N, battery.minimum_energy_kwh, dtype=float)
    max_ch_lim = np.full(N, battery.max_charge_kwh_per_hour, dtype=float)
    max_dis_lim = np.full(N, battery.max_discharge_kwh_per_hour, dtype=float)
    max_grid_lim = np.full(N, np.inf, dtype=float)

    # Process interpretations to modify bounds/profiles
    applied_directives_summary = []
    for interp in interpretations:
        if interp.applies and interp.structured_adjustment:
            dtype = interp.directive_type
            adj = interp.structured_adjustment
            hours = adj.get("hours", [])
            
            if dtype == "solar_reduction":
                factor = float(adj.get("factor", 1.0))
                for h in hours:
                    if 0 <= h < N:
                        eff_solar[h] *= factor
                applied_directives_summary.append(f"solar reduction to {int(factor*100)}% in hours {hours}")

            elif dtype == "minimum_battery_reserve":
                req_min = float(adj.get("minimum_energy_kwh", battery.minimum_energy_kwh))
                for h in hours:
                    if 0 <= h < N:
                        min_reserve[h] = max(min_reserve[h], req_min)
                applied_directives_summary.append(f"minimum reserve {req_min} kWh in hours {hours}")

            elif dtype == "no_charge_window":
                for h in hours:
                    if 0 <= h < N:
                        max_ch_lim[h] = 0.0
                applied_directives_summary.append(f"charging disabled in hours {hours}")

            elif dtype == "no_discharge_window":
                for h in hours:
                    if 0 <= h < N:
                        max_dis_lim[h] = 0.0
                applied_directives_summary.append(f"discharging disabled in hours {hours}")

            elif dtype == "max_grid_window":
                max_grid = float(adj.get("max_grid_kwh", np.inf))
                for h in hours:
                    if 0 <= h < N:
                        max_grid_lim[h] = min(max_grid_lim[h], max_grid)
                applied_directives_summary.append(f"max grid import capped at {max_grid} kWh in hours {hours}")

    # Variables per hour h (0..23):
    # 0*N + h: Grid import G_h
    # 1*N + h: Solar used S_h
    # 2*N + h: Battery charge C_h
    # 3*N + h: Battery discharge D_h
    # 4*N + h: Battery energy state E_h (after hour h)
    num_vars = 5 * N

    c = np.zeros(num_vars)
    for h in range(N):
        c[0 * N + h] = tariff[h]  # Minimize total grid cost BDT

    bounds = []
    # G_h bounds: 0 .. max_grid_lim[h]
    for h in range(N):
        bound_high = None if np.isinf(max_grid_lim[h]) else max_grid_lim[h]
        bounds.append((0.0, bound_high))

    # S_h bounds: 0 .. eff_solar[h]
    for h in range(N):
        bounds.append((0.0, float(eff_solar[h])))

    # C_h bounds: 0 .. max_ch_lim[h]
    for h in range(N):
        bounds.append((0.0, float(max_ch_lim[h])))

    # D_h bounds: 0 .. max_dis_lim[h]
    for h in range(N):
        bounds.append((0.0, float(max_dis_lim[h])))

    # E_h bounds: min_reserve[h] .. capacity_kwh
    for h in range(N):
        bounds.append((float(min_reserve[h]), float(battery.capacity_kwh)))

    A_eq = []
    b_eq = []

    # 1. Energy balance equation for each hour h:
    # G_h + S_h + D_h - C_h = demand[h]
    for h in range(N):
        row = np.zeros(num_vars)
        row[0 * N + h] = 1.0   # G_h
        row[1 * N + h] = 1.0   # S_h
        row[3 * N + h] = 1.0   # D_h
        row[2 * N + h] = -1.0  # -C_h
        A_eq.append(row)
        b_eq.append(float(demand[h]))

    # 2. Battery state transition equations:
    # h=0: E_0 - C_0 + D_0 = initial_energy_kwh
    row = np.zeros(num_vars)
    row[4 * N + 0] = 1.0   # E_0
    row[2 * N + 0] = -1.0  # -C_0
    row[3 * N + 0] = 1.0   # D_0
    A_eq.append(row)
    b_eq.append(float(battery.initial_energy_kwh))

    # h=1..23: E_h - E_{h-1} - C_h + D_h = 0
    for h in range(1, N):
        row = np.zeros(num_vars)
        row[4 * N + h] = 1.0      # E_h
        row[4 * N + h - 1] = -1.0 # -E_{h-1}
        row[2 * N + h] = -1.0     # -C_h
        row[3 * N + h] = 1.0      # D_h
        A_eq.append(row)
        b_eq.append(0.0)

    # 3. End-of-day battery neutrality: E_23 = initial_energy_kwh
    row = np.zeros(num_vars)
    row[4 * N + 23] = 1.0
    A_eq.append(row)
    b_eq.append(float(battery.initial_energy_kwh))

    # Solve LP using HiGHS solver
    res = linprog(
        c,
        A_eq=np.array(A_eq),
        b_eq=np.array(b_eq),
        bounds=bounds,
        method="highs"
    )

    if not res.success:
        raise RuntimeError(f"Linear programming optimization failed: {res.message}")

    x = res.x

    hourly_plan: List[HourlyPlanEntry] = []
    total_grid_kwh = 0.0
    total_cost_bdt = 0.0
    peak_grid_kwh = 0.0

    for h in range(N):
        g = float(x[0 * N + h])
        s = float(x[1 * N + h])
        ch = float(x[2 * N + h])
        dis = float(x[3 * N + h])
        e = float(x[4 * N + h])

        # Clean numerical precision
        if g < 1e-6: g = 0.0
        if s < 1e-6: s = 0.0
        if ch < 1e-6: ch = 0.0
        if dis < 1e-6: dis = 0.0

        # Action determination
        if ch > 1e-4:
            action = "charge"
            bat_kwh = ch
        elif dis > 1e-4:
            action = "discharge"
            bat_kwh = dis
        else:
            action = "idle"
            bat_kwh = 0.0

        grid_cost = g * tariff[h]
        total_grid_kwh += g
        total_cost_bdt += grid_cost
        if g > peak_grid_kwh:
            peak_grid_kwh = g

        hourly_plan.append(
            HourlyPlanEntry(
                hour=h,
                grid_kwh=round(g, 4),
                solar_used_kwh=round(s, 4),
                battery_action=action,
                battery_kwh=round(bat_kwh, 4),
                battery_energy_after_kwh=round(e, 4)
            )
        )

    # Format summary description
    if applied_directives_summary:
        dir_str = "Applied directives (" + ", ".join(applied_directives_summary) + "). "
    else:
        dir_str = "No active directives. "

    plan_summary = (
        f"{dir_str}Optimized 24-hour campus energy schedule minimizing grid cost "
        f"to {round(total_cost_bdt, 2)} BDT across peak and off-peak tariff periods while maintaining end-of-day battery neutrality."
    )

    return OptimizationResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=interpretations,
        hourly_plan=hourly_plan,
        total_grid_kwh=round(total_grid_kwh, 4),
        total_cost_bdt=round(total_cost_bdt, 4),
        peak_grid_kwh=round(peak_grid_kwh, 4),
        plan_summary=plan_summary
    )
