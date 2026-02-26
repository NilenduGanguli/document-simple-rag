import os
from typing import List
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

    def chunk(self, text: str, document_id: str, routing_result=None) -> List[dict]:
        """
        Split text into chunks using the configured strategy.
        Returns list of dicts ready for DB insertion.
        """
        strategy = self._strategies.get(self._default_strategy)
        if not strategy:
            raise ValueError(f"Unknown chunking strategy: {self._default_strategy}")

        raw_chunks = strategy.split(text)

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
