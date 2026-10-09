# Check and publish operations

The `Check and publish cases` workflow keeps one native serial queue per ref (`queue: max`, `cancel-in-progress: false`). For main, the lock covers reading the target S3 manifest, planning, source checks, extraction and every upload. Pushes and manual dispatches publish their fixed `github.sha`; publication never follows a moving branch tip. Pull requests run structure/declaration checks and the Python test suite without S3 credentials or publication.

## Configuration and first publication

Set these repository secrets:

- `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
- `AWS_DEFAULT_REGION`
- `AWS_TARGET_FOLDER`, an `s3://bucket[/prefix]` URI

The AWS identity needs permission to read the root manifest, list the target prefix, upload objects and delete objects under selected case `extracted/` prefixes. Listing permission is also needed for S3 to report an absent key as `NoSuchKey` instead of an ambiguous access error. Bucket configuration may require additional permissions such as KMS access.

The publisher reads `<AWS_TARGET_FOLDER>/manifest.json` using `GetObject`. Only an explicit `NoSuchKey` response means first publication. In that case it plans every resource declared by the fixed repository manifest and extracts every declared `.science` package. Local originals are uploaded; remote originals remain at `case.release_url`. Even an empty repository manifest is uploaded on first publication.

Access errors, network errors, missing buckets, ambiguous HTTP 404 responses, invalid JSON, duplicate keys/names and invalid manifest resource metadata stop preparation. There is no empty-baseline fallback for these failures. Fix the stored manifest or restore a known valid copy when it is malformed; do not delete it merely to bypass a read error.

There is no Actions-history lookup, `initial_baseline` input, publication commit range, failure latch or finalizer. No Actions write permission is needed. An existing valid S3 manifest becomes the baseline immediately when this workflow is introduced.

## Planning and checking

The root manifest is the publication authority. Resource selection compares the fetched S3 declarations to the fixed repository declarations by case name and resource destination. New local resources and changed resource metadata (checksum, source path, filename, size or package URL) are uploaded directly over their destinations. SHA hexadecimal letter case alone does not change resource content. Unchanged resources are skipped. File-only edits absent from manifest changes do not cause publication; update declarations when changing resources.

Added or modified case records are validated before downloading or extracting any package. Required case directories and local files must exist and must not be symlinks. Each package has exactly one source: a local declared `.science` file with an empty `release_url`, or no local file and a valid nonempty HTTP(S) `.science` `release_url`. Remote packages still retain `case.path`. Legacy entries without an introduction are supported when publishing the resources they actually declare; PR checks retain their existing introduction requirement for added/edited cases.

The check stage validates manifest structure, paths, source rules and 64-digit hexadecimal SHA-256 declarations. Existing HEAD requests still check resource availability and byte size. It does not download resource bodies or compute their checksums. PR declaration checks may compare Git object identities to detect unsynchronized declarations; Git diffs are never the publication baseline. Publication checks select affected entries from the S3 plan, including entries that have been unchanged in Git since a previous failed release.

Removed cases or resources disappear only from the final root manifest. Their original S3 files and extracted content remain. A local-to-remote package transition retains the old original S3 package. Unlisted files are ignored. Metadata-only or formatting changes still publish the final root manifest, even if no resource uploads are needed. An identical manifest skips processing and uploads.

## Downloading and replacing science packages

Every new or changed `.science` declaration selects extraction, including changes to checksum, byte size, source or destination. Cover/introduction/title-only changes do not rebuild unchanged packages. Removed entries are never extracted or cleared.

After the complete resource plan and source checks pass, a separate step downloads every selected package. Repository packages use the fixed commit on `raw.githubusercontent.com`; external packages use `case.release_url`. Downloads stream to temporary disk and verify actual byte size and SHA-256 before unpacking. These integrity checks belong to extraction, not the metadata-only check stage. No local-checkout fallback is used for unpacking.

Supported packages are `open-science-session` schema 1 tar.gz archives containing version 2 `session.json`. The extractor starts with an empty local per-case `extracted/`, writes inventory objects at their `storageKey` paths, and preserves nested artifacts, notebooks, uploads and execution evidence. Exact `$DATA/<storageKey>` references to packaged files become relative paths; working-directory markers and historical prose stay unchanged. `records.json`, package metadata and duplicate `objects/` storage are not published. Invalid JSON, missing inventory objects, unsafe paths and archive links fail extraction. Content is never executed.

All selected packages must finish extraction before any S3 mutation. The publisher preflights all planned files, then recursively clears each selected case's exact `<case.name>/extracted/` prefix, including stale and hidden objects. The trailing slash protects similarly named sibling prefixes. It uploads resources and generated files, then uploads the root manifest last. Unrelated cases and original resources are not deleted.

Every uploaded object receives user metadata `sha256`, computed by streaming the actual file being uploaded. This includes local originals, covers, introductions, extracted binary files, the rewritten `session.json` and the final root manifest. This metadata is not copied from the manifest declarations and is not used to select uploads.

## Failure and retry behavior

Any preparation, source check, download, unpack, delete or upload failure stops the current publication. A missing later local file is detected before any remote mutation, even when the plan has no packages. A failed resource upload prevents all later uploads, including the root manifest. If the final manifest upload itself fails, the run also fails.

After fixing the cause, dispatch a new run against main:

```sh
gh workflow run check-structure.yml --repo OWNER/REPO --ref main
```

Native reruns remain disabled; use a new dispatch with its own fixed SHA and a fresh S3 manifest read. Later queued runs are not canceled or blocked by Actions history: each compares its own fixed target with the current S3 index. The queue serializes execution but does not establish commit ancestry ordering, so an older queued target can publish an older manifest. Dispatch the intended current main version after obsolete queued work if necessary.

Publication is not atomic. Replacing extracted content temporarily exposes missing or partial files, and successful writes before a later failure remain on S3. The old root manifest remains until the final upload succeeds. Retrying the same target compares against that old manifest and repeats the planned replacement. If a subsequent target reverts a partially written resource to its old manifest declaration, it is considered unchanged and is not automatically repaired. There is no object reconciliation or garbage collection; an operator must repair such partial/reverted objects explicitly. Do not publish to the same target outside the serialized workflow while a run is active.

## Verification scope

Run the complete local suite with:

```sh
python3 -B -m unittest discover -s .github/tests -v
```

Tests use temporary Git repositories, local HTTP fixtures and a filesystem AWS boundary. They cover first full publication, S3-based incremental plans, empty and removal-only manifests, retained original/extracted objects, missing/invalid sources, baseline read/format failures, HEAD-only checks, download integrity, safe extraction, science replacement, actual upload checksum metadata and fail-fast behavior. Integration tests execute the workflow's preparation, check, extraction and upload commands under a fail-fast shell, including retrying a partial replacement.

Local passing tests do not establish hosted queue behavior, live AWS permissions or a successful real S3 deployment. Those require a main deployment after merge.
