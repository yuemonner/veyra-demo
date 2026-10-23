// Keeps the hosted demo engine awake: Render's free plan sleeps after 15 idle minutes.
// A cron trigger calls the public /health endpoint every 10 minutes. No secrets, no data.
const TARGET = "https://veyra-demo-api.onrender.com/health";

async function ping() {
  const started = Date.now();
  try {
    const response = await fetch(TARGET, { headers: { "user-agent": "veyra-keepalive" }, cf: { cacheTtl: 0 } });
    return { status: response.status, ms: Date.now() - started };
  } catch (error) {
    return { status: 0, error: String(error), ms: Date.now() - started };
  }
}

export default {
  async scheduled(_event, _env, ctx) {
    ctx.waitUntil(ping().then((result) => console.log(JSON.stringify({ target: TARGET, ...result }))));
  },
  // Visiting the worker URL runs one ping and shows the result, for checking it works.
  async fetch() {
    return new Response(JSON.stringify({ target: TARGET, ...(await ping()) }), { headers: { "content-type": "application/json" } });
  },
};
