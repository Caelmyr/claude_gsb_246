/* 视图 8：结果对比（原图/处理图 + 滑块 + 像素差异）。
   差异热力图是「对比记录」，与处理结果分开管理：
   不进结果下拉、不能再当输入，只在下方「对比记录」里展示来源并可单独删除。 */
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
            <div class="panel">
              <div class="panel-title">对比记录<span class="dim">差异图仅在此展示，不进入处理结果列表</span></div>
              <div id="cp-diff-list" style="max-height:280px;overflow:auto"><span class="dim">加载中…</span></div>
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
        const r = await Api.post("/api/compare/diff", { image_id: imgId, result_id: resultId });
        box.innerHTML = `<img src="${r.file_url}?t=${Date.now()}">`;
        showMetrics(el, r.metrics);
        loadDiffs(el);
      };

      // 对比记录：点击查看 / 删除
      el.querySelector("#cp-diff-list").addEventListener("click", async (e) => {
        const item = e.target.closest("[data-diff]");
        if (!item) return;
        const id = item.dataset.diff;
        if (e.target.closest("[data-act=del]")) {
          await Api.del("/api/compare/diffs/" + id);
          C.toast("已删除对比记录");
          loadDiffs(el);
          return;
        }
        if (e.target.closest("[data-act=view]")) {
          el.querySelector("#cp-diff-stage").innerHTML = `<img src="/api/compare/diffs/${id}/file?t=${Date.now()}">`;
          try {
            showMetrics(el, JSON.parse(item.dataset.metrics || "{}"));
          } catch (_) { /* 指标缺失时保持原样 */ }
        }
      });

      bindSlider(el);
    },

    refresh() { const el = document.querySelector('.view[data-view="compare"]'); if (el && this.mounted) { loadResults(el); loadDiffs(el); } },
  };

  async function loadResults(el) {
    const r = await Api.get("/api/results");
    el.querySelector("#cp-result").innerHTML = `<option value="">— 选择结果 —</option>` +
      r.results.map((x) => `<option value="${x.result_id}">${C.fmtDate(x.created_at)} · ${x.width}×${x.height} · #${x.result_id.slice(0, 8)}</option>`).join("");
  }

  async function loadDiffs(el) {
    const box = el.querySelector("#cp-diff-list");
    const r = await Api.get("/api/compare/diffs");
    if (!r.diffs.length) {
      box.innerHTML = `<div class="empty" style="padding:16px">暂无对比记录</div>`;
      return;
    }
    box.innerHTML = r.diffs.map((d) => `
      <div data-diff="${d.diff_id}" data-metrics='${C.esc(JSON.stringify(d.metrics || {}))}'
           style="display:flex;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)">
        <img data-act="view" src="${d.file_url}" title="点击查看"
             style="width:72px;height:54px;object-fit:cover;border-radius:6px;cursor:pointer;flex:none">
        <div style="flex:1;min-width:0;font-size:12px">
          <div>原图：${C.esc(d.image_name)} ｜ 结果：#${(d.result_id || "").slice(0, 8)}${d.result_exists ? "" : "（已删除）"}</div>
          <div class="dim">MSE ${d.metrics && d.metrics.mse != null ? d.metrics.mse : "-"} · PSNR ${d.metrics && d.metrics.psnr != null ? d.metrics.psnr + " dB" : "-"} · ${C.fmtDate(d.created_at)}</div>
        </div>
        <button class="btn btn-sm btn-danger" data-act="del" style="flex:none">删除</button>
      </div>`).join("");
  }

  function showMetrics(el, m) {
    el.querySelector("#cp-metrics").innerHTML = `
      <div>MSE：<strong>${m.mse}</strong></div>
      <div>RMSE：<strong>${m.rmse}</strong></div>
      <div>PSNR：<strong>${m.psnr} dB</strong></div>
      <div>变化像素占比：<strong>${(m.changed_ratio * 100).toFixed(2)}%</strong></div>`;
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
