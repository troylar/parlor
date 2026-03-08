"""Cross-encoder reranker: re-score retrieved chunks for relevance."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config import AppConfig
from .embeddings import EmbeddingPermanentError, EmbeddingTransientError

logger = logging.getLogger(__name__)


class LocalRerankerService:
    """Rerank chunks locally using fastembed TextCrossEncoder (ONNX, no external API)."""

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2", cache_dir: str = "") -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._cross_encoder: Any = None

    @property
    def model(self) -> str:
        return self._model_name

    def _ensure_model(self) -> Any:
        """Lazy-load the fastembed TextCrossEncoder on first use."""
        if self._cross_encoder is not None:
            return self._cross_encoder
        try:
            from fastembed import TextCrossEncoder
        except ImportError:
            raise EmbeddingPermanentError(
                "fastembed is not installed. Install it with: pip install anteroom[embeddings]"
            )
        logger.info("Loading cross-encoder model '%s' (first use may download)", self._model_name)
        try:
            kwargs: dict[str, Any] = {"model_name": self._model_name}
            if self._cache_dir:
                kwargs["cache_dir"] = self._cache_dir
                kwargs["local_files_only"] = True
            self._cross_encoder = TextCrossEncoder(**kwargs)
        except Exception as e:
            error_str = str(e).lower()
            if any(hint in error_str for hint in ("connection", "timeout", "resolve", "ssl", "network", "urlopen")):
                raise EmbeddingPermanentError(
                    f"Failed to download cross-encoder model '{self._model_name}'. "
                    f"If you're behind a firewall, download the model on a machine with internet access "
                    f"and copy ~/.cache/fastembed/ to this machine, or set reranker.cache_dir "
                    f"in config.yaml. Error: {e}"
                ) from e
            raise EmbeddingPermanentError(f"Failed to load cross-encoder model '{self._model_name}': {e}") from e
        logger.info("Cross-encoder model '%s' loaded", self._model_name)
        return self._cross_encoder

    async def rerank(self, query: str, documents: list[str], *, top_k: int | None = None) -> list[tuple[int, float]]:
        """Score query-document pairs and return sorted (index, score) tuples.

        Returns indices into the original documents list, sorted by relevance
        (highest score first). If top_k is provided, only the top-K results
        are returned.
        """
        if not documents:
            return []
        try:
            model = self._ensure_model()

            def _score() -> list[float]:
                return list(model.rerank(query, documents))

            raw_scores: list[float] = await asyncio.to_thread(_score)
            indexed = list(enumerate(raw_scores))
            indexed.sort(key=lambda x: x[1], reverse=True)
            if top_k is not None:
                indexed = indexed[:top_k]
            return indexed
        except EmbeddingPermanentError:
            raise
        except MemoryError as e:
            raise EmbeddingTransientError(f"Out of memory during reranking: {e}") from e
        except Exception as e:
            raise EmbeddingTransientError(f"Local reranking failed: {e}") from e

    async def probe(self, timeout: float = 10.0) -> bool:
        """Test whether the cross-encoder model is available."""
        try:
            result = await asyncio.wait_for(self.rerank("test query", ["test document"]), timeout=timeout)
            return len(result) > 0
        except Exception:
            logger.debug("Cross-encoder probe failed", exc_info=True)
            return False


def create_reranker_service(config: AppConfig) -> LocalRerankerService | None:
    """Factory: create a reranker service from app config. Returns None if disabled.

    When ``enabled`` is ``None`` (auto-detect), the service is still created so
    callers can probe it. When ``enabled`` is ``False``, returns ``None``.
    """
    if config.reranker.enabled is False:
        return None

    provider = config.reranker.provider
    if provider != "local":
        logger.warning("Reranker provider %r is not yet supported, disabling reranker", provider)
        return None

    return LocalRerankerService(model_name=config.reranker.model, cache_dir=config.reranker.cache_dir)
