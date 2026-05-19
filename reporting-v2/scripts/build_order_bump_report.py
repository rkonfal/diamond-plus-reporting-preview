#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import shlex
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path('/Users/rudolfkonfal/.openclaw/workspace/reporting-v2')
SITE_DIR = ROOT / 'site'
CURRENT_DIR = ROOT / 'data' / 'current'
TARGET_HTML = SITE_DIR / 'order-bump.html'
TARGET_JSON = CURRENT_DIR / 'order_bump_report.json'
DEFAULT_HOST = os.environ.get('ORDER_BUMP_SSH_HOST', 'root@70.34.246.98')
DEFAULT_HOURS = int(os.environ.get('ORDER_BUMP_REPORT_HOURS', '48'))
SSH_KEY = os.environ.get('ORDER_BUMP_SSH_KEY', str(Path.home() / '.ssh' / 'rudolf_tiande_key'))
MAIN_DB = 'main_db'
WAREHOUSE_DB = 'eshop_analytics'
EVENT_TYPES = ('order_bump_accepted', 'order_bump_dismissed', 'cart_related_added')
LOCAL_TZ = timezone(timedelta(hours=2))


@dataclass(frozen=True)
class EventRow:
    event_type: str
    created_at: datetime
    market: str
    product_id: int
    product_code: str
    product_name: str
    display_price: float


@dataclass(frozen=True)
class OrderRow:
    order_code: str
    created_at: datetime
    market: str
    order_total_czk: float
    fx_rate_used: float


