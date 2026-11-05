# Workflow and interface audit

Reliability baseline: `716bd426051aabc2c63d16a9dd9dc0cccba8757b`, committed
separately after reproducing the three unchanged Standard limitations at `b9451ca`.

## Existing implementation

`app.py` calls the Streamlit shell and `universal/ui.py`. Eight pages are selected
by a sidebar radio; the landing page is Overview for populated workspaces and
Data Hub otherwise. Workspace creation and naming live in sidebar expanders.
Data Hub supports files, databases, paginated HTTP APIs, PostgreSQL and XES.
Full-table profiling, reversible preparation and bulk semantic review already
exist. Explore has ranked bars with selection/records, histograms, scatter,
calendar trends, group distributions and two-dimensional counts. Investigation
is hidden under Advanced. Report exports and saved evidence exist, but assembling
an analyst's narrative and ordered findings requires too much navigation.

## Usability problems

- The workflow is not visible in the main content; a new user sees controls before
  understanding the purpose or next action.
- Projects, their purpose, datasets and saved work are difficult to recognize.
- An imported table has a catalog entry but no convenient profiled handoff into
  quality review, charts or analysis.
- Quality starts with an exhaustive catalog, rather than prioritizing potential
  parsing/missingness issues and distinguishing them from usable fields.
- Explore requires finding a technical chart selector. Scope is primarily in the
  sidebar; saved evidence and the next investigative step are easy to miss.
- Investigations lack a persistent question/notes workspace. Reports expose
  exports but have no cohesive editorial workflow for sections and evidence order.

## Revised hierarchy and reuse

Main workflow: **Workspace Home → Data Hub → Quality & Preparation → Explore →
Investigate → Reports**. Overview, AI Analyst, SQL Lab and Advanced remain accessible
as supporting tools. The sidebar retains project/dataset selection and shared
analytical context; main content repeats active scope without changing it.

Reuse `workspace_labels` and existing `Workspace` storage for project cards and
creation; `profile` for import/quality overviews; Engine, Plotly and existing
Explore controls for charts; `configuration`, saved blocks and replay for evidence;
existing comparison/statistical tools for investigation; and self-contained HTML
report exports for deliverables. Persist user-authored investigation/report drafts
as namespaced existing layout objects, avoiding a database migration or new store.

Implementation stages: central navigation/home and data handoff; quality and Explore
guidance; investigation brief and ordered report builder; real browser/visual and
automated regression verification. No new model, calculation engine, causal engine,
frontend framework or dependency is planned. Statistical anomalies remain prompts
to investigate; unsupported methods and incomplete verification are disclosed.
