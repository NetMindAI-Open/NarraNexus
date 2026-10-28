"""
@file_name: edit_bridge.py
@author: NetMind.AI
@date: 2026-08-19
@description: The per-element edit bridge for html artifacts (spec A §3.3).

The artifact renders in a sandboxed iframe; editing gestures happen INSIDE
that document, so the public raw route injects this script into the ENTRY
html when the viewer asks for it (?edit_bridge=1). The bridge is a thin
sensor:

  - click on a text leaf (children are text / inline format tags only) →
    that one element becomes contentEditable;
  - Enter inserts a soft <br> (structural splits are the AI's job);
    Cmd/Ctrl+B / I toggle inline bold/italic within the element;
  - blur → if the innerHTML changed, postMessage
    {type, innerBefore, innerAfter, outerBefore} to the parent window.

It carries NO write-back logic and NO secrets: the parent (HtmlRenderer)
maps the edit onto the source via anchored literal replace and commits it
through the authenticated PUT /content pipeline. The entry CSP already
allows 'unsafe-inline' scripts, and the iframe sandbox is unchanged.
"""

from __future__ import annotations

import re

# Marker doubles as the postMessage type prefix and the injection test hook.
BRIDGE_MARKER = "narra-edit-bridge"

EDIT_BRIDGE_JS = r"""
/* narra-edit-bridge */
(function () {
  'use strict';
  var INLINE = { B:1, I:1, STRONG:1, EM:1, SPAN:1, A:1, CODE:1, BR:1, U:1, S:1, SMALL:1, MARK:1, SUB:1, SUP:1 };
  var BLOCKED = /^(SCRIPT|STYLE|INPUT|TEXTAREA|SELECT|BUTTON|IFRAME|SVG|CANVAS|VIDEO|AUDIO|IMG|HTML|BODY|HEAD|A)$/;

  function onlyInlineChildren(el) {
    for (var n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3 || n.nodeType === 8) continue;
      if (n.nodeType !== 1) return false;
      if (!INLINE[n.tagName]) return false;
      if (!onlyInlineChildren(n)) return false;
    }
    return true;
  }

  function isEditableLeaf(el) {
    if (!el || el.nodeType !== 1) return false;
    if (BLOCKED.test(el.tagName)) return false;
    if (!el.textContent || !el.textContent.trim()) return false;
    return onlyInlineChildren(el);
  }

  var active = null;
  var innerBefore = '';
  var outerBefore = '';

  function finish() {
    if (!active) return;
    var el = active;
    active = null;
    el.removeAttribute('contenteditable');
    el.style.outline = '';
    var innerAfter = el.innerHTML;
    if (innerAfter === innerBefore) return;
    window.parent.postMessage({
      type: 'narra-edit-bridge:edit',
      innerBefore: innerBefore,
      innerAfter: innerAfter,
      outerBefore: outerBefore
    }, '*');
  }

  document.addEventListener('click', function (e) {
    var el = e.target;
    while (el && el !== document.body && !isEditableLeaf(el)) el = el.parentElement;
    if (!el || el === document.body) return;
    if (active === el) return;
    finish();
    active = el;
    innerBefore = el.innerHTML;
    outerBefore = el.outerHTML;
    el.setAttribute('contenteditable', 'true');
    el.style.outline = '1px dashed rgba(245, 158, 11, 0.8)';
    el.focus();
  }, true);

  document.addEventListener('keydown', function (e) {
    if (!active) return;
    if (e.key === 'Escape') { e.preventDefault(); active.blur(); return; }
    if (e.key === 'Enter') {
      /* Soft line break within the element. A structural split (new
         paragraph/element) is not well-posed in arbitrary CSS — that is the
         AI channel's job. */
      e.preventDefault();
      document.execCommand('insertHTML', false, '<br>');
      return;
    }
    if ((e.metaKey || e.ctrlKey) && (e.key === 'b' || e.key === 'i')) {
      e.preventDefault();
      document.execCommand(e.key === 'b' ? 'bold' : 'italic');
    }
  }, true);

  document.addEventListener('blur', function (e) {
    if (active && e.target === active) finish();
  }, true);
})();
"""

_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)


def inject_edit_bridge(html: str) -> str:
    """Return ``html`` with the bridge script injected before </body> (or
    appended when the document has no body close tag — browsers still run
    it)."""
    script = f"<script>{EDIT_BRIDGE_JS}</script>"
    match = _BODY_CLOSE_RE.search(html)
    if match:
        return html[: match.start()] + script + html[match.start():]
    return html + script
