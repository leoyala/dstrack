"""Regenerate docs/assets/dstrack-logo.svg and .png.

Renders the rest pose of the (corrected) slab geometry from
docs/javascripts/logo-animation.js at 1x and emits:
  - the SVG as per-row run-length rects (same format as the original),
  - the PNG at 17x on a 510x510 transparent canvas, art centered.
"""

import math
import struct
import zlib
from typing import TypedDict

# Grid and projection constants, in logo pixel units. They must stay in sync
# with the ones in logo-animation.js, or the animation's first frame will not
# match the SVG this script emits.
GRID_W, GRID_H = 24, 29
CENTER_X, RADIUS, ISO_Y, THICKNESS = 12, 11, 0.5, 2
EPS = 1e-9
COS45 = math.sqrt(0.5)

RGB = tuple[int, int, int]
#: The rendered artwork: one row per logo pixel, `None` where nothing is drawn.
Grid = list[list[RGB | None]]


class Layer(TypedDict):
    """One slab: where its top face sits vertically, and its palette."""

    cy: float
    top: RGB
    left: RGB
    right: RGB
    edge: RGB


class Edge(TypedDict):
    """One segment of a slab's plan-view outline.

    `a`/`b` index into the outline points, `normal` is the outward normal at
    rest, `seam` marks the dark corner tips, and `base` is the rest normal of
    the smooth square face the segment belongs to.
    """

    a: int
    b: int
    normal: float
    seam: bool
    base: float


def hexc(s: str) -> RGB:
    """Parse a "#rrggbb" color string.

    Args:
        s: A hex color, leading "#" included.

    Returns:
        The red, green and blue channels, each 0-255.
    """
    return (int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16))


# One entry per slab, top to bottom: vertical center of the top face (screen
# units) and the palette taken verbatim from the SVG logo. `left`/`right` shade
# the side faces; `edge` is the dark seam painted on the corner tips.
LAYERS: list[Layer] = [
    Layer(
        cy=6.5,
        top=hexc("#a99cff"),
        left=hexc("#7060e0"),
        right=hexc("#4a3cc0"),
        edge=hexc("#2f2490"),
    ),
    Layer(
        cy=13.5,
        top=hexc("#8b7bf0"),
        left=hexc("#5b4bd6"),
        right=hexc("#3a2fa6"),
        edge=hexc("#241b6b"),
    ),
    Layer(
        cy=20.5,
        top=hexc("#6a5be0"),
        left=hexc("#463aae"),
        right=hexc("#2c2185"),
        edge=hexc("#1d1568"),
    ),
]


def mix(a: RGB, b: RGB, t: float) -> RGB:
    """Linearly interpolate between two colors.

    Args:
        a: The color returned at `t` = 0.
        b: The color returned at `t` = 1.
        t: The blend position, expected in [0, 1] (not clamped here).

    Returns:
        The blended color, rounded per channel.
    """
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def build_outline() -> tuple[list[float], list[float], list[Edge], int]:
    """Build the top-face outline of a slab in plan view.

    The outline is a 45deg-rotated square whose edges are staircases of 2x2
    plan-unit steps, walked as a closed loop: right side bottom-to-top, then
    left side top-to-bottom. Every vertex lands on a logo pixel boundary, which
    is what lets the rendered result reproduce the SVG exactly.

    Returns:
        The outline point coordinates `px` and `py`, the `edges` connecting
        them in order, and the point count `n`.
    """
    pts: list[tuple[float, float]] = []
    for dy in range(-5, 6):
        hw = RADIUS - 2 * abs(dy)
        pts += [(hw, dy - 0.5), (hw, dy + 0.5)]
    for dy in range(5, -6, -1):
        hw = RADIUS - 2 * abs(dy)
        pts += [(-hw, dy + 0.5), (-hw, dy - 0.5)]
    n = len(pts)
    px = [p[0] for p in pts]
    py = [p[1] / ISO_Y for p in pts]  # undo the isometric foreshortening
    edges: list[Edge] = []
    for i in range(n):
        j = (i + 1) % n
        mx, my = (px[i] + px[j]) / 2, (py[i] + py[j]) / 2
        nx, ny = py[j] - py[i], -(px[j] - px[i])
        # The outline is convex about the origin, so the normal pointing away
        # from the center is the outward one.
        if nx * mx + ny * my < 0:
            nx, ny = -nx, -ny
        # Corner tips sit exactly on a plan axis; everything else belongs to
        # the face whose rest normal is the nearest odd multiple of 45deg.
        mid = math.atan2(my, mx)
        quadrant = mid / (math.pi / 2)
        seam = abs(quadrant - round(quadrant)) < 1e-6
        m = mid % (2 * math.pi)
        base = math.floor(m / (math.pi / 2)) * (math.pi / 2) + math.pi / 4
        edges.append(Edge(a=i, b=j, normal=math.atan2(ny, nx), seam=seam, base=base))
    return px, py, edges, n


