"""Ingestion package."""

from .add import add_file, add_glob, add_tpch
from .joins import JoinCandidate, discover_joins
from .profile import ColumnProfile, TableProfile, profile_all, profile_table

__all__ = [
    "JoinCandidate",
    "ColumnProfile",
    "TableProfile",
    "add_file",
    "add_glob",
    "add_tpch",
    "discover_joins",
    "profile_all",
    "profile_table",
]
