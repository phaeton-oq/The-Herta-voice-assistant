#!/usr/bin/env bash
# Установщик Герты для Linux.
# Запуск:  chmod +x install.sh && ./install.sh
# Аргументы пробрасываются в tools/installer.py, например:
#   ./install.sh --tier voice
#   ./install.sh --tier voice --torch cu121

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo 'The Herta Voice Assistant - подготовка окружения'
echo '================================================'

# --- 1. Ищем подходящий Python ---------------------------------------------
# Проверяем не только наличие, но и версию: 3.10 и ниже не подойдёт.
find_python() {
    for exe in python3.12 python3.11 python3 python; do
        command -v "$exe" >/dev/null 2>&1 || continue
        if "$exe" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            echo "$exe"
            return 0
        fi
    done
    return 1
}

if ! python_exe="$(find_python)"; then
    echo
    echo '[FAIL] Не нашёл Python 3.11 или новее.'
    echo '       Debian/Ubuntu: sudo apt install python3.11 python3.11-venv'
    exit 1
fi
echo "[ OK ] Python $("$python_exe" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"

# --- 2. Виртуальное окружение ----------------------------------------------
# Если рядом уже лежит рабочее окружение, используем его, а не плодим второе.
# Проверяем не наличие папки, а работоспособность: окружение помнит путь к
# своему базовому Python, и если тот исчез, папка на месте, а запустить нельзя.
venv=''
for name in .venv venv; do
    exe="$root/$name/bin/python"
    [ -x "$exe" ] || continue
    if "$exe" -c 'pass' >/dev/null 2>&1; then
        venv="$root/$name"
        break
    fi
    echo "[ !! ] Окружение $name есть, но не запускается — пропускаю его"
done

if [ -z "$venv" ]; then
    venv="$root/.venv"
    create_args=(-m venv)
    if [ -d "$venv" ]; then
        echo '[ .. ] Пересобираю нерабочее окружение .venv'
        create_args+=(--clear)
    else
        echo '[ .. ] Создаю виртуальное окружение .venv'
    fi
    if ! "$python_exe" "${create_args[@]}" "$venv"; then
        echo '[FAIL] Не удалось создать окружение.'
        echo '       Возможно, не хватает пакета python3-venv:'
        echo '       sudo apt install python3-venv'
        exit 1
    fi
fi
echo "[ OK ] Окружение: $venv"

# --- 3. Основная установка -------------------------------------------------
"$venv/bin/python" "$root/tools/installer.py" "$@"
code=$?

if [ "$code" -eq 0 ]; then
    echo
    echo 'Чтобы запускать вручную, сначала активируй окружение:'
    echo "  source $venv/bin/activate"
fi
exit "$code"
