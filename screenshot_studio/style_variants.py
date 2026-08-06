"""Named style presets, expressed as StyleConfig field overrides applied on top of
whatever base style a config already defines. Shared by `style-preview` (renders all
of them per shot for comparison) and `style-pick` (records which one a shot should
actually use, read back by `compose`)."""

from __future__ import annotations

VARIANTS: dict[str, dict] = {
    "minimal": {},  # config's own style, unchanged
    "gradient": {"background_mode": "gradient"},
    "tilted": {"layout": "tilted"},
    "playful": {"background_mode": "solid", "decoration": "shapes"},
}
