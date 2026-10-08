/* Operator display only. This file never sends POSTs or calls paid providers. */
function operatingMoney(value, digits) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '산정 불가';
  return '$' + value.toLocaleString('en-US', {minimumFractionDigits:digits, maximumFractionDigits:digits}) + ' USD';
}
function renderOperatingCosts(summary) {
  const raw=summary || {}, server=raw.server || {};
  const s=raw.usage_log_available===false ? {...raw,today_cost_usd:null,rolling_24h_cost_usd:null,projected_monthly_ai_cost_usd:null,projected_monthly_total_usd:null} : raw;
  const set=(id,text)=>{const el=document.getElementById(id);if(el)el.textContent=text;};
  set('ai-today-cost',operatingMoney(s.today_cost_usd,4));
  set('ai-24h-cost',operatingMoney(s.rolling_24h_cost_usd,4));
  set('ai-monthly-projection',typeof s.projected_monthly_ai_cost_usd==='number' ? '약 '+operatingMoney(s.projected_monthly_ai_cost_usd,2)+' /월' : '산정 불가');
  set('server-monthly-projection',server.estimate_available ? '약 '+operatingMoney(server.monthly_estimate_usd,2)+' /월' : '산정 불가');
  set('ops-monthly-projection',typeof s.projected_monthly_total_usd==='number' ? '약 '+operatingMoney(s.projected_monthly_total_usd,2)+' /월' : '산정 불가');
  const metadata=s.call_metadata_24h;
  const models=Object.entries(s.by_model_24h || {}).map(([name,r])=>`${name}: ${r.calls}회 / 입력 ${r.input_tokens || 0} 출력 ${r.output_tokens || 0} 토큰`).join(' · ');
  const triggers=Object.entries(s.by_trigger_24h || {}).map(([name,r])=>`${name} ${r.calls}회`).join(' · ');
  const attempts=s.attempt_metadata_24h || {}, cohort=s.release_cohort || {};
  const releaseText=cohort.available ? `현 릴리스 ${cohort.created_at} 이후 ${cohort.since_release_calls}회 / ${operatingMoney(cohort.since_release_cost_usd,4)} · 24h 이전 릴리스 ${operatingMoney(cohort.pre_release_24h_cost_usd,4)}, 현 릴리스 ${operatingMoney(cohort.post_release_24h_cost_usd,4)}` : '현 릴리스 비용 구간: 배포 시각 확인 대기';
  const purposes=Object.entries(s.by_purpose_24h || {}).map(([name,r])=>`${name} ${r.calls}회`).join(' · ');
  set('ai-call-metadata',metadata && s.usage_log_available!==false
    ? `24h 기록 ${metadata.logged_calls}회 · 요청 기록 중복 ${metadata.duplicate_request_records} · 동일 입력 ${metadata.repeated_input_calls} (중복 호출 확정 아님) · 확인 재시도 ${metadata.measured_retry_count}, 미확인 ${metadata.unknown_retry_calls}회 · ${models} · 목적 ${purposes} · trigger ${triggers} · cache 입력 ${metadata.cached_input_tokens || 0}토큰, 미확인 ${metadata.cache_unknown_calls || 0}회 · 호출시도 ${attempts.logged_application_attempts || 0}, timeout ${attempts.timed_out_calls || 0}, 오류 ${attempts.error_calls || 0} (실패비용 미확인) · ${releaseText} · 절감 미측정`
    : '호출 모델·목적·토큰·중복·재시도 미측정 · 절감 미측정');
  set('operating-cost-assumptions',server.estimate_available
    ? `예상값 · ${server.machine_type} · ${server.region || 'us-central1'} · ${server.disk_type} ${server.disk_gb}GB · 외부 IPv4 ${server.external_ipv4} · 월 ${server.hours_per_month}시간. AI 월 예상은 최근 24시간 × 730/24. 세금·전송·로그비용·할인·무료 할당·크레딧 미반영. 실제 청구액과 다를 수 있습니다.${s.usage_log_available===false ? ' AI 사용 로그 없음.' : ''}`
    : '예상값 산정 불가 · 실제 청구액은 제공사 청구 내역에서 확인하세요.');
}
async function refreshOperatingCosts() {
  try {
    const response=await fetchWithTimeout('/api/operating-costs');
    if(!response.ok)throw new Error('operating_costs_unavailable');
    const data=await response.json();
    renderOperatingCosts(data.ok ? data.summary : null);
  } catch (_) {renderOperatingCosts(null);}
}
function renderAnalysisOpsStatus(status) {
  const el=document.getElementById('learning-ops-summary');if(!el)return;
  const s=(status || {}).scheduler || {};
  let next='산정 불가';const stamp=new Date(s.next_window_start);
  if(Number.isFinite(stamp.getTime()))next=new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(stamp)+' KST';
  const outcome=s.last_status==='no_new_sample' ? '새 표본 없음 · 유료 AI 호출 없음'
    : s.last_status ? `${s.last_status} · 유료 AI 호출 여부는 기록 확인 필요` : '저장 기록 없음';
  el.textContent=`자동 6시간 리뷰 · ${s.last_window_id || '-'} · ${outcome} · 다음 경계 ${next}. 평소 사용자 조치 없음. 수동 유료 리뷰는 즉시 추가 검토가 필요할 때만 실행하세요. 이 분석·리뷰는 관찰·설명용입니다.`;
}

