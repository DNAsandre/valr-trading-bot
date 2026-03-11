import logging
import pandas as pd

logger = logging.getLogger(__name__)

class Strategy:
    def __init__(self):
        self.rsi_length = 14
        self.bb_length = 20
        self.bb_std = 2.0
        self.macd_fast = 12
        self.macd_slow = 26
        self.macd_signal = 9

        # Multi-pair: keyed by pair name
        self.price_histories = {}

    def add_price(self, pair: str, price: float):
        """Add a price to the rolling window for a specific pair."""
        if pair not in self.price_histories:
            self.price_histories[pair] = []
        self.price_histories[pair].append(price)
        if len(self.price_histories[pair]) > 150:
            self.price_histories[pair].pop(0)

    def get_status(self, pair: str) -> dict | None:
        """Return current indicator readings for a pair without triggering signals."""
        history = self.price_histories.get(pair, [])
        if len(history) < 35:
            return {"pair": pair, "data_points": len(history), "ready": False}

        df = pd.DataFrame(history, columns=['close'])
        if df['close'].nunique() <= 1:
            return {"pair": pair, "data_points": len(history), "ready": False}

        indicators = self._compute_indicators(df)
        if indicators is None:
            return {"pair": pair, "data_points": len(history), "ready": False}

        return {
            "pair": pair,
            "data_points": len(history),
            "ready": True,
            "price": indicators["price"],
            "rsi": indicators["rsi"],
            "bb_upper": indicators["bb_upper"],
            "bb_lower": indicators["bb_lower"],
            "macd_hist": indicators["macd_hist"],
        }

    def _compute_indicators(self, df: pd.DataFrame) -> dict | None:
        """Compute RSI, Bollinger Bands, MACD from a DataFrame."""
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_length).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_length).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        df['BB_Mid'] = df['close'].rolling(window=self.bb_length).mean()
        df['BB_Std'] = df['close'].rolling(window=self.bb_length).std()
        df['BBU'] = df['BB_Mid'] + (self.bb_std * df['BB_Std'])
        df['BBL'] = df['BB_Mid'] - (self.bb_std * df['BB_Std'])

        exp1 = df['close'].ewm(span=self.macd_fast, adjust=False).mean()
        exp2 = df['close'].ewm(span=self.macd_slow, adjust=False).mean()
        df['MACD'] = exp1 - exp2
        df['MACD_Signal'] = df['MACD'].ewm(span=self.macd_signal, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

        latest = df.iloc[-1]
        if pd.isna(latest['RSI']) or pd.isna(latest['BBL']) or pd.isna(latest['MACD_Hist']):
            return None

        return {
            "price": latest['close'],
            "rsi": latest['RSI'],
            "bb_upper": latest['BBU'],
            "bb_lower": latest['BBL'],
            "macd_hist": latest['MACD_Hist'],
        }

    def analyze(self, pair: str, current_valr_ob: dict, current_luno_ob: dict) -> dict | None:
        """Run full analysis for a specific pair."""
        history = self.price_histories.get(pair, [])
        if len(history) < 35:
            return None

        df = pd.DataFrame(history, columns=['close'])
        if df['close'].nunique() <= 1:
            return None

        indicators = self._compute_indicators(df)
        if indicators is None:
            return None

        rsi_val = indicators["rsi"]
        bb_lower = indicators["bb_lower"]
        bb_upper = indicators["bb_upper"]
        current_price = indicators["price"]
        macd_hist = indicators["macd_hist"]

        try:
            valr_bids = sum(float(b['quantity']) for b in current_valr_ob.get('Bids', [])[:5])
            valr_asks = sum(float(a['quantity']) for a in current_valr_ob.get('Asks', [])[:5])
        except Exception as e:
            logger.error(f"Orderbook parsing error for {pair}: {e}")
            valr_bids, valr_asks = 0, 0

        # Format pair for display (e.g. BTCZAR -> BTC/ZAR)
        display_pair = pair[:-3] + "/" + pair[-3:]

        signal = None
        insight_text = ""

        if rsi_val <= 30 and current_price <= bb_lower and macd_hist > 0 and valr_bids > valr_asks:
            signal = "BUY"
            insight_text = (
                f"Insight: RSI is oversold at {rsi_val:.1f}, and price (R {current_price:.2f}) touched the lower Bollinger Band. "
                "MACD histogram shows incoming bullish momentum. Buying pressure is increasing on the VALR order book "
                f"(Top 5 Bids: {valr_bids:.4f} > Asks: {valr_asks:.4f}). Recommend: BUY {display_pair}."
            )
        elif rsi_val >= 70 and current_price >= bb_upper and macd_hist < 0 and valr_asks > valr_bids:
            signal = "SELL"
            insight_text = (
                f"Insight: RSI is overbought at {rsi_val:.1f}, and price (R {current_price:.2f}) touched the upper Bollinger Band. "
                "MACD histogram suggests exhaustion. Selling pressure is dominating the VALR order book "
                f"(Top 5 Asks: {valr_asks:.4f} > Bids: {valr_bids:.4f}). Recommend: SELL {display_pair}."
            )

        if signal:
            return {
                "signal": signal,
                "pair": pair,
                "display_pair": display_pair,
                "price": current_price,
                "take_profit": current_price * 1.03 if signal == "BUY" else current_price * 0.97,
                "stop_loss": current_price * 0.98 if signal == "BUY" else current_price * 1.02,
                "insight": insight_text
            }

        return None