def parse_timestamp(value: str) -> datetime:
    normalized = (value or '').strip()
    if normalized.endswith('+00'):
        normalized = normalized[:-3] + '+00:00'
    parsed = datetime.fromisoformat(normalized)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def as_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def as_int(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def ssh_prefix() -> list[str]:
    cmd = ['ssh', '-o', 'StrictHostKeyChecking=accept-new']
    key_path = Path(SSH_KEY).expanduser()
    if key_path.exists():
        cmd.extend(['-i', str(key_path)])
    cmd.append(DEFAULT_HOST)
    return cmd


def run_psql(*, db: str, query: str) -> list[list[str]]:
    compact_query = ' '.join(query.split())
    remote_cmd = (
        f"docker exec postgres-main psql -U postgres -d {shlex.quote(db)} "
        f"-AtF '|' -c {shlex.quote(compact_query)}"
    )
    output = subprocess.check_output([*ssh_prefix(), remote_cmd], text=True)
    return [line.split('|') for line in output.splitlines() if line.strip()]


def fetch_events(*, cutoff: datetime) -> list[EventRow]:
    event_values = ', '.join("'" + value + "'" for value in EVENT_TYPES)
    rows = run_psql(
        db=MAIN_DB,
        query=f"""
            SELECT
                event_type,
                created_at,
                COALESCE(market_code, payload->>'market', ''),
                COALESCE(payload->>'product_id', ''),
                COALESCE(product_code, payload->>'product_code', ''),
                COALESCE(payload->>'product_name', ''),
                COALESCE(display_price, (payload->>'display_price')::numeric, 0)
            FROM personalization_events
            WHERE created_at >= timestamp with time zone '{cutoff.isoformat()}'
              AND event_type IN ({event_values})
            ORDER BY created_at
        """,
    )
    return [
        EventRow(
            event_type=row[0],
            created_at=parse_timestamp(row[1]),
            market=(row[2] or '?').upper(),
            product_id=as_int(row[3]),
            product_code=row[4],
            product_name=row[5],
            display_price=as_float(row[6]),
        )
        for row in rows
    ]


def fetch_orders(*, cutoff: datetime) -> list[OrderRow]:
    rows = run_psql(
        db=WAREHOUSE_DB,
        query=f"""
            SELECT
                order_code,
                order_created_at,
                CASE WHEN currency = 'EUR' OR country_code = 'SK' THEN 'SK' ELSE 'CZ' END AS market,
                COALESCE(order_total_czk, 0),
                COALESCE(fx_rate_used, 1)
            FROM orders
            WHERE order_created_at >= timestamp with time zone '{cutoff.isoformat()}'
              AND is_counted IS TRUE
            ORDER BY order_created_at
        """,
    )
    return [
        OrderRow(
            order_code=row[0],
            created_at=parse_timestamp(row[1]),
            market=row[2],
            order_total_czk=as_float(row[3]),
            fx_rate_used=as_float(row[4]) or 1.0,
        )
        for row in rows
    ]


def format_int(value: int) -> str:
    return f'{int(value):,}'.replace(',', ' ')


def format_money(value: float) -> str:
    return f"{round(value):,}".replace(',', ' ') + ' Kč'


def format_pct(numerator: int | float, denominator: int | float) -> str:
    if not denominator:
        return '0,0 %'
    return f'{100 * numerator / denominator:.1f}'.replace('.', ',') + ' %'


def hour_key(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def local_dt(value: datetime) -> datetime:
    return value.astimezone(LOCAL_TZ)


def local_label(value: datetime) -> str:
    return local_dt(value).strftime('%d.%m. %H:%M')


def short_hour(value: datetime) -> str:
    return local_dt(value).strftime('%H')


def event_value_czk(event: EventRow, sk_fx_rate: float) -> float:
    return event.display_price * sk_fx_rate if event.market == 'SK' else event.display_price


def product_key(event: EventRow) -> tuple[int, str, str, str]:
    return (event.product_id, event.product_code, event.product_name, event.market)


def table_html(headers: list[str], rows: list[list[Any]], numeric_indexes: set[int] | None = None) -> str:
    numeric_indexes = numeric_indexes or set()
    thead = ''.join(f'<th>{html.escape(str(header))}</th>' for header in headers)
    body_rows = []
    for row in rows:
        cells = []
        for idx, value in enumerate(row):
            classes = []
            if idx in numeric_indexes:
                classes.append('num')
            class_attr = f' class="{" ".join(classes)}"' if classes else ''
            data_label = html.escape(str(headers[idx])) if idx < len(headers) else ''
            cells.append(f'<td{class_attr} data-label="{data_label}">{html.escape(str(value))}</td>')
        body_rows.append('<tr>' + ''.join(cells) + '</tr>')
    return f'<div class="ux-table-wrap order-bump-table-wrap"><table class="table mobile-stack"><thead><tr>{thead}</tr></thead><tbody>{"".join(body_rows)}</tbody></table></div>'


def svg_value_chart(hours: list[datetime], hourly: dict[datetime, Counter[str]]) -> str:
    width = 1120
    height = 382
    left = 72
    right = 18
    top = 18
    bottom = 58
    plot_w = width - left - right
    plot_h = height - top - bottom
    gap = 7
    bar_w = max(5.0, (plot_w / max(len(hours), 1)) - gap)
    max_value = max([
        max(hourly[hour]['orders_value_czk'], 0)
        + max(hourly[hour]['bump_net_value_czk'], 0)
        + max(hourly[hour]['related_added_value_czk'], 0)
        for hour in hours
    ] or [1])
    grid_max = max(max_value, 1)
    series = (
        ('orders_value_czk', '#1d4ed8', 'Objednávky'),
        ('bump_net_value_czk', '#16a34a', 'Bump netto'),
        ('related_added_value_czk', '#db2777', 'Doplňkové přidáno'),
    )
    parts = [
        '<svg viewBox="0 0 1120 382" class="chartsvg" role="img" aria-label="Hodinový graf v korunách">',
        '<rect x="0" y="0" width="1120" height="382" fill="#ffffff" rx="14"/>',
    ]
    for index in range(5):
        value = grid_max * (4 - index) / 4
        y = top + plot_h * index / 4
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#dbe6f1" stroke-width="1"/>')
        parts.append(f'<text x="8" y="{y + 4:.1f}" fill="#64748b" font-size="11">{format_money(value)}</text>')
    group_w = plot_w / max(len(hours), 1)
    for index, hour in enumerate(hours):
        base_x = left + index * group_w + max((group_w - bar_w) / 2, 0)
        stack_y = top + plot_h
        total_value = 0.0
        for key, color, label in series:
            value = max(hourly[hour][key], 0)
            if value <= 0:
                continue
            bar_h = plot_h * value / grid_max
            y = stack_y - bar_h
            total_value += value
            title = f'{local_label(hour)} · {label}: {format_money(value)} · součet: {format_money(total_value)}'
            parts.append(
                f'<rect x="{base_x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" rx="2" fill="{color}" opacity="0.92">'
                f'<title>{html.escape(title)}</title></rect>'
            )
            stack_y = y
        if index % 4 == 0 or index == len(hours) - 1:
            parts.append(f'<text x="{base_x + bar_w / 2:.1f}" y="354" text-anchor="middle" fill="#64748b" font-size="10">{short_hour(hour)}</text>')
        if index == 0 or local_dt(hour).hour == 0:
            parts.append(f'<text x="{base_x + bar_w / 2:.1f}" y="372" text-anchor="middle" fill="#334155" font-size="10" font-weight="700">{local_dt(hour).strftime("%d.%m.")}</text>')
    parts.append('<text x="72" y="372" fill="#64748b" font-size="10">částky po hodinách v Kč, složené sloupce, bump je netto</text>')
    parts.append('</svg>')
    return '\n'.join(parts)


def build_product_rows(stats: dict[tuple[int, str, str, str], Counter[str]], *, sort_key: str, limit: int = 12) -> list[list[str]]:
    sorted_items = sorted(stats.items(), key=lambda item: item[1][sort_key], reverse=True)[:limit]
    rows: list[list[str]] = []
    for (_product_id, product_code, product_name, market), values in sorted_items:
        rows.append([
            market,
            product_code,
            product_name[:90],
            format_int(int(values['accepted_count'])),
            format_int(int(values['dismissed_count'])),
            format_int(int(values['net_count'])),
            format_money(values['net_value_czk']),
        ])
    return rows


def build_related_rows(stats: dict[tuple[int, str, str, str], Counter[str]], *, sort_key: str, limit: int = 12) -> list[list[str]]:
    sorted_items = sorted(stats.items(), key=lambda item: item[1][sort_key], reverse=True)[:limit]
    rows: list[list[str]] = []
    for (_product_id, product_code, product_name, market), values in sorted_items:
        rows.append([
            market,
            product_code,
            product_name[:90],
            format_int(int(values['added_count'])),
            format_money(values['added_value_czk']),
        ])
    return rows


def build_report(hours_back: int = DEFAULT_HOURS) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_back)
    events = fetch_events(cutoff=cutoff)
    orders = fetch_orders(cutoff=cutoff)

    sk_rates = [order.fx_rate_used for order in orders if order.market == 'SK' and order.fx_rate_used > 1]
    sk_fx_rate = sum(sk_rates) / len(sk_rates) if sk_rates else 25.0

    hourly: dict[datetime, Counter[str]] = defaultdict(Counter)
    orders_by_market = Counter()
    revenue_czk = 0.0
    for order in orders:
        revenue_czk += order.order_total_czk
        orders_by_market[order.market] += 1
        hk = hour_key(order.created_at)
        hourly[hk]['orders_count'] += 1
        hourly[hk]['orders_value_czk'] += order.order_total_czk

    bump_stats: dict[tuple[int, str, str, str], Counter[str]] = defaultdict(Counter)
    related_stats: dict[tuple[int, str, str, str], Counter[str]] = defaultdict(Counter)
    event_counts = Counter()
    event_values = Counter()

    for event in events:
        value_czk = event_value_czk(event, sk_fx_rate)
        key = product_key(event)
        hk = hour_key(event.created_at)
        event_counts[(event.event_type, event.market)] += 1
        event_values[(event.event_type, event.market)] += value_czk
        if event.event_type == 'order_bump_accepted':
            bump_stats[key]['accepted_count'] += 1
            bump_stats[key]['net_count'] += 1
            bump_stats[key]['accepted_value_czk'] += value_czk
            bump_stats[key]['net_value_czk'] += value_czk
            hourly[hk]['bump_net_count'] += 1
            hourly[hk]['bump_net_value_czk'] += value_czk
        elif event.event_type == 'order_bump_dismissed':
            bump_stats[key]['dismissed_count'] += 1
            bump_stats[key]['net_count'] -= 1
            bump_stats[key]['dismissed_value_czk'] += value_czk
            bump_stats[key]['net_value_czk'] -= value_czk
            hourly[hk]['bump_net_count'] -= 1
            hourly[hk]['bump_net_value_czk'] -= value_czk
        elif event.event_type == 'cart_related_added':
            related_stats[key]['added_count'] += 1
            related_stats[key]['added_value_czk'] += value_czk
            hourly[hk]['related_added_count'] += 1
            hourly[hk]['related_added_value_czk'] += value_czk

    bump_accepted = sum(event_counts[('order_bump_accepted', market)] for market in ('CZ', 'SK'))
    bump_dismissed = sum(event_counts[('order_bump_dismissed', market)] for market in ('CZ', 'SK'))
    bump_net = bump_accepted - bump_dismissed
    bump_net_value = sum(event_values[('order_bump_accepted', market)] for market in ('CZ', 'SK')) - sum(event_values[('order_bump_dismissed', market)] for market in ('CZ', 'SK'))
    related_added = sum(event_counts[('cart_related_added', market)] for market in ('CZ', 'SK'))
    related_added_value = sum(event_values[('cart_related_added', market)] for market in ('CZ', 'SK'))

    hours = []
    cursor = hour_key(cutoff)
    last_hour = hour_key(now)
    while cursor <= last_hour:
        hours.append(cursor)
        cursor += timedelta(hours=1)

    market_rows: list[list[str]] = []
    market_rows_raw: list[dict[str, Any]] = []
    for market in ('CZ', 'SK'):
        accepted = event_counts[('order_bump_accepted', market)]
        dismissed = event_counts[('order_bump_dismissed', market)]
        related = event_counts[('cart_related_added', market)]
        revenue_market = sum(order.order_total_czk for order in orders if order.market == market)
        accepted_value = event_values[('order_bump_accepted', market)]
        dismissed_value = event_values[('order_bump_dismissed', market)]
        net_value = accepted_value - dismissed_value
        related_value = event_values[('cart_related_added', market)]
        market_rows.append([
            market,
            format_int(orders_by_market[market]),
            format_money(revenue_market),
            format_int(accepted),
            format_int(dismissed),
            format_int(accepted - dismissed),
            format_money(net_value),
            format_int(related),
            format_money(related_value),
        ])
        market_rows_raw.append({
            'market': market,
            'orders_count': orders_by_market[market],
            'revenue_czk': round(revenue_market, 2),
            'bump_accepted': accepted,
            'bump_dismissed': dismissed,
            'bump_net': accepted - dismissed,
            'bump_net_value_czk': round(net_value, 2),
            'related_added': related,
            'related_added_value_czk': round(related_value, 2),
        })

    hourly_rows: list[list[str]] = []
    hourly_rows_raw: list[dict[str, Any]] = []
    for hour in reversed(hours[-48:]):
        counts = hourly[hour]
        hourly_rows.append([
            local_label(hour),
            format_int(int(counts['orders_count'])),
            format_money(counts['orders_value_czk']),
            format_int(int(counts['bump_net_count'])),
            format_money(counts['bump_net_value_czk']),
            format_int(int(counts['related_added_count'])),
            format_money(counts['related_added_value_czk']),
        ])
        hourly_rows_raw.append({
            'hour_start_utc': hour.isoformat(),
            'hour_label_local': local_label(hour),
            'orders_count': int(counts['orders_count']),
            'orders_value_czk': round(counts['orders_value_czk'], 2),
            'bump_net_count': int(counts['bump_net_count']),
            'bump_net_value_czk': round(counts['bump_net_value_czk'], 2),
            'related_added_count': int(counts['related_added_count']),
            'related_added_value_czk': round(counts['related_added_value_czk'], 2),
        })

    data = {
        'source_status': 'owned_direct_query',
        'source': {
            'events_db': 'main_db.personalization_events',
            'orders_db': 'eshop_analytics.orders',
            'transport': 'ssh + docker exec postgres-main psql',
            'host': DEFAULT_HOST,
        },
        'window_hours': hours_back,
        'generated_at_utc': now.isoformat(),
        'generated_at_local': local_dt(now).strftime('%d.%m.%Y %H:%M:%S'),
        'window_start_local': local_dt(cutoff).strftime('%d.%m. %H:%M'),
        'window_end_local': local_dt(now).strftime('%d.%m. %H:%M'),
        'sk_fx_rate': round(sk_fx_rate, 4),
        'metrics': {
            'order_count': len(orders),
            'revenue_czk': round(revenue_czk, 2),
            'bump_accepted': bump_accepted,
            'bump_dismissed': bump_dismissed,
            'bump_net': bump_net,
            'bump_net_value_czk': round(bump_net_value, 2),
            'related_added': related_added,
            'related_added_value_czk': round(related_added_value, 2),
            'addon_value_czk': round(bump_net_value + related_added_value, 2),
            'addon_share_pct': round(((bump_net_value + related_added_value) / revenue_czk * 100) if revenue_czk else 0.0, 2),
        },
        'market_breakdown': market_rows_raw,
        'tables': {
            'bump_by_count': build_product_rows(bump_stats, sort_key='net_count'),
            'bump_by_value': build_product_rows(bump_stats, sort_key='net_value_czk'),
            'related_by_count': build_related_rows(related_stats, sort_key='added_count'),
            'related_by_value': build_related_rows(related_stats, sort_key='added_value_czk'),
            'hourly': hourly_rows_raw,
        },
        'chart_hours_utc': [hour.isoformat() for hour in hours[-48:]],
    }

    chart_html = svg_value_chart(hours[-48:], hourly)
    data['rendered'] = {
        'kpi_order_count': format_int(len(orders)),
        'kpi_revenue_czk': format_money(revenue_czk),
        'kpi_bump_net': format_int(bump_net),
        'kpi_bump_sub': f"{format_int(bump_accepted)} přidáno - {format_int(bump_dismissed)} odebráno · {format_money(bump_net_value)}",
        'kpi_related_added': format_int(related_added),
        'kpi_related_value': format_money(related_added_value),
        'kpi_addon_value': format_money(bump_net_value + related_added_value),
        'kpi_addon_share': format_pct(bump_net_value + related_added_value, revenue_czk),
        'market_table': table_html(['Trh', 'Objednávky', 'Obrat', 'Bump +', 'Bump -', 'Bump netto', 'Bump netto Kč', 'Doplňkové +', 'Doplňkové Kč'], market_rows, {1, 2, 3, 4, 5, 6, 7, 8}),
        'bump_count_table': table_html(['Trh', 'Kód', 'Produkt', 'Přidáno', 'Odebráno', 'Netto', 'Netto Kč'], data['tables']['bump_by_count'], {3, 4, 5, 6}),
        'bump_value_table': table_html(['Trh', 'Kód', 'Produkt', 'Přidáno', 'Odebráno', 'Netto', 'Netto Kč'], data['tables']['bump_by_value'], {3, 4, 5, 6}),
        'related_count_table': table_html(['Trh', 'Kód', 'Produkt', 'Přidáno', 'Částka'], data['tables']['related_by_count'], {3, 4}),
        'related_value_table': table_html(['Trh', 'Kód', 'Produkt', 'Přidáno', 'Částka'], data['tables']['related_by_value'], {3, 4}),
        'hourly_table': table_html(['Hodina', 'Objednávky', 'Obrat', 'Bump netto ks', 'Bump netto Kč', 'Doplňkové ks', 'Doplňkové Kč'], hourly_rows, {1, 2, 3, 4, 5, 6}),
        'chart_html': chart_html,
    }
    return data


def render_page(report: dict[str, Any]) -> str:
    metrics = report['metrics']
    rendered = report['rendered']
    return f'''<!DOCTYPE html>
<html lang="cs">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Order bump, Reporting V2</title>
  <link rel="stylesheet" href="assets/styles.css" />
</head>
<body class="page-stack">
  <aside class="sidebar" data-sidebar-page="order-bump.html" data-sidebar-title="Diamond Plus" data-sidebar-subtitle="Order bump a doplňkové produkty" data-sidebar-section="E-shop" data-sidebar-footer="Plně vlastní report z order bump eventů a skutečných objednávek, bez závislosti na cloud HTML."></aside>

  <main class="main page-stack">
    <header class="header">
      <div class="ux-topbar">
        <div class="ux-intro">
          <div class="ux-kicker">E-shop • order bump performance</div>
          <h1 class="ux-title">Order bump report v našem standardu</h1>
          <p class="ux-subtitle">Vlastní report z <code>main_db.personalization_events</code> a <code>eshop_analytics.orders</code>. Aktuální okno, vlastní metodika, bez závislosti na cizím HTML.</p>
          <div class="ux-actions">
            <a class="ux-button secondary" href="eshop.html">E-shop</a>
            <a class="ux-button secondary" href="index.html">Přehled</a>
          </div>
        </div>
        <button class="theme-toggle" data-theme-toggle>Tmavý režim</button>
      </div>
      <section class="order-bump-kpis">
        <article class="kpi"><div class="lbl">Skutečné objednávky</div><div class="val">{rendered['kpi_order_count']}</div><div class="sub2">orders.is_counted=true · {rendered['kpi_revenue_czk']}</div></article>
        <article class="kpi"><div class="lbl">Bump netto</div><div class="val">{rendered['kpi_bump_net']}</div><div class="sub2">{rendered['kpi_bump_sub']}</div></article>
        <article class="kpi"><div class="lbl">Doplňkové přidáno</div><div class="val">{rendered['kpi_related_added']}</div><div class="sub2">cart_related_added · {rendered['kpi_related_value']}</div></article>
        <article class="kpi"><div class="lbl">Add-on hodnota</div><div class="val">{rendered['kpi_addon_value']}</div><div class="sub2">{rendered['kpi_addon_share']} proti obratu objednávek</div></article>
      </section>
      <div class="status-strip" id="order-bump-status-strip"></div>
      <div class="ux-date-note"><strong>Aktualizováno:</strong> {html.escape(report['generated_at_local'])} (CEST) · <strong>Zdroj:</strong> přímé read-only dotazy do <code>main_db.personalization_events</code> a <code>eshop_analytics.orders</code> přes server <code>{html.escape(report['source']['host'])}</code>.</div>
    </header>

    <section class="layer-shell">
      <div class="layer-head">
        <div>
          <div class="layer-label">Decision layer</div>
          <h2 class="layer-title">Co ten report říká</h2>
          <p class="layer-subtitle">Rychlé shrnutí metodiky a aktuálního okna.</p>
        </div>
      </div>
      <div class="order-bump-note">Okno reportu je <strong>{html.escape(report['window_start_local'])}</strong> až <strong>{html.escape(report['window_end_local'])}</strong>. Objednávky bereme z <strong>eshop_analytics.orders</strong> s podmínkou <strong>is_counted = true</strong>. Bump je <strong>accepted minus dismissed</strong>, doplňky jsou <strong>cart_related_added</strong>. SK hodnoty přepočítáváme kurzem <strong>{report['sk_fx_rate']:.2f} Kč/EUR</strong>.</div>
    </section>

    <section class="layer-shell">
      <div class="layer-head">
        <div>
          <div class="layer-label">Trend layer</div>
          <h2 class="layer-title">Hodinový průběh v Kč</h2>
          <p class="layer-subtitle">Společný hodinový průběh objednávek, bumpu a doplňků za posledních {report['window_hours']} hodin.</p>
        </div>
      </div>
      <div class="card order-bump-chart-card"><div class="legend"><span><i class="dot" style="background:#1d4ed8"></i>Objednávky</span><span><i class="dot" style="background:#16a34a"></i>Bump netto</span><span><i class="dot" style="background:#db2777"></i>Doplňkové přidáno</span></div>{rendered['chart_html']}</div>
    </section>

    <section class="layer-shell">
      <div class="layer-head">
        <div>
          <div class="layer-label">Market layer</div>
          <h2 class="layer-title">Rozpad podle trhu</h2>
          <p class="layer-subtitle">Srovnání CZ a SK v jednom pohledu.</p>
        </div>
      </div>
      <section class="card">{rendered['market_table']}</section>
    </section>

    <section class="layer-shell">
      <div class="layer-head">
        <div>
          <div class="layer-label">Work layer</div>
          <h2 class="layer-title">Bump produkty</h2>
          <p class="layer-subtitle">Top bump produkty podle počtu a podle hodnoty.</p>
        </div>
      </div>
      <section class="grid two-col order-bump-grid">
        <article class="card">
          <h2 class="ux-section-title">Nejčastější podle počtu</h2>
          {rendered['bump_count_table']}
        </article>
        <article class="card">
          <h2 class="ux-section-title">Nejsilnější podle částky</h2>
          {rendered['bump_value_table']}
        </article>
      </section>
    </section>

    <section class="layer-shell">
      <div class="layer-head">
        <div>
          <div class="layer-label">Add-on layer</div>
          <h2 class="layer-title">Doplňkové produkty</h2>
          <p class="layer-subtitle">Top doplňkové produkty podle počtu a podle hodnoty.</p>
        </div>
      </div>
      <section class="grid two-col order-bump-grid">
        <article class="card">
          <h2 class="ux-section-title">Nejčastější podle počtu</h2>
          {rendered['related_count_table']}
        </article>
        <article class="card">
          <h2 class="ux-section-title">Nejsilnější podle částky</h2>
          {rendered['related_value_table']}
        </article>
      </section>
    </section>

    <section class="layer-shell">
      <div class="layer-head">
        <div>
          <div class="layer-label">Detail layer</div>
          <h2 class="layer-title">Hodinová detailní data</h2>
          <p class="layer-subtitle">Hodinový detail pro dohledání špiček a propadů.</p>
        </div>
      </div>
      <section class="card">{rendered['hourly_table']}</section>
    </section>
  </main>

  <script src="assets/app.js"></script>
  <script>
    DP.initThemeToggle();
    DP.renderStatusStrip('order-bump-status-strip', [
      {{ label: 'Stav', value: 'Plně vlastní build', tone: 'success' }},
      {{ label: 'Zdroj objednávek', value: 'eshop_analytics.orders', tone: 'info' }},
      {{ label: 'Zdroj eventů', value: 'main_db.personalization_events', tone: 'info' }},
      {{ label: 'Transport', value: 'SSH + read-only SQL', tone: 'info' }},
      {{ label: 'Okno', value: 'Rolling {report['window_hours']} hodin', tone: 'info' }}
    ]);
  </script>
</body>
</html>
'''


def main() -> None:
    report = build_report(DEFAULT_HOURS)
    CURRENT_DIR.mkdir(parents=True, exist_ok=True)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    TARGET_HTML.write_text(render_page(report), encoding='utf-8')
    print(f'Wrote {TARGET_JSON}')
    print(f'Wrote {TARGET_HTML}')


if __name__ == '__main__':
    main()
