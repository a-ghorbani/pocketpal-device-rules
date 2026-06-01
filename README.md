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

## Tiers

Both platforms share the same 4 output tiers:

| Tier | Approximate fit |
|---|---|
| **low** | Sub-1B models only (0.3-0.6B). Strict RAM ceiling. |
| **mid** | Adds Bonsai-1.7B Q1_0. |
| **high** | Adds SmolLM3-3B class. |
| **flagship** | Adds 3-4B q4 models (phi-4-mini, qwen3.5-4b, ministral-3-3b, qwen3-vl-4b). |

Floors (CPU TG, tok/s on each tier's representative device): low ≥ 5, mid ≥ 8, high ≥ 10, flagship ≥ 12.

## Why Android and iOS use different primary quants

- **Android**: Hexagon HTP gives Q4_0 a ~5× PP speedup over Q4_K_M (measured: phi-4-mini-reasoning CPU 47 → Hex 247 PP). Q4_0 also has lower peak load memory. For ≥3B models, Q4_0 is primary; for sub-1B models the gap is within noise and Q4_K_M's marginal quality bump wins.
- **iOS**: Metal does not give Q4_0 a comparable fastpath, so Q4_K_M is primary across all tiers. (To be verified by an iOS bench campaign — see `_status` in `rules.ios.json`.)

## Provenance and evidence

- Android candidates derived from a 6-device bench campaign: POCO F8 Ultra (SD 8 Elite Gen 5), Galaxy S23 (SD 8 Gen 2), POCO X7 Pro (Dimensity 8400), Pixel 9 (Tensor G4), Redmi Helio G88, OnePlus 6 (SD 845). Full report: [`findings.md`](https://github.com/a-ghorbani/pocketpal-dev-team/blob/main/) (internal).
- iOS candidates are v1 estimates extrapolated from Android equivalents (see `_status` and `_extrapolation_notes` in `rules.ios.json`).

## Versioning

- `schema_version` — schema shape. Bumps mean the app may need a code change to consume.
- `rules_version` — data-only revision. Bumps reflect new bench data, model additions, or classifier table updates. Safe to consume without app changes.

## Linear

- Tracking ticket: [FOU-99](https://linear.app/pocketpal/issue/FOU-99) — device-class → model tier lookup.
- Downstream consumers: FOU-92 (recommended-model UX), FOU-100 (Pal `compatible_models`).
