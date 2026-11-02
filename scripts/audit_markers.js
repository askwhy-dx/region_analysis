/* audit_markers.js — 지도 마킹 화면 감사 (읽기 전용)
 *
 *   node scripts/audit_markers.js                 위험 지역 자동 선정 8곳
 *   node scripts/audit_markers.js --n 20          표본 수
 *   node scripts/audit_markers.js --codes 28200710,41570580
 *   node scripts/audit_markers.js --shots D:\out  스크린샷 위치
 *   node scripts/audit_markers.js --selftest      점검기 자체 시험 (일부러 깨뜨려 본다)
 *
 * 왜 이게 따로 있나
 * -----------------
 * verify_dashboard.js 는 **정해둔 것**을 검사한다. 그래서 2026-09-18 에 두 번,
 * 숫자로는 전부 PASS 인데 눈으로 보면 틀린 화면이 나갔다:
 *   (1) 학교 원 하나 밑에 숫자가 둘 — 「숫자끼리 겹침 0」을 통과했다(숫자는 안 겹쳤다)
 *   (2) 마커가 실제 좌표에서 2km 밀림 — 「원·숫자 겹침 0」을 통과했다(겹침은 없었다)
 * 검사가 **목적**만 보고 **전제**를 안 봤기 때문이다. 이 파일은 전제 쪽을 본다:
 *   마커는 제 좌표에 있는가 · 선택한 동 안에 있는가 · 가려지지 않았는가 · 카드 숫자와 맞는가.
 *
 * 판정이 애매한 것은 사람이 아니라 **LLM 이 보도록** 스크린샷을 남긴다.
 * 위반이 난 지역은 전체·지도 2장을 찍는다 → .claude/agents/marker-audit.md 가 읽는다.
 *
 * 종료코드: 위반·JS에러가 있으면 1, 없으면 0 (--selftest 는 항상 0)
 * 리포트  : <shots>/marker_audit.json
 *
 * ⚠️ 읽기 전용이다. 산출물·화면을 고치지 않는다.
 */
const path = require("path");
const os = require("os");
const fs = require("fs");
const { spawn } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const { chromium } = require(path.join(ROOT, "..", "node_modules", "playwright"));
const PY = "C:/Users/User/AppData/Local/Programs/Python/Python312/python.exe";
const PROC = path.join(ROOT, "data", "processed");
const PORT = 8813;
const BASE = "http://127.0.0.1:" + PORT + "/web/";

const argv = process.argv.slice(2);
const arg = (k, d) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : d; };
const N = Number(arg("--n", 8));
const ONLY = (arg("--codes", "") || "").split(",").filter(Boolean);
const SHOT = arg("--shots", path.join(os.tmpdir(), "jiyeok_marker_audit"));
const SELFTEST = argv.includes("--selftest");
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* 허용 오차 — 왜 이 값인가
 *   SHIFT_PX  : 마커는 제 좌표에 그려져야 한다. 1px 는 반올림 여유다.
 *   SPREAD_M  : 좌표가 완전히 같은 POI 만 약 39m 흩뿌린다(index.html SPREAD_DEG). 60m 는 그 여유.
 *   OUTSIDE_M : 경계선 위의 학교는 폴리곤 밖으로 조금 나갈 수 있다. 그 이상이면 배정이 틀린 것이다. */
const SHIFT_PX = 1, SPREAD_M = 60, OUTSIDE_M = 120;

/* 표본 선정 — 무작위로만 뽑으면 **아무 일도 없는 동**만 보게 된다.
   지금까지 문제가 난 자리의 지문(좌표 중복 · 같은 이름 겹침 · 마커 밀집)을 점수화해 위험순으로 고르고,
   평범한 동의 회귀를 놓치지 않도록 무작위 2곳을 섞는다. */
