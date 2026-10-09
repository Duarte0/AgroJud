"""Source contracts and adapters for SPEC-002."""

from agrojud.sources.contracts import (
    Cursor,
    NormalizedSourceCover,
    SortTerm,
    SourceAdapter,
    SourceError,
    SourceErrorCode,
    SourceHit,
    SourcePage,
    SourceQuery,
    build_query_by_case_number,
    normalize_source_cover,
)
from agrojud.sources.datajud import DataJudSourceAdapter
from agrojud.sources.factory import SourceKind, build_source_adapter
from agrojud.sources.synthetic import (
    SyntheticPageFixture,
    SyntheticQueryFixture,
    SyntheticSourceAdapter,
)

__all__ = [
    "Cursor",
    "DataJudSourceAdapter",
    "NormalizedSourceCover",
    "SortTerm",
    "SourceAdapter",
    "SourceError",
    "SourceErrorCode",
    "SourceHit",
    "SourceKind",
    "SourcePage",
    "SourceQuery",
    "SyntheticPageFixture",
    "SyntheticQueryFixture",
    "SyntheticSourceAdapter",
    "build_query_by_case_number",
    "build_source_adapter",
    "normalize_source_cover",
]
