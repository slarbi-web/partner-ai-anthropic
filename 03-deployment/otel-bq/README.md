# Claude Code → Google-built OTel Collector on Cloud Run

Ships Claude Code telemetry into Google Cloud Observability via the
[Google-built OpenTelemetry Collector](https://docs.cloud.google.com/stackdriver/docs/instrumentation/google-built-otel)
running on Cloud Run. Designed to be deployed once by an admin and used by a
whole team — each developer authenticates as themselves from their own laptop.

```
Claude Code (each developer's machine)
   │  OTLP/HTTP (protobuf), Authorization: Bearer <ID token>
   ▼
Cloud Run "claude-otel-collector"   ← Google-built OTel Collector (public image)
   │  googlecloud + googlemanagedprometheus exporters (ADC = runtime SA)
   ▼
Cloud Logging · Cloud Monitoring (Managed Prometheus) · Cloud Trace
```

- **Signals:** logs (Claude Code events) + metrics; traces pipeline wired but idle
  (Claude Code does not emit spans today).
- **Auth:** Cloud Run requires IAM (`--no-allow-unauthenticated`). No key files —
  tokens are minted either from the GCP metadata server (on GCP compute) or via
  `gcloud` service-account impersonation (on laptops). See [Authentication](#authentication).
- **Config delivery:** `collector-config.yaml` is stored in Secret Manager and
  volume-mounted at `/etc/otelcol-google/config.yaml`. No custom image / build.
- **Port:** Cloud Run container port is `4318`; the OTLP/HTTP receiver listens there.
  External HTTPS (443) → 4318.
- **Configuration:** all deployment-specific values live in `config.env` (created
  by `./setup.sh`), which is gitignored — nothing account-specific is committed.

## Files

| File | Purpose |
|------|---------|
| `config.env.example` | Template of all settings; copy to `config.env` or run `setup.sh`. |
| `setup.sh` | Writes `config.env` (+ `.collector-url`) and renders the SQL templates. Interactive, or fully flag-driven — see `./setup.sh --help`. |
| `deploy.sh` | Idempotent provisioning: APIs, SAs, IAM, secret, Cloud Run deploy. |
| `collector-config.yaml` | Collector pipelines (receivers/processors/exporters). |
| `otel-headers-helper.sh` | Mints the `Authorization` header for Claude Code (metadata → gcloud). |
| `otel-headers-helper.cmd` | **Windows only.** Shim that lets Claude Code run the helper (see [Windows](#windows)). |
| `lib-common.sh` | Shared helpers sourced by the two scripts above; not run directly. |
| `print-settings.sh` | Prints (or merges) the `~/.claude/settings.json` for a developer. |
| `.collector-url` | The service URL, used as the token audience. Written by `deploy.sh`, or by `setup.sh --endpoint`. |
| `daily-spend.sql` / `spend-by-model.sql` | Cost queries for Log Analytics (templates; `setup.sh` renders `*.local.sql`). |

---

## Admin setup (once per project)

1. **Authenticate gcloud** with an identity that can deploy (run/secret/SA admin):
   ```bash
   gcloud auth login
   ```

2. **Configure** — provide your project and who may send telemetry:
   ```bash
   ./setup.sh
   ```
   Key answers:
   - `PROJECT` — your GCP project ID.
   - `DEVELOPERS` — **who may send telemetry.** Use a **domain or group** so you
     never list individuals: `domain:yourcompany.com` (any authenticated user in
     your org) or `group:claude-users@yourcompany.com` (manage membership in
     Google Workspace). An explicit `user:` list works too but isn't necessary.
     Whoever is covered here can impersonate the invoker SA to mint a token.
   - `INVOKER_MEMBERS` — optional; SAs of GCP machines (Cloud Workstations/VMs)
     that send directly via the metadata server.

   Every answer is also a flag, so this step can be scripted (`./setup.sh --help`):
   ```bash
   ./setup.sh --project my-proj --developers domain:yourcompany.com -y
   ```
   With `-y` (or when stdin is not a terminal) nothing is prompted and a missing
   required value is a hard error rather than a silent exit.

3. **Deploy the collector:**
   ```bash
   ./deploy.sh
   ```
   Provisions everything and prints the service URL (saved to `.collector-url`).

4. **Share** the service URL with your developers and tell them they've been
   granted access.

Onboarding later: if `DEVELOPERS` is a **group or domain**, just add the person
in Google Workspace — no repo change, no redeploy. Only if you used an explicit
`user:` list do you edit `config.env` and re-run `./deploy.sh` (idempotent).

---

## Developer setup (each laptop)

1. **Install the Google Cloud SDK** and log in as yourself:
   ```bash
   gcloud auth login
   ```

2. **Point this clone at the collector.** `config.env` and `.collector-url` are
   gitignored (they hold deployment-specific values), so a fresh clone has
   neither. Ask your admin for the collector URL, then:
   ```bash
   ./setup.sh --developer --project <project-id> --endpoint <collector-url> -y
   ```
   `--developer` skips every admin-only question and needs no deploy rights — it
   just writes the `config.env` and `.collector-url` that the header helper reads.
   Drop the `-y` to be prompted instead.

3. **Update `~/.claude/settings.json`.** Easiest — let the script merge it in for
   you (uses `jq`, or falls back to Python; backs up the file first):
   ```bash
   ./print-settings.sh you@yourcompany.com --merge
   ```
   Or print the correctly-shaped JSON and merge by hand:
   ```bash
   ./print-settings.sh you@yourcompany.com
   ```
   ```json
   {
     "env": {
       "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
       "OTEL_LOGS_EXPORTER": "otlp",
       "OTEL_METRICS_EXPORTER": "otlp",
       "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
       "OTEL_EXPORTER_OTLP_ENDPOINT": "https://claude-otel-collector-XXXX.<region>.run.app",
       "OTEL_METRIC_EXPORT_INTERVAL": "10000",
       "OTEL_LOGS_EXPORT_INTERVAL": "5000",
       "OTEL_RESOURCE_ATTRIBUTES": "user.email=you@yourcompany.com"
     },
     "otelHeadersHelper": "/absolute/path/to/otel-headers-helper.sh"
   }
   ```
   > ⚠️ `otelHeadersHelper` is a **top-level key, a sibling of `env`** — not one
   > of the keys inside `env`. Putting it inside `env` is the #1 setup mistake and
   > results in an empty `Authorization` header (Cloud Run rejects the telemetry).

   > On **Windows** the value must point at `otel-headers-helper.cmd` (quoted),
   > not the `.sh` — see [Windows](#windows). `print-settings.sh` emits the right
   > one for your platform automatically.

4. **Restart Claude Code** so it picks up the new environment.

---

## Authentication

Cloud Run only lets in requests bearing a valid Google-signed **ID token** whose
audience equals the service URL. `otel-headers-helper.sh` produces that token,
trying two methods in order:

1. **Metadata server** — on GCP compute (Cloud Workstations, GCE VMs) the
   machine's attached service account mints the token, no key files. Those
   machines' SAs must be listed in `INVOKER_MEMBERS` (granted `run.invoker`).
2. **gcloud impersonation** — on a laptop there is no metadata server, so the
   helper runs `gcloud auth print-identity-token` **impersonating the shared
   invoker SA**. The developer must have run `gcloud auth login` and hold
   `roles/iam.serviceAccountTokenCreator` on that SA (granted by `deploy.sh` for
   everyone in `DEVELOPERS`).

The helper auto-detects, so the same script works in both environments.

## Windows

The scripts are bash, so run them from **Git Bash** (`gcloud` and the Cloud SDK
work as normal). Three Windows-specific things are handled for you:

- **`otelHeadersHelper` must point at the `.cmd`.** On Windows, Claude Code always
  runs this value through `cmd.exe`, which cannot execute a `.sh` — it returns
  *success having produced no output*, so telemetry ships with an empty
  `Authorization` header and every export is rejected, with nothing in the logs to
  say why. `otel-headers-helper.cmd` locates bash and hands off to the `.sh`, which
  remains the single implementation. `print-settings.sh` emits it already quoted:
  ```json
  "otelHeadersHelper": "\"C:\\path\\to\\otel-headers-helper.cmd\""
  ```
  The inner quotes are required by the docs whenever the path contains spaces.

- **`gcloud` from Git Bash needs `CLOUDSDK_PYTHON`.** The Cloud SDK ships two
  launchers: `gcloud.cmd` (used from PowerShell/cmd, which points at the SDK's
  bundled interpreter) and a POSIX `gcloud` shell script, found first on the Git
  Bash PATH, which looks for `python` on PATH. On a stock Windows box that resolves
  to the Microsoft Store alias stub and every call fails with *"Python was not
  found"*. The helper now points it at the bundled interpreter itself.

- **`jq` is optional.** `print-settings.sh --merge` falls back to Python (including
  the Cloud SDK's bundled one) when `jq` is absent, which it usually is on Windows.

Expect the helper to be **slow on Windows** — `gcloud` startup alone runs to tens
of seconds. Claude Code invokes it at launch and roughly every 29 minutes; raise
`CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS` if that is disruptive.

## Verify

```bash
source ./config.env

# 1. Service is up
gcloud run services describe "${SERVICE}" --region "${REGION}" \
  --format='value(status.url)'

# 2. Header helper produces a token
./otel-headers-helper.sh          # expect {"Authorization": "Bearer <token>"}

# 3. Auth + ingest probe (expect HTTP 200 and {"partialSuccess":{}})
URL=$(cat .collector-url)
TOK=$(./otel-headers-helper.sh | sed -E 's/.*Bearer (.*)".*/\1/')
curl -i -X POST "$URL/v1/logs" -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  -d '{"resourceLogs":[{"scopeLogs":[{"logRecords":[{"body":{"stringValue":"probe"}}]}]}]}'

# 4. Real telemetry (after running a Claude Code prompt)
gcloud logging read 'logName:"claude-code"' --project "${PROJECT}" \
  --freshness=10m --limit 5

# 5. Collector health
gcloud run services logs read "${SERVICE}" --region "${REGION}" --limit 50
```

Metrics: Cloud Monitoring → Metrics Explorer → `prometheus/claude_code_*`
(Managed Prometheus), a couple minutes after the first export interval.

Troubleshooting the probe: `401/403` ⇒ IAM (invoker binding / token-creator
grant / token audience); `404` ⇒ wrong path or container port.

## Per-user identity (email instead of a hash)

Under Vertex auth, Claude Code emits only a hashed `user.id` — no `user.email`.
To attribute telemetry to a real person, each developer sets their email as a
resource attribute (see the developer setup above):

```json
"OTEL_RESOURCE_ATTRIBUTES": "user.email=someone@example.com"
```

The collector's `transform` processor (see `collector-config.yaml`) promotes that
attribute onto every record as a **`user_email`** label, queryable in both Cloud
Logging (`labels.user_email`) and Cloud Monitoring (metric label `user_email`).
The SQL queries group by it, falling back to `user.id` for older telemetry.

> **Note:** `user_email` is **self-declared** — the collector trusts whatever the
> developer sets. It's fine for cost attribution but is not a verified identity.
> (GCP's own audit logs still record the real authenticated principal.)

## Cost accuracy (important)

The native Cloud Monitoring metric `claude_code_cost_usage_USD_total` is a
**per-session counter that resets**, so it is unreliable for totals: summing the
raw counter under-counts, and `increase()` over-extrapolates across resets. The
**authoritative cost source is the `cost_usd` label on each `api_request` log
event** — which is exactly what the SQL queries read (via Log Analytics), so they
are exact and cover full history.

## Cost queries (ad-hoc)

`setup.sh` renders `daily-spend.local.sql` (per user) and
`spend-by-model.local.sql` (per model) from the templates, substituting your
project ID. Paste the rendered file into **Logging → Observability Analytics → Query**.
(If you didn't run `setup.sh`, edit the `*.sql` template and replace
`YOUR_PROJECT_ID` yourself.) The console time-range picker controls the window.

These queries read `_Default._AllLogs`, which requires the `_Default` log bucket
to be **upgraded to Log Analytics**. `deploy.sh` does this for you (via
`gcloud logging buckets update _Default --location=global --enable-analytics`);
to do it by hand, use **Logging → Logs Storage → `_Default` → Upgrade**.

## Cost notes

`MIN_INSTANCES=0` scales to zero (no idle cost), but a cold start can drop the
first telemetry batch after idle. `MIN_INSTANCES=1` (the default) guarantees
capture at a small always-on cost. Cloud Logging/Monitoring ingestion is billed
per usage.

## Teardown

```bash
source ./config.env
gcloud run services delete "${SERVICE}" --region "${REGION}" --quiet
gcloud secrets delete "${SECRET}" --quiet
gcloud iam service-accounts delete "${RUNTIME_SA_NAME}@${PROJECT}.iam.gserviceaccount.com" --quiet
gcloud iam service-accounts delete "${INVOKER_SA_NAME}@${PROJECT}.iam.gserviceaccount.com" --quiet
```
Then remove the OTel keys / `otelHeadersHelper` from `~/.claude/settings.json`.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](../../LICENSE).
