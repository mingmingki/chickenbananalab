from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_actual_ui_renders_cost_metadata_without_inventing_unknown_retries():
    script=r'''const fs=require('node:fs'),vm=require('node:vm'),a=require('node:assert/strict');
const elements={};const document={getElementById:id=>elements[id]||(elements[id]={})};
const context={document};vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
context.renderOperatingCosts({usage_log_available:true,server:{},by_model_24h:{'gpt-fixture':{calls:3,input_tokens:100,output_tokens:50}},by_purpose_24h:{entry_gate:{calls:3}},call_metadata_24h:{logged_calls:3,duplicate_request_records:0,repeated_input_calls:1,measured_retry_count:0,unknown_retry_calls:2}});
const text=elements['ai-call-metadata'].textContent;
a.ok(text.includes('gpt-fixture'));a.ok(text.includes('entry_gate'));a.ok(text.includes('100'));a.ok(text.includes('미확인 2'));a.ok(text.includes('동일 입력 1'));a.ok(text.includes('절감 미측정'));
context.renderOperatingCosts({usage_log_available:false});a.ok(elements['ai-call-metadata'].textContent.includes('미측정'));
'''
    subprocess.run(['node','-e',script,str(ROOT/'static/operating_observability.js')],check=True,capture_output=True)


def test_actual_entry_ui_never_renders_approval_as_fill_or_interprets_reason_html():
    script=r'''const s=require(process.argv[1]),a=require('node:assert/strict');
const doc={createElement:tag=>({tag,children:[],appendChild(x){this.children.push(x)}})};
for(const status of Object.keys(s.labels)) {
 const row={status,decision_id:'d1',gpt_raw_result:'TIMEOUT',gate_processing:'TIMEOUT_BYPASS',reason:'<script>attack</script>'};
 const tr=s.render(doc,row);a.equal(tr.children[4].innerHTML,undefined);a.ok(tr.children[4].textContent.includes('<script>attack</script>'));
}
a.ok(s.describe({status:'GPT_APPROVED'}).includes('주문 전'));a.ok(s.describe({status:'ORDER_PENDING'}).includes('확인 대기'));
'''
    subprocess.run(['node','-e',script,str(ROOT/'static/core_entry_status.js')],check=True,capture_output=True)
