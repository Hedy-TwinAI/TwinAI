"""Shared Azure AI Search indexing for BrewLine simulation results.

Chunks a BrewLine results dict (run config, per-KPI summary stats, per-
barista utilization, and the Little's-law validation check), embeds them
with Voyage AI, and upserts them into the vector index that server/main.py's
`/api/assistant` retrieves from.

Used both by scripts/build_search_index.py (manual reindex of an existing
file) and BrewLine.py's CLI (automatic reindex whenever a results file is
generated). Degrades to a no-op if Voyage AI / Azure AI Search aren't
configured, so `python brewline/BrewLine.py` keeps working standalone
without either.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
VOYAGE_MODEL = os.environ.get("VOYAGE_MODEL", "voyage-3")

AZURE_SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT", "")
AZURE_SEARCH_API_KEY = os.environ.get("AZURE_SEARCH_API_KEY", "")
AZURE_SEARCH_INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX_NAME", "brewline-docs")
VECTOR_DIMENSIONS = int(os.environ.get("AZURE_SEARCH_VECTOR_DIMENSIONS", 1024))

VECTOR_PROFILE_NAME = "brewline-vector-profile"
VECTOR_ALGORITHM_NAME = "brewline-hnsw"

CONFIGURED = bool(VOYAGE_API_KEY and AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY)


def _format_stats(title: str, stats: dict) -> str:
    return (
        f"{title}: mean={stats['mean']}, 95% CI=[{stats['ci95_low']}, {stats['ci95_high']}], "
        f"stdev={stats['stdev']}, min={stats['min']}, max={stats['max']}"
    )


def chunk_results(results: dict) -> list[tuple[str, str]]:
    """Split a BrewLine results dict into (title, content) RAG chunks."""
    cfg = results["config"]
    chunks = [
        (
            "Run config",
            f"arrival_rate={cfg['arrival_rate_per_min']}/min, "
            f"num_baristas={cfg['num_baristas']}, "
            f"mean_service_time={cfg['mean_service_time_min']} min, "
            f"horizon={cfg['horizon_min']} min, "
            f"replications={cfg['replications']}, seed={cfg['master_seed']}, "
            f"offered_load_rho={cfg['offered_load_rho']} "
            f"({'stable' if cfg['stable'] else 'UNSTABLE, rho >= 1'})",
        ),
    ]

    for kpi, stats in results.get("summary", {}).items():
        chunks.append((f"KPI: {kpi}", _format_stats(kpi, stats)))

    resource_kpis = results.get("resource_kpis")
    if resource_kpis:
        per_barista = ", ".join(
            f"barista {i}: mean={s['mean']}"
            for i, s in enumerate(resource_kpis.get("utilization_by_barista", []))
        )
        if per_barista:
            chunks.append(("Per-barista utilization", per_barista))

    validation = results.get("validation")
    if validation:
        chunks.append((
            "Validation (Little's law)",
            f"max abs error={validation.get('littles_law_max_abs_error')}. "
            f"{validation.get('note', '')}".strip(),
        ))

    return chunks


def _make_id(title: str) -> str:
    return hashlib.sha1(title.encode("utf-8")).hexdigest()


def _ensure_index(index_client) -> None:
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchField,
        SearchFieldDataType,
        SearchableField,
        SearchIndex,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    index = SearchIndex(
        name=AZURE_SEARCH_INDEX_NAME,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SearchableField(name="title", type=SearchFieldDataType.String),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SearchField(
                name="contentVector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=VECTOR_DIMENSIONS,
                vector_search_profile_name=VECTOR_PROFILE_NAME,
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name=VECTOR_ALGORITHM_NAME)],
            profiles=[
                VectorSearchProfile(
                    name=VECTOR_PROFILE_NAME,
                    algorithm_configuration_name=VECTOR_ALGORITHM_NAME,
                ),
            ],
        ),
    )
    index_client.create_or_update_index(index)


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed document-side text with Voyage AI (asymmetric retrieval)."""
    import voyageai

    client = voyageai.Client(api_key=VOYAGE_API_KEY)
    return client.embed(texts, model=VOYAGE_MODEL, input_type="document").embeddings


def embed_query(text: str) -> list[float]:
    """Embed a query string with Voyage AI (asymmetric retrieval)."""
    import voyageai

    client = voyageai.Client(api_key=VOYAGE_API_KEY)
    return client.embed([text], model=VOYAGE_MODEL, input_type="query").embeddings[0]


def index_results(results: dict) -> int:
    """Chunk, embed, and upsert `results` into the search index.

    Returns the number of chunks indexed, or 0 if Voyage AI / Azure AI
    Search aren't configured (a no-op, not an error).
    """
    if not CONFIGURED:
        return 0

    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

    chunks = chunk_results(results)

    index_client = SearchIndexClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        credential=AzureKeyCredential(AZURE_SEARCH_API_KEY),
    )
    search_client = SearchClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        index_name=AZURE_SEARCH_INDEX_NAME,
        credential=AzureKeyCredential(AZURE_SEARCH_API_KEY),
    )

    _ensure_index(index_client)

    vectors = embed_documents([content for _, content in chunks])

    documents = [
        {
            "id": _make_id(title),
            "title": title,
            "content": content,
            "contentVector": vector,
        }
        for (title, content), vector in zip(chunks, vectors)
    ]

    search_client.upload_documents(documents)
    return len(documents)


def index_results_file(path: str) -> int:
    results = json.loads(Path(path).read_text())
    return index_results(results)
