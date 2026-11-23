# health-export-lab

A local analyzer for Apple Health exports that fixes what the raw export gets wrong before running any statistics. Sum the steps naively and you count iPhone and Watch twice; read the timestamps literally and trips land in the wrong time zone.

**[Read the report →](https://allenwang0.github.io/health-export-lab/)** Every correction, scored against an export whose right answers are known.

[![The report's opening view](docs/report.png)](https://allenwang0.github.io/health-export-lab/)

All numbers and screenshots come from synthetic exports with planted answers, not real health data.

## Highlights

<!-- results:start -->
- Steps counted once: a plain sum gives 13,913,705 steps, 73% over the planted 8,047,907; removing iPhone and Watch overlap recovers that total exactly, on 20 of 20 seeds.
- Local time rebuilt hour by hour: days within 5% of Apple's own daily totals go from 92.6% to 99.2% (seed 0; 99.0%–99.4% over 20 seeds).
- Every fix is guarded: reverting each of 14 fixes, one at a time, makes a test fail (14 of 14).
- Streams the export zip without extracting it: 1,000,000 synthetic records parse in 5.7 s (median of 5, Apple M5 Pro).
- Statistics corrected for many tests: on seed 0, 4 of 33 pre-listed tests survive, all the one effect the generator planted; over 20 seeds that effect always survives, with weak extras on 6.
<!-- results:end -->

## Run it

Requires Python 3.11.

```bash
make setup                        # virtualenv with pinned dependencies
make demo                         # synthetic export, served at http://127.0.0.1:8877
make test
./run.sh ~/Downloads/export.zip   # your own export from the Health app
make report                       # rebuild report/ from the committed results/
```

## How it works

- Parses the export zip as a stream and keeps every value as raw text.
- Rebuilds local time from Apple's time-zone tags, since every record carries the offset from the day you exported.
- Removes overlap between sources in priority order (Watch, iPhone, other apps) and picks one sleep source per night.
- Treats days with no data as unknown, never zero.
- Runs a fixed set of statistical tests chosen in advance and corrects for testing many at once (Benjamini–Hochberg).

## Limitations

- Synthetic data only: the generator and the fixes share an author, so exactness on real exports is not measured.
- Runs locally, nothing uploaded: the server only answers on 127.0.0.1 and your original export is never modified.
- Time-zone changes are placed to within about a day.
- Questions are matched by rules, not a language model, so only a fixed set of question shapes works.
