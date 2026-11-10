# Apple iOS Simulator smoke test

The smoke test builds the project's SwiftUI fixture, installs and launches it in an explicitly selected iPhone Simulator, and checks that its first view reaches SwiftUI's `onAppear` callback. The view writes a fresh marker for each run so an earlier launch cannot satisfy the check. The fixture runs independently of the MCP server and the local phone simulation.

## Set up Xcode

1. Install and open full Xcode on macOS. Select it in Xcode → Settings → Locations → Command Line Tools. Complete the first-launch setup.
2. In Xcode → Settings → Components, install iOS platform support and an iOS Simulator runtime (iOS 16 or later). Add an iPhone in Xcode's Devices and Simulators window if needed. [Apple's Xcode components guide](https://developer.apple.com/documentation/xcode/downloading-and-installing-additional-xcode-components)
3. In Terminal, check the selected developer directory and list available devices. [Apple's command-line tool reference](https://developer.apple.com/documentation/xcode/xcode-command-line-tool-reference)

   ```sh
   xcode-select -p
   xcrun --find simctl
   xcrun simctl list devices available
   ```

## Run the smoke test

From the repository root, replace the placeholder with the UDID of the chosen iPhone. Skip `boot` if it is already booted.

```sh
SIM_UDID='replace-with-iphone-simulator-udid'
xcrun simctl boot "$SIM_UDID"
xcrun simctl bootstatus "$SIM_UDID"
./scripts/simulator-smoke.sh "$SIM_UDID"
```

Record the `PASS` line, the iPhone model, the iOS runtime, and `xcodebuild -version` when reporting a completed run. For a visual check, capture the fixture screen with `xcrun simctl io "$SIM_UDID" screenshot phonebridge-fixture.png` and confirm it shows “PhoneBridge” and “Simulator fixture ready”. [Apple's Simulator screenshot guide](https://developer.apple.com/library/archive/documentation/IDEs/Conceptual/iOS_Simulator_Guide/InteractingwiththeiOSSimulator/InteractingwiththeiOSSimulator.html)

This result covers the fixture's build, installation, launch, and first view lifecycle on the selected Simulator. Keep local simulation results, Apple Simulator smoke results, and physical iPhone results as separate entries in the test report. Testing MCP-driven iOS behavior would require a bridge to the Simulator fixture.
