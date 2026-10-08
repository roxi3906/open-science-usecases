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

## Publish to S3

Run the standalone **publish** workflow manually from the GitHub Actions tab.
Configure these repository secrets first:

| Secret | Value |
| --- | --- |
| `AWS_ACCESS_KEY_ID` | AWS access key with permission to upload to the target prefix |
| `AWS_SECRET_ACCESS_KEY` | Corresponding AWS secret access key |
| `AWS_DEFAULT_REGION` | Region of the destination bucket |
| `AWS_TARGET_FOLDER` | S3 destination URI, such as `s3://my-bucket/usecases` |

The workflow reads `manifest.json` and uploads each local `cover`, `case`, and
optional `introduction` resource to
`<AWS_TARGET_FOLDER>/<name>/<file_name>`. It uses the manifest's kebab-case
`name` as the case directory and preserves each resource's original filename.
All local files must pass size and SHA-256 checks before any upload starts.
Existing objects with the same keys are overwritten; other objects are not deleted.

Resources with a nonempty `release_url` are **not downloaded or uploaded**,
even if a local copy exists. The workflow only attempts an HTTP HEAD request,
following redirects with HEAD, and logs the available content length, content
type, ETag, and last-modified time. If HEAD fails or is unsupported, it logs
the manifest metadata and continues publishing local files without falling
back to GET. HEAD metadata does not verify the remote file's SHA-256 checksum.
The remote flight case therefore remains available at its release URL and
does not receive an S3 `.science` object. README files and `manifest.json`
are not uploaded.

Run a local preview from the repository root (Python 3 and curl required):

```sh
AWS_TARGET_FOLDER=s3://my-bucket/usecases python3 scripts/publish.py --dry-run
```

This validates local files and queries remote headers without writing to S3.
The workflow uses the AWS CLI to upload; actual uploads stop on the first error,
and files already uploaded remain in S3. Run the focused tests with:

```sh
python3 -B -m unittest discover -s tests -v
```
