"""
StrategyEvolver — ปรับ parameters ให้ดีขึ้นด้วย Genetic Algorithm.

หน้าที่:
    - รับ PracticeResult → สร้าง parameter variants (mutation + crossover)
    - รัน PracticeEngine ซ้ำกับ params ใหม่ → เปรียบเทียบ score
    - เก็บ "DNA" ที่ดีที่สุดลง MemoryStore

กฎ Safety:
    - Parameters ที่ปรับได้ ถูกจำกัดใน PARAM_BOUNDS (safe range)
    - ห้าม evolve ค่าที่อาจทำลาย risk engine (SL distance, max risk)
    - Evolved params ต้อง validate ก่อนใช้จริง (validate on hold-out data)

กฎ RAM (8GB mode):
    - Population ≤ 10 (default)
    - Generations ≤ 5 (default)
    - ทุก generation ทำ practice run ใหม่ → ไม่เก็บผลเก่าใน RAM
"""

import random
import copy
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# ====================================================================
# PARAM_BOUNDS — ขอบเขตที่ปลอดภัยสำหรับแต่ละ parameter
# ====================================================================

PARAM_BOUNDS: dict[str, tuple[float, float, float]] = {
    # (min, max, step)
    "rsi_period":       (10.0, 30.0, 2.0),
    "rsi_overbought":   (65.0, 85.0, 5.0),
    "rsi_oversold":     (15.0, 35.0, 5.0),
    "atr_multiplier":   (0.5, 5.0, 0.25),
    "rr_ratio":         (1.5, 5.0, 0.5),  # min 1.5 — never evolve RR < 1.5
    "adx_threshold":    (15.0, 35.0, 5.0),
    "ema_fast":         (5.0, 20.0, 1.0),
    "ema_slow":         (20.0, 100.0, 5.0),
    "bb_period":        (10.0, 30.0, 2.0),
    "bb_std":           (1.5, 3.0, 0.25),
    "stoch_period":     (5.0, 21.0, 2.0),
    "stoch_smooth":     (3.0, 7.0, 1.0),
    "confidence_min":   (0.5, 0.9, 0.05),
}

# Default parameters (baseline ก่อน evolve)
DEFAULT_PARAMS: dict[str, float] = {
    "rsi_period": 14.0,
    "rsi_overbought": 70.0,
    "rsi_oversold": 30.0,
    "atr_multiplier": 2.0,
    "rr_ratio": 2.0,
    "adx_threshold": 25.0,
    "ema_fast": 9.0,
    "ema_slow": 21.0,
}

# Mutation probability per parameter
MUTATION_RATE = 0.3  # 30% chance ที่แต่ละ param จะถูก mutate


