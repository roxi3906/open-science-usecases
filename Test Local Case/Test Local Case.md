# Test Local Case

Synthetic test data for case validation and the S3 publish workflow. This is a
transport fixture, not a completed research result.

| Sample | Input | Expected output (input × 2) |
| --- | ---: | ---: |
| alpha | 2 | 4 |
| beta | 5 | 10 |
| gamma | 9 | 18 |

The same-named `.science` file is a small gzip-compressed tar package containing
a synthetic session, empty record tables, and an internal checksum inventory.
Its messages contain the sample data above. Application import compatibility is
not part of this fixture's validation.

Expected behavior: all three local resources pass byte-size and SHA-256 checks
and map to `test-local-case/` beneath the configured S3 target. The manifest's
`case.release_url` is intentionally empty because the package exists locally.
