"""
BERT cross-encoder reranker using an INT8 ONNX session pool.

Input format: "[CLS] query [SEP] candidate_text [SEP]"

The model outputs either:
  - 2 logits  → softmax → take index-1 as relevance score
  - 1 logit   → sigmoid → relevance score ∈ (0, 1)

All candidates are batched into a single forward pass for efficiency.

Model path: /models/crossencoder/int8/model.onnx
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import numpy as np
from transformers import BertTokenizerFast

from rag_shared.onnx.session_pool import ONNXSessionPool
from rag_shared.metrics import onnx_inference_duration_ms, onnx_pool_wait_ms

logger = logging.getLogger(__name__)


class CrossEncoderReranker:
    """
    Batch BERT cross-encoder reranker backed by an ONNXSessionPool.

    Attributes:
        session_pool: Shared pool of ONNX Runtime sessions.
        tokenizer:    HuggingFace fast tokenizer for [CLS] q [SEP] c [SEP].
        max_length:   Maximum token length for cross-encoder inputs.
    """

    def __init__(
        self,
        session_pool: ONNXSessionPool,
        tokenizer_path: str,
        max_length: int = 512,
    ) -> None:
        self.session_pool = session_pool
        self.tokenizer = BertTokenizerFast.from_pretrained(tokenizer_path)
        self.max_length = max_length
        logger.info(
            f"CrossEncoderReranker ready: tokenizer={tokenizer_path}, "
            f"max_length={max_length}"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    async def rerank(
        self,
        query: str,
        candidates: list[dict],
    ) -> list[dict]:
        """
        Re-score *candidates* using the cross-encoder and return them sorted
        by rerank_score (descending).

        Args:
            query:      User query string.
            candidates: List of result dicts.  Must have 'chunk_text'.

        Returns:
            Same dicts annotated with 'rerank_score' ∈ (0, 1), sorted desc.
        """
        if not candidates:
            return []

        loop = asyncio.get_running_loop()
        texts = [c.get('chunk_text', '') for c in candidates]

        # ---------------------------------------------------------------------------
        # 1. Tokenise as cross-encoder pairs: "[CLS] query [SEP] text [SEP]"
        # ---------------------------------------------------------------------------
        queries = [query] * len(texts)
        encoded = self.tokenizer(
            queries,
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors='np',
        )

        input_ids = encoded['input_ids'].astype(np.int64)
        attention_mask = encoded['attention_mask'].astype(np.int64)
        token_type_ids = encoded.get(
            'token_type_ids', np.zeros_like(input_ids)
        ).astype(np.int64)

        # ---------------------------------------------------------------------------
        # 2. ONNX inference
        # ---------------------------------------------------------------------------
        t0 = time.monotonic()
        async with self.session_pool.acquire() as (session, wait_ms):
            onnx_pool_wait_ms.labels(model_type='crossencoder').observe(wait_ms)
            outputs = await loop.run_in_executor(
                None,
                lambda: session.run(
                    None,
                    {
                        'input_ids': input_ids,
                        'attention_mask': attention_mask,
                        'token_type_ids': token_type_ids,
                    },
                ),
            )

        infer_ms = (time.monotonic() - t0) * 1000
        onnx_inference_duration_ms.labels(model_type='crossencoder').observe(infer_ms)
        logger.debug(
            f"Cross-encoder inference: {len(texts)} candidates in {infer_ms:.1f}ms"
        )

        # ---------------------------------------------------------------------------
        # 3. Convert logits to relevance scores
        # ---------------------------------------------------------------------------
        # outputs[0] shape: [batch, 1] (regression) or [batch, 2] (classification)
        # or [batch, seq_len, hidden] (raw last_hidden_state — no classification head)
        logits = outputs[0]  # np.ndarray

        if logits.ndim == 1:
            # Scalar per sample
            rerank_scores = _sigmoid(logits).tolist()
        elif logits.ndim >= 3:
            # last_hidden_state [batch, seq_len, hidden] — model has no classification head.
            # Use L2 norm of the [CLS] token embedding as a relevance proxy,
            # normalized to [0, 1] across the candidate set.
            cls_emb = logits[:, 0, :]  # (batch, hidden_size)
            norms = np.linalg.norm(cls_emb, axis=-1).astype(np.float32)  # (batch,)
            max_norm = float(norms.max()) if norms.max() > 0 else 1.0
            rerank_scores = (norms / max_norm).tolist()
        elif logits.shape[-1] == 1:
            # Single regression logit → sigmoid
            rerank_scores = _sigmoid(logits[:, 0]).tolist()
        elif logits.shape[-1] == 2:
            # Binary classification → softmax → take positive class probability
            probs = _softmax(logits)
            rerank_scores = probs[:, 1].tolist()
        else:
            # Multi-class: flatten to (batch,) and sigmoid first column
            reduced = logits.reshape(logits.shape[0], -1)[:, 0]
            rerank_scores = _sigmoid(reduced).tolist()

        # ---------------------------------------------------------------------------
        # 4. Annotate and sort
        # ---------------------------------------------------------------------------
        annotated = []
        for i, candidate in enumerate(candidates):
            entry = dict(candidate)
            entry['rerank_score'] = float(rerank_scores[i])
            annotated.append(entry)

        annotated.sort(key=lambda x: x['rerank_score'], reverse=True)
        return annotated


# ──────────────────────────────────────────────────────────────────────────────
# Numeric helpers
# ──────────────────────────────────────────────────────────────────────────────

def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x.astype(np.float32)))


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x.astype(np.float32) - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


# ──────────────────────────────────────────────────────────────────────────────
# Factory helper
# ──────────────────────────────────────────────────────────────────────────────

def build_reranker(
    model_base: Path,
    tokenizer_path: str,
) -> CrossEncoderReranker | None:
    """
    Build a CrossEncoderReranker if the cross-encoder model exists.
    Returns None if the model file is absent (disables reranking gracefully).
    """
    onnx_path = model_base / 'crossencoder' / 'int8' / 'model.onnx'
    if not onnx_path.exists():
        logger.warning(
            f"Cross-encoder model not found at {onnx_path}. "
            "Reranking will be disabled."
        )
        return None

    session_pool = ONNXSessionPool.from_env(str(onnx_path))
    return CrossEncoderReranker(
        session_pool=session_pool,
        tokenizer_path=tokenizer_path,
    )
