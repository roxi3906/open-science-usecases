# AIPOCH Open-Science Cases

This repository collects completed Open-Science session exports, covers, and introductions. The root `manifest.json` serves as the case index.

Classification is being migrated one catalog at a time. The first stage groups all 43 **AI Research** cases from upstream's [classification snapshot](https://github.com/aipoch/open-science-usecases/blob/2d19bdbaffa776fb6c75951005ac88e74cc6aac9/categories.json) under [`AI Research/`](<AI Research/>), with `catalog: "AI Research"` and `catalog_name: "ai-research"`. This includes 40 existing cases and three newly imported cases. The remaining 42 cases stay under `usecases/` with both classification fields set to `""`; their migration is deferred to later stages.

Each entry contains a `title`, a unique kebab-case `name`, the two classification strings, and three resources: `cover`, `case`, and `introduction`. Every resource provides `file_name`, `path`, `bytes`, `sha256`, and `release_url`. An empty URL selects a local file; a nonempty URL selects a remote resource. Fully remote entries do not need local directories but must still retain valid paths. The three newly imported AI Research packages retain their upstream remote sources; their covers and introductions are stored locally.

A nonempty `catalog` is the display label and a directory at the repository root. All resource paths start with `<catalog>/` and may use independent subdirectories beneath it. Derive `catalog_name` as ASCII kebab-case, for example `Data Science` becomes `data-science`. Non-ASCII names, empty conversions, and collisions between different catalogs produce errors for the user to resolve. Multiple cases may share a catalog. Both classification fields must be empty or both populated.

```text
Data Science/example/
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
