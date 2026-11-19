# KIBM Dashboard Surface Brief

## Direction contract

**THESIS:** Establish whether the Klipper host is safe and backup-ready before asking the user to interpret storage details. Keep the current installation protected.

**OWN-WORLD:** A dark, restrained operations surface: near-black green-tinted canvas, layered charcoal panels, thin separators, lime for confirmed readiness, amber for caution, and red for blocked capabilities. System measurements use compact data typography; the main title uses a quiet serif display face. Icons stay geometric and consistent.

**STORY:** The user sees the actual host state, understands why backup preparation is blocked, can rerun the prerequisite check, and can inspect the live OS, Moonraker, disk, filesystem, and partition values. Unknown states remain blocked and explain the reason.

**FIRST VIEWPORT:** Under the navigation and page title, show the read-only host status, then a compact backup-readiness panel with current blocker causes and a “Voraussetzungen erneut prüfen” action. System and printer details follow; storage facts and the complete partition table remain below.

**FORM:** “Sicherheitsüberblick zuerst” (readiness-first), first of the assigned surface roll. Form seed key `0f000bf7`, recorded as the choice source by the `concept-seed --kind assigned --from 0f000bf7 --scope surface --mode operate` telemetry.

**QUALITY BAR:** The first view quickly communicates safety and backup readiness. A non-specialist can identify the cause of a block and the next safe action. System detail remains available without fabricated sample values. At 390 px the page has no horizontal overflow; the partition table may scroll inside its labeled region. No backup, reboot, or boot-file modification is implied.

**FINISH:** unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
