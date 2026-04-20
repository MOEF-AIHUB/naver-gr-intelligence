/**
 * LEGi-AI assistant panel.
 * Usage:
 *   <script src="/legi-ai/web/ai-panel.js"></script>
 *   <script>LegiAI.mount({ apiBase: "http://localhost:8000", apiKey: "..." });</script>
 */
(function (global) {
  const STYLES = `
  .legi-ai-fab{position:fixed;bottom:24px;right:24px;z-index:900;background:linear-gradient(135deg,#03C75A,#00A847);color:#fff;border:none;border-radius:999px;padding:12px 18px;font-weight:800;font-size:12px;box-shadow:0 8px 24px rgba(3,199,90,.35);cursor:pointer}
  .legi-ai-panel{position:fixed;top:0;right:-420px;width:420px;height:100vh;background:#111E30;border-left:1px solid #1E3048;color:#D8E8F5;font-family:-apple-system,"Malgun Gothic","맑은 고딕",sans-serif;z-index:950;display:flex;flex-direction:column;transition:right .25s ease}
  .legi-ai-panel.on{right:0}
  .legi-ai-hdr{padding:12px 16px;border-bottom:1px solid #1E3048;display:flex;align-items:center;justify-content:space-between;font-weight:800}
  .legi-ai-hdr .close{background:none;border:none;color:#5A7A9A;font-size:18px;cursor:pointer}
  .legi-ai-body{flex:1;overflow-y:auto;padding:14px 16px;font-size:12.5px;line-height:1.6}
  .legi-ai-msg{padding:10px 12px;border-radius:8px;margin-bottom:10px;white-space:pre-wrap;word-break:break-word}
  .legi-ai-msg.user{background:rgba(3,199,90,.1);border:1px solid rgba(3,199,90,.3)}
  .legi-ai-msg.assistant{background:#162030;border:1px solid #1E3048}
  .legi-ai-src{font-size:10px;color:#5A7A9A;margin-top:6px}
  .legi-ai-src a{color:#03C75A;text-decoration:underline;cursor:pointer}
  .legi-ai-input{border-top:1px solid #1E3048;padding:10px;display:flex;gap:6px}
  .legi-ai-input textarea{flex:1;background:#0A1628;border:1px solid #1E3048;border-radius:6px;color:#D8E8F5;padding:8px;font-size:12px;resize:none;min-height:36px;max-height:100px;font-family:inherit}
  .legi-ai-input button{background:#03C75A;border:none;border-radius:6px;padding:0 14px;color:#fff;font-weight:700;cursor:pointer}
  .legi-ai-input button[disabled]{opacity:.5;cursor:wait}
  .legi-ai-loading{color:#5A7A9A;font-size:11px;font-style:italic}
  .legi-ai-err{color:#D94F3B;font-size:11px;margin-top:6px}
  @media(max-width:640px){.legi-ai-panel{width:100vw;right:-100vw}}
  `;

  let cfg = { apiBase: "http://localhost:8000", apiKey: "", threadId: "legi-" + Date.now() };
  let history = [];

  function el(tag, attrs = {}, children = []) {
    const e = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => {
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v);
    });
    children.forEach((c) => e.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return e;
  }

  function renderMarkdown(text) {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[(\d+)\]/g, '<a class="legi-ai-cite" data-n="$1">[$1]</a>')
      .replace(/\n/g, "<br/>");
  }

  function scrollBody(body) {
    body.scrollTop = body.scrollHeight;
  }

  async function sendQuery(body, input, btn) {
    const q = input.value.trim();
    if (!q) return;
    input.value = "";
    btn.disabled = true;

    const userMsg = el("div", { class: "legi-ai-msg user" }, [q]);
    body.appendChild(userMsg);

    const asst = el("div", { class: "legi-ai-msg assistant" });
    const loading = el("div", { class: "legi-ai-loading" }, ["분석 중…"]);
    asst.appendChild(loading);
    body.appendChild(asst);
    scrollBody(body);

    try {
      const resp = await fetch(cfg.apiBase + "/api/v1/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(cfg.apiKey ? { "X-LEGi-Key": cfg.apiKey } : {}),
        },
        body: JSON.stringify({ query: q, thread_id: cfg.threadId }),
      });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();
      asst.innerHTML = renderMarkdown(data.answer || "(응답 없음)");
      history.push({ q, a: data.answer });
    } catch (err) {
      asst.innerHTML = "";
      asst.appendChild(el("div", { class: "legi-ai-err" }, ["오류: " + err.message]));
    } finally {
      btn.disabled = false;
      scrollBody(body);
    }
  }

  function mount(options = {}) {
    cfg = { ...cfg, ...options };
    const style = el("style");
    style.textContent = STYLES;
    document.head.appendChild(style);

    const panel = el("div", { class: "legi-ai-panel" });
    const body = el("div", { class: "legi-ai-body" });
    const closeBtn = el("button", { class: "close", onclick: () => panel.classList.remove("on") }, ["×"]);
    const hdr = el("div", { class: "legi-ai-hdr" }, ["LEGi AI 어시스턴트", closeBtn]);

    const textarea = el("textarea", { placeholder: "의안·의원·법령·쟁점을 질문하세요. (Enter 전송, Shift+Enter 줄바꿈)" });
    const sendBtn = el("button", {}, ["전송"]);
    const input = el("div", { class: "legi-ai-input" }, [textarea, sendBtn]);

    sendBtn.addEventListener("click", () => sendQuery(body, textarea, sendBtn));
    textarea.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendQuery(body, textarea, sendBtn);
      }
    });

    panel.appendChild(hdr);
    panel.appendChild(body);
    panel.appendChild(input);

    const fab = el(
      "button",
      {
        class: "legi-ai-fab",
        onclick: () => {
          panel.classList.toggle("on");
          if (panel.classList.contains("on")) textarea.focus();
        },
      },
      ["AI 어시스턴트"]
    );

    document.body.appendChild(fab);
    document.body.appendChild(panel);

    body.addEventListener("click", (e) => {
      if (e.target && e.target.classList && e.target.classList.contains("legi-ai-cite")) {
        const n = e.target.getAttribute("data-n");
        window.dispatchEvent(new CustomEvent("legi-ai-cite-click", { detail: { n } }));
      }
    });
  }

  global.LegiAI = { mount };
})(window);
