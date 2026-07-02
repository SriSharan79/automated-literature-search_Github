#!/usr/bin/env python
"""
Generate a requirements.txt that lists every package
installed in the current virtual environment.

Compatible with Python 3.8+ (uses importlib.metadata).
For older versions, fall back to pkg_resources.

Usage:
    python generate_requirements.py  # writes requirements.txt in cwd
"""

import sys
import pathlib
import json

# ------------- helpers -----------------------------------------------------

def get_distributions():
    """
    Return an iterable of (name, version) tuples for all installed packages.
    Tries importlib.metadata (Python 3.8+) first; falls back to pkg_resources.
    """
    try:
        # Python 3.8+ – the recommended API
        from importlib.metadata import distributions
        return ((dist.metadata["Name"], dist.version) for dist in distributions())
    except Exception:
        # Older Python – use pkg_resources
        try:
            import pkg_resources
            return ((dist.project_name, dist.version) for dist in pkg_resources.working_set)
        except Exception as e:
            print("ERROR: cannot determine installed distributions", file=sys.stderr)
            print(f"       {e!r}", file=sys.stderr)
            sys.exit(1)

def write_requirements(distributions, path):
    """
    Write each distribution as `name==version` to *path*.
    """
    # Sort alphabetically for readability
    sorted_distributions = sorted(distributions, key=lambda x: x[0].lower())

    with path.open("w", encoding="utf-8") as f:
        for name, version in sorted_distributions:
            # Skip packages that are part of the standard library
            # (they usually don't appear in importlib.metadata, but just in case)
            if name == "pip":
                continue
            f.write(f"{name}=={version}\n")
    print(f"✔  Wrote {len(sorted_distributions)} requirements to {path}")

# ------------- main --------------------------------------------------------

def main():
    # Where to write the file – same directory as the script
    req_file = pathlib.Path.cwd() / "requirements.txt"

    distributions = list(get_distributions())
    write_requirements(distributions, req_file)

if __name__ == "__main__":
    main()