import asyncio
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from datetime import datetime, timedelta

from exchange import ExchangeInterface
from main import HitlTradingBot
from risk import TradingRiskGuard
from strategy import Strategy
from paper import PaperPortfolio
from reporting import format_paper_daily_report
from backtest import run_backtest
from telegram_bot import TelegramNotifier
from live_state import LiveState


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
            self.assertEqual(signal["execution_status"], "skipped")
            self.assertEqual(signal["execution_reason"], "daily_loss_limit")
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
            self.assertEqual(
                bot.exchange.place_valr_order.await_args.kwargs["execution_source"],
                "autonomous_xrpzar",
            )

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
            self.assertEqual(signal["execution_status"], "skipped")
            self.assertEqual(signal["execution_reason"], "position_already_open")
            bot.exchange.place_valr_order.assert_not_awaited()

        asyncio.run(execute_second_buy())

    def test_position_skip_notification_does_not_send_telegram_message(self):
        notifier = TelegramNotifier.__new__(TelegramNotifier)
        notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        trade_info = {
            "pair": "XRPZAR",
            "display_pair": "XRP/ZAR",
            "signal": "BUY",
            "price": 22.85,
            "insight": "Test signal",
            "execution_status": "skipped",
            "execution_reason": "position_already_open",
        }

        async def notify_skip():
            with patch("telegram_bot.TELEGRAM_ALLOWED_USERS", [123]):
                await notifier.notify_execution(trade_info, False, 0.0)
            notifier.app.bot.send_message.assert_not_awaited()

        asyncio.run(notify_skip())

    def test_successful_paper_notification_is_explicitly_labeled_paper(self):
        notifier = TelegramNotifier.__new__(TelegramNotifier)
        notifier.exchange = SimpleNamespace(execution_mode="paper")
        notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        trade_info = {
            "pair": "XRPZAR",
            "display_pair": "XRP/ZAR",
            "signal": "BUY",
            "price": 22.85,
            "take_profit": 23.19,
            "stop_loss": 22.62,
            "insight": "Test signal",
        }

        async def notify_paper_fill():
            with patch("telegram_bot.TELEGRAM_ALLOWED_USERS", [123]):
                await notifier.notify_execution(trade_info, True, 2.5)
            message = notifier.app.bot.send_message.await_args.kwargs["text"]
            self.assertIn("PAPER TRADE EXECUTED", message)

        asyncio.run(notify_paper_fill())

    def test_live_submission_notification_is_pending_not_failed_or_filled(self):
        notifier = TelegramNotifier.__new__(TelegramNotifier)
        notifier.exchange = SimpleNamespace(execution_mode="live")
        notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        trade_info = {
            "pair": "XRPZAR",
            "signal": "BUY",
            "price": 22.85,
            "insight": "Test signal",
            "execution_status": "submitted",
            "execution_reason": "pending_reconciliation",
        }

        async def notify_submission():
            with patch("telegram_bot.TELEGRAM_ALLOWED_USERS", [123]):
                await notifier.notify_execution(trade_info, False, 0.0)
            message = notifier.app.bot.send_message.await_args.kwargs["text"]
            self.assertIn("LIVE ORDER SUBMITTED", message)
            self.assertIn("PENDING", message)
            self.assertNotIn("FAILED", message)
            self.assertNotIn("EXECUTED", message)

        asyncio.run(notify_submission())

    def test_telegram_cannot_raise_position_size_above_two_percent(self):
        notifier = TelegramNotifier.__new__(TelegramNotifier)
        notifier.risk_pct = 0.02
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        context = SimpleNamespace(args=["10"])

        async def attempt_risk_increase():
            with patch("telegram_bot.TELEGRAM_ALLOWED_USERS", [123]):
                await notifier.risk_cmd(update, context)
            self.assertEqual(notifier.risk_pct, 0.02)
            message = update.message.reply_text.await_args.args[0]
            self.assertIn("maximum", message.lower())
            self.assertIn("2.0%", message)

        asyncio.run(attempt_risk_increase())

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


