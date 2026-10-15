(function () {
  "use strict";
  var modal = document.getElementById("cblQuantityMessengerModal");
  var opener = document.getElementById("qty-resume");
  var flow = document.getElementById("qty-flow");
  if (!modal || !opener || !flow) return;
  var timer = null;
  var labels = {review_required: "검토 이어하기", completed: "결과 보기", partial: "부분 결과 보기", running: "진행 확인", interrupted: "중단 확인", failed: "오류 확인"};

  // A job stopped by a server restart continues from its kept uploads; the page script
  // (home.html) posts the resume and shows the progress.
  function offerContinue(jobId, output) {
    var go = document.createElement("button");
    go.type = "button";
    go.textContent = "이어서 실행 (끝난 단계는 다시 호출하지 않음)";
    go.addEventListener("click", function () {
      var list = document.getElementById("qty-history-list");
      if (list) list.remove();
      modal.dispatchEvent(new CustomEvent("cbl:quantity-continue", {detail: {job_id: jobId}}));
    });
    output.parentNode.insertBefore(go, output.nextSibling);
  }

  async function resume(jobId, output) {
    try {
      var response = await fetch("/api/quantity/progress/?job_id=" + encodeURIComponent(jobId), {method: "GET"});
      var data = await response.json();
      if (data.interrupted) {
        output.textContent = data.error || "분석이 중단됐습니다.";
        if (data.resumable) offerContinue(jobId, output);
        return;
      }
      if (!response.ok || data.ok === false) throw new Error(data.error || "저장된 작업을 읽지 못했습니다.");
      if (data.done) {
        output.textContent = "저장된 작업을 불러왔습니다.";
        modal.dispatchEvent(new CustomEvent("cbl:quantity-resume", {detail: data}));
      } else {
        output.textContent = data.label || "분석 진행 중입니다.";
        timer = window.setTimeout(function () { if (!modal.hidden) resume(jobId, output); }, 1500);
      }
    } catch (error) {
      output.textContent = error.message;
    }
  }

  opener.addEventListener("click", async function () {
    opener.disabled = true;
    if (timer) window.clearTimeout(timer);
    var old = document.getElementById("qty-history-list");
    if (old) old.remove();
    var list = document.createElement("div");
    list.id = "qty-history-list";
    list.className = "qty-history-list";
    flow.appendChild(list);
    list.textContent = "최근 7일간 저장된 작업을 확인하고 있습니다…";
    try {
      var response = await fetch("/api/quantity/jobs/", {method: "GET"});
      var data = await response.json();
      if (!response.ok) throw new Error(data.error || "작업 목록을 읽지 못했습니다.");
      list.textContent = data.jobs.length ? "저장된 작업" : "최근 7일간 저장된 산출 작업이 없습니다.";
      data.jobs.forEach(function (job) {
        var button = document.createElement("button");
        button.type = "button";
        var label = job.status === "interrupted" && job.resumable ? "중단됨 · 이어서 실행 가능" : labels[job.status];
        button.textContent = new Date(job.created_at * 1000).toLocaleString("ko-KR") + " · " + (label || "확인");
        button.addEventListener("click", function () {
          if (timer) window.clearTimeout(timer);
          var output = document.createElement("p");
          list.appendChild(output);
          resume(job.job_id, output);
        });
        list.appendChild(button);
      });
    } catch (error) {
      list.textContent = error.message;
    } finally {
      opener.disabled = false;
      var body = document.getElementById("qty-body");
      if (body) body.scrollTop = body.scrollHeight;
    }
  });
})();
