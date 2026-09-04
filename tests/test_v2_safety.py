import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from datetime import datetime

from exchange import ExchangeInterface
from main import HitlTradingBot
from risk import TradingRiskGuard
from strategy import Strategy
from paper import PaperPortfolio
from reporting import format_paper_daily_report
from backtest import run_backtest


class PaperExecutionTests(unittest.TestCase):
    def test_paper_mode_never_calls_valr_order_endpoint(self):
        exchange = ExchangeInterface(execution_mode="paper")

        def real_order_must_not_be_called(**kwargs):
            raise AssertionError("Paper mode must not call VALR's order endpoint")

        async def place_simulated_order():
            with patch.object(exchange.valr_client, "post_limit_order", real_order_must_not_be_called):
                result = await exchange.place_valr_order(
                    pair="XRPZAR", side="BUY", amount=10.0, price=23.50
                )
            self.assertTrue(result["simulated"])
            self.assertEqual(result["mode"], "paper")
            self.assertEqual(result["pair"], "XRPZAR")
            self.assertEqual(result["side"], "BUY")

        asyncio.run(place_simulated_order())

    def test_daily_loss_limit_blocks_new_trade(self):
        guard = TradingRiskGuard(max_daily_loss_zar=50.0, cooldown_seconds=0, max_trades_per_day=10)
        now = datetime(2026, 9, 5, 10, 0, 0)
        guard.record_execution(now, realized_pnl_zar=-50.0)

        decision = guard.can_execute(now)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "daily_loss_limit")

    def test_blocked_risk_guard_prevents_autonomous_execution(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        bot.exchange = SimpleNamespace(get_valr_balances=AsyncMock())
        bot.notifier = SimpleNamespace(risk_pct=0.02)
        bot.risk_guard = SimpleNamespace(
            can_execute=lambda now: SimpleNamespace(allowed=False, reason="daily_loss_limit")
        )
        signal = {"pair": "XRPZAR", "signal": "BUY", "price": 23.50}

        async def execute_blocked_signal():
            success, amount = await bot.execute_signal_autonomously(signal)
            self.assertFalse(success)
            self.assertEqual(amount, 0.0)
            bot.exchange.get_valr_balances.assert_not_awaited()

        asyncio.run(execute_blocked_signal())

    def test_paper_execution_updates_only_virtual_portfolio(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        bot.exchange = SimpleNamespace(
            execution_mode="paper",
            get_valr_balances=AsyncMock(return_value=[
                {"currency": "ZAR", "available": "1000", "total": "1000"},
                {"currency": "XRP", "available": "0", "total": "0"},
            ]),
            place_valr_order=AsyncMock(return_value={"simulated": True}),
        )
        bot.notifier = SimpleNamespace(risk_pct=0.02)
        bot.risk_guard = TradingRiskGuard(max_daily_loss_zar=50.0, cooldown_seconds=0, max_trades_per_day=3)
        bot.paper_portfolio = None
        signal = {"pair": "XRPZAR", "signal": "BUY", "price": 100.0}

        async def execute_paper_signal():
            success, amount = await bot.execute_signal_autonomously(signal)
            self.assertTrue(success)
            self.assertAlmostEqual(amount, 0.2, places=8)
            self.assertIsInstance(bot.paper_portfolio, PaperPortfolio)
            self.assertAlmostEqual(bot.paper_portfolio.zar_balance, 979.96, places=2)
            bot.exchange.place_valr_order.assert_awaited_once()

        asyncio.run(execute_paper_signal())

    def test_existing_paper_xrp_position_blocks_another_buy(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        bot.exchange = SimpleNamespace(
            execution_mode="paper",
            get_valr_balances=AsyncMock(return_value=[]),
            place_valr_order=AsyncMock(),
        )
        bot.notifier = SimpleNamespace(risk_pct=0.02)
        bot.risk_guard = TradingRiskGuard(max_daily_loss_zar=50.0, cooldown_seconds=0, max_trades_per_day=3)
        bot.paper_portfolio = PaperPortfolio(
            initial_zar=980.0, initial_xrp=0.2, initial_xrp_price=100.0, fee_pct=0.002
        )
        signal = {"pair": "XRPZAR", "signal": "BUY", "price": 100.0}

        async def execute_second_buy():
            success, amount = await bot.execute_signal_autonomously(signal)
            self.assertFalse(success)
            self.assertEqual(amount, 0.0)
            bot.exchange.place_valr_order.assert_not_awaited()

        asyncio.run(execute_second_buy())

    def test_ticks_only_create_one_indicator_point_per_closed_candle(self):
        strategy = Strategy(candle_seconds=300)
        strategy.add_price("XRPZAR", 23.00, timestamp=0)
        strategy.add_price("XRPZAR", 23.10, timestamp=120)
        strategy.add_price("XRPZAR", 23.20, timestamp=299)
        strategy.add_price("XRPZAR", 23.30, timestamp=300)

        self.assertEqual(strategy.price_histories["XRPZAR"], [23.20])

    def test_paper_portfolio_calculates_fee_adjusted_round_trip_pnl(self):
        portfolio = PaperPortfolio(initial_zar=1_000.0, initial_xrp=0.0, fee_pct=0.002)
        portfolio.buy(quantity=10.0, price=20.0)
        result = portfolio.sell(quantity=10.0, price=22.0)

        self.assertAlmostEqual(result.realized_pnl_zar, 19.16, places=2)
        self.assertAlmostEqual(portfolio.zar_balance, 1_019.16, places=2)
        self.assertAlmostEqual(portfolio.xrp_balance, 0.0, places=8)

    def test_paper_portfolio_preserves_fifo_cost_after_partial_sale(self):
        portfolio = PaperPortfolio(initial_zar=1_000.0, initial_xrp=0.0, fee_pct=0.0)
        portfolio.buy(quantity=5.0, price=10.0)
        portfolio.buy(quantity=5.0, price=20.0)
        portfolio.sell(quantity=7.0, price=30.0)
        final_sale = portfolio.sell(quantity=3.0, price=20.0)

        self.assertAlmostEqual(final_sale.realized_pnl_zar, 0.0, places=8)

    def test_paper_portfolio_daily_report_includes_marked_equity(self):
        portfolio = PaperPortfolio(initial_zar=1_000.0, initial_xrp=0.0, fee_pct=0.0)
        portfolio.buy(quantity=10.0, price=20.0)

        report = portfolio.daily_report(mark_price=21.0)

        self.assertEqual(report["fills"], 1)
        self.assertAlmostEqual(report["realized_pnl_zar"], 0.0, places=8)
        self.assertAlmostEqual(report["unrealized_pnl_zar"], 10.0, places=8)
        self.assertAlmostEqual(report["equity_zar"], 1_010.0, places=8)

    def test_daily_report_formatter_labels_paper_mode(self):
        text = format_paper_daily_report(
            {
                "fills": 2,
                "realized_pnl_zar": 10.0,
                "unrealized_pnl_zar": -2.0,
                "equity_zar": 1_008.0,
                "total_pnl_zar": 8.0,
                "zar_balance": 900.0,
                "xrp_balance": 5.0,
            }
        )

        self.assertIn("PAPER DAILY REPORT", text)
        self.assertIn("R 1,008.00", text)
        self.assertIn("Fills: 2", text)

    def test_paper_portfolio_persists_fifo_state(self):
        portfolio = PaperPortfolio(initial_zar=1_000.0, initial_xrp=0.0, fee_pct=0.002)
        portfolio.buy(quantity=10.0, price=20.0)
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "paper-state.json"
            portfolio.save(state_path)
            restored = PaperPortfolio.load(state_path)

        report = restored.daily_report(mark_price=21.0)
        self.assertEqual(report["fills"], 1)
        self.assertAlmostEqual(report["equity_zar"], 1_009.6, places=2)

    def test_backtest_applies_fees_to_forced_round_trip(self):
        candles = [
            {"startTime": "2026-09-01T00:00:00Z", "close": "100"},
            {"startTime": "2026-09-01T00:05:00Z", "close": "110"},
        ]
        signals = ["BUY", "SELL"]
        result = run_backtest(
            candles,
            signal_provider=lambda index, _: signals[index],
            starting_zar=1_000.0,
            position_size_pct=0.02,
            fee_pct=0.002,
            cooldown_seconds=0,
            max_trades_per_day=3,
        )

        self.assertEqual(result["fills"], 2)
        self.assertAlmostEqual(result["realized_pnl_zar"], 1.916, places=3)
        self.assertAlmostEqual(result["ending_equity_zar"], 1_001.916, places=3)


if __name__ == "__main__":
    unittest.main()
