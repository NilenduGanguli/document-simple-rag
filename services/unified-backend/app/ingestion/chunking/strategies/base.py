from abc import ABC, abstractmethod
from typing import List
from dataclasses import dataclass, field


@dataclass
class Chunk:
    chunk_index: int
    chunk_text: str
    char_start: int
    char_end: int
    page_number: int = None
    source_type: str = 'text'
    token_count: int = 0
    language: str = None
    chunk_metadata: dict = field(default_factory=dict)


class BaseChunkStrategy(ABC):
    @abstractmethod
    def split(self, text: str, **kwargs) -> List[Chunk]:
        pass
