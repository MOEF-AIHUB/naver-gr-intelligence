/**
 * NVGC GR Intelligence — Cloudflare Worker API Proxy
 * 
 * 배포 방법:
 * 1. cloudflare.com 무료 계정 생성
 * 2. Workers & Pages → Create Worker
 * 3. 이 코드 전체 붙여넣기 → Save and Deploy
 * 4. 워커 URL 복사 (예: https://nvgc-proxy.yourid.workers.dev)
 * 5. NVGC 사이트 ⚙️ 설정에서 "워커 URL" 입력
 *
 * 지원 엔드포인트:
 * POST /bigkinds  → BigKinds (한국언론진흥재단) 뉴스 검색
 * GET  /naver     → 네이버 뉴스 검색 API
 * GET  /health    → 연결 상태 확인
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-Naver-Client-Id, X-Naver-Client-Secret',
  'Content-Type': 'application/json',
};

addEventListener('fetch', event => {
  event.respondWith(handleRequest(event.request).catch(err =>
    new Response(JSON.stringify({ error: err.message }), { status: 500, headers: CORS })
  ));
});

async function handleRequest(req) {
  // CORS preflight
  if (req.method === 'OPTIONS') return new Response(null, { headers: CORS });

  const url = new URL(req.url);
  const path = url.pathname.replace(/\/$/, '');

  // ─── Health check ───────────────────────────────────────
  if (path === '/health') {
    return new Response(JSON.stringify({ status: 'ok', time: new Date().toISOString() }), { headers: CORS });
  }

  // ─── BigKinds (한국언론진흥재단) ────────────────────────
  if (path === '/bigkinds') {
    if (req.method !== 'POST') return new Response(JSON.stringify({ error: 'POST required' }), { status: 405, headers: CORS });

    const body = await req.json();
    // body 에 access_key 포함 (프론트엔드에서 전달)
    const res = await fetch('https://tools.kinds.or.kr/search/news', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return new Response(JSON.stringify(data), { headers: CORS });
  }

  // ─── BigKinds 이슈 랭킹 ─────────────────────────────────
  if (path === '/bigkinds/issue') {
    if (req.method !== 'POST') return new Response(JSON.stringify({ error: 'POST required' }), { status: 405, headers: CORS });
    const body = await req.json();
    const res = await fetch('https://tools.kinds.or.kr/issue_ranking', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return new Response(JSON.stringify(data), { headers: CORS });
  }

  // ─── BigKinds 키워드 트렌드 ─────────────────────────────
  if (path === '/bigkinds/trend') {
    if (req.method !== 'POST') return new Response(JSON.stringify({ error: 'POST required' }), { status: 405, headers: CORS });
    const body = await req.json();
    const res = await fetch('https://tools.kinds.or.kr/time_line', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return new Response(JSON.stringify(data), { headers: CORS });
  }

  // ─── 네이버 뉴스 검색 ───────────────────────────────────
  if (path === '/naver') {
    const clientId = req.headers.get('X-Naver-Client-Id') || url.searchParams.get('cid');
    const clientSecret = req.headers.get('X-Naver-Client-Secret') || url.searchParams.get('cs');
    const query = url.searchParams.get('query') || '네이버';
    const display = url.searchParams.get('display') || '10';
    const sort = url.searchParams.get('sort') || 'date';

    if (!clientId || !clientSecret) {
      return new Response(JSON.stringify({ error: '네이버 Client ID/Secret 없음' }), { status: 400, headers: CORS });
    }

    const res = await fetch(
      `https://openapi.naver.com/v1/search/news.json?query=${encodeURIComponent(query)}&display=${display}&sort=${sort}`,
      { headers: { 'X-Naver-Client-Id': clientId, 'X-Naver-Client-Secret': clientSecret } }
    );
    const data = await res.json();
    return new Response(JSON.stringify(data), { headers: CORS });
  }

  return new Response(JSON.stringify({ error: 'Not found', paths: ['/health','/bigkinds','/bigkinds/issue','/bigkinds/trend','/naver'] }), { status: 404, headers: CORS });
}
