"""Shop-consolidation solver.

Given Parts and candidate Listings across shops, pick one listing per part to
minimize:  total part cost + total shipping + shop_penalty * (number of shops)

shop_penalty encodes "hassle per extra order" (in CNY-equivalent), on top of
real shipping fees. max_shops is a soft cap: when it makes the problem
infeasible the solver reports how many shops are really needed and returns the
cheapest plan anyway (pass strict_shops=True to fail instead). Uses an ILP via
pulp when available; falls back to a greedy set-cover heuristic otherwise.
"""
from __future__ import annotations

from .models import Assignment, Listing, Part, Plan, Shop

MAX_ALTERNATIVES = 3


class InfeasibleError(RuntimeError):
    """The requested shop limit cannot cover every part."""


class SolverUnavailable(RuntimeError):
    """pulp is installed but its CBC solver cannot be started."""


def solve(
    parts: list[Part],
    shops: dict[str, Shop],
    listings: list[Listing],
    max_shops: int = 5,
    shop_penalty: float = 8.0,
    risk_weight: float = 15.0,
    strict_shops: bool = False,
) -> Plan:
    if max_shops is not None and max_shops < 0:
        raise ValueError("max-shops must be >= 0 (0 = no limit)")
    cap = max_shops or None

    unknown = sorted({lst.shop_id for lst in listings if lst.shop_id not in shops})
    if unknown:
        raise ValueError(
            "listings reference shops that are not in the shop list: "
            + ", ".join(unknown)
        )

    by_part: dict[str, list[Listing]] = {}
    for lst in listings:
        by_part.setdefault(lst.part_id, []).append(lst)

    matched = [p for p in parts if by_part.get(p.id)]
    unmatched = [p for p in parts if not by_part.get(p.id)]
    candidate_shops = sorted({lst.shop_id for p in matched for lst in by_part[p.id]})

    if not matched:
        return Plan(shops=shops, assignments=[], unmatched=unmatched,
                    requested_max_shops=cap or 0)

    min_shops = 0
    engine = "ilp"
    try:
        assignments = _solve_ilp(matched, shops, by_part, cap, shop_penalty, risk_weight)
    except (ImportError, SolverUnavailable):
        engine = "greedy"
        assignments, leftover = _solve_greedy(
            matched, shops, by_part, cap, shop_penalty, risk_weight)
        if leftover:
            if strict_shops:
                full, _ = _solve_greedy(
                    matched, shops, by_part, None, shop_penalty, risk_weight)
                needed = len({a.listing.shop_id for a in full})
                raise RuntimeError(_cap_message(needed, cap)) from None
            assignments, _ = _solve_greedy(
                matched, shops, by_part, None, shop_penalty, risk_weight)
    except InfeasibleError:
        min_shops = _min_shops(matched, shops, by_part, candidate_shops)
        if strict_shops:
            raise RuntimeError(_cap_message(min_shops, cap)) from None
        assignments = _solve_ilp(
            matched, shops, by_part, None, shop_penalty, risk_weight)

    used = len({a.listing.shop_id for a in assignments})
    if not min_shops:
        min_shops = used

    return Plan(
        shops=shops,
        assignments=assignments,
        unmatched=unmatched,
        alternatives=_alternatives(matched, by_part, assignments, risk_weight),
        requested_max_shops=cap or 0,
        min_shops_required=min_shops,
        single_shop=_single_shop_baseline(matched, shops, by_part),
        engine=engine,
    )


def _cap_message(min_shops: int, cap: int | None) -> str:
    if not min_shops:
        return (f"no combination of shops covers every part; raise --max-shops "
                f"or check the listings file")
    return (
        f"covering every part needs at least {min_shops} shops, but "
        f"--max-shops is {cap}. Raise --max-shops to {min_shops} or more "
        f"(0 = no limit), or pass --strict-shops to fail instead."
    )


