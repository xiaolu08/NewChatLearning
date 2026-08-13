# NewChatLearning Engineering Guide

## Project Scope

NewChatLearning is a Beta-stage AstrBot plugin that ports ChatLearning to Windows, NapCat, OneBot v11, and QQ group chats.

## Required Reading

Before changing behavior or architecture, read:

- `README.md`
- `docs/product/requirements.md`
- `docs/architecture/system-design.md`
- `docs/architecture/data-model.md`
- `docs/compatibility/napcat-messages.md`
- `docs/security/webui-security.md`
- `docs/testing/beta-acceptance.md`

## Engineering Rules

- Preserve the zero-LLM-token core learning and reply pipeline.
- Keep the first usable Beta aligned with the documented original-feature parity target.
- Treat Windows + AstrBot + NapCat as the initial supported environment.
- Never commit `.cl` samples, runtime databases, downloaded media, backups, logs, or credentials.
- Use restricted parsing for legacy pickle data; never load unknown `.cl` files directly in the plugin process.
- Keep WebUI, chat commands, and AstrBot plugin configuration on one versioned configuration service.
- Record security-sensitive, destructive, import, permission, and configuration operations in the audit log.
- Maintain AGPL-3.0 attribution and the notices in `NOTICE`.
- Keep README, metadata, and releases marked Beta until the maintainer explicitly approves a formal release.

## Documentation

- Community and contributor documentation belongs under `docs/`.
- Update `docs/README.md` when adding, renaming, replacing, or removing a document.
- Distinguish confirmed requirements, implementation decisions, research evidence, and unresolved release items.
- Use professional project terminology and avoid internal brainstorming or workflow-template language in community-facing files.

## Verification

- Add focused tests for each implementation change.
- Check all Markdown links after documentation moves.
- Confirm ignored runtime and legacy data remain untracked before every release-related commit.
- Do not claim feature completion until the corresponding item in `docs/testing/beta-acceptance.md` is verified.
