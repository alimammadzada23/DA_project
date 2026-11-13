"""Phase 4: findings digest, report exports, SVG charts and the optional local-AI summary checks."""
import csv
import io
import json
from pathlib import Path
import re

import plotly.graph_objects as go
import pytest

from insightpilot.universal import Workspace
from insightpilot.universal import auto_analysis as aa
from insightpilot.universal.analyst_interpret import CAUSAL
from insightpilot.universal.auto import narrative
from insightpilot.universal.auto.report import chart_svg, decode, evidence, findings_csv, nice_ticks, results
from insightpilot.universal.auto_report import html_report

FIXTURES = Path(__file__).parent / 'fixtures'
KEYS = ('02-leakage', '05-simpson', '06-interaction', '07-nonlinear', '08-null', '16-missingness')


@pytest.fixture(scope='module')
def runs(tmp_path_factory):
    workspace = Workspace.create(tmp_path_factory.mktemp('findings') / 'w'); output = {}
    for key in KEYS:
        table = 't' + key[:2]
        workspace.add_file(FIXTURES / f'ENGINE-{key}.csv', table, provenance={'origin': 'synthetic'})
        output[key[:2]] = aa.run(workspace, table, target='outcome', target_type='binary', positive='yes', save_evidence=False)
    output['workspace'] = workspace
    return output


def types(output):
    return [f['type'] for f in output['findings']]


def by_type(output, kind):
    return [f for f in output['findings'] if f['type'] == kind]


def test_ids_are_sequential_and_severity_ordered(runs):
    for key in (k[:2] for k in KEYS):
        findings = runs[key]['findings']
        assert [f['id'] for f in findings] == [f'F{i:02d}' for i in range(1, len(findings) + 1)]
        order = {'critical': 0, 'important': 1, 'informative': 2}
        assert [order[f['severity']] for f in findings] == sorted(order[f['severity']] for f in findings)
        assert runs[key]['finding_counts'] == {s: sum(f['severity'] == s for f in findings) for s in order}


def test_every_finding_links_to_existing_evidence_and_has_no_causal_wording(runs):
    for key in (k[:2] for k in KEYS):
        output = runs[key]
        ids = {i.get('id') for i in aa.iter_items(output)}
        for f in output['findings']:
            assert f['evidence'] in ids, (key, f)
            assert not CAUSAL.search(f['statement']), f['statement']
            assert len(f['fields']) == len(set(f['fields']))
            assert f['label'] and f['statement'].endswith('.')


def test_planted_truths_become_findings(runs):
    assert types(runs['02'])[:2] == ['leakage_suspect', 'leakage_suspect']
    assert {f['fields'][0] for f in by_type(runs['02'], 'leakage_suspect')} == {'copy', 'closed_on'}
    assert all(f['severity'] == 'critical' for f in by_type(runs['02'], 'leakage_suspect'))
    reversal = [f for f in by_type(runs['05'], 'confounding_or_simpson') if f['fields'] == ['treatment', 'stratum']]
    assert reversal and 'reverses direction' in reversal[0]['statement'] and reversal[0]['severity'] == 'important'
    assert [f['fields'] for f in by_type(runs['06'], 'interaction')] == [['b', 'a']] or [f['fields'] for f in by_type(runs['06'], 'interaction')] == [['a', 'b']]
    assert by_type(runs['07'], 'nonlinear_shape')[0]['fields'] == ['x']
    assert types(runs['08']) == ['no_signal']
    assert by_type(runs['16'], 'deterministic_missingness')


def test_null_results_and_non_monotone_associations(runs):
    assert {f['fields'][0] for f in by_type(runs['05'], 'null_result')} == {'noise'}
    # A U-shape is missed by the rank test but found by the permutation test, with a caveat saying so.
    u_shape = [f for f in by_type(runs['07'], 'strong_association') if f['fields'] == ['x']]
    assert u_shape and any('not monotone' in c for c in u_shape[0]['caveats'])
    # A field that only matters jointly is a null result alone, and its caveat points at the interaction.
    alone = [f for f in by_type(runs['06'], 'null_result') if f['fields'] == ['a']]
    assert alone and 'interaction' in ' '.join(alone[0]['caveats'])


def test_association_numbers_match_the_rate_table(runs):
    output = runs['05']
    finding = next(f for f in by_type(output, 'strong_association') if f['fields'] == ['treatment'])
    table = next(i for i in aa.iter_items(output) if i.get('id') == finding['evidence'])
    names = [c['name'] for c in table['columns']]
    rates = [round(r[names.index('Positive rate')] * 100, 1) for r in table['rows']]
    assert any(n in rates for n in finding['numbers'])


