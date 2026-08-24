@echo off
rem Windows equivalent of the engine-free podman-compose parser fixture.
if not "%FAKE_PODMAN_LOG%"=="" echo %*>>"%FAKE_PODMAN_LOG%"
if "%1"=="version" echo podman version 5.5.2& exit /b 0
if "%1"=="--version" echo podman version 5.5.2& exit /b 0
if "%1"=="-v" echo podman version 5.5.2& exit /b 0
if "%1"=="network" if "%2"=="exists" exit /b 0
if "%1"=="ps" echo []
exit /b 0
