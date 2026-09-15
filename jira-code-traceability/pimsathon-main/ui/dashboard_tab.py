"""Dashboard tab — token usage & cost overview.

Top: header (period filter + display-currency picker + refresh), then stat
cards (total, input, output, cache tokens, and cost per bucket). Unit prices
still come from Monitoring's model pricing table (same ``usage.*`` config keys
— both screens always agree); the currency picker itself lives HERE, beside
refresh. Bottom: a habits summary — which tasks/sessions burn the most tokens,
average per prompt, busiest day/hour. Data comes from the local usage log (one
event per model turn, recorded by the providers — real server counts when
available, ~4 chars/token estimates otherwise).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QTextBrowser, QVBoxLayout, QWidget,
)

from ..core import usage_tracker as ut
from ..core.worker import AgentWorker
from ..i18n import on_language_changed, tr
from ..state import AppContext
from .icons import icon
from .spline_chart import SplineChart
from .widgets import BudgetCard as _BudgetCard
from .widgets import StatCard as _StatCard
from .widgets import fmt_tokens as _fmt_tokens


class DashboardTab(QWidget):
    status_message = Signal(str)

    _PERIODS = ("today", "week", "month", "all")

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        root = QVBoxLayout(content)

        # ---- header: title + the PERIOD FILTER (applies to the WHOLE dashboard —
        #      cards, chart and habits all follow the selected week/month) + refresh
        self._chart_offset = 0           # 0 = current period; <0 = a past period
        head = QHBoxLayout()
        self._title = QLabel()
        self._title.setStyleSheet("font-weight:700; font-size:15px;")
        self.chart_prev_btn = QPushButton()
        self.chart_prev_btn.setIcon(icon("chevron-left"))
        self.chart_prev_btn.setFixedWidth(30)
        self.chart_prev_btn.clicked.connect(self._chart_prev)
        self._chart_period_lbl = QLabel()
        self._chart_period_lbl.setObjectName("hint")
        self._chart_period_lbl.setAlignment(Qt.AlignCenter)
        self._chart_period_lbl.setMinimumWidth(170)
        self.chart_next_btn = QPushButton()
        self.chart_next_btn.setIcon(icon("chevron-right"))
        self.chart_next_btn.setFixedWidth(30)
        self.chart_next_btn.clicked.connect(self._chart_next)
        self.gran_combo = QComboBox()
        for g in ("week", "month", "year"):
            self.gran_combo.addItem(tr(f"dashboard.gran_{g}"), g)
        self.gran_combo.currentIndexChanged.connect(self._on_gran_changed)
        self.metric_combo = QComboBox()
        for m in ("cost", "tokens"):
            self.metric_combo.addItem(tr(f"dashboard.metric_{m}"), m)
        self.metric_combo.currentIndexChanged.connect(self._refresh_chart)
        # Display-currency picker — moved here from Monitoring's Token Usage
        # card, right beside refresh; both screens still share the same
        # usage.currency config key, so changing it here updates everywhere.
        self.currency_lbl = QLabel()
        self.currency_lbl.setObjectName("hint")
        self.currency_combo = QComboBox()
        for cur in ut.SUPPORTED_CURRENCIES:
            self.currency_combo.addItem(cur, cur)
        idx = self.currency_combo.findData(
            (self.ctx.config.data.get("usage") or {}).get("currency", "USD"))
        self.currency_combo.setCurrentIndex(max(0, idx))
        self.currency_combo.currentIndexChanged.connect(self._on_currency_changed)
        self.refresh_btn = QPushButton("")
        self.refresh_btn.setIcon(icon("refresh"))
        self.refresh_btn.setFixedWidth(34)
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self._title, 1)
        head.addWidget(self.chart_prev_btn)
        head.addWidget(self._chart_period_lbl)
        head.addWidget(self.chart_next_btn)
        head.addWidget(self.gran_combo)
        head.addWidget(self.metric_combo)
        head.addWidget(self.currency_lbl)
        head.addWidget(self.currency_combo)
        head.addWidget(self.refresh_btn)
        root.addLayout(head)

        # ---- stat cards ---------------------------------------------------
        # Single row, 5 equal-width cards (same layout as Monitoring Overview)
        cards_grid = QGridLayout()
        cards_grid.setSpacing(8)
        self.card_total = _StatCard()
        self.card_in = _StatCard()
        self.card_out = _StatCard()
        self.card_cache = _StatCard()
        self.card_cost = _StatCard()
        for i, card in enumerate((self.card_total, self.card_in, self.card_out,
                                  self.card_cache, self.card_cost)):
            cards_grid.addWidget(card, 0, i)
        # Budget: remaining/budget, direct entry, auto-warns red past 85% used.
        self.budget_card = _BudgetCard()
        self.budget_card.apply_btn.setIcon(icon("check"))
        self.budget_card.apply_btn.clicked.connect(self._apply_budget)
        cards_grid.addWidget(self.budget_card, 0, 5)
        # Equal stretch on every column — otherwise the grid sizes each column
        # to its widest cell's natural content (Budget's longer "$X / $Y" value
        # + entry row made its column ~25% wider than the plain stat cards).
        for col in range(6):
            cards_grid.setColumnStretch(col, 1)
        root.addLayout(cards_grid)

        # ---- token/cost within the selected period (spline): WEEK → 7 days
        #      (Mon–Sun) · MONTH → weeks W1…Wn · YEAR → 12 months. Dashed lines
        #      compare the previous week / month. ----
        chart_head = QHBoxLayout()
        self._chart_title = QLabel()
        self._chart_title.setStyleSheet("font-weight:600;")
        chart_head.addWidget(self._chart_title, 1)
        root.addLayout(chart_head)
        self.chart = SplineChart()
        root.addWidget(self.chart)

        # ---- habits summary -------------------------------------------------
        self._habits_title = QLabel()
        self._habits_title.setStyleSheet("font-weight:600;")
        habits_head = QHBoxLayout()
        self.ai_analyze_btn = QPushButton()
        self.ai_analyze_btn.setIcon(icon("sparkle"))
        self.ai_analyze_btn.clicked.connect(self._ai_analyze)
        # Apply an AI-suggested cost-saving strategy (enable auto-compress + tune
        # the compression threshold) — only after the user clicks to approve it.
        self.apply_strategy_btn = QPushButton()
        self.apply_strategy_btn.setIcon(icon("bolt"))
        self.apply_strategy_btn.setVisible(False)
        self.apply_strategy_btn.clicked.connect(self._apply_saving_strategy)
        habits_head.addWidget(self._habits_title, 1)
        habits_head.addWidget(self.apply_strategy_btn)
        habits_head.addWidget(self.ai_analyze_btn)
        root.addLayout(habits_head)
        self.habits = QTextBrowser()
        self.habits.setOpenExternalLinks(False)
        self.habits.setMinimumHeight(160)
        root.addWidget(self.habits, 1)
        # AI recommendations panel (filled by the ✨ button).
        self._ai_title = QLabel()
        self._ai_title.setStyleSheet("font-weight:600;")
        self._ai_title.setVisible(False)
        root.addWidget(self._ai_title)
        self.ai_advice = QTextBrowser()
        self.ai_advice.setOpenExternalLinks(False)
        self.ai_advice.setMinimumHeight(140)
        self.ai_advice.setVisible(False)
        root.addWidget(self.ai_advice, 1)

        # Auto-refresh every 30s so numbers follow ongoing work.
        self._timer = QTimer(self)
        self._timer.setInterval(30_000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        on_language_changed(self._retranslate)
        self.refresh()

    # ---- helpers -----------------------------------------------------------
    def _pricing(self) -> Dict:
        from ..core import model_pricing as mp
        mp.sync_to_usage(self.ctx.config)   # cost/total comes straight from the price table
        return {**ut.DEFAULT_PRICING, **(self.ctx.config.data.get("usage") or {})}

    def _on_currency_changed(self, _idx: int) -> None:
        cur = self.currency_combo.currentData()
        if not cur:
            return
        self.ctx.config.data.setdefault("usage", {})["currency"] = cur
        self.ctx.save()
        self.refresh()

    def _retranslate(self) -> None:
        self._title.setText(tr("dashboard.title"))
        self.refresh_btn.setToolTip(tr("dashboard.refresh_tooltip"))
        self.currency_lbl.setText(tr("monitoring.overview_currency"))
        self.currency_combo.setToolTip(tr("dashboard.currency_tooltip"))
        self.ai_analyze_btn.setText(tr("dashboard.ai_analyze_btn"))
        self.ai_analyze_btn.setToolTip(tr("dashboard.ai_analyze_tooltip"))
        self.apply_strategy_btn.setText(tr("dashboard.strategy_btn"))
        self.apply_strategy_btn.setToolTip(tr("dashboard.strategy_tooltip"))
        self._habits_title.setText(tr("dashboard.habits_title"))
        self._chart_title.setText(tr("dashboard.chart_title"))
        self.chart_prev_btn.setToolTip(tr("dashboard.chart_prev"))
        self.chart_next_btn.setToolTip(tr("dashboard.chart_next"))
        self.budget_card.apply_btn.setToolTip(tr("usage.budget_apply_tooltip"))
        self.budget_card.budget_spin.setToolTip(tr("usage.budget_spin_tooltip"))
        self.refresh()

    def _apply_budget(self) -> None:
        """Persist the spin box's value as the new budget — starts a fresh
        remaining-balance window (spend before now is no longer counted)."""
        ccy = (self.ctx.config.data.get("usage") or {}).get("currency", "USD")
        ut.set_budget(self.ctx.config, self.budget_card.budget_spin.value(), ccy)
        self.ctx.save()
        self._refresh_budget()

    def _refresh_budget(self) -> None:
        from ..core import model_pricing as mp
        pricing = self._pricing()
        status = ut.budget_status(self.ctx.config)
        if status is None:
            self.budget_card.set(tr("usage.budget_title"), "—", tr("usage.budget_no_budget"))
            self.budget_card.budget_spin.setValue(0.0)
            return
        remaining_disp = mp.convert(status["remaining_usd"], "USD",
                                    pricing.get("currency", "USD"), self.ctx.config)
        amount_disp = mp.convert(status["amount_usd"], "USD",
                                 pricing.get("currency", "USD"), self.ctx.config)
        value = (f"{ut.format_cost(status['remaining_usd'], pricing, digits=2)}"
                 f" / {ut.format_cost(status['amount_usd'], pricing, digits=2)}")
        pct = int(round(status["pct_used"] * 100))
        sub = tr("usage.budget_over_warning") if status["over_85"] else tr("usage.budget_used_pct", pct=pct)
        self.budget_card.set(tr("usage.budget_title"), value, sub, warn=status["over_85"])
        # keep the entry field showing the CURRENT budget (in display currency) —
        # only when it doesn't already have unsaved focus/edits from the user.
        if not self.budget_card.budget_spin.hasFocus():
            self.budget_card.budget_spin.setValue(round(amount_disp, 2))

    def _period_range(self):
        """The SELECTED period as an inclusive (start, end) date range — drives
        the whole dashboard (cards, chart, habits)."""
        gran = self.gran_combo.currentData() or "week"
        start, end = ut.period_bounds(gran, self._chart_offset)
        return start, end - timedelta(days=1)     # load_events end is inclusive

    def _on_gran_changed(self, *_a) -> None:
        self._chart_offset = 0            # period size changed → back to current
        self.refresh()                    # the filter drives the WHOLE dashboard

    def _chart_prev(self) -> None:
        self._chart_offset -= 1           # page one period into the past
        self.refresh()

    def _chart_next(self) -> None:
        self._chart_offset = min(0, self._chart_offset + 1)   # never past the present
        self.refresh()

    @staticmethod
    def _delta_txt(cur: float, prev: float) -> str:
        """▲/▼ percent change of ``cur`` vs ``prev`` (empty if no baseline)."""
        if not prev:
            return ""
        pct = (cur - prev) / prev * 100
        arrow = "▲" if pct > 0.5 else ("▼" if pct < -0.5 else "•")
        return f"{arrow}{abs(pct):.0f}%"

    def _refresh_chart(self, *_a) -> None:
        """Break the SELECTED period into its parts: WEEK → 7 days (Mon–Sun) ·
        MONTH → weeks W1…Wn · YEAR → 12 months. Dashed lines mark the previous
        week's / month's average per point with the % change of the totals."""
        if not hasattr(self, "chart"):
            return
        gran = self.gran_combo.currentData() or "week"
        metric = self.metric_combo.currentData() or "cost"
        events = ut.load_events()                 # all events; breakdown slices by period
        pricing = self._pricing()
        parts = ut.period_breakdown(events, gran, pricing, offset=self._chart_offset)
        mi = 0 if metric == "tokens" else 1       # (label, tokens, cost) → +1 for the value
        pts = [(row[0], float(row[mi + 1])) for row in parts]
        # Compact cost format (2 decimals, K/M above 1,000/1,000,000) — the
        # chart's y-axis label box is narrow; format_cost's full precision (up
        # to 4 decimals for USD) overflowed it, clipping/obscuring the amount.
        fmt = _fmt_tokens if metric == "tokens" else (lambda v: ut.format_cost_compact(v, pricing))

        # One dashed comparison line that FOLLOWS the filter: the selected period
        # vs the previous SAME-granularity one — "Last week" in week view,
        # "Last month" in month view, "Last year" in year view. Drawn at the
        # previous period's average per point so it sits on-scale; the label shows
        # the % change of the period totals.
        cur = ut.period_totals(events, gran, pricing, self._chart_offset)
        prev = ut.period_totals(events, gran, pricing, self._chart_offset - 1)
        ref_key = {"week": "dashboard.ref_last_week",
                   "month": "dashboard.ref_last_month",
                   "year": "dashboard.ref_last_year"}.get(gran, "dashboard.ref_last_week")
        n_points = max(1, len(parts))
        refs = []
        if prev[mi] > 0:
            refs.append((prev[mi] / n_points,
                         f"{tr(ref_key)} {self._delta_txt(cur[mi], prev[mi])}", "#B08968"))
        self.chart.set_reference_lines(refs)
        self.chart.set_data(pts, fmt, tr(f"dashboard.metric_{metric}"))
        self._chart_period_lbl.setText(ut.period_range_label(gran, self._chart_offset))
        self.chart_next_btn.setEnabled(self._chart_offset < 0)

    # ---- main refresh --------------------------------------------------------
    def refresh(self) -> None:
        start, end = self._period_range()
        events = ut.load_events(start, end)

        s = ut.summarize(events)
        pricing = self._pricing()
        costs = ut.cost_usd_events(events, pricing)   # honors the per-model price table
        total_cost = sum(costs.values())

        est_note = (tr("dashboard.estimated_note", pct=int(s["estimated_share"] * 100))
                    if s["estimated_share"] > 0 else "")
        self.card_total.set(tr("dashboard.card_total"), _fmt_tokens(s["total"]),
                            tr("dashboard.card_turns", n=s["turns"]))
        self.card_in.set(tr("dashboard.card_in"), _fmt_tokens(s["in"]),
                         ut.format_cost(costs["in"], pricing))
        self.card_out.set(tr("dashboard.card_out"), _fmt_tokens(s["out"]),
                          ut.format_cost(costs["out"], pricing))
        self.card_cache.set(tr("dashboard.card_cache"), _fmt_tokens(s["cache"]),
                            ut.format_cost(costs["cache"], pricing))
        self.card_cost.set(tr("dashboard.card_cost"),
                           ut.format_cost(total_cost, pricing, digits=2), est_note)

        # ---- habits -----------------------------------------------------------
        lines: List[str] = []
        if not events:
            lines.append(f"<i>{tr('dashboard.no_data')}</i>")
        else:
            lines.append(f"<b>{tr('dashboard.h_top')}</b>")
            lines.append("<ol>")
            for label, tok in s["top_labels"]:
                pct = int(tok * 100 / s["total"]) if s["total"] else 0
                lines.append(f"<li>{label[:60]} — {_fmt_tokens(tok)} tokens ({pct}%)</li>")
            lines.append("</ol>")
            src_parts = ", ".join(
                f"{tr(f'app.tab.{k}') if k in ('cowork', 'code') else k}: {_fmt_tokens(v)}"
                for k, v in s["by_source"])
            lines.append(f"<b>{tr('dashboard.h_by_source')}</b>: {src_parts}<br>")
            lines.append(f"<b>{tr('dashboard.h_avg')}</b>: "
                         f"{_fmt_tokens(s['avg_per_turn'])} tokens<br>")
            if s["busiest_day"]:
                lines.append(f"<b>{tr('dashboard.h_busiest_day')}</b>: {s['busiest_day']}<br>")
            if s["busiest_hour"] is not None:
                lines.append(f"<b>{tr('dashboard.h_busiest_hour')}</b>: "
                             f"{s['busiest_hour']:02d}:00–{s['busiest_hour']:02d}:59<br>")
            if s["estimated_share"] > 0:
                lines.append(f"<i>{tr('dashboard.estimated_note', pct=int(s['estimated_share'] * 100))}</i>")
        self.habits.setHtml("".join(lines))
        self._refresh_chart()
        self._refresh_budget()

    def _apply_saving_strategy(self) -> None:
        """Apply an AI-suggested cost-saving strategy AFTER the user approves:
        turn on auto-compress and compress earlier (lower threshold) + compress
        content before sending it to the agent — cutting tokens on every turn."""
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(self, tr("dashboard.strategy_title"),
                                tr("dashboard.strategy_confirm")) != QMessageBox.Yes:
            return
        cx = self.ctx.config.data.setdefault("context", {})
        cx["auto_compact"] = True
        cx["compact_threshold"] = 0.6          # compress at 60% of the window (was ~80%)
        cx["compress_before_send"] = True      # digest context before each turn
        self.ctx.save()
        self.status_message.emit(tr("dashboard.strategy_applied"))

    # ---- AI habits analysis ----------------------------------------------------
    def _ai_analyze(self) -> None:
        """✨ Send the aggregated numbers (never raw prompt text) to the active
        provider and show habit feedback + token-saving recommendations."""
        if getattr(self, "_ai_worker", None) is not None:
            return
        start, end = self._period_range()
        events = ut.load_events(start, end)
        if not events:
            self.status_message.emit(tr("dashboard.no_data"))
            return
        summary = ut.summarize(events)
        self.ai_analyze_btn.setEnabled(False)
        self.ai_analyze_btn.setText(tr("dashboard.ai_analyzing"))
        ctx = self.ctx

        def job(worker: AgentWorker):
            from ..i18n import get_language

            prompt = ut.build_ai_analysis_prompt(summary, get_language())
            provider = ctx.build_active_provider()
            reply = provider.chat([{"role": "user", "content": prompt}],
                                  cancel=worker.stop_event)
            return {"text": (reply.get("content") or "").strip()}

        def done(result: dict) -> None:
            self._ai_worker = None
            self.ai_analyze_btn.setEnabled(True)
            self.ai_analyze_btn.setText(tr("dashboard.ai_analyze_btn"))
            text = result.get("text") or ""
            if text:
                self._ai_title.setText(tr("dashboard.ai_advice_title"))
                self._ai_title.setVisible(True)
                self.ai_advice.setMarkdown(text)
                self.ai_advice.setVisible(True)
                self.apply_strategy_btn.setVisible(True)   # offer to apply the saving strategy

        def failed(err: str) -> None:
            self._ai_worker = None
            self.ai_analyze_btn.setEnabled(True)
            self.ai_analyze_btn.setText(tr("dashboard.ai_analyze_btn"))
            self.status_message.emit(str(err))

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(failed)
        self._ai_worker = w
        w.start()