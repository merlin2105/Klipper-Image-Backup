---
name: Klipper Image Backup Manager
description: Read-only Klipper host diagnosis with backup readiness made clear first.
colors:
  canvas: "#111514"
  navigation-rail: "#151a18"
  panel: "#191f1d"
  panel-raised: "#1d2522"
  separator: "#2d3632"
  text: "#e7ece7"
  muted: "#929d96"
  readiness-lime: "#c5ee78"
  caution-amber: "#e7b667"
  blocked-red: "#ed7a6d"
typography:
  display:
    fontFamily: "Georgia, Times New Roman, serif"
    fontSize: "clamp(38px, 4vw, 54px)"
    fontWeight: 500
    lineHeight: 1.06
    letterSpacing: "-0.04em"
  body:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 400
  data:
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    letterSpacing: "0.08em"
rounded:
  xs: "3px"
  sm: "5px"
  md: "7px"
  panel: "8px"
  mark: "10px"
spacing:
  compact: "8px"
  control: "12px"
  panel: "18px"
  section: "28px"
  page: "48px"
components:
  action-recheck:
    backgroundColor: "#293326"
    textColor: "#d8e6ce"
    rounded: "{rounded.sm}"
    padding: "0 13px"
    height: "36px"
  status-strip:
    backgroundColor: "#20291f"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "12px 16px"
  data-panel:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    rounded: "{rounded.panel}"
    padding: "18px 20px"
---

# Design System: Klipper Image Backup Manager

## Overview

**Creative North Star: “Sicherheitsüberblick zuerst”**

KIBM is a restrained operations surface for checking and backing up a Klipper host safely. The page makes the protected system card, current host status, and backup readiness legible before presenting system and storage details. PC mode downloads a full raw image. USB mode writes a full image by default and offers optional Pi-side shrinking with an explicit full-image retention choice.

Near-black green-tinted backgrounds and charcoal panels keep dense measurements calm. Lime marks confirmed or interactive readiness cues; amber carries caution, and red marks blocked capabilities. A quiet Georgia display face gives the page a human entry point while compact monospace labels make machine values easy to scan.

**Key Characteristics:**
- Protected source status and backup readiness precede system details.
- Destination controls distinguish full PC downloads from optional USB shrinking.
- Dense host measurements sit in quiet, separated panels.
- Status colors communicate readiness, caution, and blocked capability.

## Colors

The palette is dark and green-tinted, with restrained semantic accents for readiness and safety state.

### Primary
- **Readiness Lime** (`{colors.readiness-lime}`): Marks confirmed readiness, key status indicators, and the recheck affordance.

### Secondary
- **Caution Amber** (`{colors.caution-amber}`): Signals unknown or cautionary host and printer conditions.

### Tertiary
- **Blocked Red** (`{colors.blocked-red}`): Marks unavailable capabilities in the readiness panel.

### Neutral
- **Night Canvas** (`{colors.canvas}`): Main page background.
- **Navigation Charcoal** (`{colors.navigation-rail}`): Persistent desktop rail and mobile brand bar.
- **Panel Charcoal** (`{colors.panel}`): Data cards and readiness surfaces.
- **Raised Charcoal** (`{colors.panel-raised}`): Inset notices and secondary surface emphasis.
- **Quiet Separator** (`{colors.separator}`): Panel borders, rules, and table divisions.
- **Soft Text** (`{colors.text}`): Primary copy on dark surfaces.
- **Muted Text** (`{colors.muted}`): Supporting descriptions and secondary facts.

**The State Color Rule.** Use lime, amber, and red to convey actual state; never present an unknown or unavailable state as ready.

## Typography

**Display Font:** Georgia (with Times New Roman, serif)
**Body Font:** System UI (with platform sans-serif fallbacks)
**Label/Mono Font:** ui-monospace (with SFMono-Regular, Consolas, monospace)

**Character:** Georgia gives the primary headline a quiet editorial tone. System UI keeps explanatory text familiar, while monospace distinguishes measured values, compact labels, and operational metadata.

