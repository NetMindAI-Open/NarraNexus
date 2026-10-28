"""@file_name: pages.py
@description: Browser target discovery and independent page lifetime/state.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger

_METADATA_BINDING = "__narranexusPageMetadata"
_METADATA_WORLD = "narranexus-page-metadata"
_METADATA_SCRIPT = """(() => {
  if (window.top !== window || window.__narranexusMetadataObserver) return;
  window.__narranexusMetadataObserver = true;
  let previous = '';
  const report = () => {
    const value = JSON.stringify({title: document.title, url: location.href});
    if (value !== previous) { previous = value; window.__narranexusPageMetadata(value); }
  };
  new MutationObserver(report).observe(document, {subtree: true, childList: true, characterData: true});
  addEventListener('DOMContentLoaded', report);
  addEventListener('popstate', report);
  addEventListener('hashchange', report);
  report();
})();"""


@dataclass
class BrowserPage:
    id: str
    cdp: Any
    title: str = ""
    url: str = "about:blank"
    opener_id: str | None = None
    sinks: list[Callable[[dict], None]] = field(default_factory=list)
    last_frame: dict | None = None
    streaming: bool = False
    stream_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    viewport: tuple[int, int] | None = None
    metadata_ready: bool = False
    unsubscribe_metadata: Callable[[], None] = field(default=lambda: None)

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "url": self.url, "opener_id": self.opener_id}


class BrowserPages:
    """Track page targets; mutations share the session's operation lock."""

    def __init__(self, cdp: Any, *, page_id: str, lock: asyncio.Lock, browser: Any = None) -> None:
        self.browser = browser
        self._transport = browser or cdp
        self._lock = lock
        self.items: dict[str, BrowserPage] = {}
        self.active_id = page_id
        self._events: asyncio.Queue = asyncio.Queue()
        self._listeners: list[Callable[[], None]] = []
        self._worker: asyncio.Task | None = None
        self._unsubscribe: Callable[[], None] = lambda: None
        self.error: str | None = None
        self._closed = False
        self._destroyed: set[str] = set()
        self.add(page_id, cdp)

    @property
    def is_open(self) -> bool:
        return not self._closed and self._transport.is_open

    @property
    def current(self) -> BrowserPage:
        return self.get(self.active_id)

    def get(self, page_id: str) -> BrowserPage:
        page = self.items.get(page_id)
        if page is None or not page.cdp.is_open:
            raise ValueError("Browser page is closed or unknown; refresh the page list.")
        return page

    def add(self, page_id: str, cdp: Any, **metadata: Any) -> BrowserPage:
        page = BrowserPage(id=page_id, cdp=cdp, **metadata)
        self.items[page_id] = page
        self.changed()
        return page

    def snapshot(self) -> dict:
        return {"pages": [page.to_dict() for page in self.items.values()],
                "active_page_id": self.active_id, "page_error": self.error}

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def changed(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                logger.exception("browser page listener failed")

    def select(self, page_id: str) -> None:
        self.get(page_id)
        if self.active_id != page_id:
            self.active_id = page_id
            self.changed()

    @staticmethod
    def _visible(info: dict) -> bool:
        url = info.get("url", "")
        return info.get("type") == "page" and not info.get("subtype") and (
            not url or url == "about:blank" or url.startswith(("http://", "https://"))
        )

    async def start(self) -> None:
        if self.browser is None:
            return

        def event(method: str, params: dict) -> None:
            if method in {"Target.targetCreated", "Target.targetInfoChanged", "Target.targetDestroyed"}:
                if method == "Target.targetDestroyed":
                    self._destroyed.add(params["targetId"])
                self._events.put_nowait((method, params))

        self._unsubscribe = self.browser.subscribe_events(event)
        await self._watch_metadata(self.current)
        await self.browser.call("Target.setDiscoverTargets", {"discover": True})
        targets = await self.browser.call("Target.getTargets")
        for info in targets.get("targetInfos", []):
            await self._upsert(info, select_new=False)
        self._worker = asyncio.create_task(self._follow())
        self._worker.add_done_callback(lambda task: task.cancelled() or task.exception())

    async def _upsert(self, info: dict, *, select_new: bool = True) -> None:
        page_id = info["targetId"]
        if page_id in self._destroyed:
            return
        page = self.items.get(page_id)
        if page is None:
            if not self._visible(info):
                return
            try:
                attached = await self.browser.call("Target.attachToTarget", {"targetId": page_id, "flatten": True})
            except RuntimeError:
                if page_id in self._destroyed:
                    return
                raise
            cdp = self.browser.channel(attached["sessionId"])
            page = self.add(page_id, cdp)
            await self._watch_metadata(page)
            if select_new and (not info.get("openerId") or info["openerId"] == self.active_id):
                self.active_id = page_id
        if not page.metadata_ready or page.url != info.get("url"):
            page.title = info.get("title", "")
            page.metadata_ready = False
        page.url = info.get("url", "about:blank")
        page.opener_id = info.get("openerId")
        self.changed()

    async def _watch_metadata(self, page: BrowserPage) -> None:
        def event(method: str, params: dict) -> None:
            if method == "Runtime.bindingCalled" and params.get("name") == _METADATA_BINDING:
                self._events.put_nowait(("metadata", {"page_id": page.id, "payload": params.get("payload")}))

        page.unsubscribe_metadata = page.cdp.subscribe_events(event)
        await page.cdp.call("Page.enable")
        await page.cdp.call("Runtime.enable")
        await page.cdp.call("Runtime.addBinding", {"name": _METADATA_BINDING, "executionContextName": _METADATA_WORLD})
        await page.cdp.call("Page.addScriptToEvaluateOnNewDocument", {
            "source": _METADATA_SCRIPT, "worldName": _METADATA_WORLD, "runImmediately": True,
        })

    async def _follow(self) -> None:
        while True:
            method, params = await self._events.get()
            try:
                async with self._lock:
                    if method == "Target.targetDestroyed":
                        await self.remove(params["targetId"])
                        self._destroyed.discard(params["targetId"])
                    elif method == "metadata":
                        page = self.items.get(params["page_id"])
                        value = json.loads(params["payload"])
                        if page is not None and isinstance(value, dict) and all(
                            isinstance(value.get(key), str) for key in ("title", "url")
                        ):
                            page.title, page.url = value["title"], value["url"]
                            page.metadata_ready = True
                            self.changed()
                    else:
                        await self._upsert(params["targetInfo"])
                self.error = None
            except Exception:
                logger.exception("could not update browser pages")
                self.error = "Could not update browser pages."
                self.changed()
            finally:
                self._events.task_done()

    async def flush(self) -> None:
        if self._worker is not None:
            await self._events.join()

    async def create(self) -> BrowserPage:
        if self.browser is None:
            raise RuntimeError("Browser target management is unavailable")
        result = await self.browser.call("Target.createTarget", {"url": "about:blank"})
        info = await self.browser.call("Target.getTargetInfo", {"targetId": result["targetId"]})
        await self._upsert(info["targetInfo"])
        self.select(result["targetId"])
        return self.current

    async def remove(self, page_id: str) -> None:
        page = self.items.pop(page_id, None)
        if page is None:
            return
        page.sinks.clear()
        page.last_frame = None
        page.unsubscribe_metadata()
        await page.cdp.close()
        if not self.items and not self._closed and self.browser is not None and self.browser.is_open:
            await self.create()
        elif self.active_id == page_id:
            self.active_id = page.opener_id if page.opener_id in self.items else next(reversed(self.items), "")
        self.changed()

    async def close_page(self, page_id: str) -> None:
        self.get(page_id)
        if self.browser is None:
            raise RuntimeError("Browser target management is unavailable")
        # Preserve the process/profile when the user closes the final tab.
        if len(self.items) == 1:
            await self.create()
        result = await self.browser.call("Target.closeTarget", {"targetId": page_id})
        if not result.get("success"):
            raise RuntimeError("Could not close browser page")
        await self.remove(page_id)

    async def close(self) -> None:
        await self.stop_tracking()
        try:
            for page in list(self.items.values()):
                page.sinks.clear()
                page.last_frame = None
                page.unsubscribe_metadata()
                await page.cdp.close()
        finally:
            if self.browser is not None:
                await self.browser.close()

    async def stop_tracking(self) -> None:
        """Stop target recovery before Browser.close starts destroying pages."""
        self._closed = True
        self._unsubscribe()
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        while not self._events.empty():
            self._events.get_nowait()
            self._events.task_done()
