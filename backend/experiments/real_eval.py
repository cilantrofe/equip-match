"""Эксперимент на реальных данных из БД."""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import Product
from app.matching.matcher import match_by_price, match_by_tech
from app.normalization.spec_aliases import WEIGHT_DEFAULTS

DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
MIN_SPECS: int = 3
N_TARGETS: int = 20
TOP_K: int = 5
PLOTS_DIR = Path(__file__).parent / "plots"
CSV_PATH = Path(__file__).parent / "real_results.csv"

ALGO_LABELS: dict[str, str] = {
    "price": "Только цена",
    "uniform": "Равные веса",
    "no_penalty": "Без штрафа\nза пропуск",
    "weighted": "Взвешенный\n(выбранный алгоритм)",
}
UNIFORM_WEIGHTS = {k: 1.0 for k in WEIGHT_DEFAULTS}


async def _load_products(category: str) -> list[Product]:
    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(
            select(Product)
            .where(Product.category == category)
            .options(selectinload(Product.specs))
        )
        products = list(result.scalars().all())
    await engine.dispose()
    return products


async def _list_categories() -> list[tuple[str, int]]:
    from sqlalchemy import func

    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(
            select(Product.category, func.count(Product.id))
            .group_by(Product.category)
            .order_by(func.count(Product.id).desc())
        )
        rows = result.all()
    await engine.dispose()
    return [(r[0], r[1]) for r in rows if r[0]]


def _pick(products: list[Product], n_targets: int):
    """Выбрать target-товары и кандидатов."""
    good = [p for p in products if len(p.specs or []) >= MIN_SPECS]

    by_source: dict[int, list[Product]] = defaultdict(list)
    for p in good:
        by_source[p.source_id or 0].append(p)

    target_source_id = max(by_source, key=lambda s: len(by_source[s]))
    targets = by_source[target_source_id][:n_targets]
    candidates = [p for p in good if p.source_id != target_source_id]

    target_source_label = (
        (targets[0].brand or f"source_id={target_source_id}")
        if targets
        else str(target_source_id)
    )
    return targets, candidates, target_source_label


def _no_penalty_results(target, candidates: list, limit: int):
    """match_by_tech без штрафа за отсутствующие характеристики."""
    raw = match_by_tech(target, candidates, limit=limit * 4, exclude_same_brand=False)
    rescored = []
    for r in raw:
        common_w = sum(
            f.weight for f in r.breakdown if f.note != "missing_on_candidate"
        )
        score = (
            sum(f.contribution for f in r.breakdown) / common_w if common_w > 0 else 0.0
        )
        rescored.append(
            SimpleNamespace(
                candidate=r.candidate, score=round(score, 3), breakdown=r.breakdown
            )
        )
    rescored.sort(key=lambda x: x.score, reverse=True)
    return rescored[:limit]


def _run_all(target, candidates: list, limit: int):
    return {
        "price": match_by_price(
            target, candidates, limit=limit, exclude_same_brand=False
        ),
        "uniform": match_by_tech(
            target,
            candidates,
            limit=limit,
            weight_overrides=UNIFORM_WEIGHTS,
            exclude_same_brand=False,
        ),
        "no_penalty": _no_penalty_results(target, candidates, limit),
        "weighted": match_by_tech(
            target, candidates, limit=limit, exclude_same_brand=False
        ),
    }


def _generate_csv(targets, candidates, top_k: int, out: Path) -> None:
    rows = []
    for target in targets:
        algo_results = _run_all(target, candidates, limit=top_k)
        for algo_key, results in algo_results.items():
            for rank, r in enumerate(results, start=1):
                cand = r.candidate
                top3_specs = ", ".join(
                    f"{f.name}={f.candidate}"
                    for f in r.breakdown[:3]
                    if f.candidate is not None
                )
                rows.append(
                    {
                        "target_sku": target.source_sku or "",
                        "target_brand": target.brand or "",
                        "algorithm": algo_key,
                        "rank": rank,
                        "candidate_sku": cand.source_sku or "",
                        "candidate_brand": cand.brand or "",
                        "score": r.score,
                        "top3_specs": top3_specs,
                        "relevant": "",
                        "comment": "",
                    }
                )

    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    n_targets = len(targets)
    n_cands = len(candidates)
    print(
        f"\nЦелей: {n_targets}  |  Кандидатов: {n_cands}  |  Строк в CSV: {len(rows)}"
    )


