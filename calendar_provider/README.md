# Calendar provider

The provider creates `bandoribench-calendar-v1` JSON files.

Design rules:

- Fetch/generate once.
- Freeze the resulting JSON into the benchmark hash.
- Do not query live calendars during scoring.
- Add official CN makeup-workday overrides before releasing CN benchmarks.

The current implementation is a holiday foundation using the optional `holidays` package. The next step is merging annual State Council holiday schedules, because weekend makeup workdays are not represented reliably by a generic holiday library.
