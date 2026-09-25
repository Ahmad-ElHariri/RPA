"""ALT-click development inspector for discovering useful Airtable structure."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from backend.config import lebanon_now

from playwright.sync_api import BrowserContext, Page, Error as PlaywrightError


LOGGER = logging.getLogger(__name__)
_BINDING_NAME = "__airtableInspectorCapture"


def _inspection_path(output_dir: Path, app_name: str, label: str, kind: str) -> Path:
    def slug(value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-_")[:80] or "page"

    stamp = lebanon_now().strftime("%m-%d__%H.%M")
    name = f"{slug(app_name)}__{slug(label)}__{kind}"
    path = output_dir / f"{name}  -  {stamp}.json"
    number = 2
    while path.exists():
        path = output_dir / f"{name}  -  {stamp} ({number}).json"
        number += 1
    return path

_INSPECTOR_SCRIPT = r"""
(() => {
  if (window.__airtableInspectorInstalled) return;
  window.__airtableInspectorInstalled = true;

  const clean = (value, limit = 240) => {
    if (!value) return null;
    const normalized = String(value).replace(/\s+/g, " ").trim();
    return normalized ? normalized.slice(0, limit) : null;
  };

  const describe = (element) => {
    const attributes = {};
    for (const name of [
      "id", "role", "aria-label", "aria-labelledby", "aria-describedby",
      "data-testid", "data-elementtype", "name", "type", "title"
    ]) {
      const value = element.getAttribute(name);
      if (value) attributes[name] = clean(value);
    }

    const labelledBy = element.getAttribute("aria-labelledby");
    const labelledText = labelledBy
      ? labelledBy.split(/\s+/).map(id => document.getElementById(id)?.textContent || "").join(" ")
      : null;
    const accessibleName = clean(
      element.getAttribute("aria-label") || labelledText || element.getAttribute("title")
    );

    let selectorHint = element.tagName.toLowerCase();
    if (attributes["data-testid"]) {
      selectorHint += `[data-testid=${JSON.stringify(attributes["data-testid"])}]`;
    } else if (attributes["data-elementtype"]) {
      selectorHint += `[data-elementtype=${JSON.stringify(attributes["data-elementtype"])}]`;
    } else if (attributes["aria-label"]) {
      selectorHint += `[aria-label=${JSON.stringify(attributes["aria-label"])}]`;
    } else if (attributes.role) {
      selectorHint += `[role=${JSON.stringify(attributes.role)}]`;
    }

    const implicitRole = (() => {
      const tag = element.tagName.toLowerCase();
      if (tag === "button") return "button";
      if (tag === "a" && element.hasAttribute("href")) return "link";
      if (tag === "select") return "combobox";
      if (tag === "textarea") return "textbox";
      if (tag === "input") {
        const type = (element.getAttribute("type") || "text").toLowerCase();
        if (["button", "submit", "reset"].includes(type)) return "button";
        if (type === "checkbox") return "checkbox";
        if (type === "radio") return "radio";
        return "textbox";
      }
      return null;
    })();

    return {
      tag: element.tagName.toLowerCase(),
      role: element.getAttribute("role") || implicitRole,
      accessible_name: accessibleName,
      visible_text: clean(element.innerText || element.textContent),
      attributes,
      selector_hint: selectorHint
    };
  };

  document.addEventListener("click", async (event) => {
    if (!event.altKey) return;
    event.preventDefault();
    event.stopImmediatePropagation();

    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;

    const ancestors = [];
    let current = target.parentElement?.parentElement;
    while (current && ancestors.length < 4) {
      const info = describe(current);
      if (info.role || info.accessible_name || info.visible_text ||
          Object.keys(info.attributes).length > 0) {
        ancestors.push(info);
      }
      current = current.parentElement;
    }

    const previousOutline = target.style.outline;
    target.style.outline = "3px solid #d946ef";
    setTimeout(() => { target.style.outline = previousOutline; }, 700);

    await window.__airtableInspectorCapture({
      page_url: location.href,
      element: describe(target),
      parent: target.parentElement ? describe(target.parentElement) : null,
      ancestors
    });
  }, true);
})();
"""


class InspectionRecorder:
    """Persist a bounded set of clicked element descriptions as one JSON document."""

    def __init__(self, output_dir: Path, app_name: str = "airtable", label: str = "page") -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self.path = _inspection_path(output_dir, app_name, label, "clicks")
        self._records: list[dict[str, Any]] = []

    def capture(self, source: Any, payload: dict[str, Any]) -> None:
        record = {
            "captured_at": lebanon_now().isoformat(),
            **payload,
        }
        self._records.append(record)
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(self._records, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary_path.replace(self.path)
        element = record.get("element", {})
        LOGGER.info(
            "Captured %s %r (%d total) -> %s",
            element.get("tag", "element"),
            element.get("accessible_name") or element.get("visible_text"),
            len(self._records),
            self.path,
        )


def install_inspector(context: BrowserContext, recorder: InspectionRecorder) -> None:
    """Install capture support for existing pages and future navigations."""
    context.expose_binding(_BINDING_NAME, recorder.capture)
    context.add_init_script(_INSPECTOR_SCRIPT)
    for page in context.pages:
        page.evaluate(_INSPECTOR_SCRIPT)


def wait_for_inspections(page: Page) -> None:
    """Keep Playwright's event loop active until the user closes Chrome or interrupts."""
    print("Inspector ready. ALT+click Airtable elements; press Ctrl+C when finished.")
    while not page.is_closed():
        try:
            page.wait_for_timeout(500)
        except PlaywrightError:
            if page.is_closed():
                return
            raise


def capture_page_structure(
    page: Page, output_dir: Path, app_name: str = "airtable", label: str = "page"
) -> Path:
    """Snapshot accessible structure and useful DOM attributes in every frame."""
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for frame in page.frames:
        try:
            frames.append({
                "url": frame.url,
                "accessibility": frame.locator("body").aria_snapshot(),
                "elements": frame.locator(
                    '[role], [aria-label], [data-testid], [data-rfd-draggable-id], '
                    'button, input, select, textarea, a[href]'
                ).evaluate_all("""es => es.map(e => ({
                    tag:e.tagName.toLowerCase(),
                    attributes:Object.fromEntries([...e.attributes].filter(a =>
                        /^(id|role|aria-.+|data-testid|data-rfd-draggable-id|name|type|href|title)$/.test(a.name)
                    ).map(a=>[a.name,a.value])),
                    text:(e.innerText||'').trim().slice(0,500),
                    rendered:Boolean(e.getClientRects().length),
                    ancestors:(()=>{const out=[];for(let n=e.parentElement;n&&out.length<4;n=n.parentElement)
                        out.push({tag:n.tagName.toLowerCase(),role:n.getAttribute('role'),
                        label:n.getAttribute('aria-label'),testid:n.getAttribute('data-testid')});return out;})()
                }))"""),
            })
        except Exception as exc:
            frames.append({"url": frame.url, "error": str(exc)})
    path = _inspection_path(output_dir, app_name, label, "structure")
    path.write_text(json.dumps({"page_url": page.url, "frames": frames},
                               indent=2, ensure_ascii=False), encoding="utf-8")
    return path
