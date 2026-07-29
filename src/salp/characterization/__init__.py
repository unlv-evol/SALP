"""SAP characterization: Coverage, Fidelity, and Readiness.

Coverage measures investigation completion over required categories; Fidelity
measures representation quality over PRESENT elements only; Readiness is their
harmonic mean, constrained by the foundational conditions.
"""

from salp.characterization.engine import (
    CategoryScore,
    CharacterizationProfile,
    Characterizer,
    aggregate_readiness,
)
from salp.characterization.levels import (
    CoverageLevel,
    FidelityLevel,
    ReadinessLevel,
)

__all__ = [
    "CategoryScore",
    "CharacterizationProfile",
    "Characterizer",
    "CoverageLevel",
    "FidelityLevel",
    "ReadinessLevel",
    "aggregate_readiness",
]