class StrategyEvolver:
    """
    Genetic Algorithm สำหรับปรับ strategy parameters.

    Flow:
        1. สร้าง population (parameter sets) จาก baseline + random mutations
        2. แต่ละ individual ถูกทดสอบด้วย PracticeEngine
        3. เรียงตาม score → select top 50% เป็น parents
        4. Crossover + Mutate → สร้าง generation ใหม่
        5. ทำซ้ำ N generations → return best params
    """

    def __init__(self, memory_store=None) -> None:
        """
        Args:
            memory_store: MemoryStore สำหรับเก็บ evolved params
        """
        self.memory = memory_store

    # ────────────────────────────────────────────────────────────────
    # evolve — รัน Genetic Algorithm
    # ────────────────────────────────────────────────────────────────

    async def evolve(
        self,
        practice_engine,
        symbol: str,
        candles,
        profile=None,
        strategy_name: str = "",
        baseline_params: dict | None = None,
        generations: int = 5,
        population_size: int = 10,
    ) -> dict:
        """
        รัน Genetic Algorithm เพื่อหา parameters ที่ดีที่สุด.

        Args:
            practice_engine: PracticeEngine instance
            symbol: สัญลักษณ์ที่ทดสอบ
            candles: DataFrame ของแท่งเทียน (training portion)
            profile: SymbolProfile
            strategy_name: ชื่อ strategy ที่จะ evolve
            baseline_params: params เดิม (ถ้าไม่ส่ง → ใช้ DEFAULT_PARAMS)
            generations: จำนวนรุ่น (default 5)
            population_size: ขนาดประชากรต่อรุ่น (default 10)

        Returns:
            dict: {
                "best_params": {...},
                "best_score": float,
                "generations_run": int,
                "improvement": float,  # score ใหม่ - score เก่า
            }
        """
        import asyncio

        base = baseline_params or DEFAULT_PARAMS.copy()
        base_score = 0.0

        # --- ทดสอบ baseline ก่อน ---
        try:
            base_result = await practice_engine.run_practice(
                symbol=symbol,
                strategy_name=strategy_name,
                candles=candles,
                profile=profile,
                params=base,
            )
            base_score = base_result.score
        except Exception as e:
            logger.error("evolve_baseline_error", extra={"error": str(e)})

        # --- สร้าง initial population ---
        population = [base.copy()]
        for _ in range(population_size - 1):
            individual = self._mutate(base.copy())
            population.append(individual)

        best_params = base.copy()
        best_score = base_score

        # --- Evolution loop ---
        for gen in range(generations):
            # ทดสอบทุก individual
            scored: list[tuple[dict, float]] = []
            for params in population:
                try:
                    result = await practice_engine.run_practice(
                        symbol=symbol,
                        strategy_name=strategy_name,
                        candles=candles,
                        profile=profile,
                        params=params,
                    )
                    scored.append((params, result.score))
                except Exception:
                    scored.append((params, 0.0))

            # เรียง → top 50% เป็น parents
            scored.sort(key=lambda x: x[1], reverse=True)

            if scored[0][1] > best_score:
                best_score = scored[0][1]
                best_params = scored[0][0].copy()

            # Select parents (top 50%)
            n_parents = max(2, population_size // 2)
            parents = [s[0] for s in scored[:n_parents]]

            # สร้าง generation ใหม่
            new_pop = [best_params.copy()]  # elitism: เก็บตัวที่ดีที่สุดไว้
            while len(new_pop) < population_size:
                # Pick 2 random parents → crossover → mutate
                p1 = random.choice(parents)
                p2 = random.choice(parents)
                child = self._crossover(p1, p2)
                child = self._mutate(child)
                new_pop.append(child)

            population = new_pop

            logger.debug("evolve_generation", extra={
                "gen": gen + 1,
                "best_score": round(best_score, 4),
                "population": len(population),
            })

            # Yield control
            await asyncio.sleep(0)

        improvement = best_score - base_score

        logger.info("evolution_complete", extra={
            "strategy": strategy_name,
            "symbol": symbol,
            "best_score": round(best_score, 4),
            "base_score": round(base_score, 4),
            "improvement": round(improvement, 4),
            "generations": generations,
        })

        # --- Save ลง MemoryStore ---
        if self.memory and improvement > 0:
            self._save_evolved_params(
                strategy_name=strategy_name,
                symbol=symbol,
                params=best_params,
                score=best_score,
            )

        return {
            "best_params": best_params,
            "best_score": round(best_score, 4),
            "base_score": round(base_score, 4),
            "generations_run": generations,
            "improvement": round(improvement, 4),
        }

    # ────────────────────────────────────────────────────────────────
    # _mutate — สุ่มเปลี่ยน parameters
    # ────────────────────────────────────────────────────────────────

    def _mutate(self, params: dict) -> dict:
        """
        Random mutation ภายใน safe bounds.

        แต่ละ parameter มี MUTATION_RATE chance ที่จะถูกเปลี่ยน.
        ค่าใหม่ = ค่าเดิม ± random steps (ภายใน min/max bounds).
        """
        mutated = copy.deepcopy(params)

        for key, value in mutated.items():
            if key not in PARAM_BOUNDS:
                continue
            if random.random() > MUTATION_RATE:
                continue  # ไม่ mutate param นี้

            min_val, max_val, step = PARAM_BOUNDS[key]
            # สุ่มเปลี่ยน ±1-3 steps
            n_steps = random.choice([-3, -2, -1, 1, 2, 3])
            new_val = value + n_steps * step
            # Clamp ให้อยู่ใน bounds
            new_val = max(min_val, min(max_val, new_val))
            mutated[key] = round(new_val, 4)

        return mutated

    # ────────────────────────────────────────────────────────────────
    # _crossover — ผสมลักษณะจาก 2 parents
    # ────────────────────────────────────────────────────────────────

    def _crossover(self, parent_a: dict, parent_b: dict) -> dict:
        """
        Uniform crossover: แต่ละ parameter มี 50% chance มาจาก parent_a หรือ parent_b.
        """
        child: dict = {}
        all_keys = set(parent_a.keys()) | set(parent_b.keys())

        for key in all_keys:
            if random.random() < 0.5:
                child[key] = parent_a.get(key, parent_b.get(key))
            else:
                child[key] = parent_b.get(key, parent_a.get(key))

        return child

    # ────────────────────────────────────────────────────────────────
    # _save_evolved_params — บันทึก best params ลง MemoryStore
    # ────────────────────────────────────────────────────────────────

    def _save_evolved_params(
        self,
        strategy_name: str,
        symbol: str,
        params: dict,
        score: float,
        regime: str = "ALL",
    ) -> None:
        """บันทึก evolved parameters ลง MemoryStore (SQLite)."""
        if not self.memory:
            return

        try:
            self.memory.save_evolved_params(
                strategy_name=strategy_name,
                symbol=symbol,
                regime=regime,
                params=params,
                score=score,
            )
            logger.info("evolved_params_saved", extra={
                "strategy": strategy_name,
                "symbol": symbol,
                "score": round(score, 4),
            })
        except Exception as e:
            logger.error("evolved_params_save_error", extra={"error": str(e)})
