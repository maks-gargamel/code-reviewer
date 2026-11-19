from __future__ import annotations

from code_reviewer.core.patterns import matches_patterns


def test_dotfiles_and_hidden_dirs_are_matched_accurately():
    # Регрессионный тест: lstrip("./") раньше портил .env -> env и .github/ -> github/.
    patterns = [".env", ".github/", ".eslintrc.json"]

    assert matches_patterns(".env", patterns) is True
    assert matches_patterns("backend/.env", patterns) is True
    assert matches_patterns(".github/workflows/ci.yml", patterns) is True
    assert matches_patterns(".eslintrc.json", patterns) is True

    assert matches_patterns("env", patterns) is False
    assert matches_patterns("src/env.py", patterns) is False
    assert matches_patterns("github/workflows/ci.yml", patterns) is False


def test_directory_negation_and_globbing():
    patterns = ["vendor/", "!vendor/keep.py", "*.min.js", "/root_only.py", "src/**/test_*.py"]

    assert matches_patterns("vendor/lib/a.py", patterns) is True
    assert matches_patterns("vendor/keep.py", patterns) is False
    assert matches_patterns("vendor", patterns) is False

    assert matches_patterns("static/app.min.js", patterns) is True
    assert matches_patterns("static/app.js", patterns) is False

    assert matches_patterns("root_only.py", patterns) is True
    assert matches_patterns("sub/root_only.py", patterns) is False

    assert matches_patterns("src/test_one.py", patterns) is True
    assert matches_patterns("src/a/b/test_two.py", patterns) is True
    assert matches_patterns("other/test_one.py", patterns) is False
