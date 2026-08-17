"""Reading files and declarations from a pinned repository state."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path, PurePosixPath

from salp.models import RepositoryStatePin
from salp.repos.cache import is_slug, repo_dir
from salp.repos.git import run


def read_file(cache_dir: Path, pin: RepositoryStatePin | None, path: str) -> str | None:
    """Read one file at a pinned state, or None when it cannot be read."""
    if pin is None or not pin.is_resolved or not is_slug(pin.repo):
        return None
    result = run(
        "show", f"{pin.commit}:{path}", cwd=repo_dir(cache_dir, pin.repo)
    )
    return result.stdout if result.ok else None


def list_tree(
    cache_dir: Path, pin: RepositoryStatePin | None, prefix: str = ""
) -> list[str]:
    """List the file paths present at a pinned state, optionally under a prefix."""
    if pin is None or not pin.is_resolved or not is_slug(pin.repo):
        return []
    args = ["ls-tree", "-r", "--name-only", str(pin.commit)]
    if prefix:
        args += ["--", prefix]
    result = run(*args, cwd=repo_dir(cache_dir, pin.repo))
    return result.text.splitlines() if result.ok else []


def file_exists(cache_dir: Path, pin: RepositoryStatePin | None, path: str) -> bool:
    if pin is None or not pin.is_resolved or not is_slug(pin.repo):
        return False
    result = run(
        "cat-file", "-e", f"{pin.commit}:{path}", cwd=repo_dir(cache_dir, pin.repo)
    )
    return result.ok


def grep_files(
    cache_dir: Path, pin: RepositoryStatePin | None, needle: str, *pathspecs: str
) -> list[str]:
    """Repository-relative paths at a pinned state whose contents contain ``needle``.

    Searching the object database directly is what makes target-side test
    discovery tractable: no checkout, and no reading files that cannot match.
    The needle is matched as a fixed string, never as a pattern.
    """
    if pin is None or not pin.is_resolved or not is_slug(pin.repo) or not needle:
        return []
    args = ["grep", "--files-with-matches", "--fixed-strings", needle, str(pin.commit)]
    if pathspecs:
        args += ["--", *pathspecs]
    result = run(*args, cwd=repo_dir(cache_dir, pin.repo))
    if not result.ok:
        return []
    # each line is "<sha>:<path>"
    return [line.split(":", 1)[1] for line in result.text.splitlines() if ":" in line]


def find_build_files(cache_dir: Path, pin: RepositoryStatePin | None, near: str) -> list[str]:
    """Build files governing a source path, nearest module first, then the root.

    Dependency versions are declared per module and inherited from the root, so
    the nearest declaration wins and the root is the fallback.
    """
    if pin is None or not pin.is_resolved:
        return []
    names = {"build.gradle", "build.gradle.kts", "pom.xml"}
    present = {p for p in list_tree(cache_dir, pin) if PurePosixPath(p).name in names}
    if not present:
        return []

    found: list[str] = []
    parent = PurePosixPath(near).parent
    while True:
        for name in ("build.gradle", "build.gradle.kts", "pom.xml"):
            candidate = str(parent / name) if str(parent) != "." else name
            if candidate in present and candidate not in found:
                found.append(candidate)
        if str(parent) in (".", "/", ""):
            break
        parent = parent.parent
    return found


# Gradle: implementation 'group:artifact:version' / api("g:a:v") / testImplementation(...)
_GRADLE_DEP = re.compile(
    r"""^\s*(?:api|implementation|compileOnly|runtimeOnly|testImplementation|
        testCompileOnly|testRuntimeOnly|annotationProcessor)\s*[( ]\s*["']([^"']+)["']""",
    re.MULTILINE | re.VERBOSE,
)


_MAVEN_DEP = re.compile(
    r"<dependency>\s*(?:<!--.*?-->\s*)*<groupId>([^<]+)</groupId>\s*"
    r"<artifactId>([^<]+)</artifactId>",
    re.DOTALL,
)


# Gradle version catalogs: `implementation libs.guava` resolves through
# gradle/libs.versions.toml rather than naming a coordinate inline. Modern Gradle
# builds declare almost everything this way, so a build-file scan alone finds
# nothing for them.
_CATALOG = "gradle/libs.versions.toml"
# Older Gradle builds keep the same idea in a Groovy map:
#   versions += [ guava: "31.1-jre", ... ]
#   libs     += [ guava: "com.google.guava:guava:$versions.guava", ... ]
_GROOVY_CATALOG = "gradle/dependencies.gradle"
_GROOVY_ENTRY = re.compile(r"""^\s*(\w+)\s*:\s*["']([^"']+)["']""", re.MULTILINE)
_GROOVY_BLOCK = re.compile(r"(versions|libs)\s*\+?=\s*\[(.*?)\n\s*\]", re.DOTALL)
_INTERPOLATION = re.compile(r"\$\{?versions\.(\w+)\}?")


def _catalog_dependencies(cache_dir: Path, pin: RepositoryStatePin) -> list[str]:
    """Coordinates declared in a Gradle version catalog, resolved to versions."""
    raw = read_file(cache_dir, pin, _CATALOG)
    if not raw:
        return []
    try:
        catalog = tomllib.loads(raw)
    except tomllib.TOMLDecodeError:
        return []

    versions = {k: str(v) for k, v in (catalog.get("versions") or {}).items()}
    found: list[str] = []
    for entry in (catalog.get("libraries") or {}).values():
        if isinstance(entry, str):  # guava = "com.google.guava:guava:31.1"
            found.append(entry)
            continue
        if not isinstance(entry, dict):
            continue
        module = entry.get("module") or ":".join(
            filter(None, (entry.get("group"), entry.get("name")))
        )
        if not module:
            continue
        version = entry.get("version")
        if isinstance(version, dict):  # {version.ref = "guava"}
            version = versions.get(str(version.get("ref", "")), "")
        found.append(f"{module}:{version}" if version else str(module))
    return found


def _groovy_catalog_dependencies(cache_dir: Path, pin: RepositoryStatePin) -> list[str]:
    """Coordinates from a Groovy dependency map, with ``$versions.x`` resolved."""
    raw = read_file(cache_dir, pin, _GROOVY_CATALOG)
    if not raw:
        return []

    blocks: dict[str, str] = {}
    for name, body in _GROOVY_BLOCK.findall(raw):
        blocks[name] = blocks.get(name, "") + body
    versions = dict(_GROOVY_ENTRY.findall(blocks.get("versions", "")))

    found: list[str] = []
    for _, coordinate in _GROOVY_ENTRY.findall(blocks.get("libs", "")):
        resolved = _INTERPOLATION.sub(lambda m: versions.get(m.group(1), ""), coordinate)
        if ":" in resolved:
            found.append(resolved.rstrip(":"))
    return found


def read_dependencies(
    cache_dir: Path, pin: RepositoryStatePin | None, near: str | None
) -> list[str] | None:
    """Dependencies declared for a source path at a pinned state.

    Returns None when no build file governs the path -- an unanswered question,
    distinct from an empty list, which is a build file that declares nothing.
    Only the coordinates are kept: what compatibility needs is whether the target
    declares a dependency at all, not how its version was interpolated.
    """
    if pin is None or not pin.is_resolved or not near:
        return None
    build_files = find_build_files(cache_dir, pin, near)
    if not build_files and not any(
        file_exists(cache_dir, pin, c) for c in (_CATALOG, _GROOVY_CATALOG)
    ):
        return None

    found: list[str] = []
    for path in build_files:
        text = read_file(cache_dir, pin, path)
        if not text:
            continue
        if path.endswith(".xml"):
            found += [f"{g.strip()}:{a.strip()}" for g, a in _MAVEN_DEP.findall(text)]
        else:
            found += [c.strip() for c in _GRADLE_DEP.findall(text)]
    found += _catalog_dependencies(cache_dir, pin)
    found += _groovy_catalog_dependencies(cache_dir, pin)
    # keep first-seen order: the nearest build file's declarations lead
    return list(dict.fromkeys(found))
