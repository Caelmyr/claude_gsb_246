"""差异对比图与处理结果分离管理的端到端测试（Flask test client，不监听端口）。

运行：python tests/test_diffs.py

覆盖需求：
1. 差异图不进入 /api/results（处理结果选择列表的数据源）；
2. 差异图带来源标识（原图 + 结果），通过 /api/compare/diffs 单独展示；
3. 差异图不能被当作输入继续对比（404）；
4. 删除对比记录时差异图文件一并清理；
5. 删除原图 / 删除结果（经历史删除）时级联清理对应差异图；
6. 旧版本混入结果缓存的差异图在启动时被迁移到对比记录。
"""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 先把数据目录重定向到临时目录，再导入 server 模块（单例在导入时按 config 建路径）
_TMP = tempfile.mkdtemp(prefix="diffs-test-")
from server import config  # noqa: E402

config.DATA_DIR = _TMP
config.IMAGES_DIR = os.path.join(_TMP, "images")
config.RESULTS_DIR = os.path.join(_TMP, "results")
config.DIFFS_DIR = os.path.join(_TMP, "diffs")
config.THUMBS_DIR = os.path.join(_TMP, "thumbnails")
config.CACHE_DIR = os.path.join(_TMP, "cache")
config.META_DIR = os.path.join(_TMP, "metadata")
for _name in ("IMAGES_JSON", "PIPELINES_JSON", "HISTORY_JSON", "PRESETS_JSON",
              "QUEUE_JSON", "CACHE_JSON", "DIFFS_JSON"):
    setattr(config, _name, os.path.join(config.META_DIR, _name.lower()))
# _ALL_DIRS 在 config 导入时已按旧路径固化，重定向后需要重建
config._ALL_DIRS = [config.DATA_DIR, config.IMAGES_DIR, config.RESULTS_DIR,
                    config.DIFFS_DIR, config.THUMBS_DIR, config.CACHE_DIR,
                    config.META_DIR]
config.ensure_dirs()

from PIL import Image  # noqa: E402

from server import api  # noqa: E402
from server.cache import make_key  # noqa: E402

PASS = []


def check(name, cond):
    PASS.append((name, bool(cond)))
    print(f"  {'✔' if cond else '✘'} {name}")
    if not cond:
        raise AssertionError(name)


