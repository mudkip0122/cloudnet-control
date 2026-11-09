"""Shared real-metric sampler and standalone console output."""
import argparse
from datetime import datetime, timezone
import json
import logging
import time

import psutil

LOG = logging.getLogger("cloudnet.agent")


def network_rates(current, previous, elapsed):
    if current is None or previous is None or elapsed <= 0:
        return None, None
    sent = current.bytes_sent - previous.bytes_sent
    received = current.bytes_recv - previous.bytes_recv
    if sent < 0 or received < 0:
        return None, None
    return sent * 8 / elapsed / 1_000_000, received * 8 / elapsed / 1_000_000


def samples(pc_id):
    cpu_ready = False
    try:
        psutil.cpu_percent(interval=None)
        cpu_ready = True
    except (OSError, psutil.Error):
        LOG.warning("CPU sampler initialization failed; retrying on next sample")
    previous = None
    try:
        while True:
            time.sleep(2)
            cpu = ram = upload = download = None
            try:
                measured = psutil.cpu_percent(interval=None)
                cpu = measured if cpu_ready else None
                cpu_ready = True
            except (OSError, psutil.Error):
                cpu_ready = False
                LOG.warning("CPU collection failed; sending null")
            try:
                ram = psutil.virtual_memory().percent
            except (OSError, psutil.Error):
                LOG.warning("RAM collection failed; sending null")
            try:
                counters = psutil.net_io_counters()
                now = time.monotonic()
                if counters is None:
                    previous = None
                else:
                    if previous is not None:
                        old, then = previous
                        elapsed = now - then
                        upload, download = network_rates(counters, old, elapsed)
                    previous = (counters, now)
            except (OSError, psutil.Error):
                previous = None
                LOG.warning("Network collection failed; sending null and resetting baseline")
            yield {"type": "METRICS", "pc_id": pc_id,
                              "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                              "cpu_percent": cpu, "ram_percent": ram,
                   "upload_mbps": upload, "download_mbps": download}
    except KeyboardInterrupt:
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pc-id", required=True)
    args = parser.parse_args()
    try:
        for message in samples(args.pc_id):
            print(json.dumps(message, allow_nan=False), flush=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
