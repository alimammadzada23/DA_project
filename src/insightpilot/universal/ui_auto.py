"""Streamlit layer for automatic analysis: choose the target, watch each stage, read and download the report."""
import pandas as pd
import plotly.io as pio
import streamlit as st

from . import auto_analysis as aa
from .auto import narrative
from .auto_report import REPORT_SECTIONS, downloads, excel_report, frame, html_report, method_items, tiles
from .local_ai import LocalAIError, LocalConfig, OllamaClient

STATUS_ICON = {'pending': '○', 'running': '…', 'done': '✓', 'done with errors': '✓ with errors', 'failed': '✗', 'skipped': '–'}
TYPE_LABEL = {'binary': 'Binary (two classes)', 'multiclass': 'Multiclass (categories)', 'numeric': 'Numeric'}


def automatic_page(workspace, active_table=None):
    st.subheader('Automatic analysis')
    st.caption('Profiles the data, applies only safe and reversible corrections, compares every field with your target and builds one '
               'report with every table, chart and query. It computes and organises; it does not interpret or recommend.')
    tables = workspace.tables()
    if not tables:
        st.info('Import a dataset in Data Hub first.'); return
    names = list(tables)
    if st.session_state.get('auto-table') not in names:
        st.session_state['auto-table'] = active_table if active_table in names else names[0]
    table = st.selectbox('Dataset', names, key='auto-table', format_func=lambda n: tables[n]['source'].get('filename', n) + ' · ' + n)
    previous = [r for r in aa.list_runs(workspace) if r['table'] == table]
    tabs = st.tabs(['New analysis', f'Previous reports ({len(previous)})'])
    with tabs[0]:
        setup(workspace, table)
    with tabs[1]:
        if not previous:
            st.caption('No automatic analyses for this dataset yet.')
        else:
            run_id = st.selectbox('Report', [r['run_id'] for r in previous], key='auto-previous',
                                  format_func=lambda r: next(f'{p["created_at"]} · target: {p["target"] or "none"}' for p in previous if p['run_id'] == r))
            show_report(aa.load_run(workspace, run_id), key='previous')


def prepared_for(workspace, table):
    version = workspace.info(table)['current']
    key = f'auto-prepared-{workspace.directory}-{table}-{version}'
    if key not in st.session_state:
        with st.spinner('Profiling every column…'):
            st.session_state[key] = aa.prepare(workspace, table)
    return st.session_state[key]


def setup(workspace, table):
    prepared = prepared_for(workspace, table)
    st.markdown('**Step 1 · Data profile**')
    st.caption(f'{len(prepared["columns"]):,} columns profiled in {prepared["seconds"]:.1f} s. The full findings appear in the report.')
    st.markdown('**Step 2 · Choose the target**')
    suggestions = prepared['suggestions']
    if suggestions:
        st.dataframe(pd.DataFrame([{'Column': s['column'], 'Type': TYPE_LABEL[s['target_type']], 'Reasons': '; '.join(s['reasons'])}
                                   for s in suggestions[:8]]), hide_index=True, width='stretch')
        st.caption('Suggestions come from column types and values. Names are hints only.')
    eligible = [n for n, c in prepared['columns'].items() if c['allowed_target_types']]
    ordered = [s['column'] for s in suggestions] + [n for n in eligible if n not in {s['column'] for s in suggestions}]
    target = st.selectbox('Target column', [None] + ordered, index=1 if ordered else 0, key=f'auto-target-{table}',
                          format_func=lambda v: 'No target (descriptive report)' if v is None else v)
    target_type = positive = None
    if target:
        column = prepared['columns'][target]
        allowed = column['allowed_target_types']
        target_type = st.radio('Target type', allowed, horizontal=True, key=f'auto-type-{table}-{target}', format_func=TYPE_LABEL.get) \
            if len(allowed) > 1 else allowed[0]
        if len(allowed) == 1: st.caption('Target type: ' + TYPE_LABEL[target_type])
        if target_type == 'binary':
            labels = [v for v, _ in column['values']]
            suggested = aa.suggest_positive(column['values'])
            positive = st.selectbox('Positive class', labels, index=labels.index(suggested), key=f'auto-positive-{table}-{target}',
                                    format_func=lambda v: f'{target} = {v}')
    time_axis = None
    if prepared['date_columns']:
        time_axis = st.selectbox('Time axis (optional)', [None] + [c for c in prepared['date_columns'] if c != target], key=f'auto-time-{table}',
                                 format_func=lambda v: 'None' if v is None else v)
    if not st.button('Run automatic analysis', type='primary', key=f'auto-run-{table}'):
        current = st.session_state.get('auto-last-run')
        if current and current.get('table') == table:
            show_report(current, key='current')
        return
    lines = {}
    with st.status('Running automatic analysis', expanded=True) as status:
        for key, title in aa.STAGES:
            lines[key] = st.empty(); lines[key].write(f'{STATUS_ICON["pending"]} {title}')

        def progress(key, title, state, detail):
            lines[key].write(f'{STATUS_ICON.get(state, state)} **{title}** · {state}' + (f' · {detail}' if detail else ''))
        output = aa.run(workspace, table, target=target, target_type=target_type, positive=positive, time_axis=time_axis,
                        progress=progress, prepared=prepared)
        aa.save_run(workspace, output)
        failed = [s for s in output['stages'] if s['status'] == 'failed']
        status.update(label='Automatic analysis finished' + (f' with {len(failed)} failed stage(s)' if failed else ''),
                      state='error' if failed else 'complete', expanded=bool(failed))
    st.session_state['auto-last-run'] = output
    show_report(output, key='current')


