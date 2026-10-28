"""
@file_name: visual.py
@author:
@date: 2026-09-24
@description: Fixed viewport inspection and screenshot-coordinate mapping for visual tools.

``browser_look`` captures what the model should see; this module owns the
page-side geometry probe, the output-resolution budget, PNG validation and
the observation that maps image pixels back to viewport CSS pixels.
"""
from __future__ import annotations

import base64
import json
import math
import struct
from dataclasses import dataclass
from typing import Any
from narranexus.platform.browser._browser_impl.limits import check_selector

#: Output budget. Vision providers downscale past roughly these bounds
#: (Anthropic: 1568px long edge / ~1.15MP; OpenAI high detail fits a
#: 768px short side), so capturing more only costs bandwidth and memory.
MAX_IMAGE_EDGE = 1568
MAX_IMAGE_AREA = 1_150_000
#: Sanity bounds on what Chrome returns (rounding slack over the budget).
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 2 * MAX_IMAGE_AREA

_VIEW_FUNCTION = r"""
(({selector, region}) => {
  const viewport = {width: innerWidth, height: innerHeight, scroll_x: scrollX,
    scroll_y: scrollY, dpr: devicePixelRatio};
  let rect = region || {x: 0, y: 0, width: innerWidth, height: innerHeight};
  if (selector !== null) {
    const elements = document.querySelectorAll(selector);
    if (elements.length !== 1) return {error: 'Choose a selector matching exactly one visible element'};
    const element = elements[0];
    if (!element.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}))
      return {error: 'Element is hidden; scroll it into view before browser_look'};
    const box = element.getBoundingClientRect();
    const x = Math.max(0, box.left), y = Math.max(0, box.top);
    rect = {x, y, width: Math.min(innerWidth, box.right) - x,
      height: Math.min(innerHeight, box.bottom) - y};
  }
  if (rect.width <= 0 || rect.height <= 0 || rect.x < 0 || rect.y < 0 ||
      rect.x + rect.width > innerWidth || rect.y + rect.height > innerHeight)
    return {error: 'Capture region is outside the viewport; scroll and call browser_look again'};
  return {url: location.href, title: document.title, document_id: performance.timeOrigin,
    viewport, region: rect};
})
"""


def view_expression(*, selector: str | None = None, x: float | None = None,
                    y: float | None = None, width: float | None = None,
                    height: float | None = None, scale: float = 1) -> str:
    if not finite_number(scale) or not 1 <= scale <= 3:
        raise ValueError("Screenshot scale must be between 1 and 3")
    check_selector(selector)
    values = (x, y, width, height)
    region = None
    if any(value is not None for value in values):
        if selector is not None or not all(finite_number(value) for value in values):
            raise ValueError("Supply either selector or all of x, y, width and height")
        assert x is not None and y is not None and width is not None and height is not None
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("Screenshot region must have nonnegative origin and positive dimensions")
        region = dict(zip(("x", "y", "width", "height"), values))
    args = json.dumps({"selector": selector, "region": region}, allow_nan=False)
    return f"{_VIEW_FUNCTION.strip()}({args})"


def capture_scale(region: dict, dpr: float, requested: float) -> float:
    """CDP ``clip.scale`` for a region within the output budget.

    Chrome emits ``css_size * clip.scale * devicePixelRatio`` pixels (measured
    against a real headless Chrome), so the requested magnification applies
    on top of the device ratio and is then reduced to fit the budget. The
    caller reads the real PNG size back, so coordinate mapping never depends
    on this arithmetic being exact.
    """
    width, height = region["width"], region["height"]
    output = min(
        requested * dpr,
        MAX_IMAGE_EDGE / max(width, height),
        math.sqrt(MAX_IMAGE_AREA / (width * height)),
    )
    return output / dpr


def finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def png_dimensions(data: str) -> tuple[int, int]:
    if not isinstance(data, str) or len(data) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
        raise ValueError("Screenshot is too large; use browser_look with a smaller region")
    raw = base64.b64decode(data, validate=True)
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        raise ValueError("Browser returned an invalid PNG screenshot")
    width, height = struct.unpack(">II", raw[16:24])
    if width == 0 or height == 0 or width * height > MAX_IMAGE_PIXELS:
        raise ValueError("Screenshot dimensions are too large; capture a smaller region")
    return width, height


@dataclass(frozen=True)
class VisualObservation:
    id: str
    page_id: str
    scope: tuple[str, str]
    state: dict
    width: int
    height: int

    def matches(self, state: dict, page_id: str, scope: tuple[str, str]) -> bool:
        return self.page_id == page_id and self.scope == scope and all(
            self.state[key] == state.get(key) for key in ("url", "document_id", "viewport")
        )

    def point(self, x: float | None, y: float | None) -> tuple[float, float]:
        if not finite_number(x) or not finite_number(y):
            raise ValueError("Visual actions require finite image x and y coordinates")
        assert x is not None and y is not None
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError("Coordinates are outside the observed image")
        region = self.state["region"]
        return (region["x"] + x * region["width"] / self.width,
                region["y"] + y * region["height"] / self.height)
