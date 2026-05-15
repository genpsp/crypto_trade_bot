from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _fmt_jpy(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"¥{float(value):,.0f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "-"


def _fmt_ratio(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_num(value: Any) -> str:
    if value is None:
        return "-"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "-"
    if abs(f) >= 1000:
        return f"{f:,.2f}"
    return f"{f:.4f}"


def _fmt_qty(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "-"


def _pn_class(value: Any) -> str:
    if value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return ""
    if f > 0:
        return "positive"
    if f < 0:
        return "negative"
    return ""


def render_report_html(payload: dict[str, Any]) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("report.html.j2")
    return template.render(
        fmt_jpy=_fmt_jpy,
        fmt_pct=_fmt_pct,
        fmt_ratio=_fmt_ratio,
        fmt_num=_fmt_num,
        fmt_qty=_fmt_qty,
        pn_class=_pn_class,
        **payload,
    )