def _min_shops(
    parts: list[Part],
    shops: dict[str, Shop],
    by_part: dict[str, list[Listing]],
    candidate_shops: list[str],
) -> int:
    """Smallest number of shops for which a complete assignment exists."""
    def feasible(k: int) -> bool:
        try:
            _solve_ilp(parts, shops, by_part, k, 0.0, 0.0)
            return True
        except InfeasibleError:
            return False

    lo, hi = 1, len(candidate_shops)
    if hi == 0:
        return 0
    if not feasible(hi):
        return 0
    while lo < hi:
        mid = (lo + hi) // 2
        if feasible(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


def _eff_cost(lst: Listing, qty: int, risk_weight: float) -> float:
    """Objective cost: real price plus a penalty for uncertain matches, so a
    ¥0.5 listing at 45% confidence doesn't beat a ¥20 one at 90%."""
    return lst.cost_for(qty) + (1.0 - lst.confidence) * risk_weight


def _solve_ilp(
    parts: list[Part],
    shops: dict[str, Shop],
    by_part: dict[str, list[Listing]],
    max_shops: int | None,
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
    used_ids = sorted({lst.shop_id for p in parts for lst in by_part[p.id]})
    used_shops = {s: pulp.LpVariable(f"y_{s}", cat="Binary") for s in used_ids}

    part_cost = pulp.lpSum(
        x[(p.id, i)] * _eff_cost(by_part[p.id][i], p.qty, risk_weight)
        for p in parts
        for i in range(len(by_part[p.id]))
    )
    # Shipping modeled as worst-case flat fee per used shop (threshold waivers
    # are recomputed exactly on the final plan; penalty dominates anyway).
    overhead = pulp.lpSum(
        used_shops[s] * (shops[s].ship_fee + shop_penalty) for s in used_ids
    )
    prob += part_cost + overhead

    for p in parts:
        prob += pulp.lpSum(x[(p.id, i)] for i in range(len(by_part[p.id]))) == 1
        for i, lst in enumerate(by_part[p.id]):
            prob += x[(p.id, i)] <= used_shops[lst.shop_id]

    if max_shops:
        prob += pulp.lpSum(used_shops.values()) <= max_shops

    try:
        status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    except Exception as exc:  # CBC missing/broken; caller falls back to greedy
        raise SolverUnavailable(str(exc)) from exc
    if pulp.LpStatus[status] != "Optimal":
        raise InfeasibleError(
            f"no assignment covers every part (status={pulp.LpStatus[status]})"
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
    max_shops: int | None,
    shop_penalty: float,
    risk_weight: float,
) -> tuple[list[Assignment], list[Part]]:
    """Repeatedly open the shop with the best cost-per-covered-part ratio.

    Returns the assignments plus any parts that could not be covered, so the
    caller can report them instead of silently dropping them.
    """
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
            if max_shops and sid not in open_shops and len(open_shops) >= max_shops:
                continue
            score = cost / len(picks)
            if best_score is None or score < best_score:
                best_shop, best_score, best_picks = sid, score, picks
        if best_shop is None:
            break
        open_shops.add(best_shop)
        for pid, lst in best_picks.items():
            chosen[pid] = lst
            del remaining[pid]

    assignments = [
        Assignment(part=p, listing=chosen[p.id], lots=chosen[p.id].lots_for(p.qty),
                   cost=chosen[p.id].cost_for(p.qty))
        for p in parts if p.id in chosen
    ]
    return assignments, [p for p in parts if p.id not in chosen]


def _alternatives(
    parts: list[Part],
    by_part: dict[str, list[Listing]],
    assignments: list[Assignment],
    risk_weight: float,
) -> dict[str, list[Listing]]:
    """Second-choice listings, for when the chosen one is out of stock."""
    chosen = {a.part.id: a.listing for a in assignments}
    out: dict[str, list[Listing]] = {}
    for p in parts:
        offers = sorted(
            (l for l in by_part[p.id] if l is not chosen.get(p.id)),
            key=lambda l: (_eff_cost(l, p.qty, risk_weight), -l.confidence),
        )
        if offers:
            out[p.id] = offers[:MAX_ALTERNATIVES]
    return out


def _single_shop_baseline(
    parts: list[Part],
    shops: dict[str, Shop],
    by_part: dict[str, list[Listing]],
) -> tuple[str, float] | None:
    """Cheapest plan that uses exactly one shop, if any shop can cover every part.

    Compared against the real (non-penalised) plan total so the rendered plan
    can say "two shops save ¥X over the best single shop".
    """
    best: tuple[str, float] | None = None
    for sid, shop in shops.items():
        subtotal = 0.0
        complete = True
        for p in parts:
            offers = [l for l in by_part[p.id] if l.shop_id == sid]
            if not offers:
                complete = False
                break
            subtotal += min(l.cost_for(p.qty) for l in offers)
        if not complete:
            continue
        shipping = 0.0
        if shop.ship_fee > 0:
            waived = shop.free_ship_over > 0 and subtotal >= shop.free_ship_over
            shipping = 0.0 if waived else shop.ship_fee
        total = subtotal + shipping
        if best is None or total < best[1]:
            best = (sid, total)
    return best
