(function(root){
  const labels={GPT_APPROVED:'GPT 승인 · 주문 전',TIMEOUT_BYPASS:'GPT 타임아웃 · Gemini 후보로 안전검사 통과',NO_RESPONSE_BYPASS:'GPT 빈 응답 · Gemini 후보로 안전검사 통과',GPT_WAIT:'GPT 대기',GPT_REJECT:'GPT 거절',GPT_ERROR:'GPT 오류',LOCAL_BLOCKED:'로컬 차단',ORDER_SUBMITTED:'주문 제출',ORDER_PENDING:'주문 확인 대기',FILLED:'체결 · 보호 확인',ORDER_FAILED:'주문 실패'};
  function gptDisplay(row){
    const gate=row.gate_processing || row.gate_result;
    if(gate==='NOT_REQUESTED' || (!gate && row.status==='LOCAL_BLOCKED')) return '미호출 (로컬 차단)';
    if(gate==='TIMEOUT_BYPASS' || row.timeout_bypass===true) return '타임아웃 (Gemini 예외)';
    if(gate==='NO_RESPONSE_BYPASS' || row.no_response_bypass===true) return '빈 응답 (Gemini 예외)';
    const original=row.gpt_raw_result ?? row.gpt_decision;
    if(original==='TIMEOUT') return '타임아웃 (검사 차단)';
    if(original!==null && original!==undefined && original!=='') return original;
    const error=row.gpt_error_reason || row.error_reason;
    if(error==='empty_response') return '빈 응답 (검사 차단)';
    if(error) return 'GPT 오류 ('+error+')';
    return 'GPT 결과 미확인';
  }
  function describe(row){
    const status=row.status || row.gate_processing || 'UNKNOWN';
    return [labels[status] || status,'decision_id='+ (row.decision_id || '미저장'),
      'GPT 원본='+gptDisplay(row),
      '게이트='+(row.gate_processing || row.gate_result || '미저장'),
      '사유='+(row.reason || row.gpt_reason || row.gpt_reasoning || row.error_reason || '미저장'),
      row.validation_values ? '검증값='+JSON.stringify(row.validation_values) : '',
      row.order_id ? '주문 ID='+row.order_id : '',
      row.exchange_code ? '거래소 코드='+row.exchange_code : ''].filter(Boolean).join(' · ');
  }
  function render(document,row){
    const tr=document.createElement('tr');
    const cells=[row.time || '',row.symbol || '',(row.gemini_action || '')+' '+(row.gemini_confidence ?? ''),
      gptDisplay(row),describe(row)];
    cells.forEach(value=>{const td=document.createElement('td');td.textContent=String(value);tr.appendChild(td);});
    return tr;
  }
  root.coreEntryStatus={labels,describe,render};
  if(typeof module !== 'undefined' && module.exports) module.exports=root.coreEntryStatus;
})(typeof window==='undefined'?globalThis:window);
