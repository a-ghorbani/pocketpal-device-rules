#!/usr/bin/env python3
"""Correctness checks for the rules files. No dependencies beyond `jsonschema`.

    python3 tools/check_rules.py schema      # JSON Schema (v1 + v2 files)
    python3 tools/check_rules.py invariants  # rules the APP enforces, plus repo policy
    python3 tools/check_rules.py hf          # size_bytes / sha256 match Hugging Face (metadata only)
    python3 tools/check_rules.py urls        # every GGUF / projector / draft URL resolves
    python3 tools/check_rules.py cdn         # jsDelivr serves what main has (run after a merge+purge)
    python3 tools/check_rules.py all         # schema + invariants (the offline ones)

Why each exists:
  schema      - catches malformed entries before they reach a client.
  invariants  - mirrors src/services/deviceRules/parse.ts: an entry the app would silently DROP is
                a bug, not a recommendation. Also enforces the v1 freeze.
  hf          - a repo can re-upload a file under the same name; a stale sha256 breaks integrity
                checks. Metadata only, nothing is downloaded.
  urls        - upstream deletes files. `ggml-org/gemma-4-E4B-it-GGUF/...Q4_K_M.gguf` was deleted
                2026-07-16 and iOS shipped a 404 for two months before anyone noticed.
  cdn         - a merge only reaches users once jsDelivr's @main cache is purged.
"""
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V1 = ["rules.android.json", "rules.ios.json"]
V2 = ["rules.android.v2.json", "rules.ios.v2.json"]
CDN = "https://cdn.jsdelivr.net/gh/a-ghorbani/pocketpal-device-rules@main"
HF = "https://huggingface.co"
MMPROJ_RE = re.compile(r"mmproj.*\.gguf$", re.I)
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
CAP = 10

# Models whose GGUF architecture needs a minimum app version. They must be gated in v2 and must
# NEVER appear in the frozen v1 files, which pre-gating clients (v1.16.0-v1.17.x) still read.
GATED = {
    "spark-x2.5-1.7b": "1.17.3",
    "spark-x2.5-4b": "1.17.3",
    "nanbeige4.2-3b": "1.17.0",
}

errors: list[str] = []
warnings: list[str] = []


def err(f, msg):
    errors.append(f"{f}: {msg}")


def warn(f, msg):
    warnings.append(f"{f}: {msg}")


def load(name):
    return json.loads((ROOT / name).read_text())


def candidates(doc):
    for tier, block in doc["tiers"].items():
        for i, c in enumerate(block["candidates"]):
            yield tier, i, c


def check_schema():
    from jsonschema import Draft202012Validator
    for name in V1 + V2:
        schema_file = "schema/rules.v2.schema.json" if name in V2 else "schema/rules.v1.schema.json"
        schema = load(schema_file)
        Draft202012Validator.check_schema(schema)
        for e in Draft202012Validator(schema).iter_errors(load(name)):
            err(name, f"/{'/'.join(map(str, e.absolute_path))}: {e.message}")


def check_invariants():
    for name in V1 + V2:
        doc = load(name)
        is_v2 = name in V2
        want_major = "2" if is_v2 else "1"
        if not doc["schema_version"].startswith(want_major + "."):
            err(name, f"schema_version {doc['schema_version']} is not major {want_major}")
        if doc["platform"] != ("android" if "android" in name else "ios"):
            err(name, "platform does not match the filename")

        cls = doc["classifier"]
        bands = {b["id"] for b in cls["ram_bands"]}
        classes = set(cls["soc_classes"])
        seen_matrix = set()
        for row in cls["tier_matrix"]:
            if row["ram_band"] not in bands:
                err(name, f"tier_matrix references unknown ram_band {row['ram_band']}")
            if row["soc_class"] not in classes:
                err(name, f"tier_matrix references unknown soc_class {row['soc_class']}")
            seen_matrix.add((row["ram_band"], row["soc_class"]))
        for b in bands:
            for c in classes:
                if (b, c) not in seen_matrix:
                    err(name, f"tier_matrix has no row for ({b}, {c}) - device would be unclassified")
        for layer in ("soc_model_to_class", "hardware_to_class", "chip_to_class"):
            for k, v in (cls.get(layer) or {}).items():
                if v not in classes:
                    err(name, f"{layer}[{k}] = {v}, not in soc_classes")

        for tier, block in doc["tiers"].items():
            cands = block["candidates"]
            if len(cands) > CAP:
                err(name, f"tier {tier} has {len(cands)} candidates (cap {CAP})")
            keys = [(c["model"], c.get("quant")) for c in cands]
            dupes = {k for k in keys if keys.count(k) > 1}
            if dupes:
                err(name, f"tier {tier} has duplicate (model, quant): {sorted(dupes)}")

        for tier, i, c in candidates(doc):
            where = f"{tier}[{i}] {c.get('model')}"
            # the app drops a candidate missing these outright
            for field in ("model", "hf_repo", "hf_filename", "size_bytes"):
                if not c.get(field):
                    err(name, f"{where}: missing required {field} - the app would drop this entry")
            if "/" not in str(c.get("hf_repo", "")):
                err(name, f"{where}: hf_repo must be 'owner/name'")
            if not str(c.get("hf_filename", "")).endswith(".gguf"):
                err(name, f"{where}: hf_filename must end in .gguf")
            if c.get("sha256") and not re.fullmatch(r"[0-9a-f]{64}", c["sha256"]):
                err(name, f"{where}: sha256 is not 64 lowercase hex chars")

            mm = c.get("mmproj")
            if c.get("multimodal") and not mm:
                err(name, f"{where}: multimodal without mmproj - the app would drop this entry")
            if mm and not c.get("multimodal"):
                err(name, f"{where}: mmproj present but multimodal is not true")
            if mm:
                # parse.ts: a projector in a different repo is dropped, and so is the whole candidate
                if mm.get("hf_repo") != c.get("hf_repo"):
                    err(name, f"{where}: mmproj repo {mm.get('hf_repo')} != model repo - candidate dropped")
                if not MMPROJ_RE.search(mm.get("hf_filename", "")):
                    err(name, f"{where}: mmproj filename must match mmproj*.gguf - candidate dropped")
                if not mm.get("size_bytes"):
                    err(name, f"{where}: mmproj without size_bytes - candidate dropped")

            gate = c.get("min_app_version")
            if gate is not None:
                if not is_v2:
                    err(name, f"{where}: min_app_version in a v1 file - v1 clients ignore it")
                if not SEMVER_RE.match(str(gate)):
                    err(name, f"{where}: min_app_version {gate!r} is not strict X.Y.Z - entry dropped")

            need = GATED.get(c["model"])
            if need and not is_v2:
                err(name, f"{where}: needs app {need}; it must not appear in the frozen v1 files")
            if need and is_v2 and gate != need:
                err(name, f"{where}: min_app_version is {gate!r}, expected {need!r}")

            if c.get("min_ram_gb") and c.get("size_bytes"):
                if c["min_ram_gb"] * 1024**3 < c["size_bytes"]:
                    warn(name, f"{where}: min_ram_gb {c['min_ram_gb']} is below the file size itself")


