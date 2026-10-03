/* 视图 8：结果对比（原图/处理图 + 滑块 + 像素差异）。
   差异热力图由后端 DiffStore 独立管理：只在本页「对比记录」中展示并标明来源，
   不会进入「选择处理结果」列表，也不能被当作输入再次对比。 */
window.Views = window.Views || {};
window.Views.compare = (function () {
  const C = window.Common;
  let imgId = null, resultId = null, pos = 50;

  return {
    mount(el) {
      el.innerHTML = `
        <div class="split">
          <div class="col">
            <div class="panel"><div class="panel-title">选择原图</div><div id="cp-gallery" style="max-height:300px;overflow:auto"></div></div>
            <div class="panel">
              <div class="panel-title">选择处理结果</div>
              <div class="select-row"><select id="cp-result"></select><button class="btn btn-sm" id="cp-refresh">刷新</button></div>
            </div>
            <div class="panel">
              <div class="panel-title">差异分析</div>
              <button class="btn" id="cp-diff">生成差异热力图</button>
              <div id="cp-metrics" class="keypoint-stats" style="margin-top:10px"><span class="dim">尚未分析</span></div>
              <div class="stage" id="cp-diff-stage" style="margin-top:10px"></div>
            </div>
          </div>
          <div class="col">
            <div class="panel">
              <div class="panel-title">对比视图<span class="dim">拖动分隔线 · 左原图 / 右处理图</span></div>
              <div class="compare-wrap" id="cp-wrap" style="min-height:300px">
                <img id="cp-bottom" style="width:100%;display:block">
                <img id="cp-top" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover">
                <div class="compare-handle" id="cp-handle" style="left:50%"></div>
              </div>
              <div class="caption" id="cp-caption" style="color:var(--text-faint);font-size:12px;margin-top:8px;text-align:center">选择原图与结果后显示</div>
            </div>
            <div class="panel">
              <div class="panel-title">对比记录<span class="dim">差异图仅在此展示，不会进入处理结果列表</span></div>
              <div id="cp-diff-list" style="max-height:360px;overflow:auto"></div>
            </div>
          </div>
        </div>`;

      C.fetchImages().then((images) => {
        el.querySelector("#cp-gallery").innerHTML = C.galleryHTML(images);
        C.bindGallery(el.querySelector("#cp-gallery"), images, (id) => { imgId = id; updateCompare(el); });
      });

      el.querySelector("#cp-refresh").onclick = () => loadResults(el);
      loadResults(el);
      loadDiffs(el);

      el.querySelector("#cp-result").onchange = () => { resultId = el.querySelector("#cp-result").value; updateCompare(el); };

      el.querySelector("#cp-diff").onclick = async () => {
        if (!imgId || !resultId) { C.toast("请选择原图与结果", "error"); return; }
        const box = el.querySelector("#cp-diff-stage");
        box.innerHTML = `<div class="loading">计算差异…</div>`;
        try {
          const r = await Api.post("/api/compare/diff", { image_id: imgId, result_id: resultId });
          showDiff(el, r);
          loadDiffs(el);
        } catch (e) {
          box.innerHTML = "";
          C.toast(e.message || "差异分析失败", "error");
        }
      };

      bindSlider(el);
    },

    refresh() {
      const el = document.querySelector('.view[data-view="compare"]');
      if (el && this.mounted) { loadResults(el); loadDiffs(el); }
    },
  };

  async function loadResults(el) {
    const r = await Api.get("/api/results");
    el.querySelector("#cp-result").innerHTML = `<option value="">— 选择结果 —</option>` +
      r.results.map((x) => `<option value="${x.result_id}">${C.fmtDate(x.created_at)} · ${x.width}×${x.height}</option>`).join("");
  }

  /* 对比记录列表：每条标明来源（原图 + 结果），可查看、可删除。 */
  async function loadDiffs(el) {
    const r = await Api.get("/api/compare/diffs");
    const list = r.diffs || [];
    const box = el.querySelector("#cp-diff-list");
    if (!list.length) {
      box.innerHTML = `<div class="empty"><span class="big">⚖️</span>暂无对比记录<br>选择原图与结果后点击「生成差异热力图」</div>`;
      return;
    }
    box.innerHTML = list.map((d) => `
      <div class="diff-item" data-id="${d.id}" style="display:flex;gap:10px;align-items:center;padding:8px;border:1px solid var(--border);border-radius:8px;margin-bottom:8px;cursor:pointer">
        <img src="${d.file_url}?t=${encodeURIComponent(d.updated_at || "")}" style="width:72px;height:54px;object-fit:cover;border-radius:6px;flex:none">
        <div style="flex:1;min-width:0;font-size:12px;line-height:1.7">
          <div><span class="badge">差异图</span> <strong>${C.esc(d.image_name)}</strong>${d.image_deleted ? ' <span class="badge red">原图已删</span>' : ""}</div>
          <div class="dim">对比结果：${C.esc(d.result_label)}</div>
          <div class="dim">PSNR ${d.metrics.psnr ?? "-"} dB · 变化 ${d.metrics.changed_ratio != null ? (d.metrics.changed_ratio * 100).toFixed(2) + "%" : "-"} · ${C.fmtDate(d.created_at)}</div>
        </div>
        <button class="btn btn-sm btn-danger" data-del="${d.id}" title="删除该对比记录及差异图">删除</button>
      </div>`).join("");

    box.querySelectorAll(".diff-item").forEach((item) => {
      item.onclick = (e) => {
        if (e.target.closest("[data-del]")) return;
        const d = list.find((x) => x.id === item.dataset.id);
        if (d) showDiff(el, d);
      };
    });
    box.querySelectorAll("[data-del]").forEach((btn) => {
      btn.onclick = async () => {
        if (!confirm("删除该对比记录（连同差异图文件）？")) return;
        await Api.del(`/api/compare/diffs/${btn.dataset.del}`);
        C.toast("已删除对比记录", "success");
        loadDiffs(el);
      };
    });
  }

  /* 在差异分析区展示一张差异图及其指标与来源。 */
  function showDiff(el, d) {
    el.querySelector("#cp-diff-stage").innerHTML =
      `<img src="${d.file_url}?t=${Date.now()}">` +
      `<div class="caption">原图：${C.esc(d.image_name)} ｜ 对比结果：${C.esc(d.result_label)}</div>`;
    const m = d.metrics || {};
    el.querySelector("#cp-metrics").innerHTML = `
      <div>MSE：<strong>${m.mse ?? "-"}</strong></div>
      <div>RMSE：<strong>${m.rmse ?? "-"}</strong></div>
      <div>PSNR：<strong>${m.psnr ?? "-"} dB</strong></div>
      <div>变化像素占比：<strong>${m.changed_ratio != null ? (m.changed_ratio * 100).toFixed(2) + "%" : "-"}</strong></div>`;
  }

  async function updateCompare(el) {
    if (!imgId || !resultId) return;
    const img = (await C.fetchImages()).find((i) => i.id === imgId);
    if (!img) return;
    const top = el.querySelector("#cp-top"), bottom = el.querySelector("#cp-bottom");
    top.src = img.file_url;
    bottom.src = "/api/results/" + resultId + "/file";
    el.querySelector("#cp-caption").textContent = "左：原图 ｜ 右：处理结果";
    applyClip(el);
  }

  function applyClip(el) {
    el.querySelector("#cp-top").style.clipPath = `inset(0 calc(100% - ${pos}%) 0 0)`;
    el.querySelector("#cp-handle").style.left = pos + "%";
  }

  function bindSlider(el) {
    const wrap = el.querySelector("#cp-wrap");
    const handle = el.querySelector("#cp-handle");
    handle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const move = (ev) => {
        const rect = wrap.getBoundingClientRect();
        pos = Math.max(0, Math.min(100, (ev.clientX - rect.left) / rect.width * 100));
        applyClip(el);
      };
      const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
  }
})();
