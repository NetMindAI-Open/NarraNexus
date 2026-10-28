"""
@file_name: actions.py
@date: 2026-09-23
@description: Validated browser gestures with fixed, parameterized DOM operations.
"""
from __future__ import annotations

import json
import math

from narranexus.platform.browser._browser_impl.cdp import input_events_for
from narranexus.platform.browser._browser_impl.limits import MAX_FIELD_TEXT_CHARS, check_selector, check_text


# Only this function body is executable. Caller-supplied selectors and values
# are JSON arguments, never interpolated as JavaScript source fragments.
_ACTION_SCRIPT = r"""(args => {
  const el = args.selector ? document.querySelector(args.selector) : null;
  if (args.selector && !el) return {error: 'Element not found'};
  if (el) {
    if (el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true')
      return {error: 'Element is disabled'};
    if (el.matches('input[type=file]')) return {error: 'File inputs are not supported'};
    el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
    const style = getComputedStyle(el);
    if (!el.getClientRects().length || style.visibility !== 'visible')
      return {error: 'Element is not visible'};
  }
  if (args.action === 'fill') {
    if (el.readOnly) return {error: 'Element is read-only'};
    el.focus();
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
      if (el instanceof HTMLInputElement &&
          ['checkbox', 'radio', 'button', 'submit', 'reset', 'image', 'hidden'].includes(el.type))
        return {error: 'Element is not a text input'};
      const proto = el instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, args.text);
      if (el.value !== args.text) return {error: 'Input rejected the supplied value'};
    } else if (el.isContentEditable) {
      el.textContent = args.text;
    } else return {error: 'Element is not editable'};
    el.dispatchEvent(new InputEvent('input', {bubbles: true, composed: true,
      inputType: 'insertReplacementText', data: args.text}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return {filled: true};
  }
  if (args.action === 'select') {
    if (!(el instanceof HTMLSelectElement)) return {error: 'Element is not a select'};
    const option = Array.from(el.options).find(item => item.value === args.value);
    if (!option) return {error: 'Option value not found'};
    if (option.disabled || option.parentElement.matches('optgroup:disabled'))
      return {error: 'Option is disabled'};
    el.focus();
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(el, args.value);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return {selected: args.value};
  }
  if (args.action === 'press') {
    if (el) {
      el.focus();
      if (document.activeElement !== el && !el.contains(document.activeElement))
        return {error: 'Element cannot receive keyboard input'};
    }
    return {focused: true};
  }
  let x = args.x, y = args.y;
  if (el) {
    const rect = el.getBoundingClientRect();
    const left = Math.max(0, rect.left), right = Math.min(innerWidth, rect.right);
    const top = Math.max(0, rect.top), bottom = Math.min(innerHeight, rect.bottom);
    if (left >= right || top >= bottom) return {error: 'Element is outside the viewport'};
    x = (left + right) / 2; y = (top + bottom) / 2;
    const hit = document.elementFromPoint(x, y);
    if (args.action === 'click' && hit !== el && !el.contains(hit))
      return {error: 'Element is covered by another element'};
  } else if (x == null) {
    x = innerWidth / 2; y = innerHeight / 2;
  }
  if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight)
    return {error: 'Coordinates are outside the viewport'};
  return {x, y};
})"""

_KEYS = {
    'Enter': ('Enter', 13), 'Tab': ('Tab', 9), 'Escape': ('Escape', 27),
    'Backspace': ('Backspace', 8), 'Delete': ('Delete', 46), 'Insert': ('Insert', 45),
    'ArrowLeft': ('ArrowLeft', 37), 'ArrowUp': ('ArrowUp', 38),
    'ArrowRight': ('ArrowRight', 39), 'ArrowDown': ('ArrowDown', 40),
    'Home': ('Home', 36), 'End': ('End', 35),
    'PageUp': ('PageUp', 33), 'PageDown': ('PageDown', 34),
    ' ': ('Space', 32),
    **{f'F{i}': (f'F{i}', 111 + i) for i in range(1, 13)},
}
_MODIFIERS = {'Alt': 1, 'Control': 2, 'Meta': 4, 'Shift': 8}


def key_events(key: str) -> list[tuple[str, dict]]:
    """A complete key press, with optional Control/Alt/Meta/Shift chord."""
    if not isinstance(key, str) or not key:
        raise ValueError('press requires a key')
    parts = key.split('+')
    if key.endswith('+'):
        parts = parts[:-2] + ['+']
    modifiers = 0
    for modifier in parts[:-1]:
        if modifier not in _MODIFIERS:
            raise ValueError(f'Unknown key modifier: {modifier}')
        modifiers |= _MODIFIERS[modifier]
    name = parts[-1]
    if name == 'Space':
        name = ' '
    code, vk = _KEYS.get(name, ('', 0))
    if not code:
        if len(name) != 1 or not name.isprintable():
            raise ValueError(f'Unsupported key: {name}')
        if name.isascii() and name.isalpha():
            code, vk = f'Key{name.upper()}', ord(name.upper())
        elif name.isascii() and name.isdigit():
            code, vk = f'Digit{name}', ord(name)
    event = {
        'kind': 'key', 'key': name, 'code': code, 'windowsVirtualKeyCode': vk,
        'modifiers': modifiers, 'text': '\r' if name == 'Enter' else name if len(name) == 1 else '',
    }
    return [
        *input_events_for({**event, 'type': 'keyDown'}),
        *input_events_for({**event, 'type': 'keyUp'}),
    ]


def action_expression(action: str, **args) -> str:
    """Validate the tool arguments before any DOM mutation or input dispatch."""
    allowed = {
        'click': {'selector', 'x', 'y'}, 'fill': {'selector', 'text'},
        'select': {'selector', 'value'}, 'press': {'selector', 'key'},
        'scroll': {'selector', 'x', 'y', 'delta_x', 'delta_y'},
    }
    if not isinstance(action, str) or action not in allowed:
        raise ValueError('action must be click, fill, select, press, or scroll')
    for name, value in args.items():
        if value is not None and name not in allowed[action]:
            raise ValueError(f'{name} is not supported for {action}')
    selector = args.get('selector')
    check_selector(selector)
    for name in ('text', 'value'):
        check_text(name, args.get(name), MAX_FIELD_TEXT_CHARS)
    for name in ('x', 'y', 'delta_x', 'delta_y'):
        value = args.get(name)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError(f'{name} must be a finite number')
    x, y = args.get('x'), args.get('y')
    if (x is None) != (y is None):
        raise ValueError('x and y must be supplied together')
    if selector is not None and x is not None:
        raise ValueError('Use either selector or coordinates')
    if action == 'click' and selector is None and x is None:
        raise ValueError('click requires a selector or x and y')
    if action in ('fill', 'select'):
        field = 'text' if action == 'fill' else 'value'
        if selector is None or args.get(field) is None:
            raise ValueError(f'{action} requires selector and {field}')
    if action == 'press':
        key_events(args.get('key'))
    if action == 'scroll' and args.get('delta_x') is None and args.get('delta_y') is None:
        raise ValueError('scroll requires delta_x or delta_y')
    return f'{_ACTION_SCRIPT}({json.dumps({"action": action, **args}, allow_nan=False)})'
