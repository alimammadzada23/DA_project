"""Dataset routing and business views, extending the preserved procurement UI."""
from dataclasses import asdict, replace
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import uuid

import duckdb
import pandas as pd
import streamlit as st

from . import business_charts as visual, charts, ui
from .agent import Analyst
from .complaints import Complaints, UnusableComplaintDates, download_extract, ingest_complaints, management_summary, export_complaints
from .custom import CustomAnalytics, CustomProcessAnalytics
from .datasets import TableFilters, inspect_table, ingest_table, metadata, MAX_BYTES

WORKSPACES = ['Choose a workspace', 'Procurement Intelligence', 'Customer Experience & Risk Intelligence', 'Analyze My Dataset', 'Universal Intelligence']
COMPLAINT_PAGES = ['Executive Overview', 'Company Comparison', 'Root-Cause Investigation', 'AI Business Analyst']
COMPLAINT_QUESTIONS = ['Which companies have the highest reported complaint volumes?',
    'Which products account for the largest share of complaints?', 'How have complaints changed over time?',
    'Compare the complaint categories of two selected companies.', 'Which issues appear to be increasing?',
    'What should a customer-experience manager investigate next?']


def number(value, suffix=''):
    return 'Unavailable' if value is None else f'{value:,.2f}' + suffix


@st.cache_data(ttl=300, max_entries=192, show_spinner=False)
def calculation(database, modified, size, filters, module, method, arguments=()):
    service = Complaints(database, filters) if module == 'complaints' else CustomAnalytics(database, filters)
    return getattr(service, method)(*arguments)


def available(module):
    paths = set(Path('data/workspaces').glob('*/analytics.duckdb')) | set(Path('data/cfpb').glob('*/analytics.duckdb'))
    paths |= set(Path('data/cfpb').glob('analytics.duckdb'))
    configured = os.environ.get('INSIGHTPILOT_CFPB_DATABASE' if module == 'complaints' else 'INSIGHTPILOT_CUSTOM_DATABASE')
    if configured: paths.add(Path(configured))
    entries = {}
    for path in sorted(paths):
        try:
            info = metadata(path)
            if info['module'] == module: entries[str(path)] = info
        except (OSError, duckdb.Error, ValueError):
            continue
    return entries


def switch_workspace():
    # Clear current answers and evidence; filter widgets are namespaced by dataset.
    for key in ['tabular_history', 'tabular_scope', 'tabular_sql_result', 'sql_evidence', 'process', 'root_selection']:
        st.session_state.pop(key, None)
    if st.session_state.get('investigations'):
        st.session_state.setdefault('investigation_archive', []).extend(st.session_state['investigations'])
    st.session_state['investigations'] = []


def select_workspace(value):
    st.session_state['pending_workspace'] = value
    st.rerun()


def landing():
    st.markdown('<div class="eyebrow">EVIDENCE / DECISIONS / ACTION</div>', unsafe_allow_html=True)
    st.title('InsightPilot — AI Business Intelligence')
    st.write('Choose a dataset workspace. Explore the evidence, compare patterns and decide what deserves investigation.')
    with st.container(border=True):
        st.subheader('Universal Intelligence')
        st.write('Import unfamiliar data and related tables, review every field, define explicit metrics, investigate changes and export reproducible analysis.')
        if st.button('Open Universal Intelligence',type='primary'): select_workspace(WORKSPACES[4])
    columns = st.columns(3)
    descriptions = [
        ('Procurement Intelligence', 'Understand recorded process paths, waiting intervals and repeated activities.', 'Original BPI import or clearly labeled synthetic demonstrations.'),
        ('Customer Experience & Risk Intelligence', 'Investigate reported complaint volumes, product mix and company responses.', 'Official CFPB period extracts; source availability is checked during download.'),
        ('Analyze My Dataset', 'Inspect your data, approve its field mapping and unlock compatible analytical views.', 'CSV, Excel, Parquet and existing XES / XES.gz logs. Files stay local by default.'),
    ]
    for column, (name, description, source) in zip(columns, descriptions):
        with column:
            with st.container(border=True):
                st.subheader(name); st.write(description); st.caption(source)
                if st.button('Open workspace', key='open-' + name, width='stretch'): select_workspace(name)
    st.caption('Guided analysis runs local SQL without an API key. Optional live AI requires explicit consent. Source facts, uploads and synthetic examples remain visibly distinct.')


def source_banner(info):
    if info['origin'] == 'synthetic': st.warning('SYNTHETIC DATA · Software demonstration only. No genuine business findings are implied.')
    elif info['origin'] == 'official_cfpb': st.success('GENUINE SOURCED DATA · Official CFPB HTTPS extract · retained source and download receipt')
    else: st.info('USER-UPLOADED DATA · Source authenticity, usage rights and business definitions require your review.')
    st.caption(f'Active dataset · {info["title"]} · {info["rows"]:,} imported records · snapshot {info["dataset_id"][:8]}')
    if info.get('coverage'):
        coverage = info['coverage']; st.caption(f'Coverage · {coverage["start_inclusive"]} to {coverage["end_exclusive"]} (exclusive end) · {coverage["basis"]}')
    if info.get('downloaded_at'): st.caption('Downloaded · ' + info['downloaded_at'])


