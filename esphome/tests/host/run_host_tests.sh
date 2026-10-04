#!/usr/bin/env bash
# Compiles the platform-independent forwarder core with the host compiler and
# validates the rendered webhook payloads with Python's json module.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build
"${CXX:-g++}" -std=c++17 -Wall -Wextra -Werror -O1 -fsanitize=address,undefined \
  -o build/test_forwarder_core test_forwarder_core.cpp
./build/test_forwarder_core | python3 check_payloads.py
