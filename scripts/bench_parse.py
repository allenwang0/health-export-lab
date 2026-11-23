"""Time the parser on a SYNTHETIC 1,000,000-record export zip and write results/parse_bench.json.

    python scripts/bench_parse.py [--records 1000000] [--reps 5] [--out results/parse_bench.json]

The export is built by tests/synthetic.bulk (heart-rate records) and zipped the way the Health
app ships it; only parse.ingest is timed, once per repetition into a fresh directory. Load
average is read with `sysctl -n vm.loadavg` (os.getloadavg elsewhere) before and after.
"""
import argparse
import contextlib
import io
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from healthlab import parse  # noqa: E402
import synthetic  # noqa: E402


def sysctl(name):
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def loadavg():
    raw = sysctl("vm.loadavg")
    if raw:
        return [float(v) for v in raw.strip("{} ").split()[:3]]
    return [round(v, 2) for v in os.getloadavg()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", type=int, default=1_000_000)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", default=str(ROOT / "results" / "parse_bench.json"))
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        xml = tmp / "export.xml"
        synthetic.bulk(xml, a.records)
        zpath = tmp / "export.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(xml, "apple_health_export/export.xml")
        xml_bytes, zip_bytes = xml.stat().st_size, zpath.stat().st_size
        xml.unlink()
        before = loadavg()
        times = []
        for i in range(a.reps):
            out = tmp / f"run{i}"
            t = time.perf_counter()
            with contextlib.redirect_stdout(io.StringIO()):
                parse.ingest(str(zpath), out)
            times.append(round(time.perf_counter() - t, 3))
            print(f"rep {i + 1}: {times[-1]:.2f} s", flush=True)
        after = loadavg()
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    result = dict(synthetic=True, records=a.records, record_type="HeartRate", xml_bytes=xml_bytes, zip_bytes=zip_bytes,
                  reps=a.reps, seconds=times, median_s=statistics.median(times), min_s=min(times), max_s=max(times),
                  timer="time.perf_counter around parse.ingest", loadavg_before=before, loadavg_after=after,
                  machine=dict(cpu=sysctl("machdep.cpu.brand_string") or platform.processor(), cores=os.cpu_count(),
                               memory_gib=round(int(sysctl("hw.memsize") or 0) / 2**30) or None, os=platform.platform(terse=True)),
                  python=platform.python_version(), code_sha=sha, date=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(result, indent=1) + "\n")
    print(f"median {result['median_s']:.2f} s over {a.reps} reps, load {before} -> {after}; wrote {a.out}")


if __name__ == "__main__":
    main()
