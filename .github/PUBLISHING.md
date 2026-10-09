# Check and publish operations

The `Check and publish cases` workflow owns one native concurrency queue for main. The lock covers admission, structure and declaration checks, planning, uploading, and finalization. Pull requests run checks and the test suite without publishing or changing publication state. Main batches omit the test suite, which is verified on the PR.

## Publication history and migration

GitHub Actions attempt-one history is the publication record. No state file, state branch, deployment record or artifact is created or updated. An existing `check-publish-state` branch is ignored and left untouched.

Admission reads repository-wide run history, selecting only this repository's main push/manual `check-structure.yml` runs and the former `publish.yml` workflow's main `workflow_run` runs. Unfiltered pagination avoids GitHub's 1,000-result filtered-query cap. PRs and other workflows do not affect publication. Job evidence is fetched only for successful candidates that can extend the verified baseline or recovery coverage; covered older successes do not each consume another jobs request. Native reruns never replace attempt one's result.

A baseline requires the **entire attempt** to have a final `completed/success` result. For the combined workflow, the admission and upload-planning steps must have succeeded, and finalization must have verified a successful upload or an explicitly empty plan. A green check-only run, covered push with skipped planning, or skipped legacy upload is not a publication. Baseline selection follows commit ancestry rather than workflow creation order; divergent successful targets fail closed.

The former full-sync publisher is recognized automatically when its upload step succeeded. Its actual checked-out SHA is read from the retained checkout log: a `workflow_run` run's own `head_sha` can refer to a different default-branch commit. This allows migration from a verified old publication without entering `initial_baseline`. Once a combined-workflow manual recovery succeeds, future admission no longer needs the legacy logs.

Required permissions and evidence:

- The workflow token needs `contents: read` and `actions: write`. Actions write permission is used only to cancel waiting pushes; the workflow never writes Git refs or files.
- S3 credentials need the existing upload permissions plus `s3:ListBucket` and `s3:DeleteObject` for replacing `<case.name>/extracted/`. Restrict list/delete permissions to those prefixes under `AWS_TARGET_FOLDER`. Planning still needs no S3 access; no object GET, tagging or checksum queries are used. AWS CLI may perform its normal multipart upload operations. If bucket versioning is enabled, deletion hides current objects with delete markers; it does not purge historical versions.
- Preserve relevant run records, attempt-one job/step results, and legacy logs until migration completes. An API error, missing required job/log evidence, missing commit, stalled pagination or unknown ancestry stops publication. A deleted run that no longer appears in history cannot be detected as a missing failure; do not delete publication history while relying on this protocol. Actions history also cannot detect out-of-band changes to S3.

## Normal pushes and failures

Every main push retains its original `before→after` endpoints. Both check and upload checkout the fixed `after`. The push's `before` must equal the successful baseline; admission never silently changes it to another commit. Covered old pushes skip resource checks and uploads.

The shared native concurrency lock covers admission, checking, planning, upload and finalization. Queue arrival order is not necessarily commit order. A missing predecessor or nonterminal active publisher blocks rather than waiting while holding the lock.

Failed, canceled, timed-out and interrupted attempts are the failure record, including runs canceled before admission or after their finalizer. Subsequent automatic runs stop before checking resources or uploading until a successful manual recovery covers the failed target. Recovery coverage follows commit ancestry: covered push targets remain obsolete even if their runs are canceled later, while failed manual requests must also predate the successful recovery. A failed request for a later or unrelated target remains blocking even when its run number is smaller. Another ordinary successful run cannot silently clear an uncovered failure.

On failure, finalization cancels waiting pushes where possible; manual runs are never canceled by this code. Pushes beyond a queued manual recovery's target are preserved. If finalization is interrupted or cancellation fails, later admission still sees the failed attempt and refuses publication. A late finalizer whose attempt has already ended does not cancel waiters.

Native reruns (`run_attempt > 1`), including failed-jobs-only reruns, remain disabled independently in checking, uploading and finalization. Use a new main manual dispatch for recovery.

## Manual recovery

After fixing the issue and pushing the intended final state to main:

```sh
gh workflow run check-structure.yml --repo OWNER/REPO --ref main
```

Leave `initial_baseline` empty. Admission discovers the last successful publication O and checks/publishes the final net manifest difference to the fixed dispatch SHA C. It does not replay failed intermediate batches, and a stale manual target cannot roll back a newer published baseline. Only the entire manual attempt's final success establishes recovery coverage. Resource failure prevents manifest upload and leaves later pushes blocked. An explicitly empty plan skips upload and still permits successful recovery.

For the migration that first introduces this history-based workflow, previously failed main runs remain failures. Merge the workflow and manually dispatch it once to cover them; finding a legacy baseline alone does not bypass the failure gate.

If **no successful publication evidence exists at all**, manually complete a full sync at a known main commit O, then dispatch once with its full SHA:

```sh
gh workflow run check-structure.yml --repo OWNER/REPO --ref main \
  -f initial_baseline=FULL_40_CHARACTER_SHA_ALREADY_SYNCED
```

