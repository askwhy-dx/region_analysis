/* verify_dashboard.js — 발행 게이트(C-8) 화면 검증 — **읽기 전용**
 *
 *   node scripts/verify_dashboard.js            (정적 서버를 직접 띄웠다 내린다)
 *   node scripts/verify_dashboard.js --keep     (서버를 남겨 둔다)
 *   node scripts/verify_dashboard.js --shots D:\out
 *
 * 데이터 검증(`verify_c8.py`)이 못 잡는 것만 본다 — **화면에서 사라지는 것들**:
 *   ① mock 폴백 (배지가 '실데이터' 인가)          ⑤ 자사 2브랜드 합산·둘 다 강조
 *   ② 지표 드롭다운 3종 · 코로플레스 채색          ⑥ 마커 z-order (학원 246개에 묻히지 않는가)
 *   ③ 전국→시도→시군구→동 드릴다운               ⑦ 토글 껐다 켠 뒤에도 z-order 유지
 *   ④ ④경쟁 환경 블록 문구 (시너지세권·대기 축)    ⑧ JS 런타임 에러
 *
 * ⚠️ 심화 검증은 **송도2동**에서 한다 — 8개 브랜드가 모두 있고 학원이 463개라
 *    z-order 가 깨지면 자사·경쟁사가 실제로 안 보이는 유일한 표본이다.
 */
const path = require("path");
const os = require("os");
const fs = require("fs");
const { spawn } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const { chromium } = require(path.join(ROOT, "..", "node_modules", "playwright"));

const argv = process.argv.slice(2);
const KEEP = argv.includes("--keep");
const SHOT = argv.includes("--shots")
  ? argv[argv.indexOf("--shots") + 1]
  : path.join(os.tmpdir(), "jiyeok_verify_shots");
const PORT = 8765;
const BASE = `http://127.0.0.1:${PORT}/web/`;
const PY = "C:\\Users\\User\\AppData\\Local\\Programs\\Python\\Python312\\python.exe";

const R = [];
const ck = (ok, name, detail = "") => {
  R.push([ok ? "PASS" : "FAIL", name, detail]);
  console.log(`  ${ok ? "[OK]" : "[!!]"} ${name}${detail ? "  — " + detail : ""}`);
};
const warn = (name, detail = "") => {
  R.push(["WARN", name, detail]);
  console.log(`  [~~] ${name}${detail ? "  — " + detail : ""}`);
};
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* VWorld 타일 검사 — **남의 서버 장애로 우리 발행이 막히면 안 된다.**
   2026-08-11 에 VWorld WMTS 가 실제로 죽어(키 없이 호출해도 503) 데이터가 멀쩡한데도
   발행이 통째로 멈췄다.
   ⚠️ 그렇다고 무조건 통과시키면 **키가 진짜 죽었을 때도 조용히 넘어간다.**
      그래서 실패하면 타일 URL 을 직접 찔러 **원인을 갈라서** 판정한다:
        5xx·응답없음  = VWorld 장애   → WARN (발행 진행)
        그 밖(4xx 등) = 우리 키·설정 → FAIL (발행 차단)
   ⚠️ WARN 이 **며칠씩 이어지면 그건 장애가 아니라 키 문제**다. 월간 점검이 그걸 본다. */
async function probeVworld() {
  const url = "https://api.vworld.kr/req/wmts/1.0.0/NOKEY/Satellite/14/6348/13973.jpeg";
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(15000) });
    return { status: r.status, outage: r.status >= 500 };
  } catch (e) {
    return { status: 0, outage: true, err: String(e.message || e) };
  }
}
async function ckVworldTiles(page, sel, name, reload) {
  const read = () => page.$$eval(sel, imgs => ({
    total: imgs.length, loaded: imgs.filter(x => x.complete && x.naturalWidth > 0).length
  }));
  let t = await read();
  const good = s => s.total > 0 && s.loaded / s.total >= .95;
  if (!good(t)) {                      // 한 번은 봐 준다 — 순간적인 실패가 흔하다
    await sleep(3000);
    if (reload) await reload();
    await sleep(2500);
    t = await read();
  }
  if (good(t)) { ck(true, name, JSON.stringify(t)); return; }
  const p = await probeVworld();
  if (p.outage) {
    warn(name, `${JSON.stringify(t)} · VWorld 서버 장애로 보임(probe ${p.status || "응답없음"}) — 발행은 진행한다`);
  } else {
    ck(false, name, `${JSON.stringify(t)} · probe ${p.status} — 서버는 살아 있다. 키·설정을 볼 것`);
  }
}

async function serve() {
  const p = spawn(fs.existsSync(PY) ? PY : "python",
                  ["-m", "http.server", String(PORT), "--bind", "127.0.0.1"],
                  { cwd: ROOT, stdio: "ignore" });
  for (let i = 0; i < 40; i++) {
    await sleep(250);
    try { if ((await fetch(BASE)).ok) return p; } catch (e) { /* 아직 안 떴다 */ }
  }
  throw new Error(`정적 서버가 안 뜬다 (${BASE})`);
}

