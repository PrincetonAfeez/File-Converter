# Multi-Format File Converter with Background Jobs (Enterprise-Grade, Full Product Scope v3)

**Type:** Standalone full-product build. 

**One-line:** A production-ready Django + HTMX SaaS file-conversion platform where authenticated users and teams upload files, run slow or risky conversions through reliable background jobs, track honest progress, manage batches, enforce quotas, and download validated outputs through secure, expiring storage.

**The real subject:** asynchronous job processing done correctly. File conversion is the payload; the deliverable is a robust job platform that treats slow, crash-prone, binary-driven work as normal production behavior.

**Register:** Mastery of Python + system architecture. Enterprise-grade, production-ready, go-to-market product.

**Stack constraint:** Entire app in the Python ecosystem. The web layer is Django templates + HTMX. Background jobs use Celery + Redis. Redis also carries the high-frequency progress/status tier. File conversion uses Python libraries and Python-orchestrated external binaries such as LibreOffice and FFmpeg via subprocess, run inside a hardened, network-isolated sandbox. No Channels/WebSockets. This build is **DB + Web**; a public API is a documented future surface, while internal JSON/status endpoints exist only to support HTMX, admin, and job operations.

> **Changes in v3 (architecture pass).** Scope is *not* reduced. v2 was already mature — there was no hard self-contradiction to fix — so this revision adds distributed-systems depth and an explicit untrusted-binary isolation model, and tightens a few soft tensions.
> - **Added — exactly-once effects:** v2 has at-least-once delivery (`acks_late` + `task_reject_on_worker_lost`) but never specified how two workers running the same job are prevented from corrupting state. v3 adds an explicit **claim protocol + fencing token** (§3.3, §6.3, §7, ADR-0014).
> - **Added — cooperative cancellation:** a DB cancel flag polled at safe checkpoints, alongside process-group kill, because Celery `revoke` is best-effort for long subprocess work (§6.4, ADR-0015).
> - **Added — progress/status tier:** high-frequency progress goes to Redis (throttled), the status endpoint reads cache with a DB fallback, and polling backs off (§3.3, §14, §17, ADR-0016).
> - **Clarified — storage promotion:** "atomic promotion" on object storage is a **DB-pointer flip**, not a rename; plus incomplete-multipart-upload cleanup (§8.3, ADR-0017).
> - **Added — atomic quota reservation:** concurrent submissions cannot both pass a read-only quota check; reservation is atomic (§11.4, ADR-0018).
> - **Tightened — tenant isolation:** blobs and dedup/cache reuse are tenant-scoped only; the global checksum index is for integrity, never cross-tenant reuse (§8.2, §22, ADR-0019).
> - **Added — untrusted-binary isolation model:** network egress blocked at the namespace level, seccomp/read-only-rootfs/dropped-caps, LibreOffice per-invocation profile, webhook SSRF defenses (§18a, ADR-0020).
> - **Added:** distributed tracing across the async boundary, SLOs/alerts, at-rest encryption, right-to-erasure reconciled with append-only ledgers, converter-version replay policy, idempotency-key client semantics, scan-as-first-class-job, streaming batch ZIP (§17, §19, §3.2, §9, ADR-0021..0023).

---

## 1. Product Thesis

File conversion looks simple until it meets production reality: large files, untrusted uploads, external binaries, worker crashes, timeouts, storage cleanup, progress reporting, retries, user cancellations, and cost abuse.

This product is built around one promise:

> **Upload a file, safely convert it in the background, and always know what happened.**

The product must be reliable enough for a user or organization to trust it with business files, batch workflows, and repeat conversions. It must not pretend external binaries are safe, deterministic, fast, or always cooperative.

---

## 2. Locked Decisions

Settled during scoping. Not re-litigated inside the build.

| # | Area | Decision |
|---|------|----------|
| 1 | Product scope | Full SaaS-grade file converter with authenticated users, organizations/workspaces, job history, batch conversion, quotas, support tooling, and operational visibility |
| 2 | Conversion breadth | All four conversion families: images, data, documents to PDF, and audio/video |
| 3 | Architecture spine | Pluggable `Converter` interface + registry mapping `(source_format, target_format) → converter` |
| 4 | Background jobs | Celery + Redis, with crash-safe settings for long-running external processes |
| 5 | Status delivery | HTMX polling against job/batch status endpoints; no WebSockets |
| 6 | Progress model | Honest progress only: determinate when the converter reports real progress; indeterminate when it cannot |
| 7 | Upload security | Input-format allowlist, content-based type validation, file-size ceilings, malware-scanning hook, quarantine flow, filename sanitization |
| 8 | Storage | S3-compatible object storage in production via `django-storages`; MinIO/local volume for dev parity |
| 9 | Retention | Inputs deleted after successful conversion unless policy requires retention; outputs expire by TTL; cleanup through Celery Beat |
| 10 | Auth model | Authenticated users; organization/workspace membership; no anonymous public conversion path in this build |
| 11 | Tenant/team model | Organizations and workspaces are first-class; jobs, batches, files, quotas, presets, and audit logs are scoped |
| 12 | Conversion options | Converter-declared option schemas and reusable presets; options are validated server-side |
| 13 | Batch conversion | Multi-file batch jobs are in scope, with child jobs, partial success, batch cancellation, and ZIP download |
| 14 | Quotas | User/org/workspace quotas and fair scheduling protect cost and reliability |
| 15 | Side effects | Domain events + transactional outbox drive notifications, webhooks, metrics, and cleanup without coupling them to DB commits |
| 16 | API boundary | DB + Web product; no public API surface. Internal endpoints are private to web/admin/status flows. Public API is future, not built now |
| 17 | Production posture | PostgreSQL, Redis, object storage, Docker, Sentry, structured logs, metrics, runbooks, backup/restore, CI quality gates |
| 18 | Job correctness model | At-least-once delivery (`acks_late`) is made exactly-once *in effect* by an atomic claim protocol plus a per-claim fencing token; a stale worker's terminal write is rejected |
| 19 | Cancellation | Cooperative DB cancel flag polled at safe checkpoints, combined with subprocess process-group termination; Celery `revoke` alone is not relied upon |
| 20 | Progress/status tier | High-frequency progress writes go to Redis (throttled); the status endpoint reads cache with DB fallback; HTMX polling backs off and stops on terminal state |
| 21 | Storage promotion | "Promotion" is a DB-pointer flip after a validated, completed upload — never a non-atomic object-store rename; incomplete multipart uploads are lifecycle-cleaned |
| 22 | Tenant isolation | Blobs, dedup, and cache reuse are tenant-scoped; the global checksum index is for integrity only and never enables cross-tenant reuse |
| 23 | Untrusted-binary isolation | Conversion runs in a network-isolated sandbox (no egress namespace, read-only rootfs, dropped caps, seccomp); LibreOffice uses a per-invocation profile |
| 24 | Quota reservation | Concurrent-limit quotas are enforced by atomic reservation (locked counter / Redis reserve-commit), not by reading an append-only ledger |
| 25 | At-rest encryption | Stored blobs are encrypted at rest (SSE-KMS); per-tenant key strategy is documented |