function pickCodes(n) {
  const met = JSON.parse(fs.readFileSync(path.join(PROC, "metrics.json"), "utf8")).data;
  const risk = new Map();
  const bump = (code, why, w) => {
    if (!code || !met[code]) return;
    const r = risk.get(code) || { score: 0, why: [] };
    r.score += w;
    if (!r.why.includes(why)) r.why.push(why);
    risk.set(code, r);
  };
  for (const f of fs.readdirSync(PROC)) {
    if (!/^poi_\d{5}\.json$/.test(f)) continue;
    const d = JSON.parse(fs.readFileSync(path.join(PROC, f), "utf8"));
    for (const kind of ["school", "academy", "apartment", "own", "competitor", "english_kinder"]) {
      const rows = d[kind] || [];
      const at = new Map();
      for (const r of rows) {
        if (!r.lat) continue;
        const key = r.lat.toFixed(6) + "," + r.lng.toFixed(6);
        const prev = at.get(key) || [];
        prev.push(r);
        at.set(key, prev);
      }
      for (const group of at.values()) {
        if (group.length < 2) continue;
        const code = String(group[0].adm_cd || "");
        const sameName = new Set(group.map(r => r.name)).size < group.length;
        bump(code, sameName ? kind + " 같은이름·같은좌표" : kind + " 좌표중복 " + group.length,
             sameName ? 12 : 3 + group.length);
      }
      if (kind === "school") {
        const byAdm = new Map();
        for (const r of rows) byAdm.set(String(r.adm_cd), (byAdm.get(String(r.adm_cd)) || 0) + 1);
        for (const [code, cnt] of byAdm) if (cnt >= 6) bump(code, "학교 " + cnt + "개 밀집", cnt);
      }
    }
  }
  const ranked = [...risk.entries()].sort((a, b) => b[1].score - a[1].score);
  const out = ranked.slice(0, Math.max(1, n - 2)).map(e => ({ code: e[0], why: e[1].why.join(" · ") }));
  const all = Object.keys(met);
  while (out.length < n) {
    const c = all[Math.floor(Math.random() * all.length)];
    if (!out.some(o => o.code === c)) out.push({ code: c, why: "무작위 표본" });
  }
  return out;
}

async function serve() {
  const p = spawn(fs.existsSync(PY) ? PY : "python",
                  ["-m", "http.server", String(PORT), "--bind", "127.0.0.1"],
                  { cwd: ROOT, stdio: "ignore" });
  for (let i = 0; i < 40; i++) {
    await sleep(250);
    try { if ((await fetch(BASE)).ok) return p; } catch (e) { /* 아직 안 떴다 */ }
  }
  throw new Error("정적 서버가 안 뜬다 (" + BASE + ")");
}

/* 규칙 주입 케이스 — **규칙을 새로 넣을 때 여기에도 케이스를 넣어야 등록이 끝난다.**
   규칙만 넣고 케이스를 빠뜨리면 「그 규칙이 실제로 잡는지」 아무도 모른다.
   2026-09-18 에 그렇게 만든 검사가 결함 두 건을 통과시켰다(숫자 겹침만 보다가 마커 2km 밀림을 놓쳤다).
   각 케이스는 화면 DOM 을 일부러 깨뜨리고, --selftest 가 그 규칙이 검출되는지 확인한다. */