def show_items(items, key):
    for item in items:
        if item['type'] == 'text':
            st.write(item['text'])
        elif item['type'] == 'table':
            st.markdown(f'**{item["title"]}**')
            data = frame(item)
            if len(data): st.dataframe(data, hide_index=True, width='stretch')
            else: st.caption('No rows.')
            if item.get('note'): st.caption(item['note'])
        else:
            st.plotly_chart(pio.from_json(item['figure']), width='stretch', theme=None, key=f'auto-{key}-{item["id"]}',
                            config={'displaylogo': False})


def findings_frame(findings):
    return pd.DataFrame([{'ID': f['id'], 'Severity': f['severity'], 'Type': f['label'], 'Statement': f['statement'],
                          'Evidence': f.get('evidence') or '', 'Caveats': ' '.join(f.get('caveats') or [])} for f in findings])


def ai_panel(output, key):
    """Optional and off by default: the local model sees only the findings digest; every sentence is checked before it is shown."""
    store = st.session_state.setdefault('auto-ai', {})
    with st.expander('Optional: local AI summary of the findings'):
        st.caption('Runs only when you press the button, on the local model at a loopback address. The model receives the findings list '
                   'only; sentences without a finding ID, with numbers that are not in the cited findings, or with causal wording are removed. '
                   'The summary is added to the HTML download as a separate, labelled section.')
        if st.button('Write local AI summary', key=f'auto-ai-{key}-{output["run_id"]}'):
            try:
                with st.spinner('Asking the local model…'):
                    store[output['run_id']] = narrative.summarise(output, OllamaClient(LocalConfig()))
            except LocalAIError as exc:
                st.error(str(exc))
        section = store.get(output['run_id'])
        if section:
            for sentence in section['sentences']: st.write('• ' + sentence['text'])
            if section['rejected']: st.caption(f'{len(section["rejected"])} sentence(s) failed the checks and were removed.')
    return store.get(output['run_id'])


def show_report(output, key):
    st.markdown(f'### Report · {output["dataset"]}')
    failed = [s for s in output['stages'] if s['status'] == 'failed']
    for stage in failed:
        st.error(f'{stage["title"]} failed: {stage.get("error", "")}')
    for warning in output.get('warnings', []):
        st.warning(warning)
    found = tiles(output)
    for column, (value, label) in zip(st.columns(len(found)), found):
        column.metric(label, value)
    ai_section = ai_panel(output, key)
    stem = f'analysis-{output["table"]}-{output["run_id"]}'
    buttons = st.columns(5)
    buttons[0].download_button('Download HTML', html_report(output, ai_section), file_name=stem + '.html', mime='text/html', on_click='ignore',
                               key=f'auto-html-{key}-{output["run_id"]}', width='stretch')
    buttons[1].download_button('Download Excel', excel_report(output), file_name=stem + '.xlsx', on_click='ignore', key=f'auto-xlsx-{key}-{output["run_id"]}',
                               mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', width='stretch')
    for column, (suffix, (data, mime)) in zip(buttons[2:], downloads(output).items()):
        column.download_button(suffix, data, file_name=f'{stem}-{suffix}', mime=mime, on_click='ignore',
                               key=f'auto-{suffix}-{key}-{output["run_id"]}', width='stretch')
    findings = output.get('findings') or []
    top = (output.get('config') or {}).get('top_findings', 10)
    st.markdown(f'#### Top findings')
    if findings:
        st.caption('Ordered by severity; associations, not causes. Each finding names the table or chart that supports it.')
        st.dataframe(findings_frame(findings[:top]), hide_index=True, width='stretch')
        if len(findings) > top:
            with st.expander(f'All {len(findings)} findings'):
                st.dataframe(findings_frame(findings), hide_index=True, width='stretch')
    else:
        st.caption('No findings met the reporting thresholds.')
    sections = output['sections']
    for section, title, sources, empty in REPORT_SECTIONS:
        present = [s for s in sources if sections.get(s)]
        if section == 'fields':
            if present:
                st.markdown('#### Field details')
                for index, field in enumerate(sections['fields']):
                    with st.expander(f'{field["field"]} · {field["type"]}', expanded=False):
                        show_items(field['items'], key)
            continue
        if not present: continue
        with st.expander(title, expanded=section == 'drivers'):
            for source in present: show_items(sections[source], key)
    with st.expander('Method and limitations'):
        st.dataframe(pd.DataFrame([{'Stage': s['title'], 'Status': s['status'], 'Detail': s.get('detail') or s.get('error') or '',
                                    'Seconds': s['seconds']} for s in output['stages']]), hide_index=True, width='stretch')
        show_items(method_items(output), key)
    if output.get('saved_blocks'):
        st.caption(f'{output["saved_blocks"]:,} charts were also saved as evidence for the report builder and the rest of the manual workflow.')
    st.caption('The downloaded HTML report also contains the evidence appendix and an empty Analyst conclusions section.')
