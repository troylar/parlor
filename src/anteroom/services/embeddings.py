"""Embedding service: generate vector embeddings via OpenAI-compatible API or locally via fastembed."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    InternalServerError,
    NotFoundError,
    RateLimitError,
    UnprocessableEntityError,
)

from ..config import AppConfig
from .token_provider import TokenProvider, TokenProviderError

logger = logging.getLogger(__name__)

MAX_INPUT_TOKENS = 8191

_LOCAL_MODEL_DIMENSIONS: dict[str, int] = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "nomic-ai/nomic-embed-text-v1.5": 768,
}

_PERMANENT_ERRORS = (NotFoundError, UnprocessableEntityError)
_TRANSIENT_ERRORS = (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)


class EmbeddingPermanentError(Exception):
    """Raised when the embedding API returns a non-recoverable error (e.g., 404 model not found)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class EmbeddingTransientError(Exception):
    """Raised when the embedding API returns a recoverable error (e.g., 429 rate limit)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class EmbeddingService:
    def __init__(self, client: AsyncOpenAI, model: str = "text-embedding-3-small", dimensions: int = 1536) -> None:
        self._client = client
        self._model = model
        self._dimensions = dimensions
        self._token_provider: TokenProvider | None = None

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _set_token_provider(self, provider: TokenProvider) -> None:
        self._token_provider = provider

    def _try_refresh_token(self) -> bool:
        if not self._token_provider:
            return False
        try:
            self._token_provider.refresh()
            new_key = self._token_provider.get_token()
            self._client = AsyncOpenAI(base_url=str(self._client.base_url), api_key=new_key)
            logger.info("Embedding service token refreshed")
            return True
        except TokenProviderError:
            logger.exception("Embedding token refresh failed")
            return False

    async def embed(self, text: str, *, _auth_retried: bool = False) -> list[float] | None:
        """Generate an embedding for a single text.

        Returns the embedding vector on success, None on empty input.
        Raises EmbeddingPermanentError for non-recoverable API errors.
        Raises EmbeddingTransientError for recoverable API errors.
        """
        if not text or not text.strip():
            return None
        truncated = text[: MAX_INPUT_TOKENS * 4]
        try:
            response = await self._client.embeddings.create(
                model=self._model,
                input=truncated,
                dimensions=self._dimensions,
            )
            return response.data[0].embedding
        except AuthenticationError:
            if not _auth_retried and self._try_refresh_token():
                return await self.embed(text, _auth_retried=True)
            raise EmbeddingPermanentError("Authentication failed", status_code=401)
        except _PERMANENT_ERRORS as e:
            status = getattr(e, "status_code", None)
            logger.error("Permanent embedding error: %s (status=%s)", type(e).__name__, status)
            raise EmbeddingPermanentError(str(e), status_code=status) from e
        except _TRANSIENT_ERRORS as e:
            status = getattr(e, "status_code", None)
            logger.warning("Transient embedding error: %s (status=%s)", type(e).__name__, status)
            raise EmbeddingTransientError(str(e), status_code=status) from e

    async def embed_batch(self, texts: list[str], batch_size: int = 100) -> list[list[float] | None]:
        """Generate embeddings for a batch of texts.

        Returns list of embedding vectors (or None per-item on partial failure).
        Raises EmbeddingPermanentError for non-recoverable API errors.
        Raises EmbeddingTransientError for recoverable API errors.
        """
        results: list[list[float] | None] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            truncated = [t[: MAX_INPUT_TOKENS * 4] for t in batch]
            try:
                response = await self._client.embeddings.create(
                    model=self._model,
                    input=truncated,
                    dimensions=self._dimensions,
                )
                batch_results: list[list[float] | None] = [None] * len(batch)
                for item in response.data:
                    batch_results[item.index] = item.embedding
                results.extend(batch_results)
            except AuthenticationError:
                if self._try_refresh_token():
                    try:
                        retry_response = await self._client.embeddings.create(
                            model=self._model,
                            input=truncated,
                            dimensions=self._dimensions,
                        )
                    except AuthenticationError:
                        raise EmbeddingPermanentError("Batch authentication failed after refresh", status_code=401)
                    batch_results = [None] * len(batch)
                    for item in retry_response.data:
                        batch_results[item.index] = item.embedding
                    results.extend(batch_results)
                else:
                    raise EmbeddingPermanentError("Batch authentication failed", status_code=401)
            except _PERMANENT_ERRORS as e:
                status = getattr(e, "status_code", None)
                logger.error("Permanent embedding error: %s (status=%s)", type(e).__name__, status)
                raise EmbeddingPermanentError(str(e), status_code=status) from e
            except _TRANSIENT_ERRORS as e:
                status = getattr(e, "status_code", None)
                logger.warning("Transient embedding error: %s (status=%s)", type(e).__name__, status)
                raise EmbeddingTransientError(str(e), status_code=status) from e
        return results


def get_local_model_dimensions(model_name: str) -> int:
    """Return the known output dimensions for a local embedding model, or 384 as default."""
    return _LOCAL_MODEL_DIMENSIONS.get(model_name, 384)


def get_effective_dimensions(config: AppConfig) -> int:
    """Resolve the effective embedding dimensions from config (for DB schema setup)."""
    if config.embeddings.dimensions > 0:
        return config.embeddings.dimensions
    if config.embeddings.provider == "local":
        return get_local_model_dimensions(config.embeddings.local_model)
    return 1536  # OpenAI default


class LocalEmbeddingService:
    """Generate embeddings locally using fastembed (ONNX Runtime, no external API)."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", dimensions: int = 0) -> None:
        self._model_name = model_name
        self._dimensions = dimensions if dimensions > 0 else get_local_model_dimensions(model_name)
        self._embedding_model: Any = None

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _ensure_model(self) -> Any:
        """Lazy-load the fastembed TextEmbedding model on first use."""
        if self._embedding_model is not None:
            return self._embedding_model
        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise EmbeddingPermanentError(
                "fastembed is not installed. Install it with: pip install anteroom[embeddings]"
            )
        logger.info("Loading local embedding model '%s' (first use may download ~50MB)", self._model_name)
        try:
            self._embedding_model = TextEmbedding(model_name=self._model_name)
        except Exception as e:
            error_str = str(e).lower()
            if any(hint in error_str for hint in ("connection", "timeout", "resolve", "ssl", "network", "urlopen")):
                raise EmbeddingPermanentError(
                    f"Failed to download embedding model '{self._model_name}'. "
                    f"If you're behind a firewall, download the model on a machine with internet access "
                    f"and copy ~/.cache/fastembed/ to this machine. Error: {e}"
                ) from e
            raise EmbeddingPermanentError(f"Failed to load local embedding model '{self._model_name}': {e}") from e
        logger.info("Local embedding model '%s' loaded (%d dimensions)", self._model_name, self._dimensions)
        return self._embedding_model

    async def embed(self, text: str) -> list[float] | None:
        """Generate an embedding for a single text using the local model."""
        if not text or not text.strip():
            return None
        truncated = text[: MAX_INPUT_TOKENS * 4]
        try:
            model = self._ensure_model()
            embeddings = await asyncio.to_thread(lambda: list(model.embed([truncated])))
            return embeddings[0].tolist()
        except EmbeddingPermanentError:
            raise
        except MemoryError as e:
            raise EmbeddingTransientError(f"Out of memory during embedding: {e}") from e
        except Exception as e:
            raise EmbeddingTransientError(f"Local embedding failed: {e}") from e

    async def embed_batch(self, texts: list[str], batch_size: int = 100) -> list[list[float] | None]:
        """Generate embeddings for a batch of texts using the local model."""
        results: list[list[float] | None] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            non_empty = [(j, t[: MAX_INPUT_TOKENS * 4]) for j, t in enumerate(batch) if t and t.strip()]
            batch_results: list[list[float] | None] = [None] * len(batch)
            if not non_empty:
                results.extend(batch_results)
                continue
            try:
                model = self._ensure_model()
                indices, clean_texts = zip(*non_empty)
                embeddings = await asyncio.to_thread(lambda ct=list(clean_texts): list(model.embed(ct)))
                for idx, emb in zip(indices, embeddings):
                    batch_results[idx] = emb.tolist()
            except EmbeddingPermanentError:
                raise
            except MemoryError as e:
                raise EmbeddingTransientError(f"Out of memory during batch embedding: {e}") from e
            except Exception as e:
                raise EmbeddingTransientError(f"Local batch embedding failed: {e}") from e
            results.extend(batch_results)
        return results