def dataset_picker(module):
    entries = available(module)
    key = module + '_dataset'
    pending = st.session_state.pop('pending_' + key, None)
    if pending in entries: st.session_state[key] = pending
    current = st.session_state.get(key)
    if current and current not in entries:
        # A transient scan/reader error must never silently choose another
        # dataset. Re-open the selected file or surface its actual failure.
        if Path(current).is_file():
            info = metadata(current)
            if info['module'] != module: raise ValueError('Selected file belongs to another analytical workspace.')
            entries[current] = info
        else:
            st.warning('The previously selected snapshot is no longer present. Choose another dataset.')
            st.session_state.pop(key, None)
    if not entries: return None
    path = st.sidebar.selectbox('Active dataset', list(entries), key=key,
        format_func=lambda value: f'{entries[value]["title"]} · {entries[value]["origin"]} · {entries[value]["rows"]:,} records · {entries[value]["dataset_id"][:6]}')
    return path


def cfpb_import():
    st.subheader('Load a CFPB complaint snapshot')
    st.write('Download a complete, dated extract from the official API, or import its CSV export. An uploaded file remains labeled as user supplied.')
    with st.form('download-cfpb'):
        left, right = st.columns(2)
        # A stable week with a publication buffer; date bounds are explicit.
        start = left.date_input('Received on or after', value=date(2026, 9, 1))
        end = right.date_input('Received before · exclusive end', value=date(2026, 9, 8))
        submitted = st.form_submit_button('Download official CFPB extract')
    if submitted:
        directory = Path('data/cfpb') / str(uuid.uuid4())
        try:
            with st.spinner('Downloading, verifying the returned schema and preparing DuckDB…'):
                download_extract(directory, start, end)
            st.session_state['pending_complaints_dataset'] = str(directory / 'analytics.duckdb'); st.rerun()
        except Exception as exc:
            st.error('The official extract could not be obtained. No genuine snapshot was loaded. You can download it on your Mac and import the original CSV.')
            with st.expander('Connection diagnostic'): st.write(f'{type(exc).__name__}: {exc}')
    with st.form('upload-cfpb'):
        file = st.file_uploader('CFPB CSV export', type=['csv'], key='cfpb-file')
        synthetic = st.checkbox('This complaint file is synthetic test data')
        imported = st.form_submit_button('Import complaint CSV')
    if imported:
        if file is None: st.error('Choose a CFPB CSV export first.')
        elif file.size > 200 * 1024 * 1024: st.error('Choose an extract below 200 MiB.')
        else:
            directory = Path('data/workspaces') / str(uuid.uuid4()); directory.mkdir(parents=True)
            source = directory / 'complaints.csv'; source.write_bytes(file.getvalue())
            try:
                with st.spinner('Validating complaint fields and preserving source rows…'):
                    ingest_complaints(source, directory / 'analytics.duckdb', synthetic=synthetic)
                st.session_state['pending_complaints_dataset'] = str(directory / 'analytics.duckdb'); st.rerun()
            except (ValueError, OSError, duckdb.Error) as exc: st.error(f'Import rejected: {exc}')
    with st.expander('Official source and exact Mac download commands'):
        st.link_button('Consumer Complaint Database', 'https://www.consumerfinance.gov/data-research/consumer-complaints/')
        st.write('On the official search page, select your date range and export all matching records as CSV. Keep the original export and review the publisher’s database limitations and usage statements.')
        st.code('uv run --frozen insightpilot cfpb-download data/cfpb/2026-09-01-week --start 2026-09-01 --end 2026-09-08', language='bash')
        st.caption('The local snapshot enforces an exclusive end date and records any excluded boundary-date rows. The CSV API export ignores paging parameters. Use a shorter period if needed. No source authentication is inferred from a filename.')


def tabular_filters(service, path):
    options = service.options(); tag = service.metadata['dataset_id']
    st.sidebar.markdown('### Refine the selection')
    names = {'companies': 'Company', 'products': 'Product', 'issues': 'Complaint issue', 'states': 'State',
             'responses': 'Company response', 'timely': 'Timely response', 'categories': 'Category'}
    def reset():
        for dimension in service.dimensions: st.session_state['tab-' + tag + '-' + dimension] = []
        st.session_state['tab-' + tag + '-date-enabled'] = False
    st.sidebar.button('Reset dataset filters', on_click=reset)
    values = {}
    for dimension, data in options.items():
        if dimension == 'dates': continue
        if data['values'] != [None] and data['values']:
            values[dimension] = tuple(st.sidebar.multiselect(names[dimension], data['values'], format_func=charts.label, key='tab-' + tag + '-' + dimension))
        if data['truncated']: st.sidebar.caption(f'{names[dimension]}: 1,000 most represented values shown. Use a smaller source extract for high-cardinality fields.')
    start, end = options['dates']
    if start and st.sidebar.checkbox('Filter by record date', key='tab-' + tag + '-date-enabled'):
        dates = st.sidebar.date_input('Record date · inclusive bounds', (start, end), min_value=start, max_value=end, key='tab-' + tag + '-dates')
        if len(dates) == 2: values.update(start_date=dates[0], end_date=dates[1])
    return TableFilters(**values)


