// Liquid glass: an SVG displacement map refracts the backdrop and splits the
// RGB channels slightly, which a plain backdrop blur cannot do.
//
// The recipe is from the project's ui-motion cookbook (Mineradio). Each element
// gets its own filter and map because the map must match the element's size;
// browsers without `backdrop-filter: url()` keep the CSS blur fallback.

import { useEffect, useRef } from "react";

const SVG_NS = "http://www.w3.org/2000/svg";
let counter = 0;

export function buildGlassMap(width, height, radius) {
  const w = Math.max(240, Math.round(width || 400));
  const h = Math.max(48, Math.round(height || 92));
  const r = Math.max(12, Math.round(radius || 24));
  const edge = Math.min(w, h) * 0.035;
  const innerW = Math.max(1, w - edge * 2);
  const innerH = Math.max(1, h - edge * 2);
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" ` +
    `viewBox="0 0 ${w} ${h}">` +
    `<defs>` +
    `<linearGradient id="gr" x1="100%" y1="0%" x2="0%" y2="0%">` +
    `<stop offset="0%" stop-color="#000"/><stop offset="100%" stop-color="red"/>` +
    `</linearGradient>` +
    `<linearGradient id="gb" x1="0%" y1="0%" x2="0%" y2="100%">` +
    `<stop offset="0%" stop-color="#000"/><stop offset="100%" stop-color="blue"/>` +
    `</linearGradient>` +
    `</defs>` +
    `<rect width="${w}" height="${h}" fill="black"/>` +
    `<rect width="${w}" height="${h}" rx="${r}" fill="url(#gr)"/>` +
    `<rect width="${w}" height="${h}" rx="${r}" fill="url(#gb)" ` +
    `style="mix-blend-mode:difference"/>` +
    `<rect x="${edge.toFixed(2)}" y="${edge.toFixed(2)}" ` +
    `width="${innerW.toFixed(2)}" height="${innerH.toFixed(2)}" rx="${r}" ` +
    `fill="hsl(0 0% 50%)" style="filter:blur(11px)"/>` +
    `</svg>`;
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

function supportsSvgBackdrop() {
  return (
    typeof CSS !== "undefined" &&
    typeof CSS.supports === "function" &&
    CSS.supports("backdrop-filter", "url(#liquid-glass-probe)")
  );
}

export function applyLiquidGlass(element, radius = 24) {
  if (!element || !supportsSvgBackdrop()) return () => {};

  const id = `liquid-glass-${++counter}`;
  const host = document.createElementNS(SVG_NS, "svg");
  host.setAttribute("width", "0");
  host.setAttribute("height", "0");
  host.setAttribute("aria-hidden", "true");
  host.style.position = "absolute";
  host.style.pointerEvents = "none";

  const defs = document.createElementNS(SVG_NS, "defs");
  const filter = document.createElementNS(SVG_NS, "filter");
  filter.setAttribute("id", id);
  filter.setAttribute("x", "-12%");
  filter.setAttribute("y", "-28%");
  filter.setAttribute("width", "124%");
  filter.setAttribute("height", "156%");
  filter.setAttribute("color-interpolation-filters", "sRGB");

  const map = document.createElementNS(SVG_NS, "feImage");
  map.setAttribute("preserveAspectRatio", "none");
  map.setAttribute("result", "map");

  const channels = [
    ["dR", "cR", 180, "1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0"],
    ["dG", "cG", 170, "0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0"],
    ["dB", "cB", 160, "0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0"],
  ];
  const nodes = channels.map(([dispResult, colorResult, scale, matrix]) => {
    const displacement = document.createElementNS(SVG_NS, "feDisplacementMap");
    displacement.setAttribute("in", "SourceGraphic");
    displacement.setAttribute("in2", "map");
    displacement.setAttribute("scale", String(scale));
    displacement.setAttribute("xChannelSelector", "R");
    displacement.setAttribute("yChannelSelector", "B");
    displacement.setAttribute("result", dispResult);
    const color = document.createElementNS(SVG_NS, "feColorMatrix");
    color.setAttribute("in", dispResult);
    color.setAttribute("type", "matrix");
    color.setAttribute("values", matrix);
    color.setAttribute("result", colorResult);
    return [displacement, color];
  });

  const blendRG = document.createElementNS(SVG_NS, "feBlend");
  blendRG.setAttribute("in", "cR");
  blendRG.setAttribute("in2", "cG");
  blendRG.setAttribute("mode", "screen");
  blendRG.setAttribute("result", "rg");

  const blendRGB = document.createElementNS(SVG_NS, "feBlend");
  blendRGB.setAttribute("in", "rg");
  blendRGB.setAttribute("in2", "cB");
  blendRGB.setAttribute("mode", "screen");
  blendRGB.setAttribute("result", "rgb");

  const blur = document.createElementNS(SVG_NS, "feGaussianBlur");
  blur.setAttribute("in", "rgb");
  blur.setAttribute("stdDeviation", "0.5");

  filter.append(map, ...nodes.flat(), blendRG, blendRGB, blur);
  defs.append(filter);
  host.append(defs);
  document.body.append(host);

  let timer = 0;
  const update = () => {
    const rect = element.getBoundingClientRect();
    if (rect.width < 40 || rect.height < 30) return;
    map.setAttribute("href", buildGlassMap(rect.width, rect.height, radius));
    map.setAttribute("width", String(rect.width));
    map.setAttribute("height", String(rect.height));
    element.style.backdropFilter = `url(#${id}) saturate(1.1)`;
    element.style.webkitBackdropFilter = `url(#${id}) saturate(1.1)`;
    element.dataset.glass = "on";
  };

  update();
  const observer = new ResizeObserver(() => {
    window.clearTimeout(timer);
    timer = window.setTimeout(update, 180);
  });
  observer.observe(element);

  return () => {
    window.clearTimeout(timer);
    observer.disconnect();
    host.remove();
    element.style.backdropFilter = "";
    element.style.webkitBackdropFilter = "";
    delete element.dataset.glass;
  };
}

export function useLiquidGlass(radius = 24) {
  const ref = useRef(null);
  useEffect(() => {
    if (!ref.current) return undefined;
    return applyLiquidGlass(ref.current, radius);
  }, [radius]);
  return ref;
}