---

## 3. Core Architecture Spine

The central engineering requirement:

> Adding a new format pair must touch only the converter implementation, option schema, registry entry, and tests. The job runner, status view, HTMX templates, storage pipeline, quota system, and audit flow must not care what the file type is.

### 3.1 Converter interface

Every converter implements a small, explicit contract:

```python
class Converter(Protocol):
    converter_name: str
    progress_mode: Literal["determinate", "indeterminate"]

    def supported_pairs(self) -> list[FormatPair]: ...
    def option_schema(self) -> ConversionOptionSchema: ...
    def estimate_cost(self, metadata: SourceMetadata, options: dict) -> ResourceEstimate: ...
    def probe(self, input_path: Path) -> SourceMetadata: ...
    def validate_input(self, input_path: Path, metadata: SourceMetadata) -> None: ...
    def convert(
        self,
        input_path: Path,
        output_path: Path,
        target_format: str,
        options: dict,
        progress_callback: ProgressCallback | None = None,
    ) -> ConversionResult: ...
    def validate_output(self, output_path: Path, result: ConversionResult) -> None: ...
    def cleanup(self, work_dir: Path) -> None: ...
```

Each converter declares:

- supported source and target formats
- progress mode
- converter name/version
- user-facing options
- safe option ranges
- resource estimate model
- maximum input constraints
- output validation rules
- retryability behavior
- known external-binary quirks

### 3.2 Registry

The registry maps each supported `(source_format, target_format)` pair to exactly one converter.

The registry must:

- reject unsupported pairs before job creation
- expose available target formats for a detected source format
- expose converter option schema to forms/templates
- expose converter progress mode
- include converter version in every job record
- allow converter upgrades without rewriting historical job records

**Version replay policy.** Because every job and preset pins a `converter_version` and `option_schema_version`, a retry of an old job replays the **originally pinned converter version** by default (reproducibility), not "latest." Re-running on a newer converter is an explicit, audited choice (e.g. a support "re-convert on current engine" action), and option payloads are re-validated against the schema version they were created under; if a schema field was removed, the job fails cleanly with a diagnostic rather than silently dropping options. (ADR-0022.)

### 3.3 Format-blind job runner

The Celery job runner does not contain conversion-family logic. It:

1. Loads the job.
2. Checks authorization/scope and terminal state.
3. **Claims the job atomically and acquires a fencing token** (see §6.3). If the claim affects no row (already claimed by a live worker, or already terminal), the worker acks and exits.
4. Downloads input to a per-job temp directory.
5. Resolves the converter from the registry (pinned `converter_version`).
6. Runs scanning/probing/validation.
7. Runs conversion under time/resource limits, **polling the cancel flag at safe checkpoints** (§6.4) and reporting progress to the Redis progress tier when determinate.
8. Updates progress (throttled, to Redis; DB row only at coarse milestones).
9. Validates output.
10. Promotes output: writes to a temp key, validates, then **flips the durable output pointer in the DB conditional on `claim_generation = my token`**. A stale worker's flip affects zero rows and the worker aborts and cleans up — output is never double-promoted.
11. Marks the job terminal (same fenced, conditional transition).
12. Emits audit/domain events through the outbox in the same transaction as the terminal transition (idempotency key on the outbox row prevents duplicate emission on a fenced re-run).
13. Cleans temp files in a `finally` block regardless of outcome.

Converter-specific behavior belongs inside converters. Orchestration — claim, fence, cancel, progress transport, promotion, cleanup — belongs in the runner.

### 3.4 Progress and status transport

Progress is high-frequency (FFmpeg can emit many ticks per second) and must not hammer Postgres:

- The converter's `progress_callback` writes to **Redis**, throttled to roughly one update per second or per ≥1% change.
- The HTMX status endpoint reads progress and state from Redis with a Postgres fallback, keeping it cheap enough to hold the status-endpoint latency target under many concurrent pollers.
- The durable `ConversionJob.progress_percent` is updated only at coarse milestones and on terminal transition, so the source of truth for *final* state is always the DB while live progress is ephemeral.
- HTMX polling uses interval backoff (poll quickly while young, slow as the job ages) and stops on terminal state.

---

## 4. Conversion Families

| Family | Engine | Example pairs | Progress |
|---|---|---|---|
| Images | Pillow | PNG ↔ JPEG ↔ WebP, resize, quality changes | Indeterminate unless chunked processing is implemented |
| Data | pandas / openpyxl | CSV ↔ JSON ↔ XLSX | Indeterminate by default; determinate optional for large row-streaming conversions |
| Documents → PDF | LibreOffice headless, optionally Markdown/HTML render pipeline | DOCX / HTML / Markdown → PDF | Indeterminate |
| Audio/Video | FFmpeg | WAV → MP3, MP4 → GIF, MP4 transcode, audio extraction | Determinate by parsing duration/progress |

### 4.1 Tier 3 consequences accepted

- The Docker image installs LibreOffice and FFmpeg.
- External binaries can hang, OOM, crash, produce invalid output, or write partial files.
- Every subprocess has timeouts, process-group control, stdout/stderr capture, redaction, and cleanup.
- The worker image is larger and must be documented as an operational dependency.
- Conversion failures are expected system states, not exceptional mysteries.

---

## 5. Organizations, Workspaces & Roles

The product is not just a single-user utility. It supports individuals and teams.

### 5.1 Data model

- **Organization** — account container; name, slug, status, default retention policy, default quota policy, created/updated timestamps.
- **Workspace** — scoped area inside an organization for jobs, batches, presets, policies, and history.
- **Membership** — user, organization, role, status.
- **WorkspaceMembership** — optional finer-grained workspace access.
- **UsageQuota** — quota policy assigned at org/workspace/user level.
- **UsageLedger** — append-only usage accounting.
- **AuditEvent** — scoped to org/workspace/user/job/batch.

### 5.2 Roles

| Role | Can do |
|---|---|
| Owner | Manage organization, billing-adjacent settings, quotas, retention, members, audit exports, workspace creation |
| Admin | Manage workspaces, presets, quotas within permission scope, cancel/retry jobs, view usage analytics |
| Member | Upload files, create jobs/batches, view own jobs and workspace jobs when allowed |
| Auditor | Read-only access to job history, audit logs, usage reports, and support exports |

Authorization is enforced server-side for all web views, HTMX endpoints, downloads, files, admin pages, and background job operations.

---

## 6. Job & Batch Model

### 6.1 ConversionJob

A single file conversion.

Fields:

