from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.enums import RegimeType  # noqa: E402
from app.strategy.templates import _SEED_REGISTRY  # noqa: E402

VALID_REGIMES = {r.value for r in RegimeType}
VALID_WITH_ALL = VALID_REGIMES | {"ALL"}
SOURCE_W = {"tournament": 1.0, "backtest_1y": 0.85, "brain_elite": 0.75, "brain_best": 0.65}
ASSET_MARKERS = {
    "gold": ["XAU", "GOLD"],
    "silver": ["XAG", "SILVER"],
    "crypto": ["BTC", "ETH", "XRP", "DOGE", "SOL", "BNB", "ADA"],
    "oil": ["USOIL", "UKOIL", "WTI", "BRENT"],
}
FOREX_MARKERS = ["EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "USD"]


@dataclass(slots=True)
class Rules:
    min_trades: int = 30
    min_wr: float = 40.0
    min_pf: float = 1.05
    max_dd: float = 25.0
    min_score: float = 0.0


def f(v, d=0.0):
    try:
        x = float(v)
        if x != x or x in (float("inf"), float("-inf")):
            return d
        return x
    except Exception:
        return d


def i(v, d=0):
    try:
        return int(float(v))
    except Exception:
        return d


def wr(v):
    x = f(v, 0.0)
    return x * 100.0 if 0 < x <= 1.0 else x


def norm_name(s: str) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def norm_regime(raw) -> str:
    if not raw:
        return "ALL"
    t = str(raw).strip()
    u = t.upper()
    if u in VALID_WITH_ALL:
        return u
    m = re.search(r"RegimeType\.([A-Z_]+)", t)
    if m and m.group(1) in VALID_REGIMES:
        return m.group(1)
    for rg in VALID_REGIMES:
        if rg in u:
            return rg
    return "ALL"


def norm_symbol(raw) -> str:
    s = str(raw).strip()
    if not s:
        return s
    up = s.upper()
    if up.endswith("C") or up.endswith("M"):
        return up[:-1] + up[-1].lower()
    return up


def detect_asset_class(symbol: str) -> str:
    up = str(symbol).upper()
    for asset, markers in ASSET_MARKERS.items():
        if any(m in up for m in markers):
            return asset
    fx_hits = sum(1 for m in FOREX_MARKERS if m in up)
    if fx_hits >= 2:
        return "forex"
    return "*"


def score(trades: int, win_rate: float, pf: float, pnl: float, dd: float, base: float = 0.0) -> float:
    if trades <= 0 or pf <= 0:
        return -999.0
    s = min(pf, 5.0) * 12.0 + max(0.0, win_rate) * 0.25 + min(trades, 400) * 0.05 + max(-20.0, min(20.0, pnl / 500.0))
    if dd > 0:
        s -= min(dd, 80.0) * 0.35
    if base > 0:
        s = s * 0.65 + base * 0.35
    return round(s, 6)


def parse_per_regime(blob: str | None) -> dict[str, dict]:
    if not blob:
        return {}
    try:
        obj = json.loads(blob)
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    acc: dict[str, dict] = {}
    for k, v in obj.items():
        if not isinstance(v, dict):
            continue
        rg = norm_regime(k)
        if rg == "ALL":
            continue
        t = i(v.get("trades", 0), 0)
        if t <= 0:
            continue
        w = wr(v.get("win_rate", 0))
        p = f(v.get("pnl", 0.0), 0.0)
        b = acc.setdefault(rg, {"t": 0, "w": 0.0, "p": 0.0})
        b["t"] += t
        b["w"] += w * t
        b["p"] += p
    out: dict[str, dict] = {}
    for rg, b in acc.items():
        t = b["t"]
        out[rg] = {"trades": t, "win_rate": (b["w"] / t) if t > 0 else 0.0, "pnl": b["p"]}
    return out


def ensure_schema(conn: sqlite3.Connection, rules: Rules):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_rankings_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            rank_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            regime TEXT NOT NULL,
            rank INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            source_mix TEXT NOT NULL,
            total_trades INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0,
            profit_factor REAL DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            max_drawdown_pct REAL DEFAULT 999,
            score REAL DEFAULT 0,
            production_eligible INTEGER DEFAULT 0,
            reject_reasons TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rank_daily ON strategy_rankings_daily(run_at, symbol, regime, rank)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS production_routing_rules (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            min_trades INTEGER NOT NULL,
            min_win_rate REAL NOT NULL,
            min_profit_factor REAL NOT NULL,
            max_drawdown_pct REAL NOT NULL,
            min_score REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO production_routing_rules
            (id, min_trades, min_win_rate, min_profit_factor, max_drawdown_pct, min_score, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            min_trades=excluded.min_trades,
            min_win_rate=excluded.min_win_rate,
            min_profit_factor=excluded.min_profit_factor,
            max_drawdown_pct=excluded.max_drawdown_pct,
            min_score=excluded.min_score,
            updated_at=excluded.updated_at
        """,
        (rules.min_trades, rules.min_wr, rules.min_pf, rules.max_dd, rules.min_score, datetime.now(timezone.utc).isoformat()),
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(backtest_routing)").fetchall()]
    if "max_drawdown_pct" not in cols:
        conn.execute("ALTER TABLE backtest_routing ADD COLUMN max_drawdown_pct REAL DEFAULT 999")
    conn.commit()


def sync_registry(conn: sqlite3.Connection) -> int:
    need = [x for x in _SEED_REGISTRY if x["strategy_name"] == "vfinal" or x["strategy_name"].startswith("opus_")]
    now = datetime.now(timezone.utc).isoformat()
    for x in need:
        conn.execute(
            """
            INSERT INTO strategy_registry
                (strategy_name,class_name,module_path,timeframe,asset_class,suitable_regimes,priority,is_active,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,1,?,?)
            ON CONFLICT(strategy_name) DO UPDATE SET
                class_name=excluded.class_name,
                module_path=excluded.module_path,
                timeframe=excluded.timeframe,
                asset_class=excluded.asset_class,
                suitable_regimes=excluded.suitable_regimes,
                priority=excluded.priority,
                is_active=1,
                updated_at=excluded.updated_at
            """,
            (
                x["strategy_name"],
                x["class_name"],
                x["module_path"],
                x.get("timeframe", "M5"),
                x.get("asset_class", "*"),
                json.dumps(x.get("suitable_regimes", [])),
                x.get("priority", 50),
                now,
                now,
            ),
        )
    conn.commit()
    return len(need)


def strategy_map(conn: sqlite3.Connection) -> dict[str, str]:
    m: dict[str, str] = {}
    for r in conn.execute("SELECT strategy_name FROM strategy_registry").fetchall():
        m[norm_name(r[0])] = r[0]
    for x in _SEED_REGISTRY:
        m.setdefault(norm_name(x["strategy_name"]), x["strategy_name"])
    return m


def strategy_assets(conn: sqlite3.Connection) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in conn.execute("SELECT strategy_name, asset_class FROM strategy_registry").fetchall():
        out[str(r[0])] = str(r[1] or "*")
    return out


def canon(raw: str, m: dict[str, str]) -> str:
    n = norm_name(raw)
    if n in m:
        return m[n]
    return str(raw).strip().lower().replace("-", "_").replace(" ", "_")


def add(lst: list[dict], source: str, symbol: str, regime: str, strategy: str, trades: int, win_rate: float, pf: float, pnl: float, dd: float, sc: float):
    lst.append(
        {
            "source": source,
            "symbol": norm_symbol(symbol),
            "regime": norm_regime(regime),
            "strategy": strategy,
            "total_trades": i(trades, 0),
            "win_rate": wr(win_rate),
            "profit_factor": f(pf, 0.0),
            "total_pnl": f(pnl, 0.0),
            "max_drawdown_pct": f(dd, 999.0),
            "score": f(sc, 0.0),
        }
    )


def extract_candidates(trading: sqlite3.Connection, brain: sqlite3.Connection) -> list[dict]:
    out: list[dict] = []

    latest = trading.execute("SELECT MAX(created_at) FROM tournament_results").fetchone()[0]
    if latest:
        rows = trading.execute(
            "SELECT symbol,strategy_name,total_trades,win_rate,profit_factor,total_profit_usd,max_drawdown_pct,composite_score,per_regime FROM tournament_results WHERE created_at=?",
            (latest,),
        ).fetchall()
        for r in rows:
            add(out, "tournament", r[0], "ALL", r[1], r[2], r[3], r[4], r[5], r[6], r[7])
            for rg, st in parse_per_regime(r[8]).items():
                rg_sc = score(i(st.get("trades", 0)), wr(st.get("win_rate", 0)), f(r[4], 0.0), f(st.get("pnl", 0.0), 0.0), f(r[6], 999.0), f(r[7], 0.0))
                add(out, "tournament", r[0], rg, r[1], st.get("trades", 0), st.get("win_rate", 0), r[4], st.get("pnl", 0.0), r[6], rg_sc)

    rows = trading.execute(
        "SELECT symbol,strategy,total_trades,win_rate,profit_factor,total_profit_usd,max_drawdown_pct,per_regime_json,created_at FROM backtest_1y_results WHERE total_trades>0 ORDER BY datetime(created_at) DESC"
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    for r in rows:
        key = (norm_symbol(r[0]), str(r[1]))
        if key in seen:
            continue
        seen.add(key)
        base_sc = score(i(r[2]), wr(r[3]), f(r[4]), f(r[5]), f(r[6], 999.0), 0.0)
        add(out, "backtest_1y", r[0], "ALL", r[1], r[2], r[3], r[4], r[5], r[6], base_sc)
        for rg, st in parse_per_regime(r[7]).items():
            rg_sc = score(i(st.get("trades", 0)), wr(st.get("win_rate", 0)), f(r[4], 0.0), f(st.get("pnl", 0.0), 0.0), f(r[6], 999.0), 0.0)
            add(out, "backtest_1y", r[0], rg, r[1], st.get("trades", 0), st.get("win_rate", 0), r[4], st.get("pnl", 0.0), r[6], rg_sc)

    elite = brain.execute(
        "SELECT symbol,strategy,total_trades,win_rate,profit_factor,total_profit_usd,max_drawdown_pct FROM elite_backtest_results ORDER BY timestamp DESC"
    ).fetchall()
    seen = set()
    for r in elite:
        key = (norm_symbol(r[0]), str(r[1]))
        if key in seen:
            continue
        seen.add(key)
        sc = score(i(r[2]), wr(r[3]), f(r[4]), f(r[5]), f(r[6], 999.0), 0.0)
        add(out, "brain_elite", r[0], "ALL", r[1], r[2], r[3], r[4], r[5], r[6], sc)

    best = brain.execute(
        "SELECT symbol,strategy,total_trades,win_rate,profit_factor,total_profit_usd,max_drawdown_pct FROM best_strategy_params WHERE is_active=1 ORDER BY timestamp DESC"
    ).fetchall()
    seen = set()
    for r in best:
        key = (norm_symbol(r[0]), str(r[1]))
        if key in seen:
            continue
        seen.add(key)
        sc = score(i(r[2]), wr(r[3]), f(r[4]), f(r[5]), f(r[6], 999.0), 0.0)
        add(out, "brain_best", r[0], "ALL", r[1], r[2], r[3], r[4], r[5], r[6], sc)

    return out


def is_noise(r: dict) -> bool:
    if i(r["total_trades"], 0) <= 0:
        return True
    if f(r["profit_factor"], 0.0) <= 0 or f(r["profit_factor"], 0.0) > 50:
        return True
    if wr(r["win_rate"]) <= 0:
        return True
    if abs(f(r["total_pnl"], 0.0)) < 1e-9:
        return True
    return False


def aggregate(rows: list[dict], m: dict[str, str]) -> list[dict]:
    g: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        if is_noise(r):
            continue
        sym = norm_symbol(r["symbol"])
        rg = norm_regime(r["regime"])
        st = canon(r["strategy"], m)
        src = str(r["source"])
        w = SOURCE_W.get(src, 0.5)
        k = (sym, rg, st)
        e = g.setdefault(
            k,
            {
                "symbol": sym,
                "regime": rg,
                "strategy": st,
                "sources": set(),
                "w": 0.0,
                "wr": 0.0,
                "pf": 0.0,
                "pnl": 0.0,
                "dd": 0.0,
                "sc": 0.0,
                "trades": 0,
            },
        )
        e["sources"].add(src)
        e["w"] += w
        e["wr"] += wr(r["win_rate"]) * w
        e["pf"] += f(r["profit_factor"]) * w
        e["pnl"] += f(r["total_pnl"]) * w
        e["dd"] += f(r["max_drawdown_pct"], 999.0) * w
        s = f(r["score"], 0.0)
        if s <= 0:
            s = score(i(r["total_trades"]), wr(r["win_rate"]), f(r["profit_factor"]), f(r["total_pnl"]), f(r["max_drawdown_pct"], 999.0), 0.0)
        e["sc"] += s * w
        e["trades"] = max(e["trades"], i(r["total_trades"], 0))

    out: list[dict] = []
    for e in g.values():
        w = e["w"] or 1.0
        out.append(
            {
                "symbol": e["symbol"],
                "regime": e["regime"],
                "strategy": e["strategy"],
                "source_mix": ",".join(sorted(e["sources"])),
                "total_trades": e["trades"],
                "win_rate": round(e["wr"] / w, 6),
                "profit_factor": round(e["pf"] / w, 6),
                "total_pnl": round(e["pnl"] / w, 6),
                "max_drawdown_pct": round(e["dd"] / w, 6),
                "score": round(e["sc"] / w, 6),
            }
        )
    return out


def evaluate(r: dict, rules: Rules, asset_map: dict[str, str]) -> tuple[int, str]:
    reason = []
    symbol_asset = detect_asset_class(r["symbol"])
    strategy_asset = asset_map.get(r["strategy"], "*")
    if strategy_asset not in ("*", symbol_asset):
        reason.append(f"asset_mismatch:{strategy_asset}!={symbol_asset}")
    if i(r["total_trades"], 0) < rules.min_trades:
        reason.append(f"trades<{rules.min_trades}")
    if wr(r["win_rate"]) < rules.min_wr:
        reason.append(f"wr<{rules.min_wr}")
    if f(r["profit_factor"], 0.0) < rules.min_pf:
        reason.append(f"pf<{rules.min_pf}")
    dd = f(r["max_drawdown_pct"], 999.0)
    if dd <= 0 or dd > rules.max_dd:
        reason.append(f"dd>{rules.max_dd}")
    if f(r["score"], -999.0) < rules.min_score:
        reason.append(f"score<{rules.min_score}")
    return (1 if not reason else 0, ",".join(reason))


def rank_rows(rows: list[dict], rules: Rules, asset_map: dict[str, str]) -> list[dict]:
    b: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        b.setdefault((r["symbol"], r["regime"]), []).append(r)
    out: list[dict] = []
    for (sym, rg), lst in b.items():
        tmp = []
        for r in lst:
            ok, rej = evaluate(r, rules, asset_map)
            d = dict(r)
            d["production_eligible"] = ok
            d["reject_reasons"] = rej
            tmp.append(d)
        tmp.sort(key=lambda x: (x["production_eligible"], f(x["score"]), f(x["profit_factor"]), wr(x["win_rate"]), i(x["total_trades"])), reverse=True)
        for idx, r in enumerate(tmp, 1):
            r["rank"] = idx
            r["symbol"] = sym
            r["regime"] = rg
            out.append(r)
    return out


def build_routes(ranked: list[dict]) -> list[dict]:
    b: dict[tuple[str, str], list[dict]] = {}
    regimes: dict[str, set[str]] = {}
    for r in ranked:
        b.setdefault((r["symbol"], r["regime"]), []).append(r)
        regimes.setdefault(r["symbol"], set()).add(r["regime"])
    out: list[dict] = []
    for sym, rs in regimes.items():
        all_rows = b.get((sym, "ALL"), [])
        all_pick = next((x for x in all_rows if x["production_eligible"] == 1), None)
        if all_pick is None:
            any_ok = [x for x in ranked if x["symbol"] == sym and x["production_eligible"] == 1]
            any_ok.sort(key=lambda x: f(x["score"]), reverse=True)
            all_pick = any_ok[0] if any_ok else None
        if all_pick:
            out.append({
                "symbol": sym, "regime": "ALL", "strategy": all_pick["strategy"],
                "profit_factor": all_pick["profit_factor"], "win_rate": all_pick["win_rate"],
                "total_trades": all_pick["total_trades"], "total_pnl": all_pick["total_pnl"],
                "max_drawdown_pct": all_pick["max_drawdown_pct"], "score": all_pick["score"],
            })
        for rg in sorted(rs):
            if rg == "ALL":
                continue
            rows = b.get((sym, rg), [])
            pick = next((x for x in rows if x["production_eligible"] == 1), None)
            if pick is None:
                pick = all_pick
            if pick is None:
                continue
            out.append({
                "symbol": sym, "regime": rg, "strategy": pick["strategy"],
                "profit_factor": pick["profit_factor"], "win_rate": pick["win_rate"],
                "total_trades": pick["total_trades"], "total_pnl": pick["total_pnl"],
                "max_drawdown_pct": pick["max_drawdown_pct"], "score": pick["score"],
            })
    return out


def persist_rankings(conn: sqlite3.Connection, ranked: list[dict], run_at: str):
    d = run_at[:10]
    conn.execute("DELETE FROM strategy_rankings_daily WHERE rank_date=?", (d,))
    conn.executemany(
        """
        INSERT INTO strategy_rankings_daily
            (run_at,rank_date,symbol,regime,rank,strategy,source_mix,total_trades,win_rate,profit_factor,total_pnl,max_drawdown_pct,score,production_eligible,reject_reasons)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                run_at, d, r["symbol"], r["regime"], r["rank"], r["strategy"], r["source_mix"],
                r["total_trades"], r["win_rate"], r["profit_factor"], r["total_pnl"], r["max_drawdown_pct"],
                r["score"], r["production_eligible"], r["reject_reasons"],
            )
            for r in ranked
        ],
    )
    conn.commit()


