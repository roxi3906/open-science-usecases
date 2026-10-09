# Check and publish operations

The `Check and publish cases` workflow owns one native concurrency queue for main. The lock covers admission, structure and declaration checks, planning, uploading, and finalization. Pull requests run checks and the test suite without publishing or changing publication state. Main batches omit the test suite, which is verified on the PR.

## State and first setup

The GitHub branch `check-publish-state` contains only `state.json`. It is created by the first explicitly initialized manual run, not by normal pushes. State commits use a fast-forward reference update so concurrent or stale writers cannot overwrite another owner.

The state stores a confirmed successful commit, the failure latch, the current batch and its provisional completion, a conservative run-history audit boundary, and the last confirmed manual recovery coverage. The coverage marker acknowledges covered manual failures even when an older uncovered waiter keeps the audit boundary open. The active batch's run ID and endpoints identify exactly what was checked. A success candidate becomes effective **only when attempt 1 of the entire workflow has GitHub's final `completed/success` conclusion**. The next queue admission confirms that conclusion and materializes the new baseline. A finalizer cannot claim that its own workflow has already finished: this distinction prevents cancellation after its last step from being counted as success.

Required permissions:

- The workflow token needs `contents: write` to create/update this state branch and `actions: write` to cancel waiting runs. Repository/organization policy and branch rules must permit those writes. The workflow never writes the main branch.
- S3 credentials need the existing upload permissions plus `s3:ListBucket` and `s3:DeleteObject` for replacing `<case.name>/extracted/`. Restrict list/delete permissions to those prefixes under `AWS_TARGET_FOLDER`. Planning still needs no S3 access; no object GET, tagging or checksum queries are used. AWS CLI may perform its normal multipart upload operations. If bucket versioning is enabled, deletion hides current objects with delete markers; it does not purge historical versions.
- Preserve the state branch and the referenced attempt-1 run records. Missing/corrupt state, unavailable run evidence, or unavailable commits fail closed.

After you have **manually completed the initial full sync at a known main commit O**, initialize once:

```sh
repo=OWNER/REPO
baseline=FULL_40_CHARACTER_SHA_ALREADY_SYNCED

gh workflow run check-structure.yml --repo "$repo" --ref main \
  -f initial_baseline="$baseline"
```

Replace both placeholders. The dispatched main SHA is fixed as C; initialization runs the normal checked net difference O→C, which may be empty. An existing state branch makes `initial_baseline` an error; it cannot reset a previous deployment or silently bypass a failure. This command must be run after the workflow is available on main. Do not create a baseline from an assumed S3 state.

## Normal pushes and failures

Every main push retains its original `before→after` endpoints. Both check and upload checkout the fixed `after`. Admission requires the predecessor to equal the effective successful baseline; already-covered old pushes are skipped. The success baseline is never substituted for a normal push's `before`.

GitHub's `queue: max` admits up to 100 waiting runs and orders them by arrival at the concurrency queue, not commit time or workflow run number. A missing, reordered, canceled, timed-out or interrupted dependency causes a failure latch rather than waiting for another task that needs the same lock. A history audit also detects cancellation before admission could persist an active marker. Older unresolved runs remain visible across newer-numbered successes until their targets are covered. History is paginated through the workflow's unfiltered runs and restricted to main push/manual events locally, avoiding GitHub's filtered-query 1,000-result cap. It stops at the audit boundary or the end of the listing, so a long backlog can still be acknowledged after manual recovery. Commit coverage uses local Git objects when available, with cached comparisons and an API fallback only for commits missing from the checkout (such as later queued pushes). This avoids one remote comparison per historical run. Duplicate rows across pages are ignored; a stalled listing, API failure or unknown relationship produces an explicit error rather than returning partial evidence or assuming success.

On a check/upload failure, the latch is written before cancellation calls. Waiting pushes are canceled; manual runs are never canceled by this code. Pushes targeting commits after a queued manual recovery are preserved, then still must satisfy exact predecessor admission. If a run is terminated before its finalizer executes, the unfinished active marker/history audit blocks the next admission. If cancellation APIs fail, the latch remains set and remaining runs reject resource checking/publication at admission. Concurrent arrivals missed by a cancellation snapshot are rejected by the same latch.

Native reruns (`run_attempt > 1`), including failed-jobs-only reruns, are refused independently in check, publish, and finalization. They cannot write state, change the baseline, cancel batches, or upload resources.

## Manual recovery

After fixing the issue and pushing the intended final state to main:

```sh
gh workflow run check-structure.yml --repo "$repo" --ref main
```

Leave `initial_baseline` empty. No `before` or moving-main lookup is used. After acquiring the same lock, the workflow resolves the last overall success O and checks/publishes the final net manifest difference to its fixed dispatch SHA C. It does not replay failed batches. A check success alone cannot advance O. Resource failure prevents manifest upload and keeps the latch; an explicitly empty plan skips the publish job and counts as successful completion. Following admission confirms the terminal success and clears the latch. Older covered batches do not publish again; targets behind a confirmed baseline cannot roll it back.

A dispatch made while a newer main push is already queued may reach the lock in an unexpected order. Admission and ancestry checks remain authoritative. If an uncovered predecessor cannot be established, the run blocks with an error and requires another main dispatch; it never guesses or publishes an overlapping range.

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

The Python tests use temporary Git repositories, local HTTP GitHub API and package-download fixtures, and a filesystem S3 fixture supporting uploads and extracted-prefix deletion. These verify state transitions, archive parsing, download integrity, prefix replacement, and upload/delete failures without writing to GitHub state or real S3. Run them with:

```sh
python3 -B -m unittest discover -s .github/tests -v
```

A passing PR check does not exercise main's hosted concurrency scheduler, state-branch permissions, cancellation permissions or real S3 credentials. Those require an explicitly initialized main deployment after merge and manual initial synchronization.
