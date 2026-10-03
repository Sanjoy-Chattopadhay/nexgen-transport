"""Kilometres and hours as rupees: what a trip cost against its plan.

The model is deliberately plain, because every rate in it is an assumption
the operator must own, and a plain model is one they can check by hand:

    planned cost   = planned km × ₹/km  +  planned transit h × ₹/h
    actual cost    = actual km  × ₹/km  +  actual transit h  × ₹/h
    variance       = planned cost − actual cost      (+ saving, − loss)
    detention      = (loading h − free h)⁺ + (unloading h − free h)⁺, × ₹/h
    margin         = freight (₹/planned km × planned km) − actual cost − detention
                     (only when a freight rate is configured)

₹/km is running cost: diesel, tyres, maintenance. ₹/h is time cost: the
driver and the truck's fixed costs for every hour it is on the road instead of
earning. The defaults (ROUTE_COST_PER_KM, ROUTE_COST_PER_HOUR, …) are
placeholders to be replaced with the fleet's own figures; the Routes page
lets anyone try other rates without changing them for everyone.

The variance splits into its distance part and its time part, so "₹2,400
lost" always comes with "because 61 km more and 3 h longer".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rates:
    per_km: float = 28.0
    per_hour: float = 150.0
    detention_per_hour: float = 150.0
    free_hours: float = 4.0
    revenue_per_km: float | None = None
    # A variance inside this share of the plan's cost is "on plan".
    tolerance_pct: float = 3.0

    def as_dict(self) -> dict:
        return {"per_km": self.per_km, "per_hour": self.per_hour,
                "detention_per_hour": self.detention_per_hour, "free_hours": self.free_hours,
                "revenue_per_km": self.revenue_per_km, "tolerance_pct": self.tolerance_pct}


def trip_cost(planned_km: float | None, planned_h: float | None, actual_km: float | None,
              actual_h: float | None, loading_h: float | None, unloading_h: float | None,
              rates: Rates) -> dict:
    """The money view of one trip. Missing inputs give missing outputs, never zeros."""
    out = {"cost_plan": None, "cost_actual": None, "variance": None, "variance_km": None,
           "variance_time": None, "detention_h": None, "detention_cost": None, "revenue": None,
           "margin": None, "verdict": None}
    if actual_km is None or actual_h is None:
        return out
    actual = actual_km * rates.per_km + actual_h * rates.per_hour
    out["cost_actual"] = round(actual, 0)
    det_h = sum(max(0.0, (h or 0.0) - rates.free_hours) for h in (loading_h, unloading_h))
    out["detention_h"] = round(det_h, 2)
    out["detention_cost"] = round(det_h * rates.detention_per_hour, 0)
    if planned_km is not None and planned_h is not None:
        plan = planned_km * rates.per_km + planned_h * rates.per_hour
        out["cost_plan"] = round(plan, 0)
        out["variance"] = round(plan - actual, 0)
        out["variance_km"] = round((planned_km - actual_km) * rates.per_km, 0)
        out["variance_time"] = round((planned_h - actual_h) * rates.per_hour, 0)
        band = plan * rates.tolerance_pct / 100
        v = plan - actual
        out["verdict"] = "saving" if v > band else "loss" if v < -band else "on_plan"
        if rates.revenue_per_km:
            out["revenue"] = round(planned_km * rates.revenue_per_km, 0)
            out["margin"] = round(out["revenue"] - actual - out["detention_cost"], 0)
    return out
