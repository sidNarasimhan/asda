(() => {
  const zone = document.getElementById("dropzone");
  const input = document.getElementById("lead-files");
  const hint = zone && zone.querySelector("[data-drop-hint]");
  const isSheet = (name) => /\.(csv|tsv|xlsx|xls)$/i.test(name || "");
  const sheetsOf = (list) => [...(list || [])].filter((f) => isSheet(f.name));
  const carryingFiles = (e) =>
    [...((e.dataTransfer && e.dataTransfer.types) || [])].includes("Files");
  const setHint = (text) => {
    if (hint) hint.textContent = text;
  };
  let sending = false;
  const send = (files) => {
    if (!files.length || sending) return;
    sending = true;
    if (zone) zone.classList.add("busy");
    document.body.classList.remove("file-drag");
    setHint("Adding " + files.length + " file" + (files.length > 1 ? "s" : "") + "…");
    const body = new FormData();
    files.forEach((f) => body.append("files", f, f.name));
    fetch("/leads/upload", { method: "POST", body })
      .then((res) => {
        window.location.href = res.url || "/leads";
      })
      .catch(() => {
        sending = false;
        if (zone) zone.classList.remove("busy");
        setHint("Could not upload. Click the box and pick the file.");
      });
  };
  if (input) {
    input.addEventListener("change", () => {
      if (input.files && input.files.length) send([...input.files]);
    });
  }
  let dragDepth = 0;
  document.addEventListener("dragenter", (e) => {
    if (!carryingFiles(e)) return;
    e.preventDefault();
    dragDepth += 1;
    document.body.classList.add("file-drag");
    if (zone) zone.classList.add("on");
  });
  document.addEventListener("dragover", (e) => {
    if (!carryingFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  });
  document.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) {
      document.body.classList.remove("file-drag");
      if (zone) zone.classList.remove("on");
    }
  });
  document.addEventListener("drop", (e) => {
    const files = sheetsOf(e.dataTransfer && e.dataTransfer.files);
    dragDepth = 0;
    document.body.classList.remove("file-drag");
    if (zone) zone.classList.remove("on");
    if (!files.length) {
      if (carryingFiles(e)) e.preventDefault();
      if (zone && e.target && (zone.contains(e.target) || e.target === zone)) {
        setHint("That was not a CSV or Excel file.");
      }
      return;
    }
    e.preventDefault();
    send(files);
  });
  const filter = document.getElementById("lead-filter");
  if (filter) {
    filter.addEventListener("input", () => {
      const q = filter.value.trim().toLowerCase();
      document.querySelectorAll("tr[data-filter]").forEach((row) => {
        row.hidden = q && !row.dataset.filter.includes(q);
      });
    });
  }
  document.querySelectorAll("[data-confirm]").forEach((el) => {
    el.addEventListener("click", (ev) => {
      if (!window.confirm(el.getAttribute("data-confirm"))) ev.preventDefault();
    });
  });
})();
