from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

from app.matching.matcher import (
    match_by_tech,
    match_by_price,
    MatchResult,
    _collect_specs,
    _score_pair,
)
from app.normalization.spec_aliases import WEIGHT_DEFAULTS

_PID = 0


def _spec(canonical: str, num=None, text: str = None, weight=None) -> SimpleNamespace:
    return SimpleNamespace(
        spec_name=canonical,
        spec_name_canonical=canonical,
        spec_value_num=num,
        spec_value_text=text,
        weight=weight,
    )


def _product(
    brand: str, price: float, specs: list, category: str = "ip_camera"
) -> SimpleNamespace:
    global _PID
    _PID += 1
    return SimpleNamespace(
        id=_PID,
        source_sku=f"SKU-{_PID:03d}",
        brand=brand,
        model=f"M-{_PID}",
        category=category,
        price=price,
        url="",
        specs=specs,
    )


TOTAL_W = 20.0


def _target_specs(
    res,
    ip,
    voltage,
    power,
    codec="h.265",
    poe_val="да",
    mount="потолочный",
    wifi_val="нет",
    color_val="белый",
    storage_val="microsd",
) -> list:
    return [
        _spec("camera_resolution", num=res),
        _spec("ip_rating", num=ip),
        _spec("voltage", num=voltage),
        _spec("power", num=power),
        _spec("video_codec", text=codec),
        _spec("poe", text=poe_val),
        _spec("mount_type", text=mount),
        _spec("wifi", text=wifi_val),
        _spec("color", text=color_val),
        _spec("storage", text=storage_val),
    ]


def _relevant_specs(
    res, ip, voltage, power, codec="h.265", poe_val="да", mount="потолочный"
) -> list:
    return [
        _spec("camera_resolution", num=round(res * 0.925, 2)),  # sim ≈ 0.925
        _spec("ip_rating", num=round(ip * 0.910, 2)),  # sim ≈ 0.910
        _spec("voltage", num=voltage),
        _spec("power", num=power),
        _spec("video_codec", text=codec),
        _spec("poe", text=poe_val),
        _spec("mount_type", text=mount),
    ]


def _uniform_trap_specs(
    voltage,
    power,
    mount="потолочный",
    wifi_val="нет",
    color_val="белый",
    storage_val="microsd",
) -> list:

    return [
        _spec("voltage", num=round(voltage * 0.9, 2)),  # sim ≈ 0.90
        _spec("power", num=round(power * 0.9, 2)),  # sim ≈ 0.90
        _spec("video_codec", text="h.264"),  # sim ≈ 0.80
        _spec("poe", text="да"),
        _spec("mount_type", text=mount),
        _spec("wifi", text=wifi_val),
        _spec("color", text=color_val),
        _spec("storage", text=storage_val),
    ]


def _no_penalty_trap_specs(res, ip, voltage) -> list:

    return [
        _spec("camera_resolution", num=res),
        _spec("ip_rating", num=ip),
        _spec("voltage", num=voltage),
    ]


_T = [
    (4.0, 67.0, 48.0, 15.0, "потолочный", "нет", 10_000, "Hikvision"),
    (8.0, 66.0, 12.0, 8.0, "потолочный", "нет", 15_000, "Dahua"),
    (2.0, 54.0, 24.0, 12.0, "настенный", "нет", 6_000, "Uniview"),
    (4.0, 68.0, 24.0, 20.0, "потолочный", "да", 18_000, "Axis"),
    (12.0, 65.0, 48.0, 25.0, "настенный", "нет", 25_000, "Hanwha"),
]

_BRANDS_POOL = [
    "Reolink",
    "Ezviz",
    "TP-Link",
    "Bosch",
    "Panasonic",
    "Sony",
    "Grandstream",
    "Fanvil",
]


