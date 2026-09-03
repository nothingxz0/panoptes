#!/usr/bin/env bash
# Panoptes launcher. Runs the package from the project root.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
exec python3 -m panoptes "$@"
