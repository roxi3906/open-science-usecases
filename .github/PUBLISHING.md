# Case Publication

The baseline is the current S3 `manifest.json`. The target is the manifest's exact bytes
in the fixed Git checkout, never a push range. Only `NoSuchKey` establishes a missing
baseline; permission, transport, bucket, JSON and schema errors fail preparation.
The workflow lock covers baseline reading, preparation, all resource writes, manifest
batches and postprocessing. Every publisher, including a recovery operator, must hold
the same lock for the same target.

## Differences and task inventory

Historical indexes may omit introductions, both classification fields, and cover or
introduction `release_url` fields. A missing URL compares as `""`; SHA comparisons
ignore hexadecimal letter case. Targets satisfy the current [schema](CHECKING.md).

| Change | Selected work |
| --- | --- |
| `sha256`, `bytes`, `file_name`, or normalized `release_url` changes | Stage and overwrite that resource, without a pre-upload HEAD or deduplication |
| Added resource | Stage and overwrite, even if leftover target objects exist |
| Selected `.science` | Upload the original and overwrite **every** object in its verified extracted inventory |
| Only `path`, title, classification, order, removal, formatting, or SHA case changes | No resource upload, repair, or proactive HEAD; synchronize the final index |
| Unchanged declaration | Do not scan or repair the remote resource |

Local source paths and S3 destinations are independent. The consumer contract remains:

```text
<AWS_TARGET_FOLDER>/<case.name>/<file_name>
<AWS_TARGET_FOLDER>/<case.name>/extracted/<restored storageKey>
<AWS_TARGET_FOLDER>/manifest.json
```

Staging directory names do not determine keys. File renames upload the new key;
cover/introduction renames record the old key for post-commit cleanup. Same-key writes
overwrite without deletion. Package renames and removed cases do not trigger broader
historical resource garbage collection.

The plan retains validated case patches (`steps`) and an execution inventory organized
as `tasks → cases → objects`. Each object records `local_path`, `target_key`, `bytes`,
`sha256`, `content_type`, `action`, `reason`, and `old_key` when renamed. Directory work
records the staging root, exact trailing-slash prefix and complete object list.
Connected classification migrations form one task even if intermediate indexes would
be legal. That task includes all related case patches but uploads only changed resources.
Index-only patches are deferred to final synchronization.

## Preparation and safety

All selected local and remote sources finish staging, actual size/SHA verification and
safe extraction **before any S3 resource write**. Local inputs must be committed regular
files in the fixed tree with no changed working-copy bytes. All selected packages reuse
their verified staged original; remote packages are downloaded once. Source validation,
manifest schema/global catalog validation and local symlink restrictions still apply.

Extraction preserves the bytes of `session.json`, `records.json`, `manifest.json` and
`README.md`. Inventory resources are restored under `extracted/` by `storageKey`, with
`content-dedupe` support. Do not publish `objects/` or rewrite historical `$DATA/` strings.
Consumers resolve `$DATA/<storageKey>` against the extracted prefix and encode segments.

Traversal, absolute paths, links, special files, duplicate/cross-platform conflicting
paths, unsupported formats/features, undeclared or missing files, invalid size/SHA and
corrupt gzip streams fail extraction. Execution regenerates the candidate and task plan
from the fixed commit, verifies every staged file, and reconstructs the complete unpacked
inventory from verified archive bytes. Omissions, extra objects and plan tampering fail
before writes. Each upload, including repair, checks its staged source again.

## SDK pools and scheduling

Install `.github/scripts/requirements-publication.txt`; CI explicitly installs this lock.
An S3 SDK client is constructed before workers start and shared across all tasks. The
HTTP connection pool is 64; standard SDK retry uses at most four attempts. Connection
capacity is not a rate limiter. Resource transfers disable SDK internal concurrency
(including the automatic CRT transfer manager) to avoid nested upload pools.

* Up to four ordinary single-case tasks run concurrently.
* A multi-case task stops further ordinary dispatch, drains earlier running tasks, and
  runs alone in the outer scheduler. Its own pool runs at most four cases. Later tasks
  cannot overtake it.
* The shared upload pool defaults to 16; `PUBLICATION_UPLOAD_WORKERS` accepts 1–64
  as an environment variable or repository Actions variable. It replaces the previous
  `CASE_UPLOAD_WORKERS` / `FILE_UPLOAD_WORKERS` settings; case concurrency is fixed at four.
* Every case shares one 32-worker HEAD pool.
* Separate executors and bounded submission windows prevent dependency deadlocks and
  unbounded object-future queues. The monitor bounds out-of-order task results as well.

Every selected object uses the same upload boundary with its own actual SHA metadata
and Content-Type. Multipart transfers remain supported with serial SDK part processing.
No `sync` size/time skipping or recursive single-metadata command is used.

After all selected objects of a case upload, HEAD checks only those exact inventory keys.
It compares `ContentLength` and `Metadata.sha256` (case insensitive). Missing object,
missing SHA metadata, wrong size or wrong SHA queues only that object for `replace` and
another HEAD. At most two repair rounds are allowed. 403 errors are fatal; exhausted
SDK transient retries, including 503/timeouts, are fatal, not missing-object evidence.
This verifies size and custom metadata; it does not recompute a remote-body SHA.

Only after all selected uploads and HEAD checks succeed, list the exact selected
`<name>/extracted/` prefix with pagination. Delete remote keys absent from the complete
new inventory, in batches of at most 1,000; inspect individual deletion errors. Never
clear the prefix first. A cleanup failure prevents the case/task from becoming ready.

## Manifest monitor