### Hierarchy
- **Display** (500, responsive 38–54px, line-height 1.06): The page title and login welcome; use the restrained negative tracking shown in the implementation.
- **Headline** (600, 15px): Section headings and prominent panel titles.
- **Title** (600, 14px): Device, printer, and readiness card titles.
- **Body** (400, 12–14px, line-height up to 1.75): Explanations, facts, and supporting interface copy.
- **Label** (400, 12px, 0.08em tracking, uppercase where shown): Status tags, table headings, and operational metadata.

**The Measurement Voice Rule.** Keep machine measurements and compact status labels in monospace; keep explanations in the body face.

## Layout

On wide screens, a fixed 232px navigation rail anchors a centered content area capped at 1600px. The content uses 48px horizontal padding, growing to 64px on very wide viewports; around 1000px it reduces to 27px and the storage layout becomes a single column. At 680px and below, navigation becomes a 58px brand bar, content has 17px gutters, and system cards stack. The desktop system cards form two columns; storage pairs a summary and partition panel until the narrow layout stacks them. The first viewport orders the page title, overall read-only host status, backup readiness and safe recheck action, then system details. Storage and its scrollable partition table follow. Common gaps recur at 8–18px within components and 28–34px between major sections.

**The Readiness-First Rule.** Place overall host state and backup blockers before system and storage facts; preserve the safe recheck action beside its explanation.

## Elevation & Depth

Depth comes from tonal layers, fine borders, and subtle status gradients rather than prominent drop shadows. The status strip uses a restrained horizontal charcoal-green gradient; panels remain visually grounded against the canvas.

**The Quiet Surface Rule.** Separate panels with tonal contrast and thin borders; keep elevation understated so status remains the strongest signal.

## Shapes

Cards use gently rounded corners (8px), navigation links and status strips use compact rounding (7px), and controls use smaller corners (5px). Thin separators define table rows and panel sections. The compact brand mark uses a small rounded square and a rotated geometric four-cell motif; icons are consistent outlined SVGs.

## Components

### Buttons
- **Character:** Compact, low-profile actions with a clear lime readiness cue.
- **Primary:** Lime filled login action with dark text, 36px minimum height, and 18px horizontal padding.
- **Secondary:** Recheck action uses charcoal-green fill, restrained green border, 5px corners, and 0 13px padding.
- **Hover / Focus:** Hover shifts the secondary action to a brighter green surface; text links brighten toward lime. The password field shows a green border and visible offset outline on focus.

### Cards / Containers
- **Corner Style:** Gently rounded panels (8px).
- **Background:** Panel charcoal on the night canvas, with raised charcoal for nested notices.
- **Border:** Fine separator stroke; panel structure remains visible without heavy shadow.
- **Internal Padding:** Commonly 18–20px desktop and 16px on mobile; readiness panel uses 21–22px desktop and 17px mobile.

### Inputs / Fields
- **Style:** Dark inset field with a quiet border, 5px corners, and 42px height.
- **Focus:** Green border plus a visible 2px offset outline.

### Navigation
- **Style:** Desktop uses a fixed vertical rail with compact links and a subtly filled active row marked by a narrow lime edge. At mobile widths, it collapses to the brand bar and the links are hidden.

### Status and Readiness
The overall status strip pairs a semantic state dot with a short title, explanatory sentence, and right-aligned host identifier. The readiness panel lists concrete blockers and provides a recheck action. Backup controls show the PC or USB destination, optional PiShrink, full-image retention, and a separate destructive USB-format confirmation. Use live reported values only; unknown and unavailable conditions keep their explicit labels.

### Storage Summary and Partitions
The storage summary combines detected device facts, measured filesystem usage, and an estimate only when the service provides one. The partition table preserves compact monospace device paths and may scroll inside its labeled region on mobile; the surrounding page remains within the viewport.

## Do's and Don'ts

### Do:
- **Do** communicate that the running system card remains unchanged and show current backup readiness before technical detail.
- **Do** explain each blocker in plain language and keep the safe prerequisite recheck visible.
- **Do** show host, filesystem, and partition values only when supplied by the live service.
- **Do** let the partition table scroll within its own labeled region on narrow screens.
- **Do** retain the restrained Georgia headline, system body text, and monospace measurement roles.

### Don't:
- **Don't** imply that recovery boot or restore is available.
- **Don't** fill missing measurements with sample values or style unknown status as confirmed readiness.
- **Don't** let dense storage detail displace the readiness summary in the page hierarchy.
