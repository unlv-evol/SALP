"""Tests the functionalities in the analyzer directory.
This includes GACPD-related items, tree-sitter items, and RefactoringMiner items.
"""

import json
import tempfile
from pathlib import Path

import pytest

from salp.analyzers import tools
from salp.config import Config
from salp.repos import (
    repo_dir,
)


@pytest.fixture
def cfg():
    """Provides a default Config instance to tests."""
    yaml_path = Path('./configs/default.yaml')
    config = Config.load(yaml_path)

    return config

# --- output formatting ------------------------------------------------------
def test_c_bc_command_result_equivalence(cfg):
    # Must have a GACPD output saved in data/GACPD and run the 
    # "salp -c configs/default.yaml fetch-repos" command.
    if not repo_dir(cfg.paths.repo_cache, "apache/kafka").is_dir():
        pytest.skip(
            'skipping test_c_bc_command_result_equivalence: '
            'no apache/kafka folder present in data/repos'
        )

    if not cfg.tools.refactoringminer_jar.is_file():
        pytest.skip('skipping test_c_bc_command_result_equivalence: '
              'RefactoringMiner not found in the specified directory')

    with open ('tests/data/Apache_Kafka_PR_12289_Commits_Info.json'
               ,  encoding = 'utf-8') as commit_file:
        commits_info = json.loads(commit_file.read())
        # Getting the "-bc" command refactorings
        start_sha = commits_info[0]['sha']
        end_sha = commits_info[-1]['sha']
        bc_refactorings = tools.run_refactoring_miner(
            cfg.tools.refactoringminer_jar,
            repo_dir(cfg.paths.repo_cache, "apache/kafka"),
            start_sha,
            end_sha,
            cfg.paths.repo_cache / ".refactoring-cache",
            cfg.tools.refactoringminer_timeout,
        )
        # Getting the "-c" command refactorings
        sha_list = tuple(x['sha'] for x in commits_info)
        c_refactorings = tools.run_refactoring_miner_list(
            cfg.tools.refactoringminer_jar,
            repo_dir(cfg.paths.repo_cache, "apache/kafka"),
            sha_list,
            cfg.paths.repo_cache / ".refactoring-cache",
            cfg.tools.refactoringminer_timeout,
        )
        # Have to test equivalence in json format, otherwise there could be 
        # strange differences.
        with tempfile.TemporaryDirectory() as tmp:
            c_refactorings_temp_json = Path(tmp) / "c_refactorings.json"
            bc_refactorings_temp_json = Path(tmp) / "bc_refactorings.json"
            with open(c_refactorings_temp_json, 'w', encoding = 'utf-8') as c_file:
                json.dump(c_refactorings, c_file, indent= 2)
            with open(bc_refactorings_temp_json, 'w', encoding = 'utf-8') as bc_file:
                json.dump(bc_refactorings, bc_file, indent= 2)

            with (
                open(c_refactorings_temp_json,  encoding = 'utf-8') as c_in_file,
                open (bc_refactorings_temp_json,  encoding = 'utf-8') as bc_in_file,
            ):
                c_json = json.loads(c_in_file.read())
                bc_json = json.loads(bc_in_file.read())
                assert c_json == bc_json