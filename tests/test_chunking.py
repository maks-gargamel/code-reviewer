from __future__ import annotations

from code_reviewer.core.models import EdgeStatus, GraphEdge, RepoMap
from code_reviewer.review.chunking import plan_chunks
from code_reviewer.vcs.diff import DiffSet, FileDiff


def _file(path: str, size: int = 100) -> FileDiff:
    return FileDiff(path=path, additions=1, patch="+" + "x" * size)


def _map(edges: list[GraphEdge] | None = None) -> RepoMap:
    return RepoMap(edges=edges or [])


def test_single_and_multi_chunk_planning():
    small = DiffSet(files=[_file("a.py"), _file("b.py")])
    plan = plan_chunks(small, _map(), budget=100_000, max_chunks=3)
    assert len(plan.chunks) == 1
    assert plan.omitted == []

    large = DiffSet(files=[_file("a.py", 4000), _file("b.py", 4000), _file("c.py", 4000)])
    split = plan_chunks(large, _map(), budget=2500, max_chunks=5)
    assert len(split.chunks) == 3
    assert split.files == 3


def test_related_files_stay_together_when_within_budget():
    diffset = DiffSet(files=[_file("a.py", 4000), _file("b.py", 4000), _file("c.py", 4000)])
    related = _map(
        [GraphEdge(src="a.py", dst="b.py", name="b", kind="from", status=EdgeStatus.INTERNAL)]
    )
    plan = plan_chunks(diffset, related, budget=8000, max_chunks=3)
    groups = [{f.path for f in chunk} for chunk in plan.chunks]
    assert any({"a.py", "b.py"} <= g for g in groups)


def test_oversized_connected_component_splits_across_chunks():
    diffset = DiffSet(files=[_file("a.py", 6000), _file("b.py", 6000)])
    related = _map(
        [GraphEdge(src="a.py", dst="b.py", name="b", kind="from", status=EdgeStatus.INTERNAL)]
    )
    plan = plan_chunks(diffset, related, budget=3500, max_chunks=3)
    assert len(plan.chunks) == 2
    assert plan.omitted == []


def test_respects_max_chunks_and_records_omitted():
    diffset = DiffSet(files=[_file(f"f{i}.py", 4000) for i in range(5)])
    plan = plan_chunks(diffset, _map(), budget=2500, max_chunks=2)
    analyzed = {item.path for chunk in plan.chunks for item in chunk}
    assert len(plan.chunks) == 2
    assert analyzed | set(plan.omitted) == {f"f{i}.py" for i in range(5)}
    assert len(plan.omitted) == 3


def test_empty_diff_has_no_chunks():
    plan = plan_chunks(DiffSet(files=[]), _map())
    assert plan.is_empty
    assert plan.chunks == []
