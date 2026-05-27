/**
 * @file_name: FileUpload.test.tsx
 * @description: Pins the workspace tree default-expansion behavior.
 *
 * Symptom this guards against: P0 bug "本地版本：workspace 里面现在能够显示
 * folder了，但是 sub-folder 还是会被忽略" (2026-05-18, Xinyao Hu).
 *
 * Backend already returns the full recursive tree; the bug surface was the
 * frontend defaulting only depth=0 to expanded, so depth=1 sub-folders
 * showed only their name with no contents — easy to misread as "ignored".
 * Fix: all folders default to expanded, since the data is already there.
 */
import { describe, expect, test } from 'vitest';
import { render } from '@testing-library/react';
import type { FileInfo } from '@/types';

import { TreeNode } from '../FileUpload';

function makeTree(): FileInfo {
  return {
    name: 'reports',
    path: 'reports',
    is_dir: true,
    size: 0,
    modified_at: '0',
    children: [
      {
        name: 'q1',
        path: 'reports/q1',
        is_dir: true,
        size: 0,
        modified_at: '0',
        children: [
          {
            name: 'sales.csv',
            path: 'reports/q1/sales.csv',
            is_dir: false,
            size: 100,
            modified_at: '0',
            children: null,
          },
        ],
      },
      {
        name: 'README.md',
        path: 'reports/README.md',
        is_dir: false,
        size: 50,
        modified_at: '0',
        children: null,
      },
    ],
  };
}

describe('TreeNode default expansion', () => {
  test('sub-folder contents are visible by default — no manual expand needed', () => {
    const { getByText } = render(
      <TreeNode
        node={makeTree()}
        depth={0}
        onDelete={() => {}}
        onPreview={() => {}}
        onRegister={() => {}}
        agentId="agent_x"
        userId="user_y"
      />,
    );

    // Top-level folder name visible
    expect(getByText('reports')).toBeTruthy();
    // depth=1 sub-folder name visible
    expect(getByText('q1')).toBeTruthy();
    // depth=2 file inside sub-folder is the bug surface: pre-fix this was
    // hidden because depth=1 folder defaulted to collapsed.
    expect(getByText('sales.csv')).toBeTruthy();
    // sibling file at depth=1
    expect(getByText('README.md')).toBeTruthy();
  });
});