def draw(figure, key):
    st.plotly_chart(figure, key=key, width='stretch', config={'displaylogo': False, 'toImageButtonOptions': {'format': 'png', 'scale': 2}})
    with st.expander('Export this chart'):
        st.download_button('Download interactive chart', figure.to_html(include_plotlyjs=True), file_name=key + '.html', mime='text/html', key='export-' + key)


def evidence_panel(result, label='View analytical evidence'):
    with st.expander(label):
        st.caption('Executed local read-only SQL; source hash and filter parameters bind this evidence to a snapshot.')
        st.code(result['sql'], language='sql'); st.json(result)


def record_inspector(service, scope, *, default_dimension=None, default_value=None):
    st.subheader('Inspect corresponding records')
    st.caption('The drill-down is intersected with every active filter. Pages contain at most 100 records; exports contain the current page.')
    dimensions = list(service.dimensions.values())
    dimension = st.selectbox('Record dimension', dimensions, index=dimensions.index(default_dimension) if default_dimension in dimensions else 0,
                             key='record-dimension-' + scope)
    candidates = service.run(f'SELECT "{dimension}" AS value, count(*) AS records FROM selected GROUP BY 1 ORDER BY records DESC, value NULLS LAST LIMIT 1000')
    choices = [row[0] for row in candidates['rows']]
    if not choices: st.info('No matching records to inspect.'); return
    value_key = 'record-value-' + scope + '-' + dimension
    chart_key = 'last-chart-drill-' + scope + '-' + dimension
    if dimension == default_dimension and default_value in choices and st.session_state.get(chart_key, object()) != default_value:
        st.session_state[value_key] = default_value; st.session_state[chart_key] = default_value
    if value_key not in st.session_state or st.session_state[value_key] not in choices:
        st.session_state[value_key] = default_value if dimension == default_dimension and default_value in choices else choices[0]
    value = st.selectbox('Record category', choices, format_func=charts.label, key=value_key)
    count = next(row[1] for row in candidates['rows'] if row[0] == value)
    page = int(st.number_input('Record page', min_value=1, max_value=max(1, (count+99)//100), step=1, value=1,
                              key='record-page-' + scope + '-' + dimension + '-' + str(value))) - 1
    result = service.records(page, dimension=dimension, value=value)
    frame = charts.frame(result)
    st.dataframe(frame, hide_index=True, width='stretch')
    st.caption(f'{charts.label(value)} · {count:,} matching records · page {page+1}')
    st.download_button('Download these filtered records', frame.to_csv(index=False), file_name='filtered-records-page.csv', mime='text/csv', key='records-export-' + scope)
    evidence_panel(result, 'View record-selection evidence')


def selected_chart(result, dimension, scope):
    figure = visual.ranked(result, dimension, 'complaints')
    figure.data[0].customdata = [[row[0], scope] for row in result['rows'][:12]]
    figure.update_layout(dragmode='select', selectionrevision=scope)
    st.caption('Drag a box over a bar to reveal its corresponding complaint records below.')
    selection = st.plotly_chart(figure, key='complaint-selection-' + scope, width='stretch', on_select='rerun', selection_mode=('points', 'box'))
    points = selection.selection.points
    if points:
        data = points[-1].get('customdata', [])
        if len(data) == 2 and data[1] == scope: return data[0]
    return result['rows'][0][0] if result['rows'] else None


def complaints_executive(service, get, scope):
    values, result = get('overview')
    columns = st.columns(4)
    for column, label, value in zip(columns, ['Reported complaints', 'Represented companies', 'Timely-response share', 'Known timeliness coverage'],
        [f'{values["complaints"]:,}', f'{values["companies"]:,}', 'Unavailable' if values['timely_share'] is None else f'{values["timely_share"]:.1%}',
         'Unavailable' if not values['complaints'] else f'{values["known_timely"]/values["complaints"]:.1%}']): column.metric(label, value)
    st.caption(f'Timely denominator · {values["timely_yes"]:,} Yes / {values["known_timely"]:,} known Yes/No responses · {values["complaints"]-values["known_timely"]:,} unknown. {values["missing_dates"]:,} records excluded from date trends.')
    st.write('Use reported complaint volumes to identify investigation priorities. Company quality and complaint rates require defensible exposure data that this dataset does not contain.')
    left, right = st.columns([1.35, 1])
    with left: st.subheader('Reported complaints over time'); draw(visual.trend(get('investigate', ('daily',))), 'complaints-trend-' + scope)
    with right:
        st.subheader('Product mix')
        draw(visual.donut(get('investigate', ('donut',))), 'complaints-donut-' + scope)
        st.caption('Up to five products; categories below 3% and remaining products are combined in Other. Hover for counts and shares of all selected complaints.')
    left, right = st.columns(2)
    with left: st.subheader('Concentrated issues'); chosen = selected_chart(get('investigate', ('issues',)), 'issue', scope)
    with right: st.subheader('Company volume distribution'); draw(visual.histogram(get('investigate', ('company_distribution',))), 'company-distribution-' + scope)
    record_inspector(service, scope, default_dimension='issue', default_value=chosen)
    st.download_button('Download one-page management summary', management_summary(service), file_name='management-summary.md', mime='text/markdown')
    evidence_panel(result)


def company_comparison(service, get, scope):
    ranking = get('investigate', ('companies',)); choices = [row[0] for row in ranking['rows']]
    st.info('Company ranking shows reported complaint volume. It is not a ranking of service quality. Market share and customer exposure are unavailable.')
    chosen = selected_chart(ranking, 'company', scope)
    if len(choices) >= 2:
        left, right = st.columns(2)
        a = left.selectbox('Company A', choices, format_func=charts.label, key='company-a-' + scope)
        b = right.selectbox('Company B', [value for value in choices if value != a], format_func=charts.label, key='company-b-' + scope)
        comparison = Complaints(service.database, replace(service.filters, companies=(a, b)))
        stats = comparison.investigate('companies')
        for column, company in zip(st.columns(2), [a, b]):
            row = next(row for row in stats['rows'] if row[0] == company)
            with column:
                st.subheader(charts.label(company)); st.metric('Reported complaints', f'{row[1]:,}')
                st.metric('Timely among known responses', 'Unavailable' if row[5] is None else f'{row[5]:.1%}')
                st.caption(f'{row[3]:,} Yes / {row[2]:,} known responses · {row[1]-row[2]:,} unknown')
        draw(visual.stacked(comparison.investigate('stacked_mix')), 'company-stacked-' + scope)
        timeline = comparison.investigate('trend')
        if timeline['truncated']: st.warning('Company trend exceeds 1,000 grouped points. Narrow the period to avoid an incomplete comparison.')
        else: draw(visual.trend(timeline, group='company'), 'company-trend-' + scope)
        evidence_panel(stats)
    else: st.info('Select a cohort containing at least two represented companies to compare them.')
    record_inspector(service, scope, default_dimension='company', default_value=chosen)


def complaint_investigation(service, get, scope):
    st.write('Explore associations and changes. Operational causes require complaint review, exposure data and process evidence.')
    draw(visual.heatmap(get('investigate', ('heatmap',))), 'complaint-heatmap-' + scope)
    changes = get('issue_changes'); periods = changes['comparison_periods']
    st.subheader('Issues changing across equal periods')
    st.caption(f'{periods["earlier_start"]}–{periods["later_start"]} versus {periods["later_start"]}–{periods["end_exclusive"]} (exclusive ends); {periods["days_per_period"]} days each. {periods["excluded_tail_days"]} unmatched tail day(s) excluded.')
    frame = charts.frame(changes); frame['count_change'] = frame['later']-frame['earlier']
    chart_evidence = {'columns': list(frame.columns), 'rows': list(frame.itertuples(index=False, name=None))}
    draw(visual.ranked(chart_evidence, 'issue', 'count_change', unit='Change in reported complaints'), 'issue-changes-' + scope)
    st.dataframe(frame.head(20), hide_index=True, column_config={'change_share': st.column_config.NumberColumn('Relative change · fraction', format='%.3f')})
    for row in [r for r in changes['rows'] if r[2] > r[1]][:3]:
        with st.container(border=True):
            st.subheader(charts.label(row[0])); st.write(f'Observation · {row[1]:,} → {row[2]:,} reported complaints across equal periods.')
            st.write('Hypothesis · A product-mix change, reporting change or service problem could contribute. None is demonstrated by these counts.')
            st.write('Action · Review a structured complaint sample, validate data maturity and compare product mix with exposure data. Monitor equal-period issue counts and known-response coverage.')
    draw(visual.stacked(get('investigate', ('response',)), group='response', category='timely'), 'complaint-response-' + scope)
    evidence_panel(changes); record_inspector(service, scope, default_dimension='issue')


def render_tabular_answer(result, service, scope, index):
    st.caption(result.get('mode', 'Live LLM · executed evidence; narrative requires review'))
    answer = ui.re.sub(r'```[\s\S]*?```', '', result.get('answer') or '')
    st.write(answer)
    evidence = result.get('evidence', [])
    key = result.get('visual')
    if evidence and evidence[0]['rows']:
        item = evidence[0]
        if isinstance(service, Complaints):
            if key in ('companies', 'issues', 'products'): draw(visual.ranked(item, {'companies': 'company', 'issues': 'issue', 'products': 'product'}[key], 'complaints'), f'answer-{scope}-{index}')
            elif key == 'daily': draw(visual.trend(item), f'answer-{scope}-{index}')
            elif key == 'company_product': draw(visual.stacked(item), f'answer-{scope}-{index}')
        elif isinstance(service, CustomProcessAnalytics):
            if key == 'top_variants': draw(charts.variants(item), f'answer-{scope}-{index}')
            elif key == 'repeat_activity_analysis': draw(charts.activities(item, True), f'answer-{scope}-{index}')
            elif key == 'activity_frequency': draw(charts.activities(item), f'answer-{scope}-{index}')
        elif key == 'trend': draw(visual.trend(item, 'month', 'records', unit='Records'), f'answer-{scope}-{index}')
        elif key in ('groups', 'products', 'customers'): draw(visual.ranked(item, {'groups': 'category', 'products': 'product', 'customers': 'customer'}[key], 'records', unit='Records'), f'answer-{scope}-{index}')
    st.caption(result.get('limitations', service.limitations))
    with st.expander('View analytical evidence'):
        for item in evidence: st.code(item['sql'], language='sql'); st.json(item)


def tabular_analyst(service, scope):
    st.write('Ask a business question about the active dataset and filters. Guided answers execute local SQL without sending data to an AI provider.')
    mode = st.radio('Analysis mode', ['Guided SQL analyst', 'Live LLM · optional'], horizontal=True, key='analysis-mode-' + scope)
    consent = False
    if mode.startswith('Live'):
        st.caption('Optional: configure INSIGHTPILOT_LLM_API_KEY and INSIGHTPILOT_LLM_MODEL securely. A ChatGPT subscription does not provide API credits.')
        consent = st.checkbox('I authorize sending this dataset’s schema, selected analytical results and questions to the configured external AI provider.', key='consent-' + scope)
    suggestions = COMPLAINT_QUESTIONS if isinstance(service, Complaints) else [value[0] for value in service.catalog.values()]
    suggested = st.selectbox('Suggested business question', suggestions)
    question = suggested if st.button('Run business investigation', type='primary') else st.chat_input('Ask about this selection')
    history = st.session_state.setdefault('tabular_history', [])
    if question:
        if mode.startswith('Live') and not consent: st.warning('Authorize external transmission before using live AI. Guided SQL remains available locally.')
        else:
            try:
                with st.spinner('Executing the analytical investigation…'):
                    if mode == 'Guided SQL analyst': result = service.guided_answer(question)
                    else:
                        result = Analyst(service.database, domain=service.metadata['module'], limitations=service.limitations,
                            data_tables=getattr(service, 'data_tables', {service.table})).chat(question, audit_path=Path('outputs/audits') / (str(uuid.uuid4())+'.json'), dashboard=service)
                    history.append({'question': question, 'result': result})
            except Exception as exc: st.error(f'Investigation incomplete: {exc}')
    for index, entry in enumerate(history):
        with st.chat_message('user'): st.write(entry['question'])
        with st.chat_message('assistant'): render_tabular_answer(entry['result'], service, scope, index)


def developer_tools(service, scope):
    with st.expander('Advanced / Developer Tools'):
        quality, sql, powerbi = st.tabs(['Quality & provenance', 'SQL evidence', 'Power BI preparation'])
        with quality:
            st.json(service.metadata)
            st.download_button('Download snapshot manifest', json.dumps(service.metadata, default=str, indent=2), file_name='dataset-manifest.json')
        with sql:
            question = st.selectbox('Dataset query catalog', list(service.catalog), format_func=lambda key: service.catalog[key][0])
            result = service.investigate(question); st.code(result['sql'], language='sql'); st.dataframe(charts.frame(result), hide_index=True); st.json(result)
            st.caption('The guided/live analyst can execute only this dataset’s approved catalog within the fixed filters. The procurement SQL workbench remains available in its workspace.')
        with powerbi:
            if isinstance(service, Complaints):
                st.write('Exported Parquet, a provenance manifest and executed query evidence support the documented complaint semantic model. Windows Power BI Desktop import, DAX, refresh and visual validation remain unverified.')
                if st.button('Prepare local Power BI export'):
                    directory = Path('outputs/complaints-powerbi') / service.metadata['dataset_id']
                    export_complaints(service.database, directory); st.success(f'Prepared {directory}. Read powerbi/complaints/README.md for the Windows checks.')
            else: st.write('Use approved mapped fields and verify their business definitions before designing measures. Procurement’s existing PBIP companion is preserved in its workspace.')


def complaints_workspace(path=None):
    custom = path is not None
    path = path or dataset_picker('complaints')
    if not path:
        st.title('Customer Experience & Risk Intelligence')
        st.info('No genuine complaint dataset is loaded. Download or import an official CFPB extract to begin. No complaint findings are claimed.')
        cfpb_import(); return
    if not custom:
        with st.sidebar.expander('Load another complaint snapshot'): cfpb_import()
    try: service = Complaints(path)
    except UnusableComplaintDates as exc:
        st.title('Customer Experience & Risk Intelligence'); st.error(str(exc))
        if custom: st.info('Use Import another dataset above to reimport the original source with its approved mapping.')
        else: st.info('Open Load another complaint snapshot in the sidebar and import the original CSV again.')
        return
    page = st.sidebar.radio('Complaint analytics', COMPLAINT_PAGES, key='custom-complaint-page' if custom else 'complaint-page')
    source_banner(service.metadata)
    qa = service.metadata['quality']; qa = qa.get('complaint_validation', qa)
    if 'missing_received_dates' in qa:
        st.caption(f'Source received-date quality · {qa["missing_received_dates"]:,} missing · {qa["invalid_received_dates"]:,} invalid '
                   'before dashboard filters. Timestamp-based dates use UTC; date-only values retain their calendar date.')
    filters = tabular_filters(service, path); service = Complaints(path, filters)
    signature = Path(path).stat()
    scope = hashlib.sha256((service.metadata['dataset_id'] + json.dumps(asdict(filters), default=str, sort_keys=True)).encode()).hexdigest()[:16]
    def get(method, args=()): return calculation(path, signature.st_mtime_ns, signature.st_size, filters, 'complaints', method, args)
    if st.session_state.get('tabular_scope') != scope:
        st.session_state['tabular_history'] = []; st.session_state['tabular_scope'] = scope
    overview, _ = get('overview')
    st.title(page); st.caption(f'Selected cohort · {overview["complaints"]:,} of {service.metadata["rows"]:,} imported complaints · independent complaint IDs')
    st.caption('Complaint counts are descriptive, not company-quality rankings. See data interpretation and limitations below.')
    with st.expander('Data interpretation and limitations'):
        st.write(service.limitations)
    if overview['complaints'] == 0: st.info('No complaints match these filters. Reset the dataset filters or broaden the selection.')
    if page == COMPLAINT_PAGES[0]: complaints_executive(service, get, scope)
    elif page == COMPLAINT_PAGES[1]: company_comparison(service, get, scope)
    elif page == COMPLAINT_PAGES[2]: complaint_investigation(service, get, scope)
    else: tabular_analyst(service, scope)
    developer_tools(service, scope)


def upload_workflow():
    st.subheader('1 · Inspect a business dataset')
    st.caption('Local files only · CSV, .xlsx Excel, flat Parquet or XES · at most 100 MiB / 500,000 tabular rows. No private records are sent to AI during inspection or guided analysis.')
    with st.form('inspect-upload'):
        file = st.file_uploader('Business dataset', type=['csv', 'xlsx', 'parquet', 'xes', 'gz'])
        sheet = st.text_input('Excel sheet name · blank uses the first sheet')
        submitted = st.form_submit_button('Inspect dataset')
    if submitted:
        if file is None: st.error('Choose a file first.')
        elif file.size > MAX_BYTES: st.error('Upload exceeds 100 MiB. Create a smaller extract.')
        elif file.name.lower().endswith(('.xes', '.xes.gz')):
            # Existing event-log import and all original process analytics are retained.
            directory = Path('data/imports') / str(uuid.uuid4()); directory.mkdir(parents=True)
            source = directory / ('source.xes.gz' if file.name.lower().endswith('.gz') else 'source.xes'); source.write_bytes(file.getvalue())
            st.session_state['pending_xes'] = str(source)
        else:
            directory = Path('data/workspaces') / str(uuid.uuid4()); directory.mkdir(parents=True)
            source = directory / ('source' + Path(file.name).suffix.lower()); source.write_bytes(file.getvalue())
            try:
                report = inspect_table(source, sheet or 0)
                st.session_state['pending_upload'] = {'source': str(source), 'report': report, 'sheet': sheet or 0}
            except Exception as exc: st.error(f'Inspection rejected: {exc}'); st.session_state.pop('pending_upload', None)
    if st.session_state.get('pending_xes'):
        st.write('XES already declares case, activity and timestamp roles. The existing streaming importer validates those fields.')
        synthetic = st.checkbox('This event log is synthetic test data', key='custom-xes-synthetic')
        if st.button('Import event log using the existing process engine'):
            from .ingest import ingest
            source = Path(st.session_state['pending_xes']); target = source.parent / 'analytics.duckdb'
            try:
                ingest(source, target, synthetic=synthetic)
                st.session_state['pending_database'] = str(target); st.session_state.pop('pending_xes')
                select_workspace(WORKSPACES[1])
            except Exception as exc: st.error(f'Event-log import rejected: {exc}')
    pending = st.session_state.get('pending_upload')
    if not pending: return
    report = pending['report']
    st.subheader('2 · Review schema and quality')
    st.write(f'{report["rows"]:,} records · {report["exact_duplicate_rows"]:,} exact duplicate rows. Duplicates are retained because their business meaning is unknown.')
    st.dataframe(pd.DataFrame(report['columns']), hide_index=True); st.write('Sample records'); st.dataframe(pd.DataFrame(report['sample']), hide_index=True)
    st.caption('Numeric parsing uses plain numbers; currency symbols and locale separators require preparation. Date parsing supports ISO and the explicitly selected slash-date convention.')
    with st.form('approve-mapping'):
        st.subheader('3 · Approve analytical field roles')
        mapping_scope = report['source_sha256'][:16]
        title = st.text_input('Dataset name', 'My business dataset', key='upload-title-' + mapping_scope)
        profile = st.selectbox('Analytical profile', ['descriptive', 'transactions', 'complaints', 'event_log'], key='upload-profile-' + mapping_scope, format_func=lambda x: {'descriptive': 'Descriptive business records', 'transactions': 'Transactions', 'complaints': 'Customer complaints · one row per complaint', 'event_log': 'Process event log'}[x])
        suggestions = report['suggested_mapping']; names = [None] + [c['column'] for c in report['columns']]
        roles = ['date', 'category', 'product', 'customer', 'measure', 'quantity', 'unit_price', 'company', 'issue', 'response', 'timely', 'case_id', 'activity', 'resource']
        mapping = {}; columns = st.columns(3)
        for index, role in enumerate(roles):
            value = columns[index % 3].selectbox(role.replace('_', ' ').title() + (' / timestamp' if role == 'date' else ''), names,
                index=names.index(suggestions.get(role)), format_func=lambda x: 'Not mapped' if x is None else x, key='map-' + report['source_sha256'][:12] + '-' + role)
            if value is not None: mapping[role] = value
        revenue = st.checkbox('I confirm the mapped amount represents revenue, or quantity × unit price is a valid revenue calculation.', key='upload-revenue-' + mapping_scope)
        day_first = st.checkbox('Slash dates use day/month/year · otherwise month/day/year', key='upload-dates-' + mapping_scope)
        utc = st.checkbox('For event logs, interpret offset-free ISO timestamps as UTC', key='upload-utc-' + mapping_scope)
        synthetic = st.checkbox('This dataset is synthetic demonstration data', key='upload-synthetic-' + mapping_scope)
        approved = st.form_submit_button('Approve mapping and create workspace')
    if approved:
        source = Path(pending['source']); target = source.parent / 'analytics.duckdb'
        try:
            with st.spinner('Validating approved roles and preparing compatible analytics…'):
                ingest_table(source, target, mapping, title=title, profile=profile, revenue=revenue, day_first=day_first,
                    sheet=pending['sheet'], synthetic=synthetic, assume_utc=utc)
            st.session_state['pending_custom_dataset'] = str(target); st.session_state.pop('pending_upload'); st.rerun()
        except (ValueError, OSError, duckdb.Error) as exc: st.error(f'Mapping could not be applied: {exc}')


def custom_process(service, scope):
    from .dashboard import Dashboard, Filters
    options = Dashboard(service.database).options()
    activities = st.sidebar.multiselect('Cases containing activity', options['activities']['values'], key='custom-event-activity-' + scope)
    start, end = None, None
    if options['dates'][0] and st.sidebar.checkbox('Filter by case start date', key='custom-event-dates-' + scope):
        dates = st.sidebar.date_input('Case start · inclusive UTC dates', (options['dates'][0], options['dates'][1]), key='custom-event-range-' + scope)
        if len(dates) == 2: start, end = dates
    process = CustomProcessAnalytics(service.database, Filters(activities=tuple(activities), start_date=start, end_date=end))
    active_scope = hashlib.sha256((scope + json.dumps(asdict(process.filters), default=str, sort_keys=True)).encode()).hexdigest()[:16]
    if st.session_state.get('tabular_scope') != active_scope:
        st.session_state['tabular_history'] = []; st.session_state['tabular_scope'] = active_scope
    page = st.sidebar.radio('Mapped process analytics', ['Process Overview', 'AI Business Analyst'], key='process-page-' + scope)
    if page == 'AI Business Analyst': tabular_analyst(process, active_scope); return
    st.caption('Filters select complete cases by first UTC timestamp and activity membership. Full event histories remain in every process chart.')
    overview, _ = process.overview()
    for column, label, value in zip(st.columns(3), ['Recorded cases', 'Recorded events', 'Median observed span'],
        [f'{overview["item_count"]:,}', f'{overview["event_count"]:,}', number(overview['median_span_days'], ' days')]): column.metric(label, value)
    draw(charts.flow(process.flow_transitions()), 'custom-process-flow-' + scope)
    draw(charts.variants(process.investigate('top_variants')), 'custom-process-variants-' + scope)
    draw(charts.activities(process.repeated_activities(), True), 'custom-process-repeats-' + scope)
    selected = process.investigate('long_spans'); ids = [row[0] for row in selected['rows']]
    if ids:
        case = st.selectbox('Inspect a selected case · up to 30 longest spans', ids, key='process-case-' + active_scope)
        events = process.case_events(case); draw(charts.timeline(events), 'custom-process-timeline-' + active_scope)
        st.dataframe(charts.frame(events), hide_index=True); evidence_panel(events, 'View process drill-down evidence')
    st.caption('Observed spans are not verified completion times. Repeats may be legitimate. No procurement matching KPIs are applied to this uploaded event log.')


def custom_workspace():
    path = dataset_picker('custom')
    st.title('Analyze My Dataset')
    if not path: upload_workflow(); return
    with st.expander('Import another dataset'): upload_workflow()
    service = CustomAnalytics(path)
    if service.metadata['profile'] == 'complaints': complaints_workspace(path); return
    source_banner(service.metadata)
    if service.metadata['profile'] == 'event_log':
        # Avoid promising event-filter behavior on a row-filtered table.
        scope = service.metadata['dataset_id'][:16]
        custom_process(service, scope); developer_tools(service, scope); return
    filters = tabular_filters(service, path); service = CustomAnalytics(path, filters)
    signature = Path(path).stat()
    scope = hashlib.sha256((service.metadata['dataset_id'] + json.dumps(asdict(filters), default=str, sort_keys=True)).encode()).hexdigest()[:16]
    if st.session_state.get('tabular_scope') != scope: st.session_state['tabular_history'] = []; st.session_state['tabular_scope'] = scope
    page = st.sidebar.radio('Custom analytics', ['Dataset Overview', 'AI Business Analyst'], key='custom-page')
    if page == 'AI Business Analyst': tabular_analyst(service, scope); developer_tools(service, scope); return
    def get(key): return calculation(path, signature.st_mtime_ns, signature.st_size, filters, 'custom', 'investigate', (key,))
    result = get('summary'); values = dict(zip(result['columns'], result['rows'][0])); capabilities = service.metadata['capabilities']
    st.subheader('Compatible analytical view')
    metric_columns = st.columns(2) if 'numeric_distribution' in capabilities else st.columns(1)
    metric_columns[0].metric('Selected records', f'{values["records"]:,}')
    if not values['records']: st.info('No records match these filters. Broaden the selection.')
    if 'numeric_distribution' in capabilities:
        metric_columns[1].metric('Confirmed revenue' if service.metadata['revenue_confirmed'] else 'Mapped numeric total', number(values['total_measure']))
        st.caption(f'{values["numeric_records"]:,} valid numeric records. No currency or profit assumption is made.')
    else: st.info('Numeric KPIs are disabled: no approved numeric measure is available.')
    if 'trends' in capabilities:
        draw(visual.trend(get('trend'), 'month', 'records', unit='Records'), 'custom-trend-' + scope)
        if service.metadata['revenue_confirmed']: draw(visual.trend(get('trend'), 'month', 'total_measure', unit='Confirmed revenue · user units'), 'custom-revenue-' + scope)
    else: st.info('Time trends are disabled: no date role was approved.')
    if 'groups' in capabilities: draw(visual.ranked(get('groups'), 'category', 'records', unit='Records'), 'custom-groups-' + scope)
    if 'numeric_distribution' in capabilities: draw(visual.histogram(get('distribution'), label='band', value='records', unit='Records'), 'custom-distribution-' + scope)
    if 'customer_segments' in capabilities: draw(visual.ranked(get('customers'), 'customer', 'records', unit='Records'), 'custom-customers-' + scope)
    if service.metadata['mapping'].get('category') or service.metadata['mapping'].get('product') or service.metadata['mapping'].get('company'): record_inspector(service, scope)
    else:
        rows = service.records(); frame = charts.frame(rows); st.dataframe(frame, hide_index=True)
        st.download_button('Download selected records · first 100', frame.to_csv(index=False), file_name='selected-records.csv')
    evidence_panel(result); developer_tools(service, scope)


def main():
    st.set_page_config(page_title='InsightPilot · AI Business Intelligence', page_icon='◈', layout='wide')
    st.markdown(ui.SHELL, unsafe_allow_html=True)
    st.sidebar.markdown('<div class="brand"><div class="brand-mark">IP</div><div><div class="brand-name">InsightPilot</div><div class="brand-sub">AI BUSINESS INTELLIGENCE</div></div></div>', unsafe_allow_html=True)
    if 'pending_workspace' in st.session_state:
        st.session_state['dataset_workspace'] = st.session_state.pop('pending_workspace'); switch_workspace()
    # Explicit legacy process configuration opens its existing workspace. Fresh
    # installs see dataset selection first, preserving existing launch commands.
    if 'dataset_workspace' not in st.session_state:
        st.session_state['dataset_workspace'] = WORKSPACES[1] if os.environ.get('INSIGHTPILOT_DATABASE') else WORKSPACES[0]
    workspace = st.sidebar.selectbox('Dataset workspace', WORKSPACES, key='dataset_workspace', on_change=switch_workspace)
    try:
        if workspace == WORKSPACES[0]: landing()
        elif workspace == WORKSPACES[1]: ui.main(embedded=True)
        elif workspace == WORKSPACES[2]: complaints_workspace()
        elif workspace == WORKSPACES[3]: custom_workspace()
        else:
            from .universal.ui import main as universal_main
            universal_main()
    except (ValueError, OSError, duckdb.Error) as exc:
        st.error('This dataset view could not finish. Review the source and mapping or choose another dataset.')
        with st.expander('View diagnostic'): st.write(f'{type(exc).__name__}: {exc}')
