@echo off
rem Windows 一键构建 ret2console 单文件版（Nuitka onefile）。
rem 构建逻辑在 build.ps1（PowerShell 5.1 为 Windows 自带），此处仅做入口转发。
rem 用法: scripts\build.bat   产物: build\win\ret2console.exe
setlocal
cd /d "%~dp0.."

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
exit /b %errorlevel%
