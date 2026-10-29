// Finder bot relay (Cloudflare Worker, free plan).
// Telegram calls this instantly for every message you send your bot; it acknowledges you and starts the
// "Finder bot" GitHub Action with your message, which replies with the tailored resume + referral pack.
//
// Worker secrets (Settings -> Variables and Secrets):
//   TELEGRAM_BOT_TOKEN  your bot token
//   TELEGRAM_CHAT_ID    5450865509 (only this chat is served)
//   WEBHOOK_SECRET      any random string; the same one goes into setWebhook
//   GITHUB_TOKEN        fine-grained token for GHANGHASASHWANI/Finder with "Contents: Read and write"
//   GITHUB_REPO         GHANGHASASHWANI/Finder

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("Finder bot relay is running.");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const update = await request.json().catch(() => ({}));
    const msg = update.message || {};
    const text = msg.text || msg.caption || "";
    if (String((msg.chat || {}).id) !== String(env.TELEGRAM_CHAT_ID) || !text) {
      return new Response("ok");          // not you, or not a text message: ignore
    }
    const say = (t) => fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text: t, disable_web_page_preview: true }),
    });
    const links = (text.match(/https?:\/\/\S+/g) || []).length;
    if (!links) {
      await say("Send me a job link (or several in one message) and I'll reply with a tailored resume PDF and referral messages in about 3 minutes.");
      return new Response("ok");
    }
    const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`, "Accept": "application/vnd.github+json",
        "User-Agent": "finder-bot-relay", "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ event_type: "job-link", client_payload: { text: text.slice(0, 4000), update_id: update.update_id } }),
    });
    await say(r.ok
      ? `Got it - working on ${links} job${links > 1 ? "s" : ""}. Resume + referral messages in about 3 minutes.`
      : `Couldn't start the GitHub job (HTTP ${r.status}). Check the GITHUB_TOKEN secret in Cloudflare.`);
    return new Response("ok");
  },
};
