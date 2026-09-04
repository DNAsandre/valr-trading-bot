"""Human-readable reporting for simulated XRP/ZAR performance."""

from __future__ import annotations


def format_paper_daily_report(report: dict[str, float | int]) -> str:
    return (
        "📄 *XRP/ZAR PAPER DAILY REPORT*\n\n"
        f"Fills: {report['fills']}\n"
        f"Realised P&L: R {float(report['realized_pnl_zar']):,.2f}\n"
        f"Unrealised P&L: R {float(report['unrealized_pnl_zar']):,.2f}\n"
        f"*Total P&L: R {float(report['total_pnl_zar']):,.2f}*\n\n"
        f"Marked equity: *R {float(report['equity_zar']):,.2f}*\n"
        f"Paper ZAR: R {float(report['zar_balance']):,.2f}\n"
        f"Paper XRP: {float(report['xrp_balance']):,.8f}\n\n"
        "⚠️ Simulation only — no real VALR order was placed."
    )
