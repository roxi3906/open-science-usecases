# Rules for Adding Cases

- When adding a case directory without a local `.science` package, you must set `case.release_url` in the corresponding entry in the root `manifest.json` to the download URL for that case's `.science` package. The field must not be missing, empty, or whitespace-only.
- Exactly one package source must be provided: a local `.science` file or a nonempty `case.release_url`. If the directory contains the local file, `case.release_url` must be an empty string. Keep `case.path` for both local and remote packages.
- Before completing a case addition, check each new directory and its manifest entry to ensure they meet these requirements.

# SHA-256 Metadata

- Before committing added or modified case resources, compute SHA-256 from the actual files on the user's device and update the corresponding `sha256` values in `manifest.json`. For remotely hosted `.science` packages, compute the digest from the local package before uploading it.
- Every resource (`cover`, `case`, and `introduction`) in an affected case must have a `sha256` string containing exactly 64 hexadecimal characters (`0-9`, `a-f`, or `A-F`), without whitespace.
- The check action validates SHA-256 presence and format only. It must not download resource bodies or recompute checksums; existing HEAD availability and size checks remain in place.
