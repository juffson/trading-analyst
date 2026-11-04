# Quant Lab interface system

The application uses an industrial research-terminal layout: a narrow navigation rail, a stable page heading, and a task-oriented content canvas. Native GPUI and embedded HTML pages share the same visual scale and palette.

## Foundations

- Background `#10151C`; rail `#131A22`; surface `#171E27`; border `#2B3542`.
- Primary text `#E6EDF3`; secondary `#9CAABC`; muted `#7C8B9D`; action accent `#B8DE8F`.
- SF Pro Display / PingFang SC retained for continuity with the existing macOS application. Numeric data uses tabular figures. This deliberately overrides the design skill's default font prohibition.
- Page heading 23px, section heading 13–17px, body 12–13px, metadata 10–11px. Native base text is 13px.
- 204px navigation rail; 20–28px content inset; 8px surface corners; 34–38px controls.
- Lucide icons are vendored locally with their license. Research chart colors retain distinct semantic series, with a rose lower-scenario line.

## Information hierarchy

Navigation groups research (company, daily close, archive, factors, backtest), trading (paper, live, review), and connection settings. Each page has a location label, a title, and a short task description.

Company analysis and trade review place their creation form above a history column and an active work area. Workflow, terminal, and report share that work area. Compare reports remain below the workspace.

Settings use accessible tabs for market credentials, AI providers, and local CLI. Fields and saved configuration handlers remain in their original forms.

Backtesting reserves a parameter column and a results canvas. Before a result exists, instructions replace empty charts. Research archive uses its existing historical-data dashboard with the shared typography and surfaces.

## Implementation

`static/app-theme.css` owns the shared visual theme and page layouts. It loads after existing component styles and `research-dashboard.css`. `static/app-shell.js` owns settings tab selection and keyboard navigation. Existing business handlers remain in `index.html`. No framework rewrite or external font request is required.

GPUI mirrors the rail width and palette in `desktop/src/main.rs`; the embedded webview offset is kept in sync in `desktop/src/embedded.rs`. Asset-version detection includes all local theme/dashboard entry files so changes are loaded on navigation.

Daily close adds a three-market schedule strip, dated report table, and repository publication history. It reuses the existing automation configuration and public research archive; status limitations and refresh behavior are documented in `daily-close.md`. Internal webview navigation reports the current route to the native rail so report drill-down keeps its selected item in sync.
