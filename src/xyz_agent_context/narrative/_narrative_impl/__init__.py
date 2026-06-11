"""
Private implementation of the Narrative module

This directory contains the concrete implementation of NarrativeService and should not be imported directly externally.

Module list:
- crud: Narrative creation, read, update, delete
- retrieval: BM25 keyword retrieval and LLM confirmation
- updater: Narrative summary generation
- instance_handler: Instance dependency management
- prompt_builder: Prompt assembly
- continuity: Continuity detection
- default_narratives: Default Narrative management
"""

from .crud import NarrativeCRUD
from .retrieval import NarrativeRetrieval
from .updater import NarrativeUpdater
from .instance_handler import InstanceHandler
from .prompt_builder import PromptBuilder
from .continuity import ContinuityDetector
from .default_narratives import (
    DEFAULT_NARRATIVES_CONFIG,
    build_default_narrative_id,
    build_default_narrative_id_pattern,
    create_default_narrative,
    ensure_default_narratives,
    get_default_narrative_by_name,
    get_default_narrative_by_code,
    get_all_default_narrative_names,
    get_all_default_narrative_codes,
    get_default_narrative_config,
)

__all__ = [
    "NarrativeCRUD",
    "NarrativeRetrieval",
    "NarrativeUpdater",
    "InstanceHandler",
    "PromptBuilder",
    "ContinuityDetector",
    # Default Narratives
    "DEFAULT_NARRATIVES_CONFIG",
    "build_default_narrative_id",
    "build_default_narrative_id_pattern",
    "create_default_narrative",
    "ensure_default_narratives",
    "get_default_narrative_by_name",
    "get_default_narrative_by_code",
    "get_all_default_narrative_names",
    "get_all_default_narrative_codes",
    "get_default_narrative_config",
]
