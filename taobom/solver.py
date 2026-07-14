"""Shop-consolidation solver.

Given Parts and candidate Listings across shops, pick one listing per part to
minimize:  total part cost + total shipping + shop_penalty * (number of shops)

shop_penalty encodes "hassle per extra order" (in CNY-equivalent), on top of
real shipping fees. max_shops is a hard cap. Uses an ILP via pulp when
available; falls back to a greedy set-cover heuristic otherwise.
"""
from __future__ import annotations

from .models import Assignment, Listing, Part, Plan, Shop


def solve(
    parts: list[Part],
    shops: dict[str, Shop],
    listings: list[Listing],
    max_shops: int = 5,
    shop_penalty: float = 8.0,
    risk_weight: float = 15.0,
) -> Plan:
    by_part: dict[str, list[Listing]] = {}
    for lst in listings:
        by_part.setdefault(lst.part_id, []).append(lst)

    matched = [p for p in parts if by_part.get(p.id)]
    unmatched = [p for p in parts if not by_part.get(p.id)]

    try:
        assignments = _solve_ilp(matched, shops, by_part, max_shops, shop_penalty, risk_weight)
    except ImportError:
        assignments = _solve_greedy(matched, shops, by_part, max_shops, shop_penalty, risk_weight)

    return Plan(shops=shops, assignments=assignments, unmatched=unmatched)


def _eff_cost(lst: Listing, qty: int, risk_weight: float) -> float:
    """Objective cost: real price plus a penalty for uncertain matches, so a
    ¥0.5 listing at 45% confidence doesn't beat a ¥20 one at 90%."""
    return lst.cost_for(qty) + (1.0 - lst.confidence) * risk_weight


def _solve_ilp(
    parts: list[Part],
    shops: dict[str, Shop],
    by_part: dict[str, list[Listing]],
    max_shops: int,
    shop_penalty: float,
    risk_weight: float,
) -> list[Assignment]:
    import pulp

    prob = pulp.LpProblem("taobom", pulp.LpMinimize)

    # x[(part, idx)] = 1 if part fulfilled by by_part[part.id][idx]
    x = {
        (p.id, i): pulp.LpVariable(f"x_{p.id}_{i}", cat="Binary")
        for p in parts
        for i in range(len(by_part[p.id]))
    }
    used_shops = {s: pulp.LpVariable(f"y_{s}", cat="Binary") for s in shops}

    part_cost = pulp.lpSum(
        x[(p.id, i)] * _eff_cost(by_part[p.id][i], p.qty, risk_weight)
        for p in parts
        for i in range(len(by_part[p.id]))
    )
    # Shipping modeled as worst-case flat fee per used shop (threshold waivers
    # are recomputed exactly on the final plan; penalty dominates anyway).
    overhead = pulp.lpSum(
        used_shops[s] * (shops[s].ship_fee + shop_penalty) for s in shops
    )
    prob += part_cost + overhead

    for p in parts:
        prob += pulp.lpSum(x[(p.id, i)] for i in range(len(by_part[p.id]))) == 1
        for i, lst in enumerate(by_part[p.id]):
            prob += x[(p.id, i)] <= used_shops[lst.shop_id]

    prob += pulp.lpSum(used_shops.values()) <= max_shops

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(
            f"ILP infeasible (status={pulp.LpStatus[status]}); "
            f"try raising --max-shops above {max_shops}"
        )

    out: list[Assignment] = []
    for p in parts:
        for i, lst in enumerate(by_part[p.id]):
            if x[(p.id, i)].value() > 0.5:
                out.append(
                    Assignment(part=p, listing=lst, lots=lst.lots_for(p.qty),
                               cost=lst.cost_for(p.qty))
                )
                break
    return out


def _solve_greedy(
    parts: list[Part],
    shops: dict[str, Shop],
    by_part: dict[str, list[Listing]],
    max_shops: int,
    shop_penalty: float,
    risk_weight: float,
) -> list[Assignment]:
    """Repeatedly open the shop with the best (coverage / marginal cost) ratio."""
    remaining = {p.id: p for p in parts}
    open_shops: set[str] = set()
    chosen: dict[str, Listing] = {}

    while remaining:
        best_shop, best_score, best_picks = None, None, {}
        for sid, shop in shops.items():
            picks = {}
            cost = 0.0 if sid in open_shops else (shop.ship_fee + shop_penalty)
            for pid, p in remaining.items():
                offers = [l for l in by_part[pid] if l.shop_id == sid]
                if offers:
                    best = min(offers, key=lambda l: _eff_cost(l, p.qty, risk_weight))
                    picks[pid] = best
                    cost += _eff_cost(best, p.qty, risk_weight)
            if not picks:
                continue
            if sid not in open_shops and len(open_shops) >= max_shops:
                continue
            score = cost / len(picks)
            if best_score is None or score < best_score:
                best_shop, best_score, best_picks = sid, score, picks
        if best_shop is None:
            break  # cap reached and open shops cover nothing new
        open_shops.add(best_shop)
        for pid, lst in best_picks.items():
            chosen[pid] = lst
            del remaining[pid]

    return [
        Assignment(part=p, listing=chosen[p.id], lots=chosen[p.id].lots_for(p.qty),
                   cost=chosen[p.id].cost_for(p.qty))
        for p in parts if p.id in chosen
    ]