Task states are `waiting → running → ready_to_commit → manifest_committed → completed`,
or `failed`. Workers only produce resource results; one monitor owns the baseline,
ETag, status snapshots and manifest writes.

The monitor consumes ready tasks in plan order, buffering early completions. Every two
consecutive ready tasks form one batch; a multi-case task counts as one and is never
split. Apply those complete patches to the last successfully read-back baseline and
validate the resulting full index. Save the batch manifest and postprocessing intent,
then conditionally PUT using `If-Match` or first-write `If-None-Match: *`. Read back exact
bytes before updating the baseline/ETag. Conflicts never fall back to unconditional PUT.
Only an uncertain connection/timeout result permits read-back resolution.
Conditional manifest PUTs use a separate, pre-created client with a single attempt;
SDK retries must not turn a lost success response into a false ETag conflict. Resource
uploads and reads retain their bounded SDK retries on the shared resource client.

The last one-task tail also commits. Once every task succeeds, publish the exact fixed
manifest including remaining index-only changes. The last resource batch performs that
final synchronization itself, avoiding an extra PUT. With no resource tasks, one final
manifest PUT and CDN refresh still run.

After read-back, delete only the batch's renamed cover/introduction keys that the current
index no longer references. Invalidate the manifest plus actually uploaded case prefixes
in one CloudFront request and wait for completion. A batch becomes completed only after
cleanup and CDN succeed. Index-only synchronization invalidates only the manifest.

## Evidence, failures and recovery

Console output is limited to plan, task/case phase and manifest batch summaries, including
counts and elapsed case times. Object expectations, observed HEAD metadata, errors and
repair events go to `events.jsonl` through one bounded event writer. Status snapshots
are written at monitor boundaries, not for every object. The artifact contains the plan,
baseline, per-step manifests/inventories, `status.json`, `summary.md`, and `batches/` with
exact submitted manifests and durable postprocessing intent. Staged bodies are excluded.
Actions retains evidence for 14 days. Resource URLs must not contain credentials.

Any task failure stops new dispatch, cancels work not started and drains in-flight work.
No subsequent manifest batch or FINAL_SYNC runs. Successful earlier batches remain.
Other concurrent tasks may already have overwritten objects. Events record successful
object uploads and task states identify uploads without a committed index. Directory
replacement and multi-resource publication are individual object operations, not atomic.
`uploaded_uncommitted` retains successful uploads even if a later HEAD or repair fails.
Index-only FINAL_SYNC records acceptance and read-back separately, including when
postprocessing fails after the manifest has already been committed.

Each batch saves `cleanup_pending`, `cdn_pending`, target/distribution, task patches,
manifest SHA and accepted/read-back state **before** its PUT. A failed read-back can leave
commit acceptance unknown. A cleanup or CDN failure retains exact pending work even if
later ordinary planning sees no manifest differences.

To recover postprocessing, download and preserve the failed run's artifact, configure the
same `AWS_TARGET_FOLDER` and `AWS_DISTRIBUITION_ID`, acquire the publication workflow lock,
and execute this entry point against the directory containing `batches/`:

```sh
python3 .github/scripts/publish.py --recover-postprocess /path/to/publication-plan
```

Recovery needs the original fixed Git commit available in the current repository. It
reconstructs task patches from that commit and the saved baseline, validates the batch
manifest, target, distribution, recorded identity and pending operations,
reads the current index, and only performs still-applicable cleanup/CDN. It never uploads
resources or manifests. Keys reintroduced by a newer manifest are retained. An unverified
commit requires an exact current-manifest match; a definite denied/conflicting PUT cannot
be recovered as accepted just because its bytes match. If evidence cannot establish the
commit, investigate rather than replaying the upload plan. CDN retry may create another
invalidation; check an accepted request's status if the original wait was interrupted.
Recovery updates batch evidence; original task status remains the historical run outcome.

After resolving pending postprocessing and the failure cause, dispatch a fresh workflow
on main with `dry_run: false` to replan remaining resource differences against S3. Native reruns remain rejected
(`GITHUB_RUN_ATTEMPT != 1`). Do not reuse staged plans or retry completed extraction.
A fresh no-difference publication does **not** resolve a prior run's pending postprocessing;
use the failed artifact. Reusing an evidence directory with pending postprocessing is
rejected, so a no-difference plan cannot overwrite its recovery intent. Once no pending
postprocessing remains, reusing a finished run directory archives the managed evidence
to a sibling `<directory>-previous-<unique-id>` directory before creating new batches;
unrelated files are retained. Queued runs are
not guaranteed commit ancestry order.

No proactive object repair or broad garbage collection occurs. Unchanged declarations
cannot detect manually removed objects or repair overwrites from uncommitted tasks;
explicit resource repair must be arranged separately.

## Local validation and migration acceptance

```sh
python3 -m pip install -r .github/scripts/requirements-publication.txt
python3 -B -m unittest discover -s .github/tests -v
```

Tests use temporary Git trees, local HTTP and a thread-safe fault-injectable fake SDK.
Concurrency tests use barriers/events. Real AWS permissions, transport, CloudFront and
consumer compatibility require separately authorized integration validation; local test
success is not a real publication result.

Manual workflow dispatch defaults to `dry_run: true`, which runs these tests without
production AWS credentials. PR CI runs the same suite. Set `dry_run: false` only for
an authorized publication on main.

For path/classification migrations, preserve resource contents and metadata. Use the
pre-migration fake S3 manifest as baseline: assert empty upload/extraction/deletion lists,
exact final manifest bytes, and unchanged resource keys/bytes. Existing unclassified
cases retain `usecases/` paths with `catalog: ""` and `catalog_name: ""`.
