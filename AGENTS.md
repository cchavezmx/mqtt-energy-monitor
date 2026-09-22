# Repository Guidelines

## Project Structure & Module Organization

This repository contains ESP32 firmware for reading a PZEM-004T v3.0 energy meter and publishing measurements over MQTT.

- `src/main.cpp`: firmware entry point, hardware pin definitions, Wi-Fi/MQTT setup, sensor reads, and MQTT publishing.
- `platformio.ini`: PlatformIO board, framework, and library dependencies.
- `images/`: wiring, implementation, and Home Assistant screenshots referenced by `README.md`.
- `include/`, `lib/`, and `test/`: standard PlatformIO locations for headers, private libraries, and tests. They currently contain only placeholder documentation.

Keep application logic in `src/`; extract reusable components into `lib/<ComponentName>/` with matching headers when `main.cpp` becomes difficult to navigate.

## Build, Test, and Development Commands

Run commands from the repository root:

- `pio run`: compile the `esp32dev` environment and resolve declared dependencies.
- `pio run -t upload`: build and upload firmware to a connected ESP32.
- `pio device monitor -b 9600`: view serial output at the baud rate configured in `setup()`.
- `pio test`: run PlatformIO tests once tests are added under `test/`.
- `pio run -t clean`: remove generated build artifacts.

## Coding Style & Naming Conventions

Use two-space indentation for C++ and place opening braces on the same line. Follow the existing Arduino style: `camelCase` for functions (`reconnectMQTT`), lowercase descriptive names for local variables, and uppercase macros for fixed pin mappings (`PZEM_RX`). Keep MQTT topic names centralized and stable because Home Assistant automations may depend on them. No formatter or linter is configured, so match nearby code and keep changes focused.

## Testing Guidelines

There is currently no automated test suite. At minimum, require `pio run` to pass before submitting changes. For hardware-facing changes, document manual validation: board model, sensor connection, serial output, MQTT topics observed, and Home Assistant behavior. Add future tests under `test/test_<feature>/` using PlatformIO's Unity framework.

## Commit & Pull Request Guidelines

Recent history uses short, imperative summaries such as `Update README.md` and `added electronics schematic`. Prefer concise, specific messages like `Add MQTT reconnect timeout`. Pull requests should explain the motivation, list build/manual test results, link related issues, and include screenshots for dashboard or wiring-documentation changes.

## Security & Configuration

Never commit real Wi-Fi or MQTT credentials. Replace local values with obvious placeholders before committing, and avoid exposing broker addresses or secrets in logs, screenshots, or review artifacts.
