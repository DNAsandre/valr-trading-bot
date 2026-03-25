import logging
import json
import pandas as pd
from openai import AsyncOpenAI
from config import TRAILING_STOP_LOSS_PCT, OPENAI_API_KEY, SUPPORTED_PAIRS

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
        self.ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

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
            "price": float(indicators["price"]),
            "rsi": float(indicators["rsi"]),
            "bb_upper": float(indicators["bb_upper"]),
            "bb_lower": float(indicators["bb_lower"]),
            "macd_hist": float(indicators["macd_hist"]),
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

        df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['EMA_50'] = df['close'].ewm(span=50, adjust=False).mean()

        latest = df.iloc[-1]
        if pd.isna(latest['RSI']) or pd.isna(latest['BBL']) or pd.isna(latest['MACD_Hist']):
            return None

        return {
            "price": latest['close'],
            "rsi": latest['RSI'],
            "bb_upper": latest['BBU'],
            "bb_lower": latest['BBL'],
            "macd_hist": latest['MACD_Hist'],
            "ema_9": latest['EMA_9'],
            "ema_21": latest['EMA_21'],
            "ema_50": latest['EMA_50']
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
        ema_9 = indicators["ema_9"]
        ema_21 = indicators["ema_21"]
        ema_50 = indicators["ema_50"]

        best_bid_price = current_price
        best_ask_price = current_price
        try:
            valr_bids = sum(float(b['quantity']) for b in current_valr_ob.get('Bids', [])[:5])
            valr_asks = sum(float(a['quantity']) for a in current_valr_ob.get('Asks', [])[:5])
            
            if current_valr_ob.get('Bids'):
                best_bid_price = float(current_valr_ob.get('Bids')[0].get('price', current_price))
            if current_valr_ob.get('Asks'):
                best_ask_price = float(current_valr_ob.get('Asks')[0].get('price', current_price))
        except Exception as e:
            logger.error(f"Orderbook parsing error for {pair}: {e}")
            valr_bids, valr_asks = 0, 0

        # Format pair for display (e.g. BTCZAR -> BTC/ZAR)
        display_pair = pair[:-3] + "/" + pair[-3:]

        signal = None
        insight_text = ""

        is_uptrend = current_price > ema_50
        bullish_cross = ema_9 > ema_21
        bearish_cross = ema_9 < ema_21

        buy_pressure = valr_bids > valr_asks
        sell_pressure = valr_asks > valr_bids

        # BUY Logic (Mean Reversion OR Trend Following)
        if (rsi_val <= 30 and current_price <= bb_lower and macd_hist > 0) or (is_uptrend and bullish_cross and macd_hist > 0):
            if buy_pressure:
                signal = "BUY"
                insight_text = (
                    f"RSI={rsi_val:.1f}, Trend={'Up' if is_uptrend else 'Down'}. "
                    "Technical confirmations align (MACD/EMA/BB). "
                    f"Bids: {valr_bids:.2f} > Asks: {valr_asks:.2f}. Autonomous BUY triggered."
                )

        # SELL Logic (Mean Reversion OR Trend Following)
        elif (rsi_val >= 70 and current_price >= bb_upper and macd_hist < 0) or (not is_uptrend and bearish_cross and macd_hist < 0):
            if sell_pressure:
                signal = "SELL"
                insight_text = (
                    f"RSI={rsi_val:.1f}, Trend={'Up' if is_uptrend else 'Down'}. "
                    "Technical exhaustions align (MACD/EMA/BB). "
                    f"Asks: {valr_asks:.2f} > Bids: {valr_bids:.2f}. Autonomous SELL triggered."
                )

        if signal:
            tp_pct = TRAILING_STOP_LOSS_PCT * 1.5  # Dynamic Risk/Reward multiplier of 1.5
            sl_pct = TRAILING_STOP_LOSS_PCT
            
            limit_price = best_bid_price if signal == "BUY" else best_ask_price

            return {
                "signal": signal,
                "pair": pair,
                "display_pair": display_pair,
                "price": limit_price,
                "take_profit": limit_price * (1 + tp_pct) if signal == "BUY" else limit_price * (1 - tp_pct),
                "stop_loss": limit_price * (1 - sl_pct) if signal == "BUY" else limit_price * (1 + sl_pct),
                "insight": insight_text
            }

        return None

    async def ai_market_scan(self, current_balances: list) -> str | None:
        """Scan all supported pairs, gather technical data, and ask AI to pick the best trade."""
        if not self.ai_client:
            logger.error("AI Client not initialized. Cannot run market scan.")
            return None

        # Build market state payload
        market_state = []
        for pair in SUPPORTED_PAIRS:
            status = self.get_status(pair)
            if status and status.get('ready'):
                # We only want to provide the AI with pairs that have actionable data
                market_state.append({
                    "pair": status['pair'],
                    "price": status['price'],
                    "rsi": status['rsi'],
                    "macd_hist": status['macd_hist'],
                    "bb_lower": status['bb_lower'],
                    "bb_upper": status['bb_upper']
                })

        if not market_state:
            logger.warning("Not enough market data gathered yet to perform an AI scan.")
            return None

        prompt = (
            "You are an expert quantitative crypto hedge fund manager. "
            "Your ONLY goal is to maximize profit in ZAR (South African Rands).\n"
            "Here is the current technical state of the top trading pairs:\n"
            f"{json.dumps(market_state, indent=2)}\n\n"
            "Analyze the RSI (look for oversold < 30), MACD momentum, and Bollinger Bands.\n"
            "Based ONLY on this data, which SINGLE pair is the absolute best BUY right now?\n"
            "Reply strictly with the exact pair ticker (e.g., BTCZAR, XRPZAR) and nothing else. "
            "If no pairs present a good buying opportunity, reply exactly with 'NONE'."
        )

        try:
            logger.info("Executing AI Market Scan via OpenAI...")
            response = await self.ai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "user", "content": prompt}
                ],
                max_tokens=10,
                temperature=0.1
            )

            choice = response.choices[0].message.content.strip().upper()
            logger.info(f"AI Market Scan Complete. Top Pick: {choice}")
            
            if choice == "NONE" or choice not in SUPPORTED_PAIRS:
                return None
                
            return choice

        except Exception as e:
            logger.error(f"Failed to run AI market scan: {e}")
            return None

    async def ai_double_zar_scan(self, market_summaries: list, zar_balance: float) -> dict | None:
        """Scan the ENTIRE VALR market (all ZAR pairs) using AI to find the best buy.
        
        Unlike ai_market_scan which only checks SUPPORTED_PAIRS with technical data,
        this method analyses ALL available pairs using market data (price, % change, volume).
        
        Returns: {"pair": "XRPZAR", "reason": "...", "confidence": "HIGH/MED/LOW"} or None
        """
        if not self.ai_client:
            logger.error("AI Client not initialized. Cannot run Double ZAR scan.")
            return None

        if not market_summaries:
            logger.warning("No market summaries available for Double ZAR scan.")
            return None

        # Split into gainers and losers
        gainers = [s for s in market_summaries if s['changePct'] > 0][:10]
        losers = [s for s in reversed(market_summaries) if s['changePct'] < 0][:10]

        prompt = (
            "You are an expert quantitative crypto hedge fund manager trading on the South African VALR exchange.\n"
            "Your MISSION: Find the SINGLE best cryptocurrency to buy RIGHT NOW to maximize ZAR profit.\n\n"
            f"💰 Available ZAR to deploy: R {zar_balance:,.2f}\n\n"
            "📈 TOP GAINERS (highest % change in last 24h):\n"
            f"{json.dumps(gainers, indent=2)}\n\n"
            "📉 TOP LOSERS (biggest drops — potential bounce plays):\n"
            f"{json.dumps(losers, indent=2)}\n\n"
            "ANALYSIS RULES:\n"
            "1. Consider MOMENTUM: Gainers with strong volume may continue upward.\n"
            "2. Consider MEAN REVERSION: Oversold losers with high volume may bounce.\n"
            "3. AVOID low-volume pairs (volume < 100 in base currency) — they are illiquid.\n"
            "4. AVOID pairs that have barely moved (< 0.5% change).\n"
            "5. Factor in risk: prefer a strong setup over a risky moonshot.\n\n"
            "Reply in EXACTLY this JSON format and nothing else:\n"
            '{"pair": "XXXZAR", "reason": "one-line explanation", "confidence": "HIGH"}\n'
            "If NO pair offers a good opportunity, reply exactly: {\"pair\": \"NONE\", \"reason\": \"no good setups\", \"confidence\": \"NONE\"}"
        )

        try:
            logger.info("Executing AI Double ZAR Scan...")
            response = await self.ai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "user", "content": prompt}
                ],
                max_tokens=150,
                temperature=0.2
            )

            raw = response.choices[0].message.content.strip()
            logger.info(f"AI Double ZAR raw response: {raw}")

            result = json.loads(raw)
            pair = result.get("pair", "NONE").upper()
            reason = result.get("reason", "No reason given.")
            confidence = result.get("confidence", "LOW").upper()

            if pair == "NONE":
                logger.info("AI Double ZAR Scan: No good setups found.")
                return None

            return {
                "pair": pair,
                "reason": reason,
                "confidence": confidence
            }

        except json.JSONDecodeError as e:
            logger.error(f"AI Double ZAR Scan returned non-JSON: {raw} — {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to run AI Double ZAR scan: {e}")
            return None
