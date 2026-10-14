/* AI 수량산출 자동화 소개 페이지: 시작하기 → (로그인 창 | 베타 승인 안내 | 진행 화면). */
(function () {
  "use strict";

  var root = document.getElementById("qi");
  if (!root) return;
  root.classList.add("qi-js");

  var authenticated = root.getAttribute("data-qi-authenticated") === "true";
  var canStart = root.getAttribute("data-qi-can-start") === "true";
  var startUrl = root.getAttribute("data-qi-start-url") || "/quantity/start/";
  var lastFocus = null;

  function openModal(id) {
    var modal = document.getElementById(id);
    if (!modal) return;
    lastFocus = document.activeElement;
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("qi-modal-open");
    var first = modal.querySelector("input, a.qi-btn, button.qi-btn");
    window.setTimeout(function () { if (first) first.focus(); }, 30);
  }

  function closeModals() {
    root.querySelectorAll(".qi-modal").forEach(function (modal) {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
    });
    document.body.classList.remove("qi-modal-open");
    if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
  }

  root.querySelectorAll("[data-qi-start]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (canStart) {
        window.location.assign(startUrl);
      } else if (!authenticated) {
        openModal("qiLoginModal");
      } else {
        openModal("qiBetaModal");
      }
    });
  });

  root.querySelectorAll("[data-qi-close]").forEach(function (el) {
    el.addEventListener("click", closeModals);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && document.body.classList.contains("qi-modal-open")) closeModals();
  });

  root.querySelectorAll("[data-qi-eye]").forEach(function (button) {
    button.addEventListener("click", function () {
      var input = button.parentNode.querySelector("input");
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      button.setAttribute("aria-label", show ? "비밀번호 숨기기" : "비밀번호 보기");
    });
  });

  root.querySelectorAll('a[href^="#qi-"]').forEach(function (link) {
    link.addEventListener("click", function (event) {
      var target = document.querySelector(link.getAttribute("href"));
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  var reveals = root.querySelectorAll(".qi-reveal");
  if ("IntersectionObserver" in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-in");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.15 });
    reveals.forEach(function (el) { observer.observe(el); });
  } else {
    reveals.forEach(function (el) { el.classList.add("is-in"); });
  }

  if (root.getAttribute("data-qi-restricted") === "true") openModal("qiBetaModal");
})();
