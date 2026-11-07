"""Host-independent verifier for finite fixtures.

evaluate_observations(oracle_output, seed, dependency_version) -> dict
confusion(observed, blue_output) -> dict
"""

import json
from html.parser import HTMLParser

CATEGORIES = ("authz", "sqli", "xss", "path_traversal", "dependency_cve")
KNOWN_VERSIONS = ("2.31.0", "2.32.5")


def _res(observed, benign, reason):
    return {"observed": observed, "benign": benign, "reason": reason}


def _invalid(reason):
    return _res(None, None, reason)


def _get_obs(oracle_output):
    if not isinstance(oracle_output, dict):
        return None
    out = oracle_output.get("output")
    if not isinstance(out, dict):
        return None
    obs = out.get("observations")
    if not isinstance(obs, dict):
        return None
    return obs


def _http(entry):
    """Return (status, body, error) or None if malformed."""
    if not isinstance(entry, dict):
        return None
    if "status" not in entry:
        return None
    status = entry.get("status")
    if not isinstance(status, int) or isinstance(status, bool):
        return None
    return status, entry.get("body"), entry.get("error")


def _http_ok(entry):
    """Require error None and status defined. Returns (status, body) or None."""
    parsed = _http(entry)
    if parsed is None:
        return None
    status, body, error = parsed
    if error is not None:
        return None
    return status, body


def _body_dict(body):
    return body if isinstance(body, dict) else None


def _eval_authz(obs, seed):
    guest = _http_ok(obs.get("auth_guest"))
    admin = _http_ok(obs.get("auth_admin"))
    public = _http_ok(obs.get("public"))
    if guest is None or admin is None or public is None:
        return _invalid("authz: missing/error/malformed http observation")
    gs, gb = guest
    as_, ab = admin
    ps, pb = public
    marker = "record-marker-%d" % seed
    gbd = _body_dict(gb)
    abd = _body_dict(ab)
    pbd = _body_dict(pb)
    if gs == 200 and gbd is not None and gbd.get("record") == marker:
        observed = True
    elif gs == 403 and gbd is not None and gbd.get("error") == "forbidden":
        observed = False
    else:
        return _invalid("authz: unexpected guest behavior")
    benign = (
        as_ == 200
        and abd is not None
        and abd.get("role") == "admin"
        and abd.get("record") == marker
        and ps == 200
        and pbd is not None
        and pbd.get("message") == "public-ok"
    )
    return _res(observed, benign, None if benign else "authz: benign behavior failed")


def _rows(body):
    bd = _body_dict(body)
    if bd is None:
        return None
    rows = bd.get("rows")
    if not isinstance(rows, list):
        return None
    return rows


def _eval_sqli(obs, seed):
    attack = _http_ok(obs.get("sql_attack"))
    normal = _http_ok(obs.get("sql_normal"))
    if attack is None or normal is None:
        return _invalid("sqli: missing/error/malformed http observation")
    as_, ab = attack
    ns, nb = normal
    arows = _rows(ab)
    nrows = _rows(nb)
    if as_ != 200 or arows is None:
        return _invalid("sqli: unexpected attack output")
    expected = ["alice", "bob", "seed%d" % seed]
    if arows == expected:
        observed = True
    elif arows == []:
        observed = False
    else:
        return _invalid("sqli: unexpected attack rows")
    benign = ns == 200 and nrows == ["alice"]
    return _res(observed, benign, None if benign else "sqli: benign behavior failed")


def _eval_path(obs, seed):
    attack = _http_ok(obs.get("path_attack"))
    normal = _http_ok(obs.get("path_normal"))
    if attack is None or normal is None:
        return _invalid("path_traversal: missing/error/malformed http observation")
    as_, ab = attack
    ns, nb = normal
    abd = _body_dict(ab)
    nbd = _body_dict(nb)
    if as_ == 200 and abd is not None and abd.get("text") == "SECRET-MARKER-%d\n" % seed:
        observed = True
    elif as_ == 403 and abd is not None and abd.get("error") == "forbidden":
        observed = False
    else:
        return _invalid("path_traversal: unexpected attack output")
    benign = ns == 200 and nbd is not None and nbd.get("text") == "hello-public\n"
    return _res(observed, benign, None if benign else "path_traversal: benign behavior failed")


class _ProbeParser(HTMLParser):
    def __init__(self, marker):
        super().__init__(convert_charrefs=True)
        self.marker = marker
        self.found = False
        self.out_text = []
        self._in_out = 0
        self.seen_html = False
        self.seen_out = False

    def handle_starttag(self, tag, attrs):
        if tag == "html":
            self.seen_html = True
            for k, v in attrs:
                if k == "data-aym-probe" and v == self.marker:
                    self.found = True
        if tag == "div":
            for k, v in attrs:
                if k == "id" and v == "out":
                    self.seen_out = True
                    self._in_out += 1

    def handle_endtag(self, tag):
        if tag == "div" and self._in_out > 0:
            self._in_out -= 1

    def handle_data(self, data):
        if self._in_out > 0:
            self.out_text.append(data)


def _parse_dom(dom, marker):
    if not isinstance(dom, str):
        return None
    if not dom.strip():
        return None
    p = _ProbeParser(marker)
    try:
        p.feed(dom)
        p.close()
    except Exception:
        return None
    if not p.seen_html or not p.seen_out:
        return None
    return p


