"""Render report figures from Prometheus over the load-test window.

Reads the [start, end] epoch window written by the load run (scripts/loadout/window.txt),
range-queries Prometheus (localhost:9090), and writes PNG charts into
docs/diagrams/metrics/ for embedding in the report. Also prints the Locust summary row.

Run after the load test:  python scripts/render_metrics.py
"""

import csv
import os
import sys
import time
from pathlib import Path

import httpx
import matplotlib

matplotlib.use("Agg")
from datetime import datetime  # noqa: E402

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

PROM = os.environ.get("PROM_URL", "http://localhost:9090")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "diagrams" / "metrics"
LOAD = ROOT / "scripts" / "loadout"

plt.rcParams.update({"figure.dpi": 130, "font.size": 10, "axes.grid": True, "grid.alpha": 0.3})


def window() -> tuple[float, float]:
    f = LOAD / "window.txt"
    if f.exists():
        s, e = f.read_text().split()
        return float(s) - 20, float(e) + 5
    now = time.time()
    return now - 600, now


def q_range(expr: str, start: float, end: float, step: int = 5):
    r = httpx.get(
        f"{PROM}/api/v1/query_range",
        params={"query": expr, "start": start, "end": end, "step": step},
        timeout=15,
    )
    r.raise_for_status()
    out = []
    for series in r.json()["data"]["result"]:
        xs = [datetime.fromtimestamp(float(t)) for t, _ in series["values"]]
        ys = [float(v) for _, v in series["values"]]
        out.append((series.get("metric", {}), xs, ys))
    return out


def _fmt_time(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(0)


def chart(expr, title, ylabel, fname, start, end, label_key=None, step=5):
    series = q_range(expr, start, end, step)
    if not series:
        print(f"  [skip] {fname}: no data for {expr!r}")
        return
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    for metric, xs, ys in series:
        label = metric.get(label_key) if label_key else None
        ax.plot(xs, ys, linewidth=1.8, label=label)
    ax.set_title(title, fontweight="bold")
    ax.set_ylabel(ylabel)
    ax.set_ylim(bottom=0)
    _fmt_time(ax)
    if label_key:
        ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / fname, bbox_inches="tight")
    plt.close(fig)
    print(f"  [ok]   {fname}")


def locust_summary():
    f = LOAD / "results_stats.csv"
    if not f.exists():
        print("  [skip] locust summary: results_stats.csv not found")
        return
    with f.open() as fh:
        rows = list(csv.DictReader(fh))
    agg = next((r for r in rows if r.get("Name") == "Aggregated"), None)
    if not agg:
        return
    print("\nLocust summary (Aggregated):")
    for k in [
        "Request Count",
        "Failure Count",
        "Requests/s",
        "Median Response Time",
        "95%",
        "99%",
        "Max Response Time",
    ]:
        if k in agg:
            print(f"  {k:24}: {agg[k]}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    start, end = window()
    print(f"Prometheus window: {datetime.fromtimestamp(start)} .. {datetime.fromtimestamp(end)}")
    chart(
        "sum(rate(http_requests_total[20s]))",
        "API request throughput",
        "requests / s",
        "api_throughput.png",
        start,
        end,
    )
    chart(
        "sum by (status) (rate(http_requests_total[20s]))",
        "API requests by HTTP status",
        "requests / s",
        "api_status.png",
        start,
        end,
        label_key="status",
    )
    _latency_multi(start, end)
    chart(
        'rabbitmq_queue_messages_ready{queue=~"alerts|scoring"}',
        "RabbitMQ queue depth (build during burst -> drain)",
        "ready messages",
        "queue_depth.png",
        start,
        end,
        label_key="queue",
    )
    locust_summary()


def _latency_multi(start, end):
    """Latency chart with p50/p95/p99 overlaid."""
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    drawn = False
    # Use the high-resolution histogram (many fine buckets) for accurate tail percentiles;
    # the default http_request_duration_seconds buckets top out at ~1s and clamp p95/p99.
    for q, name in [(0.50, "p50"), (0.95, "p95"), (0.99, "p99")]:
        expr = (
            f"histogram_quantile({q}, sum by (le) "
            f"(rate(http_request_duration_highr_seconds_bucket[30s])))"
        )
        series = q_range(expr, start, end)
        for _, xs, ys in series:
            ax.plot(xs, ys, linewidth=1.8, label=name)
            drawn = True
    if not drawn:
        plt.close(fig)
        print("  [skip] api_latency.png: no histogram data")
        return
    ax.set_title("API request latency percentiles", fontweight="bold")
    ax.set_ylabel("seconds")
    ax.set_ylim(bottom=0)
    _fmt_time(ax)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "api_latency.png", bbox_inches="tight")
    plt.close(fig)
    print("  [ok]   api_latency.png (p50/p95/p99)")


if __name__ == "__main__":
    sys.exit(main())