def _png_bytes(color_fn, w=160, h=120):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = color_fn(x, y)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _upload(client, data, filename):
    r = client.post("/api/images", data={"files": (io.BytesIO(data), filename)},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    return r.get_json()["saved"][0]


def main():
    from flask import Flask
    app = Flask(__name__)
    api.init_app(app)
    client = app.test_client()

    print("== 准备：上传图像并跑出两个处理结果 ==")
    img_a = _upload(client, _png_bytes(lambda x, y: (x % 256, y % 256, 128)), "a.png")
    img_b = _upload(client, _png_bytes(lambda x, y: (200, x % 256, y % 256)), "b.png")
    nodes = [{"id": "n1", "type": "brightness", "params": {"amount": 30}, "inputs": []}]
    r1 = client.post("/api/run", json={"image_id": img_a["id"], "nodes": nodes}).get_json()
    r2 = client.post("/api/run", json={"image_id": img_b["id"], "nodes": nodes}).get_json()
    res1, res2 = r1["result_id"], r2["result_id"]
    check("两个处理结果已生成", res1 and res2 and res1 != res2)
    n_results = len(client.get("/api/results").get_json()["results"])
    check("结果列表初始为 2 条", n_results == 2)

    print("== 生成差异热力图 ==")
    d1 = client.post("/api/compare/diff", json={"image_id": img_a["id"], "result_id": res1})
    check("差异接口返回 200", d1.status_code == 200)
    d1 = d1.get_json()
    check("返回 diff_id / 指标 / 来源", d1.get("diff_id") and d1["metrics"].get("psnr") is not None
          and d1["image_id"] == img_a["id"] and d1["result_id"] == res1)

    print("== 需求 1：差异图不混进处理结果列表 ==")
    results = client.get("/api/results").get_json()["results"]
    check("生成差异图后结果列表仍为 2 条", len(results) == 2)
    check("结果列表中没有差异图 id", all(x["result_id"] != d1["diff_id"] for x in results))

    print("== 需求 2：差异图在对比记录中展示并标明来源 ==")
    diffs = client.get("/api/compare/diffs").get_json()["diffs"]
    check("对比记录有 1 条", len(diffs) == 1)
    rec = diffs[0]
    check("来源：原图名", rec["image_name"] == "a.png")
    check("来源：结果标识含尺寸", "×" in rec["result_label"])
    f = client.get(rec["file_url"])
    check("差异图文件可访问且为 PNG", f.status_code == 200 and f.mimetype == "image/png")

    print("== 同一组合重复生成不堆积 ==")
    d1b = client.post("/api/compare/diff", json={"image_id": img_a["id"], "result_id": res1}).get_json()
    diffs = client.get("/api/compare/diffs").get_json()["diffs"]
    check("重复生成仍只有 1 条记录", len(diffs) == 1)
    check("复用同一 diff_id（更新而非新增）", d1b["diff_id"] == d1["diff_id"])

    print("== 需求 3：差异图不能被当作输入继续对比 ==")
    bad = client.post("/api/compare/diff", json={"image_id": img_b["id"], "result_id": d1["diff_id"]})
    check("用差异图 id 对比返回 404", bad.status_code == 404)
    check("结果列表里依然取不到差异图", client.get(f"/api/results/{d1['diff_id']}").status_code == 404)

    print("== 再生成一张差异图（另一组合） ==")
    d2 = client.post("/api/compare/diff", json={"image_id": img_b["id"], "result_id": res2}).get_json()
    check("对比记录变为 2 条", len(client.get("/api/compare/diffs").get_json()["diffs"]) == 2)
    diff_file_1 = api.diff_store.file_path(d1["diff_id"])
    check("差异图文件确实在 data/diffs/ 下", diff_file_1 and os.path.dirname(diff_file_1) == config.DIFFS_DIR)

    print("== 需求 4：删除对比记录，差异图一并清理 ==")
    rm = client.delete(f"/api/compare/diffs/{d1['diff_id']}")
    check("删除返回 200", rm.status_code == 200)
    check("差异图文件已删除", not os.path.exists(diff_file_1))
    check("对比记录剩 1 条", len(client.get("/api/compare/diffs").get_json()["diffs"]) == 1)
    check("重复删除返回 404", client.delete(f"/api/compare/diffs/{d1['diff_id']}").status_code == 404)

    print("== 需求 5a：删除结果（经历史删除）级联清理差异图 ==")
    hist = client.get("/api/history").get_json()["history"]
    h2 = next(h for h in hist if h.get("result_id") == res2)
    diff_file_2 = api.diff_store.file_path(d2["diff_id"])
    client.delete(f"/api/history/{h2['id']}")
    check("源结果删除后对比记录被级联清理", len(client.get("/api/compare/diffs").get_json()["diffs"]) == 0)
    check("对应差异图文件已删除", not os.path.exists(diff_file_2))

    print("== 需求 5b：删除原图级联清理差异图 ==")
    d3 = client.post("/api/compare/diff", json={"image_id": img_a["id"], "result_id": res1}).get_json()
    diff_file_3 = api.diff_store.file_path(d3["diff_id"])
    client.delete(f"/api/images/{img_a['id']}")
    check("原图删除后对比记录被级联清理",
          all(d["id"] != d3["diff_id"] for d in client.get("/api/compare/diffs").get_json()["diffs"]))
    check("对应差异图文件已删除", not os.path.exists(diff_file_3))

    print("== 需求 6：旧版本混入结果缓存的差异图被迁移 ==")
    # 模拟旧行为：按旧规则 make_key(图像哈希, 结果id, "diff") 直接写进结果缓存
    legacy_img = Image.new("RGB", (32, 32), (255, 0, 0))
    legacy_key = make_key(api.image_store.get(img_b["id"])["hash"], res1, "diff")
    legacy_result_id = api.cache.put(legacy_key, legacy_img)
    legacy_path = api.cache.result_path(legacy_result_id)
    check("旧差异图确实混进了结果列表",
          any(x["result_id"] == legacy_result_id for x in client.get("/api/results").get_json()["results"]))

    moved = api._migrate_legacy_diffs()
    check("迁移函数识别出 1 条旧差异图", moved == 1)
    remaining = client.get("/api/results").get_json()["results"]
    check("迁移后结果列表不再含旧差异图",
          all(x["result_id"] != legacy_result_id for x in remaining))
    diffs = client.get("/api/compare/diffs").get_json()["diffs"]
    check("迁移后对比记录包含该差异图且标明来源",
          any(d["image_id"] == img_b["id"] and d["result_id"] == res1
              and d["image_name"] == "b.png" for d in diffs))
    check("旧文件已搬出 results 目录", not os.path.exists(legacy_path))
    check("迁移幂等（再跑一次为 0）", api._migrate_legacy_diffs() == 0)

    print("\n全部通过 ✔")


if __name__ == "__main__":
    main()
