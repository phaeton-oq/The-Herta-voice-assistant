# Установщик Герты для Windows.
# Запуск:  powershell -ExecutionPolicy Bypass -File .\install.ps1
# Аргументы пробрасываются в tools\installer.py, например:
#   .\install.ps1 --tier voice
#   .\install.ps1 --tier voice --torch cu121

param([Parameter(ValueFromRemainingArguments = $true)] $InstallerArgs)

# Намеренно НЕ 'Stop': скрипт целиком состоит из вызовов внешних программ.
# При 'Stop' любая строчка, которую они напишут в stderr, превращается в
# терминирующую ошибку и роняет установку — даже когда программа отработала
# нормально. Успех проверяем явно, по $LASTEXITCODE.
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot

Write-Host 'The Herta Voice Assistant - подготовка окружения'
Write-Host '================================================'

# --- 1. Ищем подходящий Python ---------------------------------------------
# Проверяем не только наличие, но и версию: 3.10 и ниже не подойдёт,
# в коде используются синтаксис и типы из 3.11.
function Find-Python {
    $candidates = @(
        @{ Exe = 'py';     Args = @('-3.12') },
        @{ Exe = 'py';     Args = @('-3.11') },
        @{ Exe = 'py';     Args = @('-3')    },
        @{ Exe = 'python'; Args = @()        }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        # Мало найти команду — надо убедиться, что интерпретатор реально
        # запускается. У py.exe в реестре могут остаться записи о версиях,
        # которых на диске давно нет, и тогда он падает при запуске.
        #
        # В пробнике намеренно нет кавычек: PowerShell теряет их при передаче
        # аргумента нативной программе, и Python получает сломанный код.
        # Поэтому версия отдаётся одним числом: 3.11 -> 311.
        $probe = @($c.Args) + @('-c', 'import sys; print(sys.version_info[0]*100+sys.version_info[1])')
        $raw = $null
        try { $raw = (& $c.Exe @probe 2>$null | Select-Object -Last 1) } catch { }
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($raw)) { continue }
        $code = 0
        if (-not [int]::TryParse("$raw".Trim(), [ref]$code)) { continue }
        if ($code -ge 311) {
            $shown = '{0}.{1}' -f [math]::Floor($code / 100), ($code % 100)
            return @{ Exe = $c.Exe; Args = $c.Args; Version = $shown }
        }
    }
    return $null
}

$python = Find-Python
if ($null -eq $python) {
    Write-Host ''
    Write-Host '[FAIL] Не нашёл Python 3.11 или новее.'
    Write-Host '       Скачать: https://www.python.org/downloads/'
    Write-Host '       При установке обязательно отметь "Add python.exe to PATH".'
    exit 1
}
Write-Host "[ OK ] Python $($python.Version)"

# --- 2. Виртуальное окружение ----------------------------------------------
# Если рядом уже лежит рабочее окружение, используем его, а не плодим второе.
# Но именно рабочее: окружение помнит путь к своему базовому Python, и если
# тот жил на диске, которого больше нет, папка на месте, а запустить её нельзя.
$venv = $null
foreach ($name in @('.venv', 'venv')) {
    $candidate = Join-Path $root $name
    $exe = Join-Path $candidate 'Scripts\python.exe'
    if (-not (Test-Path $exe)) { continue }
    $probe = $null
    try { $probe = (& $exe -c 'print(1)' 2>$null | Select-Object -Last 1) } catch { }
    if ($LASTEXITCODE -eq 0 -and "$probe".Trim() -eq '1') {
        $venv = $candidate
        break
    }
    Write-Host "[ !! ] Окружение $name есть, но не запускается — пропускаю его"
}

if ($null -eq $venv) {
    $venv = Join-Path $root '.venv'
    $createArgs = @($python.Args) + @('-m', 'venv')
    if (Test-Path $venv) {
        # Папка есть, но рабочего интерпретатора в ней нет — пересобираем.
        Write-Host '[ .. ] Пересобираю нерабочее окружение .venv'
        $createArgs += '--clear'
    } else {
        Write-Host '[ .. ] Создаю виртуальное окружение .venv'
    }
    $createArgs += $venv
    & $python.Exe @createArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[FAIL] Не удалось создать окружение.'
        exit 1
    }
}
Write-Host "[ OK ] Окружение: $venv"

$venvPython = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host "[FAIL] В окружении нет python.exe: $venvPython"
    exit 1
}

# --- 3. Основная установка -------------------------------------------------
$installer = Join-Path $root 'tools\installer.py'
$runArgs = @($installer)
if ($InstallerArgs) { $runArgs += $InstallerArgs }

& $venvPython @runArgs
$code = $LASTEXITCODE

if ($code -eq 0) {
    Write-Host ''
    Write-Host 'Чтобы запускать вручную, сначала активируй окружение:'
    Write-Host "  $venv\Scripts\Activate.ps1"
}
exit $code
