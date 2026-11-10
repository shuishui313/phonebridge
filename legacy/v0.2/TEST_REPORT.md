# Verification report — Phone Bridge v0.2

## Proven here

- Executed the existing mock-driver unit tests and the added deterministic end-to-end simulated-message workflow tests in Linux.
- The workflow tests verify the command chain (status → open simulated WeChat → observe synthetic screen → prepare message), reject empty message input, and confirm that even with an approval flag, no message can be sent by this implementation.
- `python demo.py` executes with Python's standard library only.

## Not proven

- **Not an Apple iOS Simulator test.** Apple's official iOS Simulator/Xcode requires macOS; this environment is Linux.
- No actual iPhone/iOS 27 screenshot, tap, swipe, app opening, or WeChat message was executed.
- MCP interoperability with Codex/Hark was **not tested**, since the optional MCP Python SDK is not installed in this environment.
- The mock driver does **not** model real WeChat navigation, contact lookup, screen pixels, UI permissions or recipient matching.

## macOS/iOS and hardware validation gate

1. Build a separately reviewed iOS control adapter through lawful, authorized device testing facilities.
2. Verify a read-only connection, screenshots and non-sensitive app navigation on an iOS 27 test device.
3. Validate that recipient matching and message composition do not depend on guessed coordinates.
4. Keep real message sending disabled until independently verified confirmation and a trusted approval channel are added.
5. Test actual MCP calls from Codex, then via a Hark-controlled session. ChatGPT plugin support is a distinct compatibility test.

Do not report the project as production-ready until these tests have actually passed.
