"""Сборка автономного исполняемого файла code-reviewer через PyInstaller."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_binary(
    *,
    output_name: str = "code-reviewer",
    dist_dir: Path = ROOT / "dist",
    checksum: bool = False,
    skip_smoke: bool = False,
) -> Path:
    if sys.platform == "win32" and not output_name.lower().endswith(".exe"):
        final_name = f"{output_name}.exe"
    else:
        final_name = output_name

    stem = final_name[:-4] if final_name.lower().endswith(".exe") else final_name
    work_dir = ROOT / "build" / "pyinstaller"
    entry = ROOT / "scripts" / "pyinstaller_entry.py"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--clean",
        "--noconfirm",
        "--name",
        stem,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(work_dir),
        "--collect-submodules",
        "rich._unicode_data",
        "--collect-submodules",
        "pydantic",
        "--collect-data",
        "certifi",
        "--collect-all",
        "tree_sitter",
        "--collect-all",
        "tree_sitter_language_pack",
        str(entry),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)

    binary = dist_dir / final_name
    if not binary.is_file():
        raise FileNotFoundError(f"Ожидаемый бинарник не найден: {binary}")

    if checksum:
        digest = _sha256(binary)
        checksum_file = binary.with_name(f"{binary.name}.sha256")
        checksum_file.write_text(f"{digest}  {binary.name}\n", encoding="utf-8")
        print(f"SHA-256: {digest} -> {checksum_file}")

    if not skip_smoke:
        smoke_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        for args in (["--version"], ["demo"]):
            subprocess.run([str(binary), *args], env=smoke_env, check=True)

    size_mb = binary.stat().st_size / (1024 * 1024)
    print(f"Готово: {binary} ({size_mb:.1f} МБ)")
    return binary


def main() -> None:
    parser = argparse.ArgumentParser(description="Сборка автономного бинарника code-reviewer")
    parser.add_argument(
        "--output-name",
        default="code-reviewer",
        help="Имя итогового бинарного файла (по умолчанию code-reviewer).",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=ROOT / "dist",
        help="Каталог назначения (по умолчанию ./dist).",
    )
    parser.add_argument(
        "--checksum",
        action="store_true",
        help="Создать рядом файл <binary>.sha256.",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Пропустить проверку --version и demo после сборки.",
    )
    args = parser.parse_args()
    build_binary(
        output_name=args.output_name,
        dist_dir=args.dist_dir.resolve(),
        checksum=args.checksum,
        skip_smoke=args.skip_smoke,
    )


if __name__ == "__main__":
    main()