def persist_routes(conn: sqlite3.Connection, routes: list[dict], ranked_symbols: list[str]):
    syms = sorted({str(s) for s in ranked_symbols})
    if not syms:
        return
    lower_syms = [s.lower() for s in syms]
    q = ",".join(["?"] * len(lower_syms))
    conn.execute(f"DELETE FROM backtest_routing WHERE lower(symbol) IN ({q})", lower_syms)
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """
        INSERT INTO backtest_routing
            (symbol,regime,strategy,profit_factor,win_rate,total_trades,total_pnl,max_drawdown_pct,score,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol,regime) DO UPDATE SET
            strategy=excluded.strategy,
            profit_factor=excluded.profit_factor,
            win_rate=excluded.win_rate,
            total_trades=excluded.total_trades,
            total_pnl=excluded.total_pnl,
            max_drawdown_pct=excluded.max_drawdown_pct,
            score=excluded.score,
            updated_at=excluded.updated_at
        """,
        [(r["symbol"], r["regime"], r["strategy"], r["profit_factor"], r["win_rate"], r["total_trades"], r["total_pnl"], r["max_drawdown_pct"], r["score"], now) for r in routes],
    )
    conn.commit()


def export_reports(out_dir: Path, ranked: list[dict], routes: list[dict]):
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "strategy_ranking_latest.csv"
    md_path = out_dir / "strategy_ranking_latest.md"

    headers = ["symbol", "regime", "rank", "strategy", "source_mix", "total_trades", "win_rate", "profit_factor", "total_pnl", "max_drawdown_pct", "score", "production_eligible", "reject_reasons"]
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=headers)
        w.writeheader()
        for r in sorted(ranked, key=lambda x: (x["symbol"], x["regime"], x["rank"])):
            w.writerow({h: r.get(h) for h in headers})

    with md_path.open("w", encoding="utf-8") as fp:
        fp.write("# Daily Strategy Ranking\n\n")
        fp.write("| Symbol | Regime | Strategy | Trades | WR | PF | DD% | Score | Prod |\n")
        fp.write("|---|---|---|---:|---:|---:|---:|---:|---|\n")
        for r in sorted([x for x in ranked if x["rank"] == 1], key=lambda x: (x["symbol"], x["regime"])):
            prod = "YES" if r["production_eligible"] == 1 else "NO"
            fp.write(f"| {r['symbol']} | {r['regime']} | {r['strategy']} | {i(r['total_trades'])} | {wr(r['win_rate']):.1f} | {f(r['profit_factor']):.2f} | {f(r['max_drawdown_pct']):.1f} | {f(r['score']):.2f} | {prod} |\n")

        fp.write("\n## Seeded Routing\n\n")
        fp.write("| Symbol | Regime | Strategy | Trades | WR | PF | DD% | Score |\n")
        fp.write("|---|---|---|---:|---:|---:|---:|---:|\n")
        for r in sorted(routes, key=lambda x: (x["symbol"], x["regime"])):
            fp.write(f"| {r['symbol']} | {r['regime']} | {r['strategy']} | {i(r['total_trades'])} | {wr(r['win_rate']):.1f} | {f(r['profit_factor']):.2f} | {f(r['max_drawdown_pct']):.1f} | {f(r['score']):.2f} |\n")

    return csv_path, md_path