_VALID_PROVIDERS = {"local", "api"}


def create_embedding_service(config: AppConfig) -> EmbeddingService | LocalEmbeddingService | None:
    """Factory: create an embedding service from app config. Returns None if unavailable."""
    if not config.embeddings.enabled:
        return None

    provider = config.embeddings.provider
    if provider not in _VALID_PROVIDERS:
        logger.error(
            "Invalid embedding provider %r (must be one of %s), disabling embeddings",
            provider,
            _VALID_PROVIDERS,
        )
        return None

    if provider == "local":
        dims = config.embeddings.dimensions
        if dims == 0:
            dims = get_local_model_dimensions(config.embeddings.local_model)
        return LocalEmbeddingService(
            model_name=config.embeddings.local_model,
            dimensions=dims,
        )

    # API-based provider
    base_url = config.embeddings.base_url or config.ai.base_url
    api_key = config.embeddings.api_key or config.ai.api_key
    api_key_command = config.embeddings.api_key_command or config.ai.api_key_command

    if not api_key and not api_key_command:
        return None

    provider: TokenProvider | None = None
    if api_key_command:
        provider = TokenProvider(api_key_command)
        api_key = provider.get_token()

    kwargs: dict[str, Any] = {
        "base_url": base_url,
        "api_key": api_key,
    }
    if not config.ai.verify_ssl:
        kwargs["http_client"] = httpx.AsyncClient(verify=False)  # noqa: S501

    dims = config.embeddings.dimensions if config.embeddings.dimensions > 0 else 1536
    client = AsyncOpenAI(**kwargs)
    service = EmbeddingService(
        client=client,
        model=config.embeddings.model,
        dimensions=dims,
    )
    if provider:
        service._set_token_provider(provider)
    return service
