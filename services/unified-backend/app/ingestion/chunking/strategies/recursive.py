"""
RecursiveCharacterSplitter — Section 9 of the design document.

Strategy
--------
1. Attempt to split `text` using a priority-ordered list of separators
   ("\n\n", "\n", ". ", " ", "").  Work through separators from coarse to fine.
2. Recursively split any piece that still exceeds max_tokens.
3. Merge the resulting short segments into final Chunks of at most max_tokens
   with overlap_tokens of context carried over from one chunk to the next.

Token counting uses BertTokenizerFast loaded from the local model volume that
was already initialised by model-init.  No internet downloads are performed.

Environment variables
---------------------
CHUNK_MAX_TOKENS      Maximum tokens per chunk           (default: 400)
CHUNK_OVERLAP_TOKENS  Overlap tokens between chunks      (default: 50)
TOKENIZER_MODEL       Path to the tokenizer directory    (default: /models/embedding/int8)
MODEL_DEST            Base model directory               (default: /models)
"""

import os
import logging
from collections import deque
from typing import List, Optional, Tuple, Dict

from transformers import BertTokenizerFast

from .base import BaseChunkStrategy, Chunk

logger = logging.getLogger(__name__)

# Type alias: (segment_text, abs_char_start, abs_char_end)
_Segment = Tuple[str, int, int]
# Window entry: (segment_text, abs_char_start, abs_char_end, token_count)
_WindowEntry = Tuple[str, int, int, int]


