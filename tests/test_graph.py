from __future__ import annotations

from code_reviewer.core.models import EdgeStatus, FileInfo, GraphEdge, RepoMap, Symbol
from code_reviewer.indexing.graph import find_cycles, search_symbols, to_dot


def _edge(src: str, dst: str | None) -> GraphEdge:
    return GraphEdge(
        src=src, dst=dst, name=dst or "ext", kind="from", status=EdgeStatus.INTERNAL
    )


def _map(edges: list[GraphEdge], files: list[str] | None = None) -> RepoMap:
    paths = files or sorted({edge.src for edge in edges} | {e.dst for e in edges if e.dst})
    return RepoMap(
        files={path: FileInfo(path=path, lang="python") for path in paths},
        edges=edges,
    )


def test_cycle_detection():
    assert find_cycles(_map([_edge("a.py", "b.py"), _edge("b.py", "c.py")])) == []

    two = find_cycles(_map([_edge("a.py", "b.py"), _edge("b.py", "a.py")]))
    assert len(two) == 1
    assert two[0].files == ["a.py", "b.py"]

    self_loop = find_cycles(_map([_edge("a.py", "a.py")]))
    assert len(self_loop) == 1
    assert self_loop[0].files == ["a.py"]

    multi = find_cycles(
        _map([
            _edge("a.py", "b.py"),
            _edge("b.py", "a.py"),
            _edge("c.py", "d.py"),
            _edge("d.py", "c.py"),
        ])
    )
    assert [cycle.files for cycle in multi] == [["a.py", "b.py"], ["c.py", "d.py"]]


def test_search_symbols():
    repo_map = RepoMap(
        files={
            "a.py": FileInfo(
                path="a.py",
                lang="python",
                symbols=[
                    Symbol(
                        name="load_user",
                        kind="function",
                        line=3,
                        end_line=5,
                        signature="def load_user(db, user_id) -> User",
                    ),
                    Symbol(name="User", kind="class", line=8, end_line=12, signature="class User"),
                ],
            ),
            "b.py": FileInfo(
                path="b.py",
                lang="python",
                symbols=[
                    Symbol(
                        name="parse",
                        kind="function",
                        line=1,
                        end_line=2,
                        signature="def parse(value: str) -> int",
                    ),
                ],
            ),
        }
    )

    assert [h.name for h in search_symbols(repo_map, "user")] == ["load_user", "User"]
    assert [h.name for h in search_symbols(repo_map, "-> int")] == ["parse"]
    assert len(search_symbols(repo_map, "a", limit=1)) == 1
    assert search_symbols(repo_map, "   ") == []


def test_dot_export_escapes_quotes_and_respects_focus():
    repo_map = _map(
        [_edge('a"1.py', "b\\2.py"), _edge('a"1.py', "b\\2.py"), _edge("c.py", "d.py")],
        ['a"1.py', "b\\2.py", "c.py", "d.py"],
    )
    dot = to_dot(repo_map, focus='a"1.py', depth=1)

    assert '"a\\"1.py";' in dot
    assert '"b\\\\2.py";' in dot
    assert dot.count('"a\\"1.py" -> "b\\\\2.py";') == 1
    assert '"c.py";' not in dot
