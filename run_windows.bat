@echo off
chcp 65001 > nul
title Smart Photo Deduplication Manager

python "%~dp0photo_dedup.py" %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Press any key to exit / לחץ על מקש כלשהו ליציאה...
    pause > nul
)
