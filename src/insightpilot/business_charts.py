"""Reusable, bounded visualizations for mapped tabular datasets."""
import plotly.graph_objects as go
from .charts import AMBER, BLUE, TEAL, MUTED, frame, style

COLORS = [TEAL, BLUE, AMBER, '#B89AFF', '#ED8EAC', '#6986A6']


def donut(result, label='category', value='complaints'):
    data = frame(result)
    fig = go.Figure(go.Pie(labels=data[label].fillna('Not recorded'), values=data[value], hole=.68,
        sort=False, marker={'colors': COLORS}, textinfo='percent',
        hovertemplate='%{label}<br>%{value:,} selected records<br>%{percent} of this selection<extra></extra>'))
    fig.update_layout(showlegend=True, legend={'orientation': 'h', 'y': -.15}, margin={'b': 100})
    return style(fig, height=410)


def ranked(result, label, value, *, unit='Reported complaints', top=12, percentage=False):
    data = frame(result).head(top)
    fig = go.Figure(go.Bar(x=data[value], y=data[label].fillna('Not recorded'), orientation='h',
        marker_color=TEAL, customdata=data[label].to_numpy(dtype=object).reshape(-1, 1),
        hovertemplate='%{y}<br>%{x:,.1%}<extra></extra>' if percentage else '%{y}<br>%{x:,} '+unit.lower()+'<extra></extra>'))
    style(fig, x_title=unit, height=max(320, min(530, 100+34*len(data))))
    fig.update_yaxes(autorange='reversed')
    if percentage: fig.update_xaxes(tickformat='.0%')
    return fig


def trend(result, date='record_date', value='complaints', *, unit='Reported complaints', group=None):
    data = frame(result); fig = go.Figure()
    if group:
        for index, (name, subset) in enumerate(data.groupby(group, dropna=False, sort=False)):
            if index >= 8: break
            fig.add_trace(go.Scatter(x=subset[date], y=subset[value], name=str(name), mode='lines+markers',
                line={'color': COLORS[index % len(COLORS)]}, hovertemplate='%{x|%Y-%m-%d}<br>%{y:,} '+unit.lower()+'<extra>%{fullData.name}</extra>'))
    else:
        fig.add_trace(go.Scatter(x=data[date], y=data[value], mode='lines+markers', line={'color': TEAL, 'width': 3},
            fill='tozeroy', fillcolor='rgba(97,213,197,.07)', hovertemplate='%{x|%Y-%m-%d}<br>%{y:,} '+unit.lower()+'<extra></extra>'))
    return style(fig, x_title='Received date' if date == 'record_date' else 'Month (monthly totals)' if date == 'month' else 'Mapped date', y_title=unit)


def stacked(result, group='company', category='product', value='complaints'):
    data = frame(result); fig = go.Figure()
    # Keep the most represented companies and combine excess categories explicitly.
    if not data.empty:
        companies = data.groupby(group, dropna=False)[value].sum().nlargest(8).index
        data = data[data[group].isin(companies)].copy()
        data[group] = data[group].fillna('Not recorded'); data[category] = data[category].fillna('Not recorded')
        top = data.groupby(category)[value].sum().nlargest(5).index
        data[category] = data[category].where(data[category].isin(top), 'Other categories (combined)')
        for i, (name, rows) in enumerate(data.groupby(category, sort=False)):
            grouped = rows.groupby(group, as_index=False)[value].sum()
            fig.add_trace(go.Bar(x=grouped[group], y=grouped[value], name=name, marker_color=COLORS[i % len(COLORS)],
                hovertemplate='%{x}<br>%{y:,} reported complaints<extra>%{fullData.name}</extra>'))
    fig.update_layout(barmode='stack', legend={'orientation': 'h', 'y': -.4}, margin={'b': 150})
    return style(fig, x_title='Company · up to 8 by volume', y_title='Reported complaints', height=490)


def heatmap(result):
    data = frame(result)
    if data.empty: return style(go.Figure(), x_title='Product', y_title='Company')
    data = data.copy(); data['company'] = data.company.fillna('Not recorded'); data['product'] = data['product'].fillna('Not recorded')
    companies = data.groupby('company')['complaints'].sum().nlargest(8).index
    products = data.groupby('product')['complaints'].sum().nlargest(8).index
    matrix = data[data.company.isin(companies) & data['product'].isin(products)].pivot_table(index='company', columns='product', values='complaints', aggfunc='sum', fill_value=0)
    fig = go.Figure(go.Heatmap(z=matrix.values, x=matrix.columns, y=matrix.index,
        colorscale=[[0, '#142137'], [1, TEAL]], colorbar={'title': 'Complaints'},
        hovertemplate='%{y}<br>%{x}<br>%{z:,} reported complaints<extra></extra>'))
    return style(fig, x_title='Product · top 8 shown', y_title='Company · top 8 shown', height=450)


def histogram(result, *, label='volume_band', value='companies', unit='Represented companies'):
    data = frame(result)
    fig = go.Figure(go.Bar(x=data[label], y=data[value], marker_color=BLUE,
        hovertemplate='%{x}<br>%{y:,} '+unit.lower()+'<extra></extra>'))
    return style(fig, x_title='Reported complaints per company' if label == 'volume_band' else 'Mapped numeric measure · bin', y_title=unit)
