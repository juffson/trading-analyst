"use strict";

/* Public, static research pages. Report text is never interpreted as markup. */
(() => {
  const search = document.querySelector("[data-security-search]");
  const filterButtons = Array.from(document.querySelectorAll("[data-market-filter]"));
  const cards = Array.from(document.querySelectorAll("[data-security-card]"));
  const coverageStatus = document.querySelector("[data-coverage-status]");
  const noResults = document.querySelector("[data-no-results]");
  let activeMarket = "all";

  function filterSecurities() {
    const query = search ? search.value.trim().toLocaleLowerCase() : "";
    let visible = 0;
    for (const card of cards) {
      const marketMatches = activeMarket === "all" || card.dataset.market === activeMarket;
      const queryMatches = (card.dataset.search || "").toLocaleLowerCase().includes(query);
      card.hidden = !(marketMatches && queryMatches);
      if (!card.hidden) visible += 1;
    }
    if (coverageStatus) coverageStatus.textContent = `显示 ${visible} / ${cards.length} 只观察标的`;
    if (noResults) noResults.hidden = visible !== 0;
  }

  if (search) search.addEventListener("input", filterSecurities);
  for (const button of filterButtons) {
    button.addEventListener("click", () => {
      activeMarket = button.dataset.marketFilter;
      for (const peer of filterButtons) peer.setAttribute("aria-pressed", String(peer === button));
      filterSecurities();
    });
  }

  const comparisonButtons = Array.from(document.querySelectorAll("[data-compare-target]"));
  const comparisonPanels = Array.from(document.querySelectorAll("[data-compare-panel]"));
  for (const button of comparisonButtons) {
    button.addEventListener("click", () => {
      for (const peer of comparisonButtons) peer.setAttribute("aria-pressed", String(peer === button));
      for (const panel of comparisonPanels) panel.hidden = panel.id !== button.dataset.compareTarget;
    });
  }

  for (const frame of document.querySelectorAll("[data-chart-frame]")) {
    const svg = frame.querySelector("[data-chart-points]");
    const tooltip = frame.querySelector(".chart-tooltip");
    const hover = frame.querySelector(".chart-hover");
    if (!svg || !tooltip || !hover) continue;
    let points;
    try { points = JSON.parse(svg.dataset.chartPoints); } catch (_) { continue; }
    if (!Array.isArray(points) || points.length === 0) continue;
    const crosshair = hover.querySelector("line");
    const dot = hover.querySelector("circle");
    let activeIndex = points.length - 1;

    function showPoint(index, pointerX) {
      const point = points[index];
      if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return;
      activeIndex = index;
      hover.hidden = false;
      hover.removeAttribute("display");
      crosshair.setAttribute("x1", point.x);
      crosshair.setAttribute("x2", point.x);
      dot.setAttribute("cx", point.x);
      dot.setAttribute("cy", point.y);
      tooltip.textContent = point.label;
      tooltip.hidden = false;
      const rect = frame.getBoundingClientRect();
      const svgRect = svg.getBoundingClientRect();
      const viewWidth = svg.viewBox.baseVal.width;
      const localX = pointerX === undefined ? (point.x / viewWidth) * svgRect.width : pointerX - rect.left;
      const left = Math.min(Math.max(localX + 13, 4), Math.max(4, rect.width - tooltip.offsetWidth - 4));
      tooltip.style.left = `${left}px`;
      tooltip.style.top = "12px";
    }

    function hidePoint() {
      hover.setAttribute("display", "none");
      tooltip.hidden = true;
    }

    svg.addEventListener("pointermove", event => {
      const matrix = svg.getScreenCTM();
      if (!matrix) return;
      const cursor = svg.createSVGPoint();
      cursor.x = event.clientX;
      cursor.y = event.clientY;
      const local = cursor.matrixTransform(matrix.inverse());
      let nearest = 0;
      let bestDistance = Infinity;
      for (let index = 0; index < points.length; index += 1) {
        const distance = Math.abs(points[index].x - local.x);
        if (distance < bestDistance) { bestDistance = distance; nearest = index; }
      }
      showPoint(nearest, event.clientX);
    });
    svg.addEventListener("pointerleave", hidePoint);
    svg.addEventListener("focus", () => showPoint(activeIndex));
    svg.addEventListener("blur", hidePoint);
    svg.addEventListener("keydown", event => {
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        const direction = event.key === "ArrowRight" ? 1 : -1;
        showPoint(Math.max(0, Math.min(points.length - 1, activeIndex + direction)));
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        showPoint(event.key === "Home" ? 0 : points.length - 1);
      } else if (event.key === "Escape") {
        hidePoint();
      }
    });
  }
})();
