import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LcaApplication } from './LcaApplication';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('uses verified server project context without a demo fallback', async () => {
  const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    calls.push(path);
    return new Response(JSON.stringify(path === '/api/lca/context' ? {project_id:'verified-project',available:true} : []), {status:200});
  }));
  render(<LcaApplication />);
  expect(await screen.findByRole('heading', {name:'Debug flows'})).toBeVisible();
  await waitFor(() => expect(calls).toContain('/api/projects/verified-project/lca/flows'));
});
it('does not launch real workspace when authorized gateway is unavailable', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({project_id:'verified-project',available:false}), {status:200})));
  render(<LcaApplication />);
  expect(await screen.findByRole('alert')).toHaveTextContent('unavailable');
  expect(screen.queryByRole('heading',{name:'Debug flows'})).not.toBeInTheDocument();
});
