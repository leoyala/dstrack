/* dstrack logo animation.
 *
 * Re-renders the pixel-art isometric logo (three stacked square slabs seen
 * from an isometric viewpoint) on a small canvas that is upscaled with
 * `image-rendering: pixelated`. At scroll position 0 the render is
 * pixel-identical to docs/assets/dstrack-logo.svg; as the user scrolls down,
 * each slab rotates in the horizontal plane at its own speed and direction.
 *
 * The chunky stair steps on each slab's border are modeled as real geometry
 * (a stepped polygon in plan view), not left to rasterization: this keeps the
 * number of border indentations constant while a slab turns. The scene is
 * rasterized at SCALE x the logo grid so the rotating steps stay steady
 * instead of crawling.
 *
 * The script progressively enhances the landing page: it looks for the
 * `#dstrack-logo` image and swaps it for a canvas. Without JavaScript the
 * original PNG logo is shown. Re-initializes after instant navigation
 * (zensical swaps the page content via XHR) and honors
 * `prefers-reduced-motion`.
 */
(() => {
  "use strict";

  // Grid and projection constants, in logo pixel units. The slab is a
  // 4-fold-symmetric stepped square in plan view: every corner tip step is
  // 2 plan units wide, front/back and left/right alike.
  const GRID_W = 24;
  const GRID_H = 29;
  const CENTER_X = 12; // horizontal center of the slabs
  const RADIUS = 11; // half-diagonal of each square slab (plan view)
  const ISO_Y = 0.5; // 2:1 isometric vertical foreshortening
  const THICKNESS = 2; // slab thickness
  const SCALE = 4; // internal subpixels per logo pixel
  const CANVAS_W = GRID_W * SCALE;
  const CANVAS_H = GRID_H * SCALE;
  const EPS = 1e-9;
  const COS45 = Math.SQRT1_2;

  // Rotation per scrolled pixel (radians), top slab first. Alternating
  // directions make the stack feel like meshing gears.
  const SPEEDS = [0.36, -0.24, 0.16].map((deg) => (deg * Math.PI) / 180);
  const EASE = 0.12; // per-frame easing toward the scroll target

  const hex = (s) => [
    parseInt(s.slice(1, 3), 16),
    parseInt(s.slice(3, 5), 16),
    parseInt(s.slice(5, 7), 16),
  ];

  // One entry per slab, top to bottom: vertical center of the top face
  // (screen units) and the palette taken verbatim from the SVG logo.
  // `left`/`right` shade the side faces at rest; `edge` is the dark seam
  // painted on the corner tips.
  const LAYERS = [
    { cy: 6.5, top: hex("#a99cff"), left: hex("#7060e0"), right: hex("#4a3cc0"), edge: hex("#2f2490") },
    { cy: 13.5, top: hex("#8b7bf0"), left: hex("#5b4bd6"), right: hex("#3a2fa6"), edge: hex("#241b6b") },
    { cy: 20.5, top: hex("#6a5be0"), left: hex("#463aae"), right: hex("#2c2185"), edge: hex("#1d1568") },
  ];

  const mix = (a, b, t) => [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ];

  // ------------------------------------------------------------------
  // Slab geometry
  // ------------------------------------------------------------------

  // Top-face outline in plan view (horizontal plane, +y toward the viewer),
  // built once from the pixel pattern of the logo: a 45deg-rotated square
  // whose edges are staircases of 2x2 plan-unit steps. At rest every vertex
  // projects onto a logo pixel boundary, which is what makes the resting
  // frame reproduce the SVG exactly.
  //
  // Each outline edge knows its outward normal (rest angle), whether it is a
  // dark corner-tip seam, and which face of the underlying smooth square it
  // belongs to (rest normal of that face), used for shading.
  function buildOutline() {
    const pts = [];
    for (let dy = -5; dy <= 5; dy++) {
      const hw = RADIUS - 2 * Math.abs(dy);
      pts.push([hw, dy - 0.5], [hw, dy + 0.5]);
    }
    for (let dy = 5; dy >= -5; dy--) {
      const hw = RADIUS - 2 * Math.abs(dy);
      pts.push([-hw, dy + 0.5], [-hw, dy - 0.5]);
    }
    const n = pts.length;
    const px = new Float64Array(n);
    const py = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      px[i] = pts[i][0];
      py[i] = pts[i][1] / ISO_Y; // undo the isometric foreshortening
    }
    const edges = [];
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      const mx = (px[i] + px[j]) / 2;
      const my = (py[i] + py[j]) / 2;
      let nx = py[j] - py[i];
      let ny = -(px[j] - px[i]);
      if (nx * mx + ny * my < 0) {
        nx = -nx;
        ny = -ny;
      }
      // Corner tips sit exactly on a plan axis; everything else belongs to
      // the face whose rest normal is the nearest odd multiple of 45deg.
      const mid = Math.atan2(my, mx);
      const quadrant = mid / (Math.PI / 2);
      const seam = Math.abs(quadrant - Math.round(quadrant)) < 1e-6;
      const m = ((mid % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
      const base = Math.floor(m / (Math.PI / 2)) * (Math.PI / 2) + Math.PI / 4;
      edges.push({ a: i, b: j, normal: Math.atan2(ny, nx), seam, base });
    }
    return { px, py, edges, count: n };
  }

  const OUTLINE = buildOutline();

  // Fixed light from the left: the darkest shade when a face points right
  // (rest angle 45deg), the lightest when it points left (135deg), clamped
  // beyond, so the resting pose uses the exact logo colors.
  function shade(layer, normalAngle) {
    const t = Math.min(1, Math.max(0, (COS45 - Math.cos(normalAngle)) / (2 * COS45)));
    return mix(layer.right, layer.left, t);
  }

  // ------------------------------------------------------------------
  // Rasterization
  // ------------------------------------------------------------------

  function setPixel(data, x, y, rgb) {
    const i = (y * CANVAS_W + x) * 4;
    data[i] = rgb[0];
    data[i + 1] = rgb[1];
    data[i + 2] = rgb[2];
    data[i + 3] = 255;
  }

  // Even-odd scanline fill of a (possibly non-convex) polygon given in
  // canvas subpixel coordinates. A pixel is filled when its center lies
  // inside the polygon.
  function fillPolygon(data, xs, ys, count, rgb) {
    let minY = Infinity;
    let maxY = -Infinity;
    for (let i = 0; i < count; i++) {
      minY = Math.min(minY, ys[i]);
      maxY = Math.max(maxY, ys[i]);
    }
    const r0 = Math.max(0, Math.ceil(minY - 0.5));
    const r1 = Math.min(CANVAS_H - 1, Math.floor(maxY - 0.5));
    const crossings = [];
    for (let r = r0; r <= r1; r++) {
      const yc = r + 0.5;
      crossings.length = 0;
      for (let i = 0; i < count; i++) {
        const j = (i + 1) % count;
        const y1 = ys[i];
        const y2 = ys[j];
        if ((y1 <= yc) === (y2 <= yc)) continue;
        crossings.push(xs[i] + ((yc - y1) / (y2 - y1)) * (xs[j] - xs[i]));
      }
      crossings.sort((a, b) => a - b);
      for (let k = 0; k + 1 < crossings.length; k += 2) {
        const c0 = Math.max(0, Math.ceil(crossings[k] - 0.5));
        const c1 = Math.min(CANVAS_W - 1, Math.floor(crossings[k + 1] - 0.5));
        for (let c = c0; c <= c1; c++) setPixel(data, c, r, rgb);
      }
    }
  }

  const quadX = new Float64Array(4);
  const quadY = new Float64Array(4);

  function drawLayer(data, layer, angle) {
    const cosA = Math.cos(angle);
    const sinA = Math.sin(angle);
    const n = OUTLINE.count;
    const sx = new Float64Array(n);
    const syT = new Float64Array(n);
    const syB = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      const x = OUTLINE.px[i] * cosA - OUTLINE.py[i] * sinA;
      const y = OUTLINE.px[i] * sinA + OUTLINE.py[i] * cosA;
      sx[i] = (CENTER_X + x) * SCALE;
      syT[i] = (layer.cy + y * ISO_Y) * SCALE;
      syB[i] = syT[i] + THICKNESS * SCALE;
    }

    // Side faces: every outline edge whose outward normal currently points
    // toward the viewer, extruded down by the slab thickness. Shaded faces
    // first, dark seams second so they stay visible at the corners, top
    // face last so it owns the shared boundary pixels. Far-side steps that
    // slip through the visibility test are overpainted by the top face.
    for (const pass of [false, true]) {
      for (const e of OUTLINE.edges) {
        if (e.seam !== pass) continue;
        if (Math.sin(e.normal + angle) <= EPS) continue;
        quadX[0] = sx[e.a];
        quadX[1] = sx[e.b];
        quadX[2] = sx[e.b];
        quadX[3] = sx[e.a];
        quadY[0] = syT[e.a];
        quadY[1] = syT[e.b];
        quadY[2] = syB[e.b];
        quadY[3] = syB[e.a];
        const rgb = e.seam ? layer.edge : shade(layer, e.base + angle);
        fillPolygon(data, quadX, quadY, 4, rgb);
      }
    }
    fillPolygon(data, sx, syT, n, layer.top);
  }

  function render(imageData, angles) {
    imageData.data.fill(0);
    for (let i = LAYERS.length - 1; i >= 0; i--) {
      drawLayer(imageData.data, LAYERS[i], angles[i]);
    }
  }

  // ------------------------------------------------------------------
  // Page wiring
  // ------------------------------------------------------------------

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  let canvas = null;
  let ctx = null;
  let imageData = null;
  let rafId = 0;
  const current = [0, 0, 0];
  const target = [0, 0, 0];

  function renderNow() {
    render(imageData, current);
    ctx.putImageData(imageData, 0, 0);
  }

  function updateTarget() {
    const y = reduceMotion.matches ? 0 : window.scrollY || 0;
    for (let i = 0; i < 3; i++) target[i] = y * SPEEDS[i];
  }

  function tick() {
    let settled = true;
    let changed = false;
    for (let i = 0; i < 3; i++) {
      const delta = target[i] - current[i];
      if (Math.abs(delta) < 1e-4) {
        if (current[i] !== target[i]) {
          current[i] = target[i];
          changed = true;
        }
      } else {
        current[i] += delta * EASE;
        changed = true;
        settled = false;
      }
    }
    if (changed && canvas && canvas.isConnected) renderNow();
    rafId = settled ? 0 : requestAnimationFrame(tick);
  }

  function onScroll() {
    if (!canvas || !canvas.isConnected) return;
    updateTarget();
    if (!rafId) rafId = requestAnimationFrame(tick);
  }

  function initLogo() {
    const img = document.getElementById("dstrack-logo");
    if (!img) return;
    const replacement = document.createElement("canvas");
    replacement.width = CANVAS_W;
    replacement.height = CANVAS_H;
    replacement.className = img.className;
    replacement.setAttribute("role", "img");
    replacement.setAttribute("aria-label", img.alt || "dstrack logo");
    replacement.style.width = (img.getAttribute("width") || "200") + "px";
    replacement.style.height = "auto";
    replacement.style.aspectRatio = `${GRID_W} / ${GRID_H}`;
    replacement.style.imageRendering = "pixelated";
    img.replaceWith(replacement);

    canvas = replacement;
    ctx = canvas.getContext("2d");
    imageData = ctx.createImageData(CANVAS_W, CANVAS_H);
    updateTarget();
    for (let i = 0; i < 3; i++) current[i] = target[i];
    renderNow();
  }

  function boot() {
    initLogo();
    // Instant navigation swaps the page content via XHR without a reload;
    // watch for the logo image to (re)appear and upgrade it again.
    new MutationObserver(() => {
      if (!canvas || !canvas.isConnected) initLogo();
    }).observe(document.documentElement, { childList: true, subtree: true });
    window.addEventListener("scroll", onScroll, { passive: true });
    reduceMotion.addEventListener("change", onScroll);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
