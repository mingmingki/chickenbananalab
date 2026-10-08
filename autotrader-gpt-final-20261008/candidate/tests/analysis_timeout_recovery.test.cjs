const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/dashboard.html','utf8');
function source(start, end, fallback) {
  let a = html.indexOf(start);
  if (a < 0 && fallback) a = html.indexOf(fallback);
  const b = html.indexOf(end, a);
  assert(a >= 0 && b > a, 'production function is available');
  return html.slice(a,b);
}
function fixture(failingPath, httpError=false) {
  const elements = new Map();
  const renders = {trade:0};
  const data = {
    '/api/analysis/summary':{ok:true,ready:true,analysis:{summary:{completed_trades:589}},generated_at:'saved',stale:false},
    '/api/analysis/trades?limit=500':{ok:true,trades:[{trade_id:'trade-589'}]},
    '/api/analysis/learning':{ok:true,latest:{}},
    '/api/analysis/reviews?limit=30':{ok:true,reviews:[]},
    '/api/analysis/self-learning':{ok:true,state_counts:{}},
    '/api/analysis/daily-completion':{ok:true,daily_completion:{}},
    '/api/analysis/reports?limit=20':{ok:true,reports:[]}
  };
  const context = vm.createContext({
    tradeLearningState:{analysis:null}, Promise, Date, setTimeout,
    document:{getElementById(id){if(!elements.has(id)) elements.set(id,{textContent:'',innerHTML:''});return elements.get(id);}},
    fetchWithTimeout:async (url)=>{
      if(url===failingPath) {
        if(httpError) return {ok:false,status:500,json:async()=>({ok:false,error:'broken evidence'})};
        throw new Error('request timed out');
      }
      return {ok:true,status:200,json:async()=>data[url]};
    },
    renderTradeLearning(){renders.trade++;},renderSelfLearning(){},renderAnalysisReports(){}
  });
  return {context,elements,renders};
}
const loadSource = source('async function fetchLearningData(', 'function openTradeLearningAnalysis()', 'async function loadTradeLearningAnalysis()');
test('a failed daily check leaves the completed trade analysis visible',async()=>{
  const f=fixture('/api/analysis/daily-completion');
  vm.runInContext(loadSource,f.context);
  await vm.runInContext('loadTradeLearningAnalysis()',f.context);
  assert.equal(f.context.tradeLearningState.analysis.summary.completed_trades,589);
  assert.equal(f.renders.trade,1);
  assert.match(f.elements.get('trade-learning-analysis-status').textContent,/일일 점검/);
});
test('an HTTP error is shown while successful analysis sections still render',async()=>{
  const f=fixture('/api/analysis/learning',true);
  vm.runInContext(loadSource,f.context);
  await vm.runInContext('loadTradeLearningAnalysis()',f.context);
  assert.equal(f.context.tradeLearningState.trades[0].trade_id,'trade-589');
  assert.match(f.elements.get('trade-learning-analysis-status').textContent,/AI 학습/);
});
const reportSource=source('async function waitForAnalysisTask(', 'async function generateAnalysisReport(', 'async function requestAnalysisReport()');
test('report generation waits for its background task and displays the completed report',async()=>{
  let sent=null;
  const context=vm.createContext({
    tradeLearningState:{reports:[]},Date,Promise,setTimeout:(fn)=>{fn();},
    postJSON:async (_url,body)=>{sent=body;return {ok:true,data:{ok:true,queued:true,job_id:'job-1'}};},
    fetchWithTimeout:async ()=>({ok:true,status:200,json:async()=>({ok:true,status:'completed',result:{report:{report_id:'RPT-1',text:'result'}}})}),
    renderAnalysisReports(){}
  });
  vm.runInContext(reportSource,context);
  const result=await vm.runInContext('requestAnalysisReport()',context);
  assert.equal(result.report_id,'RPT-1');
  assert.equal(sent.background,true);
  assert.equal(context.tradeLearningState.latestReport.text,'result');
});