This emergency first-sync input cannot override existing successful history or an API/evidence error. The resulting successful manual run becomes the future history anchor. Do not guess a baseline from assumed S3 contents.

## Manifest contract and uploads

`manifest.json` is the publication authority. If it is unchanged, case resource checks are skipped and the upload plan is empty, even when directories or files were added, edited, renamed, or removed. When the manifest changes, only added or modified case entries select resources for checking; unrelated directories and unchanged entries are ignored. Formatting or entry reordering alone does not recheck resources. Removed entries are not checked, and their local files and existing S3 resources are left untouched. Every changed manifest, including a removal-only change, is still uploaded to S3 after any planned resource uploads succeed.

For added or modified entries, existing directory, filename, resource-path, size/availability and local/remote `.science` source checks remain in the check stage. The declaration stage additionally uses Git changes/object IDs to require a valid 64-digit hexadecimal SHA-256 declaration for affected local resources. Same-path content changes within those entries require a changed declaration, even when file size is unchanged. It does **not** compute or prove the actual SHA-256; contributors remain responsible for its truth. To publish resource edits staged by an earlier file-only commit, update the corresponding manifest SHA-256 declarations. Unchanged historical cases do not require a bulk declaration migration. The checker requires valid SHA-256 metadata for every resource in an affected case, including remote package metadata; it checks format without computing or downloading package contents.

Planning reads only the two manifests and Git metadata. New local targets, changed declarations, and remote-to-local transitions are uploaded. Unchanged target/declaration pairs are not. Removed resources and local-to-remote transitions leave original S3 resources intact. A changed manifest is uploaded last, only after every planned resource succeeds. No S3 HEAD/GET, ETag/size/checksum comparison, source metadata or version tags are used. Existing HEAD validation stays in the preceding check; that check never downloads or hashes resource bodies.

## Downloading and replacing extracted packages

The publish job runs a separate download/extraction step before the AWS upload step, sharing files through `RUNNER_TEMP`. It reuses the checker's `changed_entries` selection, then selects new cases or changes to package SHA-256, source (`path` / `release_url`), or destination (`name` / `file_name`). Cover/introduction/title-only changes, byte-size corrections without a changed package declaration, formatting, reordering, file-only commits and removed entries do not rebuild extracted content. Existing cases are not backfilled automatically when this workflow is introduced.

Every selected package is downloaded over HTTP(S): repository packages come from the fixed checked target commit on `raw.githubusercontent.com`, and external packages come from `case.release_url`. Downloading streams to disk and verifies the declared byte size and SHA-256 before unpacking. These checks belong to extraction, not the preceding metadata-only check. No local-checkout fallback is used. Remote original `.science` packages remain externally hosted; their extracted content is published to S3.

Supported packages are `open-science-session` schema 1 tar.gz archives containing version 2 `session.json`. The extractor starts with an empty per-case local `extracted/`, writes inventory objects using their `storageKey` paths, and preserves all levels including `artifacts/`, `notebooks/`, `uploads/`, and `execution-file-evidence/`. The JSON retains its structure; exact `$DATA/<storageKey>` references to packaged files become relative paths. Working-directory markers and historical prose remain unchanged. `records.json`, package metadata, and duplicate `objects/` storage are not published. Missing inventory files, invalid JSON, unsafe paths and archive links fail extraction. Content is never executed.

After **every** selected package downloads and extracts successfully, the step adds generated files to the upload plan. The publisher refuses unprepared package plans and preflights files before S3 writes. It recursively deletes each selected case's exact `<case.name>/extracted/` prefix (with the trailing slash), then uploads resources and generated content, and finally the root manifest. For example, the entry point is `<case.name>/extracted/session.json`; the root manifest schema does not change. Unrelated cases, sibling prefixes, covers and original packages are not deleted.

Replacement is not atomic: readers can see missing or partially uploaded extracted content between deletion and completion. Delete or upload failure prevents the final manifest upload and uses the existing failure latch and manual recovery. A normal recovery retries only packages selected by O→C's net difference, recreating their output and clearing their prefixes again. A package reverted to O after a partial replacement is not automatically repaired; the existing no-reconciliation limitation below also applies to extracted content. Ensure list/delete permissions are provisioned before deploying this workflow.

Recovery deliberately accepts partial writes left by a failure. Only O→C's net manifest difference is published: a failed intermediate object can remain, and a resource reverted to O's declaration is not repaired. There is no garbage collection, remote reconciliation, or automatic full synchronization.

## Verification scope

The Python tests use temporary Git repositories, local HTTP GitHub API and package-download fixtures, and a filesystem S3 fixture supporting uploads and extracted-prefix deletion. These verify history-based admission, recovery coverage, archive parsing, download integrity, prefix replacement, and upload/delete failures without writing to GitHub or real S3. Run them with:

```sh
python3 -B -m unittest discover -s .github/tests -v
```

A passing PR check does not exercise main's hosted concurrency scheduler, cancellation permissions or real S3 credentials. Those require an actual main deployment after merge; local fixtures and read-only history checks do not prove hosted publication success.
