# NewChatLearning WebUI Design Brief

## Product

NewChatLearning is a local-first AstrBot operations plugin for QQ group learning, reply libraries, media, migration, scheduled maintenance, TTS, diagnostics, and audit review.

## Audience

The primary user is a bot administrator who repeatedly scans status, changes group-level policy, inspects libraries, and runs high-risk maintenance actions. The interface should optimize for quick recognition, predictable controls, and dense but calm information.

## Design Intent

- Product language: operational, direct, and specific.
- Visual style: restrained dense dashboard rather than a promotional site.
- Theme: neutral graphite surfaces with one teal brand accent and subdued semantic colors.
- Density: 8/10. Preserve breathing room around sections while keeping controls compact.
- Motion: micro-interactions only. No entrance animation, decorative movement, or page transition.
- Signature: a slim accent rail connects the active navigation item with live system status.

## Constraints

- The page remains a single offline HTML file with no CDN or frontend build dependency.
- Preserve existing element IDs, plugin bridge calls, and server-side one-hour session behavior.
- Do not add a password field or per-action password prompt.
- High-risk actions continue to use explicit confirmation dialogs.
- Refresh actions update their region without reloading the page.
- Never expose credentials, chat text, local paths beyond existing safe summaries, or unrelated group IDs.
- Desktop AstrBot embedding is primary; narrow desktop and mobile remain usable.

## Learned Constraints

- Entry is passwordless and the primary entry action is labelled "??".
- The dashboard should resemble a mature API operations console, not a card-heavy marketing template.
- WebUI settings and AstrBot plugin configuration are two views of the same versioned configuration service.
- Beta status must remain visible without dominating the interface.
