from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("tree_sitter_language_pack")

from code_reviewer.core.config import Config
from code_reviewer.indexing.builder import build_map
from code_reviewer.indexing.parsers.base import ParseOptions
from code_reviewer.indexing.parsers.treesitter import (
    TreeSitterAdapter,
    invalidate_grammar_cache,
    language_for,
    resolve_relative_file,
)

JS_SOURCE = '''// User service
import { db } from "./db.js";
import express from "express";

/** Service that loads users. */
export class UserService {
  constructor(repo) { this.repo = repo; }

  /** Load one user by id. */
  async getUser(id) {
    return this.repo.find(id);
  }
}

export function makeService(repo) {
  return new UserService(repo);
}
'''

COMMONJS_SOURCE = '''// CommonJS service
const db = require("./db.js");
const express = require("express");
// const fakeComment = require("./commented.js");
const fakeStr = 'require("./inside_string.js")';
const add = (a, b) => a + b;
const mul = async function (a, b) { return a * b; };
app.set = function (name, value) { return value; };
exports.handler = async (req) => req;
module.exports = function () { return 1; };
'''

GO_SOURCE = '''package demo

import (
    "fmt"
    "example.com/pkg/db"
)

// Service loads users.
type Service struct { repo *db.Repo }

// Get loads a user.
func (s *Service) Get(id string) (*User, error) {
    return s.repo.Find(id)
}
'''


def test_language_detection():
    assert language_for("a/b.tsx") == "tsx"
    assert language_for("a/b.go") == "go"
    assert language_for("a/b.py") is None
    assert language_for("Makefile") is None


def test_symbols_and_imports_from_javascript():
    info = TreeSitterAdapter().extract(
        "src/user.js", JS_SOURCE, size=len(JS_SOURCE), mtime=1.0, digest="d"
    )
    by_name = {symbol.name: symbol for symbol in info.symbols}

    assert info.lang == "javascript"
    assert by_name["UserService"].kind == "class"
    assert by_name["getUser"].parent == "UserService"
    assert by_name["getUser"].kind == "method"
    assert by_name["makeService"].kind == "function"

    modules = {item.module: item for item in info.imports}
    assert modules["db.js"].level == 1
    assert modules["express"].level == 0


def test_commonjs_ast_ignores_comments_and_strings():
    info = TreeSitterAdapter().extract(
        "lib/app.js",
        COMMONJS_SOURCE,
        size=len(COMMONJS_SOURCE),
        mtime=1.0,
        digest="d",
    )
    modules = {item.module for item in info.imports}
    assert "db.js" in modules
    assert "express" in modules
    assert "commented.js" not in modules
    assert "inside_string.js" not in modules

    by_name = {symbol.name: symbol for symbol in info.symbols}
    assert by_name["add"].kind == "function"
    assert by_name["mul"].kind == "function"
    assert by_name["set"].parent == "app"
    assert by_name["handler"].parent == "exports"
    assert all(symbol.name != "<anonymous>" for symbol in info.symbols)


def test_non_js_does_not_trigger_commonjs_extraction(monkeypatch):
    # Регрессионный тест: JS-парсер require() не должен вызываться для не-JS языков (например Java).
    import tree_sitter_language_pack as pack

    called = False

    def fake_process(source, cfg):
        return SimpleNamespace(
            structure=[],
            imports=[],
            metrics=SimpleNamespace(total_lines=2),
        )

    def fail_get_parser(lang):
        nonlocal called
        called = True
        raise AssertionError("get_parser не должен вызываться для JS AST на Java")

    monkeypatch.setattr(
        "code_reviewer.indexing.parsers.treesitter.ensure_grammar",
        lambda lang, fetch=True: True,
    )
    monkeypatch.setattr(pack, "process", fake_process)
    monkeypatch.setattr(pack, "get_parser", fail_get_parser)

    info = TreeSitterAdapter().extract(
        "App.java",
        'class App { void f() { require("./not_js"); } }\n',
        size=48,
        mtime=1.0,
        digest="d",
    )
    assert called is False
    assert info.imports == []


def test_fetch_grammars_false_never_downloads(monkeypatch):
    import tree_sitter_language_pack as pack

    invalidate_grammar_cache()
    monkeypatch.setattr(pack, "downloaded_languages", lambda: [])

    def fail_download(langs):
        raise AssertionError("при fetch_grammars=False загрузка запрещена")

    monkeypatch.setattr(pack, "download", fail_download)

    info = TreeSitterAdapter().extract(
        "Main.java",
        "class Main {}",
        size=13,
        mtime=1.0,
        digest="d",
        options=ParseOptions(fetch_grammars=False),
    )
    assert info.lang == "java"
    assert any("не загружена" in err for err in info.parse_errors)
    invalidate_grammar_cache()


def test_symbols_from_go():
    info = TreeSitterAdapter().extract(
        "svc/service.go", GO_SOURCE, size=len(GO_SOURCE), mtime=1.0, digest="d"
    )
    names = {symbol.name for symbol in info.symbols}
    assert info.lang == "go"
    assert "Get" in names
    assert any(item.module == "fmt" for item in info.imports)


def test_resolve_relative_file():
    known = {
        "src/lib/util.js",
        "src/components/index.tsx",
        "examples/auth/index.js",
        "examples/db.js",
        "db.js",
        "index.js",
    }
    assert resolve_relative_file("src/app.js", 1, "lib/util.js", known) == "src/lib/util.js"
    assert resolve_relative_file("src/app.js", 1, "components", known) == "src/components/index.tsx"
    assert resolve_relative_file("src/app.js", 2, "lib/util.js", known) is None
    assert resolve_relative_file("examples/auth/index.js", 2, "db", known) == "examples/db.js"
    assert resolve_relative_file("examples/auth/index.js", 3, "", known) == "index.js"


def test_js_commonjs_require_becomes_graph_edge(tmp_path: Path):
    (tmp_path / "src/lib").mkdir(parents=True)
    (tmp_path / "src/app.js").write_text(
        'const util = require("./lib/util.js");\nutil();\n',
        encoding="utf-8",
    )
    (tmp_path / "src/lib/util.js").write_text(
        "exports.util = function () { return 1; };\n", encoding="utf-8"
    )

    repo_map = build_map(tmp_path, Config()).repo_map
    internal = {(edge.src, edge.dst) for edge in repo_map.edges if edge.status == "internal"}
    assert ("src/app.js", "src/lib/util.js") in internal
    assert "util" in {symbol.name for symbol in repo_map.files["src/lib/util.js"].symbols}
