#!/bin/sh
set -eu

REPO="maks-gargamel/code-reviewer"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"
VERSION="${VERSION:-latest}"
LOCAL_BINARY=""
UNINSTALL=0

usage() {
    cat <<'EOF'
Установщик code-reviewer (и алиаса cr) для Linux и macOS.

Использование:
  ./install.sh [опции]

Опции:
  --version <tag>   Версия релиза GitHub (например v0.1.0 или latest)
  --dir <path>      Каталог установки (по умолчанию /usr/local/bin)
  --local <path>    Установить уже собранный локальный бинарник без скачивания
  --uninstall       Удалить code-reviewer и cr из каталога установки
  -h, --help        Показать эту справку
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --version)
            [ "$#" -ge 2 ] || { echo "Ошибка: укажите значение для --version" >&2; exit 2; }
            VERSION="$2"
            shift 2
            ;;
        --dir)
            [ "$#" -ge 2 ] || { echo "Ошибка: укажите значение для --dir" >&2; exit 2; }
            INSTALL_DIR="$2"
            shift 2
            ;;
        --local)
            [ "$#" -ge 2 ] || { echo "Ошибка: укажите путь для --local" >&2; exit 2; }
            LOCAL_BINARY="$2"
            shift 2
            ;;
        --uninstall)
            UNINSTALL=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Неизвестный параметр: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

can_write_dir() {
    dir="$1"
    if [ -d "$dir" ]; then
        [ -w "$dir" ]
        return $?
    fi
    parent=$(dirname "$dir")
    while [ ! -d "$parent" ] && [ "$parent" != "/" ]; do
        parent=$(dirname "$parent")
    done
    [ -w "$parent" ]
}

run_priv() {
    if can_write_dir "$INSTALL_DIR"; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        echo "Ошибка: нет прав на запись в $INSTALL_DIR и sudo не найден." >&2
        echo "Укажите пользовательский каталог: ./install.sh --dir \"$HOME/.local/bin\"" >&2
        exit 1
    fi
}

if [ "$UNINSTALL" -eq 1 ]; then
    removed=0
    for name in code-reviewer cr; do
        target="$INSTALL_DIR/$name"
        if [ -e "$target" ] || [ -L "$target" ]; then
            run_priv rm -f "$target"
            echo "Удалено: $target"
            removed=1
        fi
    done
    if [ "$removed" -eq 0 ]; then
        echo "В $INSTALL_DIR ничего не найдено."
    fi
    exit 0
fi

if [ -n "$LOCAL_BINARY" ]; then
    [ -f "$LOCAL_BINARY" ] || { echo "Ошибка: файл не найден: $LOCAL_BINARY" >&2; exit 1; }
    SOURCE_BIN="$LOCAL_BINARY"
else
    os=$(uname -s)
    arch=$(uname -m)
    case "$os/$arch" in
        Linux/x86_64|Linux/amd64)
            ASSET="code-reviewer-linux-x86_64"
            ;;
        Darwin/arm64|Darwin/aarch64)
            ASSET="code-reviewer-macos-arm64"
            ;;
        *)
            echo "Ошибка: для платформы $os ($arch) нет готового бинарника." >&2
            echo "Установите через uv: uv tool install git+https://github.com/$REPO.git" >&2
            exit 1
            ;;
    esac

    if [ "$VERSION" = "latest" ]; then
        URL="https://github.com/$REPO/releases/latest/download/$ASSET"
    else
        URL="https://github.com/$REPO/releases/download/$VERSION/$ASSET"
    fi

    TMP_DIR=$(mktemp -d)
    trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

    download() {
        src_url="$1"
        dst_file="$2"
        if command -v curl >/dev/null 2>&1; then
            curl -fsSL "$src_url" -o "$dst_file"
        elif command -v wget >/dev/null 2>&1; then
            wget -qO "$dst_file" "$src_url"
        else
            echo "Ошибка: требуется curl или wget для скачивания." >&2
            exit 1
        fi
    }

    echo "Скачивание $ASSET ($VERSION)..."
    SOURCE_BIN="$TMP_DIR/$ASSET"
    download "$URL" "$SOURCE_BIN"

    if download "$URL.sha256" "$TMP_DIR/$ASSET.sha256" 2>/dev/null; then
        expected=$(awk '{print $1}' "$TMP_DIR/$ASSET.sha256" | head -n 1)
        if [ -n "$expected" ]; then
            if command -v sha256sum >/dev/null 2>&1; then
                actual=$(sha256sum "$SOURCE_BIN" | awk '{print $1}')
            elif command -v shasum >/dev/null 2>&1; then
                actual=$(shasum -a 256 "$SOURCE_BIN" | awk '{print $1}')
            else
                actual=""
            fi
            if [ -n "$actual" ] && [ "$actual" != "$expected" ]; then
                echo "Ошибка: не совпала контрольная сумма SHA-256!" >&2
                exit 1
            fi
        fi
    fi
fi

run_priv mkdir -p "$INSTALL_DIR"
tmp_target="$INSTALL_DIR/code-reviewer.new"
run_priv cp "$SOURCE_BIN" "$tmp_target"
run_priv chmod 0755 "$tmp_target"
run_priv mv -f "$tmp_target" "$INSTALL_DIR/code-reviewer"
run_priv ln -sf "$INSTALL_DIR/code-reviewer" "$INSTALL_DIR/cr"

echo "Установлено: $INSTALL_DIR/code-reviewer (и алиас $INSTALL_DIR/cr)"
"$INSTALL_DIR/code-reviewer" --version
