# Test Remote Case

Test data for a manifest entry whose `.science` package is hosted remotely.
This fixture deliberately reuses the existing flight reliability package; it
is not a new research result and no copy is stored in this directory.

Package source: [First Flight vs. Evening Flight](https://github.com/aipoch/open-science-usecases/releases/download/completed-cases-2026-09-23/first-flight-vs-evening-flight.science).

The manifest retains the existing package's declared byte size and SHA-256.
HEAD can report size, type, ETag, and last-modified time; it cannot verify the
package's checksum without downloading its content.

Expected behavior: validate and upload only the local Markdown and PNG under
`test-remote-case/`. Keep `case.release_url` nonempty, inspect the remote asset
with HEAD only, and never download or upload its content. If HEAD is unavailable,
report the manifest metadata and continue without falling back to GET.
