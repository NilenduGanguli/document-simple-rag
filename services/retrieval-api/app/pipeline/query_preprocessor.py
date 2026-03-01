"""
Query preprocessor — normalisation and optional NER entity extraction.

NER model path: /models/ner/int8/model.onnx

In production deployments where the NER model is a domain-specific token
classifier, this module extracts entities and uses them to augment the query.

When the NER model is absent (model file not found) or when the model is a
generic BERT base without token-classification fine-tuning, the module returns
an empty entity list and passes the normalised query through unchanged.  This
allows the rest of the pipeline to run without a NER model present.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import List, Tuple

import numpy as np
from transformers import BertTokenizerFast

from rag_shared.onnx.session_pool import ONNXSessionPool
from rag_shared.metrics import onnx_inference_duration_ms
import time

logger = logging.getLogger(__name__)

# IOB2 label IDs that represent the beginning / inside of a named entity.
# These are the default BERT NER label ids for CoNLL-2003.
# Label 0 = O (outside), 1-8 vary per model.  We treat anything != O as entity.
_OUTSIDE_LABEL_ID = 0


class QueryPreprocessor:
    """
    Pre-processes a raw query string before it enters the retrieval pipeline.

    Steps:
      1. Normalise: strip, collapse whitespace, lowercase.
      2. NER (optional): if a token-classification ONNX model is available,
         extract entity spans and return them as a list of strings.
      3. Return (processed_query, entities).

    When the NER model is unavailable, entities is always [].
    """

    def __init__(
        self,
        session_pool: ONNXSessionPool | None,
        tokenizer_path: str,
        id2label: dict | None = None,
    ) -> None:
        self._pool = session_pool
        self._tokenizer: BertTokenizerFast | None = None
        self._id2label = id2label or {}

        if session_pool is not None:
            try:
                self._tokenizer = BertTokenizerFast.from_pretrained(tokenizer_path)
                logger.info(f"NER tokenizer loaded from {tokenizer_path}")
            except Exception as exc:
                logger.warning(f"Failed to load NER tokenizer: {exc} — NER disabled")
                self._pool = None

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    async def process(self, raw_query: str) -> Tuple[str, List[str]]:
        """
        Normalise the query and extract named entities.

        Returns:
            (processed_query, entities)
            processed_query: normalised query string (always a str)
            entities:        list of entity strings detected (may be empty)
        """
        normalised = _normalise(raw_query)

        if self._pool is None or self._tokenizer is None:
            return normalised, []

        try:
            entities = await self._run_ner(normalised)
        except Exception as exc:
            logger.warning(f"NER inference failed (non-fatal): {exc}")
            entities = []

        return normalised, entities

    # ──────────────────────────────────────────────────────────────────────────
    # Internal NER inference
    # ──────────────────────────────────────────────────────────────────────────

    async def _run_ner(self, query: str) -> List[str]:
        """
        Run token-classification ONNX inference on *query* and extract entity
        spans from the predicted labels.

        Returns list of deduplicated entity strings.  Returns [] if the model
        predicts all tokens as 'O' (outside), which happens when the model has
        not been fine-tuned for NER.
        """
        loop = asyncio.get_running_loop()

        encoded = self._tokenizer(
            query,
            return_tensors='np',
            return_offsets_mapping=True,
            truncation=True,
            max_length=128,
        )

        input_ids = encoded['input_ids'].astype(np.int64)
        attention_mask = encoded['attention_mask'].astype(np.int64)
        token_type_ids = encoded.get(
            'token_type_ids', np.zeros_like(input_ids)
        ).astype(np.int64)

        t0 = time.monotonic()
        async with self._pool.acquire() as (session, wait_ms):
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
        onnx_inference_duration_ms.labels(model_type='ner').observe(infer_ms)

        # logits: [1, seq_len, num_labels]
        logits = outputs[0]
        predictions = np.argmax(logits[0], axis=-1)  # [seq_len]

        # If every token is predicted as label 0 (O), no entities found
        if not np.any(predictions != _OUTSIDE_LABEL_ID):
            return []

        # Reconstruct entity spans from (offset_mapping, predictions, input_ids)
        tokens = self._tokenizer.convert_ids_to_tokens(input_ids[0].tolist())
        entities = _extract_entities(tokens, predictions.tolist(), self._id2label)
        return entities


# ──────────────────────────────────────────────────────────────────────────────
# Standalone helpers
# ──────────────────────────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """Strip, collapse internal whitespace, lowercase."""
    text = text.strip()
    text = re.sub(r'\s+', ' ', text)
    text = text.lower()
    return text


# Common English stopwords to filter from queries.
_ENGLISH_STOPWORDS: frozenset[str] = frozenset({
    'a', 'about', 'above', 'after', 'again', 'against', 'all', 'am', 'an',
    'and', 'any', 'are', "aren't", 'as', 'at', 'be', 'because', 'been',
    'before', 'being', 'below', 'between', 'both', 'but', 'by', "can't",
    'cannot', 'could', "couldn't", 'did', "didn't", 'do', 'does', "doesn't",
    'doing', "don't", 'down', 'during', 'each', 'few', 'for', 'from',
    'further', 'get', 'got', 'had', "hadn't", 'has', "hasn't", 'have',
    "haven't", 'having', 'he', "he'd", "he'll", "he's", 'her', 'here',
    "here's", 'hers', 'herself', 'him', 'himself', 'his', 'how', "how's",
    'i', "i'd", "i'll", "i'm", "i've", 'if', 'in', 'into', 'is', "isn't",
    'it', "it's", 'its', 'itself', "let's", 'me', 'more', 'most', "mustn't",
    'my', 'myself', 'no', 'nor', 'not', 'of', 'off', 'on', 'once', 'only',
    'or', 'other', 'ought', 'our', 'ours', 'ourselves', 'out', 'over', 'own',
    'same', "shan't", 'she', "she'd", "she'll", "she's", 'should',
    "shouldn't", 'so', 'some', 'such', 'than', 'that', "that's", 'the',
    'their', 'theirs', 'them', 'themselves', 'then', 'there', "there's",
    'these', 'they', "they'd", "they'll", "they're", "they've", 'this',
    'those', 'through', 'to', 'too', 'under', 'until', 'up', 'very', 'was',
    "wasn't", 'we', "we'd", "we'll", "we're", "we've", 'were', "weren't",
    'what', "what's", 'when', "when's", 'where', "where's", 'which', 'while',
    'who', "who's", 'whom', 'why', "why's", 'with', "won't", 'would',
    "wouldn't", 'you', "you'd", "you'll", "you're", "you've", 'your',
    'yours', 'yourself', 'yourselves',
})


def _remove_stopwords(text: str) -> str:
    """
    Remove common English stopwords from *text*.

    Words are split on whitespace and filtered against _ENGLISH_STOPWORDS.
    If all words are stopwords the original text is returned unchanged to
    avoid producing an empty query.
    """
    words = text.split()
    filtered = [w for w in words if w not in _ENGLISH_STOPWORDS]
    if not filtered:
        return text
    return ' '.join(filtered)


def _extract_entities(
    tokens: List[str],
    label_ids: List[int],
    id2label: dict,
) -> List[str]:
    """
    Reconstruct entity surface forms from BIO-tagged BERT subword tokens.

    Merges consecutive entity tokens (B-* / I-*), strips ## subword markers,
    and returns deduplicated entity strings.
    """
    entities: List[str] = []
    current_tokens: List[str] = []

    for token, lid in zip(tokens, label_ids):
        if token in ('[CLS]', '[SEP]', '[PAD]'):
            if current_tokens:
                entities.append(_merge_tokens(current_tokens))
                current_tokens = []
            continue

        label = id2label.get(lid, 'O') if id2label else ('O' if lid == 0 else 'ENT')

        if label == 'O':
            if current_tokens:
                entities.append(_merge_tokens(current_tokens))
                current_tokens = []
        elif label.startswith('B-') or label == 'ENT':
            if current_tokens:
                entities.append(_merge_tokens(current_tokens))
            current_tokens = [token]
        else:  # I- continuation
            current_tokens.append(token)

    if current_tokens:
        entities.append(_merge_tokens(current_tokens))

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: List[str] = []
    for e in entities:
        if e and e not in seen:
            seen.add(e)
            unique.append(e)
    return unique


def _merge_tokens(tokens: List[str]) -> str:
    """Merge BERT subword tokens into a surface form string."""
    text = ''
    for t in tokens:
        if t.startswith('##'):
            text += t[2:]
        else:
            text += (' ' if text else '') + t
    return text.strip()


# ──────────────────────────────────────────────────────────────────────────────
# Factory helper
# ──────────────────────────────────────────────────────────────────────────────

def build_query_preprocessor(
    model_base: Path,
    tokenizer_path: str,
) -> QueryPreprocessor:
    """
    Build a QueryPreprocessor, optionally with an NER ONNXSessionPool.
    Returns a preprocessor with session_pool=None if the NER model is absent.
    """
    onnx_path = model_base / 'ner' / 'int8' / 'model.onnx'
    if not onnx_path.exists():
        logger.info(
            f"NER model not found at {onnx_path}. "
            "Query preprocessing will normalise only (no NER)."
        )
        return QueryPreprocessor(session_pool=None, tokenizer_path=tokenizer_path)

    session_pool = ONNXSessionPool.from_env(str(onnx_path))
    return QueryPreprocessor(
        session_pool=session_pool,
        tokenizer_path=tokenizer_path,
    )
