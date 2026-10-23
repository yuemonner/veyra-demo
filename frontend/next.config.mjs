// The /local-api proxy exists only in `next dev` on a developer's machine. Production builds never
// proxy to localhost; they call NEXT_PUBLIC_API_URL (a public HTTPS engine) directly.
const localProxy = process.env.NODE_ENV !== 'production' && process.env.NEXT_PUBLIC_ENABLE_RUNTIME === 'true';

export default {
  // A second local preview (the private instance, port 3029) builds into its own folder so both can run at once.
  distDir: process.env.VEYRA_NEXT_DIST || '.next',
  // The local agent can take a few minutes per case; the default 30 s proxy timeout would cut it off.
  experimental: {proxyTimeout: 600000},
  async rewrites() {
    return localProxy ? [{source: '/local-api/:path*', destination: `${process.env.VEYRA_LOCAL_API || 'http://127.0.0.1:8019'}/:path*`}] : [];
  },
};
