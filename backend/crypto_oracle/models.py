import numpy as np
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone
from app.core.logging import get_logger

logger = get_logger("crypto_oracle.models")

@dataclass
class MarketState:
    symbol: str
    mark_price: float = 0.0
    funding_rate: float = 0.0
    estimated_oi: float = 0.0
    # Orderbook depth
    bids: List[tuple[float, float]] = field(default_factory=list) # (price, qty)
    asks: List[tuple[float, float]] = field(default_factory=list) # (price, qty)
    # Cumulative Volume Delta
    cvd: float = 0.0
    # Recent liquidations in last 5m
    recent_liquidations: List[Dict[str, Any]] = field(default_factory=list)
    last_update: datetime = field(default_factory=datetime.utcnow)

@dataclass
class SignalResult:
    signal: str # "LONG", "SHORT", "NEUTRAL"
    entry_price: float
    stop_loss: float
    target_liquidity_zone: float
    confidence_score: int # 0-100%
    reason: str

class LiquidityIntelligenceEngine:
    def __init__(self, leverage_levels=None, mm_rate=0.004):
        self.leverage_levels = leverage_levels or [100, 50, 25, 10]
        self.mm_rate = mm_rate
        self.state = MarketState("btcusdt")
        
    def update_from_stream(self, data: Dict[str, Any]):
        """Update market state from Binance WS stream data"""
        stream_type = data.get("e")
        if not stream_type:
            return

        if stream_type == "depthUpdate":
            self._update_orderbook(data)
        elif stream_type == "markPriceUpdate":
            self._update_mark_price(data)
        elif stream_type == "aggTrade":
            self._update_agg_trade(data)
        elif stream_type == "forceOrder":
            self._update_liquidation(data)
        
        self.state.last_update = datetime.now(timezone.utc)

    def _update_orderbook(self, data: Dict[str, Any]):
        # "b" and "a" arrays in depthUpdate or partial book
        if "b" in data:
            self.state.bids = [(float(p), float(q)) for p, q in data["b"]]
        if "a" in data:
            self.state.asks = [(float(p), float(q)) for p, q in data["a"]]

    def _update_mark_price(self, data: Dict[str, Any]):
        self.state.mark_price = float(data.get("p", 0.0))
        self.state.funding_rate = float(data.get("r", 0.0))

    def _update_agg_trade(self, data: Dict[str, Any]):
        # m: is the buyer the market maker? (True = seller initiated = sell volume, False = buyer initiated = buy volume)
        qty = float(data.get("q", 0.0))
        is_buyer_maker = data.get("m", False)
        if hasattr(self.state, 'cvd'):
            if is_buyer_maker: # Seller matched -> negative delta
                self.state.cvd -= qty
            else: # Buyer matched -> positive delta
                self.state.cvd += qty

    def _update_liquidation(self, data: Dict[str, Any]):
        # Liquidation event
        order = data.get("o", {})
        liq_data = {
            "side": order.get("S"),
            "price": float(order.get("p", 0.0)),
            "qty": float(order.get("q", 0.0)),
            "time": datetime.now(timezone.utc)
        }
        self.state.recent_liquidations.append(liq_data)
        # Prune old liquidations (older than 5 mins)
        now = datetime.now(timezone.utc)
        self.state.recent_liquidations = [l for l in self.state.recent_liquidations if (now - l["time"]).total_seconds() < 300]


    def estimate_liquidation_clusters(self) -> Dict[str, List[Dict[str, float]]]:
        """
        Estimate where the high leverage clusters are located based on current price and orderbook weight.
        Returns Top and Bottom clusters (prices where shorts and longs get liquidated).
        """
        p = self.state.mark_price
        if p == 0:
            return {"short_liqs": [], "long_liqs": []}

        # Simplified Heatmap generation:
        # Longs get liquidated below price. Shorts get liquidated above price.
        # Liq Price = Entry Price * (1 ± (1 / Leverage) - MM_Rate)
        # Since we don't know the exact entries, we look at the Orderbook depth as a proxy for OI concentration
        
        short_clusters = []
        long_clusters = []
        
        # Calculate Short Liquidations (Above price)
        # Using asks as proxy for heavy resistance/short entries
        total_ask_vol = sum(q for _, q in self.state.asks)
        if total_ask_vol > 0:
            for price, qty in self.state.asks:
                weight = qty / total_ask_vol
                if weight > 0.05: # more than 5% of visible depth
                    for lev in self.leverage_levels:
                        # If heavily shorted at 'price', liquidation is above it
                        liq_p = price * (1 + (1 / lev) - self.mm_rate)
                        short_clusters.append({"price": liq_p, "weight": weight * lev})

        # Calculate Long Liquidations (Below price)
        total_bid_vol = sum(q for _, q in self.state.bids)
        if total_bid_vol > 0:
            for price, qty in self.state.bids:
                weight = qty / total_bid_vol
                if weight > 0.05:
                    for lev in self.leverage_levels:
                        liq_p = price * (1 - (1 / lev) + self.mm_rate)
                        long_clusters.append({"price": liq_p, "weight": weight * lev})

        # Aggregate nearby clusters
        def aggregate(clusters):
            if not clusters: return []
            clusters.sort(key=lambda x: x["price"])
            res = []
            cur_price = clusters[0]["price"]
            cur_weight = clusters[0]["weight"]
            for c in clusters[1:]:
                # If within 0.2% price diff, group them
                if abs(c["price"] - cur_price) / cur_price < 0.002:
                    cur_weight += c["weight"]
                    cur_price = (cur_price + c["price"]) / 2 # center of mass
                else:
                    res.append({"price": cur_price, "weight": cur_weight})
                    cur_price = c["price"]
                    cur_weight = c["weight"]
            res.append({"price": cur_price, "weight": cur_weight})
            return sorted(res, key=lambda x: x["weight"], reverse=True)

        return {
            "short_liqs": aggregate(short_clusters), # targets above
            "long_liqs": aggregate(long_clusters)    # targets below
        }

    def detect_squeeze(self) -> SignalResult:
        """
        Core Trading Strategy Logic:
        If large liquidation cluster exists above price and funding rate is negative -> SHORT SQUEEZE -> LONG
        If large liquidation cluster exists below price and funding rate is positive -> LONG FLUSH -> SHORT
        """
        mark = self.state.mark_price
        if mark == 0:
            return SignalResult("NEUTRAL", 0, 0, 0, 0, "Waiting for Mark Price")
            
        funding = self.state.funding_rate
        clusters = self.estimate_liquidation_clusters()
        
        top_short_cluster = clusters["short_liqs"][0] if clusters["short_liqs"] else None
        top_long_cluster = clusters["long_liqs"][0] if clusters["long_liqs"] else None
        
        # Determine Sentiments
        funding_extreme_negative = funding < -0.0001 # -0.01%
        funding_extreme_positive = funding > 0.0001  # +0.01%
        
        # CVD Trend
        cvd_bulling = self.state.cvd > 0
        cvd_bearing = self.state.cvd < 0
        
        # SCENARIO 1: SHORT SQUEEZE (Signal: LONG)
        # Price is below a big short liquidation cluster, and shorts are paying longs (negative funding)
        if top_short_cluster and funding_extreme_negative and cvd_bulling:
            target = top_short_cluster["price"]
            distance = (target - mark) / mark
            if 0.005 < distance < 0.03: # Target is within 0.5% to 3% move
                return SignalResult(
                    signal="LONG",
                    entry_price=mark,
                    stop_loss=mark * 0.99, # 1% SL
                    target_liquidity_zone=target,
                    confidence_score=int(min(85 + (abs(funding) * 100000), 99)),
                    reason=f"Short Squeeze Setup: High negative funding ({funding:.4%}) with cluster at {target:.2f}"
                )

        # SCENARIO 2: LONG FLUSH (Signal: SHORT)
        # Price is above a big long liquidation cluster, and longs are paying shorts (positive funding)
        if top_long_cluster and funding_extreme_positive and cvd_bearing:
            target = top_long_cluster["price"]
            distance = (mark - target) / mark
            if 0.005 < distance < 0.03:
                return SignalResult(
                    signal="SHORT",
                    entry_price=mark,
                    stop_loss=mark * 1.01, # 1% SL
                    target_liquidity_zone=target,
                    confidence_score=int(min(85 + (funding * 100000), 99)),
                    reason=f"Long Flush Setup: High positive funding ({funding:.4%}) with cluster at {target:.2f}"
                )
                
        # Sub-scenario: Avoid entering inside heavy clusters
        # If we are too close to a cluster, Market Sentiment Model would say wait.
        
        return SignalResult("NEUTRAL", mark, 0, 0, 0, "No clear sweep conditions met")
