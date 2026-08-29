import "@testing-library/jest-dom/vitest";

// Node 18 (this repo's dev toolchain baseline) doesn't have Promise.withResolvers, which
// pdfjs-dist v4 uses at import time. Every real target browser (Chrome 119+, Firefox
// 121+, Safari 17.4+) has had this since late 2023 — this is a test-runner gap, not a
// production compatibility concern, so it's polyfilled here rather than downgrading the
// library.
if (typeof Promise.withResolvers !== "function") {
  Promise.withResolvers = function withResolvers<T>() {
    let resolve!: (value: T | PromiseLike<T>) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  };
}
