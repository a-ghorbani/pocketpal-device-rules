# pocketpal-device-rules

Device-tier classification rules + per-tier model recommendations for [PocketPal AI](https://github.com/a-ghorbani/pocketpal-ai).

The rules let the app pick sensible default models for the user's phone without bundling them in the app binary. Hosted on GitHub, served free via jsDelivr.

## Files

| File | URL | Platform |
|---|---|---|
| `rules.android.json` | https://cdn.jsdelivr.net/gh/a-ghorbani/pocketpal-device-rules@main/rules.android.json | Android |
| `rules.ios.json` | https://cdn.jsdelivr.net/gh/a-ghorbani/pocketpal-device-rules@main/rules.ios.json | iOS / iPadOS |

The app reads the file matching the current OS. Each file is independent (own classifier, own tier matrix, own candidate list) because the device-classification signals and the accelerated-backend characteristics differ enough that a shared schema would force unnatural compromises.

## How the app uses it

1. On startup (or first model picker open), fetch `rules.<platform>.json` and cache it.
2. Read the device signals the platform exposes:
   - **Android**: `Build.SOC_MODEL` (API 31+), `Build.HARDWARE`, `/proc/cpuinfo` features (`i8mm`, `sve2`, `dotprod`), big-core max frequency, `MemTotal`.
   - **iOS**: `utsname.machine` (e.g. `iPhone16,1`), `NSProcessInfo.processInfo.physicalMemory`.
3. Run the classifier in the rules file to compute a `soc_class`, look up the `(ram_band × soc_class) → tier`, and present that tier's candidate list.
4. The candidate list is **ordered**: the first entry per `model_id` is the primary recommendation. Lower entries are alternates (different quant, different model).

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

### iOS — partial measurement + extrapolation

Bonsai 1.7B / 4B / 8B Q1_0 on **mid** tier is measured on iPhone 13 Pro (A15, 5.6 GiB) via Turso submissions: 51.2 / 33.0 / 19.9 TG with full Metal `ngl=99`. Bonsai on high/flagship is extrapolated by chip-class scaling (×1.4 for A16/A18-base, ×2 for A17 Pro / A18 Pro / A19 / M-series), calibrated against the broader Turso iOS dataset. `qwen3.5-4b q4_k_m` flagship is measured (n=7 Turso flagship rows; A19 Pro 13.4 median, M5 26.3). Non-Bonsai high/flagship candidates use top-TG values from Turso where measured, or extrapolation from the nearest-Android-peer where iOS data is sparse.

Known limitations:

- Only iPhone 13 Pro / A15 has measured Bonsai data on iOS. A13/A14/A16/A17 Pro / A18 / A19 / M-series Bonsai numbers are extrapolated.
- Q4_K_M vs Q4_0 on Metal has not been benchmarked — policy still prefers Q4_K_M but should be revisited.
- iOS Turso submissions show `flash_attn=0` for all iPhone 13 Pro Bonsai entries even though one was actually FA=ON — iOS bench-submit payload appears to drop the flash_attn init setting. Treat `flash_attn` on iOS Turso rows as unreliable.
- Mac (macOS) targets are out of scope.

## Updating the candidate lists

This repo holds **only the generated data artifacts** — no scripts. The per-tier
candidate lists are **generated from real community benchmark submissions**, not
hand-curated. PocketPal collects thousands of on-device benchmark runs (device
SoC/RAM + measured prefill/token-gen + peak memory); the generator turns that data
into the `tiers.*.candidates` blocks. **"Update the list" = re-run the generator.**

The generator lives in the advisory tooling (next to the data fetcher), not here:

```bash
# in the founder-advisory-board repo:
python3 tools/fetch-benchmarks.py --refresh                              # refresh community data
python3 tools/gen-device-rules.py --rules-dir ~/codes/pocketpal-device-rules            # writes the JSON
python3 tools/gen-device-rules.py --rules-dir ~/codes/pocketpal-device-rules --dry-run  # preview only
```

What it does and does not touch:

- **Regenerates** only `tiers.*.candidates` (model, quant, hf_repo/filename, `min_ram_gb`
  from real p90 peak memory, `obs_tg` from real median token-gen).
- **Never touches** the classifier (`soc_model_to_class`, `cpu_heuristic`, `chip_to_class`,
  `ram_bands`, `tier_matrix`) — that stays the curated v1 logic.

How models are chosen:

- `MODEL_REGISTRY` (top of the generator) is the single curated knob: the modern,
  llama.cpp-safe shortlist + each model's `min_tier` and ordering `rank`. Add a model →
  re-run → community data validates it.
- Community data is the **guardrail**: a model is dropped from a tier if its real median
  token-gen on that tier's devices is below `MIN_TG`, or its p90 peak memory doesn't fit
  the tier's RAM band (with OS-killer headroom). Sub-3-bit *post-training* quants are never
  eligible — but models **trained natively** at low bit-width (BitNet b1.58 / ternary, e.g.
  PrismML Bonsai) opt in via `native_low_bit` and ship their native quant, since there 1.58-bit
  is the format the model was trained in, not lossy compression.
- New models with no submissions yet (e.g. a just-released model) ship as labelled
  alternates with a size-estimated `min_ram` and **auto-promote** to real `obs_tg` /
  measured `min_ram` on the next run once submissions land.

### v2 changes vs the hand-curated v1

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
- **Max ~6 candidates per tier** (`MAX_PER_TIER`). A cap, not a target: short tiers stay
  short (never padded with models that don't fit). The trim is diversity-preserving — it
  always keeps the top-ranked models (incl. the primary) plus a guaranteed fast/light
  option and a multimodal option, instead of truncating those (which live at the list tail).
- **Gemma added** — Gemma 3 1B/4B, Gemma 3n E2B/E4B, and **Gemma 4 E2B/E4B** (now
  data-validated: E4B ~6.5, E2B ~9 tg on flagship Android — supersedes the v1 "below the
  12-tg floor" exclusion).
- **Empirical finding:** Qwen3.5-4B is dropped on Android (real median tg 3.9–4.3 — the
  gated-DeltaNet path is slow on CPU); it stays on iOS/Metal where it measures ~10–13 tg.
- **iOS uses Q4_K_M** (Metal, no Hexagon) rather than inheriting the Android Q4_0 policy.
- **`min_ram_gb` is per-model AND per-platform**, from the team's measured bench
  (`MEASURED_MIN_RAM`; FOU-99 + min-ram-smoke, 225 cells, MODELRULES-2). Peak memory is a
  property of the model, not the tier — v1 showed the *same* model with different RAM per
  tier (gemma-3-4b 4.5 in high / 2.9 in flagship). It IS platform-specific though: measured
  peak → **iOS = p90 × 1.15** (jetsam), **Android = p90 × 1.25** (lowmemorykiller is more
  aggressive), so Android sits ~0.2–0.6 GB higher. Measured values also corrected systematic
  estimate errors in both directions (qwen3-0.6b est 1.8 → 1.2/1.3 measured; smollm3-3b est
  2.4 → 2.8/3.1; Bonsai file+KV not file-size; Gemma/Mistral compute buffer). The community
  peak telemetry can't supply this — see the data-source note below. (Still estimated,
  pending measurement: phi-4-mini-instruct, gemma-3n-e2b/e4b.)

> **Data sources & a known bug.** `obs_tg` (token-gen) comes from community benchmark
> submissions. The canonical store is **Turso** (~9k rows, current, includes Bonsai), but
> its `peak_memory_usage` column is **corrupted** — stored as the literal string
> `"[object Object]"` for ~all rows (a `JSON.stringify` bug in the submission write path),
> so memory CANNOT be derived from it. That's why `min_ram_gb` uses hand-measured values.
> Bonsai's `obs_tg` is seeded from Turso's clean `tg_avg` (absent from the older Firestore
> CSV), **platform-specific** — the 1-bit kernel is slow on Android CPU but fast on
> iOS/Metal (Bonsai-8B ≈ 3.6 tg Android vs ≈ 20 tg on an iPhone 13 Pro), so a single
> number would be wrong on one platform. Fixing the ingestion bug + pointing the
> generator at Turso (it lacks the SoC/CPU columns the Android classifier needs) are the
> two open data-plumbing follow-ups.

## v2 follow-ups

- **Fix the `peak_memory_usage` ingestion bug** (Turso stores `"[object Object]"` instead
  of the JSON) so community peak telemetry becomes usable and `min_ram_gb` can be
  data-derived again instead of hand-measured.
- **Wire the generator to Turso** (the canonical, current store with Bonsai) — needs a
  device→SoC mapping since Turso lacks the CPU-feature columns the Android classifier uses,
  and gives every model real per-tier `obs_tg` (incl. Bonsai) instead of seeded fallbacks.
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
