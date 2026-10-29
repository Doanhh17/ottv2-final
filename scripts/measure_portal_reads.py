"""Bounded read-only HTTP probe; public pages are not an authenticated user test."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
from urllib.error import HTTPError, URLError


def measure(url, requests, concurrency, rps):
    parts = urlsplit(url)
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
        raise ValueError('Use only a base URL without credentials, path or query.')
    if parts.scheme != 'https' and not (parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost')):
        raise ValueError('Use HTTPS, or HTTP on loopback only.')
    if not 1 <= requests <= 500 or not 1 <= concurrency <= 32 or not 0 < rps <= 100:
        raise ValueError('Limits: 500 requests, 32 connections, 100 requests/second.')
    if parts.hostname not in ('127.0.0.1', 'localhost') and rps > 5:
        raise ValueError('Public probe is capped at 5 requests/second to respect the shared IP limit.')
    started = time.monotonic()
    def request(index):
        path = ('/', '/health', '/board.svg')[index % 3]
        t = time.monotonic()
        size = 0
        try:
            with urlopen(url.rstrip('/') + path, timeout=5) as response:
                code = response.status
                size = len(response.read(1024 * 1024))
        except HTTPError as error:
            code = error.code
            error.close()
        except (URLError, OSError):
            code = 'network_error'
        return {'status': code, 'seconds': time.monotonic()-t, 'bytes': size}
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        pending = []
        for index in range(requests):
            time.sleep(max(0, started + index / rps - time.monotonic()))
            pending.append(executor.submit(request, index))
        rows = [future.result() for future in pending]
    elapsed = time.monotonic() - started
    times = sorted(row['seconds'] for row in rows)
    return {'measured_at_utc': datetime.now(timezone.utc).isoformat(),
            'url': url, 'requests': requests, 'concurrency_limit': concurrency,
            'offered_rps': rps, 'elapsed_seconds': elapsed, 'achieved_rps': requests/elapsed,
            'status_counts': dict(Counter(str(row['status']) for row in rows)),
            'p50_seconds': times[math.ceil(len(times)*0.5)-1],
            'p95_seconds': times[math.ceil(len(times)*0.95)-1], 'max_seconds': max(times),
            'received_bytes': sum(row['bytes'] for row in rows),
            'scope': 'Public HTML, health, board only; no login, authenticated dashboard, match creation or log download.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--requests', type=int, default=60)
    parser.add_argument('--concurrency', type=int, default=8)
    parser.add_argument('--rps', type=float, default=3)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; choose a new path.')
    report = measure(args.url, args.requests, args.concurrency, args.rps)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps(report))