OUT_PX, OUT_PY, OUT_EDGES, OUT_N = build_outline()


def shade(layer: Layer, normal_angle: float) -> RGB:
    """Pick the side-face color for a face pointing in a given direction.

    Fixed light from the left: darkest when a face points right (rest angle
    45deg), lightest when it points left (135deg), clamped beyond, so the
    resting pose uses the exact logo colors.

    Args:
        layer: The slab being drawn, supplying the palette to blend.
        normal_angle: The face's current outward normal, in radians.

    Returns:
        The blend of the layer's right and left face colors for that angle.
    """
    t = min(1, max(0, (COS45 - math.cos(normal_angle)) / (2 * COS45)))
    return mix(layer["right"], layer["left"], t)


def fill_polygon(grid: Grid, xs: list[float], ys: list[float], rgb: RGB) -> None:
    """Paint a solid color over a (possibly non-convex) polygon.

    Even-odd scanline fill in grid coordinates: a cell is filled when its
    center lies inside the polygon. Later fills overwrite earlier ones.

    Args:
        grid: The target grid, modified in place.
        xs: The polygon vertices' horizontal coordinates.
        ys: The polygon vertices' vertical coordinates, paired with `xs`.
        rgb: The color to paint.
    """
    count = len(xs)
    r0 = max(0, math.ceil(min(ys) - 0.5))
    r1 = min(GRID_H - 1, math.floor(max(ys) - 0.5))
    for r in range(r0, r1 + 1):
        yc = r + 0.5
        # Collect where the scanline crosses each edge; sorted pairs of
        # crossings bracket the spans that are inside the polygon.
        crossings: list[float] = []
        for i in range(count):
            j = (i + 1) % count
            y1, y2 = ys[i], ys[j]
            if (y1 <= yc) == (y2 <= yc):
                continue
            crossings.append(xs[i] + ((yc - y1) / (y2 - y1)) * (xs[j] - xs[i]))
        crossings.sort()
        for k in range(0, len(crossings) - 1, 2):
            c0 = max(0, math.ceil(crossings[k] - 0.5))
            c1 = min(GRID_W - 1, math.floor(crossings[k + 1] - 0.5))
            for c in range(c0, c1 + 1):
                grid[r][c] = rgb


def draw_layer(grid: Grid, layer: Layer, angle: float) -> None:
    """Rasterize one slab, side faces then top face.

    Args:
        grid: The target grid, modified in place.
        layer: The slab to draw, supplying its height and palette.
        angle: The slab's rotation in the plan plane, in radians.
    """
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    # Rotate the outline in plan view, then project to screen: x straight
    # across, y foreshortened onto the top face, extruded down by THICKNESS.
    sx: list[float] = []
    syt: list[float] = []
    syb: list[float] = []
    for i in range(OUT_N):
        x = OUT_PX[i] * cos_a - OUT_PY[i] * sin_a
        y = OUT_PX[i] * sin_a + OUT_PY[i] * cos_a
        sx.append(CENTER_X + x)
        syt.append(layer["cy"] + y * ISO_Y)
        syb.append(syt[-1] + THICKNESS)
    # Side faces: every outline edge whose outward normal currently points
    # toward the viewer. Shaded faces first, dark seams second so they stay
    # visible at the corners, top face last so it owns the shared boundary
    # pixels and overpaints any far-side step that slipped through.
    for seam_pass in (False, True):
        for e in OUT_EDGES:
            if e["seam"] != seam_pass:
                continue
            if math.sin(e["normal"] + angle) <= EPS:
                continue
            a, b = e["a"], e["b"]
            rgb = layer["edge"] if e["seam"] else shade(layer, e["base"] + angle)
            fill_polygon(
                grid,
                [sx[a], sx[b], sx[b], sx[a]],
                [syt[a], syt[b], syb[b], syb[a]],
                rgb,
            )
    fill_polygon(grid, sx, syt, layer["top"])


def render_rest() -> Grid:
    """Render the unrotated pose of the whole stack.

    Bottom slab first, so the ones above paint over it where they overlap.

    Returns:
        A GRID_H x GRID_W grid holding the drawn colors.
    """
    grid: Grid = [[None] * GRID_W for _ in range(GRID_H)]
    for i in range(len(LAYERS) - 1, -1, -1):
        draw_layer(grid, LAYERS[i], 0.0)
    return grid


