(function () {
  "use strict";

  function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function setFooterYear() {
    var el = document.getElementById("footer-year");
    if (el) el.textContent = String(new Date().getFullYear());
  }

  function initNav() {
    var btn = document.querySelector(".nav-toggle");
    var menu = document.getElementById("primary-nav");
    if (!btn || !menu) return;
    btn.addEventListener("click", function () {
      var open = menu.classList.toggle("is-open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });

    menu.addEventListener("click", function (e) {
      if (e.target && e.target.tagName === "A" && menu.classList.contains("is-open")) {
        menu.classList.remove("is-open");
        btn.setAttribute("aria-expanded", "false");
      }
    });
  }

  function initFlashes() {
    document.querySelectorAll(".flash-close").forEach(function (b) {
      b.addEventListener("click", function () {
        var f = b.closest(".flash");
        if (f && f.parentNode) f.parentNode.removeChild(f);
      });
    });
  }

  function initReveal() {
    var els = document.querySelectorAll(".reveal");
    if (!els.length) return;
    if (!("IntersectionObserver" in window)) {
      els.forEach(function (e) { e.classList.add("visible"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.classList.add("visible");
          io.unobserve(en.target);
        }
      });
    }, { rootMargin: "0px 0px -40px 0px", threshold: 0.05 });
    els.forEach(function (el) { io.observe(el); });
  }

  function initProgressBars() {
    document.querySelectorAll(".progress-bar").forEach(function (bar) {
      var pct = parseFloat(bar.getAttribute("data-percent") || "0");
      if (isNaN(pct) || pct < 0) pct = 0;
      if (pct > 100) pct = 100;
      var fill = bar.querySelector(".progress-fill");
      if (fill) {

        requestAnimationFrame(function () { fill.style.width = pct + "%"; });
      }
      bar.setAttribute("role", "progressbar");
      bar.setAttribute("aria-valuemin", "0");
      bar.setAttribute("aria-valuemax", "100");
      bar.setAttribute("aria-valuenow", String(Math.round(pct)));
    });
  }

  function updateAcademyProgress(completedCount) {
    var bar = document.getElementById("academy-progress-bar");
    var totalEl = document.getElementById("academy-total-count");
    var doneEl = document.getElementById("academy-completed-count");
    if (!bar || !totalEl) return;
    var total = parseInt(totalEl.textContent, 10) || 0;
    if (total === 0) return;
    var pct = Math.round((completedCount / total) * 100);
    if (pct < 0) pct = 0; if (pct > 100) pct = 100;
    bar.setAttribute("data-percent", String(pct));
    bar.setAttribute("aria-valuenow", String(pct));
    var fill = bar.querySelector(".progress-fill");
    if (fill) fill.style.width = pct + "%";
    if (doneEl) doneEl.textContent = String(completedCount);
  }

  function setItemDone(item, done) {
    if (!item) return;
    item.classList.toggle("is-done", !!done);
    var status = item.querySelector(".item-status");
    if (status) {
      status.innerHTML = "";
      if (done) {
        var b = document.createElement("span");
        b.className = "badge badge-success";
        b.textContent = "Completed";
        status.appendChild(b);
      }
    }
    var markBtn = item.querySelector(".js-academy-complete");
    var unmarkBtn = item.querySelector(".js-academy-uncomplete");
    if (markBtn) markBtn.hidden = !!done;
    if (unmarkBtn) unmarkBtn.hidden = !done;
    if (markBtn) markBtn.disabled = false;
    if (unmarkBtn) unmarkBtn.disabled = false;
  }

  function academyCall(item, suffix, makeDone) {
    if (!item) return;
    var moduleId = item.getAttribute("data-module-id") || "";
    var itemId = item.getAttribute("data-item-id-only") || "";
    if (!moduleId || !itemId) return;
    var btn = item.querySelector(makeDone ? ".js-academy-complete" : ".js-academy-uncomplete");
    if (btn) btn.disabled = true;
    var url = "/dashboard/academy/" + encodeURIComponent(moduleId)
              + "/" + encodeURIComponent(itemId) + "/" + suffix;
    fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "X-CSRFToken": getCsrfToken(),
        "Accept": "application/json",
      },
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (data) {
      if (data && data.ok) {
        setItemDone(item, makeDone);
        if (typeof data.completed_count === "number") {
          updateAcademyProgress(data.completed_count);
        }
      } else {
        if (btn) btn.disabled = false;
        alert("Could not update item. Please refresh and try again.");
      }
    }).catch(function () {
      if (btn) btn.disabled = false;
      alert("Network error. Please try again.");
    });
  }

  function initAcademyComplete() {
    document.addEventListener("click", function (e) {
      var t = e.target;
      if (!t || !t.classList) return;
      if (t.classList.contains("js-academy-complete")) {
        academyCall(t.closest(".academy-item"), "complete", true);
      } else if (t.classList.contains("js-academy-uncomplete")) {
        academyCall(t.closest(".academy-item"), "uncomplete", false);
      }
    });
  }

  function initJsonForms() {
    document.querySelectorAll("form.js-json-form").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        var ta = form.querySelector('textarea[name="config"]');
        if (!ta) return;
        try {
          JSON.parse(ta.value);
        } catch (err) {
          e.preventDefault();
          alert("JSON is not valid: " + err.message);
        }
      });
    });
  }

  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  ready(function () {
    setFooterYear();
    initNav();
    initFlashes();
    initReveal();
    initProgressBars();
    initAcademyComplete();
    initJsonForms();
  });
})();