def test_results_json_is_deterministic_and_complete(runs):
    workspace = runs['workspace']
    again = aa.run(workspace, 't05', target='outcome', target_type='binary', positive='yes', save_evidence=False)
    first, second = results(runs['05']), results(again)
    assert first == second
    data = json.loads(first)
    assert data['findings'] == json.loads(json.dumps(runs['05']['findings']))
    assert 'run_id' not in data and 'created_at' not in data and data['config_hash'] == runs['05']['config_hash']
    assert {t['id'] for t in data['tables']} == {i['id'] for i in aa.iter_items(runs['05']) if i['type'] == 'table'}
    assert runs['05']['analysis_version'] not in first and 'version:analysed' in first


def test_findings_csv_and_evidence_json(runs):
    output = runs['02']
    reader = list(csv.reader(io.StringIO(findings_csv(output))))
    assert reader[0] == ['id', 'severity', 'type', 'label', 'statement', 'evidence', 'fields', 'caveats']
    assert [r[0] for r in reader[1:]] == [f['id'] for f in output['findings']]
    assert not any(cell.lstrip().startswith(('=', '+', '-', '@')) for row in reader for cell in row)
    entries = json.loads(evidence(output))
    assert len(entries) == sum(1 for i in aa.iter_items(output) if i['type'] != 'text')
    assert all(e['version'] and e['computation'] for e in entries)


def test_report_puts_findings_first_and_stays_small(runs):
    output = runs['05']
    html = html_report(output)
    assert html.index('id="section-findings"') < html.index('id="section-drivers"') < html.index('id="section-evidence"')
    assert html.index('id="section-conclusions"') < html.index('id="section-evidence"')
    assert re.search(r'<div class="conclusions"></div>', html) and 'section-ai' not in html
    assert len(html.encode()) < 2_000_000 and '<svg' in html and 'plotly' not in html.lower()
    for f in output['findings'][:10]:
        assert f'id="finding-{f["id"]}"' in html


# ---------------- SVG charts ----------------
def test_svg_charts_decode_typed_arrays_and_cover_the_largest_value():
    fig = go.Figure(go.Bar(x=[0.1, 0.43], y=['a', 'b'], orientation='h', error_x={'type': 'data', 'array': [0.01, 0.02]}))
    fig.add_shape(type='line', x0=0.265, x1=0.265, y0=0, y1=1, yref='paper')
    fig.update_layout(xaxis_tickformat='.0%')
    svg = chart_svg(fig.to_json(), 'Rates')
    assert svg.startswith('<figure class="chart"><svg') and 'overall 26.5%' in svg and 'Largest: b (43.0%)' in svg
    assert decode({'dtype': 'i2', 'bdata': 'AQACAA==', 'shape': '2'}) == [1, 2]
    assert nice_ticks(0, 0.487)[-1] >= 0.487 and nice_ticks(-3.2, 10.1)[0] <= -3.2
    heat = go.Figure(go.Heatmap(z=[[1, 5], [3, 2]], x=['p', 'q'], y=['r', 's']))
    assert 'Largest cell: r × q (5)' in chart_svg(heat.to_json(), 'Counts')
    assert chart_svg(go.Figure(go.Pie(values=[1, 2])).to_json(), 'Pie') is None
    assert '&lt;b&gt;' in chart_svg(go.Figure(go.Bar(x=['<b>'], y=[1])).to_json(), 'x')


# ---------------- optional local-AI summary ----------------
class FakeClient:
    def __init__(self, value):
        self.value = value; self.seen = None

    def interpret(self, messages):
        self.seen = messages
        return self.value, {'model': 'fake-local'}


def test_local_ai_sentences_must_cite_findings_and_reuse_their_numbers(runs):
    output = runs['05']
    first = output['findings'][0]
    number = next(t for t in re.findall(r'\d[\d,]*\.\d+%?', first['statement']))
    value = {'headline': f'The largest gap is {number} [{first["id"]}].',
             'findings': [f'One group differs by 99.9% [{first["id"]}].', 'Something without a citation.',
                          f'This happens because of the stratum [{first["id"]}].', 'Unknown finding [F99].'],
             'caveats': [], 'follow_ups': ['Ignored question?']}
    client = FakeClient(value)
    section = narrative.summarise(output, client)
    assert [s['text'] for s in section['sentences']] == [value['headline']]
    reasons = sorted(r['reason'].split(' ')[0] for r in section['rejected'])
    assert len(section['rejected']) == 4 and set(reasons) == {'number', 'no', 'causal', 'cites'}
    sent = json.loads(client.seen[1]['content'])
    assert set(sent) == {'target', 'findings'} and all(set(f) == {'id', 'type', 'severity', 'statement'} for f in sent['findings'])
    html = html_report(output, section)
    assert html.index('id="section-ai"') < html.index('id="section-conclusions"') and value['headline'] in html
    assert re.search(r'<div class="conclusions"></div>', html)
