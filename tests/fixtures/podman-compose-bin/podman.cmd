@echo off
rem Windows equivalent of the engine-free podman-compose parser fixture.
if "%1"=="network" if "%2"=="exists" exit /b 0
if "%1"=="ps" echo []
exit /b 0