(async () => {
  fs.mkdirSync(SHOT, { recursive: true });
  const server = await serve();
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1880, height: 840 } });
  const errors = [];
  /* 폴백 검사는 **일부러** 타일을 503 으로 만든다 → 그 구간의 콘솔 에러는 우리가 낸 것이라
     수집하면 마지막 "JS 런타임 에러 없음" 검사가 우리 손으로 깨진다.
     ⚠️ 구간을 좁게 잡는다. 통째로 무시하면 그 사이의 **진짜** 에러도 같이 묻힌다. */
  const mute = { on: false };
  page.on("pageerror", e => { if (!mute.on) errors.push(String(e)); });
  page.on("console", m => { if (m.type() === "error" && !mute.on) errors.push("console: " + m.text()); });

  console.log("=".repeat(78) + "\nC-8 발행 게이트 검증 (화면)\n" + "=".repeat(78));
  console.log(`  스크린샷 → ${SHOT}\n`);

  // ── ① 출처 ─────────────────────────────────────────────────────────
  await page.goto(BASE, { waitUntil: "networkidle" });
  await sleep(1200);
  await page.evaluate(()=>localStorage.setItem("jiyeok.basemap","vworld_label"));
  await page.reload({waitUntil:"networkidle"});
  await sleep(800);
  const defaultBasemap=await page.evaluate(()=>({state:state.basemap,plain:document.querySelector('#mainBasemaps [data-b="plain"]').getAttribute("aria-pressed")}));
  ck(defaultBasemap.state==="plain" && defaultBasemap.plain==="true","사이트 시작 배경은 항상 일반 지도",JSON.stringify(defaultBasemap));
  const clickRank=async(name,wait=800)=>{
    const open=await page.$eval("#rankFilterPanel",p=>!p.classList.contains("hidden"));
    if(!open){
      const level=await page.evaluate(()=>state.level);
      const target=level==="nation"?"sido":level==="sido"?"sgg":"adm";
      await page.click(`.region-step[data-filter-level="${target}"]`);
      await page.waitForFunction(()=>!document.getElementById("rankFilterPanel").classList.contains("hidden"));
    }
    await page.locator("#rank .row",{hasText:name}).first().click();
    await sleep(wait);
  };
  const badge = (await page.textContent("#srcBadge")).trim();
  ck(badge.includes("실데이터"), "실데이터 배지 (mock 폴백 아님)", badge);
  ck((await page.textContent("#ageFilterTrigger")).includes("3~16세"), "학령인구 기본 필터 3~16세");
  const agePresets = await page.$$eval("[data-age-preset]", bs => bs.map(b => b.textContent.trim()));
  ck(agePresets.some(x=>x.includes("와이키즈 3~7세")), "학령인구 프리셋 · 와이키즈 3~7세");
  ck(agePresets.some(x=>x.includes("와이즈만 8~16세")), "학령인구 프리셋 · 와이즈만 8~16세");
  ck((await page.$$("#regionFilterSteps .region-step")).length===4,"전국·시도·시군구·행정동 4단계 지역 필터");
  await page.click("#ageFilterTrigger");
  const ageFilterLayout=await page.evaluate(()=>{
    const age=document.getElementById("ageFilter").getBoundingClientRect();
    const region=document.getElementById("regionFilterbar").getBoundingClientRect();
    return {checks:document.querySelectorAll("#ageChecks input").length,
      hasSelect:!!document.querySelector("#ageFilter select"),regionOnRight:region.left>age.left,
      presets:[...document.querySelectorAll(".age-presets button")].map(x=>x.textContent.trim())};
  });
  ck(ageFilterLayout.checks===16 && !ageFilterLayout.hasSelect && ageFilterLayout.regionOnRight &&
     ageFilterLayout.presets[1].includes("와이키즈") && ageFilterLayout.presets[2].includes("와이즈만"),
     "학령인구 한국나이 3~18세 체크박스와 우측 지역 필터",JSON.stringify(ageFilterLayout));
  // 한국나이(세는나이) 축. 화면 8세 = 초1 = 데이터의 만 6세다.
  const ageAxis=await page.evaluate(()=>({offset:KOR_AGE_OFFSET,min:KOR_AGE_MIN,max:KOR_AGE_MAX,
    man8:toManAge(8),elem:SCHOOL_AGE_GROUPS[1].ages[0],kinder:ageGroups()[0].ages[0],
    childcare:HAS_CHILDCARE}));
  /* 유아 그룹 시작 나이는 2026-08-12 어린이집 합류로 한국 5세 → **3세**가 됐다
     (한국 3세 = 만1세 · 어린이집이 유일한 원천). 5세로 되돌아가면 만0~2세가 화면에서 사라진다. */
  /* 유아 그룹 시작 나이는 **어린이집 축이 붙었을 때만** 한국 3세(=만1세)다.
     아직 안 붙었으면 5세(=만3세)가 맞다 — 데이터가 없는데 칸만 열면 0 이 실측처럼 보인다. */
  ck(ageAxis.offset===2 && ageAxis.min===3 && ageAxis.max===18 && ageAxis.man8===6 &&
     ageAxis.elem===8 && ageAxis.kinder===(ageAxis.childcare?3:5),
     `나이 축 = 한국나이 (8세=초1=만6세) · 유아 ${ageAxis.childcare?3:5}세부터`,
     JSON.stringify(ageAxis));
  await page.click('[data-age-preset="3-7"]'); await sleep(300);
  const waikidsAges=await page.$$eval("#ageChecks input:checked",xs=>xs.map(x=>Number(x.value)));
  ck(JSON.stringify(waikidsAges)===JSON.stringify([3,4,5,6,7]),"와이키즈 학령인구 체크 연동",JSON.stringify(waikidsAges));
  await page.click("#ageFilterClear"); await sleep(300);
  const clearedAges=await page.evaluate(()=>({checked:document.querySelectorAll("#ageChecks input:checked").length,
    selected:state.selectedAges.size,label:ageRangeLabel()}));
  ck(clearedAges.checked===0&&clearedAges.selected===0&&clearedAges.label==="선택 없음",
     "학령인구 체크 해제 버튼",JSON.stringify(clearedAges));
  await page.click('[data-age-preset="3-16"]'); await sleep(300);
  // 한국 6세를 빼면 만 4세가 빠져야 한다 — 합산이 만나이로 환산되는지까지 본다
  await page.click('#ageChecks input[value="6"]'); await sleep(300);
  const customAges=await page.evaluate(()=>({count:state.selectedAges.size,has6:state.selectedAges.has(6),
    label:ageRangeLabel(),total:ageRangeTotal({age_year:{"3":10,"4":20,"5":30}})}));
  ck(customAges.count===13 && !customAges.has6 && customAges.total===40,
     "학령인구 개별 체크박스 비연속 합산 (한국나이→만나이 환산)",JSON.stringify(customAges));
  await page.click('#ageChecks input[value="6"]'); await sleep(300);
  ck(await page.isVisible("#ageFilterClose"),"학령인구 필터 X 닫기 버튼 표시");
  await page.click("#ageFilterClose");
  ck(await page.$eval("#ageFilterPanel",x=>x.classList.contains("hidden")),"학령인구 필터 X 버튼으로 닫기");
  await page.click("#ageFilterTrigger");
  await page.dispatchEvent("#map","pointerdown");
  ck(await page.$eval("#ageFilterPanel",x=>x.classList.contains("hidden")),"학령인구 필터 밖 클릭 시 닫기");
  await page.click('.region-step[data-filter-level="sido"]');
  const filterOpened=!(await page.$eval("#rankFilterPanel",x=>x.classList.contains("hidden")));
  ck(await page.isVisible("#rankFilterClose"),"지역 필터 X 닫기 버튼 표시");
  await page.click("#rankFilterClose");
  const filterXClosed=await page.$eval("#rankFilterPanel",x=>x.classList.contains("hidden"));
  await page.click('.region-step[data-filter-level="sido"]');
  await page.dispatchEvent("#map","pointerdown");
  const filterOutsideClosed=await page.$eval("#rankFilterPanel",x=>x.classList.contains("hidden"));
  await page.click('.region-step[data-filter-level="sido"]');
  await page.click('.region-step[data-filter-level="sido"]');
  const filterClosed=await page.$eval("#rankFilterPanel",x=>x.classList.contains("hidden"));
  ck(filterOpened&&filterXClosed&&filterOutsideClosed&&filterClosed,"지역 필터 X, 밖 클릭, 재클릭 닫기");

  // ── ② 지표 ─────────────────────────────────────────────────────────
  const opts = await page.$$eval("#metricSel option", els => els.map(e => e.textContent.trim()));
  ck(opts.some(o => o.includes("경쟁사 수")), "지표 · 경쟁사 수");
  ck(opts.some(o => o.includes("시너지세권")), "지표 · 시너지세권",
     opts.find(o => o.includes("시너지세권")) || "없음");
  ck(opts.some(o => o.includes("자사 센터 수")), "지표 · 자사 센터 수");
  ck(!opts.some(o => /종합점수|등급/.test(o)), "폐기된 점수·등급 지표 없음");
  // 소비력 ① 자산수준 = 중앙 평당가 (2026-08-04). 평균으로 되돌아가면 여기서 잡힌다.
  ck(opts.some(o => o.includes("평당가") && o.includes("중앙값")),
     "지표 · 아파트 평당가는 중앙값", opts.find(o => o.includes("평당가")) || "없음");
  // 표본 부족(24개월 매매 10건 미만) 동은 가격을 숫자로 띄우면 안 된다.
  // 화면에서 직접 확인한다 — 데이터에만 low_sample 이 있고 화면이 무시하면
  // 표본 3건짜리 동이 그대로 순위에 섞인다.
  // 캐시 상태에 기대지 않고 렌더 함수 자체를 때린다 — 전국 화면에서는 metrics.json(동)이
  // 아직 안 실려 있어서, 캐시를 뒤지면 검사가 조용히 무효(vacuous PASS)가 된다.
  const lowSampleShown = await page.evaluate(() => {
    if (typeof pyPriceText !== "function") return "pyPriceText 없음";
    const low  = pyPriceText({low_sample: true,  deal_count: 3, median_price_per_py: 9999});
    const good = pyPriceText({low_sample: false, deal_count: 80, median_price_per_py: 3210});
    if (!low.includes("표본 부족")) return "표본 부족인데 숫자가 나온다: " + low;
    if (/9,?999/.test(low))        return "표본 부족인데 값이 새어나온다: " + low;
    if (!good.includes("3,210"))   return "정상 동인데 값이 안 나온다: " + good;
    return "ok";
  });
  ck(lowSampleShown === "ok", "표본 부족 동은 가격 대신 '표본 부족'", lowSampleShown);
  // 색·5분위 경계도 같은 규칙을 따라야 한다 (raw 접근이 남아 있으면 여기서 걸린다)
  const metricValGuard = await page.evaluate(() => {
    if (typeof metricVal !== "function") return "metricVal 없음";
    const v = metricVal({low_sample: true, median_price_per_py: 9999}, "median_price_per_py");
    return v === null ? "ok" : "코로플레스가 표본 부족 동을 채색한다: " + v;
  });
  ck(metricValGuard === "ok", "표본 부족 동은 코로플레스에서도 제외", metricValGuard);

  // ── C-5 소비력 ②③ (2026-08-05) ─────────────────────────────────────
  ck(opts.some(o => o.includes("아파트 세대수")), "지표 · 아파트 세대수");
  ck(opts.some(o => o.includes("아파트 거주 비율")), "지표 · 아파트 거주 비율");
  ck(opts.some(o => o.includes("주거자산 총액")), "지표 · 주거자산 총액");
  // 아파트 거주 비율 100% 초과 동(67곳)은 경계 원천의 분동 반영 지연이다(E-4).
  // 색·5분위·순위에서 빠져야 한다 — 안 빠지면 "거주 188%" 동이 최상위 색을 칠한다.
  const aptRatioGuard = await page.evaluate(() => {
    if (typeof metricVal !== "function") return "metricVal 없음";
    const bad  = metricVal({apt_ratio: 1.885, apt_ratio_suspect: true},  "apt_ratio");
    const good = metricVal({apt_ratio: 0.548, apt_ratio_suspect: false}, "apt_ratio");
    if (bad !== null)   return "100% 초과 동이 채색된다: " + bad;
    if (good !== 0.548) return "정상 동인데 값이 안 나온다: " + good;
    return "ok";
  });
  ck(aptRatioGuard === "ok", "아파트 거주 비율 100% 초과 동은 색·순위에서 제외", aptRatioGuard);

  // ── 12개월 인구 증감 · 행정구역 개편 구간 (2026-08-13) ────────────────────
  // 개편 지역은 1년 전 코드가 없어 증감률이 성립하지 않는다. 그대로 두면 검단구 +49.2%
  // 처럼 **분구 점프가 「인구 증가」로 상담 자리에 나간다.** 합산 함수까지 같이 본다 —
  // 데이터에서 지워도 화면이 trend12 로 되계산하면 값이 되살아난다.
  const trendBreakGuard = await page.evaluate(() => {
    if (typeof aggregateRegions !== "function") return "aggregateRegions 없음";
    const mk = (met, pop) => ({met: {data: {X: met}}, pop: {data: {X: pop}}});
    const broke = aggregateRegions(["X"], mk({trend_break: "코드 신설"},
                                             {total: 100, trend12: [90, 91, 100]}));
    if (broke.m.trend_pct !== null) return "개편 지역인데 값이 나온다: " + broke.m.trend_pct;
    const zero = aggregateRegions(["X"], mk({}, {total: 100, trend12: [0, 0, 100]}));
    if (zero.m.trend_pct !== null) return "0 이 섞인 시계열인데 값이 나온다: " + zero.m.trend_pct;
    const good = aggregateRegions(["X"], mk({}, {total: 110, trend12: [100, 105, 110]}));
    if (good.m.trend_pct !== 10) return "정상 지역인데 값이 안 나온다: " + good.m.trend_pct;
    return "ok";
  });
  ck(trendBreakGuard === "ok", "행정구역 개편 지역은 12개월 증감을 내지 않는다", trendBreakGuard);

  const fills = await page.$$eval("#map path", ps =>
    [...new Set(ps.map(p => p.getAttribute("fill")))].filter(Boolean));
  ck(fills.filter(c => c.toLowerCase() !== "#eeeeea").length >= 3,
     "전국 코로플레스 채색 (회색만이면 조인 실패)", `색 ${fills.length}종`);
  await page.screenshot({ path: path.join(SHOT, "1_nation.png") });

  const rank = await page.textContent("#rank");
  ck(rank.includes("전남광주"), "전국 순위표에 전남광주통합특별시(12)");
  const summaryDuplicates=await page.evaluate(()=>({
    topFacts:document.querySelectorAll("#regionSummary .hero .facts").length,
    dashboardSections:document.querySelectorAll("#regionSummary .dashboard-blk").length
  }));
  ck(summaryDuplicates.topFacts===0 && summaryDuplicates.dashboardSections===3,
     "상단 중복 수치 4개 삭제",JSON.stringify(summaryDuplicates));

  const ctrlMapSelect=await page.evaluate(()=>{
    const layers=layer.getLayers().slice(0,2);
    layers.forEach(l=>l.fire("click",{originalEvent:{ctrlKey:true,preventDefault(){}}}));
    const result={selected:state.selectedCodes.size,level:state.level,sidos:state.sidos.length};
    layers.forEach(l=>l.fire("click",{originalEvent:{ctrlKey:true,preventDefault(){}}}));
    return result;
  });
  ck(ctrlMapSelect.selected===2 && ctrlMapSelect.level==="nation" && ctrlMapSelect.sidos===2,
     "Ctrl+지도 클릭 다중 선택",JSON.stringify(ctrlMapSelect));
  await page.evaluate(()=>viewNation()); await sleep(300);

  const nameClickBefore=await page.evaluate(()=>({zoom:map.getZoom(),center:map.getCenter()}));
  await clickRank("경기도");
  const nameClickAfter=await page.evaluate(()=>({zoom:map.getZoom(),center:map.getCenter(),level:state.level,features:layer.getLayers().length}));
  const nameClickFocused=nameClickAfter.level==="sido" && nameClickAfter.features===47 &&
    (Math.abs(nameClickBefore.center.lat-nameClickAfter.center.lat)>.01 ||
     Math.abs(nameClickBefore.center.lng-nameClickAfter.center.lng)>.01);
  ck(nameClickFocused,"필터 지역 이름 클릭 시 해당 지역으로 지도 이동",JSON.stringify({before:nameClickBefore,after:nameClickAfter}));
  const compactLayout = await page.evaluate(() => {
    const boxes=[document.querySelector("#centerMapCard"),
                 document.querySelector("#regionSummary")].map(x=>x.getBoundingClientRect());
    return {viewport:innerHeight,scrollHeight:document.documentElement.scrollHeight,
            maxBottom:Math.max(...boxes.map(x=>x.bottom)),rankRows:document.querySelectorAll("#rank .row").length,
            lefts:boxes.map(x=>Math.round(x.left)),heights:boxes.map(x=>Math.round(x.height)),
            widths:boxes.map(x=>Math.round(x.width)),rankUnderAge:document.getElementById("filterbar").contains(document.getElementById("regionFilterbar")),
            rankOutsideStage:!document.querySelector("#stageMap #rank")};
  });
  const equalHeights=Math.max(...compactLayout.heights)-Math.min(...compactLayout.heights)<=1;
  const mapShare=compactLayout.widths[0]/(compactLayout.widths[0]+compactLayout.widths[1]);
  const mapSummaryOrder=compactLayout.lefts[0]<compactLayout.lefts[1];
  ck(compactLayout.rankRows===47 && compactLayout.scrollHeight<=compactLayout.viewport+4 && compactLayout.maxBottom<=compactLayout.viewport+4 && equalHeights && mapSummaryOrder && compactLayout.rankUnderAge && compactLayout.rankOutsideStage && mapShare>=.59 && mapShare<=.61,
     "지역 순위 필터 상단 이동 및 지도·요약 6:4 한 화면 표시", JSON.stringify({...compactLayout,mapShare}));
  await page.click('.region-step[data-filter-level="sgg"]');
  const multiPerf=await page.evaluate(()=>{
    const boxes=[...document.querySelectorAll("#rank .pick")].slice(0,12);
    const started=performance.now();
    boxes.forEach(b=>b.click());
    return {duration:performance.now()-started,clicked:boxes.length};
  });
  await sleep(350);
  multiPerf.selected=await page.$$eval("#rank .pick:checked",xs=>xs.length);
  multiPerf.summary=(await page.textContent("#regionSummary")).includes("선택 12개 지역");
  const multiMap=await page.evaluate(()=>{
    let selected=0,dimmed=0;
    layer.eachLayer(l=>{if(state.selectedCodes.has(String(l.feature.properties.code)) && l.options.weight===5)selected++;else if(l.options.fillOpacity===.12)dimmed++;});
    return {selected,dimmed,zoom:map.getZoom(),center:map.getCenter()};
  });
  multiPerf.map=multiMap;
  const checkboxFocused=multiMap.zoom!==nameClickAfter.zoom || Math.abs(multiMap.center.lat-nameClickAfter.center.lat)>.01 || Math.abs(multiMap.center.lng-nameClickAfter.center.lng)>.01;
  ck(multiPerf.clicked===12 && multiPerf.selected===12 && multiPerf.summary && multiPerf.duration<150 && multiMap.selected===12 && multiMap.dimmed>0 && checkboxFocused,
     "다중 선택 12개 연속 처리",JSON.stringify(multiPerf));
  const summaryDashboard=await page.evaluate(()=>{
    const card=document.getElementById("regionSummary");
    const note=card.querySelector(".selection-note");
    const rails=[...card.querySelectorAll(".section-rail")];
    const competition=[...card.querySelectorAll(".competition-row .kpi")];
    const allKpis=[...card.querySelectorAll(".kpi")];
    const marketKpis=[...card.querySelectorAll(".dashboard-blk.market .kpi")];
    const studentCards=[...card.querySelectorAll(".student-kpi")];
    return {noteFont:parseFloat(getComputedStyle(note).fontSize),hero:card.querySelectorAll(".hero").length,
      facts:card.querySelectorAll(".facts").length,sections:card.querySelectorAll(".dashboard-blk").length,
      railColors:[...new Set(rails.map(x=>getComputedStyle(x).backgroundColor))],
      competition:competition.length,competitionRows:new Set(competition.map(x=>Math.round(x.getBoundingClientRect().top))).size,
      labelsAboveValues:allKpis.every(k=>k.querySelector(".l").getBoundingClientRect().bottom<=k.querySelector(".v").getBoundingClientRect().top+1),
      marketCards:marketKpis.length,marketRows:new Set(marketKpis.map(x=>Math.round(x.getBoundingClientRect().top))).size,
      studentCards:studentCards.length,
      studentRows:new Set(studentCards.map(x=>Math.round(x.getBoundingClientRect().top))).size,
      studentWidths:studentCards.map(x=>Math.round(x.getBoundingClientRect().width)),
      childcare:HAS_CHILDCARE,
      studentText:card.querySelector(".student-kpis")?.textContent.replace(/\s+/g," ").trim()||""};
  });
  ck(summaryDashboard.noteFont>=16 && summaryDashboard.hero===0 && summaryDashboard.facts===0 &&
     summaryDashboard.sections===3 && summaryDashboard.railColors.length===3,
     "선택 합산 제목 확대 및 3색 대시보드 카드",JSON.stringify(summaryDashboard));
  ck(summaryDashboard.competition===4 && summaryDashboard.competitionRows===1,
     "자사·경쟁사·시너지세권 4개 지표 한 줄",JSON.stringify(summaryDashboard));
  ck(summaryDashboard.labelsAboveValues && summaryDashboard.marketCards===4 && summaryDashboard.marketRows===1,
     "전체 KPI 라벨 상단·수치 하단 및 시장규모 4개 한 줄",JSON.stringify(summaryDashboard));
  ck(summaryDashboard.studentCards===4 && summaryDashboard.studentRows===1 &&
     Math.max(...summaryDashboard.studentWidths)-Math.min(...summaryDashboard.studentWidths)<=1 &&
     [summaryDashboard.childcare?"유치원·어린이집 원아":"유치원 원아",
      "초등학교 재학생","중학교 재학생","고등학교 재학생"].every(x=>summaryDashboard.studentText.includes(x)),
     "유치·초등·중등·고등 학생수 카드 정렬",JSON.stringify(summaryDashboard));
  const schoolStudentsDefault=await page.$$eval(".student-value",xs=>xs.map(x=>Number(x.textContent.replace(/[^0-9]/g,""))));
  // 한국나이 5~7세 = 유치원. 기본 3~16세에는 고교(17~18세)가 없으므로 고교 칸은 0이 맞다.
  await page.evaluate(async()=>{
    state.selectedAges=new Set([5,6,7]);
    await redrawForAgeFilter();
  });
  await page.waitForFunction(()=>document.querySelector("#regionSummary").textContent.includes("5~7세 필터 적용"));
  await sleep(250);
  const schoolStudentsKinder=await page.$$eval(".student-value",xs=>xs.map(x=>Number(x.textContent.replace(/[^0-9]/g,""))));
  // 한국나이 17~18세 = 고1~고2. 고교 칸만 살아나야 한다
  await page.evaluate(async()=>{
    state.selectedAges=new Set([17,18]);
    await redrawForAgeFilter();
  });
  await page.waitForFunction(()=>document.querySelector("#regionSummary").textContent.includes("17~18세 필터 적용"));
  await sleep(250);
  const schoolStudentsHigh=await page.$$eval(".student-value",xs=>xs.map(x=>Number(x.textContent.replace(/[^0-9]/g,""))));
  ck(schoolStudentsDefault.length===4 && schoolStudentsDefault.slice(0,3).every(x=>x>0) &&
     schoolStudentsDefault[3]===0 &&
     schoolStudentsKinder[0]>0 && schoolStudentsKinder.slice(1).every(x=>x===0) &&
     schoolStudentsHigh[3]>0 && schoolStudentsHigh.slice(0,3).every(x=>x===0),
     "학령인구 필터에 따른 학교급별 학생수 변환 (한국나이)",
     JSON.stringify({schoolStudentsDefault,schoolStudentsKinder,schoolStudentsHigh}));
  await page.evaluate(async()=>{
    state.selectedAges=new Set(Array.from({length:14},(_,i)=>i+3));
    await redrawForAgeFilter();
  });
  await page.waitForFunction(()=>document.querySelector("#regionSummary").textContent.includes("3~16세 필터 적용"));
  await sleep(250);
  // 말풍선 내역 — 학교급은 학년별, 경쟁 KPI 는 브랜드별.
  // ⚠️ 내역 합계가 카드 숫자와 어긋나면 안 된다(집계에서 comp_brands 를 빼먹으면 그렇게 된다).
  const tipDetail=await page.evaluate(()=>{
    const num=s=>Number(String(s).replace(/[^0-9]/g,"")||0);
    const cards=[...document.querySelectorAll("#regionSummary .student-kpi")].map(el=>({
      label:el.querySelector(".student-label").textContent,
      value:num(el.querySelector(".student-value").textContent),
      tip:el.getAttribute("data-tip")||""}));
    const kpis=[...document.querySelectorAll("#regionSummary .competition-row .kpi")].map(el=>({
      label:el.querySelector(".l").textContent,
      value:num(el.querySelector(".v").textContent),
      tip:el.getAttribute("data-tip")||""}));
    const sumOf=tip=>{const m=tip.match(/합계<\/span><span><b>([\d,]+)/);return m?num(m[1]):null;};
    const comp=kpis.find(k=>k.label==="경쟁사")||{}, syn=kpis.find(k=>k.label==="시너지세권")||{};
    return {
      grades:cards.map(c=>c.tip),
      /* 2026-08-12 어린이집 합류 — 「데이터 수집중」이 **실제 숫자**로 바뀌었다.
         3·4세 줄이 말풍선에 있고, 그 줄이 다시 '수집중'으로 돌아가지 않았는지 본다. */
      childcare:HAS_CHILDCARE,
      kinderAges34:["3세","4세"].every(g=>(cards[0]||{}).tip.includes(g)),
      kinderPending:(cards[0]||{}).tip.includes("데이터 수집중"),
      elemGrades:["초1","초2","초3","초4","초5","초6"].every(g=>(cards[1]||{}).tip.includes(g)),
      midGrades:["중1","중2","중3"].every(g=>(cards[2]||{}).tip.includes(g)),
      highGrades:["고1","고2","고3"].every(g=>(cards[3]||{}).tip.includes(g)),
      elemSum:sumOf((cards[1]||{}).tip), elemValue:(cards[1]||{}).value,
      compBrands:["소마","아담리즈","필즈더클래식","생각하는황소","시매쓰","CMS영재교육센터","CMS영재관","브레인스쿨","한생연","유투엠"].every(b=>(comp.tip||"").includes(b)),
      compSum:sumOf(comp.tip||""), compValue:comp.value,
      synBrands:["폴리어학원","라이즈어학원"].every(b=>(syn.tip||"").includes(b)),
      synSum:sumOf(syn.tip||""), synValue:syn.value,
    };
  });
  /* 어린이집이 붙었으면 3~4세가 **숫자**로, 아직이면 '데이터 수집중'으로 떠야 한다.
     둘 다 아니면(칸도 없고 안내도 없음) 사용자는 그 나이대가 없는 줄 안다. */
  ck(tipDetail.kinderAges34 && (tipDetail.childcare ? !tipDetail.kinderPending : tipDetail.kinderPending) &&
     tipDetail.elemGrades && tipDetail.midGrades && tipDetail.highGrades,
     "학교급 말풍선 = 학년별 내역 (3~4세는 어린이집 값 또는 수집중 안내)",
     JSON.stringify({kinderAges34:tipDetail.kinderAges34,kinderPending:tipDetail.kinderPending,
                     elem:tipDetail.elemGrades,mid:tipDetail.midGrades,high:tipDetail.highGrades}));
  ck(tipDetail.compBrands && tipDetail.synBrands,
     "경쟁사·시너지세권 말풍선 = 브랜드명별 내역",
     JSON.stringify({comp:tipDetail.compBrands,syn:tipDetail.synBrands}));
  ck(tipDetail.elemSum===tipDetail.elemValue && tipDetail.compSum===tipDetail.compValue &&
     tipDetail.synSum===tipDetail.synValue,
     "말풍선 내역 합계 == 카드 숫자",
     JSON.stringify({elem:[tipDetail.elemSum,tipDetail.elemValue],
                     comp:[tipDetail.compSum,tipDetail.compValue],
                     syn:[tipDetail.synSum,tipDetail.synValue]}));
  await page.screenshot({path:path.join(SHOT,"selection_dashboard.png")});
  await page.click('#rankActions [data-act="clear"]'); await sleep(200);
  /* 배경 선택은 `data-b` 를 가진 것만 센다 — 같은 줄에 '미리받기'(data-prefetch) 가
     함께 있어서 그냥 button 을 세면 개수가 어긋난다(2026-08-12). */
  ck((await page.$$("#mainBasemaps button[data-b]")).length===3, "메인 지도 배경 선택 버튼 3개");
  ck((await page.$$("#mainBasemaps button[data-prefetch]")).length===1,
     "위성 미리받기 버튼 (상담장 네트워크 대비)");
  const basemapSize=await page.$eval("#mainBasemaps button",b=>({font:parseFloat(getComputedStyle(b).fontSize),height:Math.round(b.getBoundingClientRect().height)}));
  ck(basemapSize.font<=10 && basemapSize.height<=25,"지도 배경 버튼 축소",JSON.stringify(basemapSize));
  const mainMapInteraction=await page.evaluate(()=>({wheel:map.scrollWheelZoom.enabled(),zoomPosition:map.zoomControl.getPosition()}));
  ck(mainMapInteraction.wheel && mainMapInteraction.zoomPosition==="bottomright","메인 지도 휠 확대·축소 및 우측 하단 버튼",JSON.stringify(mainMapInteraction));
  await page.click('#mainBasemaps [data-b="vworld_label"]'); await sleep(2500);
  await ckVworldTiles(page, '#map img[src*="api.vworld.kr"]', "메인 지도 VWorld 위성+라벨 타일",
    async () => { await page.evaluate(()=>{ state.basemap="vworld_label"; applyMainBasemap(); }); });

  /* VWorld 가 죽었을 때 **일반 지도로 되돌아가는지.**
     이 기능은 장애 때만 발동해서 평소 검증으로는 절대 안 걸린다 → 여기서 장애를 만든다.
     ⚠️ 예전 동작은 "실패한 레이어를 걷어내기"였는데, 그러면 배경이 하얗게 비고
        마커만 공중에 뜬다. 상담 중에 그 화면이 나오면 아무 데도 못 짚는다. */
  mute.on = true;   // 여기서부터 우리가 만든 장애다
  await page.route("**api.vworld.kr/**", r => r.fulfill({status:503, contentType:"text/html", body:"down"}));
  await page.click('#mainBasemaps [data-b="vworld_label"]');
  await page.waitForSelector("#mapNotice.on", {timeout:20000}).catch(()=>{});
  const fallback = await page.evaluate(()=>({
    basemap: state.basemap,
    osm: document.querySelectorAll('#map img[src*="openstreetmap"]').length,
    notice: !!document.querySelector("#mapNotice.on"),
    pressed: document.querySelector('#mainBasemaps [data-b="plain"]').getAttribute("aria-pressed")
  }));
  ck(fallback.basemap==="plain" && fallback.osm>0 && fallback.notice && fallback.pressed==="true",
     "VWorld 장애 시 일반 지도로 자동 폴백 + 안내", JSON.stringify(fallback));
  await page.unroute("**api.vworld.kr/**");
  // 장애는 복구되므로 **영구 차단이 아니어야** 한다 — 다시 누르면 다시 시도되는지.
  await page.click('#mainBasemaps [data-b="vworld_label"]'); await sleep(3500);
  const retry = await page.evaluate(()=>({basemap:state.basemap,
    vw:[...document.querySelectorAll('#map img[src*="api.vworld.kr"]')].filter(x=>x.complete&&x.naturalWidth>0).length}));
  ck(retry.basemap==="vworld_label" && retry.vw>0,
     "VWorld 복구 후 위성 재시도 가능", JSON.stringify(retry));
  mute.on = false;  // 여기부터 다시 진짜 에러를 본다
  await page.click('#mainBasemaps [data-b="plain"]'); await sleep(500);
  await page.screenshot({path:path.join(SHOT,"compact_gyeonggi.png")});
  await page.click('.region-step[data-filter-level="nation"]'); await sleep(700);

  await page.click('.region-step[data-filter-level="sido"]');
  await page.evaluate(()=>{
    const wanted=new Set(["경기도","인천광역시"]);
    [...document.querySelectorAll("#rank .row")]
      .filter(r=>wanted.has(r.querySelector(".nm").textContent.trim()))
      .forEach(r=>r.querySelector(".pick").click());
  });
  await sleep(250);
  const multiSido=await page.evaluate(()=>(
    {selected:state.sidos.length,sggEnabled:!document.querySelector('.region-step[data-filter-level="sgg"]').disabled,
     label:document.querySelector('.region-step[data-filter-level="sido"]').textContent.replace(/\s+/g," ").trim()}
  ));
  await page.click('.region-step[data-filter-level="sgg"]');
  await page.waitForFunction(()=>state.selectionContext&&state.selectionContext.level==="sgg");
  await sleep(300);
  multiSido.childRows=await page.$$eval("#rank .row",xs=>xs.length);
  ck(multiSido.selected===2 && multiSido.sggEnabled && multiSido.childRows>47,
     "다중 시도 선택 후 시군구 필터 연결",JSON.stringify(multiSido));

  await page.evaluate(()=>[...document.querySelectorAll("#rank .pick")].slice(0,2).forEach(x=>x.click()));
  await sleep(250);
  const multiSgg=await page.evaluate(()=>(
    {selected:state.sggs.length,admEnabled:!document.querySelector('.region-step[data-filter-level="adm"]').disabled,
     label:document.querySelector('.region-step[data-filter-level="sgg"]').textContent.replace(/\s+/g," ").trim()}
  ));
  await page.click('.region-step[data-filter-level="adm"]');
  await page.waitForFunction(()=>state.selectionContext&&state.selectionContext.level==="adm");
  await sleep(500);
  multiSgg.childRows=await page.$$eval("#rank .row",xs=>xs.length);
  multiSgg.mapFeatures=await page.evaluate(()=>layer?layer.getLayers().length:0);
  ck(multiSgg.selected===2 && multiSgg.admEnabled && multiSgg.childRows>0 && multiSgg.mapFeatures===multiSgg.childRows,
     "다중 시군구 선택 후 행정동 필터 연결",JSON.stringify(multiSgg));
  await page.evaluate(()=>[...document.querySelectorAll("#rank .pick")].slice(0,2).forEach(x=>x.click()));
  await page.waitForFunction(()=>state.adms.length===2 &&
    Object.entries(poiLayers).filter(([k])=>POI_STYLE[k]).some(([,g])=>g.getLayers().length>0));
  await sleep(250);
  const multiAdmPoi=await page.evaluate(()=>({
    selected:state.adms.length,
    poiMode:document.getElementById("stageMap").classList.contains("adm-inline"),
    summaryVisible:!document.getElementById("regionSummary").classList.contains("hidden"),
    markers:Object.entries(poiLayers).filter(([k])=>POI_STYLE[k])
      .reduce((sum,[,g])=>sum+g.getLayers().length,0)
  }));
  ck(multiAdmPoi.selected===2 && multiAdmPoi.poiMode && multiAdmPoi.summaryVisible && multiAdmPoi.markers>0,
     "다중 행정동 선택 시 POI 마커 합산 표시",JSON.stringify(multiAdmPoi));
  await page.click('.region-step[data-filter-level="nation"]'); await sleep(700);
  const upperReturnMap=await page.evaluate(()=>{
    const box=document.getElementById("map").getBoundingClientRect();
    const paths=[...document.querySelectorAll("#map path.region-boundary")];
    const coverages=paths.map(p=>{
      const r=p.getBoundingClientRect();
      return box.width&&box.height?(r.width*r.height)/(box.width*box.height):1;
    });
    return {level:state.level,overviewVisible:!document.getElementById("overviewMapView").classList.contains("hidden"),
      mapSize:map.getSize(),features:layer?layer.getLayers().length:0,
      fills:new Set(paths.map(p=>p.getAttribute("fill"))).size,
      maxPathCoverage:Math.max(0,...coverages),zoom:map.getZoom()};
  });
  ck(upperReturnMap.level==="nation" && upperReturnMap.overviewVisible && upperReturnMap.mapSize.x>500 &&
     upperReturnMap.mapSize.y>400 && upperReturnMap.features===16 && upperReturnMap.fills>=3 &&
     upperReturnMap.maxPathCoverage<.8,
     "POI 화면에서 전국·상위 필터 복귀 시 지도 투영 정상",JSON.stringify(upperReturnMap));

  // ── ③ 드릴다운 ─────────────────────────────────────────────────────
  await page.selectOption("#metricSel", "own_cnt");
  await sleep(500);
  await clickRank("인천광역시");
  ck((await page.textContent("#bc")).includes("인천"), "시도 드릴다운");
  ck((await page.textContent("#regionSummary")).includes("인천광역시"), "시도 클릭부터 상세 자료 표시");
  // 인천은 제물포·영종·검단 분구로 12개월 비교가 불가능한 시도다(trend_break).
  // 값은 «–», **이유는 마우스 오버로만** 알린다 — 라벨에 배지를 붙였더니 잘려서
  // «최근 12개월 인구 증감 개…» 가 됐다(2026-08-13 관리자 결정).
  const trendTile = await page.evaluate(() => {
    const el = [...document.querySelectorAll("#regionSummary .kpi")]
      .find(k => (k.querySelector(".l")?.textContent || "").includes("최근 12개월 인구 증감"));
    if (!el) return {miss: true};
    return {label: el.querySelector(".l").textContent.trim(),
            value: el.querySelector(".v").textContent.trim(),
            tip: el.getAttribute("data-tip") || ""};
  });
  ck(!trendTile.miss && trendTile.value.startsWith("–") && trendTile.label === "최근 12개월 인구 증감"
     && trendTile.tip.includes("비교할 수 없다"),
     "개편 시도는 «–» + 사유는 툴팁에만", JSON.stringify(trendTile).slice(0, 160));
  await page.click('.region-step[data-filter-level="sgg"]');
  const picks = await page.$$("#rank .pick");
  if(picks.length >= 2){
    await page.evaluate(()=>document.querySelectorAll("#rank .pick")[0].click());
    await page.evaluate(()=>document.querySelectorAll("#rank .pick")[1].click());
    await sleep(300);
    ck((await page.textContent("#regionSummary")).includes("선택 2개 지역 합산"), "여러 지역 선택 합산");
    await page.click('#rankActions [data-act="clear"]'); await sleep(200);
    ck((await page.evaluate(()=>state.selectedCodes.size))===0,"지역 필터 체크 해제 버튼");
  }else ck(false, "여러 지역 선택 합산", "체크박스 부족");
  await clickRank("연수구");
  ck((await page.textContent("#bc")).includes("연수"), "시군구 드릴다운");
  await page.screenshot({ path: path.join(SHOT, "2_sgg.png") });
  await page.evaluate(()=>{
    const target=layer&&layer.getLayers().find(x=>x.feature&&x.feature.properties.name==="송도2동");
    if(!target) throw new Error("송도2동 지도 경계를 찾을 수 없음");
    target.fire("click",{originalEvent:{ctrlKey:false,metaKey:false}});
  });
  await page.waitForFunction(()=>{
    const card=document.getElementById("regionSummary");
    return card && !card.classList.contains("hidden") && card.textContent.includes("송도2동") &&
      document.querySelectorAll("#regionSummary .dashboard-blk").length===3;
  });
  await sleep(900);
  const admInline=await page.evaluate(()=>({
    stageMapVisible:!document.getElementById("stageMap").classList.contains("hidden"),
    detailMode:document.getElementById("stageMap").classList.contains("adm-inline"),
    overviewHidden:document.getElementById("overviewMapView").classList.contains("hidden"),
    poiVisible:!document.getElementById("inlinePoiView").classList.contains("hidden"),
    panelHidden:document.getElementById("panel").classList.contains("hidden"),
    summaryVisible:!document.getElementById("regionSummary").classList.contains("hidden"),
    rankVisible:!!document.querySelector("#rank .row"),
    filterText:document.getElementById("regionFilterSteps").textContent.replace(/\s+/g," ").trim(),
    summaryText:document.getElementById("regionSummary").textContent,
    dashboardSections:document.querySelectorAll("#regionSummary .dashboard-blk").length
  }));
  ck(admInline.stageMapVisible && admInline.detailMode && admInline.overviewHidden && admInline.poiVisible && admInline.panelHidden && admInline.summaryVisible && admInline.rankVisible && admInline.dashboardSections===3 && admInline.summaryText.includes("송도2동") && ["전국","인천광역시","연수구","송도2동"].every(x=>admInline.filterText.includes(x)),
     "행정동 직접 클릭 시 POI 지도와 신형 요약 카드 표시",JSON.stringify({...admInline,summaryText:undefined}));
  ck(!(await page.$('[data-open-adm]')) && !(await page.textContent("#regionSummary")).includes("POI 상세"),
     "오른쪽 카드 POI 상세 버튼 삭제");
  const poiChrome=await page.evaluate(()=>{
    const mapBox=document.getElementById("map2").getBoundingClientRect();
    const baseBox=document.getElementById("basemaps").getBoundingClientRect();
    return {headerRemoved:!document.querySelector("#inlinePoiView h2") && document.getElementById("poiHint").classList.contains("hidden"),
      top:Math.round(baseBox.top-mapBox.top),right:Math.round(mapBox.right-baseBox.right)};
  });
  ck(poiChrome.headerRemoved && poiChrome.top>=0 && poiChrome.top<=12 && poiChrome.right>=0 && poiChrome.right<=12,
     "POI 설명 헤더 삭제 및 배경 버튼 지도 우측 상단 배치",JSON.stringify(poiChrome));
  const bc = (await page.textContent("#bc")).replace(/\s+/g, " ").trim();
  ck(bc.includes("송도2동"), "행정동 상세 진입", bc);
  const admBoundary=await page.evaluate(()=>{
    const styles=[];
    poiLayers.__adm.eachLayer(l=>styles.push({color:l.options.color,weight:l.options.weight}));
    return {allWhite:styles.length>0&&styles.every(x=>x.color==="#ffffff"),selected:styles.filter(x=>x.weight===4).length,zoom:map2.getZoom()};
  });
  ck(admBoundary.allWhite && admBoundary.selected===1,"행정동 경계선 흰색 표시",JSON.stringify(admBoundary));
  ck(admBoundary.zoom<=14,"행정동 진입 초기 확대 수준 제한",JSON.stringify(admBoundary));
  const poiCtrlCodes=await page.evaluate(()=>{
    const original=[...state.selectedCodes][0];
    const mapBox=document.getElementById("map2").getBoundingClientRect();
    const target=poiLayers.__adm.getLayers().find(l=>{
      if(String(l.feature.properties.code)===String(original)) return false;
      const b=l.getElement().getBoundingClientRect();
      return b.width>12&&b.height>12&&b.left<mapBox.right&&b.right>mapBox.left&&b.top<mapBox.bottom&&b.bottom>mapBox.top;
    });
    target.getElement().setAttribute("data-poi-ctrl-target","true");
    return {original,target:String(target.feature.properties.code)};
  });
  await page.locator('[data-poi-ctrl-target="true"]').click({modifiers:["Control"],force:true});
  await page.waitForFunction(()=>state.selectedCodes.size===2 && poiLayers.__adm &&
    poiLayers.__adm.getLayers().filter(l=>l.options.weight===4).length===2);
  const poiCtrlSelect=await page.evaluate(()=>({selected:state.selectedCodes.size,adms:state.adms.length,
    poiMode:document.getElementById("stageMap").classList.contains("adm-inline")}));
  ck(poiCtrlSelect.selected===2 && poiCtrlSelect.adms===2 && poiCtrlSelect.poiMode,
     "POI 상세 지도 Ctrl+행정동 클릭 다중 선택",JSON.stringify({...poiCtrlCodes,...poiCtrlSelect}));
  await page.evaluate(code=>{
    const target=poiLayers.__adm.getLayers().find(l=>String(l.feature.properties.code)===String(code));
    target.getElement().setAttribute("data-poi-single-target","true");
  },poiCtrlCodes.target);
  const poiVersionBefore=await page.evaluate(()=>poiRenderVersion);
  await page.evaluate(v=>window.__poiVersionBefore=v,poiVersionBefore);
  const poiClickStarted=Date.now();
  await page.locator('[data-poi-single-target="true"]').click({force:true});
  await page.waitForFunction(code=>state.selectedCodes.size===1 && state.level==="adm" &&
    state.adm && String(state.adm.code)===String(code) && poiRenderVersion>window.__poiVersionBefore,poiCtrlCodes.target);
  const poiClickMs=Date.now()-poiClickStarted;
  const poiSingleSelect=await page.evaluate(()=>({selected:state.selectedCodes.size,adms:state.adms.length,adm:state.adm.code,
    markerCache:poiMarkerCache.size}));
  poiSingleSelect.ms=poiClickMs;
  ck(poiSingleSelect.selected===1 && poiSingleSelect.adms===1 && poiClickMs<1000,
     "POI 상세 지도 일반 클릭 시 1개만 선택",JSON.stringify(poiSingleSelect));
  await page.evaluate(async code=>{
    const row=state.selectionContext.rows.find(r=>String(r.code)===String(code));
    await viewAdm({code:String(row.code),name:row.name});
  },poiCtrlCodes.original);
  await sleep(400);

  // ── ③ 구매력(소비력) 블록 — C-5 조인 결과 ──────────────────────────
  const buy = await page.textContent("#regionSummary");
  ck(buy.includes("아파트 세대수") && !/수집 중/.test(buy),
     "아파트 세대수가 실수치 (수집 중 표시 아님)",
     (buy.match(/([\d,]+)\s*세대/) || [])[0] || "숫자 없음");
  ck(buy.includes("아파트 거주 비율"), "③ 아파트 거주 비율 타일");
  ck(buy.includes("주거자산 총액"), "③ 주거자산 총액 타일");
  ck(buy.includes("신축(10년 이내) 비중"), "③ 신축 비중 카드");
  ck(!buy.includes("K-apt") && !buy.includes("RTMS"), "오른쪽 카드 원천 서술문 제거");

  // ── ④ 경쟁 환경 블록 ───────────────────────────────────────────────
  const panel = await page.textContent("#regionSummary");
  ck(panel.includes("교육·경쟁 환경"), "교육·경쟁 환경 블록");
  ck(panel.includes("시너지세권"), "시너지세권 표기");
  ck(!panel.includes("경쟁사+영유"), "경쟁사+시너지세권 합산 표기 없음");
  ck(!/경쟁사가 아니|자료 대기 중|두 원천의|혼합 단지/.test(panel), "오른쪽 카드 서술형 문구 제거");

  const kpis = await page.$$eval("#regionSummary .kpi", els =>
    els.map(e => e.textContent.replace(/\s+/g, " ").trim()));
  const pick = w => (kpis.find(t => t.includes(w)) || "").match(/^(\d+)/)?.[1];
  console.log("     KPI:", kpis.filter(t => /자사|경쟁사|시너지/.test(t)).join(" | "));
  ck(pick("자사 와이키즈") === "1", "자사 와이키즈 1", pick("자사 와이키즈"));
  ck(pick("자사 와이즈만") === "1", "자사 와이즈만 1", pick("자사 와이즈만"));
  ck(pick("경쟁사") === "7", "경쟁사 7 (기존 6 + CMS영재교육센터 송도, 2026-09-17)", pick("경쟁사"));
  ck(pick("시너지세권") === "2", "시너지세권 2", pick("시너지세권"));

  const topBrandFilters=await page.evaluate(()=>(
    Object.fromEntries([...document.querySelectorAll(".poi-brand-filter")].map(filter=>[
      filter.dataset.category,
      [...filter.querySelectorAll('.poi-brand-checks input[type="checkbox"]')].map(input=>input.value)
    ]))
  ));
  ck(JSON.stringify(topBrandFilters.competitor)===JSON.stringify(["소마","아담리즈","필즈더클래식","생각하는황소","시매쓰","CMS영재교육센터","CMS영재관","브레인스쿨","한생연","유투엠"]) &&
     JSON.stringify(topBrandFilters.english_kinder)===JSON.stringify(["폴리어학원","라이즈어학원"]),
     "상단 경쟁사·시너지세권 브랜드 체크박스 목록",JSON.stringify(topBrandFilters));
  await page.click('#competitorBrandFilter .poi-brand-filter-trigger');
  let filterVersion=await page.evaluate(()=>poiRenderVersion);
  await page.locator('#competitorBrandFilter input[value="소마"]').uncheck();
  await page.waitForFunction(v=>poiRenderVersion>v,filterVersion);
  const competitorFiltered=await page.evaluate(()=>(
    {count:document.querySelector('[data-filter-key="competitor"] .cnt').textContent,
     card:[...document.querySelectorAll("#regionSummary .kpi")].find(k=>k.querySelector(".l").textContent.trim()==="경쟁사").querySelector(".v").textContent.trim(),
     brands:poiLayers.competitor.getLayers().map(layer=>layer.getPopup().getContent()),
     selected:state.selectedPoiBrands.competitor.size}
  ));
  ck(competitorFiltered.count==="6" && competitorFiltered.card.startsWith("6") && competitorFiltered.selected===9 &&
     competitorFiltered.brands.every(text=>!text.includes("소마")),
     "상단 경쟁사 브랜드 체크박스가 지도 마커를 필터링",JSON.stringify(competitorFiltered));
  filterVersion=await page.evaluate(()=>poiRenderVersion);
  await page.locator('#competitorBrandFilter input[value="소마"]').check();
  await page.waitForFunction(v=>poiRenderVersion>v,filterVersion);
  await page.click('#synergyBrandFilter .poi-brand-filter-trigger');
  filterVersion=await page.evaluate(()=>poiRenderVersion);
  await page.locator('#synergyBrandFilter input[value="폴리어학원"]').uncheck();
  await page.waitForFunction(v=>poiRenderVersion>v,filterVersion);
  const synergyFiltered=await page.evaluate(()=>(
    {count:document.querySelector('[data-filter-key="english_kinder"] .cnt').textContent,
     card:[...document.querySelectorAll("#regionSummary .kpi")].find(k=>k.querySelector(".l").textContent.trim()==="시너지세권").querySelector(".v").textContent.trim(),
     brands:poiLayers.english_kinder.getLayers().map(layer=>layer.getPopup().getContent()),
     selected:state.selectedPoiBrands.english_kinder.size}
  ));
  ck(synergyFiltered.count==="1" && synergyFiltered.card.startsWith("1") && synergyFiltered.selected===1 &&
     synergyFiltered.brands.every(text=>!text.includes("폴리어학원")),
     "상단 시너지세권 브랜드 체크박스가 지도 마커를 필터링",JSON.stringify(synergyFiltered));
  filterVersion=await page.evaluate(()=>poiRenderVersion);
  await page.locator('#synergyBrandFilter input[value="폴리어학원"]').check();
  await page.waitForFunction(v=>poiRenderVersion>v,filterVersion);

  const rightCardLayout=await page.evaluate(()=>{
    const p=document.getElementById("regionSummary");
    const kpis=[...p.querySelectorAll(".kpi")];
    const values=kpis.map(k=>k.querySelector(".v"));
    return {graphs:p.querySelectorAll("svg,.chart").length,sections:p.querySelectorAll(".dashboard-blk").length,
      studentCards:p.querySelectorAll(".student-kpi").length,
      kpis:kpis.length,valueCards:values.filter(v=>getComputedStyle(v).backgroundColor!=="rgba(0, 0, 0, 0)").length,
      clientHeight:p.clientHeight,scrollHeight:p.scrollHeight};
  });
  ck(rightCardLayout.graphs===0 && rightCardLayout.sections===3 && rightCardLayout.studentCards===4,"오른쪽 신형 3색 카드와 학교급 카드",JSON.stringify(rightCardLayout));
  ck(rightCardLayout.kpis>0 && rightCardLayout.valueCards===rightCardLayout.kpis,"오른쪽 지표 라벨 상단·수치 카드형 표시",JSON.stringify(rightCardLayout));
  ck(rightCardLayout.scrollHeight<=rightCardLayout.clientHeight+1,"오른쪽 카드 전체 내용 한 화면 표시",JSON.stringify(rightCardLayout));

  // 2026-08-11 신설 — 마우스오버 설명.
  // ⚠️ 문구가 **비어 있지 않은지**까지 본다. data-tip 을 붙였다가 값이 빈 문자열이면
  //    말풍선이 빈 상자로 뜨는데, 화면만 봐서는 "설명이 원래 없는 지표"와 구분이 안 된다.
  const tipCards=await page.evaluate(()=>{
    const kpis=[...document.querySelectorAll("#regionSummary .kpi")];
    const tipped=kpis.filter(k=>(k.getAttribute("data-tip")||"").trim().length>10);
    return {kpis:kpis.length, tipped:tipped.length,
      students:[...document.querySelectorAll("#regionSummary .student-kpi")]
        .filter(k=>(k.getAttribute("data-tip")||"").trim().length>10).length};
  });
  ck(tipCards.kpis>0 && tipCards.tipped===tipCards.kpis && tipCards.students===4,
     "오른쪽 카드 전체에 마우스오버 설명",JSON.stringify(tipCards));

  const kpiTipShown=await page.evaluate(async()=>{
    const el=document.querySelector("#regionSummary .kpi[data-tip]");
    const r=el.getBoundingClientRect();
    el.dispatchEvent(new MouseEvent("mouseover",{bubbles:true,clientX:r.left+5,clientY:r.top+5}));
    await new Promise(r=>setTimeout(r,120));
    const tip=document.getElementById("kpiTip");
    const b=tip.getBoundingClientRect();
    return {exists:!!tip, on:tip.classList.contains("on"), text:(tip.textContent||"").length,
      inView:b.left>=0 && b.top>=0 && b.right<=innerWidth && b.bottom<=innerHeight};
  });
  ck(kpiTipShown.exists && kpiTipShown.on && kpiTipShown.text>10 && kpiTipShown.inView,
     "오른쪽 카드 마우스오버 시 설명 말풍선 표시",JSON.stringify(kpiTipShown));

  const admTip=await page.evaluate(()=>{
    const layers=poiLayers.__adm?poiLayers.__adm.getLayers():[];
    const texts=layers.map(l=>{const t=l.getTooltip();return t?String(t.getContent()):"";});
    return {layers:layers.length, withTip:texts.filter(t=>t.length>10).length,
      sample:(texts.find(t=>t.length>10)||"").replace(/<[^>]+>/g," ").replace(/\s+/g," ").slice(0,70)};
  });
  ck(admTip.layers>0 && admTip.withTip===admTip.layers,
     "행정동 상세 지도 마우스오버 말풍선",JSON.stringify(admTip));

  /* B-10 — 회색인 **이유**가 지표마다 다르게 나오는지.
     ⚠️ "사유가 붙어 있다"만 보면 안 된다. 전부 같은 문구가 붙어도 통과해 버리는데,
        그러면 "학교가 없다"와 "거래가 없다"가 구분 안 되는 원래 문제가 그대로다.
        → 지표를 바꿔 가며 **서로 다른 문구**가 나오는지까지 본다.
     의성군은 결측이 많아 표본으로 쓴다(아파트 없는 면지역 + 학교 없는 면지역). */
  const grayReasons = await page.evaluate(async () => {
    await viewSido({code:"47",name:"경상북도"});
    await new Promise(r=>setTimeout(r,900));
    await viewSgg({code:"47730",name:"의성군"});
    await new Promise(r=>setTimeout(r,1200));
    const out = {};
    for (const k of ["median_price_per_py","apt_households","school_students","pupils_per_academy"]) {
      const sel = document.getElementById("metricSel");
      sel.value = k; sel.dispatchEvent(new Event("change"));
      await new Promise(r=>setTimeout(r,600));
      const seen = new Set();
      layer.eachLayer(l => {
        const t = l.getTooltip(); if (!t) return;
        const h = String(t.getContent());
        const m = h.match(/tip-why">([^<]+)</);
        if (m) seen.add(m[1]);
      });
      out[k] = {count:seen.size, texts:[...seen]};
    }
    return out;
  });
  const reasonTexts = Object.values(grayReasons).map(v => v.texts[0]).filter(Boolean);
  ck(reasonTexts.length===4 && new Set(reasonTexts).size===4,
     "회색 지역이 지표별로 다른 사유를 표시 (B-10)",
     JSON.stringify(Object.fromEntries(Object.entries(grayReasons).map(([k,v])=>[k,v.texts[0]||"(없음)"]))));
  /* ⚠️ 위 검사가 화면을 **의성군으로 옮겨 놨다** — 반드시 송도2동으로 되돌린다.
     뒤따르는 검사들(z-order·POI 토글·마커 팝업)이 전부 송도2동을 전제로 하고,
     안 되돌리면 "내 검사는 통과했는데 남의 검사가 죽는" 형태로 터진다. */
  await page.evaluate(async () => {
    document.getElementById("metricSel").value = "school_age";
    document.getElementById("metricSel").dispatchEvent(new Event("change"));
    await viewSido({code:"28",name:"인천광역시"});
    await viewSgg({code:"28185",name:"연수구"});
    await viewAdm({code:"28185830",name:"송도2동"});
  });
  await sleep(2000);

  const detailMapInteraction=await page.evaluate(()=>({wheel:map2.scrollWheelZoom.enabled(),zoomPosition:map2.zoomControl.getPosition()}));
  ck(detailMapInteraction.wheel && detailMapInteraction.zoomPosition==="bottomright","POI 지도 휠 확대·축소 및 우측 하단 버튼",JSON.stringify(detailMapInteraction));

  // ── ⑤ z-order ──────────────────────────────────────────────────────
  const toggles = await page.$$eval("#toggles *", els =>
    els.map(e => e.textContent.replace(/\s+/g, " ").trim()).filter(Boolean));
  ck(toggles.some(t => t.includes("자사 와이키즈")), "POI 토글 · 자사 와이키즈");
  ck(toggles.some(t => t.includes("자사 와이즈만")), "POI 토글 · 자사 와이즈만");
  ck(toggles.some(t => t.includes("경쟁사")), "POI 필터 · 경쟁사");
  ck(toggles.some(t => t.includes("초등학교")), "POI 필터 · 초등학교");
  ck(toggles.some(t => t.includes("중학교")), "POI 필터 · 중학교");
  ck(toggles.some(t => t.includes("고등학교")), "POI 필터 · 고등학교");
  ck(toggles.some(t => t.includes("시너지세권")), "POI 토글 · 시너지세권");
  const explicitFilters=await page.evaluate(()=>({
    title:document.querySelector("#toggles .toggle-group.primary .toggle-group-label")?.textContent,
    keys:[...document.querySelectorAll("#toggles .toggle-group.primary input[data-k]")].map(x=>x.dataset.k),
    checked:[...document.querySelectorAll("#toggles .toggle-group.primary input[data-k]")].every(x=>x.checked)
  }));
  ck(explicitFilters.title==="지도 필터" && explicitFilters.checked &&
     ["competitor","school_elem","school_mid","school_high","english_kinder"].every(k=>explicitFilters.keys.includes(k)),
     "요청 5종 전용 체크박스 필터 그룹",JSON.stringify(explicitFilters));
  const filterToggleResults={};
  for(const key of ["competitor","school_elem","school_mid","school_high","english_kinder"]){
    await page.click(`#toggles input[data-k="${key}"]`);
    const hidden=await page.evaluate(k=>!map2.hasLayer(poiLayers[k]),key);
    await page.click(`#toggles input[data-k="${key}"]`);
    const restored=await page.evaluate(k=>map2.hasLayer(poiLayers[k]),key);
    filterToggleResults[key]=hidden&&restored;
  }
  ck(Object.values(filterToggleResults).every(Boolean),"요청 5종 필터 숨김·복원 동작",JSON.stringify(filterToggleResults));
  // 2026-09-17 학교 = 학교급 글자 원 + 학년별 말풍선(특수·순회 제외) · 학원 등은 반지름 4px 점 유지
  const schoolFilters=await page.evaluate(()=>{
    const sch=[...poiLayers.school_elem.getLayers(),...poiLayers.school_mid.getLayers(),...poiLayers.school_high.getLayers()];
    const tip=x=>(x.getTooltip()&&x.getTooltip().getContent())||"";
    const letter=x=>{const el=x.getElement&&x.getElement(); return el?el.querySelector(".poi-school")?.textContent:"";};
    const elemTips=poiLayers.school_elem.getLayers().map(tip);
    return {
      elem:poiLayers.school_elem.getLayers().every(x=>tip(x).includes("초등학교")),
      mid:poiLayers.school_mid.getLayers().every(x=>tip(x).includes("중학교")),
      high:poiLayers.school_high.getLayers().every(x=>tip(x).includes("고등학교")),
      letters:poiLayers.school_elem.getLayers().every(x=>letter(x)==="초") &&
              poiLayers.school_mid.getLayers().every(x=>letter(x)==="중") &&
              poiLayers.school_high.getLayers().every(x=>letter(x)==="고"),
      elemGrades:elemTips.length>0 && elemTips.every(t=>/1학년 [\d,]+명 \([\d,]+학급\)/.test(t) && /6학년 [\d,]+명 \([\d,]+학급\)/.test(t)),
      midGrades:poiLayers.school_mid.getLayers().map(tip).every(t=>/3학년 [\d,]+명 \([\d,]+학급\)/.test(t) && !/4학년/.test(t)),
      noSpecial:sch.every(x=>!/특수|순회/.test(tip(x))),
      // 2026-09-18 마커 밑 숫자 = 말풍선 학년 합(특수·순회 제외). 재학생 총원이 아니다 —
      // 총원을 쓰면 71.9% 학교에서 말풍선 합과 어긋난다. 여기서 둘의 일치를 못박는다.
      nums:sch.every(x=>{
        const el=x.getElement&&x.getElement(); if(!el) return false;
        const shown=el.querySelector(".poi-school-num");
        const sum=[...tip(x).matchAll(/\d+학년 ([\d,]+)명/g)]
          .reduce((a,m)=>a+Number(m[1].replace(/,/g,"")),0);
        return sum>0 && !!shown && Number(shown.textContent.replace(/[^0-9]/g,""))===sum;
      }),
      sample:elemTips[0]||"",
      radius:[...poiLayers.academy.getLayers(),...poiLayers.english_kinder.getLayers()].every(x=>x.getRadius()===4)
    };
  });
  ck(schoolFilters.elem&&schoolFilters.mid&&schoolFilters.high,"학교명이 아닌 학교급 기준 필터 분리",JSON.stringify(schoolFilters).slice(0,200));
  ck(schoolFilters.letters,"학교 마커 = 학교급 글자(초/중/고) 아이콘",JSON.stringify(schoolFilters.letters));
  ck(schoolFilters.elemGrades&&schoolFilters.midGrades,"학교 말풍선 = 학년별 인원·학급수(초 1~6 · 중 1~3)",schoolFilters.sample.replace(/<[^>]+>/g," "));
  ck(schoolFilters.noSpecial,"학교 말풍선에 특수·순회학급 없음 (관리자 결정)");
  ck(schoolFilters.nums,"학교 마커 밑 학생수 = 말풍선 학년 합 (특수·순회 제외)",JSON.stringify(schoolFilters.nums));
  // 2026-09-18 학교 마커 겹침 처리 — **마커를 옮기지 않는다.**
  //   1차 구현은 원과 숫자를 아래로 밀어 쌓았는데, 줌 13 논현고잔동에서 사리울초가
  //   136px(약 2km) 밀려 **행정동 경계 밖에 찍혔다.** 마커 위치가 숫자 가독성보다 먼저다.
  //   그래서 ①좌표가 완전히 같은 건 spreadCoincidentRows 가 약 9m 흩뿌리고
  //         ②그 밖의 근접 겹침은 **겹치는 숫자를 숨긴다.**
  //   이 검사의 핵심은 **마커가 제 좌표에 있는가**다 — 그게 깨지면 상담에서 위치를 잘못 짚는다.
  const numStack = await page.evaluate(() => {
    const mapBox = document.getElementById("map2").getBoundingClientRect();
    let worst = 0, n = 0, shown = 0, hidden = 0;
    const boxes = [];
    for (const k of ["school_elem", "school_mid", "school_high"]) {
      for (const m of (poiLayers[k] || { getLayers: () => [] }).getLayers()) {
        const el = m.getElement && m.getElement();
        if (!el) continue;
        const dot = el.querySelector(".poi-school");
        const num = el.querySelector(".poi-school-num");
        if (!dot) continue;
        n++;
        const r = dot.getBoundingClientRect();
        const anchor = map2.latLngToContainerPoint(m.getLatLng());
        worst = Math.max(worst, Math.abs(r.top + r.height / 2 - mapBox.top - anchor.y));
        if (num) {
          if (num.style.display === "none") hidden++;
          else { shown++; boxes.push(num.getBoundingClientRect()); }
        }
      }
    }
    let overlap = 0;
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], c = boxes[j];
      if (a.left < c.right && c.left < a.right && a.top < c.bottom && c.top < a.bottom) overlap++;
    }
    return { n, shown, hidden, overlap, worstShiftPx: Math.round(worst) };
  });
  ck(numStack.n > 0 && numStack.worstShiftPx <= 1,
     "학교 마커가 제 좌표에 그려진다 (쌓기로 옮기지 않음)", JSON.stringify(numStack));
  ck(numStack.shown > 0 && numStack.overlap === 0,
     "보이는 학교 숫자끼리 겹치지 않음 (겹치면 숨김)", JSON.stringify(numStack));



  ck(schoolFilters.radius,"점 마커(학원·시너지세권) 반지름 4px",JSON.stringify(schoolFilters.radius));

  // 2026-09-17 자사·경쟁사 = 브랜드명 이름표(HTML 마커). 마커 층은 학원·아파트 점(오버레이 층)보다 항상 위다.
  const ord = await page.evaluate(() => {
    const ps = [...document.querySelectorAll("#map2 path")];
    const first = c => ps.findIndex(p => (p.getAttribute("fill") || "").toLowerCase() === c);
    const last = c => { let i = -1; ps.forEach((p, j) => { if ((p.getAttribute("fill") || "").toLowerCase() === c) i = j; }); return i; };
    const pane = document.querySelector("#map2 .leaflet-marker-pane");
    const ov = document.querySelector("#map2 .leaflet-overlay-pane");
    const labels = [...pane.querySelectorAll(".poi-label")].map(x => x.textContent.trim());
    return { total: ps.length, kinder: first("#0e9f6e"), acaLast: last("#eb6834"), aptLast: last("#4a3aa7"),
             paneAbove: Number(getComputedStyle(pane).zIndex) > Number(getComputedStyle(ov).zIndex),
             own: labels.filter(t => t === "와이키즈").length, wiseman: labels.filter(t => t === "와이즈만").length,
             comp: poiLayers.competitor.getLayers().length, compLabels: poiLayers.competitor.getLayers()
               .map(l => l.getElement()?.querySelector(".poi-label")?.textContent.trim()),
             brands: POI_BRAND_FILTERS.competitor };
  });
  console.log("     marker order:", JSON.stringify({...ord, compLabels: undefined, brands: undefined}));
  ck(ord.paneAbove && ord.own >= 1, "자사 이름표가 학원·아파트 점보다 위 (마커 층)", JSON.stringify(ord.own));
  ck(ord.paneAbove && ord.wiseman >= 1, "와이즈만 이름표가 학원보다 위 (마커 층)", JSON.stringify(ord.wiseman));
  ck(ord.paneAbove && ord.comp === ord.compLabels.length && ord.compLabels.every(t => ord.brands.includes(t)),
     "경쟁사 이름표 = 브랜드명만", JSON.stringify(ord.compLabels));
  ck(ord.kinder > ord.acaLast, "시너지세권 마커가 학원보다 위");

  // 같은 건물 경쟁사 = 세로로 쌓기 (송도2동 신송로 170: 아담리즈 인천송도센터 + CMS 송도영재교육센터)
  const stack = await page.evaluate(() => {
    const els = [...poiLayers.own_waikids.getLayers(), ...poiLayers.own_wiseman.getLayers(), ...poiLayers.competitor.getLayers()]
      .map(l => ({ ll: l.getLatLng(), el: l.getElement()?.querySelector(".poi-label") })).filter(x => x.el);
    const groups = {};
    els.forEach(x => { const k = x.ll.lat.toFixed(5) + "," + x.ll.lng.toFixed(5); (groups[k] = groups[k] || []).push(x); });
    const multi = Object.values(groups).filter(g => g.length > 1);
    const overlap = (a, b) => !(a.bottom <= b.top || b.bottom <= a.top || a.right <= b.left || b.right <= a.left);
    const bad = multi.filter(g => g.some((a, i) => g.some((b, j) => i < j && overlap(a.el.getBoundingClientRect(), b.el.getBoundingClientRect()))));
    return { groups: multi.length, sizes: multi.map(g => g.map(x => x.el.textContent.trim())), bad: bad.length };
  });
  ck(stack.groups >= 1 && stack.bad === 0, "같은 건물 이름표 세로 쌓기 (겹침 0)", JSON.stringify(stack));

  // 팝업 = 브랜드 + 분류
  await page.evaluate(() => {
    const el = poiLayers.own_waikids.getLayers()[0].getElement().querySelector(".poi-label");
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await sleep(600);
  const pop = (await page.textContent(".leaflet-popup-content").catch(() => "")) || "";
  ck(/와이키즈|와이즈만/.test(pop) && pop.includes("자사 와이키즈"), "자사 마커 팝업 = 브랜드 + 분류",
     pop.replace(/\s+/g, " ").slice(0, 60));

  const vworldRetina = await page.evaluate(() => BASEMAPS
    .filter(b=>b.k.startsWith("vworld"))
    .every(b=>b.build(state.vworldKey)[0].options.tileSize===256 &&
              b.build(state.vworldKey)[0].options.zoomOffset===0 &&
              b.build(state.vworldKey)[0].options.updateWhenZooming===true));
  ck(vworldRetina, "VWorld 고해상도 타일 옵션");

  await page.evaluate(async()=>{
    await viewSido({code:"41",name:"경기도"});
    await viewSgg({code:"41570",name:"김포시"});
    await viewAdm({code:"41570580",name:"운양동"});
    state.basemap="vworld_label";
    drawBasemapButtons(); applyBasemap();
  });
  await sleep(2500);
  await ckVworldTiles(page, '#map2 img[src*="api.vworld.kr"]', "김포시 운양동 VWorld 위성+라벨 타일",
    async () => { await page.evaluate(()=>{ state.basemap="vworld_label"; applyBasemap(); }); });
  await page.screenshot({ path:path.join(SHOT,"4_gimpo_unyang_vworld.png") });

  // ── ⑦ 토글 재적용 ──────────────────────────────────────────────────
  const btn = await page.$('#toggles input[data-k="competitor"]');
  if (btn) {
    await btn.click(); await sleep(300);
    await btn.click(); await sleep(600);
    const after = await page.evaluate(() => ({
      layers: poiLayers.competitor.getLayers().length,
      inMarkerPane: document.querySelectorAll("#map2 .leaflet-marker-pane .poi-label").length >=
                    poiLayers.competitor.getLayers().length }));
    ck(after.layers > 0 && after.inMarkerPane, "토글 껐다 켠 뒤에도 경쟁사 이름표가 마커 층(학원 위)", JSON.stringify(after));
  }
  await page.screenshot({ path: path.join(SHOT, "3_adm_detail.png"), fullPage: true });

  const resetButtonPosition=await page.$eval('#inlinePoiView [data-dashboard-reset]',button=>{
    const b=button.getBoundingClientRect();
    const shell=button.closest('.poi-map-shell').getBoundingClientRect();
    return {text:button.textContent.trim(),left:Math.round(b.left-shell.left),top:Math.round(b.top-shell.top)};
  });
  ck(resetButtonPosition.text==="새로고침" && resetButtonPosition.left<=10 && resetButtonPosition.top<=10,
     "지도 왼쪽 상단 새로고침 버튼",JSON.stringify(resetButtonPosition));
  await page.click('#inlinePoiView [data-dashboard-reset]');
  await page.waitForFunction(()=>state.level==="nation" && layer && layer.getLayers().length===16);
  await sleep(300);
  const resetState=await page.evaluate(()=>(
    {level:state.level,ages:state.selectedAges.size,selectedRegions:state.selectedCodes.size,
     competitorBrands:state.selectedPoiBrands.competitor.size,
     synergyBrands:state.selectedPoiBrands.english_kinder.size,basemap:state.basemap,
     overviewVisible:!document.getElementById("overviewMapView").classList.contains("hidden"),
     metric:curMetric.k,
     competitorTrigger:document.querySelector('#competitorBrandFilter .poi-brand-filter-trigger').textContent.trim(),
     synergyTrigger:document.querySelector('#synergyBrandFilter .poi-brand-filter-trigger').textContent.trim()}
  ));
  ck(resetState.level==="nation" && resetState.ages===14 && resetState.selectedRegions===0 &&
     resetState.competitorBrands===10 && resetState.synergyBrands===2 && resetState.basemap==="plain" &&
     resetState.overviewVisible && resetState.metric==="school_age" &&
     resetState.competitorTrigger==="전체 10개 v" && resetState.synergyTrigger==="전체 2개 v",
     "새로고침 버튼으로 전국 초기 상태 복원",JSON.stringify(resetState));

  // ── ⑧ 시군구 다중 선택 → 행정동 고르기 (A안, 2026-10-07) ─────────────
  // 시군구 2개를 고르면 지도 하단 버튼으로 두 시군구의 동 지도로 내려가고,
  // [다중 선택]을 켠 채 동을 클릭해도 다른 시군구가 선택에서 빠지지 않아야 한다.
  await page.evaluate(async()=>{ await viewSido({code:"41",name:"경기도"}); });
  await page.waitForFunction(()=>state.selectionContext&&state.selectionContext.level==="sgg");
  await page.click('#overviewMapView [data-map-multi]');
  const clickFeature=code=>page.evaluate(code=>{
    layer.eachLayer(l=>{ if(String(l.feature.properties.code)===code) l.fire("click",{originalEvent:{}}); });
  },code);
  await clickFeature("41360"); await clickFeature("41310");
  const drillBtn=await page.$eval('#mapDrillButton',b=>({shown:!b.classList.contains("hidden"),text:b.textContent.trim()}));
  ck(drillBtn.shown && drillBtn.text.includes("2개 시군구"), "[다중 선택] 지도 클릭으로 시군구 2개 → 행정동 버튼 노출", JSON.stringify(drillBtn));
  await page.click('#mapDrillButton');
  await page.waitForFunction(()=>state.selectionContext&&state.selectionContext.level==="adm");
  const rowsBySgg=await page.evaluate(()=>[...new Set(state.selectionContext.rows.map(r=>String(r.code).slice(0,5)))].sort());
  ck(rowsBySgg.join()==="41310,41360", "행정동 지도 = 고른 두 시군구의 동 합집합", rowsBySgg.join());
  const [dA,dB]=await page.evaluate(()=>{
    const rows=state.selectionContext.rows;
    return [rows.find(r=>String(r.code).startsWith("41360")).code,rows.find(r=>String(r.code).startsWith("41310")).code].map(String);
  });
  await clickFeature(dA); await sleep(300);
  await page.evaluate(code=>toggleMapRegionSelection(code),dB); await sleep(800);
  const afterPick=await page.evaluate(()=>({sggs:state.sggs.length,picked:[...state.selectedCodes].length,label:document.querySelector('.region-step[data-filter-level="sgg"]').textContent.trim()}));
  ck(afterPick.sggs===2 && afterPick.picked===2, "동을 골라도 시군구 2개 유지", JSON.stringify(afterPick));
  const rowH=await page.evaluate(()=>{ openRegionFilterLevel("adm"); const r=document.querySelector('#rank .row'); return r?Math.round(r.getBoundingClientRect().height):0; });
  ck(rowH>=28, "지역 필터 체크박스 행 높이 ≥28px (느슨하게)", rowH);
  await page.evaluate(()=>setRankFilterOpen(false));

  ck(errors.length === 0, "JS 런타임 에러 없음", errors.slice(0, 3).join(" / "));

  await browser.close();
  if (!KEEP) server.kill();

  const fail = R.filter(r => r[0] === "FAIL");
  console.log("\n" + "=".repeat(78));
  const warns = R.filter(r => r[0] === "WARN");
  console.log(`판정 — PASS ${R.length - fail.length - warns.length} · FAIL ${fail.length} · WARN ${warns.length}`);
  warns.forEach(w => console.log(`  [~~] ${w[1]} — ${w[2]}`));
  fail.forEach(f => console.log(`  [!!] ${f[1]} — ${f[2]}`));
  console.log("=".repeat(78));
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error("[FATAL]", e); process.exit(2); });
