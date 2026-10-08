#!/usr/bin/env python3
"""Publish local resources and manifest to S3; inspect remote resources with HEAD only."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit


def publication_plan(root, target):
    destination = urlsplit(target)
    if (destination.scheme != "s3" or not destination.netloc
            or destination.query or destination.fragment):
        raise ValueError("AWS_TARGET_FOLDER must be an s3://bucket[/prefix] URI")
    target = target.rstrip("/")
    cases = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    uploads, remote, names = [], [], set()
    for case in cases:
        name = case["name"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or name in names:
            raise ValueError("Invalid or duplicate case name: " + name)
        names.add(name)
        for key, suffix in [("cover", ".png"), ("case", ".science"), ("introduction", ".md")]:
            if key == "introduction" and key not in case:
                continue
            resource = case[key]
            filename = resource["file_name"]
            if (Path(filename).name != filename or "\\" in filename
                    or not filename.endswith(suffix)):
                raise ValueError("Invalid resource filename: " + filename)
            relative = Path(resource["path"])
            path = (root / relative).resolve()
            if relative.is_absolute() or root not in path.parents:
                raise ValueError("Resource path must stay inside the repository: " + str(relative))
            # A local copy always wins and must pass validation before upload.
            if not path.exists() and resource.get("release_url"):
                remote.append((name, resource))
                continue
            if path.stat().st_size != resource["bytes"]:
                raise ValueError("Size mismatch: " + str(relative))
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != resource["sha256"]:
                raise ValueError("SHA-256 mismatch: " + str(relative))
            uploads.append((path, target + "/" + name + "/" + filename))
    # Publish the index only after every resource upload has succeeded.
    uploads.append((root / "manifest.json", target + "/manifest.json"))
    return uploads, remote


def inspect_remote(name, resource):
    result = subprocess.run(
        ["curl", "--head", "--location", "--fail", "--silent", "--show-error",
         "--connect-timeout", "10", "--max-time", "30", "--max-redirs", "5",
         "--proto", "=http,https", "--proto-redir", "=http,https",
         "--url", resource["release_url"]],
        capture_output=True, check=False,
    )
    info = {"name": name, "file_name": resource["file_name"],
            "manifest_bytes": resource["bytes"], "manifest_sha256": resource["sha256"]}
    if result.returncode:
        info["status"] = "HEAD unavailable; remote resource skipped"
        info["curl_exit_code"] = result.returncode
    else:
        headers = {}
        # HTTP field values can contain opaque non-UTF-8 bytes. Split the raw
        # header lines first so a valid 0x85 byte is not treated as a newline.
        for raw_line in result.stdout.splitlines():
            line = raw_line.decode("latin-1")
            if line.startswith("HTTP/"):
                headers = {}
            elif ":" in line:
                key, value = line.split(":", 1)
                if key.lower() in {"content-length", "content-type", "etag", "last-modified"}:
                    headers[key.lower()] = value.strip()
        info["status"] = "HEAD only; remote resource skipped"
        info["headers"] = headers
    print(json.dumps(info, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate local files and query remote HEAD metadata without uploading")
    args = parser.parse_args()
    uploads, remote = publication_plan(Path.cwd().resolve(), os.environ.get("AWS_TARGET_FOLDER", ""))
    for name, resource in remote:
        inspect_remote(name, resource)
    for path, destination in uploads:
        if args.dry_run:
            print("Would upload: " + destination)
        else:
            # Copy every file on every run, including same-size content changes.
            subprocess.run(["aws", "s3", "cp", str(path), destination, "--only-show-errors"], check=True)
    print("{} {} local files; skipped {} remote resources.".format(
        "Validated" if args.dry_run else "Uploaded", len(uploads), len(remote)))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print("Publish failed: " + str(error), file=sys.stderr)
        sys.exit(1)
