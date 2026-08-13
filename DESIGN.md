# AI Support Workbench Design

## Mode

Operate. This is a high-frequency internal customer-support console. The primary quality bar is scanability, explicit state, and low cognitive load during ticket handling.

## Visual World

Registration desk / shift console: an ink-dark navigation rail and matte paper-white work surface, with compact registration marks, hairline rules, and small uppercase labels that make operational state feel recorded rather than decorative. The visual language is restrained for daily use: teal is reserved for executable actions and healthy progress, amber for review and attention, red for destructive or failed states.

## Palette

- Ink: #172126 for navigation and high-contrast text.
- Paper: #f6f8f7 for the application surface.
- Panel: #ffffff for framed tools and detail sections.
- Rule: #dbe3e1 for separators and input borders.
- Teal: #0d8a7b for primary actions and active navigation.
- Amber: #c98627 for review and warning states.
- Red: #c94a4a for errors and destructive actions.

## Composition

Desktop uses a stable 240px navigation rail and a wide content canvas. Workbench pages lead with an eyebrow, title, summary metrics, then filters and dense tables. Ticket details use a two-column workspace with an activity timeline and an action panel. Mobile collapses the rail into a top bar and converts wide tables into stacked rows while keeping actions reachable.

## Typography

System sans stacks provide familiar enterprise reading at a fixed, compact scale. Uppercase micro-labels use modest tracking for registration-mark character; body copy keeps comfortable line-height and bounded measure.

## Interaction Rules

Use icon-plus-label buttons for primary operations, icon-only buttons only with accessible names, and a shared status vocabulary that never depends on color alone. Poll asynchronous jobs every three seconds and stop polling on navigation/unmount. Destructive actions always require confirmation.
