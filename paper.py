"""Fee-aware, in-memory XRP/ZAR portfolio used only by paper trading."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class PaperFill:
    side: str
    quantity: float
    price: float
    fee_zar: float
    realized_pnl_zar: float


class PaperPortfolio:
    def __init__(
        self,
        *,
        initial_zar: float,
        initial_xrp: float,
        fee_pct: float = 0.002,
        initial_xrp_price: float = 0.0,
    ) -> None:
        if initial_zar < 0 or initial_xrp < 0:
            raise ValueError("initial balances cannot be negative")
        if not 0 <= fee_pct < 1:
            raise ValueError("fee_pct must be between 0 and 1")
        self.zar_balance = float(initial_zar)
        self.xrp_balance = float(initial_xrp)
        self.fee_pct = float(fee_pct)
        self.fills: list[PaperFill] = []
        self.initial_equity_zar = float(initial_zar) + (float(initial_xrp) * float(initial_xrp_price))
        # Existing XRP is seeded at the initial observed market price so paper
        # reporting begins at zero unrealised P&L rather than inventing history.
        self._lots: deque[list[float]] = deque()
        if initial_xrp:
            self._lots.append([float(initial_xrp), float(initial_xrp) * float(initial_xrp_price)])

    def buy(self, *, quantity: float, price: float) -> PaperFill:
        if quantity <= 0 or price <= 0:
            raise ValueError("quantity and price must be positive")
        notional = quantity * price
        fee = notional * self.fee_pct
        total_cost = notional + fee
        if total_cost > self.zar_balance + 1e-9:
            raise ValueError("insufficient paper ZAR balance")
        self.zar_balance -= total_cost
        self.xrp_balance += quantity
        self._lots.append([quantity, total_cost])
        fill = PaperFill("BUY", quantity, price, fee, 0.0)
        self.fills.append(fill)
        return fill

    def sell(self, *, quantity: float, price: float) -> PaperFill:
        if quantity <= 0 or price <= 0:
            raise ValueError("quantity and price must be positive")
        if quantity > self.xrp_balance + 1e-9:
            raise ValueError("insufficient paper XRP balance")
        remaining = quantity
        cost = 0.0
        while remaining > 1e-12:
            lot_quantity, lot_cost = self._lots[0]
            taken = min(remaining, lot_quantity)
            lot_cost_taken = lot_cost * (taken / lot_quantity)
            cost += lot_cost_taken
            lot_quantity -= taken
            remaining -= taken
            if lot_quantity <= 1e-12:
                self._lots.popleft()
            else:
                self._lots[0] = [lot_quantity, lot_cost - lot_cost_taken]
        notional = quantity * price
        fee = notional * self.fee_pct
        proceeds = notional - fee
        self.zar_balance += proceeds
        self.xrp_balance -= quantity
        fill = PaperFill("SELL", quantity, price, fee, proceeds - cost)
        self.fills.append(fill)
        return fill

    def daily_report(self, *, mark_price: float) -> dict[str, float | int]:
        """Return a current, fee-adjusted paper performance snapshot."""
        equity = self.mark_to_market(mark_price)
        realized = sum(fill.realized_pnl_zar for fill in self.fills)
        remaining_cost = sum(cost for _, cost in self._lots)
        unrealized = (self.xrp_balance * mark_price) - remaining_cost
        return {
            "fills": len(self.fills),
            "realized_pnl_zar": realized,
            "unrealized_pnl_zar": unrealized,
            "equity_zar": equity,
            "total_pnl_zar": equity - self.initial_equity_zar,
            "zar_balance": self.zar_balance,
            "xrp_balance": self.xrp_balance,
        }

    def save(self, path: str | Path) -> None:
        """Atomically persist only simulated portfolio state (never credentials)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "zar_balance": self.zar_balance,
            "xrp_balance": self.xrp_balance,
            "fee_pct": self.fee_pct,
            "initial_equity_zar": self.initial_equity_zar,
            "lots": list(self._lots),
            "fills": [asdict(fill) for fill in self.fills],
        }
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(target)

    @classmethod
    def load(cls, path: str | Path) -> "PaperPortfolio":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        portfolio = cls(
            initial_zar=float(payload["zar_balance"]),
            initial_xrp=0.0,
            fee_pct=float(payload["fee_pct"]),
        )
        portfolio.zar_balance = float(payload["zar_balance"])
        portfolio.xrp_balance = float(payload["xrp_balance"])
        portfolio.initial_equity_zar = float(payload["initial_equity_zar"])
        portfolio._lots = deque([[float(q), float(cost)] for q, cost in payload["lots"]])
        portfolio.fills = [PaperFill(**fill) for fill in payload["fills"]]
        return portfolio

    def mark_to_market(self, price: float) -> float:
        if price <= 0:
            raise ValueError("price must be positive")
        return self.zar_balance + (self.xrp_balance * price)
