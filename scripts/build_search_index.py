"""Manually (re)index an existing BrewLine results file.

BrewLine.py's CLI already indexes automatically whenever it writes a results
file (see its `main()`). Use this script to reindex a file by hand instead —
e.g. after changing `chunk_results()` or `AZURE_SEARCH_INDEX_NAME`, or to
index a results file that wasn't generated via the CLI.

Usage:
    uv run python scripts/build_search_index.py [path/to/brewline_results.json]

Requires VOYAGE_API_KEY, AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_API_KEY
(all read from .env).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from brewline.search_index import AZURE_SEARCH_INDEX_NAME, CONFIGURED, index_results_file


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else str(REPO_ROOT / "data" / "brewline_results.json")

    if not CONFIGURED:
        print(
            "Azure AI Foundry / Azure AI Search aren't fully configured in .env "
            "(need AZURE_AI_FOUNDRY_ENDPOINT/API_KEY/EMBEDDING_MODEL and "
            "AZURE_SEARCH_ENDPOINT/API_KEY) — nothing to do."
        )
        return

    count = index_results_file(path)
    print(f"Indexed {count} chunks from {path} into '{AZURE_SEARCH_INDEX_NAME}'.")


if __name__ == "__main__":
    main()