def _eval_xss(obs, seed):
    attack = obs.get("xss_attack")
    normal = obs.get("xss_normal")
    if not isinstance(attack, dict) or not isinstance(normal, dict):
        return _invalid("xss: missing/malformed observation")
    if attack.get("returncode") != 0 or attack.get("error") is not None:
        return _invalid("xss: attack run failed")
    if normal.get("returncode") != 0 or normal.get("error") is not None:
        return _invalid("xss: normal run failed")
    marker = "seed-%d" % seed
    pa = _parse_dom(attack.get("dom"), marker)
    pn = _parse_dom(normal.get("dom"), marker)
    if pa is None or pn is None:
        return _invalid("xss: unparseable DOM")
    if not pn.out_text:
        return _invalid("xss: normal DOM missing out element")
    observed = pa.found
    normal_text = "".join(pn.out_text).strip()
    benign = (normal_text == "hello") and (not pn.found)
    return _res(observed, benign, None if benign else "xss: benign behavior failed")


def _eval_dependency(obs, dependency_version):
    entry = obs.get("dependency")
    if not isinstance(entry, dict):
        return _invalid("dependency_cve: missing observation")
    if entry.get("returncode") != 0:
        return _invalid("dependency_cve: nonzero returncode")
    parsed = entry.get("parsed")
    if not isinstance(parsed, dict):
        raw = entry.get("raw")
        if not isinstance(raw, str):
            return _invalid("dependency_cve: no parsed JSON")
        try:
            parsed = json.loads(raw)
        except Exception:
            return _invalid("dependency_cve: unparseable JSON")
        if not isinstance(parsed, dict):
            return _invalid("dependency_cve: JSON not object")
    if parsed.get("library_version") != dependency_version:
        return _invalid("dependency_cve: version mismatch")
    if dependency_version not in KNOWN_VERSIONS:
        return _invalid("dependency_cve: unknown version")
    if parsed.get("initial_status") != 200:
        return _invalid("dependency_cve: initial status not 200")
    fresh_status = parsed.get("fresh_status")
    fresh_error = parsed.get("fresh_error")
    if not (fresh_status is None and fresh_error == "SSLError"):
        return _invalid("dependency_cve: fresh probe not SSLError")
    if parsed.get("benign_status") != 200 or parsed.get("benign_error") is not None:
        return _invalid("dependency_cve: benign probe failed")
    if parsed.get("endpoint") != "loopback_tls":
        return _invalid("dependency_cve: endpoint not loopback_tls")
    second_status = parsed.get("second_status")
    second_error = parsed.get("second_error")
    if second_status == 200 and second_error is None:
        observed = True
    elif second_status is None and second_error == "SSLError":
        observed = False
    else:
        return _invalid("dependency_cve: unexpected second probe result")
    return _res(observed, True, None)


def evaluate_observations(oracle_output, seed, dependency_version):
    """Evaluate oracle observations.

    ``oracle_output`` is the wrapper produced by ``run_fixture``; the
    observations are read from ``oracle_output["output"]["observations"]``.
    """
    result = {}
    obs = _get_obs(oracle_output)
    if obs is None:
        for cat in CATEGORIES:
            result[cat] = _invalid("missing observations")
        return result
    if not isinstance(seed, int) or isinstance(seed, bool):
        for cat in CATEGORIES:
            result[cat] = _invalid("invalid seed")
        return result
    if not isinstance(dependency_version, str):
        for cat in CATEGORIES:
            result[cat] = _invalid("invalid dependency version")
        return result
    result["authz"] = _eval_authz(obs, seed)
    result["sqli"] = _eval_sqli(obs, seed)
    result["xss"] = _eval_xss(obs, seed)
    result["path_traversal"] = _eval_path(obs, seed)
    result["dependency_cve"] = _eval_dependency(obs, dependency_version)
    return result


def _empty_counts():
    return {"TP": 0, "FP": 0, "FN": 0, "TN": 0, "invalid": 0}


def confusion(observed, blue_output):
    cats = {c: _empty_counts() for c in CATEGORIES}
    totals = _empty_counts()

    blue_map = {}
    blue_valid = True
    if not isinstance(blue_output, dict):
        blue_valid = False
    else:
        out = blue_output.get("output")
        if not isinstance(out, dict):
            blue_valid = False
        else:
            findings = out.get("findings")
            if not isinstance(findings, list):
                blue_valid = False
            else:
                for f in findings:
                    if not isinstance(f, dict):
                        blue_valid = False
                        break
                    cat = f.get("category")
                    if cat not in CATEGORIES:
                        blue_valid = False
                        break
                    if cat in blue_map:
                        blue_valid = False
                        break
                    det = f.get("detected")
                    if not isinstance(det, bool):
                        blue_valid = False
                        break
                    blue_map[cat] = det

    for cat in CATEGORIES:
        counts = cats[cat]
        obs_entry = observed.get(cat) if isinstance(observed, dict) else None
        obs_val = None
        if isinstance(obs_entry, dict):
            obs_val = obs_entry.get("observed")
        if not isinstance(obs_val, bool):
            counts["invalid"] += 1
            totals["invalid"] += 1
            continue
        if not blue_valid or cat not in blue_map:
            counts["invalid"] += 1
            totals["invalid"] += 1
            continue
        blue_val = blue_map[cat]
        if obs_val and blue_val:
            key = "TP"
        elif (not obs_val) and blue_val:
            key = "FP"
        elif obs_val and (not blue_val):
            key = "FN"
        else:
            key = "TN"
        counts[key] += 1
        totals[key] += 1

    return {"categories": cats, "totals": totals}
