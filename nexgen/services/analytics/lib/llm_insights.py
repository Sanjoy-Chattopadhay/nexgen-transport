"""
LLM Insight Layer (Azure OpenAI)
--------------------------------
Turns a structured GPS analysis bundle (e.g. tta_plant_delay.build_plant_delay)
into a plain-English, ops-ready narrative: what the GPS actually shows, the
most likely root cause, and concrete recommendations.

Design notes:
  - Uses the Azure OpenAI chat deployment configured in settings
    (AZURE_OPENAI_* — see config/settings.py). The deployment name, not a
    model id, is what Azure routes on.
  - The model is given ONLY the compact structured facts we computed from
    GPS — never asked to invent numbers. It reasons over our evidence.
  - Fully graceful: if no key is configured, or the call fails, we return a
    deterministic rule-based narrative so the endpoint never hard-fails.
  - `source` in the result tells the caller which path produced it
    ("azure_openai" | "fallback").
"""

import json
import logging

from nexgen.shared.legacy_settings import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a fleet operations analyst for an Indian logistics operator. "
    "You are given GPS-derived facts about a truck's time in and around the "
    "origin plant before line-haul. Every number is already computed from the "
    "raw GPS ping trail — trust it, do not invent figures. Explain, in crisp "
    "operational language, WHY the truck lost time inside/around the plant, "
    "distinguishing genuine in-plant handling (gate, weighbridge, loading) "
    "from yard/parking waits outside the gate. Be specific about the "
    "bottleneck location. Respond ONLY with a JSON object matching the "
    "requested schema."
)

RESPONSE_SCHEMA_HINT = (
    "Return JSON with exactly these keys:\n"
    '  "headline": one-sentence summary of the delay (<= 20 words),\n'
    '  "root_cause": 2-3 sentences naming the most likely cause and the '
    "evidence that points to it,\n"
    '  "what_gps_shows": 2-3 sentences contrasting the declared metric with '
    "what the ping trail reveals,\n"
    '  "recommendations": array of 2-4 short, concrete, actionable strings,\n'
    '  "severity": one of "low" | "moderate" | "high" | "critical",\n'
    '  "confidence": one of "low" | "medium" | "high".'
)


def _compact_facts(analysis: dict) -> dict:
    """Trim the full bundle to the facts the model needs (keeps tokens/cost down)."""
    return {
        "trip_no": analysis.get("trip_no"),
        "route": analysis.get("route"),
        "origin": analysis.get("origin"),
        "declared_metrics": analysis.get("declared"),
        "origin_anchor": analysis.get("origin_anchor"),
        "window": analysis.get("window"),
        "kpis": analysis.get("kpis"),
        "delay_reason": analysis.get("delay_reason"),
        "top_stations": analysis.get("stations", [])[:6],
    }


def _get_client():
    from openai import AzureOpenAI
    return AzureOpenAI(
        api_key=settings.AZURE_OPENAI_API_KEY,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_version=settings.AZURE_OPENAI_VERSION,
    )


def _fallback(analysis: dict) -> dict:
    """Deterministic narrative when the LLM is unavailable."""
    if not analysis.get("has_gps"):
        return {
            "source": "fallback",
            "headline": "No GPS available for this trip.",
            "root_cause": "This trip has no stored GPS pings, so the in-plant "
                          "delay cannot be reconstructed from movement data.",
            "what_gps_shows": "Only the declared provider metrics are available.",
            "recommendations": ["Verify device/telematics coverage for this vehicle."],
            "severity": "low",
            "confidence": "low",
        }

    dr = analysis.get("delay_reason", {})
    k = analysis.get("kpis", {})
    declared = analysis.get("declared", {})
    loc = dr.get("location")
    idle = k.get("idle_min", 0) or 0
    yard = k.get("yard_dwell_min", 0) or 0
    in_plant = k.get("in_plant_dwell_min", 0) or 0
    excess = k.get("excess_over_benchmark_min", 0) or 0

    sev = "low"
    if idle >= 1440:
        sev = "critical"
    elif idle >= 480:
        sev = "high"
    elif idle >= 240:
        sev = "moderate"

    gps_line = (
        f"Declared plant time reads {declared.get('plant_vivo_label') or 'n/a'}, "
        f"but the ping trail places {in_plant:.0f} min at genuine in-plant "
        f"handling and {yard:.0f} min at yard/parking away from the gate."
    )
    return {
        "source": "fallback",
        "headline": f"{dr.get('primary', 'Delay')} at {loc or 'origin'} "
                    f"cost ~{dr.get('dwell_min', 0):.0f} min.",
        "root_cause": dr.get("evidence", "Unable to attribute a single cause."),
        "what_gps_shows": gps_line,
        "recommendations": _fallback_recos(dr.get("primary"), excess),
        "severity": sev,
        "confidence": "medium",
    }


def _fallback_recos(primary: str | None, excess: float) -> list[str]:
    recos = {
        "Yard / parking wait": [
            "Investigate why the vehicle was parked in the transporter yard "
            "instead of moving to line-haul after gate-out.",
            "Set a yard-dwell SLA and alert dispatch when it is breached.",
        ],
        "Gate queue": [
            "Review gate-in/gate-out throughput and appointment scheduling.",
            "Stagger vehicle arrivals to flatten the gate queue peak.",
        ],
        "Weighbridge": [
            "Check weighbridge capacity/availability during this shift.",
            "Consider a second weighing lane or pre-weighed slots.",
        ],
        "Loading / plant floor": [
            "Audit dock/loading-bay availability and material readiness.",
            "Align vehicle placement with loading-crew shifts.",
        ],
    }.get(primary, [
        "Review the origin-region standstill against the turnaround SLA.",
        "Add a dwell-time alert for stations exceeding the benchmark.",
    ])
    if excess and excess > 0:
        recos.append(
            f"This trip ran ~{excess:.0f} min over the turnaround benchmark — "
            "flag it for a detention review."
        )
    return recos


def generate_insight(analysis: dict) -> dict:
    """Narrate an analysis bundle. Never raises — falls back on any problem."""
    if not settings.AZURE_OPENAI_READY:
        logger.info("Azure OpenAI not configured — using rule-based fallback.")
        return _fallback(analysis)

    try:
        client = _get_client()
        facts = _compact_facts(analysis)
        resp = client.chat.completions.create(
            model=settings.AZURE_OPENAI_CHAT_DEPLOYMENT_NAME,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": (
                    RESPONSE_SCHEMA_HINT
                    + "\n\nGPS-derived facts:\n"
                    + json.dumps(facts, ensure_ascii=False, default=str)
                )},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        parsed = json.loads(content)
        parsed["source"] = "azure_openai"
        parsed["model"] = settings.AZURE_OPENAI_CHAT_DEPLOYMENT_NAME
        return parsed
    except Exception as exc:  # noqa: BLE001 — endpoint must never hard-fail on LLM
        logger.warning("Azure OpenAI insight failed (%s) — falling back.", exc)
        fb = _fallback(analysis)
        fb["llm_error"] = str(exc)
        return fb