class LiveExecutionSafetyTests(unittest.TestCase):
    @staticmethod
    def accepted_order(*, order_id="live-sell-1"):
        return {"id": order_id}

    def test_live_sell_uses_only_settled_bot_owned_xrp(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        state = LiveState()
        state.lots = [{"quantity": Decimal("2"), "cost_zar": Decimal("40")}]
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "live-state.json"
            state.save(state_path)
            bot.exchange = SimpleNamespace(
                execution_mode="live",
                get_valr_open_orders=AsyncMock(return_value=[]),
                get_valr_balances=AsyncMock(return_value=[
                    {"currency": "XRP", "available": "1000", "total": "1000"},
                ]),
                place_valr_order=AsyncMock(return_value=self.accepted_order()),
            )
            bot.notifier = SimpleNamespace(risk_pct=0.02)
            bot.risk_guard = SimpleNamespace(
                can_execute=lambda now: SimpleNamespace(allowed=True, reason=None),
                record_execution=unittest.mock.Mock(),
            )
            bot.live_state = state
            bot.live_state_path = state_path
            bot.live_execution_blocked = False
            signal = {"pair": "XRPZAR", "signal": "SELL", "price": 20.0, "post_only": False}

            async def execute_live_sell():
                success, amount = await bot.execute_signal_autonomously(signal)
                self.assertFalse(success)
                self.assertEqual(amount, 0.0)
                self.assertEqual(bot.exchange.place_valr_order.await_args.kwargs["amount"], 2.0)
                self.assertFalse(bot.exchange.place_valr_order.await_args.kwargs["post_only"])
                self.assertEqual(signal["execution_status"], "submitted")
                self.assertEqual(signal["execution_reason"], "pending_reconciliation")
                self.assertEqual(state.settled_xrp, Decimal("2"))
                self.assertEqual(state.pending_order["order_id"], "live-sell-1")
                bot.risk_guard.record_execution.assert_not_called()

            asyncio.run(execute_live_sell())

    def test_pending_live_state_blocks_second_buy_before_valr(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        state = LiveState()
        state.begin_order(side="BUY", requested_quantity="1", price="20")
        with tempfile.TemporaryDirectory() as directory:
            bot.exchange = SimpleNamespace(
                execution_mode="live",
                get_valr_balances=AsyncMock(),
                place_valr_order=AsyncMock(),
            )
            bot.notifier = SimpleNamespace(risk_pct=0.02)
            bot.risk_guard = SimpleNamespace(
                can_execute=lambda now: SimpleNamespace(allowed=True, reason=None),
            )
            bot.live_state = state
            bot.live_state_path = Path(directory) / "live-state.json"
            bot.live_execution_blocked = False
            signal = {"pair": "XRPZAR", "signal": "BUY", "price": 20.0}

            async def execute_second_live_buy():
                success, amount = await bot.execute_signal_autonomously(signal)
                self.assertFalse(success)
                self.assertEqual(amount, 0.0)
                self.assertEqual(signal["execution_status"], "skipped")
                self.assertEqual(signal["execution_reason"], "pending_order")
                bot.exchange.get_valr_balances.assert_not_awaited()
                bot.exchange.place_valr_order.assert_not_awaited()

            asyncio.run(execute_second_live_buy())

    def test_live_startup_fails_closed_without_state_file(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        with tempfile.TemporaryDirectory() as directory:
            bot.exchange = SimpleNamespace(
                execution_mode="live",
                get_valr_open_orders=AsyncMock(return_value=[]),
            )
            bot.live_state_path = Path(directory) / "missing-live-state.json"
            bot.live_state = None
            bot.live_execution_blocked = False

            self.assertFalse(asyncio.run(bot.initialize_live_execution()))
            self.assertTrue(bot.live_execution_blocked)
            bot.exchange.get_valr_open_orders.assert_not_awaited()

    def test_live_startup_blocks_untracked_xrpzar_open_order(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "live-state.json"
            LiveState().save(state_path)
            bot.exchange = SimpleNamespace(
                execution_mode="live",
                get_valr_open_orders=AsyncMock(return_value=[
                    {"currencyPair": "XRPZAR", "orderId": "manual-open-order"},
                ]),
            )
            bot.live_state_path = state_path
            bot.live_state = None
            bot.live_execution_blocked = False

            self.assertFalse(asyncio.run(bot.initialize_live_execution()))
            self.assertTrue(bot.live_execution_blocked)

    def test_run_does_not_start_bot_when_live_preflight_fails(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        bot.initialize_live_execution = AsyncMock(return_value=False)
        bot.notifier = SimpleNamespace(start_bot=AsyncMock(), stop_bot=AsyncMock())
        bot.exchange = SimpleNamespace(close=AsyncMock())

        with self.assertRaises(RuntimeError):
            asyncio.run(bot.run())
        bot.notifier.start_bot.assert_not_awaited()

    def test_persisted_live_daily_loss_blocks_order_before_valr(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        state = LiveState()
        state.daily_realized_pnl_zar = Decimal("-50")
        bot.live_state = state
        bot.live_state_path = Path("unused.json")
        bot.live_execution_blocked = False
        bot.exchange = SimpleNamespace(
            execution_mode="live",
            get_valr_balances=AsyncMock(),
            place_valr_order=AsyncMock(),
        )
        bot.notifier = SimpleNamespace(risk_pct=0.02)
        bot.risk_guard = SimpleNamespace(
            can_execute=lambda now: SimpleNamespace(allowed=True, reason=None),
        )
        signal = {"pair": "XRPZAR", "signal": "BUY", "price": 20.0}

        success, amount = asyncio.run(bot.execute_signal_autonomously(signal))

        self.assertFalse(success)
        self.assertEqual(amount, 0.0)
        self.assertEqual(signal["execution_reason"], "daily_loss_limit")
        bot.exchange.get_valr_balances.assert_not_awaited()
        bot.exchange.place_valr_order.assert_not_awaited()

    def test_persisted_live_daily_execution_cap_blocks_order(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        state = LiveState()
        state.daily_execution_count = 3
        bot.live_state = state
        bot.live_state_path = Path("unused.json")

        self.assertEqual(
            bot._live_risk_block_reason(datetime.now().astimezone()),
            "daily_trade_limit",
        )

    def test_persisted_live_cooldown_blocks_order(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        state = LiveState()
        now = datetime.now().astimezone()
        state.set_cooldown_until(now + timedelta(minutes=15))
        bot.live_state = state
        bot.live_state_path = Path("unused.json")

        self.assertEqual(bot._live_risk_block_reason(now), "cooldown")

    def test_live_reconciliation_error_blocks_submission_before_valr(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        state = LiveState()
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "live-state.json"
            state.save(state_path)
            bot.live_state = state
            bot.live_state_path = state_path
            bot.live_execution_blocked = False
            bot.exchange = SimpleNamespace(
                execution_mode="live",
                get_valr_open_orders=AsyncMock(side_effect=RuntimeError("VALR unavailable")),
                get_valr_balances=AsyncMock(return_value=[{"currency": "ZAR", "available": "1000"}]),
                place_valr_order=AsyncMock(),
            )
            bot.notifier = SimpleNamespace(risk_pct=0.02)
            bot.risk_guard = SimpleNamespace(
                can_execute=lambda now: SimpleNamespace(allowed=True, reason=None),
            )
            signal = {"pair": "XRPZAR", "signal": "BUY", "price": 20.0}

            success, amount = asyncio.run(bot.execute_signal_autonomously(signal))

        self.assertFalse(success)
        self.assertEqual(amount, 0.0)
        self.assertEqual(signal["execution_reason"], "reconciliation_failed")
        bot.exchange.get_valr_balances.assert_not_awaited()
        bot.exchange.place_valr_order.assert_not_awaited()

    def test_live_protective_stop_creates_non_post_only_sell_signal(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        state = LiveState()
        state.lots = [{"quantity": Decimal("2"), "cost_zar": Decimal("200")}]
        bot.live_state = state
        bot.live_execution_blocked = False
        bot.exchange = SimpleNamespace(execution_mode="live")

        signal = bot.live_protective_exit_signal("XRPZAR", 99.0)

        self.assertEqual(signal["signal"], "SELL")
        self.assertEqual(signal["price"], 99.0)
        self.assertFalse(signal["post_only"])
        self.assertIn("stop-loss", signal["insight"].lower())

    def test_triggered_live_protection_submits_and_notifies_once(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        signal = {"pair": "XRPZAR", "signal": "SELL", "price": 99.0}
        bot.live_protective_exit_signal = Mock(return_value=signal)
        bot.execute_signal_autonomously = AsyncMock(return_value=(False, 0.0))
        bot.notifier = SimpleNamespace(notify_execution=AsyncMock())

        handled = asyncio.run(bot.evaluate_live_protection("XRPZAR", 99.0))

        self.assertTrue(handled)
        bot.execute_signal_autonomously.assert_awaited_once_with(signal)
        bot.notifier.notify_execution.assert_awaited_once_with(signal, False, 0.0)

    def test_live_reconciliation_loop_stops_after_fail_closed_error(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        bot.exchange = SimpleNamespace(execution_mode="live")
        bot.reconcile_live_order = AsyncMock(return_value=False)

        asyncio.run(bot.live_reconciliation_loop())

        bot.reconcile_live_order.assert_awaited_once()

    def test_terminal_reconciled_live_fill_persists_cooldown(self):
        bot = HitlTradingBot.__new__(HitlTradingBot)
        state = LiveState()
        state.begin_order(side="BUY", requested_quantity="1", price="20")
        state.accept_order({"id": "buy-1"})
        order = {
            "orderId": "buy-1", "currencyPair": "XRPZAR", "side": "BUY",
            "status": "Filled", "originalQuantity": "1", "price": "20",
            "totalFilledQuantity": "1", "totalFilledValue": "20", "remainingQuantity": "0",
            "allowMargin": False, "type": "LIMIT",
        }
        trade = {
            "id": "fill-1", "orderId": "buy-1", "currencyPair": "XRPZAR",
            "side": "BUY", "quantity": "1", "price": "20", "fee": "0",
            "feeCurrency": "ZAR", "tradedAt": "2026-09-10T07:00:00Z",
        }
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "live-state.json"
            state.save(state_path)
            bot.live_state = state
            bot.live_state_path = state_path
            bot.live_execution_blocked = False
            bot.exchange = SimpleNamespace(
                execution_mode="live",
                get_valr_open_orders=AsyncMock(return_value=[]),
                get_valr_order_status=AsyncMock(return_value=order),
                get_xrp_zar_trade_history=AsyncMock(return_value=[trade]),
            )

            self.assertTrue(asyncio.run(bot.reconcile_live_order()))
            self.assertIsNone(state.pending_order)
            self.assertIsNotNone(state.cooldown_until)


if __name__ == "__main__":
    unittest.main()
