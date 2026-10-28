# Changelog

All notable changes are listed here. Format: [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]
- First public release of the history; personal network details scrubbed.

## [0.1.0] - 2026-10-20
- Reads over SSH and matron's REPL: status, scripts, current script, params, logs, dust inventory.
- Gated writes (dry run unless `--live` and `confirm=true`): load/start/stop, params, keys, encoders, screenshot.
- Safe rsync deploy: dry run first, backup on the device, no `--delete`, SHA-256 check.
