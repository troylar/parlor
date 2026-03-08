"""RAG retrieval eval harness.

Measures retrieval quality against a curated dataset with ground-truth
annotations.  Uses real local embeddings (fastembed BAAI/bge-small-en-v1.5)
and the production ``retrieve_context()`` code path.

Metrics: recall@k, MRR, nDCG@k, empty-result rate.

Run:
    pytest evals/rag/ -v --tb=short
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any

import pytest

from anteroom.config import RagConfig
from anteroom.services.rag import RetrievedChunk, retrieve_context

# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _recall_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    """Fraction of relevant IDs found in the top-k retrieved results."""
    if not relevant_ids:
        return 1.0  # no relevant docs => perfect recall by convention
    top_k = set(retrieved_ids[:k])
    return len(top_k & set(relevant_ids)) / len(relevant_ids)


def _mrr(retrieved_ids: list[str], relevant_ids: list[str]) -> float:
    """Mean Reciprocal Rank — 1/rank of the first relevant result."""
    relevant_set = set(relevant_ids)
    for i, rid in enumerate(retrieved_ids, 1):
        if rid in relevant_set:
            return 1.0 / i
    return 0.0


def _dcg(relevances: list[float], k: int) -> float:
    """Discounted Cumulative Gain up to position k."""
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances[:k]))


def _ndcg_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    """Normalized Discounted Cumulative Gain at k."""
    if not relevant_ids:
        return 1.0
    relevant_set = set(relevant_ids)
    gains = [1.0 if rid in relevant_set else 0.0 for rid in retrieved_ids[:k]]
    ideal = sorted(gains, reverse=True)
    idcg = _dcg(ideal, k)
    if idcg == 0:
        return 0.0
    return _dcg(gains, k) / idcg


def _extract_ids(chunks: list[RetrievedChunk]) -> list[str]:
    """Extract the canonical ID from each retrieved chunk."""
    ids: list[str] = []
    for c in chunks:
        if c.source_type == "source_chunk" and c.chunk_id:
            ids.append(c.chunk_id)
        elif c.source_type == "message" and c.message_id:
            ids.append(c.message_id)
    return ids


# ---------------------------------------------------------------------------
# Permissive config — we want to measure ranking quality, not threshold tuning
# ---------------------------------------------------------------------------

_EVAL_CONFIG = RagConfig(
    enabled=True,
    max_chunks=20,
    max_tokens=50_000,
    similarity_threshold=2.0,  # very permissive
    include_sources=True,
    include_conversations=True,
    exclude_current=False,
)


# ---------------------------------------------------------------------------
# Per-query result container
# ---------------------------------------------------------------------------


@dataclass
class QueryResult:
    query_id: str
    category: str
    query_text: str
    relevant: list[str]
    retrieved_ids: list[str]
    k: int
    recall: float
    mrr: float
    ndcg: float


# ---------------------------------------------------------------------------
# Shared retrieval runner (session-scoped via indirect fixture)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def all_results(
    seeded_env: dict[str, Any],
    embedding_service: Any,
    dataset: dict[str, Any],
) -> list[QueryResult]:
    """Run every eval query through retrieve_context() once per session."""
    db = seeded_env["db"]
    vec_manager = seeded_env["vec_manager"]
    queries = dataset["queries"]

    loop = asyncio.new_event_loop()
    results: list[QueryResult] = []

    for q in queries:
        chunks = loop.run_until_complete(
            retrieve_context(
                query=q["query"],
                db=db,
                embedding_service=embedding_service,
                config=_EVAL_CONFIG,
                vec_manager=vec_manager,
            )
        )
        retrieved_ids = _extract_ids(chunks)
        k = q["k"]
        relevant = q["relevant"]

        results.append(
            QueryResult(
                query_id=q["id"],
                category=q["category"],
                query_text=q["query"],
                relevant=relevant,
                retrieved_ids=retrieved_ids,
                k=k,
                recall=_recall_at_k(retrieved_ids, relevant, k),
                mrr=_mrr(retrieved_ids, relevant),
                ndcg=_ndcg_at_k(retrieved_ids, relevant, k),
            )
        )

    loop.close()
    return results


# ---------------------------------------------------------------------------
# Helper to get a single result by query id
# ---------------------------------------------------------------------------


def _get(results: list[QueryResult], qid: str) -> QueryResult:
    for r in results:
        if r.query_id == qid:
            return r
    raise KeyError(qid)


# ---------------------------------------------------------------------------
# Parametrized per-query tests
# ---------------------------------------------------------------------------

# IDs of queries that have relevant docs (should retrieve at least one)
_POSITIVE_QUERY_IDS = [
    "q-type-hints",
    "q-test-framework",
    "q-code-formatting",
    "q-sql-injection",
    "q-password-hashing",
    "q-session-security",
    "q-docker-deploy",
    "q-kubernetes",
    "q-env-config",
    "q-chat-endpoint",
    "q-source-api",
    "q-search-api",
    "q-conversations-schema",
    "q-messages-schema",
    "q-db-migration",
    "q-rate-limit-config",
    "q-embedding-providers",
    "q-space-scoping",
    "q-rag-overview",
    "q-security-overview",
    "q-paraphrase-docker",
    "q-paraphrase-testing",
    "q-paraphrase-passwords",
    "q-vague-security",
    "q-vague-deployment",
]

_NEGATIVE_QUERY_IDS = [
    "q-neg-capital",
    "q-neg-recipe",
    "q-neg-weather",
]


# Queries where dedup keeps the user question but drops the assistant answer.
# These are real retrieval quality issues, not test bugs.
_KNOWN_DEDUP_VICTIMS = {"q-rate-limit-config", "q-embedding-providers", "q-space-scoping"}


@pytest.mark.parametrize("qid", _POSITIVE_QUERY_IDS)
def test_positive_query_recall(all_results: list[QueryResult], qid: str) -> None:
    """Every positive query must retrieve at least one relevant doc."""
    r = _get(all_results, qid)
    if qid in _KNOWN_DEDUP_VICTIMS:
        if r.recall == 0:
            pytest.xfail(
                f"[{r.query_id}] recall@{r.k}=0 (known dedup issue) — "
                f"retrieved {r.retrieved_ids[:5]}, expected any of {r.relevant}"
            )
    assert r.recall > 0, f"[{r.query_id}] recall@{r.k}=0 — retrieved {r.retrieved_ids}, expected any of {r.relevant}"


# ---------------------------------------------------------------------------
# Aggregate scoring test with formatted report
# ---------------------------------------------------------------------------

# Minimum thresholds — these are baselines, expected to rise with #810/#811
_MIN_OVERALL_RECALL = 0.60
_MIN_SOURCE_CHUNK_RECALL = 0.70
_MIN_MESSAGE_RECALL = 0.25  # low baseline; dedup collapses conversations, often keeps question not answer


def test_aggregate_scores(all_results: list[QueryResult]) -> None:
    """Print a formatted scorecard and assert minimum aggregate thresholds."""
    # ---- build category groups ----
    by_cat: dict[str, list[QueryResult]] = {}
    for r in all_results:
        by_cat.setdefault(r.category, []).append(r)

    positive = [r for r in all_results if r.relevant]
    negative = [r for r in all_results if not r.relevant]

    def _avg(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    # ---- per-query detail table ----
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 90)
    lines.append("  RAG Retrieval Eval — Scorecard")
    lines.append("=" * 90)
    lines.append("")
    lines.append(f"  {'Query ID':<30} {'Cat':<15} {'Recall@k':>9} {'MRR':>6} {'nDCG':>6}")
    lines.append(f"  {'-' * 30} {'-' * 15} {'-' * 9} {'-' * 6} {'-' * 6}")

    for r in all_results:
        lines.append(f"  {r.query_id:<30} {r.category:<15} {r.recall:>9.2f} {r.mrr:>6.2f} {r.ndcg:>6.2f}")

    # ---- category summaries ----
    lines.append("")
    lines.append("-" * 90)
    lines.append(f"  {'Category':<20} {'Count':>6} {'Avg Recall':>11} {'Avg MRR':>9} {'Avg nDCG':>9}")
    lines.append(f"  {'-' * 20} {'-' * 6} {'-' * 11} {'-' * 9} {'-' * 9}")

    for cat in ["source_chunks", "messages", "cross", "paraphrase", "vague", "negative"]:
        group = by_cat.get(cat, [])
        if not group:
            continue
        lines.append(
            f"  {cat:<20} {len(group):>6} "
            f"{_avg([r.recall for r in group]):>11.2f} "
            f"{_avg([r.mrr for r in group]):>9.2f} "
            f"{_avg([r.ndcg for r in group]):>9.2f}"
        )

    # ---- overall ----
    overall_recall = _avg([r.recall for r in positive])
    overall_mrr = _avg([r.mrr for r in positive])
    overall_ndcg = _avg([r.ndcg for r in positive])

    # Empty-result rate for negative queries: fraction that correctly returned nothing useful
    # (all retrieved IDs are irrelevant, which is always true for negative queries)
    empty_rate = sum(1 for r in negative if not r.retrieved_ids) / len(negative) if negative else 1.0

    lines.append("")
    lines.append("-" * 90)
    lines.append("  Overall (positive queries):")
    lines.append(f"    Recall@k:          {overall_recall:.3f}  (min: {_MIN_OVERALL_RECALL})")
    lines.append(f"    MRR:               {overall_mrr:.3f}")
    lines.append(f"    nDCG@k:            {overall_ndcg:.3f}")
    lines.append(f"    Negative empty %:  {empty_rate:.1%}  (3 queries)")
    lines.append("")

    # Source-chunk and message breakdowns
    src_recall = _avg([r.recall for r in by_cat.get("source_chunks", [])])
    msg_recall = _avg([r.recall for r in by_cat.get("messages", [])])
    lines.append(f"  Source-chunk recall:  {src_recall:.3f}  (min: {_MIN_SOURCE_CHUNK_RECALL})")
    lines.append(f"  Message recall:      {msg_recall:.3f}  (min: {_MIN_MESSAGE_RECALL})")
    lines.append("=" * 90)

    print("\n".join(lines))

    # ---- assertions ----
    assert overall_recall >= _MIN_OVERALL_RECALL, (
        f"Overall recall {overall_recall:.3f} below minimum {_MIN_OVERALL_RECALL}"
    )
    assert src_recall >= _MIN_SOURCE_CHUNK_RECALL, (
        f"Source-chunk recall {src_recall:.3f} below minimum {_MIN_SOURCE_CHUNK_RECALL}"
    )
    assert msg_recall >= _MIN_MESSAGE_RECALL, f"Message recall {msg_recall:.3f} below minimum {_MIN_MESSAGE_RECALL}"