def _mrr(rows_for_algo: list[dict]) -> float:
    """MRR по списку строк одного алгоритма (отсортированных по rank)."""

    by_target: dict[str, list[dict]] = defaultdict(list)
    for r in rows_for_algo:
        by_target[r["target_sku"]].append(r)

    rr_values = []
    for target_sku, rlist in by_target.items():
        rlist_sorted = sorted(rlist, key=lambda x: int(x["rank"]))
        rr = 0.0
        for r in rlist_sorted:
            if r["relevant"].strip() == "1":
                rr = 1.0 / int(r["rank"])
                break
        rr_values.append(rr)
    return sum(rr_values) / len(rr_values) if rr_values else 0.0


def _precision_at_k(rows_for_algo: list[dict], k: int) -> float:
    """P@k усреднённый по всем target."""
    by_target: dict[str, list[dict]] = defaultdict(list)
    for r in rows_for_algo:
        by_target[r["target_sku"]].append(r)

    pk_values = []
    for _, rlist in by_target.items():
        top_k = sorted(rlist, key=lambda x: int(x["rank"]))[:k]
        relevant = sum(1 for r in top_k if r["relevant"].strip() == "1")
        pk_values.append(relevant / k)
    return sum(pk_values) / len(pk_values) if pk_values else 0.0