def main():
    p = argparse.ArgumentParser(description="Daily strategy ranking and routing seeder")
    p.add_argument("--trading-db", default=str(ROOT / "data" / "sqlite" / "trading.db"))
    p.add_argument("--brain-db", default=str(ROOT / "data" / "sqlite" / "brain.db"))
    p.add_argument("--output-dir", default=str(ROOT / "data" / "reports"))
    p.add_argument("--min-trades", type=int, default=30)
    p.add_argument("--min-win-rate", type=float, default=40.0)
    p.add_argument("--min-pf", type=float, default=1.05)
    p.add_argument("--max-dd", type=float, default=25.0)
    p.add_argument("--min-score", type=float, default=0.0)
    args = p.parse_args()

    rules = Rules(args.min_trades, args.min_win_rate, args.min_pf, args.max_dd, args.min_score)
    trading_path = Path(args.trading_db).resolve()
    brain_path = Path(args.brain_db).resolve()
    out_dir = Path(args.output_dir).resolve()

    trading = sqlite3.connect(str(trading_path))
    trading.row_factory = sqlite3.Row
    brain = sqlite3.connect(str(brain_path))
    brain.row_factory = sqlite3.Row

    try:
        ensure_schema(trading, rules)
        synced = sync_registry(trading)
        m = strategy_map(trading)
        asset_map = strategy_assets(trading)
        raw = extract_candidates(trading, brain)
        agg = aggregate(raw, m)
        ranked = rank_rows(agg, rules, asset_map)
        routes = build_routes(ranked)
        run_at = datetime.now(timezone.utc).isoformat()
        persist_rankings(trading, ranked, run_at)
        persist_routes(trading, routes, ranked_symbols=sorted({r["symbol"] for r in ranked}))
        csv_path, md_path = export_reports(out_dir, ranked, routes)

        top_prod = [x for x in ranked if x["rank"] == 1 and x["production_eligible"] == 1]
        print("=== Daily Strategy Ranking Complete ===")
        print(f"Trading DB: {trading_path}")
        print(f"Brain DB:   {brain_path}")
        print(f"Registry synced (vfinal/opus_*): {synced}")
        print(f"Raw candidates: {len(raw)}")
        print(f"Aggregated candidates: {len(agg)}")
        print(f"Ranked rows: {len(ranked)}")
        print(f"Production top routes: {len(top_prod)}")
        print(f"Seeded backtest_routing rows: {len(routes)}")
        print(f"CSV report: {csv_path}")
        print(f"MD report:  {md_path}")
    finally:
        brain.close()
        trading.close()


if __name__ == "__main__":
    main()
