(() => {
  const $ = (id) => document.getElementById(id);

  function apiError(data, fallback) {
    const d = data?.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d.map((x) => x.msg || JSON.stringify(x)).join("; ");
    }
    if (d && typeof d === "object") return JSON.stringify(d);
    return fallback || "Request failed";
  }

  function wireDrop(dropEl, inputEl, nameEl) {
    const setName = (file) => {
      nameEl.textContent = file ? file.name : "";
    };
    dropEl.addEventListener("click", () => inputEl.click());
    dropEl.addEventListener("dragover", (e) => {
      e.preventDefault();
      dropEl.classList.add("drag");
    });
    dropEl.addEventListener("dragleave", () => dropEl.classList.remove("drag"));
    dropEl.addEventListener("drop", (e) => {
      e.preventDefault();
      dropEl.classList.remove("drag");
      const file = e.dataTransfer.files?.[0];
      if (file) {
        inputEl.files = e.dataTransfer.files;
        setName(file);
      }
    });
    inputEl.addEventListener("change", () => setName(inputEl.files?.[0]));
  }

  function setMsg(el, text, ok) {
    el.textContent = text || "";
    el.className = "msg" + (text ? (ok ? " ok" : " bad") : "");
  }

  async function refreshStatus() {
    try {
      const res = await fetch("/api/data/status");
      const data = await res.json();
      $("salesCount").textContent = String(data.sales_file_count ?? "—");
      if (data.sales_date_min && data.sales_date_max) {
        $("salesWindow").textContent = `${data.sales_date_min} → ${data.sales_date_max}`;
      } else {
        $("salesWindow").textContent = "No sales yet";
      }
      const inv = data.inventory || {};
      $("invSkus").textContent = inv.unique_upcs != null ? String(inv.unique_upcs) : (inv.exists ? "Loaded" : "Missing");
      if (data.server_time) $("serverClock").textContent = `Server ${data.server_time}`;
    } catch {
      $("salesCount").textContent = "—";
    }
  }

  async function checkHealth() {
    const pill = $("healthPill");
    try {
      const res = await fetch("/api/health");
      const data = await res.json();
      pill.textContent = data.status === "ok" ? "API online" : "API issue";
      pill.className = "status-pill " + (data.status === "ok" ? "ok" : "bad");
    } catch {
      pill.textContent = "API offline";
      pill.className = "status-pill bad";
    }
  }

  async function uploadSales() {
    const file = $("salesFile").files?.[0];
    if (!file) {
      setMsg($("salesMsg"), "Choose a sales CSV first.", false);
      return;
    }
    const fd = new FormData();
    fd.append("file", file);
    const saleDate = $("saleDate").value;
    if (saleDate) fd.append("sale_date", saleDate);

    $("salesUploadBtn").disabled = true;
    setMsg($("salesMsg"), "Uploading…", true);
    try {
      const res = await fetch("/api/upload/sales", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(apiError(data, "Upload failed"));
      setMsg(
        $("salesMsg"),
        `Saved as ${data.saved_as} · ${data.rows} rows · ${data.sale_date}`,
        true
      );
      await refreshStatus();
    } catch (err) {
      setMsg($("salesMsg"), err.message || String(err), false);
    } finally {
      $("salesUploadBtn").disabled = false;
    }
  }

  async function uploadInventory() {
    const file = $("invFile").files?.[0];
    if (!file) {
      setMsg($("invMsg"), "Choose an inventory CSV first.", false);
      return;
    }
    const fd = new FormData();
    fd.append("file", file);
    $("invUploadBtn").disabled = true;
    setMsg($("invMsg"), "Uploading…", true);
    try {
      const res = await fetch("/api/upload/inventory", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(apiError(data, "Upload failed"));
      setMsg(
        $("invMsg"),
        `Inventory updated · ${data.unique_upcs} UPCs · ${data.rows} rows`,
        true
      );
      await refreshStatus();
    } catch (err) {
      setMsg($("invMsg"), err.message || String(err), false);
    } finally {
      $("invUploadBtn").disabled = false;
    }
  }

  let pollTimer = null;

  function showTrainRunning(message) {
    $("trainProgress").hidden = false;
    $("trainMsg").textContent = message || "Training in progress…";
    $("trainBtn").disabled = true;
  }

  function showTrainIdle() {
    $("trainBtn").disabled = false;
  }

  async function pollJob(jobId) {
    const res = await fetch(`/api/train/${jobId}`);
    const job = await res.json();
    if (!res.ok) throw new Error(job.detail || "Job not found");

    if (job.status === "running" || job.status === "queued") {
      showTrainRunning(job.message || "Training…");
      return false;
    }

    $("trainProgress").hidden = true;
    showTrainIdle();

    if (job.status === "completed") {
      const summary = job.summary || {};
      $("results").hidden = false;
      $("resultList").innerHTML = [
        `<li>Period: ${summary.analysis_period || "—"}</li>`,
        `<li>Model: ${summary.model || "—"}</li>`,
        `<li>Order now SKUs: ${summary.order_now_count ?? "—"}</li>`,
        `<li>SKUs sold: ${summary.total_skus_sold ?? "—"}</li>`,
      ].join("");
      $("trainMsg").textContent = "Training completed.";
      await loadOutputs();
      return true;
    }

    throw new Error(job.error || job.message || "Training failed");
  }

  async function loadOutputs() {
    try {
      const res = await fetch("/api/outputs");
      const data = await res.json();
      if (data.summary && data.summary.order_now_count != null) {
        $("results").hidden = false;
        if (!$("resultList").innerHTML) {
          $("resultList").innerHTML = [
            `<li>Period: ${data.summary.analysis_period || "—"}</li>`,
            `<li>Model: ${data.summary.model || "—"}</li>`,
            `<li>Order now SKUs: ${data.summary.order_now_count ?? "—"}</li>`,
          ].join("");
        }
      }
    } catch {
      /* ignore */
    }
  }

  async function startTrain() {
    $("results").hidden = true;
    showTrainRunning("Starting training job…");
    try {
      const res = await fetch("/api/train", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const job = await res.json();
      if (!res.ok) throw new Error(apiError(job, "Could not start training"));

      if (pollTimer) clearInterval(pollTimer);
      const tick = async () => {
        try {
          const done = await pollJob(job.job_id);
          if (done && pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
          }
        } catch (err) {
          if (pollTimer) clearInterval(pollTimer);
          pollTimer = null;
          $("trainProgress").hidden = true;
          showTrainIdle();
          $("trainMsg").textContent = err.message || String(err);
          setMsg($("trainMsg"), err.message || String(err), false);
        }
      };
      await tick();
      pollTimer = setInterval(tick, 4000);
    } catch (err) {
      $("trainProgress").hidden = true;
      showTrainIdle();
      $("trainMsg").textContent = err.message || String(err);
    }
  }

  async function resumeIfRunning() {
    try {
      const res = await fetch("/api/train/status");
      const data = await res.json();
      if (data.is_training && data.job?.job_id) {
        showTrainRunning(data.job.message);
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(async () => {
          try {
            const done = await pollJob(data.job.job_id);
            if (done && pollTimer) {
              clearInterval(pollTimer);
              pollTimer = null;
            }
          } catch {
            /* keep polling briefly */
          }
        }, 4000);
      } else if (data.job?.status === "completed") {
        await loadOutputs();
      }
    } catch {
      /* ignore */
    }
  }

  wireDrop($("salesDrop"), $("salesFile"), $("salesFileName"));
  wireDrop($("invDrop"), $("invFile"), $("invFileName"));
  $("salesUploadBtn").addEventListener("click", uploadSales);
  $("invUploadBtn").addEventListener("click", uploadInventory);
  $("trainBtn").addEventListener("click", startTrain);

  checkHealth();
  refreshStatus();
  resumeIfRunning();
  loadOutputs();
})();