def to_hex(rgb: RGB) -> str:
    """Format a color for SVG.

    Args:
        rgb: The channel values to format.

    Returns:
        The color as a "#rrggbb" string.
    """
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def emit_svg(grid: Grid, path: str, unit: int = 16) -> int:
    """Write the grid out as an SVG.

    One rect per run of same-colored cells in a row (the format the original
    hand-written logo used), on a viewBox of one unit per logo pixel.

    Args:
        grid: The rendered artwork to serialize.
        path: Where to write the SVG.
        unit: Rendered pixels per logo pixel, setting the SVG's width/height.

    Returns:
        The number of rects emitted.
    """
    rects: list[str] = []
    for r in range(GRID_H):
        c = 0
        while c < GRID_W:
            if grid[r][c] is None:
                c += 1
                continue
            # Extend the run as far as the color holds, then emit it as one rect.
            start, color = c, grid[r][c]
            while c < GRID_W and grid[r][c] == color:
                c += 1
            rects.append(
                f'<rect x="{start}" y="{r}" width="{c - start}" height="1" fill="{to_hex(color)}"></rect>'
            )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID_W} {GRID_H}" '
        f'width="{GRID_W * unit}" height="{GRID_H * unit}" shape-rendering="crispEdges">\n'
        "<title>dstrack - logo</title>\n" + "".join(rects) + "\n</svg>\n"
    )
    with open(path, "w") as f:
        f.write(svg)
    return len(rects)


def emit_png(grid: Grid, path: str, size: int = 510, scale: int = 17) -> None:
    """Write the grid out as a square, nearest-neighbor upscaled PNG.

    Hand-rolled RGBA PNG writer (no image dependency): the art is centered on a
    transparent canvas and every cell becomes one solid block, so the pixel art
    stays crisp.

    Args:
        grid: The rendered artwork to serialize.
        path: Where to write the PNG.
        size: The canvas edge length, in output pixels.
        scale: Output pixels per logo pixel; the art must fit within `size`.
    """
    # Center the drawn content on the square canvas.
    content_rows = [r for r in range(GRID_H) if any(grid[r])]
    content_cols = [c for c in range(GRID_W) if any(row[c] for row in grid)]
    art_w = (max(content_cols) - min(content_cols) + 1) * scale
    art_h = (max(content_rows) - min(content_rows) + 1) * scale
    ox = (size - art_w) // 2 - min(content_cols) * scale
    oy = (size - art_h) // 2 - min(content_rows) * scale

    buf = bytearray(size * size * 4)
    for r in range(GRID_H):
        for c in range(GRID_W):
            rgb = grid[r][c]
            if rgb is None:
                continue
            for yy in range(oy + r * scale, oy + (r + 1) * scale):
                row_base = (yy * size + ox + c * scale) * 4
                for i in range(scale):
                    buf[row_base + i * 4 : row_base + i * 4 + 4] = bytes((*rgb, 255))

    # PNG scanlines are each prefixed with a filter-type byte; 0 means "none".
    raw = b"".join(
        b"\x00" + bytes(buf[y * size * 4 : (y + 1) * size * 4]) for y in range(size)
    )

    def chunk(typ: bytes, data: bytes) -> bytes:
        """Frame a payload as a PNG chunk.

        Args:
            typ: The four-byte chunk type, such as b"IHDR".
            data: The chunk payload.

        Returns:
            The chunk: length, type, payload, then CRC32 of type and payload.
        """
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        # 8 bits per channel, color type 6 (RGBA), default compression/filter/interlace.
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as f:
        f.write(png)


def main() -> None:
    """Render the rest pose and write the SVG, logo PNG and favicon PNG."""
    grid = render_rest()
    # Symmetry check on the artifact itself: every row's drawn extent must be
    # centered on x = CENTER_X.
    for r in range(GRID_H):
        cols = [c for c in range(GRID_W) if grid[r][c] is not None]
        if cols:
            assert min(cols) + max(cols) == 2 * CENTER_X - 1, (
                f"row {r} asymmetric: {min(cols)}..{max(cols)}"
            )
    n = emit_svg(grid, "docs/assets/dstrack-logo.svg")
    emit_png(grid, "docs/assets/dstrack-logo.png", size=510, scale=17)
    emit_png(grid, "docs/assets/dstrack-favicon.png", size=60, scale=2)
    widths = [sum(1 for c in range(GRID_W) if grid[r][c]) for r in range(GRID_H)]
    print(f"SVG written ({n} rects), logo PNG (510x510 @17x), favicon PNG (60x60 @2x)")
    print("row widths:", widths)


if __name__ == "__main__":
    main()