- public UUID
- owner user
- organization
- workspace
- optional batch
- source format
- target format
- detected MIME/type
- status
- progress mode
- progress percent
- converter name
- converter version
- option payload
- option schema version
- Celery task id
- attempt count
- retry classification
- failure reason
- internal error code/detail
- input file reference
- output file reference
- original display filename
- input/output byte sizes
- input/output checksums
- malware scan verdict
- quota decision reference
- idempotency key
- worker id (set on claim)
- claim_generation (monotonic fencing token, incremented on every claim/takeover)
- cancel_requested flag (cooperative cancellation; polled by the running task)
- timestamps: created, queued, started, heartbeat, finished, expires

> **Idempotency key semantics.** The key is generated when the upload/conversion form is rendered and submitted with the request; a unique constraint per actor/scope makes a double-submit return the *existing* job rather than creating a second one or erroring. This is distinct from the fencing token, which protects an already-created job from double execution.

### 6.2 ConversionBatch

A multi-file conversion workflow.

Fields:

- public UUID
- owner user
- organization/workspace
- target format or per-file target map
- preset reference
- status
- total jobs
- completed jobs
- failed jobs
- cancelled jobs
- batch ZIP output reference
- created/started/finished/expires timestamps

Batch behavior:

- batch status is derived from child job states
- partial success is valid and visible
- failed children do not block successful children
- users can download individual outputs or a ZIP of successful outputs
- cancellation cancels pending/retrying/processing children where possible
- cleanup removes child outputs and batch ZIP by TTL
- batch timeline shows child progress and aggregate progress
- the batch ZIP is built by streaming successful outputs to a temp object (never assembled in memory), is size-capped, runs on the `batch_zip` queue, and is itself a TTL-governed output

### 6.3 Job state machine

Valid states:

- `uploaded` — file accepted into storage but not yet scanned
- `scanning` — malware/content scan in progress
- `scan_failed` — terminal security failure or unsupported scan result
- `pending` — scan passed and job is ready to enqueue or already queued
- `processing` — worker claimed the job and conversion is active
- `retrying` — transient failure occurred and bounded retry is scheduled
- `done` — output validated, stored, and downloadable
- `failed` — terminal conversion failure
- `cancelled` — user/admin cancelled the job
- `expired` — output TTL elapsed and artifacts were deleted

Rules:

- enqueue Celery tasks only with `transaction.on_commit()`
- only workers may transition `pending/retrying → processing`
- only the active worker may transition `processing → done/failed/retrying`
- terminal states are immutable except retention metadata
- all transitions are timestamped and audited
- re-running the same task must be idempotent
- a terminal job is never reprocessed by accident
- cleanup is idempotent and safe to rerun

#### Claim protocol and fencing token (exactly-once *effects* on at-least-once delivery)

`acks_late` + `task_reject_on_worker_lost` redeliver a job when a worker is lost — which also means the same job can be run by two workers at once (the common case is a worker that paused, looked dead, got taken over, then woke up still running). The state machine is made safe by:

- **Atomic claim.** Claiming is one conditional `UPDATE … SET status=processing, worker_id=:me, claim_generation=claim_generation+1, started=now WHERE status IN ('pending','retrying') OR (status='processing' AND heartbeat < now - :stale_threshold)`. The worker proceeds only if it changed the row; otherwise it acks and exits. Stale-takeover is a defined transition, not an ad-hoc script.
- **Fencing token.** The worker carries the `claim_generation` it won. Every state-mutating write that matters — the output-pointer flip and the terminal transition — is conditional on `WHERE claim_generation = :my_generation`. A woken-up zombie worker finishes, attempts to promote/terminalize, affects zero rows, and instead aborts and cleans up. This is what turns at-least-once delivery into exactly-once effect.
- **Heartbeat** is updated periodically during processing so a genuinely live worker is not prematurely taken over, and the stale threshold is comfortably larger than the heartbeat interval.

### 6.4 Cancellation

Cancellation is cooperative, not best-effort:

- A cancel request sets `cancel_requested=true` and audits the actor/reason.
- A `pending`/`retrying` job not yet claimed transitions straight to `cancelled` (and the enqueued task, if any, no-ops on claim because the row is terminal).
- A `processing` job is cancelled by the running task **polling `cancel_requested` at safe checkpoints** (between probe → convert → validate → promote) and, for an active subprocess, by killing the process group (`SIGTERM` then `SIGKILL`). Celery `revoke(terminate=True)` is used as a best-effort accelerator, never as the sole mechanism.
- A job cancelled after conversion succeeded but before the fenced output-pointer flip resolves to `cancelled`; the produced output is discarded by cleanup. No partial or post-cancel output is ever downloadable.

---

## 7. Celery Production Semantics

Celery is configured for crash-prone, long-running external processes:

- `acks_late=True`
- `task_reject_on_worker_lost=True`
- `worker_prefetch_multiplier=1`
- soft and hard time limits by converter family
- `max_tasks_per_child` to reduce leaks from native libraries/external tooling
- separate queues: `fast`, `document`, `media`, `batch_zip`, `scan`, `cleanup`
- bounded retries with explicit retryable/non-retryable exception classes
- job heartbeat updates
- worker identity stored on claim
- monotonic `claim_generation` fencing token enforced on every terminal/output write (§6.3)
- task id stored on job
- stale-processing recovery is the defined atomic-takeover claim, not a destructive script
- queue-depth metrics
- worker memory metrics
- active subprocess count metrics

### 7.1 Fair scheduling

The system must protect workers from one user or organization monopolizing capacity.

Required controls:

- max concurrent jobs per user
- max concurrent jobs per organization
- queue selection by family
- optional priority levels
- admin priority override
- queue wait-time measurement
- starvation prevention rules
- quota-aware job admission

---

## 8. File Handling & Storage

### 8.1 Upload acceptance

Uploads must pass:

- authenticated user check
- workspace permission check
- quota check
- size ceiling check
- source format allowlist
- content-based type validation
- filename sanitization
- malware scanning pipeline
- resource estimate check

Size limits are tunable globally and per converter family.

Examples:

- images: pixel/dimension cap
- video: byte and duration cap
- spreadsheet: row/column cap
- document: page estimate or byte cap
- batch: file-count and total-byte cap

### 8.2 FileBlob model

To support deduplication, auditability, and storage lifecycle, files are represented by a durable blob model.

Fields:

- sha256 checksum
- byte size
- detected MIME/type
- storage key
- storage backend
- reference count
- created_at
- expires_at
- scan verdict
- scan engine/version
- quarantine status
- ownership/scope metadata (organization/workspace — blobs are tenant-scoped)
- encryption metadata (SSE-KMS key reference)

