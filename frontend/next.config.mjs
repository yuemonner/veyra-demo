// Explicit local preview only. No open proxy or user-supplied upstream.
export default {
  async rewrites() {
    return process.env.NEXT_PUBLIC_ENABLE_RUNTIME === 'true'
      ? [{source: '/local-api/:path*', destination: 'http://127.0.0.1:8019/:path*'}]
      : [];
  },
};