# Синтетический датасет
def build_dataset() -> list[dict]:
    dataset = []
    b_idx = 0

    def next_brand():
        nonlocal b_idx
        br = _BRANDS_POOL[b_idx % len(_BRANDS_POOL)]
        b_idx += 1
        return br

    for i, (res, ip, voltage, power, mount, wifi_val, price, brand) in enumerate(_T):
        target = _product(
            brand,
            price,
            _target_specs(res, ip, voltage, power, mount=mount, wifi_val=wifi_val),
        )

        relevant = _product(
            next_brand(),
            price * 1.6,
            _relevant_specs(res, ip, voltage, power, mount=mount),
        )

        # price_trap: близкая цена, одна плохая характеристика
        price_trap = _product(
            next_brand(),
            price * 1.02,
            [
                _spec("camera_resolution", num=round(res * 0.25, 2)),  # sim ≈ 0.25
            ],
        )

        # специфические ловушки по типу target
        if i < 3:
            specific = _product(
                next_brand(),
                price * 2.5,
                _uniform_trap_specs(voltage, power, mount=mount, wifi_val=wifi_val),
            )
            trap_label = "uniform_trap"
        else:
            specific = _product(
                next_brand(), price * 2.5, _no_penalty_trap_specs(res, ip, voltage)
            )
            trap_label = "no_penalty_trap"

        # шум: несколько кандидатов с низкими скорами и далёкими ценами
        noise = [
            _product(
                next_brand(),
                price * 3.0,
                [
                    _spec("camera_resolution", num=round(res * 0.3, 2)),
                    _spec("color", text="чёрный"),
                ],
            ),
            _product(
                next_brand(),
                price * 4.0,
                [
                    _spec("poe", text="нет"),
                    _spec("wifi", text="да"),
                ],
            ),
            _product(
                next_brand(),
                price * 0.2,
                [
                    _spec("ip_rating", num=round(ip * 0.5, 2)),
                ],
            ),
        ]

        dataset.append(
            {
                "target": target,
                "relevant": relevant,
                "price_trap": price_trap,
                trap_label: specific,
                "noise": noise,
            }
        )

    return dataset


def all_candidates(entry: dict) -> list:
    cands = [entry["relevant"], entry["price_trap"]]
    for k in ("uniform_trap", "no_penalty_trap"):
        if k in entry:
            cands.append(entry[k])
    cands.extend(entry["noise"])
    return cands


# Варианты алгоритмов


def run_weighted(target, candidates, limit=20):
    return match_by_tech(target, candidates, limit=limit, exclude_same_brand=False)


def run_uniform(target, candidates, limit=20):
    overrides = {k: 1.0 for k in WEIGHT_DEFAULTS}
    return match_by_tech(
        target,
        candidates,
        limit=limit,
        exclude_same_brand=False,
        weight_overrides=overrides,
    )


def run_no_penalty(target, candidates, limit=20):
    """Взвешенный алгоритм без штрафа за отсутствующие характеристики."""
    base = match_by_tech(
        target, candidates, limit=len(candidates), exclude_same_brand=False
    )
    recalc = []
    for r in base:
        miss_w = sum(f.weight for f in r.breakdown if f.note == "missing_on_candidate")
        total_w = sum(f.weight for f in r.breakdown)
        denom = total_w - miss_w
        num = sum(f.contribution for f in r.breakdown)
        score = round(num / denom, 3) if denom > 0 else 0.0
        recalc.append(
            MatchResult(candidate=r.candidate, score=score, breakdown=r.breakdown)
        )
    recalc.sort(key=lambda r: r.score, reverse=True)
    return recalc[:limit]


def run_price(target, candidates, limit=20):
    return match_by_price(target, candidates, limit=limit, exclude_same_brand=False)


ALGORITHMS: dict[str, callable] = {
    "Только цена": run_price,
    "Равные веса": run_uniform,
    "Без штрафа\nза пропуск": run_no_penalty,
    "Взвешенный\n(выбранный алгоритм)": run_weighted,
}

ALGO_COLORS = {
    "Только цена": "#a8c7e8",
    "Равные веса": "#f9c784",
    "Без штрафа\nза пропуск": "#b5d99c",
    "Взвешенный\n(выбранный алгоритм)": "#e07b7b",
}


# Метрики


def rank_of(results: list[MatchResult], product_id: int) -> Optional[int]:
    for i, r in enumerate(results, 1):
        if r.candidate.id == product_id:
            return i
    return None


def mrr(ranks: list[Optional[int]]) -> float:
    vals = [1.0 / r for r in ranks if r is not None]
    return float(np.mean(vals)) if vals else 0.0


def precision_at_k(ranks: list[Optional[int]], k: int) -> float:
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def run_experiments(dataset: list[dict]) -> dict:
    results = {}
    for name, fn in ALGORITHMS.items():
        ranks, rel_scores, partial_scores, irrel_scores = [], [], [], []

        for entry in dataset:
            target = entry["target"]
            rel_id = entry["relevant"].id
            trap_ids = set()
            for k in ("uniform_trap", "no_penalty_trap", "price_trap"):
                if k in entry:
                    trap_ids.add(entry[k].id)
            noise_ids = {p.id for p in entry["noise"]}

            cands = all_candidates(entry)
            res = fn(target, cands, limit=len(cands))
            ranks.append(rank_of(res, rel_id))

            score_map = {r.candidate.id: r.score for r in res}
            if rel_id in score_map:
                rel_scores.append(score_map[rel_id])
            for pid in trap_ids:
                if pid in score_map:
                    partial_scores.append(score_map[pid])
            for pid in noise_ids:
                if pid in score_map:
                    irrel_scores.append(score_map[pid])

        results[name] = {
            "ranks": ranks,
            "mrr": mrr(ranks),
            "rel_scores": rel_scores,
            "partial_scores": partial_scores,
            "irrel_scores": irrel_scores,
        }
    return results


