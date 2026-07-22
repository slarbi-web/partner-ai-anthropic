# 03 — Deployment

Production-oriented deployments: things you stand up once for a team, rather
than run on a single machine.

| Guide | What it deploys |
|-------|-----------------|
| [`otel-bq/`](otel-bq/) | Claude Code usage & cost telemetry — the Google-built OpenTelemetry Collector on Cloud Run, exporting to Cloud Logging, Managed Prometheus and Cloud Trace, with per-user cost attribution and SQL spend queries for Log Analytics. |
