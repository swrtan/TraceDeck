# Phase 4.1 security, privacy, and failure-mode validation

Status: complete  
Date: 2026-09-01

## Evidence

The P4.1 acceptance tests in `tests/integration/test_p4_security.py` pass 6/6. The complete test suite passes 37/37.

Validated behavior:

- `TRACEDECK_ENABLED=0` exits the hook shim successfully and creates no TraceDeck data directory.
- A corrupt SQLite file fails startup with a database error; it is not silently replaced.
- A locked SQLite database reports the lock instead of bypassing the database lock.
- The recovery spool remains bounded and records a `spool_full` incident.
- Installer write failure leaves the existing `hooks.json` unchanged; uninstall preserves unrelated user hooks.
- Dashboard responses send the local-only CSP and `nosniff` header, reject a non-local Host, and contain no external HTTP asset references.
- Existing redaction, malformed-event quarantine, neutral Stop output, and loopback configuration tests remain green.

## Controlled limitations

Physical full-disk behavior and packet-level outbound traffic were not induced in the developer environment because doing so would risk unrelated user data and machine state. The bounded spool/full-capacity path and static no-external-resource contract were tested instead. A production release should still include an isolated clean-machine disk-pressure and network-observation run.