const INJECT = {
  /* 마커를 화면에서만 옮기면(스타일 조작) 감사가 읽는 latlng 은 그대로라 규칙이 안 걸린다.
     첫 시험에서 3개 규칙이 그래서 「못 잡는다」로 나왔다 — 규칙이 아니라 케이스가 틀렸던 것이다.
     그래서 케이스는 **Leaflet 마커를 실제로 옮긴다**(setLatLng). 화면과 데이터가 함께 움직인다.
     ⚠️ 옮긴 뒤 restackSchoolNums() 를 부르지 않는다 — 부르면 숨김 처리가 결함을 지워 시험이 무의미해진다. */
  "마커가 제 좌표에서 밀렸다": () => {
    const el = document.querySelector(".poi-school-icon .poi-school");
    if (el) { el.style.top = "51px"; return true; }        // -9 → 51 = 60px 밀림 (화면만 어긋난 상태)
    return false;
  },
  "학교 원이 거의 완전히 포개진다": () => {
    const ms = window.__auditSchools();
    if (ms.length < 2) return false;
    ms[1].setLatLng(ms[0].getLatLng());                    // 좌표를 완전히 같게
    return true;
  },
  "학교 숫자끼리 겹친다": () => {
    const ms = window.__auditSchools().filter(m => {
      const el = m.getElement && m.getElement();
      const n = el && el.querySelector(".poi-school-num");
      return n && n.style.display !== "none";
    });
    if (ms.length < 2) return false;
    const a = ms[0].getLatLng();
    ms[1].setLatLng([a.lat, a.lng + 0.00004]);             // 원은 비껴 두고 숫자만 겹치게
    return true;
  },
  "학교 숫자가 다른 학교 원에 가린다": () => {
    const ms = window.__auditSchools();
    if (ms.length < 2) return false;
    const a = ms[0].getLatLng();
    const z = map2.getZoom();
    const dLat = 26 * (156543.03392 * Math.cos(a.lat * Math.PI / 180) / Math.pow(2, z)) / 111000;
    ms[1].setLatLng([a.lat - dLat, a.lng]);                // 26px 아래 = 앞 마커의 숫자 자리
    return true;
  },
  "마커가 선택 행정동 경계 밖": () => {
    const ms = window.__auditSchools();
    if (!ms.length) return false;
    const a = ms[0].getLatLng();
    ms[0].setLatLng([a.lat + 0.02, a.lng + 0.02]);         // 약 2.5km 밖으로
    return true;
  },
  "카드 개수 != 그려진 마커 수": () => {
    const c = document.querySelector("#toggles .poi-filter-chip .cnt");
    if (!c) return false;
    c.textContent = String(Number(c.textContent.replace(/[^0-9]/g, "")) + 7);
    return true;
  },
  "마커가 지도 영역 밖": () => {
    /* 앵커가 지도 안인 마커를 골라야 한다 — 규칙이 그 조건으로 좁혀져 있다 */
    const box = document.getElementById("map2").getBoundingClientRect();
    const target = window.__auditSchools().find(m => {
      const q = map2.latLngToContainerPoint(m.getLatLng());
      return q.x >= 0 && q.y >= 0 && q.x <= box.width && q.y <= box.height;
    });
    const el = target && target.getElement().querySelector(".poi-school");
    if (el) { el.style.top = "-99999px"; return true; }
    return false;
  },
};

/* 한 지역을 열고 화면에서 불변식을 계산한다.
   inject = 규칙 이름이면 그 규칙을 **일부러 위반**시킨 뒤 검사한다(--selftest). */
