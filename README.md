# health-export-lab

A local analyzer for Apple Health exports that fixes what the raw export gets wrong before running any statistics. Sum the steps naively and you count iPhone and Watch twice; read the timestamps literally and trips land in the wrong time zone.

**[Read the report →](https://allenwang0.github.io/health-export-lab/)**

[![The report's opening view](docs/report.png)](https://allenwang0.github.io/health-export-lab/)

All numbers and screenshots come from synthetic exports with planted answers, not real health data.

## Highlights

<!-- results:start -->
- Steps counted once: a plain sum overcounts by 73%; removing iPhone and Watch overlap recovers the planted 8,047,907 exactly, on 20 of 20 seeds.
- Local time rebuilt: days within 5% of Apple's totals go from 92.6% to 99.2% (seed 0).
- 14 of 14 fixes guarded: reverting any one makes a test fail.
- 1,000,000 synthetic records parse in 5.7 s (median of 5, Apple M5 Pro).
- On seed 0, the 4 of 33 tests surviving correction are all the planted effect.
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

- Streams the export zip and keeps every value as raw text.
- Rebuilds local time from Apple's time-zone tags; every record carries the export day's offset.
- Removes overlap between sources in priority order (Watch, iPhone, other apps) and picks one sleep source per night.
- Treats days with no data as unknown, never zero.
- Runs tests chosen in advance, with Benjamini–Hochberg correction.

## Limitations

- Synthetic data only: exactness on real exports is not measured.
- Local only: the server answers on 127.0.0.1 and never modifies your export.
- Time-zone changes are placed to within about a day.
- Questions are matched by rules, not a language model.
