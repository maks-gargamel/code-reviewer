from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_language_pack")

from code_reviewer.core.config import Config
from code_reviewer.core.models import MAP_VERSION, EdgeStatus, RepoMap
from code_reviewer.indexing.builder import build_map, stale_files
from code_reviewer.indexing.parsers.treesitter import language_for


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _edges(repo_map: RepoMap, status: str = EdgeStatus.INTERNAL) -> set[tuple[str, str | None]]:
    return {(e.src, e.dst) for e in repo_map.edges if e.status == status}


def test_csharp_grammar_mapping_symbols_and_using_edges(tmp_path: Path):
    assert language_for("Program.cs") == "csharp"
    _write(
        tmp_path,
        {
            "Models/Order.cs": (
                "namespace Demo.Models;\n"
                "public enum OrderState { New, Paid }\n"
                "public record Order(int Id, decimal Total);\n"
            ),
            "Services/OrderService.cs": (
                "using System;\n"
                "using Demo.Models;\n"
                "namespace Demo.Services;\n"
                "public interface IOrderService { bool Process(Order order); }\n"
                "public class OrderService : IOrderService {\n"
                "    public bool Process(Order order) => order.Total > 0;\n"
                "}\n"
            ),
        },
    )

    built = build_map(tmp_path, Config())
    assert built.stats.parse_errors == 0
    rmap = built.repo_map
    assert rmap.files["Models/Order.cs"].lang == "csharp"

    model_syms = {s.name: s for s in rmap.files["Models/Order.cs"].symbols}
    svc_syms = {s.name: s for s in rmap.files["Services/OrderService.cs"].symbols}
    assert "Order" in model_syms
    assert "OrderState" in model_syms
    assert "IOrderService" in svc_syms
    assert "OrderService" in svc_syms
    assert "Process" in svc_syms

    assert ("Services/OrderService.cs", "Models/Order.cs") in _edges(rmap)
    ext = {e.name for e in rmap.edges if e.status == EdgeStatus.EXTERNAL}
    assert "System" in ext


def test_c_and_cpp_symbols_cxx_extension_and_include_edges(tmp_path: Path):
    assert language_for("src/extra.cxx") == "cpp"
    _write(
        tmp_path,
        {
            "include/user.h": (
                "#ifndef DEMO_USER_H\n"
                "#define DEMO_USER_H\n"
                "#define MAX_USERS 64\n"
                "typedef struct { int id; const char *name; } User;\n"
                "User make_user(int id, const char *name);\n"
                "#endif\n"
            ),
            "src/main.c": (
                "#include <stdio.h>\n"
                '#include "user.h"\n'
                "int main(void) {\n"
                '    User u = make_user(1, "Ada");\n'
                "    return u.id;\n"
                "}\n"
            ),
            "include/engine.hpp": (
                "#pragma once\n"
                "namespace demo {\n"
                "class Engine {\n"
                "public:\n"
                "    int run(int x) const;\n"
                "};\n"
                "}\n"
            ),
            "src/engine.cxx": (
                '#include "../include/engine.hpp"\n'
                "namespace demo {\n"
                "int Engine::run(int x) const { return x + 1; }\n"
                "}\n"
            ),
        },
    )

    rmap = build_map(tmp_path, Config()).repo_map
    h_syms = {s.name: s for s in rmap.files["include/user.h"].symbols}
    assert "DEMO_USER_H" not in h_syms
    assert h_syms["MAX_USERS"].kind == "constant"
    assert h_syms["User"].kind == "class"
    assert "const char *name" in h_syms["make_user"].signature

    cxx_syms = rmap.files["src/engine.cxx"].symbols
    assert rmap.files["src/engine.cxx"].lang == "cpp"
    assert all(s.name != "<anonymous>" for s in cxx_syms)
    assert any(s.name == "run" and s.parent == "Engine" for s in cxx_syms)

    hpp_syms = {s.name: s for s in rmap.files["include/engine.hpp"].symbols}
    assert hpp_syms["Engine"].kind == "class"
    assert hpp_syms["run"].parent == "Engine"

    internal = _edges(rmap)
    assert ("src/main.c", "include/user.h") in internal
    assert ("src/engine.cxx", "include/engine.hpp") in internal
    ext = {e.name for e in rmap.edges if e.status == EdgeStatus.EXTERNAL}
    assert "stdio.h" in ext


def test_go_structs_receiver_parent_and_gomod_edges(tmp_path: Path):
    _write(
        tmp_path,
        {
            "go.mod": "module example.com/demo\n\ngo 1.22\n",
            "internal/models/user.go": (
                "package models\n\n"
                "type User struct {\n"
                "\tID int\n"
                "}\n"
            ),
            "internal/service/service.go": (
                "package service\n\n"
                'import "example.com/demo/internal/models"\n\n'
                "type Greeter interface {\n"
                "\tGreet(u models.User) string\n"
                "}\n\n"
                "type Simple struct{}\n\n"
                "func (s *Simple) Greet(u models.User) string {\n"
                '\treturn "hi"\n'
                "}\n"
            ),
        },
    )

    rmap = build_map(tmp_path, Config()).repo_map
    assert "User" in {s.name for s in rmap.files["internal/models/user.go"].symbols}
    svc_syms = {s.name: s for s in rmap.files["internal/service/service.go"].symbols}
    assert "Greeter" in svc_syms
    assert "Simple" in svc_syms
    assert svc_syms["Greet"].parent == "Simple"
    assert ("internal/service/service.go", "internal/models/user.go") in _edges(rmap)