> **Tenant scoping is mandatory.** A blob belongs to exactly one tenant. The `sha256` index exists for integrity and *within-tenant* dedup, never cross-tenant reuse — a cache hit must never reveal that another tenant holds the same bytes (a dedup oracle is a privacy leak). See §22.
>
> **Encryption at rest.** Blobs are stored with server-side encryption (SSE-KMS). A per-tenant key strategy is documented; the default is a platform KMS key with per-object encryption context carrying the tenant id.

### 8.3 Storage keys

- app-generated UUID paths only
- original filenames are display metadata only
- organization/workspace-scoped prefixes
- no user-controlled path segments
- signed/expiring download links or auth-gated streaming
- download access is audited
- temporary outputs are never downloadable

> **What "atomic promotion" actually means on object storage.** Object stores have no atomic rename, so promotion is *not* a storage move. The output is uploaded to a temp key, validated, and then "promotion" is a **DB-pointer flip**: the durable output reference is written to the `ConversionJob` row (fenced on `claim_generation`) only after the upload completes and validation passes. The download path authorizes against that DB record, so a half-written or unvalidated object is unreachable because nothing points to it. There is no window in which a partial output is downloadable.
>
> **Incomplete multipart cleanup.** Large outputs use multipart upload; an interrupted upload leaves orphaned parts that accrue storage cost silently. A bucket lifecycle rule aborts incomplete multipart uploads after a short window, and stale temp keys are swept by the cleanup beat.

### 8.4 Retention

Default policy:

- input deleted after successful conversion unless workspace policy says retain for a short diagnostic window
- output retained for 24 hours by default
- batch ZIP retained for 24 hours by default
- failed-job temp files deleted immediately unless support diagnostic retention is enabled
- cleanup runs through Celery Beat
- storage lifecycle policy mirrors application TTL where possible
- legal hold / admin retention override may be enabled per workspace

---

## 9. Malware Scanning & Quarantine

The scanning architecture is in scope even if the concrete scanner is swappable.

### 9.1 Scan pipeline

1. Upload accepted into temporary/quarantine storage.
2. File hash calculated.
3. Scanner runs before conversion.
4. Verdict stored.
5. Clean files proceed to `pending`.
6. Unsafe files become `scan_failed`.
7. Unknown scanner errors are either retryable or blocked according to policy.
8. Quarantined files are not converted or downloadable.

> **Scan is a first-class background job, not an inline step.** It runs on the dedicated `scan` queue with the same rigor as conversion: atomic claim + fencing token, heartbeat, a scan timeout, bounded retries with retryable/non-retryable classification, and idempotent rerun. Scanning never blocks the upload request (the upload returns immediately in `uploaded`/`scanning`), and a scanner crash lands the job in a clean retrying/`scan_failed` state rather than wedging the file.

### 9.2 Scan metadata

Store:

- scanner name
- scanner version
- signature database version if available
- verdict
- scanned_at
- failure reason
- quarantine key
- admin review status

### 9.3 Security principles

- user-facing messages do not expose scanner internals
- scan failures are auditable
- admins can inspect scan metadata but not silently override without reason
- scanning can be backed by ClamAV or another provider without changing the job runner

---

## 10. Conversion Options & Presets

Every converter declares a server-validated option schema.

### 10.1 Option schema examples

Images:

- width
- height
- preserve aspect ratio
- quality
- lossless
- background color for transparency flattening
- strip metadata

Documents:

- page size
- orientation
- margins
- PDF/A mode where supported
- embed fonts where supported

Data:

- delimiter
- header row
- sheet name
- encoding
- date format
- JSON orientation

Media:

- bitrate
- sample rate
- resolution
- FPS
- duration trim
- GIF loop
- audio only
- codec profile

### 10.2 Presets

Presets are reusable configurations.

- **SystemPreset** — built-in recommended settings.
- **OrganizationPreset** — shared across an organization.
- **WorkspacePreset** — scoped to one workspace.
- **UserPreset** — personal shortcut.

Presets store:

- source/target pair
- converter version
- option payload
- name/description
- scope
- created_by
- created_at
- active/archived status

---

## 11. Quotas, Usage & Abuse Control

The product must protect cost, capacity, and reliability.

### 11.1 Quota dimensions

- max file size
- max batch size
- max daily conversions
- max monthly conversions
- max concurrent jobs
- max total storage
- max media duration
- max image pixels
- max spreadsheet rows/columns
- max CPU seconds per job
- max retry attempts
- max downloads per output

### 11.2 Usage ledger

Every job writes usage records:

- bytes uploaded
- bytes output
- processing duration
- queue wait time
- converter family
- status
- retry count
- failure code
- downloads
- cleanup completion

Usage records are append-only and feed reporting, quotas, and support diagnostics.

### 11.3 Quota decisions

Every admission decision records:

- quota policy evaluated
- current usage snapshot
- allowed/denied
- denial reason
- actor
- organization/workspace
- timestamp

### 11.4 Atomic reservation (no check-then-act race)

An append-only `UsageLedger` is the right *accounting* primitive but cannot enforce a *concurrent* limit: two simultaneous submissions both read "under quota" and both proceed. Concurrent-limit quotas (max concurrent jobs, max total storage) are therefore enforced by **atomic reservation**, not by reading the ledger:

- Reserve against a counter row with `SELECT … FOR UPDATE` (or an atomic Redis reserve/commit/rollback) before admitting the job.
- The reservation is committed when the job is admitted and released on terminal state, cancellation, or admission failure.
- The `QuotaDecision` record documents *what* was decided; the reservation is *how* concurrency is made safe.
- Rate-style quotas (daily/monthly counts) can be evaluated from the ledger; only concurrency/capacity limits require the locked reservation.

---

## 12. Domain Events, Audit Log & Transactional Outbox

### 12.1 Domain events

Events include:

- `OrganizationCreated`
- `WorkspaceCreated`
- `FileUploaded`
- `FileScanned`
- `ScanFailed`
- `ConversionRequested`
- `QuotaDenied`
- `JobQueued`
- `JobStarted`
- `JobProgressUpdated`
- `JobCompleted`
- `JobFailed`
- `JobRetryScheduled`
- `JobCancelled`
- `JobExpired`
- `BatchCreated`
- `BatchCompleted`
- `OutputDownloaded`
- `FileDeleted`
- `PresetCreated`
- `SupportActionPerformed`

### 12.2 Audit log

Audit records include:

- actor
- organization/workspace
- action
- target type/id
- request id
- IP/user-agent where available
- before/after metadata where relevant
- reason for admin/support actions
- timestamp

### 12.3 Transactional outbox

The outbox handles post-commit side effects:

- email notifications
- optional signed webhooks
- metrics/event export
- batch ZIP creation
- delayed cleanup
- admin alerts
- support notifications

Outbox records include:

- event type
- payload
- status
- attempts
- next retry time
- last error
- created/processed timestamps

The audit log records what happened. The outbox drives what happens next.

---

## 13. Notifications & Optional Webhooks

### 13.1 Notifications

Supported events:

