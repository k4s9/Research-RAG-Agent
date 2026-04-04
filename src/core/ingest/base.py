from abc import ABC, abstractmethod
from typing import Dict, Any, List
from dataclasses import dataclass


@dataclass
class ChunkResult:
    content: str
    content_type: str
    metadata: Dict[str, Any]


class BaseParser(ABC):
    @abstractmethod
    async def parse(self, file_path: str) -> Dict[str, Any]:
        pass

    @property
    @abstractmethod
    def file_type(self) -> str:
        pass


class BaseCleaner(ABC):
    @abstractmethod
    def clean(self, parsed_content: Dict[str, Any]) -> Dict[str, Any]:
        pass


class BaseChunker(ABC):
    def __init__(self, chunk_size: int = 384, overlap: int = 128):
        self.chunk_size = chunk_size
        self.overlap = overlap

    @abstractmethod
    def chunk(self, cleaned_content: Dict[str, Any]) -> List[ChunkResult]:
        pass
