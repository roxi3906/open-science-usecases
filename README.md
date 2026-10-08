# Open-Science Use Cases

Nine completed research cases, each with a `.science` file and a cover image.

Download a `.science` file to open in Open-Science.

| Case | .science file | Cover | File size |
| --- | --- | --- | --- |
| [Can a Simple Algorithm Beat AI at Wordle](Can%20a%20Simple%20Algorithm%20Beat%20AI%20at%20Wordle/) | [Download](Can%20a%20Simple%20Algorithm%20Beat%20AI%20at%20Wordle/Can%20a%20Simple%20Algorithm%20Beat%20AI%20at%20Wordle.science?raw=1) | [Cover](Can%20a%20Simple%20Algorithm%20Beat%20AI%20at%20Wordle/Can%20a%20Simple%20Algorithm%20Beat%20AI%20at%20Wordle.png) | 0.6 MiB |
| [Can AI Spot the Errors in a Spreadsheet](Can%20AI%20Spot%20the%20Errors%20in%20a%20Spreadsheet/) | [Download](Can%20AI%20Spot%20the%20Errors%20in%20a%20Spreadsheet/Can%20AI%20Spot%20the%20Errors%20in%20a%20Spreadsheet.science?raw=1) | [Cover](Can%20AI%20Spot%20the%20Errors%20in%20a%20Spreadsheet/Can%20AI%20Spot%20the%20Errors%20in%20a%20Spreadsheet.png) | 0.2 MiB |
| [Can GLP-1 Drugs Really Help Us Live Longer](Can%20GLP-1%20Drugs%20Really%20Help%20Us%20Live%20Longer/) | [Download](Can%20GLP-1%20Drugs%20Really%20Help%20Us%20Live%20Longer/Can%20GLP-1%20Drugs%20Really%20Help%20Us%20Live%20Longer.science?raw=1) | [Cover](Can%20GLP-1%20Drugs%20Really%20Help%20Us%20Live%20Longer/Can%20GLP-1%20Drugs%20Really%20Help%20Us%20Live%20Longer.png) | 0.1 MiB |
| [First Flight vs. Evening Flight - Which Is More Reliable](First%20Flight%20vs.%20Evening%20Flight%20-%20Which%20Is%20More%20Reliable/) | [Download](https://github.com/aipoch/open-science-usecases/releases/download/completed-cases-2026-09-23/first-flight-vs-evening-flight.science) | [Cover](First%20Flight%20vs.%20Evening%20Flight%20-%20Which%20Is%20More%20Reliable/First%20Flight%20vs.%20Evening%20Flight%20-%20Which%20Is%20More%20Reliable.png) | 650.0 MiB |
| [Hotter Years, Hotter Days](Hotter%20Years%2C%20Hotter%20Days/) | [Download](Hotter%20Years%2C%20Hotter%20Days/Hotter%20Years%2C%20Hotter%20Days.science?raw=1) | [Cover](Hotter%20Years%2C%20Hotter%20Days/Hotter%20Years%2C%20Hotter%20Days.png) | 49.6 MiB |
| [How Many Homes Could AI Data Centers Power](How%20Many%20Homes%20Could%20AI%20Data%20Centers%20Power/) | [Download](How%20Many%20Homes%20Could%20AI%20Data%20Centers%20Power/How%20Many%20Homes%20Could%20AI%20Data%20Centers%20Power.science?raw=1) | [Cover](How%20Many%20Homes%20Could%20AI%20Data%20Centers%20Power/How%20Many%20Homes%20Could%20AI%20Data%20Centers%20Power.png) | 4.9 MiB |
| [Our Nearest Exoplanet - How Long Is the Trip](Our%20Nearest%20Exoplanet%20-%20How%20Long%20Is%20the%20Trip/) | [Download](Our%20Nearest%20Exoplanet%20-%20How%20Long%20Is%20the%20Trip/Our%20Nearest%20Exoplanet%20-%20How%20Long%20Is%20the%20Trip.science?raw=1) | [Cover](Our%20Nearest%20Exoplanet%20-%20How%20Long%20Is%20the%20Trip/Our%20Nearest%20Exoplanet%20-%20How%20Long%20Is%20the%20Trip.png) | 3.1 MiB |
| [Which Planet Is Most Like Earth—and Who Decides](Which%20Planet%20Is%20Most%20Like%20Earth%E2%80%94and%20Who%20Decides/) | [Download](Which%20Planet%20Is%20Most%20Like%20Earth%E2%80%94and%20Who%20Decides/Which%20Planet%20Is%20Most%20Like%20Earth%E2%80%94and%20Who%20Decides.science?raw=1) | [Cover](Which%20Planet%20Is%20Most%20Like%20Earth%E2%80%94and%20Who%20Decides/Which%20Planet%20Is%20Most%20Like%20Earth%E2%80%94and%20Who%20Decides.png) | 12.1 MiB |
| [Why Do Coffee and Sleep Studies Disagree](Why%20Do%20Coffee%20and%20Sleep%20Studies%20Disagree/) | [Download](Why%20Do%20Coffee%20and%20Sleep%20Studies%20Disagree/Why%20Do%20Coffee%20and%20Sleep%20Studies%20Disagree.science?raw=1) | [Cover](Why%20Do%20Coffee%20and%20Sleep%20Studies%20Disagree/Why%20Do%20Coffee%20and%20Sleep%20Studies%20Disagree.png) | 0.1 MiB |

The flight reliability case is available from [Releases](https://github.com/aipoch/open-science-usecases/releases/tag/completed-cases-2026-09-23). The other eight case files are stored in their folders.

The [manifest.json](manifest.json) file groups each case into one object with a
URL-safe `name`, plus nested `cover`, `case`, and (when available)
`introduction` resources. Each resource records its filename, byte size,
SHA-256 checksum, and relative path. The `.science` resource also has a
`release_url`, which is empty when the case is stored in this repository. The
root README and per-case README files are not included as introductions.

## Adding a case

The `Check case structure` GitHub Actions workflow checks new top-level case
directories on pull requests and branch pushes. Existing directories are not
revalidated, and dot-prefixed tooling directories (such as `.github`) are ignored.
A directory rename counts as a new directory. Pull requests compare against the
common ancestor with the base branch; pushes compare against the previous commit.
The first push to a new feature branch compares against the common ancestor
with the default branch. Only the initial push of the default branch compares
against an empty tree.

For a directory named `My New Case`, include:

- `My New Case.md` and `My New Case.png`.
- Either `My New Case.science`, or a `README.md` containing an absolute HTTP(S)
  download link whose URL path ends in `.science`. Query strings and fragments
  are allowed. Use percent-encoding for spaces and parentheses in the URL.

Add exactly one matching entry in `manifest.json`. Its `title` must equal the
directory name, and its `name` must be the ASCII kebab-case form (`my-new-case`).
The conversion separates camel-case words, folds accented Latin characters,
lowercases letters, and replaces punctuation/whitespace with hyphens.

New entries require exactly `title`, `name`, `cover`, `case`, and `introduction`.
Each resource requires `file_name`, `path`, `bytes` (a nonnegative integer), and
`sha256` (64 lowercase hexadecimal characters). Filenames and paths must refer
to the corresponding same-named files in the case directory. Local byte sizes
and SHA-256 checksums must match the actual files. Symlinks are not accepted.

The `case` object additionally requires `release_url`: use an empty string for a
local `.science` file, or an exact matching download link from the case README
for a remotely hosted file. Keep `file_name` and `path` even when the file is
hosted remotely. Remote sizes and checksums are checked for format only; the
workflow does not download release assets. A README does not replace the required
same-named introduction Markdown for new cases.

The directory step exports a JSON array through `GITHUB_OUTPUT` as `cases`,
including each directory's name, validity, local-file presence, and parsed
download URLs. The manifest step receives it through the `CASES_JSON` environment
variable. Any validation error fails the workflow and identifies the directory
or field in the logs.

To reproduce the checks locally after committing the proposed case files, run
from the repository root with Python 3.9 or later:

```bash
python3 -B -m unittest discover -s .github/tests -v
CASES_JSON="$(python3 .github/scripts/check_structure.py directories --base origin/main --merge-base)" &&
  CASES_JSON="$CASES_JSON" python3 .github/scripts/check_structure.py manifest
```

Use `--base <previous-commit>` without `--merge-base` to reproduce a push check.