# Графики

PLOTS_DIR = Path(__file__).parent / "plots"


def _save(fig, name: str):
    PLOTS_DIR.mkdir(exist_ok=True)
    path = PLOTS_DIR / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {path.name}")


# График 1: Ablation Study


def plot_ablation_mrr(exp: dict):
    names = list(ALGORITHMS)
    vals = [exp[n]["mrr"] for n in names]
    colors = [ALGO_COLORS[n] for n in names]

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(
        names, vals, color=colors, edgecolor="white", linewidth=1.2, width=0.55
    )
    for bar, v in zip(bars, vals):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.02,
            f"{v:.2f}",
            ha="center",
            va="bottom",
            fontsize=12,
            fontweight="bold",
        )

    ax.set_ylim(0, 1.18)
    ax.set_ylabel("MRR (Mean Reciprocal Rank)", fontsize=12)
    ax.set_title(
        "Ablation Study: качество алгоритмов матчинга", fontsize=13, fontweight="bold"
    )
    ax.axhline(1.0, color="gray", linestyle="--", linewidth=0.8, alpha=0.5)
    ax.tick_params(axis="x", labelsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    _save(fig, "01_ablation_mrr.png")


# График 2: Precision@k


def plot_precision_at_k(exp: dict):
    ks = list(range(1, 8))
    fig, ax = plt.subplots(figsize=(9, 5))
    styles = ["-o", "-s", "-^", "-D"]
    for (name, data), ls in zip(exp.items(), styles):
        pk = [precision_at_k(data["ranks"], k) for k in ks]
        ax.plot(
            ks,
            pk,
            ls,
            label=name.replace("\n", " "),
            color=ALGO_COLORS[name],
            linewidth=2,
            markersize=7,
        )

    ax.set_xlabel("k", fontsize=12)
    ax.set_ylabel("Precision@k", fontsize=12)
    ax.set_title(
        "Precision@k: доля целевых товаров в топ-k", fontsize=13, fontweight="bold"
    )
    ax.set_xticks(ks)
    ax.set_ylim(-0.05, 1.15)
    ax.legend(fontsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.3)
    _save(fig, "02_precision_at_k.png")


# График 3: Распределение скоров


def plot_score_distribution(exp: dict):
    key = "Взвешенный\n(выбранный алгоритм)"
    d = exp[key]
    groups = ["Релевантные", "Кандидаты\n(ловушки)", "Нерелевантные"]
    values = [d["rel_scores"], d["partial_scores"], d["irrel_scores"]]
    colors = ["#e07b7b", "#f9c784", "#a8c7e8"]

    fig, ax = plt.subplots(figsize=(8, 5))
    bp = ax.boxplot(
        values,
        patch_artist=True,
        widths=0.45,
        medianprops=dict(color="black", linewidth=2),
    )
    for patch, c in zip(bp["boxes"], colors):
        patch.set_facecolor(c)
        patch.set_alpha(0.85)

    rng = np.random.default_rng(42)
    for i, vals in enumerate(values, 1):
        jitter = rng.uniform(-0.08, 0.08, len(vals))
        ax.scatter(
            np.full(len(vals), i) + jitter,
            vals,
            alpha=0.65,
            s=32,
            color="dimgray",
            zorder=3,
        )

    ax.set_xticks([1, 2, 3])
    ax.set_xticklabels(groups, fontsize=11)
    ax.set_ylabel("Score (0-1)", fontsize=12)
    ax.set_title(
        "Распределение скоров по группам кандидатов\n" "(взвешенный алгоритм)",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_ylim(-0.05, 1.12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    _save(fig, "03_score_distribution.png")


# График 4: Чувствительность к весу


def _sensitivity_dataset() -> list[dict]:
    entries = []

    # Тест 1: camera_resolution + ip_rating(2.5) + voltage(3.0); crossover=0.79
    t1 = _product(
        "VendorA",
        10_000,
        [
            _spec("camera_resolution", num=4.0),
            _spec("ip_rating", num=67.0),
            _spec("voltage", num=48.0),
        ],
    )
    r1 = _product(
        "VendorB",
        16_000,
        [  # sim: 0.95 / 0.90 / 0.90
            _spec("camera_resolution", num=3.70),
            _spec("ip_rating", num=60.97),
            _spec("voltage", num=43.20),
        ],
    )
    d1 = _product(
        "VendorC",
        9_500,
        [  # sim: 0.25 / 1.0 / 1.0
            _spec("camera_resolution", num=1.00),
            _spec("ip_rating", num=67.0),
            _spec("voltage", num=48.0),
        ],
    )
    entries.append({"target": t1, "relevant": r1, "deceiver": d1})

    # Тест 2: + power(2.5); crossover=1.14
    t2 = _product(
        "VendorA",
        12_000,
        [
            _spec("camera_resolution", num=4.0),
            _spec("ip_rating", num=67.0),
            _spec("voltage", num=48.0),
            _spec("power", num=15.0),
        ],
    )
    r2 = _product(
        "VendorB",
        19_200,
        [  # sim: 0.95 / 0.90 / 0.90 / 0.90
            _spec("camera_resolution", num=3.70),
            _spec("ip_rating", num=60.97),
            _spec("voltage", num=43.20),
            _spec("power", num=13.50),
        ],
    )
    d2 = _product(
        "VendorC",
        11_500,
        [  # sim: 0.25 / 1.0 / 1.0 / 1.0
            _spec("camera_resolution", num=1.00),
            _spec("ip_rating", num=67.0),
            _spec("voltage", num=48.0),
            _spec("power", num=15.0),
        ],
    )
    entries.append({"target": t2, "relevant": r2, "deceiver": d2})

    # Тест 3: те же 4 спец., но sim=0.85 у relevant; crossover=1.71
    t3 = _product(
        "VendorA",
        15_000,
        [
            _spec("camera_resolution", num=4.0),
            _spec("ip_rating", num=67.0),
            _spec("voltage", num=48.0),
            _spec("power", num=15.0),
        ],
    )
    r3 = _product(
        "VendorB",
        24_000,
        [  # sim: 0.95 / 0.85 / 0.85 / 0.85
            _spec("camera_resolution", num=3.70),
            _spec("ip_rating", num=57.0),
            _spec("voltage", num=40.80),
            _spec("power", num=12.75),
        ],
    )
    d3 = _product(
        "VendorC",
        14_000,
        [  # sim: 0.25 / 1.0 / 1.0 / 1.0
            _spec("camera_resolution", num=1.00),
            _spec("ip_rating", num=67.0),
            _spec("voltage", num=48.0),
            _spec("power", num=15.0),
        ],
    )
    entries.append({"target": t3, "relevant": r3, "deceiver": d3})

    return entries


# График 5: Тепловая карта сходства


def plot_similarity_heatmap(dataset: list[dict]):
    products, labels = [], []
    for i, entry in enumerate(dataset, 1):
        t, r = entry["target"], entry["relevant"]
        products += [t, r]
        labels += [f"T{i}\n{t.brand}", f"R{i}\n{r.brand}"]

    n = len(products)
    matrix = np.zeros((n, n))
    for i, pi in enumerate(products):
        for j, pj in enumerate(products):
            if i == j:
                matrix[i, j] = 1.0
                continue
            ti = _collect_specs(pi)
            tj = _collect_specs(pj)
            if ti and tj:
                score, _, common = _score_pair(ti, tj)
                matrix[i, j] = score if common > 0 else 0.0

    fig, ax = plt.subplots(figsize=(10, 8))
    sns.heatmap(
        matrix,
        annot=True,
        fmt=".2f",
        cmap="YlOrRd",
        xticklabels=labels,
        yticklabels=labels,
        linewidths=0.5,
        linecolor="white",
        vmin=0,
        vmax=1,
        ax=ax,
        annot_kws={"size": 8},
    )
    ax.set_title(
        "Матрица попарного технического сходства\n"
        "(T — target, R — релевантный аналог)",
        fontsize=13,
        fontweight="bold",
    )
    ax.tick_params(axis="x", rotation=45, labelsize=9)
    ax.tick_params(axis="y", rotation=0, labelsize=9)
    _save(fig, "05_similarity_heatmap.png")


def main():
    dataset = build_dataset()
    print(f"  {len(dataset)} targets, ~{len(dataset)*6} candidates total")

    exp = run_experiments(dataset)

    print("\n  Algorithm                     MRR    Ranks")
    for name, data in exp.items():
        short = name.replace("\n", " ")
        print(f"  {short:<32} {data['mrr']:.3f}  {data['ranks']}")

    plot_ablation_mrr(exp)
    plot_precision_at_k(exp)
    plot_score_distribution(exp)
    plot_similarity_heatmap(dataset)

    print(f"\nDone. Plots: {(PLOTS_DIR).resolve()}")


if __name__ == "__main__":
    main()
