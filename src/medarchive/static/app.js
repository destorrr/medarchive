/* Keyboard support for horizontally scrollable data regions. */
document.addEventListener("keydown", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  if (
    target.isContentEditable ||
    target.closest("input, textarea, select, button, [contenteditable='true']")
  ) return;
  if (event.altKey || event.ctrlKey || event.metaKey) return;

  const region = target.closest(
    '.table-wrap[role="region"][tabindex="0"], .receipt-table-wrap[role="region"][tabindex="0"], .dashboard-table-wrap[role="region"][tabindex="0"]'
  );
  if (!region || region.scrollWidth <= region.clientWidth) return;

  let nextLeft;
  const step = Math.max(40, Math.round(region.clientWidth * 0.12));
  switch (event.key) {
    case "ArrowRight":
      nextLeft = region.scrollLeft + step;
      break;
    case "ArrowLeft":
      nextLeft = region.scrollLeft - step;
      break;
    case "End":
      nextLeft = region.scrollWidth;
      break;
    case "Home":
      nextLeft = 0;
      break;
    default:
      return;
  }

  event.preventDefault();
  region.scrollTo({ left: nextLeft, behavior: "auto" });
});
