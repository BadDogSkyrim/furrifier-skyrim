@echo off
rem Rebuilds bc7enc_wrapper.dll and copies it into the furrifier
rem package (src/furrifier/facegen/_bc7enc.dll). Run from any cwd.
rem
rem Requires Visual Studio 2022 Community + C++ build tools. The
rem vcvarsall.bat path below is the default install location; adjust
rem if your VS lives elsewhere.

setlocal
set HERE=%~dp0
set SRC=%HERE%bc7enc
set OUT_DLL=%HERE%bc7enc\bc7enc_wrapper.dll
set PKG_DLL=%HERE%..\src\furrifier\facegen\_bc7enc.dll
set VCVARS="C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat"

if not exist %VCVARS% (
    echo ERROR: VS2022 vcvarsall.bat not found at %VCVARS%
    exit /b 1
)

pushd "%SRC%"
call %VCVARS% x64 >nul
if errorlevel 1 ( popd & exit /b 1 )

cl /nologo /O2 /LD bc7enc.c bc7enc_wrapper.c /Febc7enc_wrapper.dll
if errorlevel 1 ( popd & exit /b 1 )

copy /Y bc7enc_wrapper.dll "%PKG_DLL%" >nul
if errorlevel 1 ( popd & exit /b 1 )

popd
echo Built %OUT_DLL%
echo Copied to %PKG_DLL%
endlocal