async function auditOne(page, code, inject) {
  await page.evaluate(async c => {
    await viewSido({ code: c.slice(0, 2), name: c.slice(0, 2) });
    await viewSgg({ code: c.slice(0, 5), name: c.slice(0, 5) });
    await viewAdm({ code: c, name: c });
  }, code);
  await sleep(2600);
  let injected = null;
  if (inject) {
    /* 케이스가 마커를 직접 만질 수 있도록 도우미를 심는다 (감사 대상 코드는 건드리지 않는다) */
    await page.evaluate(() => {
      window.__auditSchools = () => ["school_elem", "school_mid", "school_high"]
        .flatMap(k => (poiLayers[k] && poiLayers[k].getLayers) ? poiLayers[k].getLayers() : [])
        .filter(m => m.getElement && m.getElement());
    });
    injected = await page.evaluate(([name, src]) => {
      try { return (new Function("return (" + src + ")"))()() ? true : false; }
      catch (e) { return "ERR:" + (e.message || e); }
    }, [inject, INJECT[inject].toString()]);
    await sleep(300);
  }
  return await page.evaluate(([code, LIM]) => {
    const SHIFT_PX = LIM[0], SPREAD_M = LIM[1], OUTSIDE_M = LIM[2];
    const inRing = (x, y, R) => {
      let s = false;
      for (let i = 0, n = R.length; i < n; i++) {
        const a = R[i], b = R[(i + 1) % n];
        if ((a[1] > y) !== (b[1] > y)) {
          const xi = a[0] + (y - a[1]) / (b[1] - a[1]) * (b[0] - a[0]);
          if (x < xi) s = !s;
        }
      }
      return s;
    };
    const segDistM = (px, py, x1, y1, x2, y2) => {
      const kx = 88000, ky = 111000;                 // 중위도 근사 (경도 1도 약 88km)
      const ax = (px - x1) * kx, ay = (py - y1) * ky, bx = (x2 - x1) * kx, by = (y2 - y1) * ky;
      const L2 = bx * bx + by * by;
      const t = L2 ? Math.max(0, Math.min(1, (ax * bx + ay * by) / L2)) : 0;
      return Math.hypot(ax - bx * t, ay - by * t);
    };
    const distToRingM = (x, y, R) => {
      let best = Infinity;
      for (let i = 0, n = R.length; i < n; i++) {
        const a = R[i], b = R[(i + 1) % n];
        best = Math.min(best, segDistM(x, y, a[0], a[1], b[0], b[1]));
      }
      return best;
    };
    const ov = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

    const mapEl = document.getElementById("map2");
    const mapBox = mapEl.getBoundingClientRect();
    const viol = [];
    /* 등급 — 오탐이 섞이면 아무도 리포트를 안 본다. 셋으로 나눈다.
         fail : 설계상 일어나면 안 되는 것 (마커 위치 · 학교 라벨 · 카드 불일치)
         warn : 틀렸을 가능성이 크지만 경계·원천 문제일 수도 있는 것
         info : 참고만 (경미한 경계 이탈) */
    const add = (level, rule, detail) => viol.push({ level: level, rule: rule, detail: detail });
    const SCHOOL = k => k === "school_elem" || k === "school_mid" || k === "school_high";

    // 선택한 동의 폴리곤 (조각이 여러 개일 수 있다)
    const rings = [];
    map2.eachLayer(l => {
      if (l.feature && l.feature.properties && String(l.feature.properties.code) === code && l.getLatLngs) {
        const walk = a => {
          if (!Array.isArray(a)) return;
          if (a.length && a[0] && typeof a[0].lat === "number") rings.push(a.map(p => [p.lng, p.lat]));
          else a.forEach(walk);
        };
        walk(l.getLatLngs());
      }
    });

    // 마커 수집 (학교 = 원+숫자 / 자사·경쟁사 = 이름표 / 나머지 = 점)
    const marks = [];
    for (const kind of Object.keys(poiLayers || {})) {
      const layer = poiLayers[kind];
      if (!layer || !map2.hasLayer(layer)) continue;
      for (const m of layer.getLayers()) {
        const ll = m.getLatLng ? m.getLatLng() : null;
        if (!ll) continue;
        const anchor = map2.latLngToContainerPoint(ll);
        const el = m.getElement && m.getElement();
        const dot = el && (el.querySelector(".poi-school") || el.querySelector(".poi-label-pin"));
        const num = el && el.querySelector(".poi-school-num");
        const label = el && el.querySelector(".poi-label");
        const raw = ((m.getTooltip && m.getTooltip() && m.getTooltip().getContent()) ||
                     (m.getPopup && m.getPopup() && m.getPopup().getContent()) || "");
        const tip = String(raw).replace(/<br\s*\/?>/g, " ").replace(/<[^>]+>/g, "")
                      .trim().split(/\s{2,}/)[0].slice(0, 24);
        marks.push({
          kind: kind, lat: ll.lat, lng: ll.lng, anchor: anchor, name: tip || "(무명)",
          dotBox: dot ? dot.getBoundingClientRect() : null,
          numBox: (num && num.style.display !== "none") ? num.getBoundingClientRect() : null,
          labelBox: label ? label.getBoundingClientRect() : null
        });
      }
    }

    // (1) 마커는 제 좌표에 그려진다 — 쌓기·오프셋으로 옮기면 상담에서 위치를 잘못 짚는다
    for (const m of marks) {
      if (!m.dotBox) continue;
      const dy = Math.abs(m.dotBox.top + m.dotBox.height / 2 - mapBox.top - m.anchor.y);
      const dx = Math.abs(m.dotBox.left + m.dotBox.width / 2 - mapBox.left - m.anchor.x);
      if (dy > SHIFT_PX || dx > SHIFT_PX)
        add("fail", "마커가 제 좌표에서 밀렸다",
            m.kind + " " + m.name + " · dx " + Math.round(dx) + "px · dy " + Math.round(dy) + "px");
    }

    // (2) 선택한 동의 POI 는 그 동 폴리곤 안에 있어야 한다
    if (rings.length) {
      for (const m of marks) {
        if (rings.some(R => inRing(m.lng, m.lat, R))) continue;
        let d = Infinity;
        for (const R of rings) d = Math.min(d, distToRingM(m.lng, m.lat, R));
        if (d > OUTSIDE_M)
          add("warn", "마커가 선택 행정동 경계 밖", m.kind + " " + m.name + " · 경계에서 " + Math.round(d) + "m");
        else if (d > SPREAD_M)
          add("info", "마커가 경계 밖 (경미)", m.kind + " " + m.name + " · " + Math.round(d) + "m");
      }
    } else {
      add("fail", "선택 행정동 폴리곤을 화면에서 못 찾음", code);
    }

    // (3) 보이는 라벨끼리 겹치지 않는다 (학교 숫자 · 브랜드 이름표)
    /* 브랜드 이름표(.poi-label)는 stackLabelRows 가 **같은 건물에 세로로 쌓도록 만든 것**이라
       서로 겹치거나 점을 가리는 게 정상이다. 여기서 보는 것은 학교 숫자뿐이다. */
    const labels = [];
    marks.forEach(m => { if (m.numBox) labels.push({ b: m.numBox, t: m.kind + " " + m.name + " 숫자" }); });
    for (let i = 0; i < labels.length; i++)
      for (let j = i + 1; j < labels.length; j++)
        if (ov(labels[i].b, labels[j].b)) add("fail", "학교 숫자끼리 겹친다", labels[i].t + " ↔ " + labels[j].t);

    // (4) 라벨이 **다른** 마커의 원에 가리지 않는다 — 2026-09-18 「한 학교에 숫자 둘」의 지문
    for (const m of marks) {
      if (!m.numBox) continue;
      for (const o of marks) {
        if (o === m || !o.dotBox || !SCHOOL(o.kind)) continue;
        if (Math.abs(o.anchor.x - m.anchor.x) < 0.5 && Math.abs(o.anchor.y - m.anchor.y) < 0.5) continue;
        if (ov(m.numBox, o.dotBox))
          add("fail", "학교 숫자가 다른 학교 원에 가린다", m.kind + " " + m.name + " -> " + o.kind + " " + o.name);
      }
    }

    // (5) 원끼리 거의 완전히 포개지지 않는다 — 좌표가 같은 다른 학교가 하나로 보인다
    /* 자사와 경쟁사가 같은 건물이면 핀이 포개지는 게 정상이다(이름표로 구분한다).
       학교는 원 자체가 식별 수단이라 포개지면 한 학교로 보인다 — 학교끼리만 본다.
       ⚠️ 배율이 낮으면 **무엇이든 포개진다.** 좌표가 같은 학교를 약 39m 흩뿌려도 줌 13 에서는
       1.3px 차이라 붙어 보인다 — 그건 지도의 본성이지 결함이 아니다. 줌 15 이상에서만 FAIL 로 본다. */
    const dots = marks.filter(m => m.dotBox && SCHOOL(m.kind));
    for (let i = 0; i < dots.length; i++)
      for (let j = i + 1; j < dots.length; j++) {
        const a = dots[i], b = dots[j];
        if (Math.hypot(a.anchor.x - b.anchor.x, a.anchor.y - b.anchor.y) >= 3) continue;
        add(map2.getZoom() >= 15 ? "fail" : "info", "학교 원이 거의 완전히 포개진다",
            a.kind + " " + a.name + " ↔ " + b.kind + " " + b.name +
            (map2.getZoom() >= 15 ? "" : " (줌 " + map2.getZoom() + " — 배율 탓일 수 있다)"));
      }

    // (6) 카드 개수와 그려진 마커 수가 맞는다
    const chip = {};
    document.querySelectorAll("#toggles .poi-filter-chip").forEach(el => {
      const k = el.querySelector("input[data-k]");
      const c = el.querySelector(".cnt");
      if (k && c) chip[k.dataset.k] = Number(c.textContent.replace(/[^0-9]/g, ""));
    });
    for (const k of Object.keys(chip)) {
      const drawn = (poiLayers[k] && poiLayers[k].getLayers) ? poiLayers[k].getLayers().length : 0;
      if (chip[k] !== drawn)
        add("fail", "카드 개수 != 그려진 마커 수", k + " 카드 " + chip[k] + " vs 마커 " + drawn);
    }

    // (7) 렌더링 파손 — **앵커는 지도 안인데 그려진 박스는 밖**인 경우만 본다.
    //     ⚠️ Leaflet 은 화면 밖 마커도 DOM 에 유지한다(keepBuffer). 박스가 지도 밖이라는 것만으로
    //        잡으면 패닝하면 보이는 정상 마커가 걸린다 — 2026-09-18 실측 오탐 13건.
    for (const m of marks) {
      if (!m.dotBox) continue;
      const anchorIn = m.anchor.x >= 0 && m.anchor.y >= 0 &&
                       m.anchor.x <= mapBox.width && m.anchor.y <= mapBox.height;
      if (!anchorIn) continue;
      if (m.dotBox.bottom < mapBox.top || m.dotBox.top > mapBox.bottom ||
          m.dotBox.right < mapBox.left || m.dotBox.left > mapBox.right)
        add("fail", "마커가 지도 영역 밖", m.kind + " " + m.name +
            " · 앵커 (" + Math.round(m.anchor.x) + "," + Math.round(m.anchor.y) + ") 는 지도 안이다");
    }

    const counts = {};
    marks.forEach(m => counts[m.kind] = (counts[m.kind] || 0) + 1);
    const schoolTotal = marks.filter(m => SCHOOL(m.kind)).length;
    const numHidden = marks.filter(m => SCHOOL(m.kind) && !m.numBox).length;
    const nmEl = document.querySelector(".selection-text") || document.querySelector(".hero .nm");
    return {
      code: code, name: nmEl ? nmEl.textContent.trim() : "", zoom: map2.getZoom(),
      marks: marks.length, counts: counts, labelsShown: labels.length,
      schoolTotal: schoolTotal, schoolNumHidden: numHidden,
      rings: rings.length, viol: viol
    };
  }, [code, [SHIFT_PX, SPREAD_M, OUTSIDE_M]]).then(r => {
    r.injected = injected;
    return r;
  });
}