- job completed
- job failed
- batch completed
- batch partially failed
- output expiring soon
- quota exceeded
- scan failed
- admin cancelled job
- workspace preset changed

Delivery channels:

- in-app notifications
- email
- optional webhook callback

### 13.2 Delivery log

Store:

- notification type
- recipient
- related job/batch/workspace
- channel
- status
- provider message id
- failure reason
- sent_at / failed_at

### 13.3 Webhook boundary

Public API is not in scope, but outbound webhooks may be supported for organizations.

Webhook requirements:

- signed payloads
- retry with bounded backoff
- delivery log
- secret rotation
- event allowlist
- no file contents in webhook payloads
- download URLs must be expiring and scope-safe
- **SSRF defenses on the sender:** HTTPS-only destinations; the *resolved* IP is validated against a denylist of private, loopback, link-local, and cloud-metadata ranges (e.g. `169.254.169.254`); DNS-rebinding is mitigated by pinning the resolved address used for the connection; and the webhook sender runs with restricted egress. A tenant-supplied URL is never trusted to be external.

---

## 14. Web Product Surface: Django + HTMX

The web app is the primary product surface.

### 14.1 User screens

- Dashboard: recent jobs, active jobs, batches, usage, quota status
- Upload/conversion form: source detection, target choices, option schema form, preset selection
- Job detail: lifecycle timeline, progress, failure reason, download link, retry/cancel where allowed
- Batch detail: aggregate status, child jobs, partial success, ZIP download
- My conversions: filters by status, family, format pair, date, workspace
- Presets: create/manage personal presets
- Downloads: expiring links and audit awareness
- Settings: profile, notification preferences

### 14.2 Organization/Admin screens

- Workspace management
- Member management
- Organization presets
- Quotas and retention policy
- Usage analytics
- Audit log
- Webhook settings
- Failed/stuck job review
- Support export

### 14.3 HTMX expectations

- status polling stops automatically on terminal state
- job cards update through partials
- batch aggregate progress updates through partials
- cancel/retry actions update only the affected job/batch partials
- option forms update when source/target changes
- quota warnings update before submission
- validation errors are inline and preserve input
- every HTMX mutation has a non-HTMX fallback where practical
- templates never branch on guessed converter behavior; they render declared progress mode

---

## 15. Internal Endpoints

This build is DB + Web. Internal endpoints are allowed for web behavior but are not a public API.

Internal endpoint groups:

```text
/jobs/
/jobs/{uuid}/
/jobs/{uuid}/status/
/jobs/{uuid}/cancel/
/jobs/{uuid}/retry/
/jobs/{uuid}/download/
/batches/
/batches/{uuid}/status/
/presets/
/workspaces/{id}/usage/
/admin/jobs/
/admin/jobs/{uuid}/timeline/
```

Requirements:

- auth required
- object-level permissions
- no cross-workspace leakage
- request ids included in responses/logs
- stable HTMX partial contracts
- no unbounded list endpoints
- no public programmatic API documentation in this build

---

## 16. Support Console

The support console is first-class, not an afterthought.

Support users can inspect:

- job timeline
- batch timeline
- worker identity
- Celery task id
- queue name
- attempt history
- retry classification
- subprocess command metadata, redacted
- stdout/stderr excerpts, redacted and size-limited
- scan verdict
- quota decision
- file checksum
- storage references
- output validation results
- cleanup status
- notification/webhook delivery status
- user/org/workspace usage summary

Support actions:

- cancel job
- retry safe failure
- mark cleanup required
- rotate webhook secret
- export audit timeline
- suspend workspace conversions
- release stuck processing job into retry/failed according to policy

Every support action requires a reason and writes an audit event.

---

## 17. Observability & Analytics

### 17.1 Structured logs

Logs include:

- request id
- organization id
- workspace id
- user id
- job id
- batch id
- converter
- queue
- worker id
- subprocess exit code
- duration
- failure code
- retry count

No sensitive file contents, internal paths, or raw user uploads appear in logs.

### 17.1a Distributed tracing

A trace context is created at upload/enqueue and **propagated across the async boundary** (web → Redis/broker → Celery task → subprocess span) via OpenTelemetry, so a single job's full latency — queue wait, scan, download, convert, validate, promote — is one connected trace. For an async job platform this is the primary tool for answering "where did this job spend its time," which logs alone cannot reconstruct across process boundaries.

### 17.2 Metrics

Required metrics:

- queue depth by queue
- queue wait time by queue
- job duration by converter
- success/failure rate by converter
- timeout count
- retry count
- cancellation count
- scan failure count
- quota denial count
- worker memory
- active subprocess count
- output validation failure count
- cleanup lag
- storage used by org/workspace
- bytes uploaded/downloaded
- batch partial-failure rate
- webhook delivery failures
- fenced-write rejections (stale-worker takeovers caught)
- progress-tier cache hit ratio

### 17.2a SLOs and alerts

Metrics without thresholds are dashboards nobody watches. The product defines SLOs and the alerts that fire off them:

- **Queue wait** for the `fast` queue p95 within target → alert on breach (signals capacity/starvation).
- **Cleanup lag** within 30 minutes → alert on breach (signals storage-cost and privacy risk).
- **Failure rate** by converter family over a rolling window → alert on spike (signals a bad converter version or a poisoned input pattern).
- **Stuck `processing`** jobs past the stale threshold without takeover → alert (signals recovery not running).
- **Dead-letter / webhook failure** growth → alert.

Alert thresholds live in config and are reviewed in the runbook's SLO review.

### 17.3 Analytics dashboards

Admin/owner dashboards show:

- conversions by family
- conversions by format pair
- failure trends
- top failure codes
- average processing duration
- storage trends
- quota usage
- expensive users/workspaces
- cleanup status
- support actions

---

## 18. Security Requirements

- `DEBUG=False` in production
- real `ALLOWED_HOSTS`
- secrets from environment/secret manager
- HTTPS-only
- secure session and CSRF cookies
- HSTS
- CSRF on all forms
- authenticated uploads only
- object-level authorization on jobs, batches, files, downloads, presets, support views
- rate limits on auth, upload, conversion submission, retry, cancel, and download
- API tokens not part of this build unless needed for webhooks/admin internals
- upload type validation by content, not extension
- filename sanitization
- path traversal prevention
- output filenames generated by the app
- no raw internal paths in user errors
- signed/expiring download URLs or auth-gated streaming
- non-root worker
- per-job temp directories
- subprocess timeouts
- CPU/memory caps
- process group termination
- no converter worker network egress unless explicitly enabled
- file bomb defenses
- malware scanning architecture
- PII/sensitive metadata redaction in logs
- support access audited

---

## 18a. Untrusted-Binary Isolation Model

This is the security spine of *this* product. Running LibreOffice and FFmpeg on arbitrary user uploads is one of the richer remote-exploit surfaces in software (malicious documents, codec CVEs, decompression bombs, SSRF via document-embedded external references). v2 lists the right controls as a checklist; v3 elevates them to an explicit, enforced isolation model.

