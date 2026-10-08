# Rules for Adding Cases

- When adding a case directory without a local `.science` package, you must set `case.release_url` in the corresponding entry in the root `manifest.json` to the download URL for that case's `.science` package. The field must not be missing, empty, or whitespace-only.
- Exactly one package source must be provided: a local `.science` file or a nonempty `case.release_url`. If the directory contains the local file, `case.release_url` must be an empty string. Keep `case.path` for both local and remote packages.
- Before completing a case addition, check each new directory and its manifest entry to ensure they meet these requirements.
