# Calendar provider

BandoriBench freezes calendars before benchmark creation.

The runtime predictor consumes the frozen `bandoribench-calendar-v1` JSON and does not query calendar services.

## Design

1. Provider generates a candidate calendar.
2. Official adjustments (such as Chinese makeup workdays) are merged.
3. The normalized JSON is committed/frozen and included in benchmark hashes.

## Current provider

- China: optional `holidays` package for public holidays.
- Makeup workdays: intentionally handled as a separate override layer.

This separation avoids silently changing historical benchmarks when external libraries update.
