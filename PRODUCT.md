# Product

## Register

product

## Users

Developers using coding agents and terminal workspaces who want a physical
notification when an agent needs input or finishes responding. They configure
the tool locally on macOS or Linux, often through a piped install command.

## Product Purpose

Connect Tidbyt or compatible Tronbyt displays to agent activity through a
harness-independent notification command. Successful onboarding ends with a
verified device, privately saved credentials, explicitly chosen hooks, and an
optional test notification.

## Brand Personality

Friendly, precise, restrained. The installer should feel like a Clack-style
developer wizard: compact, guided, and reassuring without marketing language.

## Anti-references

Avoid oversized ASCII banners, neon decoration, scrolling walls of settings,
unexplained raw configuration names, and elaborate full-screen interfaces.
Do not turn a short configuration task into a dashboard or hide progress
behind decorative animation.

## Design Principles

- Show the next decision, its default, and the reason it matters.
- Give explicit feedback for validation, network checks, and saved changes.
- Review meaningful settings before writing credentials or installing hooks.
- Preserve user control: no silent dependency installs or unrelated changes.
- Share one delivery engine across harnesses; no Claude-only product identity.

## Accessibility & Inclusion

Support arrow-key selection with numbered/text fallback. Respect `NO_COLOR`,
`TERM=dumb`, narrow terminals, and an explicit plain mode for screen readers
and simpler terminals. Convey states with text as well as color. Avoid
animation, preserve native terminal colors, restore terminal settings after
interruption, and never echo secrets or include them in summaries.
