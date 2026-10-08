#!/usr/bin/env python3
"""Validate newly added case directories and their manifest entries."""

import argparse
import hashlib
from ipaddress import IPv6Address
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata
from urllib.parse import urlsplit


def git(*args):
    return subprocess.check_output(["git", *args], stderr=subprocess.PIPE).decode("utf-8")


def kebab_case(title):
    title = "".join(c for c in unicodedata.normalize("NFKD", title)
                    if not unicodedata.combining(c))
    title = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", title)
    title = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", title)
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def regular_file(path):
    return path.is_file() and not path.is_symlink()


def is_science_url(url):
    if not isinstance(url, str) or not url or re.search(r"[\s\x00-\x1f\x7f]", url):
        return False
    try:
        parsed = urlsplit(url)
        # Accessing port rejects malformed and out-of-range port numbers.
        _ = parsed.port
        # Python 3.9 does not validate bracketed hosts during URL splitting.
        if "[" in parsed.netloc:
            IPv6Address(parsed.hostname)
        # Check the asset path so download query parameters remain valid.
        return (parsed.scheme in ("http", "https") and bool(parsed.hostname)
                and parsed.path.endswith(".science"))
    except ValueError:
        return False


def directories(base, merge_base=False, new_branch_base=None):
    if re.fullmatch(r"0{40}|0{64}", base) and new_branch_base:
        base, merge_base = new_branch_base, True
    if re.fullmatch(r"0{40}|0{64}", base):
        # The initial push of the default branch has no previous tree.
        previous = set()
    else:
        base = git("rev-parse", "--verify", base + "^{commit}").strip()
        if merge_base:
            base = git("merge-base", base, "HEAD").strip()
        previous = set(git("ls-tree", "-d", "--name-only", "-z", base).split("\0"))
    current = set(git("ls-tree", "-d", "--name-only", "-z", "HEAD").split("\0"))
    cases, errors = [], []
    for directory in sorted(current - previous):
        if not directory or directory.startswith("."):
            continue
        folder = Path(directory)
        name = kebab_case(directory)
        problems = []
        if not name:
            problems.append("directory name must produce a nonempty ASCII kebab-case name")
        for extension in ("md", "png"):
            if not regular_file(folder / f"{directory}.{extension}"):
                problems.append(f"missing regular file {directory}.{extension}")
        local = regular_file(folder / f"{directory}.science")
        cases.append({
            "directory": directory, "name": name, "valid": not problems,
            "has_local_science": local,
        })
        errors.extend(f"{directory}: {problem}" for problem in problems)
    return cases, errors


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def require_keys(value, keys, label, errors):
    if not isinstance(value, dict):
        errors.append(f"{label}: must be an object")
        return False
    missing, extra = keys - value.keys(), value.keys() - keys
    if missing:
        errors.append(f"{label}: missing keys: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"{label}: unknown keys: {', '.join(sorted(extra))}")
    return True


def validate_resource(resource, case, key, extension, errors):
    directory = case["directory"]
    label = f"{directory}.{key}"
    keys = {"file_name", "path", "bytes", "sha256"}
    if key == "case":
        keys.add("release_url")
    if not require_keys(resource, keys, label, errors):
        return
    filename = f"{directory}.{extension}"
    expected_path = f"{directory}/{filename}"
    for field, expected in (("file_name", filename), ("path", expected_path)):
        if resource.get(field) != expected:
            errors.append(f"{label}.{field}: expected {expected!r}")
    size = resource.get("bytes")
    if type(size) is not int or size < 0:
        errors.append(f"{label}.bytes: must be a nonnegative integer")
    checksum = resource.get("sha256")
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        errors.append(f"{label}.sha256: must be 64 lowercase hexadecimal characters")
    if key == "case":
        release_url = resource.get("release_url")
        if case["has_local_science"]:
            if release_url != "":
                errors.append(f"{label}.release_url: must be empty for a local .science file")
        elif not is_science_url(release_url):
            errors.append(f"{label}.release_url: must be a nonempty HTTP(S) URL with a .science path")
    if key != "case" or case["has_local_science"]:
        path = Path(expected_path)
        if not regular_file(path):
            errors.append(f"{label}: missing regular file {expected_path!r}")
            return
        if size != path.stat().st_size:
            errors.append(f"{label}.bytes: does not match the local file")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if checksum != digest.hexdigest():
            errors.append(f"{label}.sha256: does not match the local file")


def validate_manifest(cases):
    if not isinstance(cases, list):
        raise ValueError("cases output must be an array")
    if not cases:
        return []
    manifest = json.loads(Path("manifest.json").read_text(encoding="utf-8"),
                          object_pairs_hook=unique_keys)
    if not isinstance(manifest, list) or any(not isinstance(entry, dict) for entry in manifest):
        raise ValueError("manifest.json must be an array of objects")
    errors = []
    names = [case["name"] for case in cases]
    for case in cases:
        directory, name = case["directory"], case["name"]
        if not case["valid"]:
            errors.append(f"{directory}: directory validation failed")
            continue
        if names.count(name) != 1:
            errors.append(f"{directory}: multiple new directories map to name {name!r}")
        entries = [entry for entry in manifest if entry.get("name") == name]
        if len(entries) != 1:
            errors.append(f"{directory}: expected exactly one manifest entry named {name!r}; found {len(entries)}")
            continue
        entry = entries[0]
        require_keys(entry, {"title", "name", "cover", "case", "introduction"}, directory, errors)
        if entry.get("title") != directory:
            errors.append(f"{directory}.title: must match the directory name")
        for key, extension in (("cover", "png"), ("case", "science"), ("introduction", "md")):
            validate_resource(entry.get(key), case, key, extension, errors)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="stage", required=True)
    scan = commands.add_parser("directories")
    scan.add_argument("--base", required=True, help="Git base commit; all-zero SHA means an empty tree")
    scan.add_argument("--merge-base", action="store_true", help="Compare to the common ancestor for a PR")
    scan.add_argument("--new-branch-base", help="Default branch ref for a new feature branch's first push")
    commands.add_parser("manifest", help="Read directory results from CASES_JSON")
    args = parser.parse_args()
    try:
        if args.stage == "directories":
            cases, errors = directories(args.base, args.merge_base, args.new_branch_base)
            payload = json.dumps(cases, ensure_ascii=True, separators=(",", ":"))
            output = os.environ.get("GITHUB_OUTPUT")
            if output:
                with open(output, "a", encoding="utf-8") as stream:
                    stream.write(f"cases={payload}\n")
            print(payload)
        else:
            cases = json.loads(os.environ["CASES_JSON"])
            errors = validate_manifest(cases)
        for error in errors:
            print("ERROR: " + error.replace("\r", "\\r").replace("\n", "\\n"), file=sys.stderr)
        if not errors:
            print(f"{args.stage}: checked {len(cases)} new case directories.", file=sys.stderr)
        return 1 if errors else 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print("ERROR: " + str(error).replace("\r", "\\r").replace("\n", "\\n"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
