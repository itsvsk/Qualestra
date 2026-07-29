from .defects import build_defect
from .generator import compare_run_reports, generate_run_report, report_source_fingerprint
from .markdown import defect_markdown
from .renderers import (
    render_comparison_html,
    render_comparison_json,
    render_comparison_markdown,
    render_report_html,
    render_report_json,
    render_report_markdown,
)

__all__ = [
    "build_defect",
    "compare_run_reports",
    "defect_markdown",
    "generate_run_report",
    "report_source_fingerprint",
    "render_comparison_html",
    "render_comparison_json",
    "render_comparison_markdown",
    "render_report_html",
    "render_report_json",
    "render_report_markdown",
]
