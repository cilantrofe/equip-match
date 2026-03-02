def match_by_price(target, candidates):
    if not target or not candidates:
        return None
    try:
        t_price = float(target.price)
    except Exception:
        return None
    best = None
    best_diff = None
    for c in candidates:
        if not c.price: continue
        try:
            cp = float(c.price)
        except:
            continue
        diff = abs(t_price - cp) / max(t_price, 1.0)
        if best is None or diff < best_diff:
            best = c
            best_diff = diff
    return best

def match_by_tech(target, candidates):
    if not target or not candidates:
        return None
    def specs_map(p):
        out = {}
        for s in getattr(p, "specs", []):
            if s.spec_value_text:
                out[s.spec_name.lower()] = s.spec_value_text.lower()
            elif s.spec_value_num:
                out[s.spec_name.lower()] = str(s.spec_value_num)
        return out
    tmap = specs_map(target)
    best = None
    best_score = -1
    for c in candidates:
        cmap = specs_map(c)
        score = 0
        for k, v in tmap.items():
            if k in cmap and cmap[k] == v:
                score += 1
        if score > best_score:
            best_score = score
            best = c
    return best
