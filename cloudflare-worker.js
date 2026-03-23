addEventListener('fetch', event => {
  event.respondWith(handleRequest(event.request));
});

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-Assembly-Key, X-BigKinds-Key',
};

async function handleRequest(req) {
  const url = new URL(req.url);
  const path = url.pathname;

  if (req.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS });
  }

  function json(data, status = 200) {
    return new Response(JSON.stringify(data), {
      status,
      headers: { ...CORS, 'Content-Type': 'application/json' },
    });
  }

  // ─── /health ───
  if (path === '/health') {
    return json({ status: 'ok', time: new Date().toISOString() });
  }

  // ─── /assembly/bills ───
  if (path === '/assembly/bills') {
    const key = req.headers.get('X-Assembly-Key') || url.searchParams.get('key') || '';
    if (!key) return json({ error: 'API key required' }, 400);

    const KWS = ['온라인플랫폼', '인공지능', '데이터보호', '저작권', '핀테크', '알고리즘'];
    const seen = new Set();
    const bills = [];

    await Promise.allSettled(KWS.map(async kw => {
      const apiUrl = `https://open.assembly.go.kr/portal/openapi/nzmimeepazxkubdpn`
        + `?KEY=${key}&Type=json&pIndex=1&pSize=15&AGE=22&SEARCH_WORD=${encodeURIComponent(kw)}`;
      try {
        const r = await fetch(apiUrl);
        const d = await r.json();
        const rows = d?.nzmimeepazxkubdpn?.[1]?.row || [];
        rows.forEach(b => {
          if (!seen.has(b.BILL_NO)) { seen.add(b.BILL_NO); bills.push(b); }
        });
      } catch (e) { console.error(kw, e); }
    }));

    bills.sort((a, b) => (b.PROPOSE_DT || '').localeCompare(a.PROPOSE_DT || ''));
    return json({ bills, total: bills.length });
  }

  // ─── /assembly/schedule ───
  if (path === '/assembly/schedule') {
    const key = req.headers.get('X-Assembly-Key') || '';
    if (!key) return json({ error: 'API key required' }, 400);
    const apiUrl = `https://open.assembly.go.kr/portal/openapi/ncryefbycijwazpnb`
      + `?KEY=${key}&Type=json&pIndex=1&pSize=30&UNIT_CD=1000&`;
    try {
      const r = await fetch(apiUrl);
      const d = await r.json();
      const rows = d?.ncryefbycijwazpnb?.[1]?.row || [];
      return json({ schedule: rows });
    } catch (e) { return json({ error: e.message }, 500); }
  }

  // ─── /bigkinds ───
  if (path === '/bigkinds') {
    const body = await req.json().catch(() => ({}));
    try {
      const r = await fetch('https://tools.kinds.or.kr/search/news', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      return json(d);
    } catch (e) { return json({ error: e.message }, 500); }
  }

  // ─── /naver ───
  if (path === '/naver') {
    const q = url.searchParams.get('q') || '';
    const nid = req.headers.get('X-Naver-ID') || '';
    const nsec = req.headers.get('X-Naver-Secret') || '';
    if (!nid || !nsec) return json({ error: 'Naver keys required' }, 400);
    try {
      const r = await fetch(`https://openapi.naver.com/v1/search/news.json?query=${encodeURIComponent(q)}&display=20&sort=date`, {
        headers: { 'X-Naver-Client-Id': nid, 'X-Naver-Client-Secret': nsec },
      });
      const d = await r.json();
      return json(d);
    } catch (e) { return json({ error: e.message }, 500); }
  }

  return json({ error: 'Not found', path }, 404);
}
