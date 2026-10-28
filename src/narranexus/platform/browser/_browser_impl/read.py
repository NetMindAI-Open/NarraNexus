"""
@file_name: read.py
@author:
@date: 2026-09-22
@description: Fixed, non-mutating DOM snapshots with actionable targets and text pages.

Only JSON parameters vary. BrowserSession gates the current origin; callers
need no arbitrary-script privilege to inspect, narrow or continue a read.
"""
from __future__ import annotations

import json
from narranexus.platform.browser._browser_impl.limits import check_selector

DEFAULT_TEXT_LIMIT = 20_000
MAX_OFFSET = 2**53 - 1

_SNAPSHOT_FUNCTION = r"""
(({selector, offset, limit}) => {
  const root = selector === null ? (document.body || document.documentElement) : document.querySelector(selector);
  if (!root) return {error: 'Read selector matched no element'};
  const text = el => (el.innerText || '').trim();
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) &&
    Array.from(el.getClientRects()).some(r => r.width > 0 && r.height > 0);
  const targets = query => [ ...(root.matches(query) ? [root] : []),
    ...root.querySelectorAll(query) ].filter(visible);
  const uniqueId = el => {
    if (!el.id) return null;
    const candidate = '#' + CSS.escape(el.id);
    return document.querySelectorAll(candidate).length === 1 ? candidate : null;
  };
  const targetSelector = el => {
    const path = [];
    for (let node = el; node; node = node.parentElement) {
      const id = uniqueId(node);
      if (id) { path.unshift(id); break; }
      let index = 1;
      for (let sibling = node.previousElementSibling; sibling; sibling = sibling.previousElementSibling)
        if (sibling.localName === node.localName) index++;
      path.unshift(CSS.escape(node.localName) + ':nth-of-type(' + index + ')');
    }
    return path.join(' > ');
  };
  const label = el => {
    const referenced = clean((el.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map(id => document.getElementById(id)?.textContent || '').join(' '));
    if (referenced) return referenced;
    const aria = clean(el.getAttribute('aria-label'));
    if (aria) return aria;
    const native = clean(Array.from(el.labels || []).map(l => l.textContent).join(' '));
    if (native) return native;
    if (el.matches('input[type=submit],input[type=reset],input[type=button]'))
      return el.value || (el.type === 'submit' ? 'Submit' : el.type === 'reset' ? 'Reset' : '');
    if (el.matches('input[type=image]')) return el.alt || el.title || 'Submit';
    const content = clean(text(el));
    if (content && !el.matches('input,textarea,select,[contenteditable]')) return content;
    return clean(Array.from(el.querySelectorAll('img[alt],svg title'))
      .map(n => n.getAttribute('alt') || n.textContent).join(' ')) ||
      el.getAttribute('title') || el.getAttribute('placeholder') || '';
  };
  const base = el => ({selector: targetSelector(el), label: label(el),
    disabled: el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true'});
  const chars = Array.from(visible(root) ? text(root) : '');
  if (offset > chars.length) return {error: 'Read offset exceeds total; restart at offset 0 after page changes'};
  const end = Math.min(offset + limit, chars.length);
  return {
    url: location.href, title: document.title, selector,
    text: chars.slice(offset, end).join(''), total: chars.length, offset,
    next_offset: end < chars.length ? end : null,
    headings: targets('h1,h2,h3,h4,h5,h6').map(el => ({level: el.tagName, text: text(el), selector: targetSelector(el)})),
    links: targets('a[href],[role=link]').map(el => ({...base(el), text: text(el), href: el.href || ''})),
    buttons: targets('button,input[type=button],input[type=submit],input[type=reset],input[type=image],[role=button]')
      .map(el => ({...base(el), text: text(el), type: el.type || ''})),
    fields: targets('input:not([type=hidden]):not([type=button]):not([type=submit]):not([type=reset]):not([type=image]),textarea,select,[contenteditable=""],[contenteditable=true],[contenteditable=plaintext-only]')
      .map(el => {
        const field = {...base(el), tag: el.localName, type: el.type || '',
          name: el.getAttribute('name') || '', placeholder: el.getAttribute('placeholder') || '',
          required: !!el.required || el.getAttribute('aria-required') === 'true',
          read_only: !!el.readOnly || el.getAttribute('aria-readonly') === 'true'};
        if (el.type === 'password') field.value_redacted = true;
        else field.value = el.isContentEditable ? text(el) : el.value;
        if (el.matches('input[type=checkbox],input[type=radio]')) field.checked = el.checked;
        if (el.localName === 'select') {
          field.multiple = el.multiple;
          field.selected_values = Array.from(el.selectedOptions).map(o => o.value);
          field.options = Array.from(el.options).map(o => ({label: o.label, value: o.value,
            selected: o.selected, disabled: o.disabled || !!o.closest('optgroup[disabled]')}));
        }
        return field;
      }),
  };
})
"""


def snapshot_expression(
    *, selector: str | None = None, offset: int = 0, limit: int = DEFAULT_TEXT_LIMIT,
) -> str:
    """Build one fixed expression; offsets count Unicode code points, not bytes."""
    check_selector(selector)
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise ValueError(f"offset must be an integer between 0 and {MAX_OFFSET}")
    if type(limit) is not int or not 1 <= limit <= DEFAULT_TEXT_LIMIT:
        raise ValueError(f"limit must be an integer between 1 and {DEFAULT_TEXT_LIMIT}")
    arguments = json.dumps({"selector": selector, "offset": offset, "limit": limit}, allow_nan=False)
    return f"{_SNAPSHOT_FUNCTION.strip()}({arguments})"
