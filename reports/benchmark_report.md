# AgentEval — Benchmark & Comparative Quality Report

Comprehensive multi-run benchmark results with pass@k, category accuracy, latency, cost, and recovery rate.

## Exact Test Configurations & Provenance

| Model | Execution Provenance | Prompt Version | Runs / Task | Avg Score | Pass Rate | Mean Latency | Est. Cost | Recovery Rate |
|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **mock** | `[ILLUSTRATIVE_DEMO]` | `v2.1.0` | 2 | **95.5%** | 100.0% | 2426ms | $0.0018 | 0.0% |

## Per-Category Accuracy & Scores

| Category | mock ([ILLUSTRATIVE_DEMO]) |
|:---|:---:|
| `ALGORITHMS` | 99.5% (pass: 100%) |
| `API` | 100.0% (pass: 100%) |
| `BUG_FIX` | 81.7% (pass: 100%) |
| `CONCURRENCY` | 99.5% (pass: 100%) |
| `DATABASE` | 100.0% (pass: 100%) |
| `DATA_ENGINEERING` | 100.0% (pass: 100%) |
| `REGRESSION` | 100.0% (pass: 100%) |
| `SECURITY` | 96.4% (pass: 100%) |

## Task-Level Performance & Stochastic Distribution

| Task ID | Category | mock Mean ± Std (Pass@1) |
|:---|:---|:---:|
| `discount` | `BUG_FIX` | 93.3% ± 0.0 (100%) |
| `tax` | `BUG_FIX` | 70.0% ± 0.0 (100%) |
| `api_endpoint` | `API` | 100.0% ± 0.0 (100%) |
| `sql_injection` | `SECURITY` | 93.3% ± 0.0 (100%) |
| `db_constraint` | `DATABASE` | 100.0% ± 0.0 (100%) |
| `orders` | `REGRESSION` | 100.0% ± 0.0 (100%) |
| `rate_limiter` | `CONCURRENCY` | 99.5% ± 0.0 (100%) |
| `jwt_auth` | `SECURITY` | 99.5% ± 0.0 (100%) |
| `cache_lru` | `ALGORITHMS` | 99.5% ± 0.0 (100%) |
| `data_pipeline` | `DATA_ENGINEERING` | 100.0% ± 0.0 (100%) |

---
*Report generated deterministically by AgentEval Benchmark Engine.*