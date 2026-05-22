"""DBM-CHED NBC 461 KRA score computation and qualification checks."""
import json
from typing import Any


def _parse_json(value: Any, default=None):
    if default is None:
        default = []
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def rule_to_dict(rule: dict) -> dict:
    """Normalize a kra_rules row for templates and computation."""
    return {
        "id": rule["id"],
        "kra_slug": rule.get("kra_slug") or _slugify(rule["kra_name"]),
        "kra_name": rule["kra_name"],
        "weight": float(rule.get("weight") or 0),
        "min_score": float(rule.get("min_score") or 0),
        "validation_rules": rule.get("validation_rules") or "",
        "description": rule.get("description") or "",
        "criteria": _parse_json(rule.get("criteria_json"), []),
        "indicators": _parse_json(rule.get("indicators_json"), []),
        "point_values": _parse_json(rule.get("point_values_json"), []),
        "documentary_requirements": _parse_json(rule.get("documentary_json"), []),
        "auto_compute": bool(rule.get("auto_compute", 1)),
        "updated_at": rule.get("updated_at") or "",
    }


def _slugify(name: str) -> str:
    return name.lower().replace(",", "").replace(" ", "-")[:48]


def compute_kra_breakdown(rules: list[dict], raw_scores: dict[str, float]) -> dict:
    """
    Compute weighted KRA scores from configured rules and per-KRA accomplishment scores (0–100).

    raw_scores keys: kra_slug or kra_name
    """
    normalized = {_slugify(k): float(v) for k, v in raw_scores.items()}
    items = []
    total_weight = 0.0
    weighted_sum = 0.0

    for rule in rules:
        r = rule_to_dict(rule) if "criteria" not in rule else rule
        slug = r["kra_slug"]
        raw = normalized.get(slug)
        if raw is None:
            raw = normalized.get(_slugify(r["kra_name"]), 0.0)
        raw = max(0.0, min(100.0, float(raw)))

        weight = r["weight"]
        total_weight += weight
        weighted = (raw * weight / 100.0) if weight else 0.0
        weighted_sum += weighted
        passed = raw >= r["min_score"]

        items.append({
            "id": r["id"],
            "kra_slug": slug,
            "kra_name": r["kra_name"],
            "weight": weight,
            "min_score": r["min_score"],
            "raw_score": round(raw, 2),
            "weighted_contribution": round(weighted, 2),
            "passed": passed,
            "auto_compute": r["auto_compute"],
        })

    total_score = round(weighted_sum, 2) if total_weight else 0.0
    if total_weight and abs(total_weight - 100.0) > 0.01:
        scale = 100.0 / total_weight
        total_score = round(weighted_sum * scale, 2)
        for item in items:
            item["weighted_contribution"] = round(
                item["weighted_contribution"] * scale, 2
            )

    all_passed = all(i["passed"] for i in items) if items else False
    return {
        "kra_items": items,
        "total_score": total_score,
        "total_weight": round(total_weight, 2),
        "weights_valid": abs(total_weight - 100.0) <= 0.01,
        "all_kras_passed": all_passed,
    }


def check_reclassification(
    breakdown: dict,
    promotion_min: float,
    ched_required: bool = True,
) -> dict:
    """Automated qualification check for faculty reclassification / rank simulation."""
    total = breakdown["total_score"]
    meets_total = total >= promotion_min
    meets_kras = breakdown["all_kras_passed"]
    qualified = meets_total and meets_kras

    reasons = []
    if not meets_total:
        reasons.append(f"Total score {total}% is below minimum {promotion_min}%")
    if not meets_kras:
        failed = [i["kra_name"] for i in breakdown["kra_items"] if not i["passed"]]
        reasons.append(f"Below minimum on: {', '.join(failed)}")
    if ched_required:
        reasons.append("CHED compliance documentation must be verified by reviewer")

    return {
        "qualified": qualified and not ched_required,
        "simulated_qualified": qualified,
        "meets_total_score": meets_total,
        "meets_all_kra_minimums": meets_kras,
        "promotion_min": promotion_min,
        "notes": reasons if reasons else ["Meets configured DBM-CHED NBC 461 thresholds"],
    }


def default_simulation_scores(rules: list[dict]) -> dict[str, float]:
    """Demo accomplishment scores for rank simulation preview."""
    defaults = {
        "instruction": 82,
        "research-innovation-and-creative-work": 78,
        "extension-services": 74,
        "professional-development": 68,
    }
    out = {}
    for rule in rules:
        r = rule_to_dict(rule)
        out[r["kra_slug"]] = defaults.get(r["kra_slug"], 75.0)
    return out
