/**
 * NVGC GR Intelligence — Cloudflare Worker API Proxy v2
 * 
 * 엔드포인트:
 * GET  /health                  → 연결 확인
 * POST /bigkinds                → BigKinds 뉴스 검색
 * POST /bigkinds/issue          → BigKinds 이슈 랭킹
 * POST /bigkinds/trend          → BigKinds 키워드 트렌드
 * GET  /naver                   → 네이버 뉴스 검색
 * GET  /assembly/bills          → 국회 의원 발의 법률안 (GR 키워드)
 * GET  /assembly/bill-detail    → 특정 법안 상세
 * GET  /assembly/committee      → 위원회 회의 일정
 * GET  /assembly/schedule       → 본회의·위원회 안건
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-Naver-Client-Id, X-Naver-Client-Secret, X-Assembly-Key',
  'Content-Type': 'application/json',
};

// GR 핵심 키워드 (의안정보 검색용)
const GR_KEYWORDS = [
  '온라인플랫폼', '온플법', '인공지능', 'AI기본법',
  '데이터보호', '저작권', '플랫폼규제', '디지털플랫폼',
  '알고리즘', '라인야후', '핀테크', '빅테크'
];

addEventListener('fetch', event => {
  event.respondWith(
    handleRequest(event.request).catch(err =>
      new Response(JSON.stringify({ error: err.message }), { status: 500, headers: CORS })
    )
  );
});

async function handleRequest(req) {
  if (req.method === 'OPTIONS') return new Response(null, { headers: CORS });

  const url = new URL(req.url);
  const path = url.pathname.replace(/\/$/, '');

  // ─── Health ─────────────────────────────────────────────
  if (path === '/health') {
    return json({ status: 'ok', time: new Date().toISOString(), version: '2.0' });
  }

  // ─── BigKinds 뉴스 검색 ─────────────────────────────────
  if (path === '/bigkinds') {
    if (req.method !== 'POST') return json({ error: 'POST required' }, 405);
    const body = await req.json();
    const res = await fetch('https://tools.kinds.or.kr/search/news', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return new Response(await res.text(), { headers: CORS });
  }

  // ─── BigKinds 이슈 랭킹 ─────────────────────────────────
  if (path === '/bigkinds/issue') {
    if (req.method !== 'POST') return json({ error: 'POST required' }, 405);
    const body = await req.json();
    const res = await fetch('https://tools.kinds.or.kr/issue_ranking', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return new Response(await res.text(), { headers: CORS });
  }

  // ─── BigKinds 키워드 트렌드 ─────────────────────────────
  if (path === '/bigkinds/trend') {
    if (req.method !== 'POST') return json({ error: 'POST required' }, 405);
    const body = await req.json();
    const res = await fetch('https://tools.kinds.or.kr/time_line', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return new Response(await res.text(), { headers: CORS });
  }

  // ─── 네이버 뉴스 검색 ───────────────────────────────────
  if (path === '/naver') {
    const clientId = req.headers.get('X-Naver-Client-Id') || url.searchParams.get('cid');
    const clientSecret = req.headers.get('X-Naver-Client-Secret') || url.searchParams.get('cs');
    const query = url.searchParams.get('query') || '네이버';
    const display = url.searchParams.get('display') || '10';
    const sort = url.searchParams.get('sort') || 'date';
    if (!clientId || !clientSecret) return json({ error: '네이버 키 없음' }, 400);
    const res = await fetch(
      `https://openapi.naver.com/v1/search/news.json?query=${encodeURIComponent(query)}&display=${display}&sort=${sort}`,
      { headers: { 'X-Naver-Client-Id': clientId, 'X-Naver-Client-Secret': clientSecret } }
    );
    return new Response(await res.text(), { headers: CORS });
  }

  // ─── 국회 의원 발의 법률안 ───────────────────────────────
  if (path === '/assembly/bills') {
    const asmKey = req.headers.get('X-Assembly-Key') || url.searchParams.get('key');
    const keyword = url.searchParams.get('q') || '';
    const page = url.searchParams.get('page') || '1';
    const size = url.searchParams.get('size') || '20';

    if (!asmKey) return json({ error: '국회 API 키 없음' }, 400);

    // 키워드 없으면 GR 핵심 키워드 전체 병렬 검색
    let allBills = [];

    if (keyword) {
      const data = await fetchAssemblyBills(asmKey, keyword, page, size);
      allBills = data;
    } else {
      // GR 핵심 키워드 병렬 검색 (상위 4개)
      const searches = GR_KEYWORDS.slice(0, 6).map(kw =>
        fetchAssemblyBills(asmKey, kw, '1', '10')
      );
      const results = await Promise.allSettled(searches);
      const seen = new Set();
      results.forEach(r => {
        if (r.status === 'fulfilled') {
          r.value.forEach(b => {
            if (!seen.has(b.BILL_NO)) {
              seen.add(b.BILL_NO);
              allBills.push(b);
            }
          });
        }
      });
      // 최신순 정렬
      allBills.sort((a, b) => (b.PROPOSE_DT || '').localeCompare(a.PROPOSE_DT || ''));
    }

    return json({ bills: allBills, total: allBills.length });
  }

  // ─── 법안 상세 ──────────────────────────────────────────
  if (path === '/assembly/bill-detail') {
    const asmKey = req.headers.get('X-Assembly-Key') || url.searchParams.get('key');
    const billNo = url.searchParams.get('bill_no');
    if (!asmKey || !billNo) return json({ error: '파라미터 없음' }, 400);

    const apiUrl = `https://open.assembly.go.kr/portal/openapi/nzmimeepazxkubdpn`
      + `?KEY=${asmKey}&Type=json&pIndex=1&pSize=1&AGE=22&BILL_NO=${billNo}`;
    const res = await fetch(apiUrl);
    const data = await res.json();
    const rows = data?.nzmimeepazxkubdpn?.[1]?.row || [];
    return json({ bill: rows[0] || null });
  }

  // ─── 위원회 회의 일정 ────────────────────────────────────
  if (path === '/assembly/schedule') {
    const asmKey = req.headers.get('X-Assembly-Key') || url.searchParams.get('key');
    const cmit = url.searchParams.get('cmit') || '';
    if (!asmKey) return json({ error: '키 없음' }, 400);

    // 위원회별 회의 일정 API
    const apiUrl = `https://open.assembly.go.kr/portal/openapi/nwvrqwxyaytdsfvhu`
      + `?KEY=${asmKey}&Type=json&pIndex=1&pSize=20&AGE=22`
      + (cmit ? `&CMIT_NAME=${encodeURIComponent(cmit)}` : '');
    const res = await fetch(apiUrl);
    const data = await res.json();
    const rows = data?.nwvrqwxyaytdsfvhu?.[1]?.row || [];
    return json({ schedules: rows });
  }

  return json({ error: 'Not found', paths: ['/health','/bigkinds','/bigkinds/issue','/bigkinds/trend','/naver','/assembly/bills','/assembly/bill-detail','/assembly/schedule'] }, 404);
}

// ─── Assembly API 호출 헬퍼 ─────────────────────────────
async function fetchAssemblyBills(key, keyword, page, size) {
  try {
    const apiUrl = `https://open.assembly.go.kr/portal/openapi/nzmimeepazxkubdpn`
      + `?KEY=${key}&Type=json&pIndex=${page}&pSize=${size}&AGE=22`
      + `&SEARCH_WORD=${encodeURIComponent(keyword)}`;
    const res = await fetch(apiUrl);
    const data = await res.json();
    return data?.nzmimeepazxkubdpn?.[1]?.row || [];
  } catch(e) {
    return [];
  }
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: CORS });
}