def test_rust_mod_and_crate_imports_become_internal_edges(tmp_path: Path):
    _write(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
            "src/models.rs": "pub struct Item { pub id: u32 }\n",
            "src/service.rs": (
                "use crate::models::Item;\n"
                "pub fn check(item: &Item) -> bool { item.id > 0 }\n"
            ),
            "src/main.rs": (
                "use std::fmt;\n"
                "mod models;\n"
                "mod service;\n"
                "fn main() { let _ = service::check(&models::Item { id: 1 }); }\n"
            ),
        },
    )

    rmap = build_map(tmp_path, Config()).repo_map
    internal = _edges(rmap)
    assert ("src/main.rs", "src/models.rs") in internal
    assert ("src/main.rs", "src/service.rs") in internal
    assert ("src/service.rs", "src/models.rs") in internal


def test_java_and_kotlin_clean_symbols_and_internal_edges(tmp_path: Path):
    _write(
        tmp_path,
        {
            "src/main/java/com/demo/User.java": "package com.demo;\npublic class User {}\n",
            "src/main/java/com/demo/App.java": (
                "package com.demo;\n"
                "import java.util.List;\n"
                "import com.demo.User;\n"
                "public class App {\n"
                "    public Class<?> kind() { return String.class; }\n"
                "}\n"
            ),
            "src/main/kotlin/com/demo/Model.kt": (
                "package com.demo\n"
                "data class Item(val id: Int)\n"
            ),
            "src/main/kotlin/com/demo/Runner.kt": (
                "package com.demo\n"
                "import com.demo.Item\n"
                "class Runner {\n"
                "    fun run(item: Item): Int {\n"
                "        val k = Item::class\n"
                "        return item.id + k.hashCode()\n"
                "    }\n"
                "}\n"
            ),
        },
    )

    rmap = build_map(tmp_path, Config()).repo_map
    java_syms = rmap.files["src/main/java/com/demo/App.java"].symbols
    assert all(s.name != "<anonymous>" for s in java_syms)

    kt_syms = {s.name: s for s in rmap.files["src/main/kotlin/com/demo/Runner.kt"].symbols}
    assert all(s.name != "<anonymous>" for s in kt_syms.values())
    assert kt_syms["run"].parent == "Runner"
    assert "{" not in kt_syms["run"].signature

    internal = _edges(rmap)
    assert (
        "src/main/java/com/demo/App.java",
        "src/main/java/com/demo/User.java",
    ) in internal
    assert (
        "src/main/kotlin/com/demo/Runner.kt",
        "src/main/kotlin/com/demo/Model.kt",
    ) in internal


def test_ruby_php_scala_swift_lua_dart_extraction_and_edges(tmp_path: Path):
    _write(
        tmp_path,
        {
            "lib/user.rb": "class User; def name; 'a'; end; end\n",
            "lib/app.rb": "require 'json'\nrequire_relative 'user'\n",
            "src/Models/User.php": "<?php\nnamespace Demo\\Models;\nclass User {}\n",
            "src/App.php": (
                "<?php\n"
                "namespace Demo;\n"
                "use Demo\\Models\\User;\n"
                "require_once __DIR__ . '/Models/User.php';\n"
            ),
            "src/main/scala/demo/Model.scala": "package demo\ncase class Model(id: Int)\n",
            "src/main/scala/demo/App.scala": (
                "package demo\nimport demo.Model\nobject App { def go(): Int = 1 }\n"
            ),
            "Sources/App/Main.swift": (
                "import Foundation\nstruct App { func run() -> Int { 1 } }\n"
            ),
            "lua/pkg/util.lua": "local M = {}\nM.ping = function(x) return x end\nreturn M\n",
            "lua/app.lua": 'local u = require("pkg.util")\nreturn u\n',
            "pubspec.yaml": "name: demo_pkg\n",
            "lib/models.dart": "class Task {}\n",
            "lib/service.dart": (
                "import './models.dart';\n"
                "class Runner {\n"
                "  int run(Task t) => 1;\n"
                "}\n"
            ),
            "lib/main.dart": (
                "import 'dart:async';\n"
                "import 'package:demo_pkg/service.dart';\n"
                "Future<int> boot() async => 1;\n"
            ),
        },
    )

    rmap = build_map(tmp_path, Config()).repo_map
    internal = _edges(rmap)

    assert ("lib/app.rb", "lib/user.rb") in internal
    assert ("src/App.php", "src/Models/User.php") in internal
    assert ("src/main/scala/demo/App.scala", "src/main/scala/demo/Model.scala") in internal
    assert ("lua/app.lua", "lua/pkg/util.lua") in internal
    assert ("lib/service.dart", "lib/models.dart") in internal
    assert ("lib/main.dart", "lib/service.dart") in internal

    assert any(
        i.module == "Foundation" for i in rmap.files["Sources/App/Main.swift"].imports
    )
    lua_syms = {s.name: s for s in rmap.files["lua/pkg/util.lua"].symbols}
    assert lua_syms["ping"].parent == "M"
    assert lua_syms["ping"].kind == "method"

    svc_dart = {s.name: s for s in rmap.files["lib/service.dart"].symbols}
    assert "Runner" in svc_dart
    assert svc_dart["run"].parent == "Runner"
    main_dart = {s.name: s for s in rmap.files["lib/main.dart"].symbols}
    assert "boot" in main_dart


def test_map_version_bumped_and_invalidates_v1_maps(tmp_path: Path):
    assert MAP_VERSION == 2
    _write(tmp_path, {"a.py": "x = 1\n"})
    stale_v1 = RepoMap(version=1)
    assert stale_files(tmp_path, Config(), stale_v1) == ["a.py"]
