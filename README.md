# AIPOCH Open-Science Cases

This repository collects completed Open-Science session exports, covers, and introductions. The root `manifest.json` serves as the case index.

Existing cases live under `usecases/`. Future cases may use nested category directories or other locations. Validation and publication locate each resource directly through its `path`; no directory prefix, depth, or shared directory is required for the three resources. Each entry contains a `title`, a unique kebab-case `name`, and three resources: `cover`, `case`, and `introduction`. Every resource provides `file_name`, `path`, `bytes`, `sha256`, and `release_url`. An empty URL selects a local file; a nonempty URL selects a remote resource. Fully remote entries do not need local directories but must still retain valid paths.

```text
usecases/physics/example/
  cover.png
  session.science
  introduction.md
```

Directories, titles, and names can be chosen independently. S3 always uses `<name>/<file_name>`, with extracted package contents under `<name>/extracted/`. Moving directories does not change S3 resource keys.

Original `.science` session contents remain unchanged. Simulation demonstrations and unresolved conclusions noted in a case remain subject to the source session; inclusion does not guarantee general model performance.

- [Adding and updating cases](AGENTS.md)
- [Validation rules and commands](.github/CHECKING.md)
- [Publication, deduplication, and recovery](.github/PUBLISHING.md)

Local tests: `python3 -B -m unittest discover -s .github/tests -v`. Tests use temporary data and fake S3; they do not perform live publication.
