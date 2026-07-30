"""Evidence analyzers: the contract, the registry, and the built-ins.

Each analyzer investigates exactly one SAP category and reports an outcome for
every *required information element* of it, so Coverage and Fidelity are graded
rather than all-or-nothing. Registering a class replaces the built-in for its
category, so the orchestrator never needs editing to add an analysis.

Importing this package registers every built-in: the submodules below are
imported for that side effect, which is why they are re-exported rather than
merely imported.
"""

from salp.analyzers.base import (
    AnalysisContext,
    Analyzer,
    CategoryDraft,
    build_all,
    get,
    register,
    registered_categories,
)
from salp.analyzers.compatibility import CompatibilityAnalyzer
from salp.analyzers.gacpd import (
    SourceChangeAnalyzer,
    TargetLocalizationAnalyzer,
    TransformationAnalyzer,
)
from salp.analyzers.refactoring import RefactoringAnalyzer
from salp.analyzers.structural import StructuralAnalyzer, SurroundingAnalyzer
from salp.analyzers.tools import run_refactoring_miner
from salp.analyzers.verification import VerificationAnalyzer

__all__ = [
    "AnalysisContext",
    "Analyzer",
    "CategoryDraft",
    "CompatibilityAnalyzer",
    "RefactoringAnalyzer",
    "SourceChangeAnalyzer",
    "StructuralAnalyzer",
    "SurroundingAnalyzer",
    "TargetLocalizationAnalyzer",
    "TransformationAnalyzer",
    "VerificationAnalyzer",
    "build_all",
    "get",
    "register",
    "registered_categories",
    "run_refactoring_miner",
]
