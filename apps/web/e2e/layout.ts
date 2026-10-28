import { expect, type Page, type TestInfo } from '@playwright/test';

/**
 * Delay real ResizeObserver delivery to reproduce a chart's stale inline size.
 * Observers still observe real containers; resuming delivers their latest entry.
 * Disconnected observers discard pending notifications. Controls survive reload.
 * @param page Browser page whose next document receives the observer wrapper.
 * @returns Controls for holding and releasing resize delivery in that document.
 */
export async function controlResizeDelivery(page: Page) {
  await page.addInitScript(() => {
    const NativeObserver = window.ResizeObserver;
    const pending = new Map<ResizeObserver, () => void>();
    let held = false;
    window.addEventListener('tachyron:hold-resize', () => {
      held = true;
    });
    window.addEventListener('tachyron:release-resize', () => {
      held = false;
      for (const deliver of pending.values()) deliver();
      pending.clear();
    });
    window.ResizeObserver = class extends NativeObserver {
      constructor(callback: ResizeObserverCallback) {
        super((entries, observer) => {
          if (held) {
            pending.set(observer, () => callback(entries, observer));
            return;
          }
          callback(entries, observer);
        });
      }

      override disconnect() {
        pending.delete(this);
        super.disconnect();
      }
    };
  });

  return {
    hold: () =>
      page.evaluate(() =>
        window.dispatchEvent(new Event('tachyron:hold-resize')),
      ),
    release: () =>
      page.evaluate(() =>
        window.dispatchEvent(new Event('tachyron:release-resize')),
      ),
  };
}

/** Capture CSS pixels, bitmap sizes and overflow without treating table scrolling as a page defect. */
export function inspectLayout() {
  function describe(element: Element) {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return {
      tag: element.tagName,
      className: element.getAttribute('class'),
      left: rect.left,
      right: rect.right,
      width: rect.width,
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
      display: style.display,
      cssWidth: style.width,
      minWidth: style.minWidth,
      maxWidth: style.maxWidth,
      overflowX: style.overflowX,
      position: style.position,
      font: style.font,
      grid: style.gridTemplateColumns,
      withinTable: Boolean(element.closest('.table-scroll')),
    };
  }

  const viewport = document.documentElement.clientWidth;
  const charts = [...document.querySelectorAll('canvas')].map((canvas) => ({
    canvas: describe(canvas),
    bitmapWidth: canvas.width,
    bitmapHeight: canvas.height,
    parent: canvas.parentElement ? describe(canvas.parentElement) : null,
  }));
  return {
    hash: location.hash,
    innerWidth,
    clientWidth: viewport,
    documentScrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    scrollbarWidth: innerWidth - viewport,
    devicePixelRatio,
    visualViewportWidth: visualViewport?.width,
    fontStatus: document.fonts.status,
    font: getComputedStyle(document.body).fontFamily,
    mobileLayout: matchMedia('(max-width: 680px)').matches,
    userAgent: navigator.userAgent,
    charts,
    extendedElements: [...document.querySelectorAll('body *')]
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        return (
          rect.width > 0 &&
          rect.height > 0 &&
          (rect.right > viewport || rect.left < 0)
        );
      })
      .map(describe),
  };
}

/** Wait for bitmap redraw after observer delivery; CSS containment must hold before this wait. */
export async function expectChartsResized(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(() =>
        [...document.querySelectorAll('canvas')].every((canvas) => {
          const parent = canvas.parentElement;
          return (
            parent !== null &&
            Math.abs(canvas.width / devicePixelRatio - parent.clientWidth) <= 1
          );
        }),
      ),
    )
    .toBe(true);
}

/** Exercise populated views and retain viewport/overflow evidence for each supported width. */
export async function checkResponsiveViews(page: Page, testInfo: TestInfo) {
  const evidence: ReturnType<typeof inspectLayout>[] = [];
  const views = { overview: 2, compare: 6, history: 6, experiments: 0, api: 0 };
  try {
    for (const width of [320, 375, 390, 768, 1280]) {
      await page.evaluate(() => {
        location.hash = 'overview';
      });
      await expect(page.locator('canvas')).toHaveCount(2);
      await expectChartsResized(page);
      await page.setViewportSize({ width, height: 812 });
      for (const [view, chartCount] of Object.entries(views)) {
        await page.evaluate((target) => {
          location.hash = target;
        }, view);
        await expect(page.locator('a[aria-current="page"]')).toHaveAttribute(
          'href',
          `#${view}`,
        );
        await expect(page.locator('canvas')).toHaveCount(chartCount);
        await expectChartsResized(page);
        const layout = await page.evaluate(inspectLayout);
        evidence.push(layout);
        expect(
          layout.documentScrollWidth,
          `${view} at ${width}px`,
        ).toBeLessThanOrEqual(layout.clientWidth);
        expect(
          layout.charts.every(
            (chart) => chart.canvas.width <= (chart.parent?.width ?? 0),
          ),
          'Every canvas fits its container',
        ).toBe(true);
        const scrollable = await page
          .locator('.table-scroll:visible')
          .evaluateAll((elements) =>
            elements.every((element) => {
              element.scrollLeft = element.scrollWidth;
              return (
                element.scrollWidth <= element.clientWidth ||
                element.scrollLeft > 0
              );
            }),
          );
        expect(scrollable, 'Wide tables scroll within their containers').toBe(
          true,
        );
      }
    }
  } finally {
    await testInfo.attach('responsive-layout', {
      body: JSON.stringify(evidence, null, 2),
      contentType: 'application/json',
    });
  }
}
