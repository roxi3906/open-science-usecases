# Rules for Adding Cases

- When adding a case directory without a local `.science` package, you must set `case.release_url` in the corresponding entry in the root `manifest.json` to the download URL for that case's `.science` package. The field must not be missing, empty, or whitespace-only.
- If the directory already contains a `.science` package, `case.release_url` may be empty. `case.path` and `case.release_url` may coexist.
- Before completing a case addition, check each new directory and its manifest entry to ensure they meet these requirements.
