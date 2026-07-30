"""Qualitative levels for Coverage, Fidelity, and Readiness."""

from __future__ import annotations

from enum import IntEnum


class CoverageLevel(IntEnum):
    MINIMAL = 0
    LIMITED = 1
    PARTIAL = 2
    SUBSTANTIAL = 3
    COMPREHENSIVE = 4

    @classmethod
    def from_score(cls, s: float) -> CoverageLevel:
        if s >= 0.90:
            return cls.COMPREHENSIVE
        if s >= 0.75:
            return cls.SUBSTANTIAL
        if s >= 0.50:
            return cls.PARTIAL
        if s >= 0.25:
            return cls.LIMITED
        return cls.MINIMAL


class FidelityLevel(IntEnum):
    VERY_SPARSE = 0
    SPARSE = 1
    MODERATE = 2
    RICH = 3
    VERY_RICH = 4

    @classmethod
    def from_score(cls, s: float) -> FidelityLevel:
        if s >= 0.90:
            return cls.VERY_RICH
        if s >= 0.75:
            return cls.RICH
        if s >= 0.50:
            return cls.MODERATE
        if s >= 0.25:
            return cls.SPARSE
        return cls.VERY_SPARSE


class ReadinessLevel(IntEnum):
    LOW = 0
    MODERATE = 1
    HIGH = 2

    @classmethod
    def from_score(cls, s: float) -> ReadinessLevel:
        if s >= 0.85:
            return cls.HIGH
        if s >= 0.60:
            return cls.MODERATE
        return cls.LOW
