"""
peak_lab_sports: shared sport-bucket mapping for bin/training-metrics and
bin/render-analysis.

Garmin's activityType.typeKey is finely subdivided (road_biking, gravel_cycling,
mountain_biking, indoor_cycling, …). For peak-lab's purposes we coalesce those
into a small set of coarse "sport buckets" — running, cycling, swimming,
strength_training, hiking, walking, mobility, rowing, cardio_other, other.

Keeping the mapping in one place means the renderer's fallback aggregation
(used when training-metrics.json is absent) cannot drift from the helper's
canonical output.

stdlib only.
"""

from __future__ import annotations


# Canonical bucket order. Used by the renderer to stack the weekly-volume
# chart in a consistent direction and to lay out the legend. New buckets must
# be appended (not inserted) so existing reports stay visually stable.
BUCKETS: tuple[str, ...] = (
    "running",
    "cycling",
    "swimming",
    "hiking",
    "walking",
    "strength_training",
    "rowing",
    "mobility",
    "cardio_other",
    "other",
)


# Sport colors for the stacked weekly-volume chart. Running keeps the original
# teal so single-sport runners see the same look as before this change.
COLORS: dict[str, str] = {
    "running": "#14b8a6",
    "cycling": "#a78bfa",
    "swimming": "#38bdf8",
    "hiking": "#84cc16",
    "walking": "#fbbf24",
    "strength_training": "#f472b6",
    "rowing": "#fb7185",
    "mobility": "#94a3b8",
    "cardio_other": "#fcd34d",
    "other": "#9ca3af",
}


# Sports that have meaningful distance to stack on a km-per-week chart.
# Strength / mobility / rowing report sessions and duration but no useful km.
DISTANCE_SPORTS: tuple[str, ...] = (
    "running",
    "cycling",
    "hiking",
    "walking",
    "swimming",
)


def bucket_for(type_key: str | None) -> str:
    """Map a Garmin activityType.typeKey to one of BUCKETS.

    Substring matching (not exact) so future Garmin variants like
    `trail_running` or `gravel_cycling` route to the right bucket without a
    code change.
    """
    tk = (type_key or "").lower()
    if "running" in tk:
        return "running"
    if "cycling" in tk or "biking" in tk:
        return "cycling"
    if "swim" in tk:
        return "swimming"
    if "strength" in tk or "weight" in tk:
        return "strength_training"
    if "hik" in tk:
        return "hiking"
    if "walk" in tk:
        return "walking"
    if "yoga" in tk or "pilates" in tk or "mobility" in tk or "stretch" in tk:
        return "mobility"
    if "row" in tk:
        return "rowing"
    if "elliptical" in tk or "cardio" in tk or "stair" in tk:
        return "cardio_other"
    return tk or "other"
