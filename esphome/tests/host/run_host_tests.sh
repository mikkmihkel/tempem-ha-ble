#!/usr/bin/env bash
# Compiles the platform-independent forwarder core with the host compiler and
# validates the rendered webhook payloads with Python's json module.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build
"${CXX:-g++}" -std=c++17 -Wall -Wextra -Werror -O1 -fsanitize=address,undefined \
  -o build/test_forwarder_core test_forwarder_core.cpp
./build/test_forwarder_core | python3 check_payloads.py
"${CXX:-g++}" -std=c++17 -Wall -Wextra -Werror -O1 -fsanitize=address,undefined \
  -o build/test_webhook_url test_webhook_url.cpp
./build/test_webhook_url
# Again with unsigned char, as on the ESP32-C6 (RISC-V).
"${CXX:-g++}" -std=c++17 -Wall -Wextra -Werror -O1 -fsanitize=address,undefined -funsigned-char \
  -o build/test_webhook_url_unsigned test_webhook_url.cpp
./build/test_webhook_url_unsigned