function renderOpsObservability(snapshot) {
  const x=snapshot||{}, strategy=x.strategy||{}, q=strategy.entry_quality||{}, cap=strategy.mfe_capture||{};
  const health=x.health||{}, costs=x.cost_efficiency||{}, effects=strategy.effects||{}, sides=strategy.side_performance||{};
  const set=(id,text)=>{const el=document.getElementById(id);if(el)el.textContent=text;};
  const pct=v=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(1)+'%':'측정 대기';
  const pf=v=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(2):'-';
  set('ops-observability-updated',x.generated_at?('갱신 '+new Date(x.generated_at).toLocaleString('ko-KR')):'스냅샷 대기');
  const issueText=(health.issues||[]).map(i=>[i.code,i.symbol,i.detail].filter(Boolean).join(' · ')).join(' / ');
  set('ops-health-summary',health.ok===true?'✅ 운영 감시 정상'+(health.daily_loss_pct==null?'':(' · 일손실 '+pct(health.daily_loss_pct)))
    :health.ok===false?('⚠️ 운영 경고 '+(health.issue_count||0)+'건 · '+issueText):'watchdog 스냅샷 대기');
  set('obs-entry-quality','표본 '+(q.sample_count||0)+' · late '+(q.late_entry_count||0)+' ('+pct(q.late_entry_rate_pct)+') · 즉시역행 '+(q.immediate_adverse_count||0)+' ('+pct(q.immediate_adverse_rate_pct)+') · clean '+(q.clean_follow_through_count||0)+' ('+pct(q.clean_follow_through_rate_pct)+')');
  set('obs-mfe-capture',cap.sample_count?('표본 '+cap.sample_count+' · 평균 '+pct(cap.avg_capture_pct)+' · 중앙 '+pct(cap.median_capture_pct)+' · 120분 MFE 기준'):'측정 가능한 완료거래 대기');
  const sideText=['long','short'].map(k=>{const r=sides[k]||{};return k.toUpperCase()+' n='+(r.count||0)+' Net '+(typeof r.net_pnl==='number'?r.net_pnl.toFixed(2):'-')+' PF '+pf(r.profit_factor)}).join(' · ');
  set('obs-side-performance',sideText);
  const pp=(effects.partial_take_profit||{});
  set('obs-partial','완료 '+(pp.trade_count||0)+'건 · Net '+(typeof pp.net_pnl==='number'?pp.net_pnl.toFixed(2):'-')+' · PF '+pf(pp.profit_factor));
  set('obs-cost-efficiency','24h 실제 AI '+(costs.actual_paid_calls_24h||0)+'회 · GPT '+(costs.gpt_actual_calls_24h||0)+'회 · skip/cache '+(costs.observed_cache_skip_24h||0)+'회 · 비용 $'+(typeof costs.ai_cost_24h_usd==='number'?costs.ai_cost_24h_usd.toFixed(4):'-'));
  const tbody=document.getElementById('obs-effect-table');
  if(tbody){
    const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const rows=Object.values(effects).map(r=>{
      const count=r.trigger_count_24h??r.evaluation_count_24h??0;
      const extra=r.evaluation_count_24h!=null&&r.trigger_count_24h!=null?(' / 평가 '+r.evaluation_count_24h):'';
      let result=r.measurement||'관찰 중';
      if(typeof r.net_pnl==='number') result='완료 Net '+r.net_pnl.toFixed(2)+' · PF '+pf(r.profit_factor);
      if(r.executed_count_24h!=null) result='실행 '+r.executed_count_24h+' · skip '+(r.skipped_count_24h||0);
      return '<tr><td>'+esc(r.label||'-')+'</td><td>'+esc(String(count)+extra)+'</td><td>'+esc(result)+'</td></tr>';
    });
    tbody.innerHTML=rows.length?rows.join(''):'<tr><td colspan="3">관찰 데이터 없음</td></tr>';
  }
}
