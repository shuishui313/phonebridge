#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[[:xdigit:]]{8}(-[[:xdigit:]]{4}){3}-[[:xdigit:]]{12}$ ]]; then
    printf 'Usage: %s <booted iPhone Simulator UDID>\n' "$0" >&2
    exit 2
fi

if ! xcrun --find simctl >/dev/null 2>&1; then
    printf 'Select full Xcode and install the iOS Simulator runtime. See docs/apple-simulator.md.\n' >&2
    exit 1
fi

device="$1"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/phonebridge-sim.XXXXXX")"
trap 'rm -rf -- "$build_dir"' EXIT
app="$build_dir/PhoneBridgeFixture.app"
bundle_id="dev.phonebridge.simulator-fixture"
mkdir "$app"

cat > "$app/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDisplayName</key><string>PhoneBridge Fixture</string>
    <key>CFBundleExecutable</key><string>PhoneBridgeFixture</string>
    <key>CFBundleIdentifier</key><string>$bundle_id</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundleName</key><string>PhoneBridgeFixture</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>CFBundleSupportedPlatforms</key><array><string>iPhoneSimulator</string></array>
    <key>LSRequiresIPhoneOS</key><true/>
    <key>MinimumOSVersion</key><string>16.0</string>
    <key>UIApplicationSceneManifest</key>
    <dict>
        <key>UIApplicationSupportsMultipleScenes</key><false/>
        <key>UISceneConfigurations</key><dict/>
    </dict>
    <key>UIDeviceFamily</key><array><integer>1</integer></array>
    <key>UILaunchScreen</key><dict/>
</dict>
</plist>
EOF

sdk="$(xcrun --sdk iphonesimulator --show-sdk-path)"
arch="$(uname -m)"
xcrun --sdk iphonesimulator swiftc -sdk "$sdk" -target "$arch-apple-ios16.0-simulator" \
    -module-name PhoneBridgeFixture -module-cache-path "$build_dir/module-cache" -parse-as-library \
    "$repo_root/simulator/Fixture.swift" -o "$app/PhoneBridgeFixture"
codesign --force --sign - "$app"

xcrun simctl install "$device" "$app"
token="$(uuidgen)"
xcrun simctl launch --terminate-running-process "$device" "$bundle_id" --phonebridge-smoke-token "$token"
data_dir="$(xcrun simctl get_app_container "$device" "$bundle_id" data)"
marker="$data_dir/Documents/phonebridge-smoke-token.txt"

for ((attempt = 0; attempt < 30; attempt++)); do
    if [[ -f "$marker" && "$(cat "$marker")" == "$token" ]]; then
        printf 'PASS: The PhoneBridge fixture reached onAppear in Apple Simulator (%s).\n' "$device"
        exit 0
    fi
    sleep 1
done

printf 'FAIL: The fixture did not write the current run marker within 30 seconds. Check the Simulator.\n' >&2
exit 1
