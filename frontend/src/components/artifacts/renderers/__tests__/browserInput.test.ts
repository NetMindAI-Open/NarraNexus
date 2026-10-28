/** @file_name: browserInput.test.ts
 * @description: Letterboxing must not offset remote pointer events.
 */
import { describe, expect, test } from 'vitest';
import { remotePoint } from '../browserInput';
describe('remotePoint', () => {
  const rect = { left: 10, top: 20, width: 400, height: 600 };
  test('maps image center and edges, excluding vertical letterboxing', () => {
    expect(remotePoint(rect, 1280, 800, 210, 320)).toEqual({ x: 640, y: 400 });
    expect(remotePoint(rect, 1280, 800, 10, 195)).toEqual({ x: 0, y: 0 });
    expect(remotePoint(rect, 1280, 800, 210, 100)).toBeNull();
  });
  test('excludes horizontal letterboxing and zero-sized layouts', () => {
    expect(remotePoint({ left: 0, top: 0, width: 800, height: 200 }, 1280, 800, 10, 100)).toBeNull();
    expect(remotePoint({ ...rect, width: 0 }, 1280, 800, 10, 20)).toBeNull();
  });
  test('maps scaled screencast pixels to the reported page coordinate dimensions', () => {
    expect(remotePoint(rect, 1280, 800, 210, 320, { width: 640, height: 400 })).toEqual({ x: 320, y: 200 });
    expect(remotePoint(rect, 1280, 800, 10, 195, { width: 640, height: 400 })).toEqual({ x: 0, y: 0 });
    expect(remotePoint(rect, 1280, 800, 210, 100, { width: 640, height: 400 })).toBeNull();
  });
});
