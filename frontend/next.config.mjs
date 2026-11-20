// The /local-api proxy exists only in `next dev` on a developer's machine. Production builds never
// proxy to localhost; they call NEXT_PUBLIC_API_URL (a public HTTPS engine) directly.
const localProxy = process.env.NODE_ENV !== 'production' && process.env.NEXT_PUBLIC_ENABLE_RUNTIME === 'true';
const isProduction = process.env.NODE_ENV === 'production';

export default {
  // A second local preview (the private instance, port 3029) builds into its own folder so both can run at once.
  distDir: process.env.VEYRA_NEXT_DIST || '.next',
  // The local agent can take a few minutes per case; the default 30 s proxy timeout would cut it off.
  // Recordings are uploaded through the same local proxy; without this it cuts request bodies at 10 MB (the engine's own
  // limit is VEYRA_MAX_RECORDING_MB, 4096 MB by default).
  experimental: {proxyTimeout: 600000, middlewareClientMaxBodySize: '4096mb'},
  async rewrites() {
    return {
      // The old public demo (/field-case, shared widely with customers) now serves the current demo at the same address,
      // before any page file is looked at. Production builds only: the private instance runs `next dev` and keeps the
      // case workspace.
      beforeFiles: isProduction ? [{source: '/field-case', destination: '/system-investigation'}] : [],
      afterFiles: localProxy ? [{source: '/local-api/:path*', destination: `${process.env.VEYRA_LOCAL_API || 'http://127.0.0.1:8019'}/:path*`}] : [],
      fallback: [],
    };
  },
  async headers() {
    // Always fetch the demo page fresh, so a visitor never sees a cached older version first.
    const fresh = [{key: 'Cache-Control', value: 'no-cache, no-store, must-revalidate'}];
    return [{source: '/field-case', headers: fresh}, {source: '/system-investigation', headers: fresh}, {source: '/', headers: fresh}];
  },
};
