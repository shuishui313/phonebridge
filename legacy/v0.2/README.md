# iPhone Phone Bridge — original proof of concept

**Status: executable MCP interface with a mock phone driver. NOT a real iPhone controller yet.** No code was copied from Mika, Poki, or Pinky. This package uses only the standard Python library for its core and the official MCP Python SDK for the adapter.

## Features

- MCP tools: `phone_status`, `phone_observe`, `phone_open_app`, `phone_tap`, `phone_swipe`, `phone_type_text`, `phone_draft_message`, `phone_confirm_message`.
- Mock device driver and repeatable tests.
- Allowlisted apps, normalized coordinate checks, JSON-lines event log (avoid real sensitive content), send-is-blocked default.
- Driver boundary that can later receive a separately implemented and authorized iOS device connector.

## Install and run

Requires Python 3.11+ and `mcp` 2.x:

```bash
python -m pip install 'mcp>=2,<3'
python -m unittest -v
python server.py
```

The last command starts a stdio MCP process and waits for an MCP client. In Codex, configure the MCP command with the absolute path to `server.py` and Python executable. Hark can drive the Codex session it connects to, but this is **not** a native Hark plugin.

## Real iPhone integration — not implemented

For real iPhone automation, add a new driver implementing `status`, `observe`, `open_app`, `tap`, `swipe`, `type_text`. This requires a verified and authorized Apple-supported or device-specific control channel, most likely Xcode/UI-testing environment on a Mac for an early research prototype. A normal App Store iPhone app cannot freely control arbitrary other apps. Linux cloud servers alone cannot be assumed to provide such a device connection.

Add independent human confirmation and recipient verification before enabling any real send operation. Protect secrets and screen data, and avoid financial or other sensitive apps.

## Proposed next milestones

1. Implement and test an authorized device driver on an actual iOS 27 test phone.
2. Add image capture / OCR or multimodal perception (currently `observe` returns mock text).
3. Add user-controlled approval and audit logging external to the Agent.
4. Run Codex → MCP → iPhone on a local test host; then test from Hark.
5. Test ChatGPT and other MCP hosts separately for their own tool/security capabilities.

## v0.2 mock integration test (2026-11-10)

Run without external dependencies:

```bash
python -m unittest -v
python demo.py
```

See `TEST_REPORT.md` for exact pass/fail boundaries. **This is a deterministic software simulator**, not the Apple iOS Simulator and not a real device. MCP adapter uses `FastMCP` from `mcp.server.fastmcp` but SDK integration could not be run in this Linux environment. No WeChat messages are sent.
