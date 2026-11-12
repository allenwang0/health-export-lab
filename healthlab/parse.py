"""Streaming, lossless Apple Health export parser.

Reads export.zip (or an export.xml path) without extracting it and writes every
element to Parquet as raw strings. Nothing is typed, converted or dropped here:
normalisation happens in build.py, so it can be re-run without re-parsing.
"""
import hashlib
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree

BATCH_ROWS = 400_000

SCHEMAS = {
    "records": ["type", "source_name", "source_version", "device", "unit", "value",
                "creation_date", "start_date", "end_date", "metadata"],
    "hrv_beats": ["parent_start", "parent_source", "bpm", "time"],
    "workouts": ["workout_id", "activity_type", "duration", "duration_unit", "source_name",
                 "source_version", "device", "creation_date", "start_date", "end_date",
                 "attributes", "metadata"],
    "workout_stats": ["workout_id", "type", "start_date", "end_date", "unit", "sum",
                      "average", "minimum", "maximum"],
    "workout_events": ["workout_id", "type", "date", "duration", "duration_unit"],
    "workout_routes": ["workout_id", "route_path", "source_name", "start_date", "end_date",
                       "metadata"],
    "activity_summaries": ["date", "active_energy", "active_energy_goal", "active_energy_unit",
                           "move_time", "move_time_goal", "exercise_time", "exercise_time_goal",
                           "stand_hours", "stand_hours_goal"],
    "correlations": ["correlation_id", "type", "source_name", "device", "creation_date",
                     "start_date", "end_date", "metadata", "member_type", "member_value",
                     "member_unit"],
    "clinical_records": ["type", "identifier", "source_name", "source_url", "fhir_version",
                         "received_date", "resource_path"],
    "route_points": ["route_path", "time", "lat", "lon", "ele", "speed", "course",
                     "h_acc", "v_acc"],
    "ecg": ["file", "key", "value"],
}


class Writer:
    """Buffers rows per table and flushes numbered Parquet parts."""

    def __init__(self, out: Path):
        self.out, self.buf, self.part, self.count = out, {}, {}, {}
        for table, cols in SCHEMAS.items():
            (out / table).mkdir(parents=True, exist_ok=True)
            self.buf[table], self.part[table], self.count[table] = [[] for _ in cols], 0, 0

    def add(self, table, row):
        cols = self.buf[table]
        for c, v in zip(cols, row):
            c.append(v)
        self.count[table] += 1
        if len(cols[0]) >= BATCH_ROWS:
            self.flush(table)

    def flush(self, table):
        cols = self.buf[table]
        if not cols[0]:
            return
        arrays = [pa.array(c, type=pa.string()) for c in cols]
        tbl = pa.Table.from_arrays(arrays, names=SCHEMAS[table])
        pq.write_table(tbl, self.out / table / f"part-{self.part[table]:05d}.parquet",
                       compression="zstd")
        self.part[table] += 1
        self.buf[table] = [[] for _ in SCHEMAS[table]]

    def close(self):
        for table, cols in SCHEMAS.items():
            self.flush(table)
            if self.part[table] == 0:  # an empty table still needs a schema for DuckDB
                pq.write_table(pa.table({c: pa.array([], pa.string()) for c in cols}),
                               self.out / table / "part-00000.parquet")


def _meta(elem):
    out = {}
    for child in elem:
        if child.tag == "MetadataEntry":
            out[child.get("key")] = child.get("value")
    return json.dumps(out, sort_keys=True) if out else None


def _stable_id(*parts):
    return hashlib.blake2b("|".join(p or "" for p in parts).encode(), digest_size=8).hexdigest()


def _workout(el, w):
    a = el.attrib
    wid = _stable_id(a.get("workoutActivityType"), a.get("sourceName"), a.get("startDate"),
                     a.get("endDate"))
    known = {"workoutActivityType", "duration", "durationUnit", "sourceName", "sourceVersion",
             "device", "creationDate", "startDate", "endDate"}
    extra = {k: v for k, v in a.items() if k not in known}
    w.add("workouts", [wid, a.get("workoutActivityType"), a.get("duration"), a.get("durationUnit"),
                       a.get("sourceName"), a.get("sourceVersion"), a.get("device"),
                       a.get("creationDate"), a.get("startDate"), a.get("endDate"),
                       json.dumps(extra, sort_keys=True) if extra else None, _meta(el)])
    for c in el:
        if c.tag == "WorkoutStatistics":
            g = c.get
            w.add("workout_stats", [wid, g("type"), g("startDate"), g("endDate"), g("unit"),
                                    g("sum"), g("average"), g("minimum"), g("maximum")])
        elif c.tag == "WorkoutEvent":
            g = c.get
            w.add("workout_events", [wid, g("type"), g("date"), g("duration"), g("durationUnit")])
        elif c.tag == "WorkoutRoute":
            for f in c:
                if f.tag == "FileReference":
                    w.add("workout_routes", [wid, f.get("path"), c.get("sourceName"),
                                             c.get("startDate"), c.get("endDate"), _meta(c)])


def _correlation(el, w):
    g = el.get
    cid = _stable_id(g("type"), g("sourceName"), g("startDate"), g("endDate"))
    head = [cid, g("type"), g("sourceName"), g("device"), g("creationDate"), g("startDate"),
            g("endDate"), _meta(el)]
    members = [c for c in el if c.tag == "Record"]
    for m in members:
        w.add("correlations", head + [m.get("type"), m.get("value"), m.get("unit")])
    if not members:
        w.add("correlations", head + [None, None, None])


