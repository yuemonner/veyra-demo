// UAV-SEAD test-set selection (pre-registered, metadata only; no flight data is read).
// Run in a browser tab on https://huggingface.co (same origin), e.g. the dataset page.
// It reads mapping.json (labels), the file list (sizes), and the first 128 KB of candidate .ulg files
// (HTTP Range) to see which topics each log subscribes to and its height-fusion parameters.
// Output: the selected flights, the excluded counts with reasons, and the total download size.
// Selection is deterministic: candidates are ordered by SHA-256("uavsead-select-v1|" + key).
(async () => {
  const REPO = '/datasets/aykutkabaoglu/uav-flight-anomaly-dataset';
  const BASE = REPO + '/resolve/main/ulg_files/';
  const N_ANOMALY = 16, N_CAL = 12, MAX_PER_DAY = 2, HEAD = 131072, MAX_BYTES = 30e6;
  const WANT = new Set(['EKF2_HGT_MODE', 'EKF2_AID_MASK', 'EKF2_RNG_AID']);
  const td = new TextDecoder();

  function parseHead(buf) {
    const dv = new DataView(buf), u8 = new Uint8Array(buf);
    if (!td.decode(u8.slice(0, 7)).startsWith('ULog')) return { err: 'not a ULog file' };
    let o = 16, inData = false; const info = {}, params = {}, topics = new Set();
    while (o + 3 <= u8.length) {
      const sz = dv.getUint16(o, true), t = String.fromCharCode(u8[o + 2]), p = o + 3;
      if (p + sz > u8.length) break;
      if (t === 'I' || t === 'P') {
        const kl = u8[p], [typ, name] = td.decode(u8.slice(p + 1, p + 1 + kl)).split(' ');
        if (t === 'I' && typ.startsWith('char')) info[name] = td.decode(u8.slice(p + 1 + kl, p + sz));
        if (t === 'P' && WANT.has(name)) params[name] = typ === 'float' ? dv.getFloat32(p + 1 + kl, true) : dv.getInt32(p + 1 + kl, true);
      } else if (t === 'A') topics.add(td.decode(u8.slice(p + 3, p + sz)));
      else if (t === 'D') inData = true;
      o = p + sz;
    }
    const has = n => topics.has(n);
    return { uuid: info.sys_uuid || null, hw: info.ver_hw || null, params, inData,
      sig: { est: has('vehicle_local_position'), baro: has('vehicle_air_data') || has('sensor_baro'),
             range: has('distance_sensor'), gps: has('vehicle_gps_position'),
             vision: has('vehicle_vision_position') || has('vehicle_visual_odometry') || has('att_pos_mocap') } };
  }
  // A reference is independent only if the estimator is not configured to fuse it for height.
  // EKF2_HGT_MODE: 0 baro, 1 GPS, 2 range, 3 vision. EKF2_RNG_AID=1 lets EKF2 fuse range at low height.
  function independent(h) {
    const p = h.params, s = h.sig, out = [];
    if (p.EKF2_HGT_MODE === undefined) return out;
    if (s.vision && p.EKF2_HGT_MODE !== 3) out.push('vision');
    if (s.range && p.EKF2_HGT_MODE !== 2 && p.EKF2_RNG_AID === 0) out.push('range');
    if (s.baro && p.EKF2_HGT_MODE !== 0) out.push('baro');
    if (s.gps && p.EKF2_HGT_MODE !== 1) out.push('gps');
    return out;
  }
  const sha = async s => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)))].map(b => b.toString(16).padStart(2, '0')).join('');
  const dayOf = k => k.includes('/') ? k.split('/')[0] : ((k.match(/log_\d+_(\d+-\d+-\d+)/) || [])[1] || 'undated:' + k);

  const mapText = await fetch(REPO + '/resolve/main/mapping.json').then(r => r.text());
  const map = JSON.parse(mapText), mapSha = await sha(mapText);
  const sizes = {};
  for (let url = REPO.replace('/datasets', '/api/datasets') + '/tree/main/ulg_files?recursive=true'; url;) {
    const r = await fetch(url); for (const x of await r.json()) if (x.type === 'file') sizes[x.path.replace(/^ulg_files\//, '')] = x.size;
    const m = (r.headers.get('link') || '').match(/<([^>]+)>;\s*rel="next"/); url = m ? m[1] : null;
  }
  const heads = {};
  async function screen(keys) {
    const q = keys.filter(k => !heads[k]);
    await Promise.all(Array.from({ length: 6 }, async () => { while (q.length) { const k = q.shift();
      try { const r = await fetch(BASE + k + '.ulg', { headers: { Range: 'bytes=0-' + (HEAD - 1) } }); heads[k] = parseHead(await r.arrayBuffer()); }
      catch (e) { heads[k] = { err: String(e) }; } } }));
  }
  const classes = k => [...new Set(map[k].annotations.map(a => a.class))];
  const cfg = k => { const h = heads[k]; return [h.uuid, h.params.EKF2_HGT_MODE, h.params.EKF2_RNG_AID, independent(h).join('+')].join('|'); };

  // Anomaly flights: label "Altitude" only, every labelled range on the estimator height (Position.Z).
  const altAll = Object.keys(map).filter(k => classes(k).includes('Altitude'));
  await screen(altAll.filter(k => sizes[k + '.ulg']));
  const excluded = {}, ex = (k, why) => { (excluded[why] = excluded[why] || []).push(k); };
  const altOk = [];
  for (const k of altAll) {
    const a = map[k].annotations.find(a => a.class === 'Altitude'), h = heads[k];
    if (classes(k).length > 1) ex(k, 'several labels on one flight');
    else if (!a.ranges.length || !a.ranges.every(r => r[0] === 'Position.Z')) ex(k, 'label not on the height estimate');
    else if (!sizes[k + '.ulg']) ex(k, 'no .ulg file');
    else if (h.err || !h.inData) ex(k, 'header unreadable');
    else if (!h.sig.est) ex(k, 'no estimator height');
    else if (!independent(h).length) ex(k, 'no independent height reference');
    else if (sizes[k + '.ulg'] > MAX_BYTES) ex(k, 'file over 30 MB');
    else altOk.push(k);
  }
  const order = async ks => (await Promise.all(ks.map(async k => [await sha('uavsead-select-v1|' + k), k]))).sort().map(x => x[1]);
  const anomalies = [], perDay = {};
  for (const k of await order(altOk)) {
    if (anomalies.length >= N_ANOMALY) break;
    const d = dayOf(k); if ((perDay[d] || 0) >= MAX_PER_DAY) continue;
    perDay[d] = (perDay[d] || 0) + 1; anomalies.push(k);
  }
  // Normal flights: label "Normal" only, same flying days as eligible anomaly flights.
  const days = new Set(altOk.map(dayOf));
  const normAll = Object.keys(map).filter(k => classes(k).length === 1 && classes(k)[0] === 'Normal' && sizes[k + '.ulg'] && days.has(dayOf(k)));
  await screen(normAll);
  const normOk = (await order(normAll)).filter(k => { const h = heads[k]; return !h.err && h.inData && h.sig.est && independent(h).length && sizes[k + '.ulg'] <= MAX_BYTES; });
  // Matched normal per anomaly: same board, same height fusion, same independent references; same day if possible.
  const used = new Set(), matched = [], unmatched = [];
  for (const k of anomalies) {
    const c = cfg(k), pool = normOk.filter(n => !used.has(n) && cfg(n) === c);
    const pick = pool.find(n => dayOf(n) === dayOf(k)) || pool[0];
    if (pick) { used.add(pick); matched.push([k, pick]); } else unmatched.push(k);
  }
  // Calibration normals (thresholds only): disjoint from the test set, configurations of the test set, round-robin.
  const testCfgs = [...new Set(anomalies.map(cfg))], calib = [];
  for (let i = 0; calib.length < N_CAL && i < normOk.length * testCfgs.length; i++) {
    const c = testCfgs[i % testCfgs.length], n = normOk.find(n => !used.has(n) && cfg(n) === c);
    if (n) { used.add(n); calib.push(n); }
  }
  const row = (k, role) => ({ key: k, role, day: dayOf(k), bytes: sizes[k + '.ulg'], board: heads[k].uuid, hw: heads[k].hw,
    hgt_mode: heads[k].params.EKF2_HGT_MODE, rng_aid: heads[k].params.EKF2_RNG_AID, aid_mask: heads[k].params.EKF2_AID_MASK,
    signals: Object.keys(heads[k].sig).filter(s => heads[k].sig[s]), independent: independent(heads[k]),
    label: map[k].annotations.map(a => ({ class: a.class, ranges_us: a.ranges })) });
  const selected = [...matched.flatMap(([a, n]) => [row(a, 'test_anomaly'), row(n, 'test_normal')]),
                    ...unmatched.map(a => row(a, 'test_anomaly_unmatched')), ...calib.map(n => row(n, 'calibration_normal'))];
  return { version: 'uavsead-select-v1', mapping_json_sha256: mapSha, flights_labelled: Object.keys(map).length,
    altitude_labelled: altAll.length, altitude_eligible: altOk.length,
    excluded: Object.fromEntries(Object.entries(excluded).map(([w, ks]) => [w, ks])),
    normals_same_days: normAll.length, normals_eligible: normOk.length,
    counts: { test_anomaly: matched.length + unmatched.length, test_normal: matched.length, calibration_normal: calib.length, unmatched: unmatched.length },
    download_bytes: selected.reduce((s, r) => s + r.bytes, 0), selected };
})();