/* --selftest : 규칙을 하나씩 일부러 위반시켜 **그 규칙이 검출되는지** 확인한다.
   규칙 대장은 docs/MARKER_RULES.md — 규칙을 넣고 케이스를 안 넣으면 여기서 「케이스 없음」으로 드러난다. */
async function runSelftest(page, code) {
  const names = Object.keys(INJECT);
  console.log("규칙 주입 시험 — " + names.length + "개 규칙 · 표본 " + code + "\n");
  const rows = [];
  for (const name of names) {
    await page.goto(BASE, { waitUntil: "networkidle" });   // 주입을 남기지 않으려 매번 새로 연다
    await sleep(1200);
    const r = await auditOne(page, code, name);
    const caught = r.viol.some(v => v.rule === name);
    const skipped = r.injected !== true;
    rows.push({ rule: name, injected: r.injected, caught: caught });
    const mark = skipped ? "[??]" : (caught ? "[OK]" : "[!!]");
    const note = skipped ? "주입 못 함(" + r.injected + ") — 이 표본에 대상 마커가 없다"
                         : (caught ? "잡았다" : "**못 잡는다 — 규칙이 헛돈다**");
    console.log("  " + mark + " " + name + " — " + note);
  }
  const bad = rows.filter(r => r.injected === true && !r.caught);
  const skip = rows.filter(r => r.injected !== true);
  console.log("\n" + "=".repeat(78));
  console.log("자체 시험 — 규칙 " + rows.length + " · 검출 " + rows.filter(r => r.caught).length +
              " · 실패 " + bad.length + " · 주입 불가 " + skip.length);
  if (bad.length) bad.forEach(r => console.log("  [!!] " + r.rule + " 규칙이 위반을 못 잡는다"));
  if (skip.length) console.log("  [??] 주입 불가 규칙은 다른 표본(--codes)으로 다시 볼 것: " +
                               skip.map(r => r.rule).join(" , "));
  console.log("=".repeat(78));
  return { rows: rows, failed: bad.length };
}

