@echo off
rem Copyright 2026 Google LLC
rem
rem Licensed under the Apache License, Version 2.0 (the "License");
rem you may not use this file except in compliance with the License.
rem You may obtain a copy of the License at
rem
rem     https://www.apache.org/licenses/LICENSE-2.0
rem
rem Unless required by applicable law or agreed to in writing, software
rem distributed under the License is distributed on an "AS IS" BASIS,
rem WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
rem See the License for the specific language governing permissions and
rem limitations under the License.
rem
rem Windows shim for otel-headers-helper.sh.
rem
rem On Windows, Claude Code always runs `otelHeadersHelper` through cmd.exe, and
rem cmd cannot execute a .sh file -- it exits 0 having produced NO output, so
rem Claude Code silently sends telemetry with no Authorization header and every
rem export is rejected by Cloud Run with no visible error.
rem
rem Point `otelHeadersHelper` at THIS file instead; it locates bash and hands off
rem to otel-headers-helper.sh, which stays the single implementation of the
rem token logic. Quote the path in settings.json if it contains spaces:
rem
rem   "otelHeadersHelper": "\"C:\\path\\to\\otel-headers-helper.cmd\""
rem
rem (./print-settings.sh emits the correct value for you on Windows.)

setlocal

set "HELPER=%~dp0otel-headers-helper.sh"

if not exist "%HELPER%" (
  echo otel-headers-helper: %HELPER% not found. 1>&2
  exit /b 1
)

rem ---- Locate bash ------------------------------------------------------------
rem Note: %ProgramFiles(x86)% is copied to a plain name first -- the literal
rem parentheses break parsing inside a parenthesized block.
set "PF=%ProgramFiles%"
set "PFX86=%ProgramFiles(x86)%"
set "BASH="

for %%B in (
  "%PF%\Git\bin\bash.exe"
  "%PFX86%\Git\bin\bash.exe"
  "%LOCALAPPDATA%\Programs\Git\bin\bash.exe"
) do if not defined BASH if exist "%%~B" set "BASH=%%~B"

rem Fall back to PATH, skipping the WindowsApps WSL stub (which fails with
rem "Windows Subsystem for Linux has no installed distributions").
if not defined BASH (
  for /f "delims=" %%B in ('where bash.exe 2^>nul ^| findstr /v /i "WindowsApps"') do (
    if not defined BASH set "BASH=%%~B"
  )
)

if not defined BASH (
  echo otel-headers-helper: could not find bash.exe. 1>&2
  echo   Install Git for Windows: https://git-scm.com/download/win 1>&2
  exit /b 1
)

"%BASH%" "%HELPER%"
exit /b %errorlevel%