class RecursiveCharacterSplitter(BaseChunkStrategy):
    """
    Recursively splits text using a priority-ordered separator list, then
    merges segments into overlapping chunks bounded by max_tokens.
    """

    DEFAULT_SEPARATORS: List[str] = ["\n\n", "\n", ". ", " ", ""]

    def __init__(self) -> None:
        self.max_tokens: int = int(os.getenv("CHUNK_MAX_TOKENS", "400"))
        self.overlap_tokens: int = int(os.getenv("CHUNK_OVERLAP_TOKENS", "50"))

        # Default to the model volume path set by model-init — no internet access.
        model_dest = os.getenv("MODEL_DEST", "/models")
        default_tokenizer = os.path.join(model_dest, "embedding", "int8")
        tokenizer_path = os.getenv("TOKENIZER_MODEL", default_tokenizer)

        self._tokenizer = BertTokenizerFast.from_pretrained(tokenizer_path)
        logger.info(
            f"RecursiveCharacterSplitter: tokenizer loaded "
            f"(path={tokenizer_path}, max_tokens={self.max_tokens}, "
            f"overlap={self.overlap_tokens})"
        )

        # Per-call token count cache — cleared at the start of each split() call
        # so memory doesn't accumulate across documents.
        self._token_cache: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public interface (BaseChunkStrategy)
    # ------------------------------------------------------------------

    def split(
        self,
        text: str,
        max_tokens: Optional[int] = None,
        overlap_tokens: Optional[int] = None,
        **kwargs,
    ) -> List[Chunk]:
        """
        Split `text` into a list of Chunk objects.

        Parameters
        ----------
        text:           Full document text (already cleaned by TextPreprocessor).
        max_tokens:     Per-call override for maximum tokens per chunk.
                        Falls back to CHUNK_MAX_TOKENS env var if not provided.
        overlap_tokens: Per-call override for overlap tokens.
                        Falls back to CHUNK_OVERLAP_TOKENS env var if not provided.

        Returns
        -------
        Ordered list of Chunk dataclasses with char_start / char_end pointing
        into the original `text` string.
        """
        eff_max = max_tokens if max_tokens is not None else self.max_tokens
        eff_overlap = overlap_tokens if overlap_tokens is not None else self.overlap_tokens

        if not text or not text.strip():
            return []

        # Clear cache at the start of each document to release memory.
        self._token_cache.clear()

        segments = self._recursive_split(
            text, self.DEFAULT_SEPARATORS, text_offset=0, max_tokens=eff_max
        )
        result = self._merge_into_chunks(segments, max_tokens=eff_max, overlap_tokens=eff_overlap)

        # Release cache memory after processing.
        self._token_cache.clear()
        return result

    # ------------------------------------------------------------------
    # Token counting
    # ------------------------------------------------------------------

    # Conservative upper bound: BERT WordPiece averages about 4 chars per token
    # for English text.  We use a tighter 3-char estimate so we only skip the
    # tokenizer call when the text is genuinely "obviously too long" (>3×512 chars
    # = >1536 chars), keeping false-positive skips minimal.
    _CHARS_PER_TOKEN_LOWER_BOUND = 3

    def _fits_in_max_tokens(self, text: str, max_tokens: int) -> bool:
        """
        Return True if `text` fits within max_tokens.

        Uses a cheap character-count pre-check to avoid calling the tokenizer
        on very long texts (which can cause memory spikes in the Rust backend
        for inputs far exceeding max_tokens).  Only falls through to the actual
        tokenizer when the text is short enough to plausibly fit.
        """
        if len(text) > max_tokens * self._CHARS_PER_TOKEN_LOWER_BOUND:
            return False

        cached = self._token_cache.get(text)
        if cached is not None:
            return cached <= max_tokens

        count = len(self._tokenizer.encode(text, add_special_tokens=False))
        self._token_cache[text] = count
        return count <= max_tokens

    def _count_tokens(self, text: str) -> int:
        """
        Count BERT sub-word tokens in `text` without special tokens ([CLS]/[SEP]).
        Results are cached per split() call to avoid redundant tokenizer calls
        on the same text fragment (which occurs 3-4x without caching).
        Thread-safe for concurrent read access to the tokenizer.
        """
        cached = self._token_cache.get(text)
        if cached is not None:
            return cached
        count = len(self._tokenizer.encode(text, add_special_tokens=False))
        self._token_cache[text] = count
        return count

    # ------------------------------------------------------------------
    # Recursive splitting
    # ------------------------------------------------------------------

    def _recursive_split(
        self, text: str, separators: List[str], text_offset: int, max_tokens: int
    ) -> List[_Segment]:
        """
        Recursively decompose `text` into segments that each fit within
        max_tokens.  Preserves absolute character offsets relative to the
        original full document text.

        Parameters
        ----------
        text:        Slice of text to split.
        separators:  Remaining separators to try (most coarse first).
        text_offset: Byte offset of text[0] in the original document string.
        max_tokens:  Effective token ceiling for this split call.

        Returns
        -------
        List of (segment_text, abs_char_start, abs_char_end) tuples.
        """
        if not text:
            return []

        # The text already fits — return it as a single segment.
        if self._fits_in_max_tokens(text, max_tokens):
            return [(text, text_offset, text_offset + len(text))]

        # No separators left — fall back to binary character halving.
        if not separators:
            return self._split_by_char_halving(text, text_offset, max_tokens)

        separator = separators[0]
        remaining = separators[1:]

        # Empty-string separator is the final fallback when all word/line
        # separators have been exhausted.
        if separator == "":
            return self._split_by_char_halving(text, text_offset, max_tokens)

        raw_parts = text.split(separator)
        result: List[_Segment] = []
        current_offset = text_offset

        for part in raw_parts:
            if part:
                part_end = current_offset + len(part)
                if self._fits_in_max_tokens(part, max_tokens):
                    result.append((part, current_offset, part_end))
                else:
                    # Recurse with a finer-grained separator.
                    sub_segments = self._recursive_split(part, remaining, current_offset, max_tokens)
                    result.extend(sub_segments)

            # Advance past the part content AND the separator character(s).
            current_offset += len(part) + len(separator)

        return result

    def _split_by_char_halving(
        self, text: str, text_offset: int, max_tokens: int
    ) -> List[_Segment]:
        """
        Last-resort split: recursively halve `text` at the midpoint until
        each half fits within max_tokens.  Guarantees termination because
        each call halves the input length (bottom out at a single character).
        """
        if not text:
            return []

        if self._fits_in_max_tokens(text, max_tokens):
            return [(text, text_offset, text_offset + len(text))]

        mid = len(text) // 2
        left = text[:mid]
        right = text[mid:]

        return self._split_by_char_halving(
            left, text_offset, max_tokens
        ) + self._split_by_char_halving(
            right, text_offset + mid, max_tokens
        )

    # ------------------------------------------------------------------
    # Merging segments into overlapping chunks
    # ------------------------------------------------------------------

    def _merge_into_chunks(
        self, segments: List[_Segment], max_tokens: int, overlap_tokens: int
    ) -> List[Chunk]:
        """
        Merge short segments into Chunks of at most max_tokens with a
        sliding-window overlap strategy.

        Algorithm
        ---------
        Maintain a `window` (deque of (text, start, end, token_count)) and a
        running `window_tokens` count.

        For each incoming segment:
          - If it fits in the window: append and continue.
          - If it would overflow: emit the current window as a Chunk, then
            pop segments from the *front* of the window until the remaining
            token count is <= overlap_tokens.  Do NOT advance the segment
            pointer so the overflowing segment is retried on the next
            iteration with the trimmed window.

        Token counts are stored inside the window entries so the trim loop
        does not need to re-call _count_tokens on already-counted segments.

        Character positions
        -------------------
        char_start = first segment's absolute start offset.
        char_end   = last  segment's absolute end  offset.
        These refer to the original document string, not the joined chunk_text.
        """
        if not segments:
            return []

        chunks: List[Chunk] = []
        chunk_index: int = 0

        # deque of (text, abs_start, abs_end, token_count)
        window: deque[_WindowEntry] = deque()
        window_tokens: int = 0

        i = 0
        while i < len(segments):
            seg_text, seg_start, seg_end = segments[i]
            seg_tokens = self._count_tokens(seg_text)

            if window and (window_tokens + seg_tokens > max_tokens):
                # ---- Emit current window ----------------------------------------
                chunk_text = " ".join(s[0] for s in window)
                char_start = window[0][1]
                char_end = window[-1][2]

                chunks.append(
                    Chunk(
                        chunk_index=chunk_index,
                        chunk_text=chunk_text,
                        char_start=char_start,
                        char_end=char_end,
                        token_count=window_tokens,
                    )
                )
                chunk_index += 1
                logger.debug(
                    f"Chunk {chunk_index - 1}: [{char_start}:{char_end}] "
                    f"{window_tokens} tokens"
                )

                # ---- Trim front of window to at most overlap_tokens -----------
                # Use stored token count from window entry — no extra _count_tokens call.
                while window and window_tokens > overlap_tokens:
                    removed = window.popleft()
                    window_tokens -= removed[3]
                    # Guard against token count going negative due to estimation
                    if window_tokens < 0:
                        window_tokens = 0

                # Do NOT advance i — the overflowing segment will be re-evaluated
                # in the next iteration with the trimmed window.

            else:
                # ---- Segment fits: add to window --------------------------------
                window.append((seg_text, seg_start, seg_end, seg_tokens))
                window_tokens += seg_tokens
                i += 1

        # ---- Emit the remaining window as the last chunk ----------------------
        if window:
            chunk_text = " ".join(s[0] for s in window)
            char_start = window[0][1]
            char_end = window[-1][2]

            chunks.append(
                Chunk(
                    chunk_index=chunk_index,
                    chunk_text=chunk_text,
                    char_start=char_start,
                    char_end=char_end,
                    token_count=window_tokens,
                )
            )
            logger.debug(
                f"Final chunk {chunk_index}: [{char_start}:{char_end}] "
                f"{window_tokens} tokens"
            )

        logger.info(
            f"RecursiveCharacterSplitter: {len(segments)} segments -> "
            f"{len(chunks)} chunks "
            f"(max_tokens={max_tokens}, overlap={overlap_tokens})"
        )
        return chunks

