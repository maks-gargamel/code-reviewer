from __future__ import annotations

import os
from pathlib import Path

from code_reviewer.core.config import Config
from code_reviewer.core.models import EdgeStatus, FileInfo, GraphEdge, ImportRef, RepoMap
from code_reviewer.indexing.builder import build_map, detect_source_roots
from code_reviewer.indexing.resolver import resolve_edges


def _edges(
    repo_map: RepoMap, status: str = EdgeStatus.INTERNAL
) -> set[tuple[str, str | None, str]]:
    return {(e.src, e.dst, e.name) for e in repo_map.edges if e.status == status}


def test_flat_layout_resolution(project: Path):
    repo_map = build_map(project, Config()).repo_map
    internal = _edges(repo_map)

    assert ("main.py", "pkg/models.py", "pkg.models") in internal
    assert ("main.py", "pkg/utils.py", "pkg.utils") in internal
    assert ("pkg/models.py", "pkg/utils.py", "pkg.utils") in internal
    assert ("pkg/sub/mod.py", "pkg/utils.py", "pkg.utils") in internal

    external = {e.name for e in repo_map.edges if e.status == EdgeStatus.EXTERNAL}
    assert "requests" in external
    assert not any(e.status == EdgeStatus.UNRESOLVED for e in repo_map.edges)


def test_src_layout_resolution(src_project: Path):
    repo_map = build_map(src_project, Config()).repo_map
    assert "src" in repo_map.source_roots
    internal = _edges(repo_map)

    assert ("src/app/core.py", "src/app/helpers.py", "app.helpers") in internal
    assert ("src/app/core.py", "src/app/settings.py", "app.settings") in internal


def test_c_local_quoted_include_becomes_internal_edge():
    files = {
        "src/main.c": FileInfo(
            path="src/main.c",
            lang="c",
            imports=[
                ImportRef(raw='#include "util.h"', line=1, module="util.h"),
                ImportRef(raw="#include <stdio.h>", line=2, module="stdio.h"),
            ],
        ),
        "src/util.h": FileInfo(path="src/util.h", lang="c"),
    }
    edges = resolve_edges(files, [""])
    internal = {(e.src, e.dst, e.kind) for e in edges if e.status == EdgeStatus.INTERNAL}
    external = {e.name for e in edges if e.status == EdgeStatus.EXTERNAL}

    assert ("src/main.c", "src/util.h", "include") in internal
    assert "stdio.h" in external


def test_detect_source_roots_only_uses_given_paths():
    roots = detect_source_roots(["main.py", "src/pkg/core.py", "docs/readme.md"])
    assert roots == ["", "src"]


def test_repo_map_index_and_roundtrip(project: Path, tmp_path: Path):
    repo_map = build_map(project, Config()).repo_map
    target = tmp_path / "map.json"
    repo_map.save(target)
    loaded = RepoMap.load(target)

    assert {e.dst for e in loaded.dependencies("main.py")} == {"pkg/models.py", "pkg/utils.py"}
    assert {e.src for e in loaded.dependents("pkg/utils.py")} == {
        "main.py",
        "pkg/models.py",
        "pkg/sub/mod.py",
    }
    assert loaded.related("pkg/utils.py", depth=1) == {
        "main.py",
        "pkg/models.py",
        "pkg/sub/mod.py",
    }

    loaded.edges.append(
        GraphEdge(src="a.py", dst="b.py", name="b", kind="import", status=EdgeStatus.INTERNAL)
    )
    assert any(e.dst == "b.py" for e in loaded.dependencies("a.py"))


def test_incremental_rebuild_reuses_files_and_preserves_summary(project: Path):
    first = build_map(project, Config())
    assert first.stats.files_parsed == first.stats.files_total
    first.repo_map.files["pkg/utils.py"].summary = "Вспомогательные функции."

    second = build_map(project, Config(), previous=first.repo_map)
    assert second.stats.files_parsed == 0
    assert second.stats.files_reused == first.stats.files_total

    # Изменяем тело функции без изменения сигнатуры — сводка должна сохраниться.
    target = project / "pkg/utils.py"
    target.write_text("def helper() -> int:\n    return 2\n", encoding="utf-8")
    stamp = target.stat().st_mtime + 10
    os.utime(target, (stamp, stamp))

    third = build_map(project, Config(), previous=second.repo_map)
    assert third.stats.files_parsed == 1
    assert third.repo_map.files["pkg/utils.py"].summary == "Вспомогательные функции."
