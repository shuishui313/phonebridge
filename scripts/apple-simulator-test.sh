#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 || ! "$1" =~ ^[[:xdigit:]]{8}(-[[:xdigit:]]{4}){3}-[[:xdigit:]]{12}$ ]]; then
  printf 'Usage: %s <iPhone Simulator UDID>\n' "$0" >&2
  exit 2
fi
if [[ "$(uname -s)" != 'Darwin' ]]; then
  echo 'ERROR: Apple Simulator requires macOS with full Xcode.' >&2
  exit 2
fi
for cmd in xcodebuild xcrun; do
  command -v "$cmd" >/dev/null || { echo "ERROR: missing $cmd" >&2; exit 2; }
done
xcrun --find simctl >/dev/null || { echo 'ERROR: full Xcode / simctl not selected' >&2; exit 2; }
command -v xcodegen >/dev/null || { echo 'ERROR: missing xcodegen' >&2; exit 2; }

udid="$1"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$repo_root/build"
run_dir="$(mktemp -d "$repo_root/build/ios-ui.XXXXXX")"
result_bundle="$run_dir/PhoneBridgeUITests.xcresult"
xcodegen generate --spec "$repo_root/simulator/project.yml" --project "$run_dir"
xcodebuild -version
xcrun simctl bootstatus "$udid" -b
printf 'XCTest result bundle: %s\n' "$result_bundle"
xcodebuild -project "$run_dir/PhoneBridgeFixture.xcodeproj" \
  -scheme PhoneBridgeFixture \
  -destination "platform=iOS Simulator,id=$udid" \
  -parallel-testing-enabled NO \
  -derivedDataPath "$run_dir/DerivedData" \
  -resultBundlePath "$result_bundle" \
  test
printf 'APPLE SIMULATOR XCTEST PASS: %s\n' "$udid"
