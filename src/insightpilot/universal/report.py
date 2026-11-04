"""Portable, escaped reports and reproducible analysis configurations."""
from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
import io
import json
from urllib.parse import urlsplit

import pandas as pd

from .engine import Engine, Filter, frame_of
from .metrics import Metric


def configuration(engine,metric):
    return {'format_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),
        'table':engine.table,'version':engine.info['current'],'source':engine.info['source'],
        'grain':engine.info['grain'],'roles':engine.info['roles'],
        'semantics':engine.info.get('semantics',{}),'context':engine.context.dictionary(),
        'transformations':engine.info['versions'],'filters':[asdict(f) for f in engine.filters],
        'metric':asdict(metric),'limitations':'Version-bound local analysis; user-supplied provenance is not authenticated.'}


def replay(workspace,config):
    if not isinstance(config,dict) or config.get('format_version')!=1: raise ValueError('Unsupported analysis manifest version.')
    try:
        info=workspace.info(config['table'])
        if config['source']['sha256']!=info['source']['sha256']: raise ValueError('Source hash mismatch; this is a different dataset.')
        if len(config['filters'])>20: raise ValueError('Too many saved filters.')
        engine=Engine(workspace,config['table'],[Filter(**f) for f in config['filters']],version=config['version'])
        roles=config['roles']
        if not isinstance(roles,dict) or set(roles)-set(engine.info['columns']): raise ValueError('Saved roles reference unavailable columns.')
        engine.info['roles']=roles; engine.info['grain']=config['grain']
        engine.info['semantics']=config.get('semantics',{})
        if config.get('context'):
            from .context import AnalysisContext
            engine.context=AnalysisContext(**config['context']).validate(engine.info)
        metric=Metric(**config['metric']); metric.compile(engine.info['columns'],roles)
        return engine,metric
    except (KeyError,TypeError) as exc: raise ValueError('Incomplete or invalid analysis manifest.') from exc


def html_report(engine,metric,findings=(),evidence=(),figures=(),title='InsightPilot analytical report'):
    summary=engine.metric(metric); row=dict(zip(summary['columns'],summary['rows'][0]))
    config=configuration(engine,metric)
    content=['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
        '<title>'+escape(title)+'</title><style>body{font:16px system-ui;color:#172c40;max-width:1100px;margin:40px auto;padding:20px;line-height:1.6}h1,h2{line-height:1.2}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:8px;border-bottom:1px solid #cad4df;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f8;padding:16px}.note{color:#4b5f74}</style>',
        '<h1>'+escape(title)+'</h1>', '<p>Dataset: '+escape(engine.table)+' · source: '+escape(config['source']['origin'])+'</p>',
        '<p>'+escape(str(config['grain']))+'</p>',
        '<h2>Metric overview</h2><p>'+escape(metric.name)+': <strong>'+escape(str(row['value']))+'</strong> '+escape(metric.units)+'</p>',
        '<p>'+escape(metric.description)+' Formula: <code>'+escape(metric.formula)+'</code></p>',
        '<p class="note">'+escape(metric.limitations)+'</p>',
        '<h2>Findings and investigations</h2>']
    source_url=config['source'].get('url','')
    parsed=urlsplit(source_url)
    if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password:
        content.insert(-1,'<p>Source extract: <a href="'+escape(source_url,quote=True)+'">'+escape(source_url)+'</a></p>')
    if metric.components:
        content.insert(-1,'<p>Eligible denominator: '+escape(metric.denominator or metric.components['denominator'])+
            '. Executed numerator: '+escape(str(row.get('numerator')))+'; executed denominator: '+escape(str(row.get('denominator')))+'. Zero denominators are unavailable.</p>')
    for finding in findings:
        for label,key in [('Finding','finding'),('Context','context'),('Limitations','limitations'),('Investigation','investigation'),('Possible action','possible_action')]:
            content.append('<p><strong>'+label+':</strong> '+escape(str(finding[key]))+'</p>')
        content.append('<details><summary>Finding evidence</summary><pre>'+escape(json.dumps(finding['evidence'],default=str,indent=2))+'</pre></details>')
    for figure in figures:
        content.append(figure.to_html(full_html=False,include_plotlyjs=True))
    content.append('<h2>Executed evidence</h2>')
    for item in [summary,*evidence]:
        content.append(frame_of(item).head(100).to_html(index=False,escape=True))
        content.append('<details><summary>Query, filters and provenance</summary><pre>'+escape(json.dumps(item,default=str,indent=2))+'</pre></details>')
    content.append('<h2>Reproduction manifest</h2><pre>'+escape(json.dumps(config,default=str,indent=2))+'</pre>')
    content.append('<p class="note">Descriptive findings are not proven causes. Source authenticity, reporting completeness, sampling and metric definitions require review. No business outcome is implied.</p></html>')
    return '\n'.join(content)


def safe_spreadsheet_frame(frame):
    frame=frame.copy()
    # Untrusted cell values must not turn into spreadsheet formulas on export.
    frame.columns=["'"+str(c) if str(c).lstrip().startswith(('=','+','-','@')) else c for c in frame.columns]
    for col in frame.columns:
        frame[col]=frame[col].map(lambda v: "'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else
                                v.isoformat() if isinstance(v,datetime) and v.tzinfo is not None else v)
    return frame


def csv_export(result):
    return safe_spreadsheet_frame(frame_of(result)).to_csv(index=False)


def excel_export(result):
    buffer=io.BytesIO(); frame=safe_spreadsheet_frame(frame_of(result))
    with pd.ExcelWriter(buffer,engine='openpyxl') as writer: frame.to_excel(writer,index=False,sheet_name='Selected analysis')
    return buffer.getvalue()
