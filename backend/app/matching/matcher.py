def match_by_price(target, candidates):
    if not target or not candidates:
        return None

    try:
        t_price = float(target.price)
    except Exception:
        return None

    best = None
    best_diff = None

    target_brand = (target.brand or "").lower().strip()

    for c in candidates:
        # исключаем тот же бренд
        if (c.brand or "").lower().strip() == target_brand:
            continue

        if not c.price:
            continue

        try:
            cp = float(c.price)
        except:
            continue

        diff = abs(t_price - cp) / max(t_price, 1.0)

        if best is None or diff < best_diff:
            best = c
            best_diff = diff

    if not best:
        return None

    return best, round(1 - best_diff, 3)


def match_by_tech(target, candidates):
    if not target or not candidates:
        return None

    target_brand = (target.brand or "").lower().strip()

    def specs_map(p):
        out = {}
        for s in getattr(p, "specs", []):
            key = (s.spec_name or "").lower().strip()

            if s.spec_value_num is not None:
                out[key] = float(s.spec_value_num)
            elif s.spec_value_text:
                out[key] = s.spec_value_text.lower().strip()

        return out

    tmap = specs_map(target)

    scored = []

    for c in candidates:
        # исключаем тот же бренд
        if (c.brand or "").lower().strip() == target_brand:
            continue

        cmap = specs_map(c)

        total_score = 0
        used_features = 0

        for k, v in tmap.items():
            if k not in cmap:
                continue

            cv = cmap[k]

            if isinstance(v, float) and isinstance(cv, float):
                max_val = max(abs(v), abs(cv), 1.0)
                sim = 1 - abs(v - cv) / max_val
                sim = max(0, sim)

            else:
                sim = 1.0 if v == cv else 0.0

            total_score += sim
            used_features += 1

        if used_features == 0:
            continue

        final_score = total_score / used_features

        scored.append((c, final_score))

    if not scored:
        return None

    # сортировка по убыванию
    scored.sort(key=lambda x: x[1], reverse=True)

    best, best_score = scored[0]

    return best, round(best_score, 3)
