# Persistent command wheel and focused UI polish

Follow-up to `f126cd0322bdcb528fb448281783bfefbdf12301` on
`codex/insightpilot-redesign`. This change only adjusts navigation presentation,
its event handling, and the requested home copy. It does not replace the app.

## Current behavior

- **Desktop home:** the complete ten-sector wheel stays in the home hero. The
  heading is exactly **Data Analysis**.
- **Desktop analytical pages:** a compact wheel remains fixed in a reserved
  right-hand rail, including while scrolling long pages. All ten sectors remain
  selectable. Icons, the active state, a readable destination caption, hover/focus
  tooltips, and an **Expand wheel** control keep it recognizable and usable.
- **Expanded desktop wheel:** full destination labels appear immediately. The
  content area reserves a wider rail instead of placing the wheel over charts,
  tables, filters, or forms. **Collapse wheel** returns to the compact version.
- **Screens at or below 1100px:** a bottom dock keeps the wheel one tap away with
  **Open wheel**. Expansion shows all ten readable, selectable sectors. The main
  scroll viewport and sidebar reserve space for both the compact and expanded
  dock; content can scroll within the remaining area, without sitting behind it.
- **Accessible alternative:** a single collapsed **Accessible navigation** control
  exposes native navigation. There is no competing always-visible button grid or
  additional mobile route selector.
- **Keyboard:** arrow keys and Home/End move sector focus; Enter/Space activate it.
  Native tooltips and an accessible description retain keyboard information.
  The visible instruction sentence under the wheel has been removed.

Routing uses the existing Python page state and consumes each component event
once. Expansion has its own presentation state, which does not alter the dataset,
filters, calculations, saved views, investigation records, or report drafts.
Selecting another page returns the analytical wheel to its compact presentation.

## Verification

- **934 tests passed**, no failures or skips. One third-party anyio/pytest
  deprecation warning remains. Command: `python3 -m pytest -q`.
- Three added regressions cover the exact home heading, expansion across ordinary
  reruns without touching filters/source versions, and direct wheel navigation
  without clearing the selected filter.
- Actual Chromium verifies all ten destinations and active states, keyboard
  navigation, Quality & Preparation → Explore → Investigate → Reports, expansion
  and collapse, scrolling, ordinary reruns, dataset/project switching, browser
  refresh, retained saved selections, saved investigations, and report drafts.
- Tested viewports: **1440 × 900**, **1920 × 1080**, **1280 × 832**,
  **1100 × 900**, **768 × 1024**, and **390 × 844**.
- Browser checks explicitly test that the dock does not intersect visible charts,
  tables, uploaders, text inputs, or dataset/project selectors. They account for
  clipping to the main/sidebar scroll viewport, rather than treating offscreen
  content as visible. Horizontal page overflow is checked on each capture.
- The full, compact, expanded, and mobile screenshots were visually inspected.
- Browser data is generated and clearly labeled synthetic. Existing user projects
  are never used by the acceptance test.

Run the browser check from the repository root with installed dependencies and
Chromium at `/usr/bin/chromium`:

```sh
STREAMLIT_SERVER_FILE_WATCHER_TYPE=none python3 scripts/browser_redesign.py --output outputs/persistent-wheel-check
```

Use a new output directory. File watching is disabled only for the isolated test
server; navigation and all application behaviors are exercised normally. The
browser driver waits after each multiselect-triggered Streamlit rerun.

[Browser verification](persistent-wheel/browser-verification.json)

| Required view | Screenshot |
| --- | --- |
| Full home wheel and Data Analysis heading | [Home](persistent-wheel/01-home-empty-1440.png) |
| Compact wheel on Quality & Preparation | [Quality](persistent-wheel/05-quality.png) |
| Compact wheel while exploring a chart | [Explore](persistent-wheel/06-explore.png) |
| Expanded wheel on an analytical page | [Expanded Quality](persistent-wheel/05b-expanded-quality.png) |
| MacBook-sized home | [1280px](persistent-wheel/11-home-1280.png) |
| Expanded mobile wheel | [390px expanded](persistent-wheel/12-expanded-wheel-390.png) |
| Mobile dock beside analytical content | [390px Explore](persistent-wheel/13-responsive-explore-390.png) |

## Scope and limitations

DuckDB calculations, ingestion, profiling, preparation safeguards, SQL security,
statistical methods, investigation/report implementations, Ollama/Qwen, and data
storage are unchanged. No new dependencies, models, services, or unrelated features
were added.

Compact desktop sectors use icons and full names on hover/focus; expand to see all
labels simultaneously. On smaller screens, expanding the wheel intentionally
reduces the visible analysis viewport; collapse restores it. Streamlit still uses
its normal reruns and existing session/persistence rules. Saved work is retained;
this does not introduce a new persistence policy for unsaved widgets after browser
refresh. Native macOS/Safari and a formal screen-reader audit were not run.