def parse_xml(stream, w, progress=None):
    """Consume one export.xml stream. Entity resolution and network access are off."""
    ctx = etree.iterparse(stream, events=("end",), huge_tree=True, resolve_entities=False,
                          no_network=True, load_dtd=False, recover=True,
                          tag=("Record", "Workout", "ActivitySummary", "Correlation",
                               "ClinicalRecord"))
    n, depth_skip = 0, 0
    tags = {}
    for _, el in ctx:
        tag = el.tag
        parent = el.getparent()
        # Apple repeats records nested in a Correlation at the top level of the export,
        # so they are kept only as correlation members and never counted twice.
        if tag == "Record" and parent is not None and parent.tag == "Correlation":
            depth_skip += 1
            continue
        tags[tag] = tags.get(tag, 0) + 1
        if tag == "Record":
            g = el.get
            start, src = g("startDate"), g("sourceName")
            w.add("records", [g("type"), src, g("sourceVersion"), g("device"), g("unit"),
                              g("value"), g("creationDate"), start, g("endDate"), _meta(el)])
            for c in el:
                if c.tag == "HeartRateVariabilityMetadataList":
                    for b in c:
                        w.add("hrv_beats", [start, src, b.get("bpm"), b.get("time")])
        elif tag == "Workout":
            _workout(el, w)
        elif tag == "ActivitySummary":
            g = el.get
            w.add("activity_summaries", [
                g("dateComponents"), g("activeEnergyBurned"), g("activeEnergyBurnedGoal"),
                g("activeEnergyBurnedUnit"), g("appleMoveTime"), g("appleMoveTimeGoal"),
                g("appleExerciseTime"), g("appleExerciseTimeGoal"), g("appleStandHours"),
                g("appleStandHoursGoal")])
        elif tag == "Correlation":
            _correlation(el, w)
        elif tag == "ClinicalRecord":
            g = el.get
            w.add("clinical_records", [g("type"), g("identifier"), g("sourceName"), g("sourceURL"),
                                       g("fhirVersion"), g("receivedDate"), g("resourceFilePath")])
        el.clear()
        while el.getprevious() is not None:  # free siblings already handled
            del el.getparent()[0]
        n += 1
        if progress and n % 1_000_000 == 0:
            progress(n)
    errors = [str(e) for e in list(ctx.error_log)[:20]]
    return {"elements": tags, "nested_correlation_records": depth_skip, "xml_errors": errors}



def parse_gpx(stream, path, w):
    ns = "{http://www.topografix.com/GPX/1/1}"
    for _, el in etree.iterparse(stream, events=("end",), tag=ns + "trkpt", huge_tree=True,
                                 resolve_entities=False, no_network=True, load_dtd=False):
        fields = {"time": None, "ele": None, "speed": None, "course": None,
                  "hAcc": None, "vAcc": None}
        for c in el.iter():
            name = c.tag.rsplit("}", 1)[-1] if isinstance(c.tag, str) else ""
            if name in fields:
                fields[name] = c.text
        w.add("route_points", [path, fields["time"], el.get("lat"), el.get("lon"), fields["ele"],
                               fields["speed"], fields["course"], fields["hAcc"], fields["vAcc"]])
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]


def parse_ecg(text, name, w):
    """ECG CSV: leading `Key,Value` header lines, then one microvolt sample per line."""
    samples = []
    for line in text.splitlines():
        if not line.strip():
            continue
        head, _, tail = line.partition(",")
        if re.fullmatch(r"-?\d+(\.\d+)?", head.strip()) and not tail.strip():
            samples.append(head.strip())
        else:
            w.add("ecg", [name, head.strip().strip('"'), tail.strip().strip('"')])
    w.add("ecg", [name, "__samples__", str(len(samples))])


def ingest(src: str, out_root: Path):
    src_path = Path(src)
    export_id = _stable_id(src_path.name, str(src_path.stat().st_size),
                           str(int(src_path.stat().st_mtime)))
    out = out_root / "parsed" / export_id
    out.mkdir(parents=True, exist_ok=True)
    w, t0 = Writer(out), time.time()

    def progress(n):
        print(f"  {n:,} elements, {time.time() - t0:.0f}s", flush=True)

    report = {"export_id": export_id, "source": src_path.name, "files": {}}
    if src_path.suffix == ".zip":
        with zipfile.ZipFile(src_path) as z:
            names = z.namelist()
            xml = next(n for n in names if n.endswith("/export.xml") or n == "export.xml")
            with z.open(xml) as f:
                report.update(parse_xml(f, w, progress))
            gpx = [n for n in names if n.endswith(".gpx")]
            for i, n in enumerate(gpx):
                with z.open(n) as f:
                    parse_gpx(f, "/" + n.split("apple_health_export/", 1)[-1], w)
            for n in (x for x in names if "electrocardiograms/" in x and x.endswith(".csv")):
                parse_ecg(z.read(n).decode("utf-8", "replace"), n.rsplit("/", 1)[-1], w)
            for i in z.infolist():
                kind = i.filename.rsplit(".", 1)[-1].lower()
                c = report["files"].setdefault(kind, {"count": 0, "bytes": 0})
                c["count"] += 1
                c["bytes"] += i.file_size
    else:
        with open(src_path, "rb") as f:
            report.update(parse_xml(f, w, progress))
    w.close()
    report["rows"], report["seconds"] = w.count, round(time.time() - t0, 1)
    (out / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ("rows", "seconds", "xml_errors", "elements",
                                             "nested_correlation_records")}, indent=1))
    return out


if __name__ == "__main__":
    ingest(sys.argv[1], Path(sys.argv[2] if len(sys.argv) > 2 else "data"))