(async () => {
  fs.mkdirSync(SHOT, { recursive: true });
  const targets = ONLY.length ? ONLY.map(c => ({ code: c, why: "지정" })) : pickCodes(N);
  const server = await serve();
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const errs = [];
  page.on("pageerror", e => errs.push(String(e)));
  page.on("console", m => { if (m.type() === "error") errs.push("console: " + m.text()); });

  console.log("=".repeat(78));
  console.log("마킹 화면 감사" + (SELFTEST ? " — 자체 시험 모드" : ""));
  console.log("=".repeat(78));
  await page.goto(BASE, { waitUntil: "networkidle" });
  await sleep(1500);
  const mock = await page.evaluate(() => !!state.usedMock);
  if (mock) console.log("  [!!] mock 폴백 상태다 — 실데이터로 다시 볼 것");

  if (SELFTEST) {
    const st = await runSelftest(page, targets[0].code);
    fs.writeFileSync(path.join(SHOT, "marker_selftest.json"), JSON.stringify(st, null, 1), "utf8");
    console.log("리포트 → " + path.join(SHOT, "marker_selftest.json"));
    await browser.close();
    server.kill();
    process.exit(st.failed ? 1 : 0);
  }

  const report = {
    generated: new Date().toISOString(), base: BASE, selftest: SELFTEST,
    mock: mock, limits: { SHIFT_PX, SPREAD_M, OUTSIDE_M }, regions: [], jsErrors: []
  };
  for (const t of targets) {
    let r;
    try {
      r = await auditOne(page, t.code, null);
    } catch (e) {
      r = { code: t.code, name: "", marks: 0,
            viol: [{ level: "fail", rule: "감사 중 예외", detail: String(e.message || e) }] };
    }
    r.why = t.why;
    const hid = r.schoolTotal ? " · 학교 숫자 숨김 " + r.schoolNumHidden + "/" + r.schoolTotal : "";
    console.log("\n[" + r.code + "] " + (r.name || "(이름없음)") + " · 줌 " + r.zoom +
                " · 마커 " + r.marks + hid + " · 표본사유: " + t.why);
    if (!r.viol.length) console.log("   위반 없음");
    const byRule = {};
    r.viol.forEach(v => { const key = v.level + "|" + v.rule; byRule[key] = (byRule[key] || 0) + 1; });
    const MARK = { fail: "[!!]", warn: "[~~]", info: "[..]" };
    for (const key of Object.keys(byRule).sort()) {
      const level = key.split("|")[0], rule = key.slice(level.length + 1);
      console.log("   " + (MARK[level] || "[??]") + " " + rule + " — " + byRule[key] + "건");
      r.viol.filter(v => v.rule === rule).slice(0, 3).forEach(v => console.log("        " + v.detail));
    }
    r.fails = r.viol.filter(v => v.level === "fail").length;
    r.warns = r.viol.filter(v => v.level === "warn").length;
    if (r.fails || r.warns) {
      const f1 = path.join(SHOT, r.code + "_full.png");
      await page.screenshot({ path: f1 });
      const el = await page.$("#map2");
      const f2 = path.join(SHOT, r.code + "_map.png");
      if (el) await el.screenshot({ path: f2 });
      r.shots = el ? [f1, f2] : [f1];
      console.log("        스크린샷 → " + r.shots.join(" , "));
    }
    report.regions.push(r);
  }
  report.jsErrors = errs;
  const sum = lv => report.regions.reduce((s, r) => s + r.viol.filter(v => v.level === lv).length, 0);
  const fails = sum("fail"), warns = sum("warn"), infos = sum("info");
  const badRegions = report.regions.filter(r => r.viol.some(v => v.level === "fail")).length;
  report.summary = { regions: report.regions.length, fails: fails, warns: warns, infos: infos,
                     failRegions: badRegions, jsErrors: errs.length };
  console.log("\n" + "=".repeat(78));
  console.log("판정 — 지역 " + report.regions.length + "곳 · FAIL " + fails + "건(" + badRegions +
              "곳) · WARN " + warns + " · INFO " + infos + " · JS 에러 " + errs.length);
  console.log("=".repeat(78));
  const out = path.join(SHOT, "marker_audit.json");
  fs.writeFileSync(out, JSON.stringify(report, null, 1), "utf8");
  console.log("리포트 → " + out);
  await browser.close();
  server.kill();
  process.exit((fails || errs.length) ? 1 : 0);
})();
