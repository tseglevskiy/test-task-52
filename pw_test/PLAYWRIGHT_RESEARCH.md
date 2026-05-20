# Playwright Research Notes

## What is Playwright

Playwright is Microsoft's end-to-end browser automation and testing framework. It has two main modes:

- **Library** — programmatic browser control (navigate, click, fill, screenshot, etc.)
- **Test runner** — full testing framework (`@playwright/test`) with parallel execution, retries, reporters, and fixtures

## Browser Installation

Playwright installs its **own browsers** — it does not use your system browser.

- Run `npx playwright install` to download pinned builds of Chromium, Firefox, WebKit
- Stored in `~/.cache/ms-playwright/` (Linux/Mac) or `%LOCALAPPDATA%\ms-playwright\` (Windows)
- Specific versions matched to the client, separate from any browser you have installed
- WebKit on Windows/Linux is Playwright's own build (no standalone WebKit binary ships for those platforms)

You can connect to a system browser via `connectOverCDP()` if it was launched with remote debugging, but normal usage is fully self-contained.

## Application Hosting

Playwright is **not** responsible for running your app. Your dev server or staging environment must be running separately. Playwright points a browser at it via `baseURL` in config.

Convenience: `webServer` in `playwright.config.ts` can start/stop your dev server automatically around test runs — but it's just a process launcher, not Playwright hosting anything.

## Core Automation APIs

All standard browser actions are supported:

- **Click** — `page.click()` / `locator.click()`
- **Scroll** — `page.mouse.wheel()` / `locator.scrollIntoViewIfNeeded()`
- **Type** — `page.fill()` (fast), `page.type()` (keystroke-by-keystroke)
- **Navigation** — `page.goto()`, `page.goBack()`, `page.goForward()`
- **Screenshots** — `page.screenshot()`, `locator.screenshot()` (element only)
- **Visual diffing** — `expect(page).toHaveScreenshot()` baselines and diffs

## Page Inspection: DOM vs Accessibility Tree

### DOM
`page.content()` — full raw HTML. Every `<div>`, inline style, class name, data attribute. Exhaustive but noisy; mostly layout/styling scaffolding.

### Accessibility Tree
`page.accessibility.snapshot()` — filtered semantic view the browser builds for screen readers. Only includes meaningful nodes: buttons, links, inputs, headings, text — with role, label, and state (checked, disabled, focused). Decorative elements are omitted.

**For AI agents**: the a11y tree is preferred — smaller, structured around intent rather than implementation, maps directly to actions.

## MCP Integration

Playwright ships a first-class MCP server (`@playwright/mcp`) that exposes browser automation as MCP tools. AI agents (Claude, Copilot, etc.) can control a real browser via MCP.

- Uses the same underlying Playwright library and same downloaded browsers — not a separate product
- Architecture: Playwright core → MCP server wraps it → AI agent calls via MCP protocol

### Key source locations

| Path | Purpose |
|------|---------|
| `packages/playwright-core/src/tools/mcp/` | MCP server implementation |
| `packages/playwright-core/src/tools/mcp/config.ts` | Config parsing (`resolveCLIConfigForMCP`) |
| `packages/playwright-core/src/tools/mcp/configIni.ts` | INI config file parsing |
| `packages/playwright-core/src/tools/mcp/program.ts` | CLI entry point (lines 33–144) |
| `packages/playwright-core/src/tools/backend/tools.ts` | Tool registry and `filteredTools()` (lines 75–86) |
| `tests/mcp/` | MCP integration tests |
| `tests/mcp/capabilities.spec.ts` | Capability filtering tests |
| `tests/mcp/config.spec.ts` | Config tests |
| `tests/mcp/config.ini.spec.ts` | INI config tests |

## MCP Tools (Full List)

### Navigation
- `browser_navigate` — navigate to URL
- `browser_navigate_back` — go back in history
- `browser_navigate_forward` — go forward in history
- `browser_reload` — reload current page

### Page Info
- `browser_snapshot` — capture accessibility tree
- `browser_take_screenshot` — screenshot of current page
- `browser_pdf_save` — save page as PDF
- `browser_network_requests` — list network requests since page load
- `browser_network_request` — full details of a single request
- `browser_console_messages` — all console messages

### Interaction
- `browser_click` — click an element
- `browser_type` — type text into element
- `browser_fill` / `browser_fill_form` — fill input / multiple form fields
- `browser_drag` — drag and drop between elements
- `browser_hover` — hover over element
- `browser_press_key` — press a keyboard key
- `browser_press_sequentially` — type key by key
- `browser_select_option` — select dropdown option
- `browser_check` / `browser_uncheck` — checkbox/radio
- `browser_file_upload` — upload files
- `browser_drop` — drop files or data onto element
- `browser_handle_dialog` — accept or dismiss dialogs
- `browser_wait_for` — wait for text or time

### Low-level Mouse/Keyboard
- `browser_mouse_move_xy` — move mouse to coordinates
- `browser_mouse_down` / `browser_mouse_up` — press/release mouse button
- `browser_mouse_wheel` — scroll wheel
- `browser_mouse_click_xy` — click at coordinates
- `browser_mouse_drag_xy` — drag from coordinates
- `browser_keydown` / `browser_keyup` — raw key press/release

### Network
- `browser_network_clear` — clear request log
- `browser_route` — mock requests matching a URL pattern
- `browser_route_list` — list active mocks
- `browser_unroute` — remove a mock
- `browser_network_state_set` — set online/offline

### Cookies & Storage
- `browser_cookie_list/get/set/delete/clear`
- `browser_storage_state` — save cookies + localStorage to file
- `browser_set_storage_state` — restore from file
- `browser_localstorage_list/get/set/delete/clear`
- `browser_sessionstorage_list/get/set/delete/clear`

### Tabs
- `browser_tabs` — list, create, close, or select tabs
- `browser_close` — close current page

### JavaScript
- `browser_evaluate` — run JS expression on page or element
- `browser_run_code_unsafe` — run full Playwright code snippet (RCE-equivalent)

### Testing Helpers
- `browser_verify_element_visible`
- `browser_verify_text_visible`
- `browser_verify_list_visible`
- `browser_verify_value`
- `browser_generate_locator` — generate Playwright locator for an element
- `browser_wait_for`

### Tracing & Video
- `browser_start_tracing` / `browser_stop_tracing`
- `browser_start_video` / `browser_stop_video`
- `browser_video_chapter` — add chapter marker

### UI Debugging
- `browser_highlight` / `browser_hide_highlight` — overlay on elements
- `browser_annotate` — open Playwright Dashboard in annotation mode
- `browser_resume` — resume paused script execution

### Other
- `browser_resize` — resize browser window
- `browser_get_config` — get resolved server config
- `browser_network_clear` / `browser_console_clear`

## Configuring a Tool Subset (Capabilities)

Tools are grouped into capabilities. `core` is always included; everything else must be opted in.

| Capability | Tools included |
|---|---|
| `core` | Base interaction — **always on, cannot be disabled** (~23 tools by default) |
| `core-navigation` | back, forward, reload |
| `core-tabs` | tab management |
| `core-input` | keyboard/text input |
| `network` | request inspection, route mocking, online/offline |
| `storage` | cookies, localStorage, sessionStorage |
| `pdf` | save page as PDF |
| `vision` | coordinate-based mouse tools |
| `devtools` | tracing, video recording |
| `testing` | verify_* tools, generate_locator |
| `config` | browser_get_config |

### How to specify

**CLI:**
```bash
npx @playwright/mcp --caps=network,storage,pdf
```

**Config file (`config.json`):**
```json
{ "capabilities": ["network", "storage", "pdf"] }
```
```bash
npx @playwright/mcp --config config.json
```

**Environment variable:**
```bash
PLAYWRIGHT_MCP_CAPS=network,storage npx @playwright/mcp
```

Filtering logic lives in `packages/playwright-core/src/tools/backend/tools.ts` — all capabilities prefixed `core` are always included; others require explicit opt-in.
