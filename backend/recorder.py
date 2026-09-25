"""Record the live DOM structure surrounding normal browser clicks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page

from backend.config import lebanon_now


_BINDING = "__rpaSaveClick"


def _path(output_dir: Path, app: str, label: str) -> Path:
    def slug(value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-_")[:80] or "page"

    stem = f"{slug(app)}__{slug(label)}__recording  -  {lebanon_now():%m-%d__%H.%M}"
    path, number = output_dir / f"{stem}.json", 2
    while path.exists():
        path = output_dir / f"{stem} ({number}).json"
        number += 1
    return path


# Capture the clicked DOM immediately, then collect menus and dialogs after the
# application has had time to react to the click.
_SCRIPT = r"""
(() => {
  if (window.__rpaClickRecorder) return;
  window.__rpaClickRecorder = true;

  const clean = (value, limit = 500) => {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    return text ? text.slice(0, limit) : null;
  };
  const role = element => element.getAttribute("role") || ({
    BUTTON: "button", A: element.hasAttribute("href") ? "link" : null,
    SELECT: "combobox", TEXTAREA: "textbox", INPUT: "textbox"
  })[element.tagName] || null;
  const describe = (element, htmlLimit = 0) => {
    if (!(element instanceof Element)) return null;
    const state = {};
    for (const key of ["checked", "selected", "disabled", "value"])
      if (key in element) state[key] = element[key];
    for (const key of ["aria-checked", "aria-selected", "aria-expanded", "aria-pressed"])
      if (element.hasAttribute(key)) state[key] = element.getAttribute(key);
    const result = {
      tag: element.tagName.toLowerCase(), role: role(element),
      text: clean(element.innerText || element.textContent),
      attributes: Object.fromEntries(
        [...element.attributes].map(a => [a.name, clean(a.value, 1000)])
      ),
      state
    };
    if (htmlLimit) result.html = element.outerHTML.slice(0, htmlLimit);
    return result;
  };
  const actionableSelector = [
    "button", "a[href]", "input", "select", "textarea", "[contenteditable=true]",
    "[role=button]", "[role=checkbox]", "[role=radio]", "[role=combobox]",
    "[role=option]", "[role=menuitem]", "[role=tab]", "[role=listbox]"
  ].join(",");
  const regionSelector = [
    "section", "form", "[role=dialog]", "[role=menu]", "[role=listbox]",
    "[role=row]", "[role=region]", "[aria-label]", "[data-testid]"
  ].join(",");
  const overlays = () => [...document.querySelectorAll('[role=dialog],[role=menu],[role=listbox]')]
    .filter(element => {
      const box = element.getBoundingClientRect(), style = getComputedStyle(element);
      return box.width && box.height && style.display !== "none" && style.visibility !== "hidden";
    }).slice(-3).map(element => describe(element, 4000));

  document.addEventListener("click", event => {
    const path = event.composedPath().filter(node => node instanceof Element);
    const target = path[0];
    if (!target) return;
    const pointElements = document.elementsFromPoint(event.clientX, event.clientY).slice(0, 6);
    const actionable = path.find(element => element.matches(actionableSelector)) ||
      pointElements.find(element => element.matches(actionableSelector)) ||
      path.slice(0, 5).find(element => element.matches("[data-testid],[aria-label]")) || target;
    const start = Math.max(0, path.indexOf(actionable));
    const region = path.slice(start + 1).find(element => element.matches(regionSelector)) ||
      actionable.parentElement;
    const click = {
      page_url: location.href, frame: {url: location.href, name: window.name || null},
      pointer: {x: event.clientX, y: event.clientY, button: event.button},
      target: describe(target, 1500), actionable: describe(actionable, 2500),
      region: describe(region, 4000),
      elements_at_pointer: pointElements.map(element => describe(element, 500)),
      ancestors: path.slice(1, 7).map(element => describe(element))
    };
    setTimeout(() => window.__rpaSaveClick({...click, visible_overlays_after: overlays()}), 400);
  }, true);
})();
"""


class ClickRecorder:
    """Save ordered click structures to one JSON file."""

    def __init__(self, output_dir: Path, app: str, label: str) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self.path = _path(output_dir, app, label)
        self.data: dict[str, Any] = {
            "format": "rpa-click-dom-v1",
            "started_at": lebanon_now().isoformat(),
            "app": app,
            "label": label,
            "clicks": [],
        }
        self._write()

    def _write(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(self.path)

    def capture(self, _source: Any, click: dict[str, Any]) -> None:
        click = {
            "step": len(self.data["clicks"]) + 1,
            "captured_at": lebanon_now().isoformat(),
            **click,
        }
        self.data["clicks"].append(click)
        self._write()

    def finish(self) -> None:
        self.data["finished_at"] = lebanon_now().isoformat()
        self._write()


def install_recorder(context: BrowserContext, recorder: ClickRecorder) -> None:
    """Install recording in current and future pages and frames."""
    context.expose_binding(_BINDING, recorder.capture)
    context.add_init_script(_SCRIPT)
    for page in context.pages:
        for frame in page.frames:
            try:
                frame.evaluate(_SCRIPT)
            except PlaywrightError:
                pass


def wait_for_recording(page: Page) -> None:
    print("Recorder ready. Click normally, wait briefly, then close Chrome.")
    while not page.is_closed():
        try:
            page.wait_for_timeout(500)
        except PlaywrightError:
            if page.is_closed():
                return
            raise
