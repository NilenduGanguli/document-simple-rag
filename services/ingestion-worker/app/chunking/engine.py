import os
from typing import List, Optional
from .strategies.base import Chunk
from .strategies.recursive import RecursiveCharacterSplitter
import logging

logger = logging.getLogger(__name__)


class ChunkingEngine:
    """
    Strategy-pattern registry for chunking strategies.
    Active strategy: RecursiveCharacterSplitter (default).
    """

    def __init__(self):
        self._strategies = {
            'recursive': RecursiveCharacterSplitter(),
        }
        self._default_strategy = os.getenv('CHUNKING_STRATEGY', 'recursive')

    def chunk(
        self,
        text: str,
        document_id: str,
        routing_result=None,
        strategy_name: Optional[str] = None,
        max_tokens: Optional[int] = None,
        overlap_tokens: Optional[int] = None,
    ) -> List[dict]:
        """
        Split text into chunks using the configured strategy.

        Parameters
        ----------
        text:          Cleaned document text.
        document_id:   Used for logging.
        routing_result: Routing metadata (unused by the splitter directly).
        strategy_name: Override strategy for this call (falls back to env default).
        max_tokens:    Per-call max tokens override (falls back to env default).
        overlap_tokens: Per-call overlap override (falls back to env default).

        Returns list of dicts ready for DB insertion.
        """
        effective_strategy = strategy_name or self._default_strategy
        strategy = self._strategies.get(effective_strategy)
        if not strategy:
            logger.warning(
                f"Unknown chunking strategy '{effective_strategy}', falling back to 'recursive'"
            )
            strategy = self._strategies['recursive']

        raw_chunks = strategy.split(
            text,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
        )

        # Convert to DB-ready dicts
        result = []
        for chunk in raw_chunks:
            result.append({
                'chunk_index': chunk.chunk_index,
                'chunk_text': chunk.chunk_text,
                'char_start': chunk.char_start,
                'char_end': chunk.char_end,
                'source_type': chunk.source_type,
                'token_count': chunk.token_count,
                'language': chunk.language,
                'chunk_metadata': chunk.chunk_metadata,
            })

        logger.info(f"Document {document_id}: {len(result)} chunks produced")
        return result

