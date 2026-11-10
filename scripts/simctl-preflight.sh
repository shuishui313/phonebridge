#!/usr/bin/env bash
set -euo pipefail
[[ "$(uname -s)" == 'Darwin' ]] || { echo 'This command requires macOS' >&2; exit 2; }
xcode-select -p
xcodebuild -version
xcrun --find simctl
xcrun simctl list devices available