**Network isolation (enforced, not policy).** The conversion worker runs with **no egress network namespace** (or a runtime-enforced deny-all egress). This is non-negotiable because LibreOffice and any HTML/Markdown→PDF path will attempt to fetch remote images, stylesheets, and fonts — that is SSRF and silent data exfiltration straight out of a hostile document. "No egress unless explicitly enabled" is implemented as a network policy the converter cannot override, not a setting it trusts.

**Process isolation per conversion.** Each conversion runs with: read-only root filesystem, a writable tmpfs per-job work directory only, dropped Linux capabilities, no-new-privileges, and a seccomp profile restricting syscalls. The document and media queues — the highest-risk binaries — additionally warrant stronger isolation (gVisor / microVM) where the deployment supports it; the chosen tier is documented per queue.

**Resource enforcement (mechanism, not just a number).** CPU and memory caps are enforced via the container/cgroup limits plus `resource`/ulimit on the subprocess, and `estimate_cost` gates *admission* (reject before queueing if the estimate exceeds policy) while the runtime cap kills *actuals* that exceed it. Caps are a security control against resource-exhaustion inputs, not only a cost control.

**LibreOffice specifics.** Headless `soffice` is effectively single-profile and corrupts or serializes under concurrent invocations, and it leaks `soffice.bin` zombies. Each invocation gets its own `-env:UserInstallation` profile directory (or a managed pool), macros and external links are disabled in the profile, and the runner reaps orphaned processes via process-group control and `max_tasks_per_child`. This is the gnarliest converter and its concurrency model is an explicit decision (ADR-0020).

**FFmpeg specifics.** Input is constrained (duration/byte caps), protocols are restricted (no network protocols, no arbitrary `file:`/`pipe:` access beyond the job dir), and progress is parsed from a controlled stderr/`-progress` channel.

---

## 19. Data Privacy & Retention

File conversion products handle sensitive documents. The product must treat files as private by default.

Requirements:

- explicit retention policy per workspace
- output TTL visible to users
- automatic hard deletion after TTL
- audit of downloads
- audit of support access
- deletion of temp files in `finally` and scheduled sweeps
- object storage lifecycle policy aligned with app retention
- user/org export of job metadata
- organization offboarding procedure
- no training or secondary use of uploaded files
- diagnostic retention opt-in for failed jobs if needed
- sensitive metadata redaction in logs and support exports
- blobs encrypted at rest (SSE-KMS); per-tenant key strategy documented (§8.2)

**Right-to-erasure vs. append-only accounting.** Files are TTL-deletable, but the `usage_ledger`, `audit_events`, and domain-event history are append-only and reference user/file metadata. A data-subject erasure request therefore does **not** hard-delete those records — it **tombstones/anonymizes the subject**: file bytes and blobs are destroyed, identifying fields (filenames, IPs, user-agents, user/email references) are severed or hashed, while the accounting and audit rows survive as anonymized facts referencing an opaque subject id. Export, TTL deletion, and erasure are three distinct paths and are individually documented (ADR-0023). Erasure ≠ ordinary TTL deletion.

---

## 20. Data Model Summary

Core tables:

- `organizations`
- `workspaces`
- `memberships`
- `workspace_memberships`
- `conversion_batches`
- `conversion_jobs`
- `file_blobs`
- `job_events`
- `batch_events`
- `conversion_presets`
- `usage_ledger`
- `usage_quotas`
- `quota_decisions`
- `scan_results`
- `audit_events`
- `outbox_events`
- `notifications`
- `webhook_endpoints`
- `webhook_deliveries`

Constraints and indexes:

- public UUID unique for jobs and batches
- index jobs by `(workspace, created_at)`, `(owner, created_at)`, `(status, updated_at)`, `(expires_at)`
- index batches by `(workspace, created_at)` and `(status)`
- index usage by org/workspace/date
- constrain progress to 0–100
- constrain terminal timestamps
- unique idempotency key per actor/scope where applicable
- file checksum index for integrity and **within-tenant** dedup/cache reuse (never cross-tenant; see §22)
- partial indexes for active jobs, pending cleanup, expired outputs, failed jobs

---

## 21. Conversion Quality Validation

Each converter must validate output beyond “file exists.”

Examples:

Images:

- opens successfully
- target format matches
- dimensions within expected bounds
- mode is valid for target
- output is not empty
- metadata stripping applied if requested

Documents/PDF:

- PDF opens
- at least one page
- expected MIME/type
- reasonable output size
- no empty zero-page result

Data:

- CSV/JSON/XLSX parses
- row count plausible
- sheet exists when expected
- encoding valid
- output schema shape matches requested orientation

Media:

- output probes with FFmpeg/ffprobe
- duration close to source/trim expectation
- codec matches requested target
- GIF has frames
- audio stream exists when expected
- output not empty/corrupt

All validation failures become clean `failed` states with internal diagnostic codes.

---

## 22. Caching & Deduplication

The system may reuse recent outputs when all are true:

- **same tenant** (organization/workspace) — reuse is never cross-tenant
- same input checksum
- same source format
- same target format
- same converter version
- same option payload
- same workspace policy allows reuse
- prior output is still retained and validated
- user/org authorization permits access

Cache hits create a new job record with a linked reused output and audit event. Reuse and the underlying blobs are **scoped to a single tenant**: a checksum match across tenants is never a cache hit, because even revealing that another tenant holds byte-identical content is a dedup oracle and a privacy leak. The global checksum index supports integrity and within-tenant reuse only; it never crosses the tenant boundary. (ADR-0019.)

---

## 23. Architecture Decision Records

Required ADRs:

