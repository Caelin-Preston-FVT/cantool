@echo off
setlocal enabledelayedexpansion

REM === parseCANEdge.bat ===
REM Drag and drop MF4 files or a folder containing MF4 files onto this batch file.
REM Supports:
REM   1. Multiple MF4 files dragged onto the bat
REM   2. A single MF4 file dragged onto the bat
REM   3. A folder containing MF4 files (searched recursively)

set "SCRIPT_DIR=%~dp0"
set "VENV=%SCRIPT_DIR%.venv\Scripts\activate.bat"
set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
set "PARSE_SCRIPT=%SCRIPT_DIR%parseCANEdge.py"

REM Check virtual environment exists
if not exist "%PYTHON%" (
    echo ERROR: Virtual environment not found at %SCRIPT_DIR%.venv
    echo Please run: uv venv .venv ^& uv pip install -r requirements.txt
    pause
    exit /b 1
)

REM Check that arguments were provided
if "%~1"=="" (
    echo ERROR: No files or folder provided.
    echo Drag MF4 files or a folder onto this batch file.
    pause
    exit /b 1
)

REM Determine if the first argument is a directory or a file
if exist "%~1\" (
    REM A folder was dragged — use it directly as the mf4-folder
    echo Folder detected: %~1
    echo Running parseCANEdge.py with --mf4-folder "%~1" ...
    call "%VENV%"
    python "%PARSE_SCRIPT%" --mf4-folder "%~1"
    goto :done
)

REM Files were dragged — collect them into a temp folder using symlinks/copies
set "TEMPDIR=%TEMP%\cantool_mf4_%RANDOM%"
mkdir "%TEMPDIR%" 2>nul

set FILE_COUNT=0

:loop
if "%~1"=="" goto :run
if /i "%~x1"==".mf4" (
    copy "%~1" "%TEMPDIR%\" >nul 2>&1
    set /a FILE_COUNT+=1
    echo Added: %~nx1
) else (
    echo Skipped (not .mf4): %~nx1
)
shift
goto :loop

:run
if %FILE_COUNT%==0 (
    echo ERROR: No .mf4 files found in the dragged items.
    rmdir /s /q "%TEMPDIR%" 2>nul
    pause
    exit /b 1
)

echo.
echo %FILE_COUNT% MF4 file(s) staged.
echo Running parseCANEdge.py with --mf4-folder "%TEMPDIR%" ...
call "%VENV%"
python "%PARSE_SCRIPT%" --mf4-folder "%TEMPDIR%"

REM Clean up temp folder
rmdir /s /q "%TEMPDIR%" 2>nul

:done
echo.
echo Done.
pause
