"""MCP 2.x server for the original iPhone Phone Bridge prototype."""
from mcp.server.fastmcp import FastMCP
from bridge import PhoneBridge

app = FastMCP('iPhone Phone Bridge')
bridge = PhoneBridge(log_path='bridge.log')

@app.tool()
def phone_status() -> dict:
    """Get device connection and driver status."""
    return bridge.status()

@app.tool()
def phone_observe() -> dict:
    """Observe current phone state; mock driver returns a text-only state."""
    return bridge.observe()

@app.tool()
def phone_open_app(app_name: str) -> dict:
    """Open an allowlisted app (mock simulation currently)."""
    return bridge.open_app(app_name)

@app.tool()
def phone_tap(x: float, y: float) -> dict:
    """Tap a normalized point on the screen (0 through 1)."""
    return bridge.tap(x, y)

@app.tool()
def phone_swipe(start_x: float, start_y: float, end_x: float, end_y: float) -> dict:
    """Swipe between normalized points on screen (mock simulation currently)."""
    return bridge.swipe(start_x, start_y, end_x, end_y)

@app.tool()
def phone_type_text(value: str) -> dict:
    """Type text into focused input (mock simulation currently)."""
    return bridge.type_text(value)

@app.tool()
def phone_draft_message(recipient: str, message: str) -> dict:
    """Prepare a message and request independent confirmation. Does not send."""
    return bridge.draft_message(recipient, message)

@app.tool()
def phone_confirm_message(approval_id: str, approved: bool = False) -> dict:
    """Consume an approval ID; real sending is intentionally disabled."""
    return bridge.confirm_message(approval_id, approved)

if __name__ == '__main__':
    app.run(transport='stdio')
