"use strict";

document.querySelectorAll("[data-content-tabs]").forEach((wrapper) => {
    const scroller = wrapper.querySelector("[data-tabs-scroll]");
    const previous = wrapper.querySelector(".jrc-tabs-previous");
    const next = wrapper.querySelector(".jrc-tabs-next");
    const active = scroller.querySelector('[aria-selected="true"]');
    const update = () => {
        previous.hidden = scroller.scrollLeft <= 1;
        next.hidden = scroller.scrollLeft + scroller.clientWidth >= scroller.scrollWidth - 1;
    };
    // Opening History changes only the horizontal position, without jumping the page.
    const revealActive = () => {
        if (active) {
            const tab = active.getBoundingClientRect();
            const bounds = scroller.getBoundingClientRect();
            if (tab.left < bounds.left + 28 || tab.right > bounds.right - 28) {
                scroller.scrollLeft += tab.left - bounds.left - (bounds.width - tab.width) / 2;
            }
        }
        update();
    };
    previous.addEventListener("click", () => scroller.scrollBy({left: -scroller.clientWidth / 2, behavior: "smooth"}));
    next.addEventListener("click", () => scroller.scrollBy({left: scroller.clientWidth / 2, behavior: "smooth"}));
    scroller.addEventListener("scroll", update, {passive: true});
    new ResizeObserver(revealActive).observe(scroller);
    revealActive();
});
