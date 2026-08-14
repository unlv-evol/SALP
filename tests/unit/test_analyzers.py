"""Tests the functionalities in the analyzer directory.
This includes GACPD-related items, tree-sitter items, and RefactoringMiner items.
"""

from pathlib import Path

import pytest

from salp.config import Config


@pytest.fixture
def cfg():
    """Provides a default Config instance to tests."""
    yaml_path = Path('./configs/default.yaml')
    config = Config.load(yaml_path)

    return config
