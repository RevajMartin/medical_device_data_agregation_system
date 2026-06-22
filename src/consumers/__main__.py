"""Worker entry point: ``python -m src.consumers <alerts|scoring>``.

Each name maps to (queue, binding key, handler, prefetch). The two run in separate
containers so a slow scoring job never blocks latency-sensitive alerts (the same
workload-profile split as before).
"""

import asyncio
import logging
import sys

from src.consumers.alerts import process_measurement
from src.consumers.runner import run_consumer
from src.consumers.scoring import compute_risk_score

# name -> (queue, binding_key, handler, prefetch)
CONSUMERS = {
    "alerts": ("alerts", "measurement.created", process_measurement, 50),
    "scoring": ("scoring", "riskscore.requested", compute_risk_score, 20),
}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    if len(sys.argv) != 2 or sys.argv[1] not in CONSUMERS:
        raise SystemExit(f"usage: python -m src.consumers <{'|'.join(CONSUMERS)}>")
    queue, binding_key, handler, prefetch = CONSUMERS[sys.argv[1]]
    asyncio.run(run_consumer(queue, binding_key, handler, prefetch))


if __name__ == "__main__":
    main()
