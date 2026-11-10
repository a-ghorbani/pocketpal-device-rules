# pocketpal-device-rules

Device-tier classification rules + per-tier model recommendations for [PocketPal AI](https://github.com/a-ghorbani/pocketpal-ai).

The rules let the app pick sensible default models for the user's phone without bundling them in the app binary. Hosted on GitHub, served free via jsDelivr.

## Files

| File | URL | Platform |
|---|---|---|
| `rules.android.v2.json` | https://cdn.jsdelivr.net/gh/a-ghorbani/pocketpal-device-rules@main/rules.android.v2.json | Android — schema v2 |
| `rules.ios.v2.json` | https://cdn.jsdelivr.net/gh/a-ghorbani/pocketpal-device-rules@main/rules.ios.v2.json | iOS / iPadOS — schema v2 |
| `rules.android.json` | https://cdn.jsdelivr.net/gh/a-ghorbani/pocketpal-device-rules@main/rules.android.json | Android — schema v1, **frozen** |
| `rules.ios.json` | https://cdn.jsdelivr.net/gh/a-ghorbani/pocketpal-device-rules@main/rules.ios.json | iOS / iPadOS — schema v1, **frozen** |
| `schema/rules.v2.schema.json` | | JSON Schema for the v2 files |

The app reads the file matching the current OS. Each file is independent (own classifier, own tier matrix, own candidate list) because the device-classification signals and the accelerated-backend characteristics differ enough that a shared schema would force unnatural compromises.

## Schema channels and version gating

The app has no way to reject a model it cannot load unless the rules tell it which app version is needed, and shipped clients ignore fields they don't know. So gating is done **by URL**:

- **v1 channel — `rules.<platform>.json`, frozen.** Read by PocketPal v1.16.0 – v1.17.x. Keeps `schema_version` `1.2.0-draft`. Only classifier fixes and changes to models that load on **every** v1 client (llama.rn ≥ 0.12.4) may land here. **Never add a model on a newer GGUF architecture to v1.**
- **v2 channel — `rules.<platform>.v2.json`.** Read by app releases that understand v2. `schema_version` major must be `2`; any other major makes the app fall back to its bundled rules.

v2 adds one optional field, `min_app_version` (strict `X.Y.Z`), on a candidate and on its `mmproj` / `draft` blocks:

| Where | App version below it, or value malformed |
|---|---|
| candidate | candidate dropped |
| `mmproj` | **whole candidate** dropped (a vision model never ships without its projector) |
| `draft` | only the draft dropped; the target stays |

Set it to the first PocketPal release whose llama.rn supports the model's GGUF `general.architecture`. Known cut-offs: `lfm2` / `lfm2moe` / LFM2 vision projector: all v2 clients (no field needed); `nanbeige`: 1.17.0; `bailingmoe3` (Ling 3.0): 1.17.2; `spark2_5`: 1.17.3; `lfm3` and its `lfm3vl` projector: 1.22.0; `prism.hadamard` runtime (Ternary-Bonsai-2 Q2_0 — older apps load it but produce wrong output): 1.22.0. Candidates are ordered and gated per entry, so a gated entry may be followed by an ungated fallback.

Rollout rule: a v2 file must be live on `@main` (and jsDelivr purged) **before** an app build that fetches it ships. Until then the app falls back to its bundled rules on the 404, which is safe but stale.

## How the app uses it

1. On startup, show the list from the bundled rules snapshot, then fetch `rules.<platform>.v2.json` (v1 clients: `rules.<platform>.json`) in the background. On success the fetched list replaces non-downloaded presets.
2. Read the device signals the platform exposes:
   - **Android**: `Build.SOC_MODEL` (API 31+), `Build.HARDWARE`, `/proc/cpuinfo` features (`i8mm`, `sve2`, `dotprod`), big-core max frequency, `MemTotal`.
   - **iOS**: `utsname.machine` (e.g. `iPhone16,1`), `NSProcessInfo.processInfo.physicalMemory`.
3. Run the classifier in the rules file to compute a `soc_class`, look up the `(ram_band × soc_class) → tier`, and present that tier's candidate list.
4. The candidate list is **ordered**: the first entry per `model_id` is the primary recommendation. Lower entries are alternates (different quant, different model).

## Candidate fields

Each candidate entry is selection + display hints only — the app derives all model behaviour (chat template, completion settings, stop words) from the GGUF / runtime at load time.

| Field | Meaning |
|---|---|
| `model`, `quant` | model id + quant tag; together they identify the candidate within a tier |
| `display_name` | human-readable model name for the UI (e.g. `Gemma 4 E2B`, `Phi-4 Mini`); `model` stays the stable identity key |
| `hf_repo`, `hf_filename` | where to download the GGUF |
| `params` | parameter count (HF repo GGUF metadata) — display badge + rough estimates |
| `size_bytes` | exact GGUF file size (HF API) — download-size UI + pre-download fit check |
| `sha256` | file content hash (HF LFS oid) — download integrity + stable identity across URL changes |
| `min_ram_gb` | p90 peak load memory + OS-killer headroom |
| `obs_tg` | median community token-gen tok/s on the tier's devices (absent = no submissions yet) |
| `native_low_bit` | model trained natively at low bit-width (BitNet b1.58); its sub-3-bit quant is the native format, not lossy compression |
| `multimodal` | GGUF has a companion projector (mmproj) |
| `mmproj` | explicit projector reference for multimodal entries: `hf_repo` / `hf_filename` / `size_bytes` (same meanings as the main entry — the repo may differ from the model's) plus `modalities` (`["vision"]`, `["audio"]`, or both) — a pre-download labelling hint; the engine reports actual support at load |

The rules file is render-complete offline: the bundled snapshot and the CDN copy carry identical, self-sufficient data — no HF API round-trip needed to show model cards.

## Classifier layers

Each rules file resolves a device to a `soc_class` by walking up to three layers:

**Android** (`rules.android.json/classifier`):
- `soc_model_to_class` — exact match on `Build.SOC_MODEL` (API 31+). Covers ~80% of the active install base.
- `hardware_to_class` — fallback on `Build.HARDWARE` codename for Android < 12 or unknown SoCs.
- `cpu_heuristic` — last-resort rules over `/proc/cpuinfo` features and big-core max-frequency. Anchored on llama.cpp's actual dispatch signals (`i8mm` / `sve2` → flagship; `dotprod` → mid; else budget).

**iOS** (`rules.ios.json/classifier`):
- `device_id_to_chip` — `utsname.machine` (e.g. `iPhone16,1`) → chip name. Sourced from the curated APPLE_DEVICES table in `pocketpal-website/lib/device-lookup.ts`. Covers iPhone 8+ (A11+) and all iPads from iPad mini 5 / Air 3 onward.
- `chip_to_class` — chip name → `soc_class`. Apple chips cluster cleanly: A10-A13 entry, A14/A15/A16/A18-base mid, A17 Pro / A18 Pro / A19 / A19 Pro / M-series flagship.
- `device_family_fallback` — heuristics on the model identifier for devices released after this rules version.

Both files then look up `(ram_band × soc_class) → tier_id` in `tier_matrix`. Unknown SoC → `budget` (Android) / `entry` (iOS) for safety.

## Tiers

Both platforms share the same 4 output tiers:

| Tier | Approximate fit |
|---|---|
| **low** | Sub-1B models only (0.3-0.6B). Strict RAM ceiling. |
| **mid** | Adds Bonsai-1.7B Q1_0. |
| **high** | Adds SmolLM3-3B class. |
| **flagship** | Adds 3-4B q4 models (phi-4-mini, qwen3.5-4b, ministral-3-3b, qwen3-vl-4b). |

Floors (tok/s on each tier's representative device): **low ≥ 5, mid ≥ 8, high ≥ 10, flagship ≥ 12.** Android uses CPU TG as the floor backend (Hex/GPU may exceed but don't change membership); iOS uses full-Metal TG (`ngl=99`) since Metal is universally available.

The rep device for each tier is in `_selection_policy.rep_devices` inside each rules file. iOS additionally splits flagship into `flagship_A` (A-Pro iPhones, conservative anchor) and `flagship_M` (M-series iPads, ~2× higher TG on 3-4B Q4_K_M).

## Why Android and iOS use different primary quants

- **Android**: Hexagon HTP gives Q4_0 a ~5× PP speedup over Q4_K_M (measured: phi-4-mini-reasoning CPU 47 → Hex 247 PP). Q4_0 also has lower peak load memory. For ≥3B models, Q4_0 is primary; for sub-1B models the gap is within noise and Q4_K_M's marginal quality bump wins.
- **iOS**: Metal does not give Q4_0 a comparable PP fastpath, so Q4_K_M is primary across all tiers. Metal *does* have a real Q1_0 fastpath that Android lacks — Bonsai-8B Q1_0 runs ~4.9× faster on A15 Metal than on A18-Pro-equivalent Android CPU, which is why Bonsai variants are listed as legitimate speed+footprint plays on iOS.

## OS killer margin

- **Android lowmemorykiller**: leave ~25-35% of physical RAM as headroom. Peak load ≥ ~3.5 GB on 8 GiB devices is OOM risk; ≥ ~6 GB on 12 GiB devices is OOM risk. Excludes Bonsai-8B on 6 GiB low-tier (OOM-killed at load on Helio G88) and Qwen3.5-9B Q4_K_M on 8 GiB high tier.
- **iOS jetsam**: 6 GiB iPhone tolerates ~1.7 GB peak (Bonsai-8B Q1_0); 8 GiB iPhone ~3-3.5 GB; M-series iPad 8+ GiB ~6 GB. Used to exclude Qwen3.5-9B Q4_K_M on 8 GiB A-Pro iPhones.

## Provenance and evidence

### Android — 6-device CPU bench campaign

| Codename | Device | SoC | RAM | Tier (empirical) |
|---|---|---|---|---|
| poco-myron | POCO F8 Ultra | Snapdragon 8 Elite Gen 5 / SM8850 | 12 GiB | flagship |
| samsung-s23 | Galaxy S23 | Snapdragon 8 Gen 2 / SM8550 | 8 GiB | high |
| poco-x7-klee | POCO X7 Pro | Dimensity 8400 / MT6886 | 7.5 GiB | high |
| pixel-9-tokay | Pixel 9 | Tensor G4 | 12 GiB | mid (empirically) |
| redmi-aether | Redmi (Helio G88) | MT6769V/CZ | 6 GiB | low |
| oneplus6 | OnePlus 6 | Snapdragon 845 | 6-8 GiB | low |

Key findings from the campaign:

- **Tensor G3/G4/G5 demoted from flagship to mid** — empirical CPU LLM TG ~half of contemporaneous Snapdragon flagship on identical cells.
- **Bonsai Q1_0 1-bit** works in upstream llama.cpp (b9204+) but the kernel is slow-path: PP 5-10× slower than Q4_K_M, TG 2-4× slower. Footprint savings real (Bonsai-8B = 1.16 GB on disk vs ~5 GB for Qwen3-8B Q4_K_M) but speed cost is significant.
- **Bonsai-4B Q1_0** crossed the flagship CPU floor on v1.15.1 / llama.rn 0.12.4 (Myron 9.3 TG, was 7 TG on v1.15.0 — ~33% speedup from upstream Q1_0 kernel work). Added as flagship candidate.
- **Bonsai-8B Q1_0** loads on 8 GiB+ Android (S23 1.71 GiB peak, 2.9 TG) on v1.15.1 — earlier v1.15.0 load-fail was upstream llama.cpp issue #22713. Still below all tier floors → kept excluded.
- **Hex accelerates Q4_0 PP massively** (phi-4-mini-reasoning Q4_0: CPU 47 → Hex 247 PP) but not Q4_K_M. Hex provides no Q1_0 acceleration (and adds dispatch overhead — Q1_0 TG is 5-26% LOWER on Hex than CPU).
- **Adreno OpenCL also has no tuned Q1_0 kernel** — GPU TG loses to CPU on 5 of 6 (model, device) cells on Bonsai. CPU is the recommended Q1_0 backend on Android.
- **S23 RAM ceiling ≈3.5 GB peak load** (8 GiB nominal / ~6.9 reported); Klee similar at 7.5 GiB. Phi-4-mini Q4_K_M borderline.
- **Helio G88 / 6 GiB** can't reliably load 2+ GB peak models — strictly sub-1B for safety.

Models considered and excluded:

- **Bonsai-8B Q1_0** — TG below all tier floors (Myron 4.1, S23 2.9, Klee 3.8, Tokay 2.7, OP6 0.67 on v1.15.1). Loads but too slow.
- **Qwen3.5-9B Q4_K_M** — Myron only (7 TG), below flagship floor.
- **Ministral-3-8B-Reasoning** — Myron only (7 TG).
- **Qwen3-VL-8B-Thinking** — Myron only (7 TG).
- **Gemma-4-E4B** — Myron only at 10-11 TG (just below 12 floor). Reasonable v1.1 candidate if floor relaxed.
- **Phi-4-mini-reasoning Q4_K_M on high tier** — S23 7.2 / Klee 9.1 (below 10 floor); high tier ships Q4_0 only.

#### 2026-11-06 list refresh (rules_version 2026-11-06.1)

In-app e2e harness on the shipped v1.22.0 e2e build (llama.rn 0.13.0-rc.9, llama libs
byte-identical on every phone), CPU backend, pp512/tg128 nr=3, one model per cold launch, each
model twice (forward then reversed order), every cell started at ≤33 °C. Peak = the app's own
VmHWM. Text models: `min_ram_gb` = max VmHWM over all phones (GiB) × 1.20. Vision models (the
harness doesn't load projectors): (LM VmHWM + projector file) × 1.20 + 0.4 GiB for image
encoding — this reproduces the shipped lfm2-vl-1.6b value (2.5). Raw data and driver:
`rd-team/evaluation/benchmarks/2026-11-06-list-refresh/`.

| model (quant) | role | OP6 low ≥5 | Tokay mid ≥8 | S23 high ≥10 | Klee high ≥10 | Myron flag ≥12 | peak GiB |
|---|---|---|---|---|---|---|---|
| qwen3.5-0.8b (Q4_K_M) | **added** low (replaces qwen3-0.6b) | 11.8 | 29.2 | | | | 1.19 |
| qwen3-0.6b (Q4_K_M) | removed (superseded) | 11.2 | | | | | 1.03 |
| lfm2.5-vl-450m (Q4_K_M) | **added** low (first low-tier vision) | 26.8 | | | | | 0.58 |
| bonsai-1.7b (Q1_0) | **removed** Android low | **3.3–3.8** | | | | | 0.82 |
| qwen3.5-2b (Q4_K_M) | **added** Android mid + high (replaces qwen3-1.7b) | | 13.5 | 18.2 | 20.2 | | 2.09 |
| qwen3-1.7b (Q4_K_M) | removed (superseded) | | 14.5 | | | | 1.85 |
| lfm2.5-2.6b (QAD Q4_0) | **added** mid (replaces lfm2-2.6b) | | 12.6 | 18.6 | 20.4 | | 2.19 |
| lfm2-2.6b (Q4_K_M) | removed (vendor-declared successor) | | 11.8 | | | | 2.04 |
| lfm3-1.7b (QAD Q4_0) | **added** mid + high, gated 1.22.0 | | 18.6 | 26.8 | 28.1 | | 1.73 |
| lfm3-vl-2b (QAD Q4_0) | **added** mid + high, gated 1.22.0 (replaces lfm2-vl-1.6b); high speed = lfm3-1.7b (same LM) | | 18.5 | | | | 1.66 |
| lfm2.5-vl-1.6b (Q4_0) | **added** mid, ungated successor of lfm2-vl-1.6b | | 28.2 | | | | 1.21 |
| lfm2-vl-1.6b (Q4_0) | removed (superseded) | | 28.2 | | | | 1.21 |
| lfm2.5-vl-3b (Q4_0) | not added: lfm3-vl-2b is faster on high and scores higher on Liquid's 62-bench suite (57.6 vs 62.1 no-think) | | | 18.8 | 20.5 | | 2.16 |
| tiny-aya-global (Q4_0) | **added** high + flagship (multilingual) | | | 12.5 | 13.2 | 24.0 | 3.02 |
| granite-4.2-3b (Q4_0) | **added** high + flagship | | | 12.0 | 12.6 | 22.5 | 2.66 |
| ling-3.0-tiny (Q4_0, 8B-A1B MoE) | **added** flagship, gated 1.17.2 | | | | | 44.1 | 4.64 |
| bonsai-8b (Q1_0) | **removed** Android mid + high | | **4.5** | **5.7** | **6.8** | | 1.93 |
| bonsai-4b (Q1_0) | **removed** Android high (rep device S23 below floor) | | | **9.7** | 10.8 | | 1.38 |
| smollm3-3b (Q4_0) | kept (mid) | | 9.4 | | | | 2.49 |
| gemma-3-4b / gemma-4-e2b / phi-4-mini (Q4_0) | kept (high) | | | 10.7 / 16.8 / 10.5 | 11.7 / 16.4 / 11.4 | | 3.3–3.7 |
| qwen3.5-4b (Q4_0) | kept flagship-only (S23 still below the high floor) | | | **9.2** | 10.5 | 18.0 | 3.58 |
| gemma-4-e4b / lfm2.5-8b-a1b / ministral-3-3b (Q4_0) | kept (flagship) | | | | | 16.8 / 49.5 / 22.8 | 3.0–5.4 |
| nemotron-3.5-bolt-e4b (Q4_K_M) | not added: 5.8 GiB peak (LMK risk on 12 GiB), 14.5 tg, same niche as gemma-4-e4b | | | | | 14.5 | 5.78 |
| ternary-bonsai-2-2b (Q2_0, native ternary) | **added** mid, gated 1.22.0 (+vision) | | 15.5 | 16.9 | 18.9 | | 1.10 |
| ternary-bonsai-2-4b (Q2_0) | not added: below mid/high floors, flagship only (14.4) with pp ~22 | | 5.6 | 6.7 | 8.1 | 14.4 | 1.82 |
| ternary-bonsai-2-9b (Q2_0) | not added: below every floor | | | 3.6 | 4.4 | 7.1 | 3.15 |

**Ternary-Bonsai-2** (prism-ml, 2026-09-23) is the successor of the removed Android Bonsai
entries. Its Q2_0 files need the `prism.hadamard` runtime: app 1.17.0–1.20.x load them but
produce wrong output, so it is gated 1.22.0 (and in `GATED`). Output was checked on v1.22.0 (iPhone
13 Pro chat: "Paris", "Red, Yellow, Blue" on 2B/4B/9B). On Android CPU prompt processing is slow
(pp512 ~30 tok/s on the 2B vs 65-100 for qwen3.5-2b), so it ships as the last mid alternate.

Also removed: **Spark-X2.5** everywhere (Spark-X2.6 covers the same tiers under the same 1.17.3
gate) and **gemma-3-1b from Android flagship** (to stay within the 10-candidate cap; it stays in
low/mid/high, and lfm2.5-8b-a1b is the fast option on flagship). Gated replacements keep their
ungated predecessor-class model as fallback (lfm2.5-1.2b behind lfm3-1.7b, lfm2.5-vl-1.6b behind
lfm3-vl-2b) while 1.22.0 is new.

**iOS** — iPhone 13 Pro (A15, 6 GiB, the iOS mid rep device), v1.22.0 E2E build, Metal
`ngl=99`, pp512/tg128 nr=3, 2 cold launches each, all full offload, no crash/jetsam. Peak =
process-lifetime `phys_footprint` (the jetsam number, not the harness's `resident_size`).
`min_ram_gb` = peak × 1.15 (README iOS rule); vision adds projector + 0.4 GiB as on Android.
Raw reports: `.../2026-11-06-list-refresh/ios/`.

| model | tg (tok/s) | pp512 | peak phys_footprint MiB | min_ram_gb |
|---|---|---|---|---|
| lfm3-1.7b (QAD Q4_0), gated 1.22.0 | 27.3 | 412 | 1638 | 1.9 |
| lfm3-vl-2b (QAD Q4_0, text), gated 1.22.0 | 27.4 | 427 | 1635 | 2.5 (+projector) |
| lfm2.5-2.6b (Q4_K_M) | 17.3 | 214 | 2246 | 2.6 |
| lfm2.5-vl-1.6b (Q4_0, text) | 40.9 | 620 | 1056 | 2.3 (+projector) |
| qwen3.5-0.8b (Q4_K_M) | 45.5 | 775 | 1156 | 2.0 (+projector) |
| control lfm2-2.6b (Q4_K_M) | 18.7 | 215 | 1906 | 2.2 (shipped 2.3) |
| control qwen3-1.7b (Q4_K_M) | 25.0 | 373 | 1653 | 1.9 (shipped 2.0) |
| ternary-bonsai-2-2b (Q2_0), gated 1.22.0 | 37.6 | 381 | 1059 | 2.0 (+projector) |
| ternary-bonsai-2-4b / 9b (Q2_0) | 17.1 / 7.8 | 144 / 66 | 1835 / 3085 | not added (mid is at the 10 cap; 9B below floor) |
| tiny-aya-global (Q4_K_M), 1 run | 12.5 | 186 | 2814 | 3.2 |
| granite-4.2-3b (Q4_K_M), 1 run | 13.5 | 166 | 2494 | 2.9 |

iOS changes: Spark-X2.5 removed; qwen3-1.7b removed from mid (qwen3.5-2b already there);
qwen3-0.6b → qwen3.5-0.8b (low); lfm2-2.6b → lfm2.5-2.6b, + lfm3-1.7b and lfm3-vl-2b (gated
1.22.0) + lfm2.5-vl-1.6b and ternary-bonsai-2-2b (gated 1.22.0) (mid); + lfm2.5-vl-450m (low), same `lfm2`
arch/projector as the measured VL-1.6B, `min_ram_gb` = Android × 0.92; lfm3-vl-2b also in high (gated 1.22.0). Bonsai stays on iOS
(Metal has the fast Q1_0 path). tiny-aya and granite (high + flagship): `cohere2` / `granite` load
and run fully on Metal on the A15; their high/flagship speed is extrapolated with the chip-class
scaling above (~17-19 tg on A16/A18, ~25-27 on A-Pro), not measured. Ling stays Android-only
(4.6 GB file, not checked on Metal).

### iOS — partial measurement + extrapolation

Bonsai 1.7B / 4B / 8B Q1_0 on **mid** tier is measured on iPhone 13 Pro (A15, 5.6 GiB) via Turso submissions: 51.2 / 33.0 / 19.9 TG with full Metal `ngl=99`. Bonsai on high/flagship is extrapolated by chip-class scaling (×1.4 for A16/A18-base, ×2 for A17 Pro / A18 Pro / A19 / M-series), calibrated against the broader Turso iOS dataset. `qwen3.5-4b q4_k_m` flagship is measured (n=7 Turso flagship rows; A19 Pro 13.4 median, M5 26.3). Non-Bonsai high/flagship candidates use top-TG values from Turso where measured, or extrapolation from the nearest-Android-peer where iOS data is sparse.

Known limitations:

- Only iPhone 13 Pro / A15 has measured Bonsai data on iOS. A13/A14/A16/A17 Pro / A18 / A19 / M-series Bonsai numbers are extrapolated.
- Q4_K_M vs Q4_0 on Metal has not been benchmarked — policy still prefers Q4_K_M but should be revisited.
- iOS Turso submissions show `flash_attn=0` for all iPhone 13 Pro Bonsai entries even though one was actually FA=ON — iOS bench-submit payload appears to drop the flash_attn init setting. Treat `flash_attn` on iOS Turso rows as unreliable.
- Mac (macOS) targets are out of scope.

## Checks (CI)

`tools/check_rules.py` — run `python3 tools/check_rules.py all` before proposing an edit (needs `jsonschema`).

| check | when | what it catches |
|---|---|---|
| `schema` | every push/PR | malformed entries, against `schema/rules.v1.schema.json` / `rules.v2.schema.json` |
| `invariants` | every push/PR | entries the **app would silently drop** (missing `size_bytes`, cross-repo `mmproj`, multimodal without a projector, malformed `min_app_version`), duplicate `(model, quant)`, tier cap, classifier gaps (a `(ram_band × soc_class)` with no `tier_matrix` row), and the **v1 freeze**: a model needing a newer app must never appear in the v1 files |
| `hf` | every push/PR | `size_bytes` / `sha256` that no longer match Hugging Face — repos re-upload under the same name (metadata only, nothing is downloaded) |
| `urls` | nightly | a model, projector or draft file deleted upstream. `ggml-org/gemma-4-E4B-it-GGUF/…Q4_K_M.gguf` was deleted 2026-07-16 and iOS served a 404 for two months |
| `cdn` | nightly | jsDelivr still serving a stale `@main` — a merge reaches users only after a [purge](https://purge.jsdelivr.net/gh/a-ghorbani/pocketpal-device-rules@main/rules.android.v2.json) |

A nightly failure opens (or comments on) an issue labelled `automated-links-check`.

Adding a model on a new GGUF architecture: put it in the v2 files with `min_app_version`, and add it to `GATED` in `tools/check_rules.py` so the v1 freeze is enforced for it.

## Updating the candidate lists

This repo holds **only the data** — no scripts. `rules.android.json` / `rules.ios.json`
**are the source of truth.** Every update is a researched, judged edit to these files,
validated against real community benchmark submissions (PocketPal collects thousands of
on-device runs: device SoC/RAM + measured prefill/token-gen + peak memory). How an update
is prepared is the maintainer's concern and deliberately out of scope here — consumers
should treat these files as curated data and nothing else.

What an update touches:

- **Routine updates** change only `tiers.*.candidates` (model, quant, hf_repo/filename,
  `min_ram_gb` from real p90 peak memory, `obs_tg` from real median token-gen).
- The classifier (`soc_model_to_class`, `cpu_heuristic`, `chip_to_class`, `ram_bands`,
  `tier_matrix`) stays the curated v1 logic and only changes deliberately.

How models are chosen:

- A curated shortlist of modern, llama.cpp-safe models — each with a minimum tier and a
  display ordering — decides what is eligible per tier.
- Community data is the **guardrail**: a model is dropped from a tier if its real median
  token-gen on that tier's devices is below the tier floor, or its p90 peak memory doesn't
  fit the tier's RAM band (with OS-killer headroom). Sub-3-bit *post-training* quants are
  never eligible — but models **trained natively** at low bit-width (BitNet b1.58 / ternary,
  e.g. PrismML Bonsai) ship their native quant (marked `native_low_bit`), since there
  1.58-bit is the format the model was trained in, not lossy compression.
- New models with no submissions yet (e.g. a just-released model) ship as labelled
  alternates with a size-estimated `min_ram`, replaced by real `obs_tg` / measured
  `min_ram` in a later revision once submissions land.

### Curation changes vs the original hand-curated list

- **Lossy sub-3-bit quants removed; native low-bit kept.** Post-training quantization to
  1-2 bit wrecks quality, so it's no longer eligible as a default. But **Bonsai** (PrismML)
  is *trained natively* at 1.58-bit (BitNet b1.58 / ternary) — there 1-bit is the format,
  not lossy compression — so it stays, via the `native_low_bit` flag. It's placed as a
  prominent **alternate** (8B-class at ~1.2GB is a showcase on high/flagship; 240MB/130tg
  Bonsai-1.7B on low), not the silent primary, until we have our own quality eval.
- **Primary is now the best general model per tier**, not the smallest. (v1's cumulative-
  ascending ordering led flagship users with Qwen3-0.6B.)
- **Per-tier minimum model size.** The cumulative cascade no longer reaches all the way
  down: a tier only offers models worth running on a device that capable. `low` keeps the
  ultralights (350M/0.6B/1B); `mid`/`high`/`flagship` floor at 1B, so a 6-8GB phone is no
  longer suggested a 350M model. Keeps one fast/light 1B option without the sub-1B clutter.
- **Filled the mid "sweet spot."** `mid` spans 4-6GB phones (memory cap ~4.7GB), where a
  4B doesn't fit, so it's served by 1.5-3B models. Added **Qwen3-1.7B** (fast text),
  **Qwen3.5-2B** (multimodal), and the recent **LFM2.5-1.2B** / **LFM2-2.6B** (Liquid AI,
  on-device-first hybrid arch) so mid is a real, current selection — not just one 3B + one 1B.
  (Qwen3.5-2B is dropped from Android mid on memory — p90 peak 5.0GB > cap — but kept on iOS.)
- **Max 10 candidates per tier** (v2 schema limit; ~6 remains the practical target). A cap, not a target: short tiers stay
  short (never padded with models that don't fit). The trim is diversity-preserving — it
  always keeps the top-ranked models (incl. the primary) plus a guaranteed fast/light
  option and a multimodal option, instead of truncating those (which live at the list tail).
- **Gemma added** — Gemma 3 1B/4B and **Gemma 4 E2B/E4B** (now data-validated: E4B ~6.5,
  E2B ~9 tg on flagship Android — supersedes the v1 "below the 12-tg floor" exclusion).
- **LFM2.5-8B-A1B added (flagship-only alternate).** Liquid AI's on-device Mixture-of-Experts
  (2026-05-28): 8.3B total / ~1.5B active per token (32 experts, top-4), so it decodes at
  ~1.5B cost (~30 tg on phones) while vendor-claiming 3–4B-class quality. All experts stay
  resident, so memory is 8B-class — **measured on-device 6.2 GB (iOS) / 6.3 GB (Android)**
  (MODELRULES-2). **Flagship-only, not high** — 6.3 GB would never fit an 8 GB high-tier
  device once OS + app overhead is counted. Runtime verified: PocketPal's `llama.rn` 0.12.4
  bundles the `lfm2moe` arch. lfm1.0 license (same as our shipped dense LFM2). Shipped as an
  **alternate, not primary** — quality is still vendor-claimed pending our own eval (MODELRULES-6).
- **Empirical finding:** Qwen3.5-4B is dropped on Android (real median tg 3.9–4.3 — the
  gated-DeltaNet path is slow on CPU); it stays on iOS/Metal where it measures ~10–13 tg.
- **iOS uses Q4_K_M** (Metal, no Hexagon) rather than inheriting the Android Q4_0 policy.
- **`min_ram_gb` is per-model AND per-platform**, from the team's measured bench
  (FOU-99 + min-ram-smoke, 225 cells, MODELRULES-2). Peak memory is a
  property of the model, not the tier — v1 showed the *same* model with different RAM per
  tier (gemma-3-4b 4.5 in high / 2.9 in flagship). It IS platform-specific though: measured
  peak → **iOS = p90 × 1.15** (jetsam), **Android = p90 × 1.25** (lowmemorykiller is more
  aggressive), so Android sits ~0.2–0.6 GB higher. Measured values also corrected systematic
  estimate errors in both directions (qwen3-0.6b est 1.8 → 1.2/1.3 measured; smollm3-3b est
  2.4 → 2.8/3.1; Bonsai file+KV not file-size; Gemma/Mistral compute buffer). The community
  peak telemetry can't supply this — see the data-source note below. **All shipped models
  are now measured** (phi-4-mini-instruct 4.1; smoke-2 run, MODELRULES-2).
- **Gemma 3n removed (2026-06-09).** Its only draw was on-device audio+vision, but no usable
  quantized mmproj exists (E2B: none on HF; E4B: only a third-party F16, +2 GB), so in
  PocketPal it was effectively text-only — and as text it's strictly superseded by the
  gemma-4 E-series (newer, same niche, *with* working GGUF projectors: unsloth/ggml-org ship
  `mmproj-*`), which we already ship on high/flagship. Carrying it as a stale text-only
  alternate added clutter and no value, so it was dropped rather than kept. (Our multimodal
  picks — gemma-4 E2B/E4B, Qwen3-VL-4B, Qwen3.5 — all have verified GGUF projectors.)

> **Data sources.** `obs_tg` (token-gen) comes from community benchmark submissions. The
> canonical store is **Turso** (~9k rows, current, includes Bonsai). Its `peak_memory_usage`
> column was **corrupted** (stored as the literal `"[object Object]"` from a `JSON.stringify`
> bug) — **fixed + backfilled 2026-06-10**, so it now carries numeric byte values. But the
> raw community peak is noisy (failed-load and pressure-inflated outliers, free-text model
> names) and miscalibrated against the hand-measured bench (overshoots Android, undershoots
> iOS), so `min_ram_gb` **still uses hand-measured values**; community peak is a cross-check
> / coverage-extender pending re-calibration. Bonsai's `obs_tg` is seeded from Turso's clean
> `tg_avg` (absent from the older Firestore
> CSV), **platform-specific** — the 1-bit kernel is slow on Android CPU but fast on
> iOS/Metal (Bonsai-8B ≈ 3.6 tg Android vs ≈ 20 tg on an iPhone 13 Pro), so a single
> number would be wrong on one platform. Re-calibrating the community peak and sourcing
> `obs_tg` refreshes directly from Turso (it lacks the SoC/CPU columns the Android
> classifier needs) are the two open data-plumbing follow-ups.

## v2 follow-ups

- **iOS**: Ling-3.0-tiny (not checked on Metal); tiny-aya / granite high+flagship speeds are
  extrapolated. The vision projectors are not exercised by the bench on either platform.
- **Ternary-Bonsai-2-4B on iOS** (17.1 tg on A15, clears mid/high) once a tier has room; retire
  the iOS Q1_0 Bonsai entries in favour of Bonsai-2 when 1.22.0 is widely adopted.
- **Stale `obs_tg`** on entries kept from the community data (e.g. Android gemma-4-e4b 6.5 vs
  16.8 measured on Myron, gemma-3-4b high 6.6 vs 10.7-11.7). Not touched in this refresh.
- Vision `min_ram_gb` is estimated from LM peak + projector size; measure with an image loaded
  once the e2e harness can load a projector.
- **Calibrate + integrate community peak telemetry.** The `peak_memory_usage` ingestion bug
  is fixed + backfilled (2026-06-10), but the raw data is noisy and miscalibrated vs the
  hand-measured bench (overshoots Android ~+0.5–1.9 GB, undershoots iOS ~0.5–1.3 GB). Before
  `min_ram_gb` can be data-derived again, re-fit the per-platform multiplier against the
  measured set and add garbage-filtering + canonical name/quant matching.
- **Source `obs_tg` directly from Turso** (the canonical, current store with Bonsai) — needs
  a device→SoC mapping since Turso lacks the CPU-feature columns the Android classifier uses;
  gives every model real per-tier `obs_tg` (incl. Bonsai) instead of seeded fallbacks.
- Add a **quality floor** to gating (reuse the GEval/Grok judge harness): a model can't be
  a default unless it clears a small fixed-prompt quality bar at its shipped quant. This is
  what makes dropping 1-bit principled rather than a judgement call.
- iOS flagship tier is sparsely sampled (most iPhone 16 Pro = 8 GiB → `high`, not
  `flagship`); several iOS flagship entries are size-estimated pending submissions.
- Re-bench with Gemma-4-E4B unlocked (right at the floor).
- Backend-aware perf-hints overlay: Hex Q4_0 acceleration is a real perf win on phi-4-mini-reasoning that we leave on the table in CPU-floor v1.
- 8B model coverage: future Bonsai versions with an optimized Q1_0 kernel might unlock flagship 8B-class recommendations.
- Direct Tensor G2/G3 re-bench on a Pixel 7-class device to confirm the mid-tier rep numbers (currently inherited from the Pixel 9 / Tokay measurements as an upper-bound proxy).
- Capture community Bonsai bench on each iOS chip class — even single submissions per device would replace extrapolated `obs_tg_estimate` with measured `obs_tg`.
- Investigate iOS bench-submit payload for the `flash_attn` field.
- Decide Metal Q4_0 vs Q4_K_M primary from real iOS bench.

## Versioning

- `schema_version` — schema shape. Bumps mean the app may need a code change to consume.
- `rules_version` — data-only revision. Bumps reflect new bench data, model additions, or classifier table updates. Safe to consume without app changes.

## Linear

- Tracking ticket: [FOU-99](https://linear.app/pocketpal/issue/FOU-99) — device-class → model tier lookup.
- Downstream consumers: FOU-92 (recommended-model UX), FOU-100 (Pal `compatible_models`).
