"""Composable temporal retrieval algorithms.

Keep new experimental algorithms in this package.  The retrieval service owns
candidate retrieval and result persistence; this package owns only temporal
candidate transformation and ranking.
"""

from .ats import adaptive_temporal_search
from .dev_first import DevFirstCandidate, DevFirstSequence, build_dev_first_vortex_ats_sequences
from .types import Candidate, TemporalSequence
from .vortex import vortex_k_context_rerank

__all__ = [
    "Candidate",
    "DevFirstCandidate",
    "DevFirstSequence",
    "TemporalSequence",
    "adaptive_temporal_search",
    "build_dev_first_vortex_ats_sequences",
    "vortex_k_context_rerank",
]