```text
ADR-0001: Celery + Redis is the asynchronous job backbone.
ADR-0002: HTMX polling is used instead of WebSockets.
ADR-0003: Converters use a registry-driven plugin interface.
ADR-0004: External binaries run through subprocess with process-group control.
ADR-0005: Progress is honest; determinate only when the converter reports real progress.
ADR-0006: Outputs are written to temporary storage and promoted only after validation.
ADR-0007: Job enqueueing happens only after database commit.
ADR-0008: Inputs and outputs follow explicit retention policies.
ADR-0009: Domain events and transactional outbox drive side effects.
ADR-0010: Organizations/workspaces and quotas protect cost, privacy, and reliability.
ADR-0011: DB + Web is the build boundary; public API is future.
ADR-0012: Malware scanning is a pipeline stage before conversion.
ADR-0013: Support actions require reasons and write audit events.
ADR-0014: At-least-once delivery is made exactly-once in effect via an atomic claim protocol and a per-claim fencing token (`claim_generation`); stale-worker terminal/output writes are rejected.
ADR-0015: Cancellation is cooperative — a DB cancel flag polled at safe checkpoints plus process-group termination; Celery `revoke` is best-effort only.
ADR-0016: High-frequency progress lives in Redis (throttled); the status endpoint reads cache with DB fallback; the durable row holds final state. HTMX polling backs off and stops on terminal.
ADR-0017: Output "promotion" is a DB-pointer flip after a validated, completed upload — not a non-atomic object-store rename; incomplete multipart uploads are lifecycle-aborted.
ADR-0018: Concurrent-limit quotas are enforced by atomic reservation (locked counter / Redis reserve-commit), not by reading the append-only usage ledger.
ADR-0019: Blobs, dedup, and cache reuse are tenant-scoped; the global checksum index serves integrity and within-tenant reuse only, never cross-tenant.
ADR-0020: Conversion runs in a network-isolated, capability-dropped, seccomp-confined sandbox with read-only rootfs; LibreOffice uses a per-invocation profile with macros/external links disabled.
ADR-0021: A trace context is propagated across web → broker → worker → subprocess (OpenTelemetry); SLOs and alerts are defined, not just metrics.
ADR-0022: Retries replay the originally pinned converter version for reproducibility; re-converting on a newer engine is an explicit, audited action.
ADR-0023: Right-to-erasure tombstones/anonymizes the subject while preserving append-only usage/audit history; export, TTL deletion, and erasure are distinct paths. Blobs are encrypted at rest (SSE-KMS).
```

---

## 24. Django App Boundaries

Recommended apps:

- `accounts` — auth, profile, notification preferences
- `organizations` — organizations, workspaces, memberships, roles
- `conversions` — jobs, batches, state machine, service layer
- `converters` — interface, registry, converter implementations
- `files` — FileBlob, storage backends, scanning, retention
- `quotas` — quota policies, decisions, usage ledger
- `presets` — option schemas and presets
- `notifications` — notification templates, delivery log, outbox consumers
- `webhooks` — outbound webhook endpoints and delivery log
- `audit` — audit events and support timelines
- `support` — support console and admin tools
- `ops` — health checks, metrics, management commands

Service-layer rule:

- views, HTMX endpoints, admin actions, and Celery tasks call application services
- state transitions live in services
- quota checks live in services
- storage promotion lives in services
- support actions live in services
- converters do conversion; services orchestrate conversion lifecycle

---

## 25. Production Scaffolding

Services in dev compose:

- web
- Celery worker
- Celery Beat
- PostgreSQL
- Redis
- MinIO
- optional scanning service

Production:

- managed PostgreSQL
- managed Redis
- S3-compatible object storage
- container host or ECS/Fargate-equivalent
- TLS and real domain
- Sentry/error tracking
- metrics/log aggregation
- secret manager/environment configuration

Dockerfile requirements:

- slim Python base
- LibreOffice installed
- FFmpeg installed
- non-root user
- system libraries documented
- collected static
- Gunicorn
- health command
- worker-compatible runtime
- worker image runs read-only rootfs with a writable tmpfs work dir, dropped capabilities, no-new-privileges, and a seccomp profile (§18a)
- worker network egress denied at the runtime/namespace level by default

Production also requires:

- KMS for at-rest blob encryption (SSE-KMS) and webhook/secret material
- OpenTelemetry collector (or host-native tracing) for cross-boundary traces
- bucket lifecycle rules: TTL expiry aligned to app retention, and abort-incomplete-multipart-upload

---

## 26. Testing & Quality Gates

### 26.1 Unit tests

- converter registry lookup
- unsupported-pair rejection
- option schema validation
- preset application
- filename sanitization
- type detection
- size-limit enforcement
- quota decision rules
- state transition rules
- retry classification
- retention policy
- checksum/deduplication logic
- output validation rules

### 26.2 Converter fixture tests

For each supported family:

- known-good sample converts successfully
- output opens/parses/probes
- MIME/type is correct
- metadata recorded
- invalid input fails cleanly

### 26.3 Failure-mode tests

- corrupt input
- unsupported input
- malware scan failure
- subprocess non-zero exit
- timeout
- OOM-style failure simulation
- cancelled job
- missing output
- empty output
- invalid output
- worker restart/idempotent rerun
- stale temp cleanup
- failed storage promotion
- cleanup retry
- **double-claim / fencing: a stale worker that wakes after takeover cannot promote output or write a terminal state (fenced write affects zero rows)**
- **stale-heartbeat takeover: a second worker correctly claims a job whose heartbeat expired, and exactly one terminalizes it**
- **cooperative cancellation mid-conversion: cancel flag honored at a checkpoint and subprocess process group killed**
- **cancel after success but before pointer flip resolves to `cancelled`, output discarded**
- **concurrent quota submissions: reservation prevents exceeding a concurrent/storage limit**
- **progress tier: status served from Redis with DB fallback; DB not written per progress tick**

### 26.4 Integration tests

- upload flow
- HTMX status polling
- job history filtering
- batch flow
- cancel/retry
- auth-gated download
- workspace authorization
- admin/support actions
- notification outbox
- webhook retry
- **webhook SSRF: private/loopback/link-local/metadata destinations are rejected**
- **right-to-erasure: subject anonymized, blobs destroyed, append-only ledger/audit retained**
- **sandbox egress: a document/HTML converter cannot reach the network**
- Celery eager mode
- at least one real worker smoke path

### 26.5 Storage tests

- MinIO/local S3 integration
- temporary output promotion (DB-pointer flip; half-written object unreachable)
- incomplete-multipart abort lifecycle
- signed URL/auth-gated download
- TTL cleanup
- file deletion
- at-rest encryption (SSE-KMS) on stored blobs
- cross-tenant isolation: a checksum match in another tenant is never a cache hit and never serves another tenant's bytes

### 26.6 CI gates

- ruff lint/format
- type checking with mypy or pyright
- pytest
- migration checks
- dependency/security scan
- Docker build
- docker-compose smoke test
- OpenAPI is not required because public API is out of scope
- coverage threshold

---

## 27. Performance & Reliability Targets

Baseline targets:

```text
Upload validation p95: < 500ms excluding file transfer.
Status endpoint p95: < 150ms.
Job list p95: < 300ms.
Batch detail p95: < 400ms.
Queue wait target for fast jobs: < 10 seconds under normal load.
Image conversion p95: < 30 seconds for allowed sizes.
Document conversion timeout: configurable, default 120 seconds.
Media conversion timeout: configurable by duration, with a hard cap.
Cleanup lag: expired outputs removed within 30 minutes.
Worker crash recovery: job either retries or lands in a clean retrying/failed state.
Output promotion: partial outputs are never downloadable.
```

Load/reliability checks:

