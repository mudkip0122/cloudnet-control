"""Shared real-metric sampler and standalone console output."""
import argparse
from datetime import datetime, timezone
import json
import time

import psutil


def samples(pc_id):
    psutil.cpu_percent(interval=None)
    previous = None
    try:
        while True:
            time.sleep(2)
            cpu = ram = upload = download = None
            try:
                cpu = psutil.cpu_percent(interval=None)
            except (OSError, psutil.Error):
                pass
            try:
                ram = psutil.virtual_memory().percent
            except (OSError, psutil.Error):
                pass
            try:
                counters = psutil.net_io_counters()
                now = time.monotonic()
                if counters is None:
                    previous = None
                else:
                    if previous is not None:
                        old, then = previous
                        elapsed = now - then
                        if elapsed > 0 and counters.bytes_sent >= old.bytes_sent and counters.bytes_recv >= old.bytes_recv:
                            upload = (counters.bytes_sent - old.bytes_sent) * 8 / elapsed / 1_000_000
                            download = (counters.bytes_recv - old.bytes_recv) * 8 / elapsed / 1_000_000
                    previous = (counters, now)
            except (OSError, psutil.Error):
                previous = None
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
