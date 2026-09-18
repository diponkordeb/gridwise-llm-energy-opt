import os
import json
import unittest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SAMPLE_CASES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
)


class TestGridWiseSampleCases(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(SAMPLE_CASES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            cls.cases = data["cases"]

    def test_health_endpoint(self):
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_all_10_public_sample_cases(self):
        for case in self.cases:
            case_id = case["id"]
            label = case["label"]
            payload = case["input"]
            expected = case["expected_output"]

            with self.subTest(case_id=case_id, label=label):
                response = client.post("/optimize-energy", json=payload)
                self.assertEqual(
                    response.status_code, 200,
                    f"Case {case_id} failed with status {response.status_code}: {response.text}"
                )

                res_json = response.json()

                # 1. Echo scenario_id
                self.assertEqual(res_json["scenario_id"], payload["scenario_id"])

                # 2. Check directive interpretations
                res_interps = res_json["directive_interpretation"]
                exp_interps = expected["directive_interpretation"]
                self.assertEqual(len(res_interps), len(exp_interps))

                for r_interp, e_interp in zip(res_interps, exp_interps):
                    self.assertEqual(r_interp["note_index"], e_interp["note_index"])
                    self.assertEqual(r_interp["applies"], e_interp["applies"])
                    self.assertEqual(r_interp["directive_type"], e_interp["directive_type"])
                    self.assertEqual(r_interp["structured_adjustment"], e_interp["structured_adjustment"])

                # 3. Check hourly plan structure
                hourly_plan = res_json["hourly_plan"]
                self.assertEqual(len(hourly_plan), 24)

                # 4. Check GridWise mathematical constraints & energy balance
                battery = payload["battery"]
                capacity = battery["capacity_kwh"]
                initial_E = battery["initial_energy_kwh"]
                base_min_E = battery["minimum_energy_kwh"]
                max_ch = battery["max_charge_kwh_per_hour"]
                max_dis = battery["max_discharge_kwh_per_hour"]

                # Extract active directives
                eff_solar = [h["solar_kwh"] for h in payload["hours"]]
                min_reserve = [base_min_E] * 24
                max_ch_lim = [max_ch] * 24
                max_dis_lim = [max_dis] * 24
                max_grid_lim = [float("inf")] * 24

                for interp in res_interps:
                    if interp["applies"] and interp["structured_adjustment"]:
                        dt = interp["directive_type"]
                        adj = interp["structured_adjustment"]
                        hrs = adj.get("hours", [])
                        if dt == "solar_reduction":
                            f = adj["factor"]
                            for h in hrs: eff_solar[h] *= f
                        elif dt == "minimum_battery_reserve":
                            rmin = adj["minimum_energy_kwh"]
                            for h in hrs: min_reserve[h] = max(min_reserve[h], rmin)
                        elif dt == "no_charge_window":
                            for h in hrs: max_ch_lim[h] = 0.0
                        elif dt == "no_discharge_window":
                            for h in hrs: max_dis_lim[h] = 0.0
                        elif dt == "max_grid_window":
                            mgrid = adj["max_grid_kwh"]
                            for h in hrs: max_grid_lim[h] = min(max_grid_lim[h], mgrid)

                prev_E = initial_E
                recalc_total_grid = 0.0
                recalc_total_cost = 0.0
                recalc_peak_grid = 0.0

                for h, entry in enumerate(hourly_plan):
                    self.assertEqual(entry["hour"], h)
                    grid_kwh = entry["grid_kwh"]
                    solar_used = entry["solar_used_kwh"]
                    action = entry["battery_action"]
                    bat_kwh = entry["battery_kwh"]
                    energy_after = entry["battery_energy_after_kwh"]
                    demand_kwh = payload["hours"][h]["demand_kwh"]
                    tariff = payload["hours"][h]["tariff_bdt_per_kwh"]

                    # Non-negative checks
                    self.assertGreaterEqual(grid_kwh, 0.0)
                    self.assertGreaterEqual(solar_used, 0.0)
                    self.assertGreaterEqual(bat_kwh, 0.0)
                    self.assertGreaterEqual(energy_after, 0.0)

                    # Action consistency
                    if action == "charge":
                        ch = bat_kwh
                        dis = 0.0
                    elif action == "discharge":
                        ch = 0.0
                        dis = bat_kwh
                    else:
                        self.assertEqual(action, "idle")
                        self.assertAlmostEqual(bat_kwh, 0.0, places=3)
                        ch = 0.0
                        dis = 0.0

                    # Energy balance check: grid + solar_used + discharge = demand + charge
                    energy_in = grid_kwh + solar_used + dis
                    energy_out = demand_kwh + ch
                    self.assertAlmostEqual(
                        energy_in, energy_out, delta=0.01,
                        msg=f"Case {case_id} Hour {h} Energy Balance Violated: in={energy_in}, out={energy_out}"
                    )

                    # Solar limit check
                    self.assertLessEqual(
                        solar_used, eff_solar[h] + 0.01,
                        msg=f"Case {case_id} Hour {h} Solar Overuse: used={solar_used}, eff={eff_solar[h]}"
                    )

                    # Battery transition check
                    expected_E = prev_E + ch - dis
                    self.assertAlmostEqual(
                        energy_after, expected_E, delta=0.01,
                        msg=f"Case {case_id} Hour {h} Battery Transition Violated: got={energy_after}, exp={expected_E}"
                    )

                    # Reserve & Capacity bounds check
                    self.assertGreaterEqual(
                        energy_after + 0.01, min_reserve[h],
                        msg=f"Case {case_id} Hour {h} Reserve Violated: energy={energy_after}, min={min_reserve[h]}"
                    )
                    self.assertLessEqual(
                        energy_after - 0.01, capacity,
                        msg=f"Case {case_id} Hour {h} Capacity Violated: energy={energy_after}, cap={capacity}"
                    )

                    # Rate limits check
                    self.assertLessEqual(ch - 0.01, max_ch_lim[h])
                    self.assertLessEqual(dis - 0.01, max_dis_lim[h])
                    self.assertLessEqual(grid_kwh - 0.01, max_grid_lim[h])

                    recalc_total_grid += grid_kwh
                    recalc_total_cost += grid_kwh * tariff
                    if grid_kwh > recalc_peak_grid:
                        recalc_peak_grid = grid_kwh

                    prev_E = energy_after

                # End-of-day neutrality check
                self.assertAlmostEqual(
                    prev_E, initial_E, delta=0.01,
                    msg=f"Case {case_id} End of Day Neutrality Violated: final={prev_E}, initial={initial_E}"
                )

                # Total metrics recalculation check
                self.assertAlmostEqual(res_json["total_grid_kwh"], recalc_total_grid, delta=0.01)
                self.assertAlmostEqual(res_json["total_cost_bdt"], recalc_total_cost, delta=0.01)
                self.assertAlmostEqual(res_json["peak_grid_kwh"], recalc_peak_grid, delta=0.01)

                # 5. Check optimal cost matches reference optimal cost within 0.01 BDT tolerance
                expected_cost = expected["total_cost_bdt"]
                self.assertAlmostEqual(
                    res_json["total_cost_bdt"], expected_cost, delta=0.01,
                    msg=f"Case {case_id} Cost Mismatch: got {res_json['total_cost_bdt']}, expected {expected_cost}"
                )


if __name__ == "__main__":
    unittest.main()