- concurrent uploads from multiple workspaces
- concurrent job submissions under quota
- media jobs cannot starve fast jobs
- worker crash during processing
- worker pause-then-wake after takeover cannot double-promote (fencing holds)
- cancellation terminates an in-flight subprocess promptly
- concurrent submissions cannot exceed a concurrent/storage quota (reservation holds)
- Redis outage behavior documented
- object storage temporary failure handled cleanly
- cleanup idempotent under repeated runs

---

## 28. Operational Runbook

The repo includes a runbook covering:

- deployment
- rollback
- migrations
- worker startup/shutdown
- queue-depth inspection
- stuck job recovery
- stale-worker takeover and fencing diagnosis (zombie worker still running)
- safe retry
- safe cancellation
- quota incident response
- scan failure triage
- storage cleanup
- incomplete-multipart cleanup verification
- secret rotation
- Redis recovery
- object storage outage
- sandbox/egress verification
- LibreOffice failure diagnosis
- FFmpeg failure diagnosis
- support console usage
- backup and restore
- retention verification
- right-to-erasure procedure
- SLO review

---

## 29. Definition of Done

1. Authenticated users can upload supported files, select target formats/options/presets, and receive a tracked background job immediately; web requests never block on conversion.
2. Organizations, workspaces, memberships, quotas, and scoped job history work end to end.
3. Conversions across all four families work: image, data, document-to-PDF, and audio/video.
4. Batch conversion works with child jobs, partial success, cancellation, and ZIP download.
5. Adding a new format pair requires adding a converter, option schema, registry entry, and tests — not changing the job runner or templates.
6. FFmpeg jobs show true determinate progress; opaque converters show honest indeterminate progress.
7. Job and batch state machines are explicit, audited, idempotent, and terminal-state safe.
8. Celery enqueueing happens after DB commit; worker crashes do not silently lose jobs; retries are bounded and classified; an atomic claim plus a fencing token make at-least-once delivery exactly-once in effect — a stale worker cannot double-promote or double-terminalize.
9. Cancellation is cooperative (cancel flag polled at safe checkpoints) and terminates subprocess process groups and cleans partial files.
10. Upload allowlist, content validation, size limits, scan pipeline, filename sanitization, non-root workers, timeouts, resource caps, and file-bomb defenses are active.
11. Outputs are written to temporary storage, validated, and promoted by a fenced DB-pointer flip; partial or post-cancel outputs are never downloadable; incomplete multipart uploads are cleaned.
12. Inputs and outputs follow retention policy; cleanup removes expired files and stale temp artifacts.
13. Quotas and usage accounting protect cost and capacity at user/org/workspace levels; concurrent-limit quotas are enforced by atomic reservation, not by reading the ledger.
14. Domain events, audit events, and outbox side effects are present, idempotent, and testable.
15. Notifications and optional outbound webhooks work through retryable delivery logs, with SSRF defenses on the webhook sender.
16. Support console can inspect timelines, retry safe failures, cancel active jobs, view scan/quota/storage metadata, and audit every support action.
17. Observability is live: structured logs, Sentry/errors, metrics, cross-boundary traces, defined SLOs/alerts, health/readiness, queue depth, durations, failures, retries, timeouts, cleanup lag, and storage usage.
18. Security posture is verifiable: `DEBUG=False`, secrets in env, HTTPS, secure cookies, CSRF, rate limits, scoped downloads, support access audit, no raw internal paths in user errors.
19. Conversion runs in a network-isolated, capability-dropped, seccomp-confined sandbox; LibreOffice uses a per-invocation profile; blobs are encrypted at rest.
20. Blobs, dedup, and cache reuse are strictly tenant-scoped; no cross-tenant dedup oracle exists.
21. A right-to-erasure request anonymizes the subject while preserving append-only usage/audit history.
22. Tests cover unit, converter fixtures, failure modes (incl. fencing/double-claim, cooperative cancel, quota reservation race, sandbox egress, webhook SSRF), HTMX/views, batch flows, storage, quotas, support actions, and worker smoke paths.
23. CI quality gates pass; Docker Compose runs web, worker, beat, Postgres, Redis, and MinIO locally; no secret is committed.
24. Operational runbook and backup/restore procedures exist and have been walked through.

---

## 30. Suggested Build Order

This is a full-product build order, not an MVP boundary. The claim/fence protocol and the conversion sandbox are deliberately built early, because the job platform's correctness and safety depend on them before any real binary runs.

1. Django + PostgreSQL + Redis + MinIO via Docker Compose; env-driven settings; CI skeleton.
2. Accounts, organizations, workspaces, memberships, roles, and object-level permission helpers; tenant-scoped storage prefixes from day one.
3. ConversionJob (incl. `claim_generation`, `worker_id`, `cancel_requested`), ConversionBatch, FileBlob (tenant-scoped, encrypted), JobEvent, AuditEvent, UsageLedger, and OutboxEvent models.
4. Converter interface and registry with option schema support and version-pinning.
5. One image converter wired through the full background pipeline to prove the architecture.
6. Celery worker/beat integration, `transaction.on_commit()` enqueueing, **the atomic claim protocol + fencing token**, heartbeat, retries, and idempotent outbox emission.
7. The conversion sandbox: non-root worker, read-only rootfs, tmpfs work dir, dropped caps, seccomp, and no-egress namespace — before any external binary is added.
8. Redis progress/status tier; HTMX job status polling (with backoff) and job detail/history screens.
9. Storage pipeline: upload validation, temp keys, fenced DB-pointer promotion, signed/auth-gated download, incomplete-multipart lifecycle.
10. Scan pipeline as a first-class job (claim/fence/timeout/retry) and quarantine states.
11. Add data converters through registry only.
12. Add LibreOffice document-to-PDF converter with per-invocation profile, subprocess timeout, and output validation.
13. Add FFmpeg media converter with real progress parsing into the Redis tier.
14. Add conversion options and presets.
15. Add batch conversion, aggregate status, child job orchestration, and streaming ZIP output.
16. Add quotas, usage ledger, atomic reservation, quota decisions, fair scheduling, and queue limits.
17. Add cooperative cancellation (cancel-flag polling) + process-group termination + stale-processing takeover.
18. Add output validation per converter family.
19. Add domain events, transactional outbox, notifications, and optional outbound webhooks with SSRF defenses.
20. Add support console and admin operations.
21. Add analytics/metrics dashboards, distributed tracing, SLOs/alerts, and operational reporting.
22. Add retention cleanup, stale temp cleanup, lifecycle policies, deletion verification, and the right-to-erasure path.
23. Security pass: file validation, scoped storage, permissions, rate limits, CSRF, HTTPS settings, sandbox hardening, resource caps, enforced egress denial.
24. Failure-mode hardening: corrupt files, scanner failures, binary crashes, timeouts, invalid output, storage failure, worker crash, double-claim/fencing, cancel races.
25. Test suite completion and CI hardening.
26. Observability, runbooks, backup/restore, deployment, and full Definition of Done walkthrough.

---

**End of revised scope.**
