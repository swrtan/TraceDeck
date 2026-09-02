# Phase 4.2 performance, concurrency, and storage validation

Status: complete  
Date: 2026-09-01  
Environment: Windows local reference environment, Python 3.13

## Acceptance dataset

The benchmark generated 10,000 sessions/turns and 100,000 tool calls in a temporary directory in 0.421 seconds. User data was not used or modified. The repeatable benchmark is [scripts/p4_benchmark.py](../scripts/p4_benchmark.py).

## Results

| Measure | Result | MVP budget | Result |
| --- | ---: | ---: | --- |
| Summary API p95 / p99 | 3.81 / 4.67 ms | 150 ms p95 | pass |
| Recent-turn API p95 / p99 | 17.71 / 21.46 ms | 150 ms p95 | pass |
| Turn-detail API p95 / p99 | 3.01 / 4.17 ms | 200 ms p95 | pass |
| Hook capture p95 / p99 | 35.69 / 40.98 ms | 100 / 250 ms | pass |
| Concurrent API requests | 100 requests / 5 workers in 163.69 ms | 5 clients supported | pass |
| Database allocated size | 17.57 MiB | 1 GiB | pass |
| WAL after checkpoint | 0 bytes | 64 MiB | pass |
| Hook spool burst | 1,000 files / 272,000 bytes | 32 MiB | pass |
| TraceDeck process idle RSS | 9.47 MiB | 150 MiB p95 | observed below target |

The hook benchmark measures the synchronous local shim path, including bounded input handling and atomic spool writes. API timings include the FastAPI test client overhead and are intentionally measured over 1,000 warmed calls per endpoint. The concurrency run used five independent app/database connections to avoid sharing asyncio event-loop state between test workers.

## Limitations and follow-up

Event freshness was previously verified end to end with the live collector but was not remeasured at 10,000-turn scale. Active one-minute CPU and peak RSS under sustained ingestion require a long-running OS-level sampling run; the current evidence includes the live service idle sample and benchmark timings. Physical 1.25 GiB disk saturation remains intentionally uninduced; bounded-capacity behavior is covered by P4.1.
