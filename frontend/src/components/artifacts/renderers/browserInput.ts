/** @file_name: browserInput.ts
 * @description: Convert panel coordinates and modifiers to Chromium input.
 */
export function remotePoint(
  rect: { left: number; top: number; width: number; height: number },
  width: number, height: number, clientX: number, clientY: number,
  viewport = { width, height },
) {
  if (!rect.width || !rect.height || !width || !height) return null;
  const scale = Math.min(rect.width / width, rect.height / height);
  const x = (clientX - rect.left - (rect.width - width * scale) / 2) / scale;
  const y = (clientY - rect.top - (rect.height - height * scale) / 2) / scale;
  if (x < 0 || y < 0 || x >= width || y >= height) return null;
  return {
    x: Math.min(viewport.width - 1, Math.round(x * viewport.width / width)),
    y: Math.min(viewport.height - 1, Math.round(y * viewport.height / height)),
  };
}

export function inputModifiers(event: { altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }) {
  return (event.altKey ? 1 : 0) | (event.ctrlKey ? 2 : 0) | (event.metaKey ? 4 : 0) | (event.shiftKey ? 8 : 0);
}
