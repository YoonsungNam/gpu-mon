"""Base adapter interface for all metadata sources."""

from abc import ABC, abstractmethod
from typing import List, Dict


class MetadataAdapter(ABC):
    """All metadata source adapters implement this interface."""

    @abstractmethod
    def fetch(self) -> List[Dict]:
        """Fetch data from the source and return normalized rows."""

    @abstractmethod
    def get_table_name(self) -> str:
        """Return the ClickHouse target table name."""
