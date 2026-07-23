"""Data analysis skill — descriptive stats, trend, anomaly detection."""
from __future__ import annotations

import json
import logging
import statistics
from typing import Any

logger = logging.getLogger(__name__)


def _parse_data(data: str) -> list[float]:
    """Parse a data string into a list of floats.

    Accepts JSON arrays, comma-separated values, or newline-separated values.
    """
    data = data.strip()
    if not data:
        return []

    # Try JSON first
    try:
        parsed = json.loads(data)
        if isinstance(parsed, list):
            return [float(x) for x in parsed if _is_numeric(x)]
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Try comma-separated or newline-separated
    parts = data.replace("\n", ",").split(",")
    result: list[float] = []
    for part in parts:
        part = part.strip()
        if part:
            try:
                result.append(float(part))
            except ValueError:
                continue
    return result


def _is_numeric(value: Any) -> bool:
    """Check if a value is numeric."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


async def analyze_data(data: str, analysis_type: str = "summary") -> str:
    """Analyze numeric data and return insights.

    Args:
        data: Input data as a string (JSON array, CSV, or newline-separated).
        analysis_type: Type of analysis — ``summary``, ``trend``, or ``anomaly``.

    Returns:
        A formatted analysis report.
    """
    logger.info("analyze_data: type=%s, data_len=%d", analysis_type, len(data))
    values = _parse_data(data)

    if not values:
        return "Error: no numeric data found in input."

    n = len(values)
    mean_val = statistics.mean(values)
    analysis_type = analysis_type.lower().strip()

    if analysis_type == "summary":
        median_val = statistics.median(values)
        stdev_val = statistics.stdev(values) if n > 1 else 0.0
        min_val = min(values)
        max_val = max(values)
        total = sum(values)

        lines = [
            f"Data Summary ({n} values):",
            f"  Mean:    {mean_val:.4f}",
            f"  Median:  {median_val:.4f}",
            f"  Std Dev: {stdev_val:.4f}",
            f"  Min:     {min_val:.4f}",
            f"  Max:     {max_val:.4f}",
            f"  Range:   {max_val - min_val:.4f}",
            f"  Sum:     {total:.4f}",
        ]
        if n > 2:
            try:
                q1 = statistics.quantiles(values, n=4)[0]
                q3 = statistics.quantiles(values, n=4)[2]
                lines.append(f"  Q1:      {q1:.4f}")
                lines.append(f"  Q3:      {q3:.4f}")
                lines.append(f"  IQR:     {q3 - q1:.4f}")
            except statistics.StatisticsError:
                pass
        return "\n".join(lines)

    elif analysis_type == "trend":
        if n < 2:
            return "Trend analysis requires at least 2 data points."
        x_mean = (n - 1) / 2
        y_mean = mean_val
        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        slope = numerator / denominator if denominator else 0.0
        intercept = y_mean - slope * x_mean

        if slope > 0.01:
            direction = "upward (increasing)"
        elif slope < -0.01:
            direction = "downward (decreasing)"
        else:
            direction = "flat (stable)"

        change = values[-1] - values[0]
        if values[0] != 0:
            pct = (change / values[0]) * 100
            change_str = f"{change:.4f} ({pct:.2f}%)"
        else:
            change_str = f"{change:.4f}"

        lines = [
            f"Trend Analysis ({n} values):",
            f"  Direction:    {direction}",
            f"  Slope:        {slope:.6f}",
            f"  Intercept:    {intercept:.4f}",
            f"  Start value:  {values[0]:.4f}",
            f"  End value:    {values[-1]:.4f}",
            f"  Change:       {change_str}",
        ]
        return "\n".join(lines)

    elif analysis_type == "anomaly":
        if n < 3:
            return "Anomaly detection requires at least 3 data points."
        stdev_val = statistics.stdev(values) if n > 1 else 0.0
        if stdev_val == 0:
            return f"Anomaly Detection ({n} values):\n  No variation detected — all values are {mean_val:.4f}"

        threshold = 2.0  # 2 standard deviations
        anomalies: list[dict[str, Any]] = []
        for i, v in enumerate(values):
            z_score = abs(v - mean_val) / stdev_val
            if z_score > threshold:
                anomalies.append({
                    "index": i,
                    "value": v,
                    "z_score": round(z_score, 4),
                    "direction": "high" if v > mean_val else "low",
                })

        anomaly_word = "anomaly" if len(anomalies) == 1 else "anomalies"
        lines = [
            f"Anomaly Detection ({n} values, threshold={threshold}sigma):",
            f"  Mean:   {mean_val:.4f}",
            f"  StdDev: {stdev_val:.4f}",
            f"  Found {len(anomalies)} {anomaly_word}:",
        ]
        if anomalies:
            for a in anomalies:
                lines.append(
                    f"    Index {a['index']}: value={a['value']:.4f} "
                    f"(z={a['z_score']}, {a['direction']})"
                )
        else:
            lines.append("    No anomalies detected.")
        return "\n".join(lines)

    else:
        return (
            f"Unknown analysis type: '{analysis_type}'. "
            f"Supported types: summary, trend, anomaly."
        )
