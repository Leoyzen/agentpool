"""Rendering utilities for tree-based output.

This module contains helper functions for rendering tree structures and
working with filesystem entries.
"""

from __future__ import annotations

import colorsys
import random


def get_random_color() -> str:
    """Generate a random pastel color.

    Returns:
        Hex color string like '#a3b5c7'
    """
    hue = random.random()
    r, g, b = (int(x * 255) for x in colorsys.hsv_to_rgb(hue, 1, 0.75))
    return f"#{r:02x}{g:02x}{b:02x}"