def _hf_tree(repo, cache={}):
    if repo not in cache:
        try:
            with urllib.request.urlopen(f"{HF}/api/models/{repo}/tree/main", timeout=30) as r:
                cache[repo] = {x["path"]: x for x in json.load(r) if isinstance(x, dict)}
        except Exception as e:  # noqa: BLE001 - network failures are reported, not raised
            cache[repo] = {"__error__": str(e)}
    return cache[repo]


def check_hf():
    for name in V1 + V2:
        doc = load(name)
        for tier, i, c in candidates(doc):
            for label, blk in (("", c), ("mmproj ", c.get("mmproj")), ("draft ", c.get("draft"))):
                if not blk:
                    continue
                where = f"{tier}[{i}] {c['model']} {label}".strip()
                tree = _hf_tree(blk["hf_repo"])
                if "__error__" in tree:
                    warn(name, f"{where}: could not read {blk['hf_repo']} ({tree['__error__']})")
                    continue
                entry = tree.get(blk["hf_filename"])
                if not entry:
                    err(name, f"{where}: {blk['hf_repo']}/{blk['hf_filename']} is not in the repo")
                    continue
                lfs = entry.get("lfs") or {}
                if blk.get("size_bytes") and lfs.get("size") and blk["size_bytes"] != lfs["size"]:
                    err(name, f"{where}: size_bytes {blk['size_bytes']} != HF {lfs['size']}")
                if blk.get("sha256") and lfs.get("oid") and blk["sha256"] != lfs["oid"]:
                    err(name, f"{where}: sha256 differs from HF - the file was re-uploaded")


def check_urls():
    seen = set()
    for name in V1 + V2:
        doc = load(name)
        for tier, i, c in candidates(doc):
            for label, blk in (("", c), ("mmproj ", c.get("mmproj")), ("draft ", c.get("draft"))):
                if not blk:
                    continue
                url = f"{HF}/{blk['hf_repo']}/resolve/main/{blk['hf_filename']}"
                if url in seen:
                    continue
                seen.add(url)
                req = urllib.request.Request(url, method="HEAD")
                try:
                    with urllib.request.urlopen(req, timeout=30) as r:
                        code = r.status
                except urllib.error.HTTPError as e:
                    code = e.code
                except Exception as e:  # noqa: BLE001
                    warn(name, f"{tier}[{i}] {c['model']} {label}{url}: {e}")
                    continue
                if code != 200:
                    err(name, f"{tier}[{i}] {c['model']} {label}HTTP {code} for {url}")
    print(f"  checked {len(seen)} download URLs")


def check_cdn():
    for name in V1 + V2:
        local = load(name)
        try:
            with urllib.request.urlopen(f"{CDN}/{name}", timeout=30) as r:
                served = json.load(r)
        except Exception as e:  # noqa: BLE001
            err(name, f"jsDelivr did not serve this file: {e}")
            continue
        if served.get("rules_version") != local["rules_version"]:
            err(name, f"jsDelivr serves rules_version {served.get('rules_version')}, main has "
                      f"{local['rules_version']} - purge https://purge.jsdelivr.net/gh/"
                      f"a-ghorbani/pocketpal-device-rules@main/{name}")


CHECKS = {"schema": check_schema, "invariants": check_invariants, "hf": check_hf,
          "urls": check_urls, "cdn": check_cdn}

if __name__ == "__main__":
    wanted = sys.argv[1:] or ["all"]
    if wanted == ["all"]:
        wanted = ["schema", "invariants"]
    for w in wanted:
        if w not in CHECKS:
            sys.exit(f"unknown check {w!r}; pick from {', '.join(CHECKS)} or 'all'")
        print(f"== {w}")
        CHECKS[w]()
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"FAIL  {e}")
    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    sys.exit(1 if errors else 0)
