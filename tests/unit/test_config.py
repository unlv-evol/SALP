"""Configuration loading, and the platform resolution of external tools.

A config file is a contract with the person running the pipeline. These cover the
two ways that contract can fail quietly: a setting that resolves to the wrong
thing for the platform, and a setting the model accepts but never reads.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from salp.config import Config, Tools

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def distribution(tmp_path: Path) -> Path:
    """A RefactoringMiner distribution, with both launchers as it really ships."""
    bin_dir = tmp_path / "RefactoringMiner-3.1.4" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "RefactoringMiner").write_text("#!/bin/sh\n")
    (bin_dir / "RefactoringMiner.bat").write_text("@echo off\n")
    return bin_dir / "RefactoringMiner"


# --- platform resolution ------------------------------------------------------
def test_the_unix_launcher_is_used_off_windows(distribution: Path, monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    tools = Tools(refactoringminer_jar=distribution)
    assert tools.refactoringminer_launcher() == distribution


def test_the_windows_launcher_is_used_on_windows(distribution: Path, monkeypatch):
    """The .bat beside the configured path, not a second configured path.

    RefactoringMiner ships both launchers and only the .bat works on Windows, so
    hardcoding the Unix one made every Windows run fail.
    """
    monkeypatch.setattr(os, "name", "nt")
    tools = Tools(refactoringminer_jar=distribution)
    assert tools.refactoringminer_launcher() == distribution.with_suffix(".bat")


def test_one_config_file_serves_both_platforms(distribution: Path, monkeypatch):
    """The same configured value must resolve correctly on either platform.

    This is the point of deriving rather than configuring the Windows path: a
    config committed by a Linux developer has to run unchanged on Windows.
    """
    tools = Tools(refactoringminer_jar=distribution)
    monkeypatch.setattr(os, "name", "posix")
    on_unix = tools.refactoringminer_launcher()
    monkeypatch.setattr(os, "name", "nt")
    on_windows = tools.refactoringminer_launcher()
    assert on_unix != on_windows
    assert on_unix.is_file() and on_windows.is_file()


def test_windows_falls_back_when_no_bat_is_installed(tmp_path: Path, monkeypatch):
    """A distribution without the .bat keeps the failure with the analyzer.

    Returning None here would report "not configured", which is a different fact
    from "configured, but the launcher would not run".
    """
    launcher = tmp_path / "RefactoringMiner"
    launcher.write_text("#!/bin/sh\n")
    monkeypatch.setattr(os, "name", "nt")
    assert Tools(refactoringminer_jar=launcher).refactoringminer_launcher() == launcher


def test_an_unconfigured_tool_stays_unconfigured(monkeypatch):
    for name in ("posix", "nt"):
        monkeypatch.setattr(os, "name", name)
        assert Tools().refactoringminer_launcher() is None


def test_resolution_matches_the_platform_actually_running(distribution: Path):
    """The one assertion here that a Windows CI runner proves and Linux cannot.

    Every other test in this section patches `os.name`, so they check the branch
    rather than the platform. This one takes the real value, which is why the
    test matrix includes windows-latest.
    """
    resolved = Tools(refactoringminer_jar=distribution).refactoringminer_launcher()
    assert resolved is not None
    expected = ".bat" if os.name == "nt" else ""
    assert resolved.suffix == expected, f"on {os.name!r} the launcher was {resolved.name}"
    assert resolved.is_file()


# --- the config contract ------------------------------------------------------
def test_the_shipped_config_resolves_a_launcher():
    config = Config.load(Path("configs/default.yaml"))
    assert config.tools.refactoringminer_jar is not None
    assert config.tools.refactoringminer_launcher() is not None


def test_an_unknown_tool_setting_is_rejected_rather_than_ignored(tmp_path: Path):
    """A silently ignored setting turns a whole evidence category UNAVAILABLE.

    Renaming this key without rejecting the old one dropped `refactoring` from
    125 VERIFIED_ABSENT to 125 UNAVAILABLE on the reference sample, and mean
    Coverage from 0.940 to 0.823, with nothing in the output naming the cause.
    """
    bad = tmp_path / "bad.yaml"
    bad.write_text("tools:\n  refactoringminer_unix: ./tools/x\n")
    with pytest.raises(ValueError, match="refactoringminer_unix"):
        Config.load(bad)


def test_a_config_without_a_tools_section_still_loads(tmp_path: Path):
    minimal = tmp_path / "minimal.yaml"
    minimal.write_text("paths:\n  output: ./out\n")
    config = Config.load(minimal)
    assert config.tools.refactoringminer_launcher() is None
    assert config.paths.output == Path("./out")
