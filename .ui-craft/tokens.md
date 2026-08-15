# NewChatLearning WebUI Tokens

## Typography

- UI stack: `Segoe UI`, `Microsoft YaHei UI`, system sans-serif.
- Data stack: `Cascadia Mono`, `SFMono-Regular`, monospace.
- Body: 13-14px; page title: 20px; section title: 15px.
- IDs, versions, timestamps, sizes, and numeric metrics use tabular figures.

## Color

- Canvas: cool graphite-tinted neutral.
- Sidebar: subtly teal-tinted neutral, never full dark.
- Raised surface: white in light mode and a controlled graphite step in dark mode.
- Accent: teal, reserved for primary actions, active navigation, focus, and the primary metric.
- Semantic colors: subdued green, amber, red, and blue; pair color with text or icon.

## Shape

- Inputs and buttons: 6px radius.
- Data cards and framed tools: 8px radius.
- Dialogs: 12px radius.
- Pills are reserved for compact status labels only.

## Spacing

- Base rhythm: 4px and 8px.
- Common steps: 4, 8, 12, 16, 24, 32px.
- Desktop content padding: 24px; dense tables: 8px vertical and 12px horizontal.

## Motion

- Fast feedback: 80ms.
- Standard feedback: 150ms.
- Panel and overlay transitions: 240ms maximum.
- Honor `prefers-reduced-motion`.

## Accessibility

- Minimum interactive height: 36px in dense desktop layouts, 44px on touch layouts.
- Every interactive element has a visible `:focus-visible` state.
- Active navigation exposes `aria-current="page"`.
- Dialogs retain native `<dialog>` focus behavior.
- Loading, success, and error messaging remains available through existing live regions.
