(function(root){
  const labels={GPT_APPROVED:'GPT 승인 · 주문 전',TIMEOUT_BYPASS:'GPT 응답 없음 — 설정에 따른 예외 통과',GPT_WAIT:'GPT 대기',GPT_REJECT:'GPT 거절',GPT_ERROR:'GPT 오류',LOCAL_BLOCKED:'로컬 차단',ORDER_SUBMITTED:'주문 제출',ORDER_PENDING:'주문 확인 대기',FILLED:'체결 · 보호 확인',ORDER_FAILED:'주문 실패'};
  function describe(row){
    const status=row.status || row.gate_processing || 'UNKNOWN';
    return [labels[status] || status,'decision_id='+ (row.decision_id || '미저장'),
      'GPT 원본='+(row.gpt_raw_result || row.gpt_decision || '응답 없음'),
      '게이트='+(row.gate_processing || row.gate_result || '미저장'),
      '사유='+(row.reason || row.gpt_reason || row.gpt_reasoning || row.error_reason || '미저장'),
      row.validation_values ? '검증값='+JSON.stringify(row.validation_values) : '',
      row.order_id ? '주문 ID='+row.order_id : '',
      row.exchange_code ? '거래소 코드='+row.exchange_code : ''].filter(Boolean).join(' · ');
  }
  function render(document,row){
    const tr=document.createElement('tr');
    const cells=[row.time || '',row.symbol || '',(row.gemini_action || '')+' '+(row.gemini_confidence ?? ''),
      row.gpt_raw_result || row.gpt_decision || '응답 없음',describe(row)];
    cells.forEach(value=>{const td=document.createElement('td');td.textContent=String(value);tr.appendChild(td);});
    return tr;
  }
  root.coreEntryStatus={labels,describe,render};
  if(typeof module !== 'undefined' && module.exports) module.exports=root.coreEntryStatus;
})(typeof window==='undefined'?globalThis:window);
