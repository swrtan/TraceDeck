# Dashboard design audit

Status: initial audit complete; Figma comparison complete for the supplied Page 1 frame.
Date: 2026-09-02

## Scope and evidence

This audit reviewed the current local dashboard shell at desktop and 360 px viewport widths, the static HTML, CSS, and JavaScript implementation, and the supplied Figma Page 1 frame (`e6H50Ie9CLyp8QdTPdZGN0`, node `0:1`). The API could not be started in the review environment because the installed Python environment did not include FastAPI, so the captured runtime state was the backend-unavailable state.

## Overall assessment

The visual direction is coherent: a restrained dark terminal/flight-recorder aesthetic, local-only messaging, compact panels, and lifecycle-oriented terminology. The main weakness is operational clarity. When data is unavailable or empty, the dashboard loses most of its information value and does not provide a clear recovery path.

## Findings

## Figma-to-app comparison

The Figma reference defines three distinct desktop surfaces:

- `01 — Home / History`: icon rail, date range, four summary metrics, search/filter controls, history table, and pagination.
- `02 — Turn Detail`: a dedicated detail surface with quality warning, tabs, prompt/response, usage, tool timeline, and observation quality.
- `03 — Analytics`: date range, four summary metrics, token trend, model usage, heavy-token turns, and tool usage.

The coded app currently has a single home surface with lifecycle cards, two collapsible charts, a recent-turn table, and an inline detail section. This creates the following concrete deltas:

### P1 — Core navigation and surface structure are missing

Figma uses a persistent icon rail and separates Home/History, Turn Detail, and Analytics. The app has no navigation rail, no Analytics surface, and no dedicated detail route or page hierarchy.

Recommendation: make the three surfaces explicit in the product information architecture. A lightweight client-side view switch is sufficient for MVP; avoid adding a build framework solely for navigation.

### P1 — History search, filters, date range, and pagination are missing

Figma places search, model/lifecycle/tool/token/duration filters, a date range, and pagination at the center of the Home/History workflow. The app only requests the first 50 turns and exposes Refresh.

Recommendation: prioritize search, date range, lifecycle/model filters, and visible pagination before adding more charts. Keep the API bounded and local.

### P1 — Analytics is designed but not implemented

The Figma Analytics screen communicates trends and comparison: token trend, model usage, high-token turns, and tool usage. The app only has lifecycle and coverage charts, so it cannot answer the primary “where is usage going?” questions shown in the design.

Recommendation: scope Analytics as a separate approved packet with explicit nullable-data behavior, since token coverage is incomplete and unavailable values must not be plotted as zero.

### P1 — Summary metric model differs materially

Figma uses four decision-oriented metrics: total tokens, total turns, median latency, and observation coverage. The app uses eight lifecycle count cards. The current cards are useful for operational health but do not match the design’s analytical hierarchy.

Recommendation: use four primary cards aligned to the Figma home/analytics model, then expose lifecycle counts as a secondary breakdown or filter summary. Keep “Known tokens” and coverage visible rather than presenting missing usage as zero.

### P2 — Turn Detail composition is only partially represented

Figma gives prompt and response equal side-by-side weight, then dedicates full-width sections to usage, timeline, and quality. The app’s inline detail panel has the same broad content categories but lacks the Figma tab structure, metadata emphasis, and full-width quality treatment.

Recommendation: promote detail to a focused view, preserve the quality warning near the title, and use tabs or anchored sections for long content.

### P2 — The Figma reference is desktop-only evidence

The supplied Figma frame is 1440 × 1024 desktop composition. It does not establish mobile behavior, breakpoint rules, touch targets, or responsive table behavior. The app’s 360 px capture therefore remains a separate implementation finding, not a Figma mismatch claim.

### P0 — Backend-unavailable state is not actionable (resolved by P5.1)

Evidence: the header displays `Collector unavailable: Request failed: 404`; the lifecycle and recent-turn areas remain empty or show `Loading…`.

Impact: users cannot tell whether TraceDeck is stopped, misconfigured, still loading, or has no data. The error is implementation-oriented rather than user-oriented.

Recommendation: provide a dedicated unavailable state with a plain-language explanation, last successful refresh time, a retry action, and the local start/status command. Keep the last known data visible when possible.

Resolution evidence: P5.1 adds explicit loading, empty, partial, stale, and unavailable states with retry and last-refresh context. P5.4 exposes the same recovery and capture state in the Setup / Status panel. The evidence above remains the audit-time observation.

### P1 — Summary hierarchy is too flat (resolved by P5.4)

