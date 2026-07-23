// Job-status polling is driven entirely by HTMX: the status partial only emits an
// `hx-trigger="every 2s"` while the job is non-terminal, so polling stops on its own
// once a terminal partial (with no trigger) is swapped in. No JS lifecycle hook needed.

// Dashboard upload form: constrain the target dropdown to the uploaded file's type and
// show only the options that apply to the chosen target. Degrades to no-op if absent.
(function () {
  function readJSON(id) {
    const el = document.getElementById(id);
    if (!el) return {};
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return {};
    }
  }

  function extension(name) {
    const dot = name.lastIndexOf(".");
    return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
  }

  document.addEventListener("DOMContentLoaded", function () {
    const form = document.querySelector("form[data-conversion-form]");
    if (!form) return;
    const fileInput = form.querySelector('input[type="file"]');
    const targetSelect = form.querySelector('select[name="target_format"]');
    if (!targetSelect) return;

    const formatTargets = readJSON("format-targets");
    const targetOptions = readJSON("target-options");
    const allTargets = Array.from(targetSelect.options).map((o) => o.value);

    function refreshOptions() {
      const applicable = targetOptions[targetSelect.value] || [];
      form.querySelectorAll("[data-option-field]").forEach((el) => {
        const name = el.getAttribute("data-option-field");
        el.style.display = applicable.indexOf(name) === -1 ? "none" : "";
      });
    }

    function refreshTargets() {
      if (!fileInput || !fileInput.files || !fileInput.files.length) return;
      const ext = extension(fileInput.files[0].name);
      const allowed = formatTargets[ext] && formatTargets[ext].length ? formatTargets[ext] : allTargets;
      const previous = targetSelect.value;
      targetSelect.innerHTML = "";
      allowed.forEach((value) => {
        const opt = document.createElement("option");
        opt.value = value;
        opt.textContent = value.toUpperCase();
        targetSelect.appendChild(opt);
      });
      if (allowed.indexOf(previous) !== -1) targetSelect.value = previous;
      refreshOptions();
    }

    if (fileInput) fileInput.addEventListener("change", refreshTargets);
    targetSelect.addEventListener("change", refreshOptions);
    refreshOptions();
  });
})();