def _eval_and_plot(csv_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    PLOTS_DIR.mkdir(exist_ok=True)

    with open(csv_path, encoding="utf-8-sig") as f:
        all_rows = list(csv.DictReader(f))

    unlabeled = [r for r in all_rows if r["relevant"].strip() == ""]

    labeled = [r for r in all_rows if r["relevant"].strip() in ("0", "1")]

    mrr_values: dict[str, float] = {}
    for algo_key, algo_label in ALGO_LABELS.items():
        algo_rows = [r for r in labeled if r["algorithm"] == algo_key]
        if algo_rows:
            mrr_values[algo_label] = _mrr(algo_rows)

    max_k = max(int(r["rank"]) for r in labeled)
    pk_curves: dict[str, list[float]] = {}
    for algo_key, algo_label in ALGO_LABELS.items():
        algo_rows = [r for r in labeled if r["algorithm"] == algo_key]
        if algo_rows:
            pk_curves[algo_label] = [
                _precision_at_k(algo_rows, k) for k in range(1, max_k + 1)
            ]

    n_targets = len({r["target_sku"] for r in labeled})
    n_labeled = len(labeled)

    print(f"{'Алгоритм':<42} {'MRR':>6}")
    print("-" * 50)
    for label, val in mrr_values.items():
        print(f"  {label.replace(chr(10), ' '):<40} {val:.3f}")

    # График 1: MRR
    COLORS = ["#e74c3c", "#f39c12", "#3498db", "#2ecc71"]
    labels = list(mrr_values.keys())
    values = list(mrr_values.values())

    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.bar(
        labels,
        values,
        color=COLORS[: len(labels)],
        width=0.5,
        edgecolor="white",
        linewidth=1.2,
    )
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            val + 0.02,
            f"{val:.2f}",
            ha="center",
            va="bottom",
            fontsize=11,
            fontweight="bold",
        )
    ax.set_ylim(0, 1.2)
    ax.set_ylabel("MRR", fontsize=12)
    ax.set_title("Ablation study: MRR", fontsize=13, fontweight="bold")
    ax.yaxis.grid(True, alpha=0.4)
    ax.set_axisbelow(True)
    plt.tight_layout()
    out1 = PLOTS_DIR / "06_real_ablation_mrr.png"
    plt.savefig(out1, dpi=150)
    plt.close()
    print(f"\nСохранён: {out1}")

    # График 2: P@k
    fig, ax = plt.subplots(figsize=(9, 5))
    markers = ["o", "s", "^", "D"]
    ks = list(range(1, max_k + 1))
    for (label, curve), color, marker in zip(pk_curves.items(), COLORS, markers):
        ax.plot(
            ks,
            curve,
            marker=marker,
            color=color,
            linewidth=2,
            markersize=7,
            label=label.replace("\n", " "),
        )
    ax.set_xlabel("k", fontsize=12)
    ax.set_ylabel("Precision@k", fontsize=12)
    ax.set_title("Precision@k", fontsize=13, fontweight="bold")
    ax.set_xticks(ks)
    ax.set_ylim(0, 1.1)
    ax.yaxis.grid(True, alpha=0.4)
    ax.set_axisbelow(True)
    ax.legend(fontsize=10)
    plt.tight_layout()
    out2 = PLOTS_DIR / "07_real_precision_at_k.png"
    plt.savefig(out2, dpi=150)
    plt.close()
    print(f"Сохранён: {out2}")

    # График 3
    synthetic_mrr = {
        "Только цена": 0.500,
        "Равные веса": 0.700,
        "Без штрафа\nза пропуск": 0.800,
        "Взвешенный\n(выбранный алгоритм)": 1.000,
    }

    common_labels = [l for l in synthetic_mrr if l in mrr_values]
    x = np.arange(len(common_labels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(10, 5))
    bars1 = ax.bar(
        x - width / 2,
        [synthetic_mrr[l] for l in common_labels],
        width,
        label="Синтетические данные",
        color="#95a5a6",
        edgecolor="white",
    )
    bars2 = ax.bar(
        x + width / 2,
        [mrr_values[l] for l in common_labels],
        width,
        label="Реальные данные",
        color=COLORS,
        edgecolor="white",
    )

    for bar in list(bars1) + list(bars2):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.02,
            f"{bar.get_height():.2f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    ax.set_xticks(x)
    ax.set_xticklabels([l.replace("\n", "\n") for l in common_labels], fontsize=10)
    ax.set_ylim(0, 1.25)
    ax.set_ylabel("MRR", fontsize=12)
    ax.set_title(
        "MRR: синтетические vs реальные данные", fontsize=13, fontweight="bold"
    )
    ax.yaxis.grid(True, alpha=0.4)
    ax.set_axisbelow(True)
    ax.legend(fontsize=11)
    plt.tight_layout()
    out3 = PLOTS_DIR / "08_synthetic_vs_real.png"
    plt.savefig(out3, dpi=150)
    plt.close()
    print(f"Сохранён: {out3}")


async def _main_generate() -> None:
    print("Запрашиваю список категорий из БД...")
    cats = await _list_categories()
    for i, (cat, cnt) in enumerate(cats):
        print(f"  [{i}] {cat}  ({cnt} товаров)")

    if len(cats) == 1:
        chosen = cats[0][0]
    else:
        idx_str = input("\nВведите номер категории [0]: ").strip() or "0"
        chosen = cats[int(idx_str)][0]

    products = await _load_products(chosen)

    targets, candidates, src = _pick(products, N_TARGETS)

    _generate_csv(targets, candidates, TOP_K, CSV_PATH)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Эксперимент на реальных данных из БД."
    )
    parser.add_argument(
        "--eval",
        action="store_true",
        help="Считать метрики из размеченного real_results.csv и построить графики",
    )
    args = parser.parse_args()

    if args.eval:
        if not CSV_PATH.exists():
            print(f"Файл не найден: {CSV_PATH}")
            sys.exit(1)
        _eval_and_plot(CSV_PATH)
    else:
        asyncio.run(_main_generate())


if __name__ == "__main__":
    main()
