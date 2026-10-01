export const EFFECTIVE = "30 September 2026";
const CONTACT = `<a href="https://github.com/MEzombiOfficial1" target="_blank" rel="noopener">github.com/MEzombiOfficial1</a>`;

export const FAN_DISCLAIMER =
  "This material is unofficial and is not endorsed by Supercell. For more information see Supercell's Fan Content Policy: " +
  '<a href="https://www.supercell.com/fan-content-policy" target="_blank" rel="noopener">www.supercell.com/fan-content-policy</a>.';

export const PAGES = {
  terms: {
    title: "Terms of Service",
    html: `
<p class="muted">Effective ${EFFECTIVE}</p>
<p>These Terms govern your use of Ranked Draft Lab (the "Site"). By using the Site you agree to them. If you do not agree, do not use the Site.</p>

<h3>1. What the Site is</h3>
<p>The Site is a free, non-commercial fan project that shows statistics about Brawl Stars Ranked games and suggests draft picks.
It is not affiliated with, endorsed, sponsored or approved by Supercell. ${FAN_DISCLAIMER}</p>

<h3>2. No guarantees</h3>
<p>Statistics, tier lists, win chances, pick suggestions and AI coach answers are <strong>estimates</strong> computed from sampled public game data and
automated models. They can be incomplete, outdated or wrong. The Site is provided <strong>"as is" and "as available"</strong>, without warranties of any
kind, express or implied, including fitness for a particular purpose, accuracy or availability. You decide how to play; any result in the game is your own responsibility.</p>

<h3>3. AI-generated content</h3>
<p>Some text (the AI coach and map notes) is generated automatically by third-party AI models (Cloudflare Workers AI and, where available, GitHub Models).
It may contain mistakes and does not represent the views of the operator or of Supercell.</p>

<h3>4. Acceptable use</h3>
<p>Do not misuse the Site. In particular, do not: overload or attack it; scrape it at high volume; try to bypass its limits; use it to harass
or track other players; use it in any way that breaks the law or Supercell's Terms of Service. We may block access that abuses the Site.</p>

<h3>5. Player tags</h3>
<p>You may only look up player tags for the purpose of seeing which brawlers that account can play in a draft.
Player data comes from Supercell's official public API. See the <a href="#/privacy">Privacy Policy</a>.</p>

<h3>6. Intellectual property</h3>
<p>Brawl Stars, its names, characters, maps and artwork are trademarks and copyrighted material of Supercell Oy. Brawler and map images
are provided through <a href="https://brawlify.com" target="_blank" rel="noopener">Brawlify</a>. The Site's own code and statistics layout
belong to the operator and are proprietary (all rights reserved): you may not copy, scrape, re-upload, mirror or reuse them, including the
statistics and models, without written permission. Nothing on the Site grants you rights to Supercell's content.</p>

<h3>7. Limitation of liability</h3>
<p>To the maximum extent permitted by law, the operator is not liable for any indirect, incidental, special or consequential damages, or for any loss
of data, progress, rank, trophies or in-game items arising from use of or inability to use the Site. Where liability cannot be excluded, it is limited to
the amount you paid to use the Site (which is zero). Nothing in these Terms limits rights you have under mandatory consumer law.</p>

<h3>8. Availability and changes</h3>
<p>The Site may change, pause or shut down at any time without notice. We may update these Terms; the effective date above shows the latest version.
Continuing to use the Site after a change means you accept the updated Terms.</p>

<h3>9. Takedown and contact</h3>
<p>If you are a rights holder (including Supercell) and want content removed, or have any other request, contact the operator via ${CONTACT}.
We will respond promptly.</p>`,
  },

  privacy: {
    title: "Privacy Policy",
    html: `
<p class="muted">Effective ${EFFECTIVE}</p>
<p>Ranked Draft Lab is built to collect as little personal data as possible. There are <strong>no accounts, no advertising, no analytics and no tracking cookies</strong>.</p>

<h3>1. Data stored in your browser</h3>
<p>The Site saves your draft, settings, search text and (if you enter one) your player tag and its brawler list in your browser's
<em>local storage</em>, so they are still there when you come back. This data stays on your device and is never sent to us for storage.
You can delete it at any time with the button below or by clearing your browser's site data.</p>

<h3>2. Player tag lookup</h3>
<p>When you press <em>Load</em> for a player tag, the tag is sent to our server (a Cloudflare Worker), which requests the public profile for that tag from
Supercell's official Brawl Stars API (through the RoyaleAPI proxy) and returns the brawler list to your browser. The response is cached for up to
5 minutes to reduce load. We do not keep a log of which tags you look up.</p>

<h3>3. AI coach</h3>
<p>When you press <em>AI coach</em>, the current draft (map, bans, picks and statistics, but no player tag or personal data) is sent to Cloudflare Workers AI
to generate an answer. It is not stored by us.</p>

<h3>4. Game statistics we collect</h3>
<p>To build statistics, an automated collector reads <strong>public</strong> Ranked battle logs and player profiles from Supercell's official API. This includes public
in-game player tags and names, brawlers used, rank, results, and owned brawler items. It is stored in database files attached to the project's GitHub repository and used to compute
aggregate statistics (for example win rates per map). If that repository is public, these files can be downloaded; they contain only
information that Supercell's public API already provides to anyone. Per-game detail is deleted after about 60 days; only aggregate statistics are kept longer. The Site shows only aggregates, never individual players' match histories. Inactive players are removed from the collection list
automatically. If you want your player tag excluded or deleted, contact us (see below) and we will remove it.</p>

<h3>5. Service providers</h3>
<ul>
  <li><strong>Cloudflare</strong> hosts the Site and runs the AI coach. Like any web host, it processes your IP address and request details to deliver pages and protect
  against abuse (<a href="https://www.cloudflare.com/privacypolicy/" target="_blank" rel="noopener">Cloudflare privacy policy</a>).</li>
  <li><strong>Google Fonts</strong> serves the fonts; your browser requests them from Google (<a href="https://policies.google.com/privacy" target="_blank" rel="noopener">Google privacy policy</a>).</li>
  <li><strong>Supercell</strong> (via the RoyaleAPI proxy) provides player and battle data (<a href="https://supercell.com/en/privacy-policy/" target="_blank" rel="noopener">Supercell privacy policy</a>).</li>
  <li><strong>GitHub</strong> runs the data collector and stores the statistics.</li>
</ul>

<h3>6. Children</h3>
<p>The Site is a general-audience tool and does not knowingly collect personal information from children. Nothing requires you to enter personal information.
A player tag is a public in-game identifier, not a name, email or password. Never enter your Supercell ID email or login details anywhere on this Site; we will never ask for them.</p>

<h3>7. Your rights</h3>
<p>Depending on where you live (for example under the GDPR or CCPA), you may have the right to access, correct or delete personal data about you, or to object to its
processing. Because we hold almost nothing, most requests are resolved by clearing your browser data. For collected game statistics, contact us and we will delete
data linked to your player tag.</p>

<h3>8. Contact and changes</h3>
<p>Contact the operator via ${CONTACT}. We may update this policy; the effective date above shows the latest version.</p>
<p><button class="btn" id="clear-local">Delete all data this site stored in my browser</button> <span id="clear-msg" class="small muted"></span></p>`,
  },

  legal: {
    title: "Legal & credits",
    html: `
<h3>Fan content disclaimer</h3>
<p>${FAN_DISCLAIMER}</p>
<p>Ranked Draft Lab is a free fan project and is not affiliated with, endorsed, sponsored, or specifically approved by Supercell, and Supercell is not
responsible for it. Brawl Stars and all related names, characters, maps and artwork are the property of Supercell Oy.</p>

<h3>Credits</h3>
<ul>
  <li>Game data: <a href="https://developer.brawlstars.com" target="_blank" rel="noopener">Brawl Stars API</a> by Supercell, accessed through the
  <a href="https://docs.royaleapi.com" target="_blank" rel="noopener">RoyaleAPI</a> proxy.</li>
  <li>Brawler and map images: <a href="https://brawlify.com" target="_blank" rel="noopener">Brawlify</a>.</li>
  <li>Brawler classes and stats: <a href="https://brawlstars.fandom.com" target="_blank" rel="noopener">Brawl Stars Fandom wiki</a>, used under
  <a href="https://creativecommons.org/licenses/by-sa/3.0/" target="_blank" rel="noopener">CC BY-SA</a>.</li>
  <li>AI: Cloudflare Workers AI (Meta Llama models) and GitHub Models.</li>
</ul>

<h3>How the numbers work</h3>
<p>Win rates are Bayesian-smoothed: brawlers with few games are pulled toward their overall mode win rate, so small samples cannot top the lists by luck.
Draft win chances combine map strength, teammate synergy and counter matchups. All values are estimates, not guarantees.</p>

<p><a href="#/terms">Terms of Service</a> · <a href="#/privacy">Privacy Policy</a></p>`,
  },
};
