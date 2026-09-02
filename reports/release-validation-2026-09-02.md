# TraceDeck release validation

Date: 2026-09-02

## Current evidence

- Full automated test suite: **71/71 passed**.
- P4.1 security/privacy/failure tests are included in the passing suite.
- Existing P4.2 benchmark evidence remains recorded in `reports/phase-4-p4.2-performance.md`; it covers API latency, hook latency, storage, WAL, spool burst, concurrency, and idle RSS.
- Existing P4.3 controlled real-Codex compatibility evidence remains recorded in `reports/phase-4-p4.3-compatibility.md`.
- Operator guidance is available in `OPERATIONS.md`.
- Local service health check returned HTTP 200 after the release changes.

## Release gate status

| Gate | Status | Boundary |
| --- | --- | --- |
| Automated tests | Pass | 71/71 in the bundled runtime |
| Source compilation/syntax | Pass | Python compileall and browser JavaScript syntax check |
| Local-only headers/host policy | Pass | Covered by P4.1 tests |
| Packaging artifact | Blocked | Local Python lacks `setuptools`, so wheel build could not run |
| Active CPU and peak RSS | Pending | Requires a long-running OS-level sampling run |
| Physical 1.25 GiB disk pressure | Pending | Not induced on the developer machine to avoid unrelated data risk |
| Packet-level outbound observation | Pending | Requires isolated clean-machine/network observation |
| Browser and screen-reader matrix | Pending | Current evidence is limited to the local dashboard checks |

The release is therefore **validation-ready but not release-cleared**. No missing measurement is replaced with an estimate.