Evidence: the home view gives eight lifecycle cards similar visual weight, including total, completed, active, stale, failed, interrupted, partial, and unknown.

Impact: the user must scan every card to understand whether attention is required.

Recommendation: group the view into attention-needed, activity, and data coverage. Make active/stale/failed states visually primary and move less urgent lifecycle counts into a secondary breakdown.

Resolution evidence: state panels now receive an attention priority for non-healthy states, and the Setup / Status view presents capture state, hook coverage, data directory configuration, and the next action as a dedicated recovery surface.

### P1 — The first-run path is unclear (resolved by P5.4)

Evidence: the only explicit action is `Refresh`; the page does not explain how capture is enabled, how to start the local collector, or how to verify the first event.

Impact: a new user may interpret an empty dashboard as a broken integration.

Recommendation: add a first-run checklist or setup panel that reflects capture status, collector status, hook installation status, and the next concrete action.

Resolution evidence: the Setup / Status panel now shows the checklist items and is reachable from the rail and recovery flow.

### P1 — Mobile table relies on horizontal overflow (resolved by P5.3)

Evidence: at 360 px the recent-turn table keeps a 600 px minimum width, so Duration and Tokens are outside the immediately visible viewport.

Impact: important diagnostic fields are difficult to discover on small screens.

Recommendation: use a responsive row/card layout below the mobile breakpoint, or prioritize Turn, Lifecycle, and Model while exposing the remaining fields in an expandable detail row.

Resolution evidence: P5.3 adds `data-label` values to each History cell and switches rows to a two-column compact card layout below 600 px. The mobile rules remove horizontal overflow while retaining Started, Model, Status, Tokens, and Duration; desktop filters are sticky and numeric columns are right-aligned.

### P2 — Empty charts need an explicit empty state (resolved by P5.2)

Evidence: the chart panels are present but visually empty when API data is unavailable or no turns exist.

Impact: blank space looks like a rendering defect and does not teach the user what the chart will show.

Recommendation: show `No recorded activity yet` with a short explanation and the condition required for the chart to populate.

Resolution evidence: analytics cards render explicit empty and unavailable copy, retain the selected range context, and provide a clear path back to History when there is no data.

### P2 — Status language should be more user-facing and consistent (resolved by P5.2)

Evidence: the UI mixes lifecycle labels such as `completed`, `partial`, and `stale/incomplete` with operational notices and technical wording.

Impact: the distinction between lifecycle completion, data incompleteness, and semantic task success may be misunderstood.

Recommendation: retain the source-accurate lifecycle vocabulary, but add short explanations or tooltips. For example, explain that Completed means a lifecycle signal was received and does not prove the user's goal succeeded.

Resolution evidence: the dashboard maps backend conditions to the documented state vocabulary and keeps request details in supporting text rather than using transport errors as the primary label.

### P2 — Detail rendering has a nullable-data edge case (resolved by P5.2)

Evidence: `app.js` calls `tool.duration.value` after formatting `tool.duration`; if the API returns a null duration object, the detail renderer can throw.

Recommendation: normalize nullable duration values before rendering and cover the null, partial, and unavailable cases with UI tests.

Resolution evidence: the detail view renders unavailable fields explicitly and preserves lifecycle, timing, model, token, and source context when transcript content is missing.

## Accessibility and interaction checks

- The page has useful landmarks, headings, table caption, button labels, and live regions.
- The lifecycle chart has a text summary alongside its visual presentation.
- Status colors are paired with text labels and state copy.
- Mobile table rules keep essential status information readable without requiring horizontal scrolling.
- Error, loading, empty, stale, and partial states have distinct text and focus behavior. Screen-reader announcements were not validated with a dedicated assistive-technology pass.

## Evidence limits

The original review could not validate populated API data, real turn-detail interaction, keyboard focus order in every state, screen-reader announcements, or network freshness. P5.4 subsequently validated Setup / Status, keyboard shortcuts, Detail navigation, and an empty browser console; populated-data and dedicated screen-reader validation remain outside this pass. The Figma comparison is based on the supplied Page 1 frame and metadata; component-level behavior, prototype interactions, and mobile variants were not available from that frame.

## Recommended order

1. Completed: design and implement backend-unavailable, loading, and empty-data states (P5.1).
2. Completed: rework summary hierarchy and first-run setup around attention and coverage (P5.4).
3. Completed: make recent turns usable on mobile (P5.3).
4. Completed: harden nullable detail rendering and analytics empty states (P5.2).
5. Next: continue with the durable time/retention contract and its verification (P6.1).
